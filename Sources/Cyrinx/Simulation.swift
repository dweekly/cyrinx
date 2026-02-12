import Foundation

public enum SimulationProfile: String, CaseIterable, Sendable {
    case quietDesktop = "quiet"
    case officeBurst = "office-burst"
    case movingPhone = "moving-phone"

    fileprivate func makeReport(packetIndex: Int, rng: inout SplitMix64) -> ChannelSnapshot {
        switch self {
        case .quietDesktop:
            return ChannelSnapshot(
                snrDB: 31.0 + rng.nextSigned(amplitude: 1.5),
                evmPct: max(0.5, 3.0 + rng.nextSigned(amplitude: 0.8)),
                cfoHz: rng.nextSigned(amplitude: 4.0),
                per2s: max(0.0, 0.002 + rng.nextSigned(amplitude: 0.0015)),
                crcFail: false
            )

        case .officeBurst:
            let burst = packetIndex % 10 == 0
            if burst {
                return ChannelSnapshot(
                    snrDB: 9.0 + rng.nextSigned(amplitude: 2.0),
                    evmPct: 18.0 + rng.nextSigned(amplitude: 3.0),
                    cfoHz: rng.nextSigned(amplitude: 12.0),
                    per2s: 0.18 + max(0.0, rng.nextSigned(amplitude: 0.06)),
                    crcFail: rng.nextUnit() < 0.35
                )
            }
            return ChannelSnapshot(
                snrDB: 19.0 + rng.nextSigned(amplitude: 2.0),
                evmPct: 8.0 + rng.nextSigned(amplitude: 1.5),
                cfoHz: rng.nextSigned(amplitude: 8.0),
                per2s: max(0.0, 0.015 + rng.nextSigned(amplitude: 0.01)),
                crcFail: false
            )

        case .movingPhone:
            let phase = Double(packetIndex % 24) / 24.0
            let doppler = 58.0 * sin(2.0 * .pi * phase)
            return ChannelSnapshot(
                snrDB: 23.0 + rng.nextSigned(amplitude: 2.5),
                evmPct: 7.0 + rng.nextSigned(amplitude: 1.2),
                cfoHz: Float(doppler) + rng.nextSigned(amplitude: 5.0),
                per2s: max(0.0, 0.02 + rng.nextSigned(amplitude: 0.01)),
                crcFail: false
            )
        }
    }
}

public struct SimulationOptions: Sendable {
    public var packetCount: Int
    public var payloadBytes: Int
    public var streamID: UInt16
    public var priority: StreamPriority
    public var interPacketIntervalMS: UInt32
    public var seed: UInt64

    public init(
        packetCount: Int = 120,
        payloadBytes: Int = 128,
        streamID: UInt16 = 1,
        priority: StreamPriority = .normal,
        interPacketIntervalMS: UInt32 = 20,
        seed: UInt64 = 0xC7_58_2026
    ) {
        self.packetCount = packetCount
        self.payloadBytes = payloadBytes
        self.streamID = streamID
        self.priority = priority
        self.interPacketIntervalMS = interPacketIntervalMS
        self.seed = seed
    }
}

public struct SimulationResult: Codable, Sendable {
    public let profile: String
    public let seed: UInt64
    public let packetCount: Int
    public let payloadBytes: Int
    public let packetsDelivered: Int
    public let packetsFailed: Int
    public let durationMS: UInt64
    public let txFrames: UInt32
    public let txRetries: UInt32
    public let rxFrames: UInt32
    public let crcFailures: UInt32
    public let linkResets: UInt32
    public let finalGear: String
    public let gearHistogram: [String: Int]
    public let goodputBps: Float
}

private struct ChannelSnapshot {
    let snrDB: Float
    let evmPct: Float
    let cfoHz: Float
    let per2s: Float
    let crcFail: Bool
}

private struct SplitMix64 {
    private var state: UInt64

    init(seed: UInt64) {
        self.state = seed == 0 ? 0x9E37_79B9_7F4A_7C15 : seed
    }

    mutating func next() -> UInt64 {
        state &+= 0x9E37_79B9_7F4A_7C15
        var z = state
        z = (z ^ (z >> 30)) &* 0xBF58_476D_1CE4_E5B9
        z = (z ^ (z >> 27)) &* 0x94D0_49BB_1331_11EB
        return z ^ (z >> 31)
    }

    mutating func nextUnit() -> Float {
        Float(next() & 0x00FF_FFFF) / Float(0x0100_0000)
    }

    mutating func nextSigned(amplitude: Float) -> Float {
        ((nextUnit() * 2.0) - 1.0) * amplitude
    }
}

public enum SimulationRunner {
    /// Runs a deterministic in-memory transport simulation and returns machine-readable metrics.
    public static func run(profile: SimulationProfile, options: SimulationOptions) throws -> SimulationResult
    {
        validate(options: options)
        let sessions = try makeLinkedSessions()
        let desktop = sessions.desktop
        let phone = sessions.phone

        var rng = SplitMix64(seed: options.seed)
        var delivered = 0
        var failed = 0
        var gearHistogram: [String: Int] = [:]

        let start = Date()

        for packetIndex in 0..<options.packetCount {
            let channel = profile.makeReport(packetIndex: packetIndex, rng: &rng)
            try inject(channel: channel, into: desktop)

            let payload = makePayload(packetIndex: packetIndex, bytes: options.payloadBytes)
            if try deliverPacket(
                payload: payload,
                streamID: options.streamID,
                priority: options.priority,
                sender: desktop,
                receiver: phone
            ) {
                delivered += 1
            } else {
                failed += 1
            }

            let gearName = String(describing: desktop.metrics.gear)
            gearHistogram[gearName, default: 0] += 1

            if options.interPacketIntervalMS > 0 {
                Thread.sleep(forTimeInterval: Double(options.interPacketIntervalMS) / 1000.0)
            }
        }

        let elapsedMS = UInt64(Date().timeIntervalSince(start) * 1000.0)
        let tx = desktop.metrics
        let rx = phone.metrics

        return SimulationResult(
            profile: profile.rawValue,
            seed: options.seed,
            packetCount: options.packetCount,
            payloadBytes: options.payloadBytes,
            packetsDelivered: delivered,
            packetsFailed: failed,
            durationMS: elapsedMS,
            txFrames: tx.txFrames,
            txRetries: tx.txRetries,
            rxFrames: rx.rxFrames,
            crcFailures: tx.crcFailures + rx.crcFailures,
            linkResets: tx.linkResets + rx.linkResets,
            finalGear: String(describing: tx.gear),
            gearHistogram: gearHistogram,
            goodputBps: tx.goodputBps
        )
    }

    public static func makeJSON(_ result: SimulationResult) throws -> String {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        let data = try encoder.encode(result)
        guard let text = String(data: data, encoding: .utf8) else {
            throw NSError(
                domain: "SimulationRunner",
                code: 1,
                userInfo: [NSLocalizedDescriptionKey: "Could not UTF-8 encode simulation JSON"]
            )
        }
        return text
    }

    private static func validate(options: SimulationOptions) {
        precondition(options.packetCount > 0, "packetCount must be > 0")
        precondition(
            options.payloadBytes > 0 && options.payloadBytes <= 4096, "payloadBytes must be 1...4096")
    }

    private static func makeLinkedSessions() throws -> (desktop: CyrinxSession, phone: CyrinxSession) {
        let desktop = try CyrinxSession(config: Config(role: .master))
        let phone = try CyrinxSession(config: Config(role: .slave))

        try CyrinxSession.linkInMemory(desktop, phone)
        try desktop.start()
        try phone.start()
        return (desktop, phone)
    }

    private static func inject(channel: ChannelSnapshot, into session: CyrinxSession) throws {
        try session.injectChannelReport(
            snrDB: channel.snrDB,
            evmPct: channel.evmPct,
            cfoHz: channel.cfoHz,
            per2s: channel.per2s,
            crcFail: channel.crcFail
        )
    }

    private static func makePayload(packetIndex: Int, bytes: Int) -> Data {
        Data((0..<bytes).map { UInt8((packetIndex + $0) % 251) })
    }

    private static func deliverPacket(
        payload: Data,
        streamID: UInt16,
        priority: StreamPriority,
        sender: CyrinxSession,
        receiver: CyrinxSession
    ) throws -> Bool {
        do {
            try sender.send(
                payload,
                streamID: streamID,
                qos: .reliable,
                priority: priority,
                flags: [.fin]
            )

            guard let rx = try receiver.receive(timeoutMS: 800) else {
                return false
            }
            return (rx.data == payload) && (rx.streamID == streamID)
        } catch {
            return false
        }
    }
}
