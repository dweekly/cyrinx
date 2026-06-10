#if os(iOS)
    import AVFoundation
    import Foundation

    /// iOS HIL audio primitives mirroring the Android `rec_pcm` / `play_pcm`
    /// automation commands (MainActivity.kt). The Mac does all modem DSP; these
    /// are deliberately dumb PCM16LE capture/playback over the speaker/mic.
    ///
    /// Capture uses AVAudioSession category `.playAndRecord` with mode
    /// `.measurement`, which disables AGC/EQ/processing — the iOS analog of
    /// Android's `AudioSource.UNPROCESSED`, mandatory because AGC/NS would wreck
    /// the QAM constellation. The actual hardware input format/sample rate is
    /// logged; iOS resamples to the requested rate via an AVAudioConverter when the
    /// hardware native rate differs.
    enum IOSAudio {
        /// Records PCM16LE from the mic to `outURL` for `duration` seconds at the
        /// requested sample rate and channel count. Returns (framesWritten,
        /// hardwareSampleRate, rms, peak).
        static func recordPCM(
            duration: Double, sampleRate: Double, channels: Int,
            outURL: URL, log: @escaping (String) -> Void
        ) throws
            -> (frames: Int, hwRate: Double, rms: Double, peak: Double)
        {
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(
                .playAndRecord, mode: .measurement,
                options: [.defaultToSpeaker, .allowBluetooth])
            try session.setPreferredSampleRate(sampleRate)
            try session.setActive(true)
            let hwRate = session.sampleRate
            log(
                String(
                    format: "rec_pcm: session active hwRate=%.0f requested=%.0f mode=measurement",
                    hwRate, sampleRate))

            let engine = AVAudioEngine()
            let input = engine.inputNode
            let inFormat = input.outputFormat(forBus: 0)
            log(
                String(
                    format: "rec_pcm: input format %.0f Hz, %d ch",
                    inFormat.sampleRate, inFormat.channelCount))

            // Target interleaved Int16 at the requested rate/channels.
            guard
                let outFormat = AVAudioFormat(
                    commonFormat: .pcmFormatInt16,
                    sampleRate: sampleRate,
                    channels: AVAudioChannelCount(channels),
                    interleaved: true)
            else {
                throw NSError(
                    domain: "IOSAudio", code: 1,
                    userInfo: [NSLocalizedDescriptionKey: "bad out format"])
            }
            guard let converter = AVAudioConverter(from: inFormat, to: outFormat) else {
                throw NSError(
                    domain: "IOSAudio", code: 2,
                    userInfo: [NSLocalizedDescriptionKey: "no converter"])
            }

            let fh = FileManager.default.createFile(atPath: outURL.path, contents: nil)
            guard fh, let handle = try? FileHandle(forWritingTo: outURL) else {
                throw NSError(
                    domain: "IOSAudio", code: 3,
                    userInfo: [NSLocalizedDescriptionKey: "cannot open out file"])
            }

            var framesWritten = 0
            var sumSq = 0.0
            var peak = 0.0
            let lock = NSLock()
            let targetFrames = Int(duration * sampleRate)
            let done = DispatchSemaphore(value: 0)
            var finished = false

            input.installTap(onBus: 0, bufferSize: 4_096, format: inFormat) { buffer, _ in
                // Convert this input buffer to the target Int16 format.
                let ratio = sampleRate / inFormat.sampleRate
                let outCap = AVAudioFrameCount(Double(buffer.frameLength) * ratio + 256)
                guard let outBuf = AVAudioPCMBuffer(pcmFormat: outFormat, frameCapacity: outCap) else {
                    return
                }
                var supplied = false
                var err: NSError?
                converter.convert(to: outBuf, error: &err) { _, status in
                    if supplied {
                        status.pointee = .noDataNow
                        return nil
                    }
                    supplied = true
                    status.pointee = .haveData
                    return buffer
                }
                if let err {
                    log("rec_pcm convert err: \(err.localizedDescription)")
                    return
                }
                let n = Int(outBuf.frameLength) * channels
                guard n > 0, let ch = outBuf.int16ChannelData else { return }
                // interleaved -> single buffer at channel 0 pointer
                let p = ch[0]
                var bytes = [UInt8]()
                bytes.reserveCapacity(n * 2)
                for i in 0..<n {
                    let s = p[i]
                    bytes.append(UInt8(truncatingIfNeeded: Int(s)))
                    bytes.append(UInt8(truncatingIfNeeded: Int(s) >> 8))
                    let f = Double(s) / 32_768.0
                    sumSq += f * f
                    if abs(f) > peak { peak = abs(f) }
                }
                lock.lock()
                handle.write(Data(bytes))
                framesWritten += n / channels
                let reached = framesWritten >= targetFrames
                lock.unlock()
                if reached, !finished {
                    finished = true
                    done.signal()
                }
            }

            engine.prepare()
            try engine.start()
            log("rec_pcm begin: targetFrames=\(targetFrames) rate=\(Int(sampleRate)) ch=\(channels)")
            _ = done.wait(timeout: .now() + duration + 10)
            input.removeTap(onBus: 0)
            engine.stop()
            try? handle.close()
            try? session.setActive(false)
            let rms = sqrt(sumSq / Double(max(1, framesWritten * channels)))
            log(
                String(
                    format: "rec_pcm done: frames=%d rms=%.6f peak=%.4f bytes=%d",
                    framesWritten, rms, peak,
                    (try? FileManager.default.attributesOfItem(atPath: outURL.path)[.size] as? Int) ?? 0 ?? 0)
            )
            return (framesWritten, hwRate, rms, peak)
        }

        /// Plays a PCM16LE file out the speaker at full volume. The waveform is
        /// expected at `sampleRate` with `channels` interleaved channels.
        static func playPCM(
            path: String, sampleRate: Double, channels: Int,
            log: @escaping (String) -> Void
        ) throws {
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.playback, mode: .default, options: [])
            try session.setPreferredSampleRate(sampleRate)
            try session.setActive(true)
            // Route to the built-in speaker (not the earpiece).
            try? session.overrideOutputAudioPort(.speaker)
            log(
                String(
                    format: "play_pcm: session active hwRate=%.0f requested=%.0f",
                    session.sampleRate, sampleRate))

            guard let data = FileManager.default.contents(atPath: path) else {
                throw NSError(
                    domain: "IOSAudio", code: 4,
                    userInfo: [NSLocalizedDescriptionKey: "cannot read \(path)"])
            }
            let bytesPerFrame = 2 * channels
            let frameCount = data.count / bytesPerFrame
            log(
                "play_pcm begin: \(path) bytes=\(data.count) frames=\(frameCount) rate=\(Int(sampleRate)) ch=\(channels)"
            )

            guard
                let format = AVAudioFormat(
                    commonFormat: .pcmFormatInt16,
                    sampleRate: sampleRate,
                    channels: AVAudioChannelCount(channels),
                    interleaved: true),
                let buffer = AVAudioPCMBuffer(
                    pcmFormat: format,
                    frameCapacity: AVAudioFrameCount(frameCount))
            else {
                throw NSError(
                    domain: "IOSAudio", code: 5,
                    userInfo: [NSLocalizedDescriptionKey: "bad play format"])
            }
            buffer.frameLength = AVAudioFrameCount(frameCount)
            data.withUnsafeBytes { (raw: UnsafeRawBufferPointer) in
                let src = raw.bindMemory(to: Int16.self)
                let dst = buffer.int16ChannelData![0]
                for i in 0..<(frameCount * channels) { dst[i] = Int16(littleEndian: src[i]) }
            }

            let engine = AVAudioEngine()
            let player = AVAudioPlayerNode()
            engine.attach(player)
            engine.connect(player, to: engine.mainMixerNode, format: format)
            engine.prepare()
            try engine.start()
            let done = DispatchSemaphore(value: 0)
            player.scheduleBuffer(buffer, at: nil, options: []) { done.signal() }
            player.play()
            let secs = Double(frameCount) / sampleRate
            _ = done.wait(timeout: .now() + secs + 10)
            // small drain
            usleep(200_000)
            player.stop()
            engine.stop()
            try? session.setActive(false)
            log("play_pcm done: played \(frameCount) frames (\(String(format: "%.2f", secs))s)")
        }
    }
#endif
