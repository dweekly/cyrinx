import CCyrinx
import Foundation

#if canImport(AVFoundation)
    import AVFoundation
#endif
#if os(iOS)
    import AudioToolbox
    import os.log
#endif

/// Transport wiring used by `CyrinxSession`.
public enum TransportBackend: Sendable, Equatable {
    /// Pure in-memory transport for deterministic testing.
    case inMemory
    /// Apple audio stack scaffold (`RemoteIO` on iOS, `AVAudioEngine` on macOS).
    case appleAudioScaffold
}

/// Runtime state of the platform audio backend.
public enum AudioBackendState: String, Sendable {
    case idle
    case running
    case stopped
    case failed
}

/// Snapshot counters and negotiated route rates for audio backend diagnostics.
public struct AudioBackendDiagnostics: Sendable {
    public let backend: String
    public let state: AudioBackendState
    public let txFrameCount: UInt64
    public let txByteCount: UInt64
    public let rxCallbackCount: UInt64
    /// Number of output render completions observed by the backend.
    ///
    /// On iOS this tracks RemoteIO render callbacks.
    /// On macOS this tracks scheduled player-buffer completion callbacks.
    public let outputCallbackCount: UInt64
    /// Number of queued output samples that have not been fully rendered yet.
    public let pendingOutputSampleCount: UInt64
    public let configuredSampleRateHz: UInt32
    public let observedInputSampleRateHz: UInt32
    public let observedOutputSampleRateHz: UInt32
}

/// Error values emitted by backend bootstrapping and OS audio calls.
public enum AudioBackendError: Error, CustomStringConvertible {
    case unsupportedPlatform(String)
    case startupFailed(String)
    case osStatus(String, Int32)

    public var description: String {
        switch self {
        case .unsupportedPlatform(let text):
            return "unsupported audio backend platform: \(text)"
        case .startupFailed(let text):
            return "audio backend startup failed: \(text)"
        case .osStatus(let operation, let status):
            return "\(operation) failed with OSStatus=\(status)"
        }
    }
}

private final class AudioCounterBox {
    private let lock = NSLock()
    private var txFrames: UInt64 = 0
    private var txBytes: UInt64 = 0
    private var rxCallbacks: UInt64 = 0
    private var outputCallbacks: UInt64 = 0

    func recordTx(bytes: Int) {
        lock.lock()
        txFrames &+= 1
        txBytes &+= UInt64(bytes)
        lock.unlock()
    }

    func recordRxCallback() {
        lock.lock()
        rxCallbacks &+= 1
        lock.unlock()
    }

    func recordOutputCallback() {
        lock.lock()
        outputCallbacks &+= 1
        lock.unlock()
    }

    func snapshot(backend: String, state: AudioBackendState) -> AudioBackendDiagnostics {
        snapshot(
            backend: backend,
            state: state,
            pendingOutputSampleCount: 0,
            configuredSampleRateHz: 0,
            observedInputSampleRateHz: 0,
            observedOutputSampleRateHz: 0
        )
    }

    // swiftlint:disable:next function_parameter_count
    func snapshot(
        backend: String,
        state: AudioBackendState,
        pendingOutputSampleCount: UInt64,
        configuredSampleRateHz: UInt32,
        observedInputSampleRateHz: UInt32,
        observedOutputSampleRateHz: UInt32
    ) -> AudioBackendDiagnostics {
        lock.lock()
        let diagnostics = AudioBackendDiagnostics(
            backend: backend,
            state: state,
            txFrameCount: txFrames,
            txByteCount: txBytes,
            rxCallbackCount: rxCallbacks,
            outputCallbackCount: outputCallbacks,
            pendingOutputSampleCount: pendingOutputSampleCount,
            configuredSampleRateHz: configuredSampleRateHz,
            observedInputSampleRateHz: observedInputSampleRateHz,
            observedOutputSampleRateHz: observedOutputSampleRateHz
        )
        lock.unlock()
        return diagnostics
    }
}

/// Streaming linear-interpolation resampler for audio callback pipelines.
///
/// This maintains phase continuity across callback boundaries and is used when
/// the platform route sample rate differs from the modem sample rate.
final class LinearStreamResampler {
    private let inputRateHz: Double
    private let outputRateHz: Double
    private let inputSamplesPerOutputSample: Double

    private var nextOutputInputIndex: Double = 0
    private var totalInputSamples: Int64 = 0
    private var lastSample: Float?

    init(inputRateHz: Double, outputRateHz: Double) {
        self.inputRateHz = max(inputRateHz, 1)
        self.outputRateHz = max(outputRateHz, 1)
        inputSamplesPerOutputSample = self.inputRateHz / self.outputRateHz
    }

    func reset() {
        nextOutputInputIndex = 0
        totalInputSamples = 0
        lastSample = nil
    }

    func process(_ input: [Float]) -> [Float] {
        input.withUnsafeBufferPointer { process($0) }
    }

    func process(_ input: UnsafeBufferPointer<Float>) -> [Float] {
        if input.isEmpty {
            return []
        }

        let hasCarry = (lastSample != nil)
        let startIndex = Double(totalInputSamples) - (hasCarry ? 1.0 : 0.0)
        if nextOutputInputIndex < startIndex {
            nextOutputInputIndex = startIndex
        }

        let combinedCount = input.count + (hasCarry ? 1 : 0)
        let expectedCount = max(
            0,
            Int((Double(input.count) * outputRateHz / inputRateHz).rounded()) + 2
        )
        var output: [Float] = []
        output.reserveCapacity(expectedCount)

        while true {
            let relative = nextOutputInputIndex - startIndex
            let leftIndex = Int(floor(relative))
            if leftIndex < 0 || (leftIndex + 1) >= combinedCount {
                break
            }

            let frac = Float(relative - Double(leftIndex))
            let left = sampleAtCombinedIndex(
                leftIndex,
                hasCarry: hasCarry,
                carry: lastSample,
                input: input
            )
            let right = sampleAtCombinedIndex(
                leftIndex + 1,
                hasCarry: hasCarry,
                carry: lastSample,
                input: input
            )
            output.append(left + ((right - left) * frac))
            nextOutputInputIndex += inputSamplesPerOutputSample
        }

        lastSample = input[input.count - 1]
        totalInputSamples += Int64(input.count)
        return output
    }

    private func sampleAtCombinedIndex(
        _ index: Int,
        hasCarry: Bool,
        carry: Float?,
        input: UnsafeBufferPointer<Float>
    ) -> Float {
        if hasCarry {
            if index == 0 {
                return carry ?? 0
            }
            return input[index - 1]
        }
        return input[index]
    }
}

/// Audible tri-tone beacon used for local speaker verification in test labs.
enum AudibleBeaconSynthesizer {
    static func synthesize(role _: Role, sampleRate: Double, txGainCap: Float) -> [Float] {
        let fs = min(max(sampleRate.rounded(), 8_000), 192_000)
        // Keep the validation beacon clearly audible on iPhone speaker paths that may attenuate
        // play-and-record sessions (measurement mode + duplex routing).
        let amplitude = min(max(txGainCap, 0), 0.30)
        if amplitude <= 0 {
            return []
        }

        // Use the same low-high-low tri-tone for both roles so cross-device A/B checks match.
        var pattern = [(freqHz: Double, durationSec: Double)]()
        pattern.append((440, 0.28))
        pattern.append((0, 0.08))
        pattern.append((880, 0.28))
        pattern.append((0, 0.08))
        pattern.append((440, 0.28))

        var out: [Float] = []
        out.reserveCapacity(Int(fs * 1.0))
        for segment in pattern {
            out.append(
                contentsOf: makeSegment(
                    frequencyHz: segment.freqHz,
                    durationSec: segment.durationSec,
                    sampleRate: fs,
                    amplitude: amplitude
                )
            )
        }
        return out
    }

    private static func makeSegment(
        frequencyHz: Double,
        durationSec: Double,
        sampleRate: Double,
        amplitude: Float
    ) -> [Float] {
        let sampleCount = max(1, Int((durationSec * sampleRate).rounded()))
        if frequencyHz <= 0 {
            return [Float](repeating: 0, count: sampleCount)
        }

        let rampSamples = min(max(16, Int(sampleRate * 0.020)), sampleCount / 2)
        let phaseStep = Float((2.0 * Double.pi * frequencyHz) / sampleRate)
        var phase: Float = 0
        var out = [Float](repeating: 0, count: sampleCount)
        for idx in 0..<sampleCount {
            let envelope: Float
            if idx < rampSamples {
                // Raised-cosine window reduces edge clicks on tiny speakers.
                let x = Float(idx) / Float(max(1, rampSamples))
                envelope = 0.5 - (0.5 * cos(Float.pi * x))
            } else if idx >= (sampleCount - rampSamples) {
                let remaining = Float(sampleCount - idx - 1)
                let x = remaining / Float(max(1, rampSamples))
                envelope = 0.5 - (0.5 * cos(Float.pi * x))
            } else {
                envelope = 1
            }
            out[idx] = amplitude * max(0, envelope) * sin(phase)
            phase += phaseStep
        }
        return out
    }
}

enum StubWaveSynthesizer {
    static func synthesize(
        symbols: [UInt8],
        sampleRate: Double,
        txGainCap: Float,
        symbolLimit: Int = 64
    ) -> [Float] {
        if symbols.isEmpty || sampleRate <= 0 {
            return []
        }

        let cappedSymbols = Array(symbols.prefix(max(0, symbolLimit)))
        if let vDSPWaveform = tryVDSPWaveform(
            symbols: cappedSymbols,
            sampleRate: sampleRate,
            txGainCap: txGainCap
        ) {
            return vDSPWaveform
        }
        return toneFallbackWaveform(
            symbols: cappedSymbols,
            sampleRate: sampleRate,
            txGainCap: txGainCap
        )
    }

    private static func tryVDSPWaveform(
        symbols: [UInt8],
        sampleRate: Double,
        txGainCap: Float
    ) -> [Float]? {
        if sampleRate <= 0 {
            return nil
        }

        let clampedSampleRate = min(max(sampleRate.rounded(), 8_000), 192_000)
        let config = VDSPOFDMConfig(
            sampleRateHz: UInt32(clampedSampleRate),
            txGainCap: min(max(txGainCap, 0), 0.12)
        )
        return try? VDSPPHY.modulateOFDMQPSK(payload: symbols, config: config)
    }

    private static func toneFallbackWaveform(
        symbols: [UInt8],
        sampleRate: Double,
        txGainCap: Float
    ) -> [Float] {
        let samplesPerSymbol = max(Int(sampleRate * 0.002), 48)
        let totalSamples = symbols.count * samplesPerSymbol
        var out = [Float](repeating: 0, count: totalSamples)
        let nyquistSafe = max(19_000.0, (sampleRate * 0.5) - 500.0)
        let base = min(18_500.0, nyquistSafe - 800.0)
        let span = max(600.0, nyquistSafe - base)
        let amplitude = min(max(txGainCap, 0), 0.12)
        var cursor = 0

        for symbol in symbols {
            let tone = base + (Double(symbol % 31) / 30.0) * span
            let delta = Float(2.0 * .pi * tone / sampleRate)
            var phase: Float = 0
            for _ in 0..<samplesPerSymbol {
                out[cursor] = amplitude * sin(phase)
                phase += delta
                cursor += 1
            }
        }
        return out
    }
}

protocol SessionFrameIOBackend: AnyObject {
    var diagnostics: AudioBackendDiagnostics { get }
    func attachSessionHandle(_ handle: OpaquePointer)
    func start() throws
    func stop()
    func handleOutboundFrame(_ frame: UnsafeBufferPointer<UInt8>) -> Int32
    func playLocalAudibleBeacon() -> Int32
}

enum AudioBackendFactory {
    static func make(config: Config) throws -> any SessionFrameIOBackend {
        switch config.transportBackend {
        case .inMemory:
            throw AudioBackendError.startupFailed("in-memory transport does not require audio backend")
        case .appleAudioScaffold:
            #if os(iOS)
                return try IOSRemoteIOAudioBackend(config: config)
            #elseif os(macOS)
                return MacEngineAudioBackend(config: config)
            #else
                throw AudioBackendError.unsupportedPlatform(
                    "appleAudioScaffold currently supports iOS and macOS")
            #endif
        }
    }
}

#if os(iOS)
    private struct TimestampLogger {
        private enum Level {
            case info
            case warning
            case error
        }

        private let base: Logger

        init(subsystem: String, category: String) {
            base = Logger(subsystem: subsystem, category: category)
        }

        func info(_ message: String) {
            emit(.info, message)
        }

        func warning(_ message: String) {
            emit(.warning, message)
        }

        func error(_ message: String) {
            emit(.error, message)
        }

        private func emit(_ level: Level, _ message: String) {
            let monoMs = Int((ProcessInfo.processInfo.systemUptime * 1000).rounded())
            let wallMs = Int64((Date().timeIntervalSince1970 * 1000).rounded())
            let prefixed = "tMonoMs=\(monoMs) tWallMs=\(wallMs) \(message)"
            switch level {
            case .info:
                base.info("\(prefixed, privacy: .public)")
            case .warning:
                base.warning("\(prefixed, privacy: .public)")
            case .error:
                base.error("\(prefixed, privacy: .public)")
            }
        }
    }

    // swiftlint:disable type_body_length
    private final class IOSRemoteIOAudioBackend: SessionFrameIOBackend, @unchecked Sendable {
        private enum TXOutputSampleFormat {
            case float32
            case int16
        }

        private final class BeaconPlayerDelegate: NSObject, AVAudioPlayerDelegate {
            var onFinish: (() -> Void)?

            func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
                _ = player
                _ = flag
                onFinish?()
            }

            func audioPlayerDecodeErrorDidOccur(_ player: AVAudioPlayer, error: Error?) {
                _ = player
                _ = error
                onFinish?()
            }
        }

        private let config: Config
        private let counters = AudioCounterBox()
        private let phyLink: AcousticPHYLink
        private let txQueueLock = NSLock()
        private let log = TimestampLogger(subsystem: "com.dweekly.cyrinx", category: "ios-remoteio")
        private var audioUnit: AudioUnit?
        private var beaconPlayer: AVAudioPlayer?
        private var beaconPlayerDelegate: BeaconPlayerDelegate?
        private var sessionHandle: OpaquePointer?
        private var txSampleQueue: [Float] = []
        private var txSampleReadIndex: Int = 0
        private var state: AudioBackendState = .idle
        private var beaconSessionOverrideActive = false
        private var remoteIOWasPausedForBeacon = false
        private var observedInputSampleRateHz: UInt32 = 0
        private var observedOutputSampleRateHz: UInt32 = 0
        private var renderCallbackLogCount: UInt32 = 0
        private var outputUnderrunLogCount: UInt32 = 0
        private var enqueueLogCount: UInt32 = 0
        private var renderCallbacksTotal: UInt64 = 0
        private var copiedAfterEnqueueLogCount: UInt32 = 0
        private var renderBufferShapeLogCount: UInt32 = 0
        private var renderSilenceFlagLogCount: UInt32 = 0
        private var renderOutputSampleLogCount: UInt32 = 0
        private var renderCadenceLogCount: UInt32 = 0
        private var renderLastMonotonicMs: Int = 0
        private var captureScratch: [Float] = []
        private let inboundProcessingQueue = DispatchQueue(
            label: "com.dweekly.cyrinx.ios-remoteio-rx",
            qos: .userInitiated
        )
        private let inboundQueueLock = NSLock()
        private var inboundQueuedSamples: Int = 0
        private var inboundDropLogCount: UInt32 = 0
        private let inboundQueueSoftLimitSamples: Int = 96_000
        private var txOutputSampleFormat: TXOutputSampleFormat = .float32
        private var txOutputChannelCount: Int = 1
        private var txOutputIsInterleaved: Bool = false
        private let outputIsSilenceFlag = AudioUnitRenderActionFlags(rawValue: 1 << 4)

        init(config: Config) throws {
            self.config = config
            phyLink = AcousticPHYLink(config: config)
            try configureAudioSession()
            try createAudioUnit()
        }

        var diagnostics: AudioBackendDiagnostics {
            counters.snapshot(
                backend: "ios-remoteio",
                state: state,
                pendingOutputSampleCount: pendingTxSamples(),
                configuredSampleRateHz: config.sampleRateHz,
                observedInputSampleRateHz: observedInputSampleRateHz,
                observedOutputSampleRateHz: observedOutputSampleRateHz
            )
        }

        func attachSessionHandle(_ handle: OpaquePointer) {
            sessionHandle = handle
        }

        func start() throws {
            guard let audioUnit else {
                throw AudioBackendError.startupFailed("RemoteIO unit not initialized")
            }

            try configureAudioSession()
            try checkOSStatus(AudioUnitInitialize(audioUnit), operation: "AudioUnitInitialize")
            try checkOSStatus(AudioOutputUnitStart(audioUnit), operation: "AudioOutputUnitStart")
            forceSpeakerRoute(reason: "start")
            renderCallbackLogCount = 0
            outputUnderrunLogCount = 0
            enqueueLogCount = 0
            renderCallbacksTotal = 0
            copiedAfterEnqueueLogCount = 0
            renderBufferShapeLogCount = 0
            renderSilenceFlagLogCount = 0
            renderOutputSampleLogCount = 0
            renderCadenceLogCount = 0
            renderLastMonotonicMs = 0
            inboundDropLogCount = 0
            inboundQueueLock.lock()
            inboundQueuedSamples = 0
            inboundQueueLock.unlock()
            let roleName = config.role == .master ? "master" : "slave"
            log.info("RemoteIO started role=\(roleName) cfgHz=\(self.config.sampleRateHz)")
            log.info("RemoteIO observed inHz=\(self.observedInputSampleRateHz)")
            log.info("RemoteIO observed outHz=\(self.observedOutputSampleRateHz)")
            state = .running
        }

        func stop() {
            beaconPlayer?.stop()
            beaconPlayer = nil
            beaconPlayerDelegate = nil
            finishDedicatedBeaconPlaybackIfNeeded(resumeRemoteIO: false)
            guard let audioUnit else {
                return
            }

            _ = AudioOutputUnitStop(audioUnit)
            _ = AudioUnitUninitialize(audioUnit)
            log.info("RemoteIO stopped")
            inboundQueueLock.lock()
            inboundQueuedSamples = 0
            inboundQueueLock.unlock()
            state = .stopped
        }

        func handleOutboundFrame(_ frame: UnsafeBufferPointer<UInt8>) -> Int32 {
            counters.recordTx(bytes: frame.count)
            if frame.isEmpty {
                return 0
            }

            let frameBytes = Array(frame)
            let waveform =
                (try? phyLink.encode(frame: frameBytes))
                ?? StubWaveSynthesizer.synthesize(
                    symbols: frameBytes,
                    sampleRate: Double(config.sampleRateHz),
                    txGainCap: config.txGainCap
                )
            enqueueTxSamples(waveform)
            return 0
        }

        func playLocalAudibleBeacon() -> Int32 {
            if state != .running {
                return CYRINX_ERR_NOT_RUNNING.rawValue
            }
            let requestMonoMs = monotonicMs()
            let requestWallMs = wallClockMs()
            let sampleRate =
                observedOutputSampleRateHz > 0
                ? Double(observedOutputSampleRateHz)
                : Double(config.sampleRateHz)
            let beacon = AudibleBeaconSynthesizer.synthesize(
                role: config.role,
                sampleRate: sampleRate,
                txGainCap: config.txGainCap
            )
            let beaconPeak = peakAbs(beacon)
            let beaconDurationSec = sampleRate > 0 ? Double(beacon.count) / sampleRate : 0
            counters.recordTx(bytes: beacon.count)
            if AVAudioSession.sharedInstance().outputVolume <= 0.01 {
                log.warning("system output volume is near zero; beacon audibility may be poor")
            }
            forceSpeakerRoute(reason: "beacon")
            log.info("audible beacon request monoMs=\(requestMonoMs) wallMs=\(requestWallMs)")
            log.info("audible beacon role=\(self.config.role) sr=\(Int(sampleRate))Hz")
            log.info("audible beacon durationSec=\(beaconDurationSec) samples=\(beacon.count)")
            enqueueTxSamples(beacon)
            if let audioUnit {
                let startStatus = AudioOutputUnitStart(audioUnit)
                if startStatus != noErr {
                    log.error("AudioOutputUnitStart(kick) failed status=\(startStatus)")
                } else {
                    log.info("AudioOutputUnitStart(kick) ok")
                }
            }
            let pending = pendingTxSamples()
            log.info("audible beacon peakAbs=\(beaconPeak)")
            log.info("queued audible beacon samples=\(beacon.count) pendingSamples=\(pending)")
            return CYRINX_OK.rawValue
        }

        private func playAudibleBeaconViaAVAudioPlayer(_ samples: [Float], sampleRate: Double) -> Bool {
            guard !samples.isEmpty else {
                return false
            }
            let playRequestStartMs = monotonicMs()
            let wavData = makePCM16WAV(samples: samples, sampleRate: sampleRate)
            let dedicatedSessionReady = prepareSessionForDedicatedBeaconPlayback(
                sampleRate: sampleRate
            )
            do {
                beaconPlayer?.stop()
                beaconPlayer = nil
                beaconPlayerDelegate = nil
                let player = try AVAudioPlayer(data: wavData)
                let delegate = BeaconPlayerDelegate()
                delegate.onFinish = { [weak self] in
                    self?.finishDedicatedBeaconPlaybackIfNeeded()
                }
                player.delegate = delegate
                player.volume = 1.0
                player.prepareToPlay()
                let started = player.play()
                beaconPlayerDelegate = delegate
                beaconPlayer = player
                let playLatencyMs = monotonicMs() - playRequestStartMs
                log.info("beacon AVAudioPlayer started=\(started) durationSec=\(player.duration)")
                let wallMs = wallClockMs()
                log.info("beacon AVAudioPlayer latencyMs=\(playLatencyMs) t0Ms=\(playRequestStartMs)")
                log.info("beacon AVAudioPlayer wallMs=\(wallMs)")
                if !started {
                    finishDedicatedBeaconPlaybackIfNeeded()
                }
                return started
            } catch {
                log.error("beacon AVAudioPlayer failed: \(error.localizedDescription)")
                if dedicatedSessionReady {
                    finishDedicatedBeaconPlaybackIfNeeded()
                }
                return false
            }
        }

        private func prepareSessionForDedicatedBeaconPlayback(sampleRate _: Double) -> Bool {
            let prepStartMs = monotonicMs()
            let session = AVAudioSession.sharedInstance()
            // Do not pause RemoteIO or switch session category/mode for audible beacon.
            // The stop/reconfigure path introduces multi-second latency and breaks duplex continuity.
            remoteIOWasPausedForBeacon = false
            beaconSessionOverrideActive = false

            let route = session.currentRoute.outputs.map(\.portName).joined(separator: ",")
            let activeHz = Int(session.sampleRate.rounded())
            let prepLatencyMs = monotonicMs() - prepStartMs
            let categoryName = session.category.rawValue
            let modeName = session.mode.rawValue
            log.info("AVAudioSession beacon cfg category=\(categoryName) mode=\(modeName)")
            log.info("AVAudioSession beacon route=\(route) hz=\(activeHz)")
            log.info("AVAudioSession beacon prepMs=\(prepLatencyMs) wallMs=\(self.wallClockMs())")
            return true
        }

        private func finishDedicatedBeaconPlaybackIfNeeded(resumeRemoteIO: Bool = true) {
            let hadOverride = beaconSessionOverrideActive
            let hadPause = remoteIOWasPausedForBeacon
            beaconSessionOverrideActive = false
            remoteIOWasPausedForBeacon = false
            if !hadOverride && !hadPause {
                return
            }

            let restoreStartMs = monotonicMs()
            do {
                try configureAudioSession()
            } catch {
                log.error("AVAudioSession restore after beacon failed: \(error.localizedDescription)")
            }
            let restoreMs = monotonicMs() - restoreStartMs
            log.info("AVAudioSession restore after beacon ms=\(restoreMs)")

            guard resumeRemoteIO, hadPause else {
                return
            }
            guard state == .running, let audioUnit else {
                return
            }
            let resumeStartMs = monotonicMs()
            let startStatus = AudioOutputUnitStart(audioUnit)
            if startStatus != noErr {
                log.error("AudioOutputUnitStart(resume after beacon) failed status=\(startStatus)")
            } else {
                let resumeMs = monotonicMs() - resumeStartMs
                log.info("RemoteIO resumed after dedicated beacon playback ms=\(resumeMs)")
            }
        }

        private func monotonicMs() -> Int {
            Int((ProcessInfo.processInfo.systemUptime * 1000).rounded())
        }

        private func wallClockMs() -> Int64 {
            Int64((Date().timeIntervalSince1970 * 1000).rounded())
        }

        private func configureAudioSession() throws {
            let session = AVAudioSession.sharedInstance()
            let useMeasurement = ProcessInfo.processInfo.environment["CYRINX_IOS_MEASUREMENT_MODE"] == "1"
            let mode: AVAudioSession.Mode = useMeasurement ? .measurement : .default
            try session.setCategory(.playAndRecord, mode: mode, options: [.defaultToSpeaker])
            try session.setPreferredSampleRate(Double(config.sampleRateHz))
            try session.setPreferredIOBufferDuration(0.01)
            try session.setActive(true, options: [])
            try session.overrideOutputAudioPort(.speaker)
            let observedRate = UInt32(session.sampleRate.rounded())
            observedInputSampleRateHz = observedRate
            observedOutputSampleRateHz = observedRate
            let route = session.currentRoute.outputs.map(\.portName).joined(separator: ",")
            let permission = micPermissionDescription()
            let preferredHz = Int(session.preferredSampleRate)
            let actualHz = Int(session.sampleRate)
            let modeName = session.mode.rawValue
            log.info("AVAudioSession cfg prefHz=\(preferredHz) actualHz=\(actualHz)")
            log.info("AVAudioSession mode=\(modeName) measurementOptIn=\(useMeasurement)")
            log.info("AVAudioSession route=\(route) micPerm=\(permission) outVol=\(session.outputVolume)")
        }

        private func forceSpeakerRoute(reason: String) {
            let session = AVAudioSession.sharedInstance()
            do {
                try session.overrideOutputAudioPort(.speaker)
                let route = session.currentRoute.outputs.map(\.portName).joined(separator: ",")
                log.info("AVAudioSession speaker route reason=\(reason) route=\(route)")
            } catch {
                let message = "AVAudioSession speaker route failed reason=\(reason)"
                log.error("\(message) err=\(error.localizedDescription)")
            }
        }

        private func micPermissionDescription() -> String {
            switch AVAudioApplication.shared.recordPermission {
            case .granted:
                return "granted"
            case .denied:
                return "denied"
            case .undetermined:
                return "undetermined"
            @unknown default:
                return "unknown"
            }
        }

        private func createAudioUnit() throws {
            let unit = try allocateAudioUnit()
            try configureIO(unit: unit)
            try configureStreamFormat(unit: unit)
            try configureCallbacks(unit: unit)
            logStreamFormat(unit: unit, scope: kAudioUnitScope_Input, bus: 0, label: "render input bus0")
            logStreamFormat(unit: unit, scope: kAudioUnitScope_Output, bus: 1, label: "capture output bus1")
            audioUnit = unit
        }

        private func allocateAudioUnit() throws -> AudioUnit {
            var description = AudioComponentDescription(
                componentType: kAudioUnitType_Output,
                componentSubType: kAudioUnitSubType_RemoteIO,
                componentManufacturer: kAudioUnitManufacturer_Apple,
                componentFlags: 0,
                componentFlagsMask: 0
            )
            guard let component = AudioComponentFindNext(nil, &description) else {
                throw AudioBackendError.startupFailed("RemoteIO component not found")
            }

            var unit: AudioUnit?
            try checkOSStatus(
                AudioComponentInstanceNew(component, &unit),
                operation: "AudioComponentInstanceNew")
            guard let unit else {
                throw AudioBackendError.startupFailed("RemoteIO instance allocation failed")
            }
            return unit
        }

        private func configureIO(unit: AudioUnit) throws {
            var enable: UInt32 = 1
            try setAudioUnitProperty(
                unit: unit,
                spec: AudioUnitPropertySpec(
                    property: kAudioOutputUnitProperty_EnableIO,
                    scope: kAudioUnitScope_Input,
                    bus: 1,
                    operation: "EnableIO(input)"
                ),
                value: &enable
            )
            try setAudioUnitProperty(
                unit: unit,
                spec: AudioUnitPropertySpec(
                    property: kAudioOutputUnitProperty_EnableIO,
                    scope: kAudioUnitScope_Output,
                    bus: 0,
                    operation: "EnableIO(output)"
                ),
                value: &enable
            )
        }

        private func configureStreamFormat(unit: AudioUnit) throws {
            var captureFormat = makeFloatASBD(sampleRate: Double(config.sampleRateHz), channels: 1)
            try setAudioUnitProperty(
                unit: unit,
                spec: AudioUnitPropertySpec(
                    property: kAudioUnitProperty_StreamFormat,
                    scope: kAudioUnitScope_Output,
                    bus: 1,
                    operation: "SetStreamFormat(input bus)"
                ),
                value: &captureFormat
            )
            try configureOutputRenderFormat(unit: unit)
        }

        private func configureOutputRenderFormat(unit: AudioUnit) throws {
            let sampleRate = Double(config.sampleRateHz)
            var selected = false

            var int16Stereo = makeInt16ASBD(sampleRate: sampleRate, channels: 2)
            selected = trySetOutputRenderFormat(
                unit: unit,
                format: &int16Stereo,
                label: "int16 stereo"
            )
            if !selected {
                var int16Mono = makeInt16ASBD(sampleRate: sampleRate, channels: 1)
                selected = trySetOutputRenderFormat(
                    unit: unit,
                    format: &int16Mono,
                    label: "int16 mono"
                )
            }
            if !selected {
                var floatStereo = makeFloatASBD(sampleRate: sampleRate, channels: 2)
                selected = trySetOutputRenderFormat(
                    unit: unit,
                    format: &floatStereo,
                    label: "float32 stereo"
                )
            }
            if !selected {
                var floatMono = makeFloatASBD(sampleRate: sampleRate, channels: 1)
                selected = trySetOutputRenderFormat(
                    unit: unit,
                    format: &floatMono,
                    label: "float32 mono"
                )
            }
            if !selected {
                throw AudioBackendError.startupFailed("RemoteIO output stream format negotiation failed")
            }
            try refreshActiveOutputRenderFormat(unit: unit)
        }

        private func trySetOutputRenderFormat(
            unit: AudioUnit,
            format: inout AudioStreamBasicDescription,
            label: String
        ) -> Bool {
            do {
                try setAudioUnitProperty(
                    unit: unit,
                    spec: AudioUnitPropertySpec(
                        property: kAudioUnitProperty_StreamFormat,
                        scope: kAudioUnitScope_Input,
                        bus: 0,
                        operation: "SetStreamFormat(output bus \(label))"
                    ),
                    value: &format
                )
                log.info("RemoteIO output format requested: \(label)")
                return true
            } catch {
                log.info("RemoteIO output format rejected: \(label)")
                return false
            }
        }

        private func refreshActiveOutputRenderFormat(unit: AudioUnit) throws {
            var asbd = AudioStreamBasicDescription()
            var size = UInt32(MemoryLayout<AudioStreamBasicDescription>.size)
            try checkOSStatus(
                AudioUnitGetProperty(
                    unit,
                    kAudioUnitProperty_StreamFormat,
                    kAudioUnitScope_Input,
                    0,
                    &asbd,
                    &size
                ),
                operation: "GetStreamFormat(output bus active)"
            )

            let flags = asbd.mFormatFlags
            let isFloat = (flags & kAudioFormatFlagIsFloat) != 0
            let isInt16 = (flags & kAudioFormatFlagIsSignedInteger) != 0 && asbd.mBitsPerChannel == 16
            let flagsHex = String(flags, radix: 16)
            if isFloat && asbd.mBitsPerChannel == 32 {
                txOutputSampleFormat = .float32
            } else if isInt16 {
                txOutputSampleFormat = .int16
            } else {
                let unsupported = "unsupported RemoteIO output format bits=\(asbd.mBitsPerChannel)"
                throw AudioBackendError.startupFailed(
                    "\(unsupported) flags=0x\(flagsHex)"
                )
            }

            txOutputChannelCount = max(1, Int(asbd.mChannelsPerFrame))
            txOutputIsInterleaved = (flags & kAudioFormatFlagIsNonInterleaved) == 0
            let fmt = txOutputSampleFormat == .int16 ? "int16" : "float32"
            let activeChannels = txOutputChannelCount
            let activeInterleaved = txOutputIsInterleaved
            log.info(
                "RemoteIO out fmt=\(fmt) ch=\(activeChannels) ilv=\(activeInterleaved)"
            )
            log.info(
                "RemoteIO out bpf=\(asbd.mBytesPerFrame) bits=\(asbd.mBitsPerChannel) flags=0x\(flagsHex)"
            )
        }

        private func makeFloatASBD(sampleRate: Double, channels: UInt32) -> AudioStreamBasicDescription {
            let clampedChannels = max(1, channels)
            let bytesPerSample: UInt32 = 4
            let bytesPerFrame = bytesPerSample * clampedChannels
            return AudioStreamBasicDescription(
                mSampleRate: sampleRate,
                mFormatID: kAudioFormatLinearPCM,
                mFormatFlags: kAudioFormatFlagIsFloat | kAudioFormatFlagIsPacked,
                mBytesPerPacket: bytesPerFrame,
                mFramesPerPacket: 1,
                mBytesPerFrame: bytesPerFrame,
                mChannelsPerFrame: clampedChannels,
                mBitsPerChannel: 32,
                mReserved: 0
            )
        }

        private func makeInt16ASBD(sampleRate: Double, channels: UInt32) -> AudioStreamBasicDescription {
            let clampedChannels = max(1, channels)
            let bytesPerSample: UInt32 = 2
            let bytesPerFrame = bytesPerSample * clampedChannels
            return AudioStreamBasicDescription(
                mSampleRate: sampleRate,
                mFormatID: kAudioFormatLinearPCM,
                mFormatFlags: kAudioFormatFlagIsSignedInteger | kAudioFormatFlagIsPacked,
                mBytesPerPacket: bytesPerFrame,
                mFramesPerPacket: 1,
                mBytesPerFrame: bytesPerFrame,
                mChannelsPerFrame: clampedChannels,
                mBitsPerChannel: 16,
                mReserved: 0
            )
        }

        private func configureCallbacks(unit: AudioUnit) throws {
            var inputCallback = AURenderCallbackStruct(
                inputProc: remoteIOInputCallback,
                inputProcRefCon: UnsafeMutableRawPointer(Unmanaged.passUnretained(self).toOpaque())
            )
            try setAudioUnitProperty(
                unit: unit,
                spec: AudioUnitPropertySpec(
                    property: kAudioOutputUnitProperty_SetInputCallback,
                    scope: kAudioUnitScope_Global,
                    bus: 1,
                    operation: "SetInputCallback"
                ),
                value: &inputCallback
            )

            var renderCallback = AURenderCallbackStruct(
                inputProc: remoteIORenderCallback,
                inputProcRefCon: UnsafeMutableRawPointer(Unmanaged.passUnretained(self).toOpaque())
            )
            try setAudioUnitProperty(
                unit: unit,
                spec: AudioUnitPropertySpec(
                    property: kAudioUnitProperty_SetRenderCallback,
                    scope: kAudioUnitScope_Input,
                    bus: 0,
                    operation: "SetRenderCallback"
                ),
                value: &renderCallback
            )
        }

        private struct AudioUnitPropertySpec {
            let property: AudioUnitPropertyID
            let scope: AudioUnitScope
            let bus: AudioUnitElement
            let operation: String
        }

        private func setAudioUnitProperty(
            unit: AudioUnit,
            spec: AudioUnitPropertySpec,
            value: inout UInt32
        ) throws {
            try checkOSStatus(
                AudioUnitSetProperty(
                    unit,
                    spec.property,
                    spec.scope,
                    spec.bus,
                    &value,
                    UInt32(MemoryLayout<UInt32>.size)
                ),
                operation: spec.operation
            )
        }

        private func setAudioUnitProperty(
            unit: AudioUnit,
            spec: AudioUnitPropertySpec,
            value: inout AudioStreamBasicDescription
        ) throws {
            try checkOSStatus(
                AudioUnitSetProperty(
                    unit,
                    spec.property,
                    spec.scope,
                    spec.bus,
                    &value,
                    UInt32(MemoryLayout<AudioStreamBasicDescription>.size)
                ),
                operation: spec.operation
            )
        }

        private func setAudioUnitProperty(
            unit: AudioUnit,
            spec: AudioUnitPropertySpec,
            value: inout AURenderCallbackStruct
        ) throws {
            try checkOSStatus(
                AudioUnitSetProperty(
                    unit,
                    spec.property,
                    spec.scope,
                    spec.bus,
                    &value,
                    UInt32(MemoryLayout<AURenderCallbackStruct>.size)
                ),
                operation: spec.operation
            )
        }

        private func logStreamFormat(
            unit: AudioUnit,
            scope: AudioUnitScope,
            bus: AudioUnitElement,
            label: String
        ) {
            var asbd = AudioStreamBasicDescription()
            var size = UInt32(MemoryLayout<AudioStreamBasicDescription>.size)
            let rc = AudioUnitGetProperty(
                unit,
                kAudioUnitProperty_StreamFormat,
                scope,
                bus,
                &asbd,
                &size
            )
            if rc != noErr {
                log.error("stream format read failed label=\(label) status=\(rc)")
                return
            }
            let sr = Int(asbd.mSampleRate)
            let channels = asbd.mChannelsPerFrame
            let bytesPerFrame = asbd.mBytesPerFrame
            let flagsHex = String(asbd.mFormatFlags, radix: 16)
            log.info(
                "stream format \(label) sr=\(sr) ch=\(channels) bpf=\(bytesPerFrame) flags=0x\(flagsHex)"
            )
        }

        fileprivate func capture(
            ioActionFlags: UnsafeMutablePointer<AudioUnitRenderActionFlags>,
            inTimeStamp: UnsafePointer<AudioTimeStamp>,
            inBusNumber: UInt32,
            inNumberFrames: UInt32
        ) -> OSStatus {
            guard let audioUnit else {
                return noErr
            }
            let frameCount = max(0, Int(inNumberFrames))
            if frameCount <= 0 {
                return noErr
            }
            if captureScratch.count < frameCount {
                captureScratch = [Float](repeating: 0, count: frameCount)
            }
            let byteCount = frameCount * MemoryLayout<Float>.size

            var buffers = AudioBufferList(
                mNumberBuffers: 1,
                mBuffers: AudioBuffer(
                    mNumberChannels: 1,
                    mDataByteSize: UInt32(byteCount),
                    mData: nil
                )
            )
            let status = captureScratch.withUnsafeMutableBufferPointer { ptr -> OSStatus in
                guard let base = ptr.baseAddress else {
                    return noErr
                }
                buffers.mBuffers.mData = UnsafeMutableRawPointer(base)
                return AudioUnitRender(
                    audioUnit,
                    ioActionFlags,
                    inTimeStamp,
                    inBusNumber,
                    inNumberFrames,
                    &buffers
                )
            }
            if status == noErr {
                counters.recordRxCallback()
                captureScratch.withUnsafeBufferPointer { ptr in
                    guard let base = ptr.baseAddress else {
                        return
                    }
                    let samples = UnsafeBufferPointer(start: base, count: frameCount)
                    enqueueInboundSamplesForProcessing(samples)
                }
            }
            return status
        }

        private func enqueueInboundSamplesForProcessing(_ samples: UnsafeBufferPointer<Float>) {
            if samples.isEmpty {
                return
            }
            let count = samples.count
            inboundQueueLock.lock()
            let nextQueued = inboundQueuedSamples + count
            let shouldQueue = nextQueued <= inboundQueueSoftLimitSamples
            if shouldQueue {
                inboundQueuedSamples = nextQueued
            }
            let queuedSnapshot = inboundQueuedSamples
            inboundQueueLock.unlock()
            if !shouldQueue {
                if inboundDropLogCount < 6 {
                    log.warning(
                        "drop inbound samples count=\(count) "
                            + "queued=\(queuedSnapshot) "
                            + "limit=\(inboundQueueSoftLimitSamples)"
                    )
                    inboundDropLogCount += 1
                }
                return
            }

            let chunk = Array(samples)
            inboundProcessingQueue.async { [weak self] in
                guard let self else {
                    return
                }
                defer {
                    self.inboundQueueLock.lock()
                    self.inboundQueuedSamples = max(0, self.inboundQueuedSamples - chunk.count)
                    self.inboundQueueLock.unlock()
                }
                chunk.withUnsafeBufferPointer { ptr in
                    self.ingestInboundSamples(ptr)
                }
            }
        }

        fileprivate func renderSilence(
            ioData: UnsafeMutablePointer<AudioBufferList>?,
            ioActionFlags: UnsafeMutablePointer<AudioUnitRenderActionFlags>?,
            inNumberFrames: UInt32
        ) -> OSStatus {
            guard let ioData else {
                return noErr
            }
            counters.recordOutputCallback()
            renderCallbacksTotal &+= 1
            let buffers = UnsafeMutableAudioBufferListPointer(ioData)
            let frameCount = Int(inNumberFrames)
            if frameCount <= 0 {
                return noErr
            }
            if renderCallbackLogCount < 3 {
                let pendingBefore = pendingTxSamples()
                log.info(
                    "render cb frames=\(frameCount) buffers=\(buffers.count) pendingBefore=\(pendingBefore)"
                )
                renderCallbackLogCount += 1
            }
            if renderCallbacksTotal % 100 == 0 {
                let pending = pendingTxSamples()
                let total = self.renderCallbacksTotal
                log.info("render cb total=\(total) pending=\(pending)")
            }
            logRenderCadenceIfNeeded()
            let totalCopied = renderOutputBuffers(buffers, frameCount: frameCount)
            updateRenderSilenceFlag(ioActionFlags: ioActionFlags, totalCopied: totalCopied)
            return noErr
        }

        private func logRenderCadenceIfNeeded() {
            let now = monotonicMs()
            if renderLastMonotonicMs == 0 {
                renderLastMonotonicMs = now
                return
            }
            let deltaMs = now - renderLastMonotonicMs
            renderLastMonotonicMs = now
            if renderCadenceLogCount < 12 {
                log.info("render cadence dtMs=\(deltaMs)")
                renderCadenceLogCount += 1
            }
        }

        private func renderOutputBuffers(
            _ buffers: UnsafeMutableAudioBufferListPointer,
            frameCount: Int
        ) -> Int {
            guard frameCount > 0 else {
                return 0
            }

            var mono = [Float](repeating: 0, count: frameCount)
            let copiedFrames = mono.withUnsafeMutableBufferPointer { ptr in
                dequeueTxSamples(into: ptr.baseAddress!, sampleCount: frameCount)
            }

            for idx in buffers.indices {
                var buffer = buffers[idx]
                guard let data = buffer.mData else {
                    continue
                }

                let bufferChannels = max(1, Int(buffer.mNumberChannels))
                let declaredFrames = declaredFrameCapacity(for: buffer, channels: bufferChannels)
                let framesToWrite = min(frameCount, declaredFrames)
                logRenderBufferShapeIfNeeded(
                    index: idx,
                    buffer: buffer,
                    frameCount: frameCount,
                    declaredFrames: declaredFrames,
                    channels: bufferChannels
                )
                writeMonoSamples(
                    mono,
                    copiedFrames: copiedFrames,
                    to: data,
                    plan: RenderWritePlan(
                        channels: bufferChannels,
                        framesToWrite: framesToWrite,
                        sampleCapacity: declaredFrames * bufferChannels
                    )
                )
                logRenderOutputSamplesIfNeeded(
                    data: data,
                    plan: RenderWritePlan(
                        channels: bufferChannels,
                        framesToWrite: framesToWrite,
                        sampleCapacity: declaredFrames * bufferChannels
                    ),
                    copiedFrames: copiedFrames
                )
                buffer.mDataByteSize = UInt32(declaredFrames * bufferChannels * outputBytesPerSample())
                buffers[idx] = buffer
            }

            logRenderCopiedIfNeeded(copied: copiedFrames, requested: frameCount, mono: mono)
            logRenderUnderrunIfNeeded(copied: copiedFrames, requested: frameCount)
            return copiedFrames
        }

        private func declaredFrameCapacity(for buffer: AudioBuffer, channels: Int) -> Int {
            let bytes = Int(buffer.mDataByteSize)
            let perFrame = max(1, outputBytesPerSample() * max(1, channels))
            let declared = bytes / perFrame
            if declared > 0 {
                return declared
            }
            return 0
        }

        private func outputBytesPerSample() -> Int {
            txOutputSampleFormat == .int16 ? MemoryLayout<Int16>.size : MemoryLayout<Float>.size
        }

        private func logRenderBufferShapeIfNeeded(
            index: Int,
            buffer: AudioBuffer,
            frameCount: Int,
            declaredFrames: Int,
            channels: Int
        ) {
            if renderBufferShapeLogCount >= 3 {
                return
            }
            let bytes = buffer.mDataByteSize
            let fmt = txOutputSampleFormat == .int16 ? "int16" : "float32"
            let configuredChannels = txOutputChannelCount
            let configuredInterleaved = txOutputIsInterleaved
            log.info(
                "render buf i=\(index) ch=\(channels) b=\(bytes) req=\(frameCount) fmt=\(fmt)"
            )
            log.info(
                "render layout dec=\(declaredFrames) cfg=\(configuredChannels)"
            )
            log.info(
                "render layout ilv=\(configuredInterleaved)"
            )
            renderBufferShapeLogCount += 1
        }

        private struct RenderWritePlan {
            let channels: Int
            let framesToWrite: Int
            let sampleCapacity: Int
        }

        private func writeMonoSamples(
            _ mono: [Float],
            copiedFrames: Int,
            to data: UnsafeMutableRawPointer,
            plan: RenderWritePlan
        ) {
            if plan.sampleCapacity <= 0 {
                return
            }
            if txOutputSampleFormat == .int16 {
                writeMonoSamplesInt16(mono, copiedFrames: copiedFrames, to: data, plan: plan)
                return
            }
            writeMonoSamplesFloat32(mono, copiedFrames: copiedFrames, to: data, plan: plan)
        }

        private func writeMonoSamplesInt16(
            _ mono: [Float],
            copiedFrames: Int,
            to data: UnsafeMutableRawPointer,
            plan: RenderWritePlan
        ) {
            let out = data.assumingMemoryBound(to: Int16.self)
            if plan.channels == 1 {
                for idx in 0..<plan.framesToWrite {
                    let s = idx < copiedFrames ? max(-1.0, min(1.0, mono[idx])) : 0
                    out[idx] = Int16((s * Float(Int16.max)).rounded())
                }
                if plan.framesToWrite < plan.sampleCapacity {
                    (out + plan.framesToWrite).initialize(
                        repeating: 0,
                        count: plan.sampleCapacity - plan.framesToWrite
                    )
                }
                return
            }
            var writeIndex = 0
            for frame in 0..<plan.framesToWrite {
                let s = frame < copiedFrames ? max(-1.0, min(1.0, mono[frame])) : 0
                let q = Int16((s * Float(Int16.max)).rounded())
                for _ in 0..<plan.channels {
                    out[writeIndex] = q
                    writeIndex += 1
                }
            }
            if writeIndex < plan.sampleCapacity {
                (out + writeIndex).initialize(repeating: 0, count: plan.sampleCapacity - writeIndex)
            }
        }

        private func writeMonoSamplesFloat32(
            _ mono: [Float],
            copiedFrames: Int,
            to data: UnsafeMutableRawPointer,
            plan: RenderWritePlan
        ) {
            let out = data.assumingMemoryBound(to: Float.self)
            if plan.channels == 1 {
                for idx in 0..<plan.framesToWrite {
                    out[idx] = idx < copiedFrames ? mono[idx] : 0
                }
                if plan.framesToWrite < plan.sampleCapacity {
                    (out + plan.framesToWrite).initialize(
                        repeating: 0,
                        count: plan.sampleCapacity - plan.framesToWrite
                    )
                }
                return
            }
            var writeIndex = 0
            for frame in 0..<plan.framesToWrite {
                let s = frame < copiedFrames ? mono[frame] : 0
                for _ in 0..<plan.channels {
                    out[writeIndex] = s
                    writeIndex += 1
                }
            }
            if writeIndex < plan.sampleCapacity {
                (out + writeIndex).initialize(repeating: 0, count: plan.sampleCapacity - writeIndex)
            }
        }

        private func logRenderCopiedIfNeeded(copied: Int, requested: Int, mono: [Float]) {
            if copied <= 0 || copiedAfterEnqueueLogCount >= 6 {
                return
            }
            let pendingAfterCopy = pendingTxSamples()
            var peak: Float = 0
            let limit = min(copied, mono.count)
            if limit > 0 {
                for idx in 0..<limit {
                    peak = max(peak, abs(mono[idx]))
                }
            }
            log.info("render copied=\(copied) req=\(requested) peak=\(peak) pending=\(pendingAfterCopy)")
            copiedAfterEnqueueLogCount += 1
        }

        private func logRenderUnderrunIfNeeded(copied: Int, requested: Int) {
            if copied >= requested || outputUnderrunLogCount >= 3 {
                return
            }
            let pendingAfter = pendingTxSamples()
            log.info("render underrun req=\(requested) copied=\(copied) pendingAfter=\(pendingAfter)")
            outputUnderrunLogCount += 1
        }

        private func logRenderOutputSamplesIfNeeded(
            data: UnsafeMutableRawPointer,
            plan: RenderWritePlan,
            copiedFrames: Int
        ) {
            if copiedFrames <= 0 || renderOutputSampleLogCount >= 4 || plan.sampleCapacity <= 0 {
                return
            }
            let previewCount = min(8, plan.sampleCapacity)
            if txOutputSampleFormat == .int16 {
                let out = data.assumingMemoryBound(to: Int16.self)
                var preview = [String]()
                preview.reserveCapacity(previewCount)
                for idx in 0..<previewCount {
                    preview.append(String(out[idx]))
                }
                log.info("render out preview fmt=int16 samples=[\(preview.joined(separator: ","))]")
            } else {
                let out = data.assumingMemoryBound(to: Float.self)
                var preview = [String]()
                preview.reserveCapacity(previewCount)
                for idx in 0..<previewCount {
                    preview.append(String(format: "%.5f", out[idx]))
                }
                log.info("render out preview fmt=float32 samples=[\(preview.joined(separator: ","))]")
            }
            renderOutputSampleLogCount += 1
        }

        private func updateRenderSilenceFlag(
            ioActionFlags: UnsafeMutablePointer<AudioUnitRenderActionFlags>?,
            totalCopied: Int
        ) {
            guard let ioActionFlags else {
                if renderSilenceFlagLogCount < 6 {
                    log.info("render silenceFlag missing totalCopied=\(totalCopied)")
                    renderSilenceFlagLogCount += 1
                }
                return
            }
            let before = ioActionFlags.pointee
            // Keep output callbacks flowing at real-time cadence; do not advertise silence.
            ioActionFlags.pointee.remove(outputIsSilenceFlag)
            if renderSilenceFlagLogCount < 12 {
                let after = ioActionFlags.pointee
                let beforeRaw = before.rawValue
                let afterRaw = after.rawValue
                let marked = after.contains(outputIsSilenceFlag)
                let beforeHex = String(beforeRaw, radix: 16)
                let afterHex = String(afterRaw, radix: 16)
                let silenceLogLine =
                    "render silenceFlag forceClear before=0x\(beforeHex) "
                    + "after=0x\(afterHex) marked=\(marked) totalCopied=\(totalCopied)"
                log.info(silenceLogLine)
                renderSilenceFlagLogCount += 1
            }
        }

        private func peakAbs(_ samples: [Float]) -> Float {
            var peak: Float = 0
            for sample in samples {
                peak = max(peak, abs(sample))
            }
            return peak
        }

        private func clearTxQueue() {
            txQueueLock.lock()
            txSampleQueue.removeAll(keepingCapacity: false)
            txSampleReadIndex = 0
            txQueueLock.unlock()
        }

        private func makePCM16WAV(samples: [Float], sampleRate: Double) -> Data {
            let clampedRate = UInt32(min(max(sampleRate.rounded(), 8_000), 192_000))
            let sampleCount = samples.count
            let pcmBytes = sampleCount * MemoryLayout<Int16>.size
            let totalBytes = 44 + pcmBytes
            var data = Data()
            data.reserveCapacity(totalBytes)

            data.append(contentsOf: [0x52, 0x49, 0x46, 0x46])  // RIFF
            data.append(contentsOf: le32(UInt32(totalBytes - 8)))
            data.append(contentsOf: [0x57, 0x41, 0x56, 0x45])  // WAVE
            data.append(contentsOf: [0x66, 0x6D, 0x74, 0x20])  // fmt
            data.append(contentsOf: le32(16))
            data.append(contentsOf: le16(1))  // PCM
            data.append(contentsOf: le16(1))  // mono
            data.append(contentsOf: le32(clampedRate))
            let byteRate = clampedRate * UInt32(MemoryLayout<Int16>.size)
            data.append(contentsOf: le32(byteRate))
            data.append(contentsOf: le16(UInt16(MemoryLayout<Int16>.size)))
            data.append(contentsOf: le16(16))
            data.append(contentsOf: [0x64, 0x61, 0x74, 0x61])  // data
            data.append(contentsOf: le32(UInt32(pcmBytes)))

            for sample in samples {
                let s = max(-1.0, min(1.0, sample))
                let i16 = Int16((s * Float(Int16.max)).rounded())
                data.append(contentsOf: le16(UInt16(bitPattern: i16)))
            }
            return data
        }

        private func le16(_ value: UInt16) -> [UInt8] {
            [UInt8(value & 0x00FF), UInt8((value >> 8) & 0x00FF)]
        }

        private func le32(_ value: UInt32) -> [UInt8] {
            var out = [UInt8]()
            out.reserveCapacity(4)
            out.append(UInt8(value & 0x000000FF))
            out.append(UInt8((value >> 8) & 0x000000FF))
            out.append(UInt8((value >> 16) & 0x000000FF))
            out.append(UInt8((value >> 24) & 0x000000FF))
            return out
        }

        private func enqueueTxSamples(_ samples: [Float]) {
            if samples.isEmpty {
                return
            }
            txQueueLock.lock()
            let before = max(0, txSampleQueue.count - txSampleReadIndex)
            txSampleQueue.append(contentsOf: samples)
            let after = max(0, txSampleQueue.count - txSampleReadIndex)
            txQueueLock.unlock()
            if enqueueLogCount < 12 {
                log.info(
                    "enqueue samples added=\(samples.count) pendingBefore=\(before) pendingAfter=\(after)"
                )
                enqueueLogCount += 1
            }
        }

        private func dequeueTxSamples(into out: UnsafeMutablePointer<Float>, sampleCount: Int) -> Int {
            txQueueLock.lock()
            let available = max(0, txSampleQueue.count - txSampleReadIndex)
            let copied = min(sampleCount, available)
            if copied > 0 {
                for idx in 0..<copied {
                    out[idx] = txSampleQueue[txSampleReadIndex + idx]
                }
                txSampleReadIndex += copied
            }

            if txSampleReadIndex > 4096, txSampleReadIndex * 2 > txSampleQueue.count {
                txSampleQueue.removeFirst(txSampleReadIndex)
                txSampleReadIndex = 0
            }
            txQueueLock.unlock()
            return copied
        }

        private func pendingTxSamples() -> UInt64 {
            txQueueLock.lock()
            let available = max(0, txSampleQueue.count - txSampleReadIndex)
            txQueueLock.unlock()
            return UInt64(available)
        }

        private func ingestInboundSamples(_ samples: UnsafeBufferPointer<Float>) {
            guard let sessionHandle else {
                return
            }

            let decodedFrames = phyLink.ingest(samples: samples)
            for decoded in decodedFrames where !decoded.frame.isEmpty {
                var report = decoded.report
                let rc = decoded.frame.withUnsafeBufferPointer { ptr in
                    cyrinx_ingest_frame(sessionHandle, ptr.baseAddress, ptr.count, &report)
                }
                if rc != CYRINX_OK.rawValue {
                    state = .failed
                }
            }
        }
    }
    // swiftlint:enable type_body_length

    // swiftlint:disable function_parameter_count
    private func remoteIOInputCallback(
        inRefCon: UnsafeMutableRawPointer,
        ioActionFlags: UnsafeMutablePointer<AudioUnitRenderActionFlags>,
        inTimeStamp: UnsafePointer<AudioTimeStamp>,
        inBusNumber: UInt32,
        inNumberFrames: UInt32,
        ioData: UnsafeMutablePointer<AudioBufferList>?
    ) -> OSStatus {
        _ = ioData
        let backend = Unmanaged<IOSRemoteIOAudioBackend>.fromOpaque(inRefCon).takeUnretainedValue()
        return backend.capture(
            ioActionFlags: ioActionFlags,
            inTimeStamp: inTimeStamp,
            inBusNumber: inBusNumber,
            inNumberFrames: inNumberFrames
        )
    }

    private func remoteIORenderCallback(
        inRefCon: UnsafeMutableRawPointer,
        ioActionFlags: UnsafeMutablePointer<AudioUnitRenderActionFlags>,
        inTimeStamp: UnsafePointer<AudioTimeStamp>,
        inBusNumber: UInt32,
        inNumberFrames: UInt32,
        ioData: UnsafeMutablePointer<AudioBufferList>?
    ) -> OSStatus {
        _ = ioActionFlags
        _ = inTimeStamp
        _ = inBusNumber
        let backend = Unmanaged<IOSRemoteIOAudioBackend>.fromOpaque(inRefCon).takeUnretainedValue()
        return backend.renderSilence(
            ioData: ioData,
            ioActionFlags: ioActionFlags,
            inNumberFrames: inNumberFrames
        )
    }
    // swiftlint:enable function_parameter_count

    private func checkOSStatus(_ status: OSStatus, operation: String) throws {
        if status != noErr {
            throw AudioBackendError.osStatus(operation, status)
        }
    }
#endif

#if os(macOS)
    private final class MacEngineAudioBackend: SessionFrameIOBackend, @unchecked Sendable {
        private let config: Config
        private let counters = AudioCounterBox()
        private let phyLink: AcousticPHYLink
        private let engine = AVAudioEngine()
        private let txQueueLock = NSLock()
        private var sessionHandle: OpaquePointer?
        private var state: AudioBackendState = .idle
        private var observedInputSampleRateHz: UInt32 = 0
        private var observedOutputSampleRateHz: UInt32 = 0
        private var txSampleQueue: [Float] = []
        private var txSampleReadIndex: Int = 0
        private var sourceNode: AVAudioSourceNode?
        private var rxResampler: LinearStreamResampler?
        private lazy var sourceFormat: AVAudioFormat? = {
            AVAudioFormat(
                commonFormat: .pcmFormatFloat32,
                sampleRate: Double(config.sampleRateHz),
                channels: 1,
                interleaved: false
            )
        }()

        init(config: Config) {
            self.config = config
            phyLink = AcousticPHYLink(config: config)
        }

        var diagnostics: AudioBackendDiagnostics {
            counters.snapshot(
                backend: "macos-avaudioengine",
                state: state,
                pendingOutputSampleCount: pendingTxSamples(),
                configuredSampleRateHz: config.sampleRateHz,
                observedInputSampleRateHz: observedInputSampleRateHz,
                observedOutputSampleRateHz: observedOutputSampleRateHz
            )
        }

        func attachSessionHandle(_ handle: OpaquePointer) {
            sessionHandle = handle
        }

        func start() throws {
            guard let sourceFormat else {
                throw AudioBackendError.startupFailed("failed to create AVAudioFormat")
            }
            if sourceNode == nil {
                let node = AVAudioSourceNode(format: sourceFormat) { [weak self] _, _, frameCount, ioData in
                    self?.renderOutbound(ioData: ioData, frameCount: Int(frameCount))
                    return noErr
                }
                sourceNode = node
            }
            if let sourceNode, sourceNode.engine == nil {
                engine.attach(sourceNode)
                engine.connect(sourceNode, to: engine.mainMixerNode, format: sourceFormat)
            }

            engine.inputNode.removeTap(onBus: 0)
            engine.inputNode.installTap(
                onBus: 0,
                bufferSize: 1024,
                format: engine.inputNode.inputFormat(forBus: 0)
            ) { [weak self] buffer, _ in
                self?.counters.recordRxCallback()
                self?.ingestInboundBuffer(buffer)
            }

            do {
                engine.mainMixerNode.outputVolume = 1.0
                engine.prepare()
                try engine.start()
                observedInputSampleRateHz = UInt32(
                    engine.inputNode.inputFormat(forBus: 0).sampleRate.rounded()
                )
                observedOutputSampleRateHz = UInt32(
                    engine.outputNode.outputFormat(forBus: 0).sampleRate.rounded()
                )
                configureInputResamplerIfNeeded()
                state = .running
            } catch {
                state = .failed
                throw AudioBackendError.startupFailed(error.localizedDescription)
            }
        }

        func stop() {
            engine.inputNode.removeTap(onBus: 0)
            engine.stop()
            clearTxQueue()
            rxResampler = nil
            state = .stopped
        }

        func handleOutboundFrame(_ frame: UnsafeBufferPointer<UInt8>) -> Int32 {
            guard state == .running else {
                return CYRINX_ERR_NOT_RUNNING.rawValue
            }
            let frameBytes = Array(frame)
            let waveform =
                (try? phyLink.encode(frame: frameBytes))
                ?? StubWaveSynthesizer.synthesize(
                    symbols: frameBytes,
                    sampleRate: Double(config.sampleRateHz),
                    txGainCap: config.txGainCap
                )
            counters.recordTx(bytes: frame.count)
            enqueueTxSamples(waveform)
            return CYRINX_OK.rawValue
        }

        func playLocalAudibleBeacon() -> Int32 {
            guard state == .running else {
                return CYRINX_ERR_NOT_RUNNING.rawValue
            }
            let waveform = AudibleBeaconSynthesizer.synthesize(
                role: config.role,
                sampleRate: Double(config.sampleRateHz),
                txGainCap: config.txGainCap
            )
            counters.recordTx(bytes: waveform.count)
            enqueueTxSamples(waveform)
            return CYRINX_OK.rawValue
        }

        private func renderOutbound(
            ioData: UnsafeMutablePointer<AudioBufferList>,
            frameCount: Int
        ) {
            counters.recordOutputCallback()
            guard frameCount > 0 else {
                return
            }
            let buffers = UnsafeMutableAudioBufferListPointer(ioData)
            guard !buffers.isEmpty else {
                return
            }
            if buffers.count == 1 {
                renderSingleOutputBuffer(buffers[0], frameCount: frameCount)
                return
            }
            renderMultichannelOutputBuffers(buffers, frameCount: frameCount)
        }

        private func renderSingleOutputBuffer(_ buffer: AudioBuffer, frameCount: Int) {
            let sampleCount = Int(buffer.mDataByteSize) / MemoryLayout<Float>.size
            guard sampleCount > 0, let data = buffer.mData else {
                return
            }
            let out = data.assumingMemoryBound(to: Float.self)
            let toFill = min(sampleCount, frameCount)
            let copied = dequeueTxSamples(into: out, sampleCount: toFill)
            if copied < toFill {
                (out + copied).initialize(repeating: 0, count: toFill - copied)
            }
            if toFill < sampleCount {
                (out + toFill).initialize(repeating: 0, count: sampleCount - toFill)
            }
        }

        private func renderMultichannelOutputBuffers(
            _ buffers: UnsafeMutableAudioBufferListPointer,
            frameCount: Int
        ) {
            var mono = [Float](repeating: 0, count: frameCount)
            _ = mono.withUnsafeMutableBufferPointer { ptr in
                dequeueTxSamples(into: ptr.baseAddress!, sampleCount: frameCount)
            }
            for buffer in buffers {
                guard let data = buffer.mData else {
                    continue
                }
                let out = data.assumingMemoryBound(to: Float.self)
                let sampleCount = Int(buffer.mDataByteSize) / MemoryLayout<Float>.size
                let toCopy = min(sampleCount, frameCount)
                for idx in 0..<toCopy {
                    out[idx] = mono[idx]
                }
                if toCopy < sampleCount {
                    (out + toCopy).initialize(repeating: 0, count: sampleCount - toCopy)
                }
            }
        }

        private func enqueueTxSamples(_ samples: [Float]) {
            guard !samples.isEmpty else {
                return
            }
            txQueueLock.lock()
            txSampleQueue.append(contentsOf: samples)
            txQueueLock.unlock()
        }

        private func dequeueTxSamples(into out: UnsafeMutablePointer<Float>, sampleCount: Int) -> Int {
            txQueueLock.lock()
            let available = max(0, txSampleQueue.count - txSampleReadIndex)
            let copied = min(sampleCount, available)
            if copied > 0 {
                for idx in 0..<copied {
                    out[idx] = txSampleQueue[txSampleReadIndex + idx]
                }
                txSampleReadIndex += copied
            }
            if txSampleReadIndex > 4096, txSampleReadIndex * 2 > txSampleQueue.count {
                txSampleQueue.removeFirst(txSampleReadIndex)
                txSampleReadIndex = 0
            }
            txQueueLock.unlock()
            return copied
        }

        private func pendingTxSamples() -> UInt64 {
            txQueueLock.lock()
            let available = max(0, txSampleQueue.count - txSampleReadIndex)
            txQueueLock.unlock()
            return UInt64(available)
        }

        private func clearTxQueue() {
            txQueueLock.lock()
            txSampleQueue.removeAll(keepingCapacity: false)
            txSampleReadIndex = 0
            txQueueLock.unlock()
        }

        private func ingestInboundBuffer(_ buffer: AVAudioPCMBuffer) {
            guard let sessionHandle, let channel = buffer.floatChannelData?.pointee else {
                return
            }
            let frameLength = Int(buffer.frameLength)
            if frameLength == 0 {
                return
            }
            let decodedFrames: [AcousticDecodedFrame]
            if let rxResampler {
                let source = UnsafeBufferPointer(start: channel, count: frameLength)
                let modemRateSamples = rxResampler.process(source)
                if modemRateSamples.isEmpty {
                    return
                }
                decodedFrames = phyLink.ingest(samples: modemRateSamples)
            } else {
                let samples = UnsafeBufferPointer(start: channel, count: frameLength)
                decodedFrames = phyLink.ingest(samples: samples)
            }
            for decoded in decodedFrames where !decoded.frame.isEmpty {
                var report = decoded.report
                let rc = decoded.frame.withUnsafeBufferPointer { ptr in
                    cyrinx_ingest_frame(sessionHandle, ptr.baseAddress, ptr.count, &report)
                }
                if rc != CYRINX_OK.rawValue {
                    state = .failed
                }
            }
        }

        private func configureInputResamplerIfNeeded() {
            let inputRate = Double(observedInputSampleRateHz)
            let modemRate = Double(config.sampleRateHz)
            if inputRate <= 0 || abs(inputRate - modemRate) < 1.0 {
                rxResampler = nil
                return
            }
            rxResampler = LinearStreamResampler(inputRateHz: inputRate, outputRateHz: modemRate)
        }
    }
#endif
