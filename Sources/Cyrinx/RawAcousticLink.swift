#if os(macOS)
import AVFoundation
import Foundation

public struct RawAcousticDiagnostics: Sendable {
    public let observedInputSampleRateHz: UInt32
    public let observedOutputSampleRateHz: UInt32
    public let txFrameCount: UInt64
    public let rxFrameCount: UInt64
    public let pendingOutputSampleCount: UInt64
    public let recentInputRms: Float
}

public final class RawAcousticMacLink: @unchecked Sendable {
    private let config: Config
    private let codecKind: RawAcousticCodec
    private let engine = AVAudioEngine()
    private let txLock = NSLock()
    private let codecLock = NSLock()
    private let captureLock = NSLock()
    private let gateLock = NSLock()
    private let decodeQueue = DispatchQueue(label: "cyrinx.raw.decode")
    private var txSampleQueue: [Float] = []
    private var txSampleReadIndex = 0
    private var capturedInputSamples: [Float] = []
    private var captureInputEnabled = false
    private var sourceNode: AVAudioSourceNode?
    private var receiveHandler: ((Data) -> Void)?
    private var observedInputSampleRateHz: UInt32 = 0
    private var observedOutputSampleRateHz: UInt32 = 0
    private var txFrameCount: UInt64 = 0
    private var rxFrameCount: UInt64 = 0
    private var recentInputRms: Float = 0
    private let activityThreshold: Float
    private let preRollSamples: Int
    private let postRollBuffers = 3
    private var rxPreRoll: [Float] = []
    private var rxBurstOpen = false
    private var rxHangoverBuffers = 0
    private var basicCodec: BasicToneCodec?
    private var reverseBurstCodec: ReverseBurstCodec?
    private var ookCodec: OOKToneCodec?
    private var morseCodec: MorseToneCodec?
    private var dtmfCodec: DTMFCodec?
    private var nibbleCodec: NibbleToneCodec?
    private lazy var sourceFormat: AVAudioFormat? = {
        AVAudioFormat(
            commonFormat: .pcmFormatFloat32,
            sampleRate: Double(config.sampleRateHz),
            channels: 1,
            interleaved: false
        )
    }()

    public init(
        config: Config,
        rawCodec: RawAcousticCodec = .auto,
        dcssSymbolSamples: Int? = nil,
        preambleSyncThreshold: Float? = nil
    ) {
        self.config = config
        codecKind = RawAcousticCodec.resolved(rawCodec, config: config)
        switch codecKind {
        case .dtmf, .nibble, .ook, .morse:
            activityThreshold = 0.0020
        case .reverseBurst:
            activityThreshold = 0.0018
        case .basic, .auto:
            activityThreshold = 0.0015
        }
        preRollSamples = max(4096, min(32_768, (dcssSymbolSamples ?? 960) * 12))
        if codecKind == .ook {
            ookCodec = OOKToneCodec(config: config, symbolSamples: dcssSymbolSamples ?? 960)
        } else if codecKind == .morse {
            morseCodec = MorseToneCodec(config: config, symbolSamples: dcssSymbolSamples ?? 960)
        } else if codecKind == .reverseBurst {
            reverseBurstCodec = ReverseBurstCodec(config: config, symbolSamples: dcssSymbolSamples ?? 960)
        } else if codecKind == .dtmf {
            dtmfCodec = DTMFCodec(config: config, symbolSamples: dcssSymbolSamples ?? 960)
        } else if codecKind == .nibble {
            nibbleCodec = NibbleToneCodec(config: config, symbolSamples: dcssSymbolSamples ?? 960)
        } else {
            basicCodec = BasicToneCodec(config: config, symbolSamples: dcssSymbolSamples ?? 960)
        }
    }

    public func start(onReceive: @escaping (Data) -> Void) throws {
        guard let sourceFormat else {
            throw NSError(
                domain: "cyrinx.raw",
                code: 1,
                userInfo: [NSLocalizedDescriptionKey: "failed to create source format"]
            )
        }

        receiveHandler = onReceive
        if sourceNode == nil {
            sourceNode = AVAudioSourceNode(format: sourceFormat) { [weak self] _, _, frameCount, ioData in
                self?.renderOutbound(ioData: ioData, frameCount: Int(frameCount))
                return noErr
            }
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
            self?.ingestInboundBuffer(buffer)
        }

        engine.mainMixerNode.outputVolume = 1.0
        engine.prepare()
        try engine.start()

        observedInputSampleRateHz = UInt32(engine.inputNode.inputFormat(forBus: 0).sampleRate.rounded())
        observedOutputSampleRateHz = UInt32(engine.outputNode.outputFormat(forBus: 0).sampleRate.rounded())
    }

    public func stop() {
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        decodeQueue.sync {}
        clearTxQueue()
        receiveHandler = nil
    }

    public func send(frame: Data) throws {
        codecLock.lock()
        let waveform: [Float]
        if var codec = basicCodec {
            waveform = codec.encode(payload: frame)
            basicCodec = codec
        } else if var codec = morseCodec {
            waveform = codec.encode(payload: frame)
            morseCodec = codec
        } else if var codec = reverseBurstCodec {
            waveform = codec.encode(payload: frame)
            reverseBurstCodec = codec
        } else if var codec = dtmfCodec {
            waveform = codec.encode(payload: frame)
            dtmfCodec = codec
        } else if var codec = nibbleCodec {
            waveform = codec.encode(payload: frame)
            nibbleCodec = codec
        } else if var codec = ookCodec {
            waveform = codec.encode(payload: frame)
            ookCodec = codec
        } else {
            waveform = []
        }
        codecLock.unlock()
        txFrameCount += 1
        enqueueTxSamples(waveform)
    }

    public var diagnostics: RawAcousticDiagnostics {
        RawAcousticDiagnostics(
            observedInputSampleRateHz: observedInputSampleRateHz,
            observedOutputSampleRateHz: observedOutputSampleRateHz,
            txFrameCount: txFrameCount,
            rxFrameCount: rxFrameCount,
            pendingOutputSampleCount: pendingTxSamples(),
            recentInputRms: recentInputRms
        )
    }

    public func setInputCaptureEnabled(_ enabled: Bool) {
        captureLock.lock()
        captureInputEnabled = enabled
        if !enabled {
            capturedInputSamples.removeAll(keepingCapacity: false)
        }
        captureLock.unlock()
    }

    public func snapshotCapturedInput() -> [Float] {
        captureLock.lock()
        let snapshot = capturedInputSamples
        captureLock.unlock()
        return snapshot
    }

    private func renderOutbound(
        ioData: UnsafeMutablePointer<AudioBufferList>,
        frameCount: Int
    ) {
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

        var mono = [Float](repeating: 0, count: frameCount)
        _ = mono.withUnsafeMutableBufferPointer { ptr in
            dequeueTxSamples(into: ptr.baseAddress!, sampleCount: frameCount)
        }
        for buffer in buffers {
            guard let data = buffer.mData else {
                continue
            }
            let out = data.assumingMemoryBound(to: Float.self)
            let sampleCount = min(frameCount, Int(buffer.mDataByteSize) / MemoryLayout<Float>.size)
            for idx in 0..<sampleCount {
                out[idx] = mono[idx]
            }
        }
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

    private func enqueueTxSamples(_ samples: [Float]) {
        guard !samples.isEmpty else {
            return
        }
        txLock.lock()
        txSampleQueue.append(contentsOf: samples)
        txLock.unlock()
    }

    private func dequeueTxSamples(into out: UnsafeMutablePointer<Float>, sampleCount: Int) -> Int {
        txLock.lock()
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
        txLock.unlock()
        return copied
    }

    private func ingestInboundBuffer(_ buffer: AVAudioPCMBuffer) {
        guard let channel = buffer.floatChannelData?.pointee else {
            return
        }
        let frameLength = Int(buffer.frameLength)
        guard frameLength > 0 else {
            return
        }

        let samples = UnsafeBufferPointer(start: channel, count: frameLength)
        var energy: Float = 0
        for sample in samples {
            energy += sample * sample
        }
        recentInputRms = sqrt(energy / Float(frameLength))
        captureLock.lock()
        if captureInputEnabled {
            capturedInputSamples.append(contentsOf: samples)
        }
        captureLock.unlock()
        guard let copiedSamples = prepareDecodeSamples(Array(samples), rms: recentInputRms) else {
            return
        }
        decodeQueue.async { [weak self] in
            self?.decodeInboundSamples(copiedSamples)
        }
    }

    private func prepareDecodeSamples(_ samples: [Float], rms: Float) -> [Float]? {
        gateLock.lock()
        defer { gateLock.unlock() }

        if rms >= activityThreshold {
            let combined = (!rxBurstOpen && !rxPreRoll.isEmpty) ? (rxPreRoll + samples) : samples
            rxBurstOpen = true
            rxHangoverBuffers = postRollBuffers
            rxPreRoll.removeAll(keepingCapacity: false)
            return combined
        }

        if rxBurstOpen, rxHangoverBuffers > 0 {
            rxHangoverBuffers -= 1
            if rxHangoverBuffers == 0 {
                rxBurstOpen = false
            }
            return samples
        }

        rxBurstOpen = false
        rxHangoverBuffers = 0
        if samples.count > preRollSamples {
            rxPreRoll = Array(samples.suffix(preRollSamples))
        } else {
            rxPreRoll = samples
        }
        return nil
    }

    private func decodeInboundSamples(_ samples: [Float]) {
        codecLock.lock()
        let decodedFrames: [Data]
        if var codec = basicCodec {
            decodedFrames = samples.withUnsafeBufferPointer { codec.ingest(samples: $0) }
            basicCodec = codec
        } else if var codec = morseCodec {
            decodedFrames = samples.withUnsafeBufferPointer { codec.ingest(samples: $0) }
            morseCodec = codec
        } else if var codec = reverseBurstCodec {
            decodedFrames = samples.withUnsafeBufferPointer { codec.ingest(samples: $0) }
            reverseBurstCodec = codec
        } else if var codec = dtmfCodec {
            decodedFrames = samples.withUnsafeBufferPointer { codec.ingest(samples: $0) }
            dtmfCodec = codec
        } else if var codec = nibbleCodec {
            decodedFrames = samples.withUnsafeBufferPointer { codec.ingest(samples: $0) }
            nibbleCodec = codec
        } else if var codec = ookCodec {
            decodedFrames = samples.withUnsafeBufferPointer { codec.ingest(samples: $0) }
            ookCodec = codec
        } else {
            decodedFrames = []
        }
        codecLock.unlock()

        for decoded in decodedFrames where !decoded.isEmpty {
            rxFrameCount += 1
            receiveHandler?(decoded)
        }
    }

    private func pendingTxSamples() -> UInt64 {
        txLock.lock()
        let available = max(0, txSampleQueue.count - txSampleReadIndex)
        txLock.unlock()
        return UInt64(available)
    }

    private func clearTxQueue() {
        txLock.lock()
        txSampleQueue.removeAll(keepingCapacity: false)
        txSampleReadIndex = 0
        txLock.unlock()
    }
}
#endif
