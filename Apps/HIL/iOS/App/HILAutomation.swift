#if os(iOS)
    import AVFoundation
    import Foundation
    import UIKit

    /// iOS HIL automation entry point. There is no `adb am` on iOS, so the Mac-side
    /// harness drives the app over USB with `xcrun devicectl device process launch
    /// --environment-variables '<JSON>'`. The app reads the command + params from
    /// the process environment on launch, runs it, writes a `result.json` summary
    /// (and any PCM capture) into its Documents container, and the harness pulls
    /// those back with `xcrun devicectl device copy from --domain-type
    /// appDataContainer`.
    ///
    /// Recognized env keys (all strings, per devicectl's JSON dictionary contract):
    ///   CYRINX_CMD        = rec_pcm | play_pcm | bulk_decode | noop
    ///   CYRINX_OUT        = output file name in Documents (rec_pcm)
    ///   CYRINX_IN         = input file name in Documents (play_pcm, bulk_decode)
    ///   CYRINX_DURATION   = seconds (rec_pcm)
    ///   CYRINX_SR         = sample rate Hz (default 48000)
    ///   CYRINX_CH         = channels (default 1)
    ///   CYRINX_FLO/CYRINX_FHI = band edges Hz (bulk_decode)
    ///   CYRINX_NSYM       = OFDM data symbols (bulk_decode, default 64)
    ///   CYRINX_NPAYLOADS  = candidate frames (bulk_decode, default 5)
    ///   CYRINX_SEEDBASE   = DetRng payload seed base (bulk_decode, default 1000)
    enum HILAutomation {
        // Single-launch HIL automation: these statics are mutated from the launch
        // thread + one background worker, guarded by logLock for logLines. The
        // app is a throwaway test harness, not a concurrent service.
        nonisolated(unsafe) static var logLines: [String] = []
        /// Set by the app delegate: true when launched with a CYRINX_CMD env var.
        nonisolated(unsafe) static var automationActive = false
        private static let logLock = NSLock()

        static func log(_ s: String) {
            let ts = ISO8601DateFormatter().string(from: Date())
            let line = "\(ts) \(s)"
            logLock.lock()
            logLines.append(line)
            logLock.unlock()
            NSLog("CyrinxHILiOS %@", s)
            print("CyrinxHILiOS \(line)")
        }

        static func documentsDir() -> URL {
            FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        }

        /// Returns true if an automation command was found and dispatched.
        @discardableResult
        static func runIfRequested() -> Bool {
            let env = ProcessInfo.processInfo.environment
            guard let cmd = env["CYRINX_CMD"], !cmd.isEmpty else { return false }
            log("automation cmd=\(cmd) env-keys=\(env.keys.filter { $0.hasPrefix("CYRINX_") }.sorted())")
            // Keep the screen on; iOS silences/throttles audio work when the app is
            // not foreground, so the harness must keep the device unlocked and the
            // app top (the analog of Android's top-visibility mic-silencing gotcha).
            UIApplication.shared.isIdleTimerDisabled = true

            DispatchQueue.global(qos: .userInitiated).async {
                var result: [String: Any] = ["cmd": cmd, "ok": false]
                do {
                    switch cmd {
                    case "rec_pcm":
                        let dur = Double(env["CYRINX_DURATION"] ?? "5") ?? 5
                        let sr = Double(env["CYRINX_SR"] ?? "48000") ?? 48_000
                        let ch = Int(env["CYRINX_CH"] ?? "1") ?? 1
                        let out = env["CYRINX_OUT"] ?? "cap.pcm"
                        let url = documentsDir().appendingPathComponent(out)
                        let r = try IOSAudio.recordPCM(
                            duration: dur, sampleRate: sr, channels: ch,
                            outURL: url, log: log)
                        result["ok"] = true
                        result["frames"] = r.frames
                        result["hw_rate"] = r.hwRate
                        result["rms"] = r.rms
                        result["peak"] = r.peak
                        result["out"] = out

                    case "play_pcm":
                        let sr = Double(env["CYRINX_SR"] ?? "48000") ?? 48_000
                        let ch = Int(env["CYRINX_CH"] ?? "1") ?? 1
                        let inName = env["CYRINX_IN"] ?? "tx.pcm"
                        let url = documentsDir().appendingPathComponent(inName)
                        try IOSAudio.playPCM(path: url.path, sampleRate: sr, channels: ch, log: log)
                        result["ok"] = true
                        result["in"] = inName

                    case "bulk_decode":
                        let inName = env["CYRINX_IN"] ?? "cap.pcm"
                        let url = documentsDir().appendingPathComponent(inName)
                        let ch = Int(env["CYRINX_CH"] ?? "1") ?? 1
                        let fLo = Double(env["CYRINX_FLO"] ?? "1100") ?? 1_100
                        let fHi = Double(env["CYRINX_FHI"] ?? "23000") ?? 23_000
                        let nSym = Int(env["CYRINX_NSYM"] ?? "64") ?? 64
                        let nPayloads = Int(env["CYRINX_NPAYLOADS"] ?? "5") ?? 5
                        let seedBase = UInt64(env["CYRINX_SEEDBASE"] ?? "1000") ?? 1_000
                        let t0 = Date()
                        let dec = BulkDemod.decodeCapture(
                            path: url.path, channels: ch, fLo: fLo, fHi: fHi,
                            nSym: nSym, nPayloads: nPayloads,
                            payloadSeedBase: seedBase, log: log)
                        let spanS = Double(dec.spanSamples) / Double(BulkDemod.SRATE)
                        let gp = spanS > 0 ? Double(dec.verified * BulkDemod.CRC_BLOCK * 8) / spanS : 0
                        log(
                            String(
                                format:
                                    "bulk_decode TOTAL: verified=%d blocks (%d bytes) span=%.2fs goodput=%.0f bps wall_ms=%d",
                                dec.verified, dec.verified * BulkDemod.CRC_BLOCK, spanS, gp,
                                Int(Date().timeIntervalSince(t0) * 1000)))
                        result["ok"] = true
                        result["verified"] = dec.verified
                        result["verified_bytes"] = dec.verified * BulkDemod.CRC_BLOCK
                        result["span_s"] = spanS
                        result["goodput_bps"] = gp
                        result["frames"] = dec.frames.map { fr -> [String: Any] in
                            [
                                "start": fr.start, "ok": fr.ok, "blocks_ok": fr.blocksOk,
                                "blocks_total": fr.blocksTotal, "verified": fr.verified,
                                "evm": fr.evm, "decode_ms": fr.decodeMs,
                            ]
                        }

                    case "noop":
                        result["ok"] = true

                    default:
                        log("unknown automation cmd=\(cmd)")
                        result["err"] = "unknown cmd"
                    }
                } catch {
                    log("automation \(cmd) failed: \(error.localizedDescription)")
                    result["err"] = error.localizedDescription
                }
                result["log"] = logLines
                writeResult(result)
            }
            return true
        }

        private static func writeResult(_ result: [String: Any]) {
            let url = documentsDir().appendingPathComponent("result.json")
            if let data = try? JSONSerialization.data(withJSONObject: result, options: [.prettyPrinted]) {
                try? data.write(to: url)
                log("wrote result.json (\(data.count) bytes)")
            }
        }
    }
#endif
