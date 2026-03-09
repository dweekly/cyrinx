import Cyrinx
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
    var beepOnStart: Bool = false
    var rxOnly: Bool = false

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
            case "--beep":
                beepOnStart = true
            case "--rx-only":
                rxOnly = true
            default:
                break
            }
            idx += 1
        }
    }
}

@main
struct AndroidHILRunner {
    static func main() async {
        let opts = Options(args: Array(CommandLine.arguments.dropFirst()))
        print("[cyrinx-android-hil] role=\(opts.role) durationSec=\(opts.durationSec) sendIntervalMs=\(opts.sendIntervalMs)")
        print("[cyrinx-android-hil] sampleRate=\(opts.sampleRateHz) band=\(opts.bandStartHz)...\(opts.bandEndHz) txGain=\(opts.txGainCap)")

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
