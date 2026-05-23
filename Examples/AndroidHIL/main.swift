import Cyrinx
import Darwin
import Foundation
#if os(macOS)
import CoreAudio
#endif

private struct Options {
    var role: Role = .master
    var durationSec: TimeInterval = 25
    var sendIntervalMs: UInt32 = 700
    var reliableEvery: Int = 4
    var sampleRateHz: UInt32 = 48_000
    var bandStartHz: UInt32 = 18_500
    var bandEndHz: UInt32 = 21_000
    var txGainCap: Float = 0.70
    var dcssSymbolSamples: Int = 256
    var preambleSyncThreshold: Float = 0.52
    var forceRobustMode: Bool = true
    var beepOnStart: Bool = false
    var rxOnly: Bool = false
    var rawMode: Bool = false
    var rawCodec: RawAcousticCodec = .auto
    var rawSendText: String?
    var rawCaptureWavePath: String?
    var rawDecodeWavePath: String?
    var rawEncodeWavePath: String?
    var fixtureWavePath: String?
    var fixturePayloadHex: String?
    var fixturePayloadText: String?

    init(args: [String]) {
        var idx = 0
        while idx < args.count {
            let arg = args[idx]
            switch arg {
            case "--role":
                if idx + 1 < args.count {
                    idx += 1
                    role = args[idx].lowercased() == "slave" ? .slave : .master
                }
            case "--duration":
                if idx + 1 < args.count {
                    idx += 1
                    durationSec = TimeInterval(args[idx]) ?? durationSec
                }
            case "--send-interval-ms":
                if idx + 1 < args.count {
                    idx += 1
                    sendIntervalMs = UInt32(args[idx]) ?? sendIntervalMs
                }
            case "--reliable-every":
                if idx + 1 < args.count {
                    idx += 1
                    reliableEvery = max(1, Int(args[idx]) ?? reliableEvery)
                }
            case "--sample-rate":
                if idx + 1 < args.count {
                    idx += 1
                    sampleRateHz = UInt32(args[idx]) ?? sampleRateHz
                }
            case "--band-start":
                if idx + 1 < args.count {
                    idx += 1
                    bandStartHz = UInt32(args[idx]) ?? bandStartHz
                }
            case "--band-end":
                if idx + 1 < args.count {
                    idx += 1
                    bandEndHz = UInt32(args[idx]) ?? bandEndHz
                }
            case "--tx-gain":
                if idx + 1 < args.count {
                    idx += 1
                    txGainCap = Float(args[idx]) ?? txGainCap
                }
            case "--dcss-symbol-samples":
                if idx + 1 < args.count {
                    idx += 1
                    dcssSymbolSamples = Int(args[idx]) ?? dcssSymbolSamples
                }
            case "--sync-threshold":
                if idx + 1 < args.count {
                    idx += 1
                    preambleSyncThreshold = Float(args[idx]) ?? preambleSyncThreshold
                }
            case "--allow-turbo":
                forceRobustMode = false
            case "--beep":
                beepOnStart = true
            case "--rx-only":
                rxOnly = true
            case "--raw":
                rawMode = true
            case "--raw-codec":
                rawMode = true
                if idx + 1 < args.count {
                    idx += 1
                    rawCodec = parseRawCodec(args[idx]) ?? rawCodec
                }
            case "--raw-send-text":
                rawMode = true
                if idx + 1 < args.count {
                    idx += 1
                    rawSendText = args[idx]
                }
            case "--raw-capture-wave":
                rawMode = true
                if idx + 1 < args.count {
                    idx += 1
                    rawCaptureWavePath = args[idx]
                }
            case "--raw-decode-wave":
                rawMode = true
                if idx + 1 < args.count {
                    idx += 1
                    rawDecodeWavePath = args[idx]
                }
            case "--raw-encode-wave":
                rawMode = true
                if idx + 1 < args.count {
                    idx += 1
                    rawEncodeWavePath = args[idx]
                }
            case "--fixture-wave":
                if idx + 1 < args.count {
                    idx += 1
                    fixtureWavePath = args[idx]
                }
            case "--fixture-payload-hex":
                if idx + 1 < args.count {
                    idx += 1
                    fixturePayloadHex = args[idx]
                }
            case "--fixture-payload-text":
                if idx + 1 < args.count {
                    idx += 1
                    fixturePayloadText = args[idx]
                }
            default:
                break
            }
            idx += 1
        }
    }
}

private func parseRawCodec(_ raw: String) -> RawAcousticCodec? {
    switch raw.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() {
    case "auto":
        return .auto
    case "basic":
        return .basic
    case "reverse", "reverse-burst", "reverse_burst":
        return .reverseBurst
    case "ook":
        return .ook
    case "morse":
        return .morse
    case "dtmf":
        return .dtmf
    case "nibble":
        return .nibble
    default:
        return nil
    }
}

private func parseHexData(_ raw: String) -> Data? {
    let trimmed = raw.replacingOccurrences(of: " ", with: "")
    let prefixed = trimmed.hasPrefix("0x") ? String(trimmed.dropFirst(2)) : trimmed
    guard prefixed.count.isMultiple(of: 2), !prefixed.isEmpty else {
        return nil
    }

    var bytes = [UInt8]()
    bytes.reserveCapacity(prefixed.count / 2)
    var cursor = prefixed.startIndex
    while cursor < prefixed.endIndex {
        let next = prefixed.index(cursor, offsetBy: 2)
        let pair = prefixed[cursor..<next]
        guard let byte = UInt8(pair, radix: 16) else {
            return nil
        }
        bytes.append(byte)
        cursor = next
    }
    return Data(bytes)
}

private func hexPrefix(_ data: Data, count: Int = 8) -> String {
    data.prefix(count).map { String(format: "%02x", $0) }.joined()
}

private func logLine(_ message: String) {
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    print("[\(formatter.string(from: Date()))] \(message)")
}

private func writeFloat32LE(samples: [Float], path: String) throws {
    var out = Data(capacity: samples.count * 4)
    for value in samples {
        var bits = value.bitPattern.littleEndian
        withUnsafeBytes(of: &bits) { out.append(contentsOf: $0) }
    }
    try out.write(to: URL(fileURLWithPath: path))
}

private func fixturePayload(from opts: Options) throws -> Data {
    if let hex = opts.fixturePayloadHex {
        guard let data = parseHexData(hex) else {
            throw NSError(
                domain: "cyrinx.android_hil",
                code: 2,
                userInfo: [NSLocalizedDescriptionKey: "invalid --fixture-payload-hex"]
            )
        }
        return data
    }
    if let text = opts.fixturePayloadText {
        return Data(text.utf8)
    }
    return Data((0..<48).map { UInt8(($0 * 13) & 0xFF) })
}

private func rawPayload(from opts: Options) -> Data {
    if let text = opts.rawSendText {
        return Data(text.utf8)
    }
    return Data("hello-from-mac".utf8)
}

private func readFloat32LE(path: String) throws -> [Float] {
    let data = try Data(contentsOf: URL(fileURLWithPath: path))
    guard data.count.isMultiple(of: MemoryLayout<UInt32>.size) else {
        throw NSError(
            domain: "cyrinx.android_hil",
            code: 3,
            userInfo: [NSLocalizedDescriptionKey: "invalid float32le byte count for \(path)"]
        )
    }

    var values = [Float]()
    values.reserveCapacity(data.count / MemoryLayout<UInt32>.size)
    var offset = 0
    while offset < data.count {
        let word = data.withUnsafeBytes { rawPtr -> UInt32 in
            rawPtr.load(fromByteOffset: offset, as: UInt32.self)
        }
        values.append(Float(bitPattern: UInt32(littleEndian: word)))
        offset += MemoryLayout<UInt32>.size
    }
    return values
}

private func runRawMode(_ opts: Options) throws {
    let config = Config(
        role: opts.role,
        transportBackend: .inMemory,
        sampleRateHz: opts.sampleRateHz,
        bandStartHz: opts.bandStartHz,
        bandEndHz: opts.bandEndHz,
        txGainCap: opts.txGainCap
    )

    if let wavePath = opts.rawDecodeWavePath {
        let samples = try readFloat32LE(path: wavePath)
        let decoded = RawAcousticDebug.decodeWaveform(
            config: config,
            samples: samples,
            symbolSamples: opts.dcssSymbolSamples,
            rawCodec: opts.rawCodec
        )
        logLine("[raw-decode] path=\(wavePath) samples=\(samples.count) frames=\(decoded.count)")
        for (index, frame) in decoded.enumerated() {
            let text = String(decoding: frame, as: UTF8.self)
            logLine("[raw-decode] frame=\(index) bytes=\(frame.count) text=\(text) hex=\(hexPrefix(frame))")
        }
        return
    }

    if let wavePath = opts.rawEncodeWavePath {
        let payload = rawPayload(from: opts)
        let waveform = RawAcousticDebug.encodeWaveform(
            config: config,
            payload: payload,
            symbolSamples: opts.dcssSymbolSamples,
            rawCodec: opts.rawCodec
        )
        try writeFloat32LE(samples: waveform, path: wavePath)
        let decoded = RawAcousticDebug.decodeWaveform(
            config: config,
            samples: waveform,
            symbolSamples: opts.dcssSymbolSamples,
            rawCodec: opts.rawCodec
        )
        logLine("[raw-encode] path=\(wavePath) codec=\(opts.rawCodec.rawValue) samples=\(waveform.count) bytes=\(payload.count)")
        logLine("[raw-encode] decodeFrames=\(decoded.count) firstText=\(decoded.first.map { String(decoding: $0, as: UTF8.self) } ?? "")")
        return
    }

#if os(macOS)
    let rateOverride = try MacDefaultAudioRateOverride(requestedHz: Double(opts.sampleRateHz))
    defer { rateOverride?.restore() }
#endif

    let link = RawAcousticMacLink(
        config: config,
        rawCodec: opts.rawCodec,
        dcssSymbolSamples: opts.dcssSymbolSamples,
        preambleSyncThreshold: opts.preambleSyncThreshold
    )
    if opts.rawCaptureWavePath != nil {
        link.setInputCaptureEnabled(true)
    }
    try link.start { data in
        let text = String(decoding: data, as: UTF8.self)
        logLine("[raw-rx] bytes=\(data.count) text=\(text) hex=\(hexPrefix(data))")
    }
    var didFinalizeLink = false
    func finalizeLink() {
        guard !didFinalizeLink else {
            return
        }
        link.stop()
        if let capturePath = opts.rawCaptureWavePath {
            do {
                try writeFloat32LE(samples: link.snapshotCapturedInput(), path: capturePath)
                logLine("[raw-capture] wrote path=\(capturePath)")
            } catch {
                logLine("[raw-capture] failed path=\(capturePath) error=\(error)")
            }
        }
        didFinalizeLink = true
    }
    defer { finalizeLink() }

    if opts.beepOnStart {
        let tone = Data("beep".utf8)
        try? link.send(frame: tone)
    }

    let payload = rawPayload(from: opts)
    let end = Date().addingTimeInterval(opts.durationSec)
    var nextSend = Date()
    var sendCount = 0
    var nextDiag = Date()

    while Date() < end {
        if !opts.rxOnly, Date() >= nextSend {
            try link.send(frame: payload)
            logLine("[raw-tx] bytes=\(payload.count) text=\(String(decoding: payload, as: UTF8.self))")
            sendCount += 1
            nextSend = Date().addingTimeInterval(TimeInterval(opts.sendIntervalMs) / 1000.0)
        }

        if Date() >= nextDiag {
            let diag = link.diagnostics
            logLine(
                "[raw-diag] inHz=\(diag.observedInputSampleRateHz) outHz=\(diag.observedOutputSampleRateHz) " +
                    "codec=\(opts.rawCodec.rawValue) " +
                    "txFrames=\(diag.txFrameCount) rxFrames=\(diag.rxFrameCount) " +
                    "pending=\(diag.pendingOutputSampleCount) rms=\(diag.recentInputRms)"
            )
            nextDiag = Date().addingTimeInterval(1.0)
        }

        RunLoop.current.run(until: Date().addingTimeInterval(0.02))
    }

    finalizeLink()
    logLine("[raw-summary] sentFrames=\(sendCount) receivedFrames=\(link.diagnostics.rxFrameCount)")
}

#if os(macOS)
private final class MacDefaultAudioRateOverride {
    private struct DeviceSnapshot {
        let id: AudioDeviceID
        let name: String
        let nominalHz: Double
    }

    private let snapshots: [DeviceSnapshot]

    init?(requestedHz: Double) throws {
        guard requestedHz > 0 else {
            return nil
        }

        let inputID = try Self.defaultDevice(selector: kAudioHardwarePropertyDefaultInputDevice)
        let outputID = try Self.defaultDevice(selector: kAudioHardwarePropertyDefaultOutputDevice)
        var pending: [AudioDeviceID: DeviceSnapshot] = [:]

        for deviceID in [inputID, outputID] where deviceID != kAudioObjectUnknown {
            let name = try Self.deviceName(deviceID)
            let nominalHz = try Self.nominalSampleRate(deviceID)
            pending[deviceID] = DeviceSnapshot(id: deviceID, name: name, nominalHz: nominalHz)
            if abs(nominalHz - requestedHz) > 0.5 {
                try Self.setNominalSampleRate(deviceID, requestedHz: requestedHz)
            }
        }

        snapshots = Array(pending.values)
        for snapshot in snapshots {
            let liveHz = try Self.nominalSampleRate(snapshot.id)
            logLine("[mac-audio-device] name=\(snapshot.name) nominalHz=\(Int(liveHz.rounded())) requestedHz=\(Int(requestedHz.rounded()))")
        }
    }

    func restore() {
        for snapshot in snapshots {
            do {
                try Self.setNominalSampleRate(snapshot.id, requestedHz: snapshot.nominalHz)
                let restored = try Self.nominalSampleRate(snapshot.id)
                logLine("[mac-audio-restore] name=\(snapshot.name) nominalHz=\(Int(restored.rounded()))")
            } catch {
                logLine("[mac-audio-restore] name=\(snapshot.name) failed error=\(error)")
            }
        }
    }

    private static func defaultDevice(selector: AudioObjectPropertySelector) throws -> AudioDeviceID {
        var address = AudioObjectPropertyAddress(
            mSelector: selector,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var deviceID = AudioDeviceID(kAudioObjectUnknown)
        var size = UInt32(MemoryLayout<AudioDeviceID>.size)
        let status = AudioObjectGetPropertyData(
            AudioObjectID(kAudioObjectSystemObject),
            &address,
            0,
            nil,
            &size,
            &deviceID
        )
        guard status == noErr else {
            throw NSError(domain: "cyrinx.android_hil", code: Int(status), userInfo: [
                NSLocalizedDescriptionKey: "failed to get default device selector=\(selector)"
            ])
        }
        return deviceID
    }

    private static func nominalSampleRate(_ deviceID: AudioDeviceID) throws -> Double {
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioDevicePropertyNominalSampleRate,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var value = 0.0
        var size = UInt32(MemoryLayout<Double>.size)
        let status = AudioObjectGetPropertyData(deviceID, &address, 0, nil, &size, &value)
        guard status == noErr else {
            throw NSError(domain: "cyrinx.android_hil", code: Int(status), userInfo: [
                NSLocalizedDescriptionKey: "failed to read nominal sample rate"
            ])
        }
        return value
    }

    private static func setNominalSampleRate(_ deviceID: AudioDeviceID, requestedHz: Double) throws {
        var availableAddress = AudioObjectPropertyAddress(
            mSelector: kAudioDevicePropertyAvailableNominalSampleRates,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var size: UInt32 = 0
        let sizeStatus = AudioObjectGetPropertyDataSize(deviceID, &availableAddress, 0, nil, &size)
        guard sizeStatus == noErr else {
            throw NSError(domain: "cyrinx.android_hil", code: Int(sizeStatus), userInfo: [
                NSLocalizedDescriptionKey: "failed to get available nominal sample rate size"
            ])
        }
        let count = Int(size) / MemoryLayout<AudioValueRange>.size
        var ranges = [AudioValueRange](repeating: AudioValueRange(mMinimum: 0, mMaximum: 0), count: count)
        let readStatus = AudioObjectGetPropertyData(deviceID, &availableAddress, 0, nil, &size, &ranges)
        guard readStatus == noErr else {
            throw NSError(domain: "cyrinx.android_hil", code: Int(readStatus), userInfo: [
                NSLocalizedDescriptionKey: "failed to read available nominal sample rates"
            ])
        }
        let supported = ranges.contains { requestedHz >= $0.mMinimum - 0.5 && requestedHz <= $0.mMaximum + 0.5 }
        guard supported else {
            throw NSError(domain: "cyrinx.android_hil", code: 4, userInfo: [
                NSLocalizedDescriptionKey: "requested sample rate \(requestedHz) not supported by device \(deviceID)"
            ])
        }

        var address = AudioObjectPropertyAddress(
            mSelector: kAudioDevicePropertyNominalSampleRate,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var value = requestedHz
        let writeStatus = AudioObjectSetPropertyData(
            deviceID,
            &address,
            0,
            nil,
            UInt32(MemoryLayout<Double>.size),
            &value
        )
        guard writeStatus == noErr else {
            throw NSError(domain: "cyrinx.android_hil", code: Int(writeStatus), userInfo: [
                NSLocalizedDescriptionKey: "failed to set nominal sample rate to \(requestedHz)"
            ])
        }
        usleep(300_000)
    }

    private static func deviceName(_ deviceID: AudioDeviceID) throws -> String {
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioObjectPropertyName,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var name: Unmanaged<CFString>?
        var size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
        let status = AudioObjectGetPropertyData(deviceID, &address, 0, nil, &size, &name)
        guard status == noErr else {
            throw NSError(domain: "cyrinx.android_hil", code: Int(status), userInfo: [
                NSLocalizedDescriptionKey: "failed to read device name"
            ])
        }
        return name?.takeUnretainedValue() as String? ?? "device-\(deviceID)"
    }
}
#endif

private func runFixtureMode(_ opts: Options) throws {
    guard let wavePath = opts.fixtureWavePath else {
        return
    }
    let payload = try fixturePayload(from: opts)
    let config = Config(
        role: opts.role,
        transportBackend: .inMemory,
        sampleRateHz: opts.sampleRateHz,
        bandStartHz: opts.bandStartHz,
        bandEndHz: opts.bandEndHz,
        txGainCap: opts.txGainCap
    )

    let waveform = try AcousticPHYDebug.encodeFrame(
        config: config,
        frame: payload,
        dcssSymbolSamples: opts.dcssSymbolSamples,
        preambleSyncThreshold: opts.preambleSyncThreshold
    )
    try writeFloat32LE(samples: waveform, path: wavePath)

    let decoded = AcousticPHYDebug.decodeWaveform(
        config: config,
        samples: waveform,
        dcssSymbolSamples: opts.dcssSymbolSamples,
        preambleSyncThreshold: opts.preambleSyncThreshold
    )

    let first = decoded.first
    let firstLen = first?.frame.count ?? 0
    let firstPrefix = first.map { hexPrefix($0.frame) } ?? ""
    logLine("[fixture] wrote path=\(wavePath) samples=\(waveform.count)")
    logLine("[fixture] payloadBytes=\(payload.count) payloadPrefix=\(hexPrefix(payload))")
    logLine("[fixture] macDecode frames=\(decoded.count) firstLen=\(firstLen) firstPrefix=\(firstPrefix)")
}

@main
struct AndroidHILRunner {
    static func main() async {
        let opts = Options(args: Array(CommandLine.arguments.dropFirst()))
        logLine("[cyrinx-android-hil] role=\(opts.role) durationSec=\(opts.durationSec) sendIntervalMs=\(opts.sendIntervalMs)")
        logLine(
            "[cyrinx-android-hil] sampleRate=\(opts.sampleRateHz) band=\(opts.bandStartHz)...\(opts.bandEndHz) " +
                "txGain=\(opts.txGainCap) dcss=\(opts.dcssSymbolSamples) sync=\(opts.preambleSyncThreshold) " +
                "forceRobust=\(opts.forceRobustMode)"
        )
        if opts.rawMode {
            logLine("[cyrinx-android-hil] rawCodec=\(opts.rawCodec.rawValue)")
        }

        setenv("CYRINX_FORCE_ROBUST_MODE", opts.forceRobustMode ? "1" : "0", 1)
        setenv("CYRINX_DCSS_SYMBOL_SAMPLES", "\(opts.dcssSymbolSamples)", 1)
        setenv("CYRINX_PREAMBLE_SYNC_THRESHOLD", "\(opts.preambleSyncThreshold)", 1)

        if opts.fixtureWavePath != nil {
            do {
                try runFixtureMode(opts)
            } catch {
                logLine("[fatal] fixture mode failed: \(error)")
                exit(2)
            }
            return
        }

        if opts.rawMode {
            do {
                try runRawMode(opts)
            } catch {
                logLine("[fatal] raw mode failed: \(error)")
                exit(3)
            }
            return
        }

        let config = Config(
            role: opts.role,
            transportBackend: .appleAudioScaffold,
            sampleRateHz: opts.sampleRateHz,
            bandStartHz: opts.bandStartHz,
            bandEndHz: opts.bandEndHz,
            txGainCap: opts.txGainCap
        )

#if os(macOS)
        var rateOverride: MacDefaultAudioRateOverride? = nil
        do {
            rateOverride = try MacDefaultAudioRateOverride(requestedHz: Double(opts.sampleRateHz))
        } catch {
            logLine("[mac-audio-device-error] failed to override sample rate: \(error)")
        }
        defer { rateOverride?.restore() }
#endif

        do {
            let session = try CyrinxSession(config: config)
            try session.start()
            print("[cyrinx-android-hil] session started")

            if opts.beepOnStart {
                do {
                    try session.playLocalAudibleBeacon()
                    print("[cyrinx-android-hil] played local beacon")
                } catch {
                    print("[cyrinx-android-hil] beacon failed: \(error)")
                }
            }

            let eventTask = Task {
                for await event in session.events {
                    print("[event] \(event)")
                }
            }

            let start = Date()
            let end = start.addingTimeInterval(opts.durationSec)
            var nextSend = Date()
            var nextDiag = Date()
            var sendCount = 0
            var rxCount = 0

            while Date() < end {
                if !opts.rxOnly, Date() >= nextSend {
                    let stamp = String(format: "%.3f", Date().timeIntervalSince1970)
                    let bestEffortPayload = Data("mac-probe:\(stamp)".utf8)
                    do {
                        try session.send(bestEffortPayload, streamID: 7, qos: .bestEffort, priority: .high, flags: [.fin])
                        print("[tx] best-effort bytes=\(bestEffortPayload.count)")
                    } catch {
                        print("[tx] best-effort failed: \(error)")
                    }

                    if sendCount % opts.reliableEvery == 0 {
                        let reliablePayload = Data("mac-probe-rel:\(stamp)".utf8)
                        do {
                            try session.send(reliablePayload, streamID: 9, qos: .reliable, priority: .high, flags: [.fin])
                            print("[tx] reliable bytes=\(reliablePayload.count)")
                        } catch {
                            print("[tx] reliable failed: \(error)")
                        }
                    }

                    sendCount += 1
                    nextSend = Date().addingTimeInterval(TimeInterval(opts.sendIntervalMs) / 1000.0)
                }

                do {
                    if let msg = try session.receive(timeoutMS: 20) {
                        rxCount += 1
                        let preview = String(decoding: msg.data.prefix(64), as: UTF8.self)
                        print("[rx] stream=\(msg.streamID) bytes=\(msg.data.count) preview=\(preview)")
                    }
                } catch {
                    print("[rx] failed: \(error)")
                }

                if Date() >= nextDiag {
                    if let diagnostics = session.audioDiagnostics {
                        let metrics = session.metrics
                        print(
                            "[diag] inHz=\(diagnostics.observedInputSampleRateHz) outHz=\(diagnostics.observedOutputSampleRateHz) " +
                                "txFrames=\(diagnostics.txFrameCount) rxCb=\(diagnostics.rxCallbackCount) " +
                                "outCb=\(diagnostics.outputCallbackCount) pending=\(diagnostics.pendingOutputSampleCount) " +
                                "coreTx=\(metrics.txFrames) coreRx=\(metrics.rxFrames) per2s=\(String(format: "%.3f", metrics.per2s))"
                        )
                    }
                    nextDiag = Date().addingTimeInterval(1.0)
                }
            }

            eventTask.cancel()
            print("[summary] sentLoops=\(sendCount) receivedMessages=\(rxCount)")
        } catch {
            print("[fatal] \(error)")
            exit(1)
        }
    }
}
