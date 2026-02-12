import CCyrinx
import Foundation

private let CYRINX_SWIFT_OK: Int32 = 0
private let CYRINX_SWIFT_ERR_INVALID_ARGUMENT: Int32 = -1
private let CYRINX_SWIFT_ERR_BUFFER_TOO_SMALL: Int32 = -3
private let CYRINX_SWIFT_ERR_TIMEOUT: Int32 = -4
private let CYRINX_SWIFT_ERR_INTERNAL: Int32 = -9

public enum CyrinxError: Error, CustomStringConvertible {
    case status(Int32)

    public var description: String {
        switch self {
        case .status(let code):
            return "cyrinx error \(code)"
        }
    }
}

public enum Role {
    case master
    case slave

    fileprivate var cValue: cyrinx_role_t {
        switch self {
        case .master: return CYRINX_ROLE_MASTER
        case .slave: return CYRINX_ROLE_SLAVE
        }
    }
}

public enum QoS {
    case bestEffort
    case reliable

    fileprivate var cValue: cyrinx_qos_t {
        switch self {
        case .bestEffort: return CYRINX_QOS_BEST_EFFORT
        case .reliable: return CYRINX_QOS_RELIABLE
        }
    }
}

public enum Gear: UInt8 {
    case discovery = 0
    case robust = 1
    case turboQPSK = 2
    case turbo16QAM = 3
    case turbo64QAM = 4

    fileprivate init(cValue: cyrinx_gear_t) {
        self = Gear(rawValue: UInt8(cValue.rawValue)) ?? .discovery
    }
}

public enum Event: UInt8 {
    case idle = 0
    case discovery = 1
    case linked = 2
    case degraded = 3
    case recovering = 4
    case failed = 5

    fileprivate init(cValue: cyrinx_event_t) {
        self = Event(rawValue: UInt8(cValue.rawValue)) ?? .idle
    }
}

public struct Config {
    public var role: Role
    public var sampleRateHz: UInt32
    public var bandStartHz: UInt32
    public var bandEndHz: UInt32
    public var txGainCap: Float
    public var spectralLeakageLimitDbfs: Float
    public var ofdmCPSamplesDefault: UInt16
    public var ofdmCPSamplesMin: UInt16
    public var sensorAssistedARC: Bool
    public var dynamicCP: Bool
    public var sfbcStaticMode: Bool

    /// Creates a PRD-aligned session configuration.
    ///
    /// - Important: These settings only configure policy and transport behavior.
    ///   Real ultrasonic audio IO is added by platform backends.
    public init(
        role: Role = .master,
        sampleRateHz: UInt32 = 48_000,
        bandStartHz: UInt32 = 18_500,
        bandEndHz: UInt32 = 23_500,
        txGainCap: Float = 0.70,
        spectralLeakageLimitDbfs: Float = -45.0,
        ofdmCPSamplesDefault: UInt16 = 96,
        ofdmCPSamplesMin: UInt16 = 10,
        sensorAssistedARC: Bool = true,
        dynamicCP: Bool = true,
        sfbcStaticMode: Bool = true
    ) {
        self.role = role
        self.sampleRateHz = sampleRateHz
        self.bandStartHz = bandStartHz
        self.bandEndHz = bandEndHz
        self.txGainCap = txGainCap
        self.spectralLeakageLimitDbfs = spectralLeakageLimitDbfs
        self.ofdmCPSamplesDefault = ofdmCPSamplesDefault
        self.ofdmCPSamplesMin = ofdmCPSamplesMin
        self.sensorAssistedARC = sensorAssistedARC
        self.dynamicCP = dynamicCP
        self.sfbcStaticMode = sfbcStaticMode
    }

    fileprivate func toC(eventCallback: cyrinx_event_callback_t?, userData: UnsafeMutableRawPointer?)
        -> cyrinx_config_t
    {
        var c = cyrinx_config_t()
        c.role = role.cValue
        c.sample_rate_hz = sampleRateHz
        c.band_start_hz = bandStartHz
        c.band_end_hz = bandEndHz
        c.tx_gain_cap = txGainCap
        c.spectral_leakage_limit_dbfs = spectralLeakageLimitDbfs
        c.ofdm_cp_samples_default = ofdmCPSamplesDefault
        c.ofdm_cp_samples_min = ofdmCPSamplesMin
        c.enable_sensor_assisted_arc = sensorAssistedARC ? 1 : 0
        c.enable_dynamic_cp = dynamicCP ? 1 : 0
        c.enable_sfbc_static_mode = sfbcStaticMode ? 1 : 0
        c.security_mode = CYRINX_SECURITY_EXTERNAL
        c.tx_callback = nil
        c.event_callback = eventCallback
        c.user_data = userData
        return c
    }
}

public struct ARCPolicy {
    public var g2ToQPSKSNR: Float
    public var qpskTo16QAMSNR: Float
    public var qpskTo16QAMMaxEVM: Float
    public var qpskToG2SNR: Float
    public var minDwellMS: UInt32
    public var maxRetransmissions: UInt8

    /// ARC threshold overrides for simulation and tuning.
    public init(
        g2ToQPSKSNR: Float = 14,
        qpskTo16QAMSNR: Float = 25,
        qpskTo16QAMMaxEVM: Float = 5,
        qpskToG2SNR: Float = 15,
        minDwellMS: UInt32 = 1000,
        maxRetransmissions: UInt8 = 4
    ) {
        self.g2ToQPSKSNR = g2ToQPSKSNR
        self.qpskTo16QAMSNR = qpskTo16QAMSNR
        self.qpskTo16QAMMaxEVM = qpskTo16QAMMaxEVM
        self.qpskToG2SNR = qpskToG2SNR
        self.minDwellMS = minDwellMS
        self.maxRetransmissions = maxRetransmissions
    }

    fileprivate func toC() -> cyrinx_arc_policy_t {
        var c = cyrinx_arc_policy_t()
        cyrinx_default_arc_policy(&c)
        c.up_g2_to_qpsk_snr_db = g2ToQPSKSNR
        c.up_qpsk_to_16qam_snr_db = qpskTo16QAMSNR
        c.up_qpsk_to_16qam_max_evm_pct = qpskTo16QAMMaxEVM
        c.down_qpsk_to_g2_snr_db = qpskToG2SNR
        c.min_dwell_ms = minDwellMS
        c.max_retransmissions = maxRetransmissions
        return c
    }
}

public struct Metrics {
    public let gear: Gear
    public let snrDB: Float
    public let evmPct: Float
    public let cfoHz: Float
    public let per2s: Float
    public let goodputBps: Float
    public let txRetries: UInt32
    public let txFrames: UInt32
    public let rxFrames: UInt32
    public let crcFailures: UInt32
    public let linkResets: UInt32

    fileprivate init(c: cyrinx_metrics_t) {
        gear = Gear(cValue: c.current_gear)
        snrDB = c.snr_db
        evmPct = c.evm_pct
        cfoHz = c.cfo_hz
        per2s = c.per_2s
        goodputBps = c.goodput_bps
        txRetries = c.tx_retries
        txFrames = c.tx_frames
        rxFrames = c.rx_frames
        crcFailures = c.crc_failures
        linkResets = c.link_resets
    }
}

private final class EventRelay {
    var continuation: AsyncStream<Event>.Continuation?
}

@_cdecl("cyrinx_swift_event_callback")
private func cyrinx_swift_event_callback(_ event: cyrinx_event_t, _ userData: UnsafeMutableRawPointer?) {
    guard let userData else { return }
    let relay = Unmanaged<EventRelay>.fromOpaque(userData).takeUnretainedValue()
    relay.continuation?.yield(Event(cValue: event))
}

public final class CyrinxSession {
    private let handle: OpaquePointer
    private let relay: EventRelay
    private let relayToken: UnsafeMutableRawPointer
    public let events: AsyncStream<Event>

    /// Opens a new session handle and prepares event streaming.
    public init(config: Config = Config()) throws {
        relay = EventRelay()
        relayToken = UnsafeMutableRawPointer(Unmanaged.passRetained(relay).toOpaque())

        var streamContinuation: AsyncStream<Event>.Continuation?
        events = AsyncStream<Event> { continuation in
            streamContinuation = continuation
        }
        relay.continuation = streamContinuation

        let callback: cyrinx_event_callback_t = cyrinx_swift_event_callback
        var cConfig = config.toC(eventCallback: callback, userData: relayToken)

        guard let opened = cyrinx_open(&cConfig) else {
            Unmanaged<EventRelay>.fromOpaque(relayToken).release()
            throw CyrinxError.status(CYRINX_SWIFT_ERR_INTERNAL)
        }

        handle = opened
    }

    deinit {
        relay.continuation?.finish()
        cyrinx_close(handle)
        Unmanaged<EventRelay>.fromOpaque(relayToken).release()
    }

    /// Moves the session from idle to discovery mode.
    public func start() throws {
        let rc = cyrinx_start(handle)
        try Self.checkStatus(rc)
    }

    /// Sends a logical payload; underlying C core handles fragmentation/retransmit.
    public func send(_ data: Data, qos: QoS = .reliable) throws {
        let rc = data.withUnsafeBytes { rawBuffer -> Int32 in
            guard let base = rawBuffer.baseAddress?.assumingMemoryBound(to: UInt8.self) else {
                return CYRINX_SWIFT_ERR_INVALID_ARGUMENT
            }
            return cyrinx_send(handle, base, data.count, qos.cValue)
        }
        try Self.checkStatus(rc)
    }

    /// Receives a logical payload if available before timeout.
    public func receive(timeoutMS: UInt32 = 0, maxBytes: Int = 4096) throws -> Data? {
        var buffer = [UInt8](repeating: 0, count: maxBytes)
        var len = buffer.count
        let rc: Int32 = buffer.withUnsafeMutableBufferPointer { buf in
            cyrinx_recv(handle, buf.baseAddress, &len, timeoutMS)
        }
        if rc == CYRINX_SWIFT_ERR_TIMEOUT {
            return nil
        }
        if rc == CYRINX_SWIFT_ERR_BUFFER_TOO_SMALL {
            buffer = [UInt8](repeating: 0, count: len)
            var retryLen = len
            let retry: Int32 = buffer.withUnsafeMutableBufferPointer { buf in
                cyrinx_recv(handle, buf.baseAddress, &retryLen, timeoutMS)
            }
            try Self.checkStatus(retry)
            return Data(buffer.prefix(retryLen))
        }

        try Self.checkStatus(rc)
        return Data(buffer.prefix(len))
    }

    /// Returns instantaneous transport and ARC metrics.
    public var metrics: Metrics {
        var c = cyrinx_metrics_t()
        _ = cyrinx_get_metrics(handle, &c)
        return Metrics(c: c)
    }

    /// Replaces the ARC threshold policy for this session.
    public func setARCPolicy(_ policy: ARCPolicy) throws {
        var c = policy.toC()
        let rc = cyrinx_set_arc_policy(handle, &c)
        try Self.checkStatus(rc)
    }

    /// Injects channel-state feedback for simulation and deterministic testing.
    public func injectChannelReport(
        snrDB: Float,
        evmPct: Float,
        cfoHz: Float,
        per2s: Float,
        crcFail: Bool = false
    ) throws {
        var r = cyrinx_channel_report_t(
            snr_db: snrDB,
            evm_pct: evmPct,
            cfo_hz: cfoHz,
            per_2s: per2s,
            crc_fail: crcFail ? 1 : 0
        )
        let rc = cyrinx_update_channel_report(handle, &r)
        try Self.checkStatus(rc)
    }

    /// Connects two sessions in memory, bypassing any audio modem backend.
    public static func linkInMemory(_ a: CyrinxSession, _ b: CyrinxSession) throws {
        let rc = cyrinx_link_in_memory(a.handle, b.handle)
        try Self.checkStatus(rc)
    }

    private static func checkStatus(_ status: Int32) throws {
        if status != CYRINX_SWIFT_OK {
            throw CyrinxError.status(status)
        }
    }
}

public enum Cyrinx {
    /// Current C core version string.
    public static var version: String {
        String(cString: cyrinx_version())
    }
}
