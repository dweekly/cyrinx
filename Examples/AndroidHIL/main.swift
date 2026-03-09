import Cyrinx
import Darwin
import Foundation

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
    var rawSendText: String?
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
            case "--raw-send-text":
                rawMode = true
                if idx + 1 < args.count {
                    idx += 1
                    rawSendText = args[idx]
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

private func runRawMode(_ opts: Options) throws {
    let config = Config(
        role: opts.role,
        transportBackend: .inMemory,
        sampleRateHz: opts.sampleRateHz,
        bandStartHz: opts.bandStartHz,
        bandEndHz: opts.bandEndHz,
        txGainCap: opts.txGainCap
    )

    let link = RawAcousticMacLink(
        config: config,
        dcssSymbolSamples: opts.dcssSymbolSamples,
        preambleSyncThreshold: opts.preambleSyncThreshold
    )
    try link.start { data in
        let text = String(decoding: data, as: UTF8.self)
        print("[raw-rx] bytes=\(data.count) text=\(text) hex=\(hexPrefix(data))")
    }
    defer { link.stop() }

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
            print("[raw-tx] bytes=\(payload.count) text=\(String(decoding: payload, as: UTF8.self))")
            sendCount += 1
            nextSend = Date().addingTimeInterval(TimeInterval(opts.sendIntervalMs) / 1000.0)
        }

        if Date() >= nextDiag {
            let diag = link.diagnostics
            print(
                "[raw-diag] inHz=\(diag.observedInputSampleRateHz) outHz=\(diag.observedOutputSampleRateHz) " +
                    "txFrames=\(diag.txFrameCount) rxFrames=\(diag.rxFrameCount) " +
                    "pending=\(diag.pendingOutputSampleCount) rms=\(diag.recentInputRms)"
            )
            nextDiag = Date().addingTimeInterval(1.0)
        }

        RunLoop.current.run(until: Date().addingTimeInterval(0.02))
    }

    print("[raw-summary] sentFrames=\(sendCount) receivedFrames=\(link.diagnostics.rxFrameCount)")
}

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
    print("[fixture] wrote path=\(wavePath) samples=\(waveform.count)")
    print("[fixture] payloadBytes=\(payload.count) payloadPrefix=\(hexPrefix(payload))")
    print("[fixture] macDecode frames=\(decoded.count) firstLen=\(firstLen) firstPrefix=\(firstPrefix)")
}

@main
struct AndroidHILRunner {
    static func main() async {
        let opts = Options(args: Array(CommandLine.arguments.dropFirst()))
        print("[cyrinx-android-hil] role=\(opts.role) durationSec=\(opts.durationSec) sendIntervalMs=\(opts.sendIntervalMs)")
        print(
            "[cyrinx-android-hil] sampleRate=\(opts.sampleRateHz) band=\(opts.bandStartHz)...\(opts.bandEndHz) " +
                "txGain=\(opts.txGainCap) dcss=\(opts.dcssSymbolSamples) sync=\(opts.preambleSyncThreshold) " +
                "forceRobust=\(opts.forceRobustMode)"
        )

        setenv("CYRINX_FORCE_ROBUST_MODE", opts.forceRobustMode ? "1" : "0", 1)
        setenv("CYRINX_DCSS_SYMBOL_SAMPLES", "\(opts.dcssSymbolSamples)", 1)
        setenv("CYRINX_PREAMBLE_SYNC_THRESHOLD", "\(opts.preambleSyncThreshold)", 1)

        if opts.fixtureWavePath != nil {
            do {
                try runFixtureMode(opts)
            } catch {
                print("[fatal] fixture mode failed: \(error)")
                exit(2)
            }
            return
        }

        if opts.rawMode {
            do {
                try runRawMode(opts)
            } catch {
                print("[fatal] raw mode failed: \(error)")
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
