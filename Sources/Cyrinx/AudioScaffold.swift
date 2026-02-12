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

/// Audible role-distinct beacon used for local speaker verification in test labs.
enum AudibleBeaconSynthesizer {
    static func synthesize(role: Role, sampleRate: Double, txGainCap: Float) -> [Float] {
        let fs = min(max(sampleRate.rounded(), 8_000), 192_000)
        let amplitude = min(max(txGainCap, 0), 0.55)
        if amplitude <= 0 {
            return []
        }

        let pattern: [(freqHz: Double, durationSec: Double)]
        switch role {
        case .master:
            pattern =
                [(1_300, 0.18), (0, 0.06), (1_700, 0.18), (0, 0.06), (1_300, 0.18)]
                + [(0, 0.12), (1_300, 0.18), (0, 0.06), (1_700, 0.18), (0, 0.06), (1_300, 0.18)]
        case .slave:
            pattern =
                [(900, 0.18), (0, 0.06), (1_200, 0.18), (0, 0.06), (1_700, 0.18)]
                + [(0, 0.12), (900, 0.18), (0, 0.06), (1_200, 0.18), (0, 0.06), (1_700, 0.18)]
        }

        var out: [Float] = []
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

        let rampSamples = min(max(8, Int(sampleRate * 0.004)), sampleCount / 2)
        let phaseStep = Float((2.0 * Double.pi * frequencyHz) / sampleRate)
        var phase: Float = 0
        var out = [Float](repeating: 0, count: sampleCount)
        for idx in 0..<sampleCount {
            let envelope: Float
            if idx < rampSamples {
                envelope = Float(idx) / Float(max(1, rampSamples))
            } else if idx >= (sampleCount - rampSamples) {
                envelope = Float(sampleCount - idx - 1) / Float(max(1, rampSamples))
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
    // swiftlint:disable type_body_length
    private final class IOSRemoteIOAudioBackend: SessionFrameIOBackend {
        private let config: Config
        private let counters = AudioCounterBox()
        private let phyLink: AcousticPHYLink
        private let txQueueLock = NSLock()
        private let log = Logger(subsystem: "com.dweekly.cyrinx", category: "ios-remoteio")
        private var audioUnit: AudioUnit?
        private var sessionHandle: OpaquePointer?
        private var txSampleQueue: [Float] = []
        private var txSampleReadIndex: Int = 0
        private var state: AudioBackendState = .idle
        private var observedInputSampleRateHz: UInt32 = 0
        private var observedOutputSampleRateHz: UInt32 = 0
        private var renderCallbackLogCount: UInt32 = 0
        private var outputUnderrunLogCount: UInt32 = 0
        private var enqueueLogCount: UInt32 = 0
        private var renderCallbacksTotal: UInt64 = 0
        private var copiedAfterEnqueueLogCount: UInt32 = 0
        private var renderBufferShapeLogCount: UInt32 = 0

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

            try checkOSStatus(AudioUnitInitialize(audioUnit), operation: "AudioUnitInitialize")
            try checkOSStatus(AudioOutputUnitStart(audioUnit), operation: "AudioOutputUnitStart")
            renderCallbackLogCount = 0
            outputUnderrunLogCount = 0
            enqueueLogCount = 0
            renderCallbacksTotal = 0
            copiedAfterEnqueueLogCount = 0
            renderBufferShapeLogCount = 0
            let roleName = config.role == .master ? "master" : "slave"
            log.info("RemoteIO started role=\(roleName) cfgHz=\(self.config.sampleRateHz)")
            log.info("RemoteIO observed inHz=\(self.observedInputSampleRateHz)")
            log.info("RemoteIO observed outHz=\(self.observedOutputSampleRateHz)")
            state = .running
        }

        func stop() {
            guard let audioUnit else {
                return
            }

            _ = AudioOutputUnitStop(audioUnit)
            _ = AudioUnitUninitialize(audioUnit)
            log.info("RemoteIO stopped")
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
            counters.recordTx(bytes: beacon.count)
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

        private func configureAudioSession() throws {
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.playAndRecord, mode: .measurement, options: [.defaultToSpeaker])
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
            log.info("AVAudioSession cfg prefHz=\(preferredHz) actualHz=\(actualHz)")
            log.info("AVAudioSession route=\(route) micPerm=\(permission) outVol=\(session.outputVolume)")
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
            var streamFormat = AudioStreamBasicDescription(
                mSampleRate: Double(config.sampleRateHz),
                mFormatID: kAudioFormatLinearPCM,
                mFormatFlags: kAudioFormatFlagIsFloat | kAudioFormatFlagIsPacked,
                mBytesPerPacket: 4,
                mFramesPerPacket: 1,
                mBytesPerFrame: 4,
                mChannelsPerFrame: 1,
                mBitsPerChannel: 32,
                mReserved: 0
            )
            try setAudioUnitProperty(
                unit: unit,
                spec: AudioUnitPropertySpec(
                    property: kAudioUnitProperty_StreamFormat,
                    scope: kAudioUnitScope_Output,
                    bus: 1,
                    operation: "SetStreamFormat(input bus)"
                ),
                value: &streamFormat
            )
            try setAudioUnitProperty(
                unit: unit,
                spec: AudioUnitPropertySpec(
                    property: kAudioUnitProperty_StreamFormat,
                    scope: kAudioUnitScope_Input,
                    bus: 0,
                    operation: "SetStreamFormat(output bus)"
                ),
                value: &streamFormat
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

        private func setAudioUnitProperty<T>(
            unit: AudioUnit,
            spec: AudioUnitPropertySpec,
            value: inout T
        ) throws {
            try checkOSStatus(
                AudioUnitSetProperty(
                    unit,
                    spec.property,
                    spec.scope,
                    spec.bus,
                    &value,
                    UInt32(MemoryLayout<T>.size)
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
            let byteCount = Int(inNumberFrames) * MemoryLayout<Float>.size
            let raw = UnsafeMutableRawPointer.allocate(
                byteCount: byteCount,
                alignment: MemoryLayout<Float>.alignment
            )
            defer { raw.deallocate() }

            var buffers = AudioBufferList(
                mNumberBuffers: 1,
                mBuffers: AudioBuffer(
                    mNumberChannels: 1,
                    mDataByteSize: UInt32(byteCount),
                    mData: raw
                )
            )
            let status = AudioUnitRender(
                audioUnit,
                ioActionFlags,
                inTimeStamp,
                inBusNumber,
                inNumberFrames,
                &buffers
            )
            if status == noErr {
                counters.recordRxCallback()
                let sampleCount = Int(inNumberFrames)
                let samples = UnsafeBufferPointer(
                    start: raw.assumingMemoryBound(to: Float.self),
                    count: sampleCount
                )
                ingestInboundSamples(samples)
            }
            return status
        }

        fileprivate func renderSilence(
            ioData: UnsafeMutablePointer<AudioBufferList>?,
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
            renderOutputBuffers(buffers, frameCount: frameCount)
            return noErr
        }

        private func renderOutputBuffers(
            _ buffers: UnsafeMutableAudioBufferListPointer,
            frameCount: Int
        ) {
            for idx in buffers.indices {
                var buffer = buffers[idx]
                guard let data = buffer.mData else {
                    continue
                }
                let declaredSamples = Int(buffer.mDataByteSize) / MemoryLayout<Float>.size
                let sampleCount = max(frameCount, declaredSamples)
                if renderBufferShapeLogCount < 3 {
                    let channels = buffer.mNumberChannels
                    let bytes = buffer.mDataByteSize
                    log.info(
                        "render buf idx=\(idx) channels=\(channels) bytes=\(bytes) samples=\(sampleCount)"
                    )
                    renderBufferShapeLogCount += 1
                }
                let out = data.assumingMemoryBound(to: Float.self)
                let copied = dequeueTxSamples(into: out, sampleCount: sampleCount)
                if copied > 0, copiedAfterEnqueueLogCount < 6 {
                    let pendingAfterCopy = pendingTxSamples()
                    let peak = peakAbs(UnsafeBufferPointer(start: out, count: copied))
                    log.info(
                        "render copied=\(copied) req=\(sampleCount) peak=\(peak) pending=\(pendingAfterCopy)"
                    )
                    copiedAfterEnqueueLogCount += 1
                }
                if copied < sampleCount {
                    let remainder = sampleCount - copied
                    (out + copied).initialize(repeating: 0, count: remainder)
                    if outputUnderrunLogCount < 3 {
                        let pendingAfter = pendingTxSamples()
                        log.info(
                            "render underrun req=\(sampleCount) copied=\(copied) pendingAfter=\(pendingAfter)"
                        )
                        outputUnderrunLogCount += 1
                    }
                }
                buffer.mDataByteSize = UInt32(sampleCount * MemoryLayout<Float>.size)
                buffers[idx] = buffer
            }
        }

        private func peakAbs(_ samples: [Float]) -> Float {
            var peak: Float = 0
            for sample in samples {
                peak = max(peak, abs(sample))
            }
            return peak
        }

        private func peakAbs(_ samples: UnsafeBufferPointer<Float>) -> Float {
            var peak: Float = 0
            for sample in samples {
                peak = max(peak, abs(sample))
            }
            return peak
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
        return backend.renderSilence(ioData: ioData, inNumberFrames: inNumberFrames)
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
