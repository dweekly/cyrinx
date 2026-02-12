import Foundation

#if canImport(AVFoundation)
    import AVFoundation
#endif
#if os(iOS)
    import AudioToolbox
#endif

public enum TransportBackend: Sendable, Equatable {
    case inMemory
    case appleAudioScaffold
}

public enum AudioBackendState: String, Sendable {
    case idle
    case running
    case stopped
    case failed
}

public struct AudioBackendDiagnostics: Sendable {
    public let backend: String
    public let state: AudioBackendState
    public let txFrameCount: UInt64
    public let txByteCount: UInt64
    public let rxCallbackCount: UInt64
}

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

    func snapshot(backend: String, state: AudioBackendState) -> AudioBackendDiagnostics {
        lock.lock()
        let diagnostics = AudioBackendDiagnostics(
            backend: backend,
            state: state,
            txFrameCount: txFrames,
            txByteCount: txBytes,
            rxCallbackCount: rxCallbacks
        )
        lock.unlock()
        return diagnostics
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
        let samplesPerSymbol = max(Int(sampleRate * 0.002), 48)
        let totalSamples = cappedSymbols.count * samplesPerSymbol
        var out = [Float](repeating: 0, count: totalSamples)
        let nyquistSafe = max(19_000.0, (sampleRate * 0.5) - 500.0)
        let base = min(18_500.0, nyquistSafe - 800.0)
        let span = max(600.0, nyquistSafe - base)
        let amplitude = min(max(txGainCap, 0), 0.12)
        var cursor = 0

        for symbol in cappedSymbols {
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
    func start() throws
    func stop()
    func handleOutboundFrame(_ frame: UnsafeBufferPointer<UInt8>) -> Int32
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
        private let txQueueLock = NSLock()
        private var audioUnit: AudioUnit?
        private var txSampleQueue: [Float] = []
        private var txSampleReadIndex: Int = 0
        private var state: AudioBackendState = .idle

        init(config: Config) throws {
            self.config = config
            try configureAudioSession()
            try createAudioUnit()
        }

        var diagnostics: AudioBackendDiagnostics {
            counters.snapshot(backend: "ios-remoteio", state: state)
        }

        func start() throws {
            guard let audioUnit else {
                throw AudioBackendError.startupFailed("RemoteIO unit not initialized")
            }

            try checkOSStatus(AudioUnitInitialize(audioUnit), operation: "AudioUnitInitialize")
            try checkOSStatus(AudioOutputUnitStart(audioUnit), operation: "AudioOutputUnitStart")
            state = .running
        }

        func stop() {
            guard let audioUnit else {
                return
            }

            _ = AudioOutputUnitStop(audioUnit)
            _ = AudioUnitUninitialize(audioUnit)
            state = .stopped
        }

        func handleOutboundFrame(_ frame: UnsafeBufferPointer<UInt8>) -> Int32 {
            counters.recordTx(bytes: frame.count)
            if frame.isEmpty {
                return 0
            }

            let symbols = Array(frame)
            let waveform = StubWaveSynthesizer.synthesize(
                symbols: symbols,
                sampleRate: Double(config.sampleRateHz),
                txGainCap: config.txGainCap
            )
            enqueueTxSamples(waveform)
            return 0
        }

        private func configureAudioSession() throws {
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.playAndRecord, mode: .measurement, options: [.defaultToSpeaker])
            try session.setPreferredSampleRate(Double(config.sampleRateHz))
            try session.setPreferredIOBufferDuration(0.01)
            try session.setActive(true, options: [])
        }

        private func createAudioUnit() throws {
            let unit = try allocateAudioUnit()
            try configureIO(unit: unit)
            try configureStreamFormat(unit: unit)
            try configureCallbacks(unit: unit)
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
                    scope: kAudioUnitScope_Global,
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
            }
            return status
        }

        fileprivate func renderSilence(ioData: UnsafeMutablePointer<AudioBufferList>?) -> OSStatus {
            guard let ioData else {
                return noErr
            }
            let buffers = UnsafeMutableAudioBufferListPointer(ioData)
            for buffer in buffers {
                guard let data = buffer.mData else {
                    continue
                }
                let sampleCount = Int(buffer.mDataByteSize) / MemoryLayout<Float>.size
                let out = data.assumingMemoryBound(to: Float.self)
                let copied = dequeueTxSamples(into: out, sampleCount: sampleCount)
                if copied < sampleCount {
                    let remainder = sampleCount - copied
                    (out + copied).initialize(repeating: 0, count: remainder)
                }
            }
            return noErr
        }

        private func enqueueTxSamples(_ samples: [Float]) {
            if samples.isEmpty {
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
        _ = inNumberFrames
        let backend = Unmanaged<IOSRemoteIOAudioBackend>.fromOpaque(inRefCon).takeUnretainedValue()
        return backend.renderSilence(ioData: ioData)
    }
    // swiftlint:enable function_parameter_count

    private func checkOSStatus(_ status: OSStatus, operation: String) throws {
        if status != noErr {
            throw AudioBackendError.osStatus(operation, status)
        }
    }
#endif

#if os(macOS)
    private final class MacEngineAudioBackend: SessionFrameIOBackend {
        private let config: Config
        private let counters = AudioCounterBox()
        private let engine = AVAudioEngine()
        private let player = AVAudioPlayerNode()
        private var state: AudioBackendState = .idle
        private lazy var format: AVAudioFormat? = {
            AVAudioFormat(
                commonFormat: .pcmFormatFloat32,
                sampleRate: Double(config.sampleRateHz),
                channels: 1,
                interleaved: false
            )
        }()

        init(config: Config) {
            self.config = config
        }

        var diagnostics: AudioBackendDiagnostics {
            counters.snapshot(backend: "macos-avaudioengine", state: state)
        }

        func start() throws {
            guard let format else {
                throw AudioBackendError.startupFailed("failed to create AVAudioFormat")
            }
            if player.engine == nil {
                engine.attach(player)
                engine.connect(player, to: engine.mainMixerNode, format: format)
            }

            engine.inputNode.removeTap(onBus: 0)
            engine.inputNode.installTap(
                onBus: 0,
                bufferSize: 1024,
                format: engine.inputNode.inputFormat(forBus: 0)
            ) { [weak self] _, _ in
                self?.counters.recordRxCallback()
            }

            do {
                try engine.start()
                player.play()
                state = .running
            } catch {
                state = .failed
                throw AudioBackendError.startupFailed(error.localizedDescription)
            }
        }

        func stop() {
            engine.inputNode.removeTap(onBus: 0)
            player.stop()
            engine.stop()
            state = .stopped
        }

        func handleOutboundFrame(_ frame: UnsafeBufferPointer<UInt8>) -> Int32 {
            counters.recordTx(bytes: frame.count)
            guard state == .running, let buffer = makeBuffer(frame: frame) else {
                return 0
            }
            player.scheduleBuffer(buffer, completionHandler: nil)
            return 0
        }

        private func makeBuffer(frame: UnsafeBufferPointer<UInt8>) -> AVAudioPCMBuffer? {
            guard let format else {
                return nil
            }
            let waveform = StubWaveSynthesizer.synthesize(
                symbols: Array(frame),
                sampleRate: format.sampleRate,
                txGainCap: config.txGainCap
            )
            if waveform.isEmpty {
                return nil
            }
            guard
                let buffer = AVAudioPCMBuffer(
                    pcmFormat: format,
                    frameCapacity: AVAudioFrameCount(waveform.count)
                ),
                let channel = buffer.floatChannelData?.pointee
            else {
                return nil
            }
            buffer.frameLength = AVAudioFrameCount(waveform.count)
            for (idx, sample) in waveform.enumerated() {
                channel[idx] = sample
            }
            return buffer
        }
    }
#endif
