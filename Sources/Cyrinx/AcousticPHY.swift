import CCyrinx
import CryptoKit
import Foundation

private let acousticFrameMaxBytes = 2048

private let acousticHeaderBytes = 7
private let acousticHeaderMagic0: UInt8 = 0xAC
private let acousticHeaderMagic1: UInt8 = 0x51

private enum AcousticBodyMode: UInt8 {
    case robustDCSS = 0
    case turboQPSK = 1
    case turbo16QAM = 2
    case turbo64QAM = 3
}

private struct AcousticPHYHeader {
    let mode: AcousticBodyMode
    let payloadLength: UInt16

    func encode() -> [UInt8] {
        var out = [UInt8](repeating: 0, count: acousticHeaderBytes)
        out[0] = acousticHeaderMagic0
        out[1] = acousticHeaderMagic1
        out[2] = mode.rawValue
        out[3] = UInt8((payloadLength >> 8) & 0xFF)
        out[4] = UInt8(payloadLength & 0xFF)
        let crc = crc16CCITT(Array(out[0...4]))
        out[5] = UInt8((crc >> 8) & 0xFF)
        out[6] = UInt8(crc & 0xFF)
        return out
    }

    static func decode(_ bytes: [UInt8]) -> AcousticPHYHeader? {
        if bytes.count != acousticHeaderBytes {
            return nil
        }
        if bytes[0] != acousticHeaderMagic0 || bytes[1] != acousticHeaderMagic1 {
            return nil
        }
        guard let mode = AcousticBodyMode(rawValue: bytes[2]) else {
            return nil
        }
        let payloadLength = (UInt16(bytes[3]) << 8) | UInt16(bytes[4])
        let expectedCRC = crc16CCITT(Array(bytes[0...4]))
        let actualCRC = (UInt16(bytes[5]) << 8) | UInt16(bytes[6])
        if expectedCRC != actualCRC {
            return nil
        }
        return AcousticPHYHeader(mode: mode, payloadLength: payloadLength)
    }
}

struct AcousticDecodedFrame: Sendable {
    let frame: [UInt8]
    let report: cyrinx_channel_report_t
}

// swiftlint:disable type_body_length
final class AcousticPHYLink {
    private let sampleRateHz: UInt32
    private let preambleBlock: [Float]
    private let preamble: [Float]
    private let preambleEnergy: Float
    private let syncThreshold: Float
    private let forceRobustMode: Bool
    private let config: Config
    private var robustConfig: VDSPDCSSConfig
    private var headerConfig: VDSPDCSSConfig
    private var turboConfig: VDSPOFDMConfig
    private let lock = NSLock()
    private let rxLock = NSLock()
    private var rxBuffer: [Float] = []
    private var rxBufferRight: [Float] = []
    private var rxSearchStart: Int = 0

    // MIMO 2x2 tracking properties:
    public private(set) var lastH11: Float = 0
    public private(set) var lastH12: Float = 0
    public private(set) var lastH21: Float = 0
    public private(set) var lastH22: Float = 0
    public private(set) var lastSigma1: Float = 0
    public private(set) var lastSigma2: Float = 0
    public private(set) var lastKappaDb: Float = 0
    public private(set) var lastSpatialMode: Int = 0

    // MIMO 2x2 preambles:
    private let preambleBlockL: [Float]
    private let preambleBlockR: [Float]
    private let preambleL: [Float]
    private let preambleR: [Float]

    private var peerPublicKey: Data?
    private var k_enc: Data?
    private var k_mac: Data?
    private var txSequence: UInt64 = 0

    /// Dynamically updates the peer's hardware signature to look up calibration curves.
    public func updatePeerSignature(_ signature: UInt8) {
        lock.lock()
        defer { lock.unlock() }
        robustConfig.peerDeviceSignature = signature
        headerConfig.peerDeviceSignature = signature
        turboConfig.peerDeviceSignature = signature
    }

    /// Dynamically updates the peer's subcarrier notch mask.
    public func updatePeerNotchMask(_ mask: [UInt8]) {
        lock.lock()
        defer { lock.unlock() }
        turboConfig.peerNotchMask = mask
    }

    /// Dynamically updates the peer's public key.
    public func updatePeerPublicKey(_ key: Data) {
        lock.lock()
        defer { lock.unlock() }

        if peerPublicKey == key { return }

        let allZeros = key.allSatisfy { $0 == 0 }
        if allZeros {
            self.peerPublicKey = nil
            self.k_enc = nil
            self.k_mac = nil
            return
        }

        self.peerPublicKey = key

        guard let privKeyData = config.localPrivateKey else { return }
        do {
            let localPrivateKey = try Curve25519.KeyAgreement.PrivateKey(rawRepresentation: privKeyData)
            let peerPubKey = try Curve25519.KeyAgreement.PublicKey(rawRepresentation: key)
            let sharedSecret = try localPrivateKey.sharedSecretFromKeyAgreement(with: peerPubKey)

            let derived = sharedSecret.hkdfDerivedSymmetricKey(
                using: SHA256.self,
                salt: Data("CyrinxEncryptionSalt".utf8),
                sharedInfo: Data(),
                outputByteCount: 64
            )
            let derivedBytes = derived.withUnsafeBytes { Data($0) }
            self.k_enc = derivedBytes.prefix(32)
            self.k_mac = derivedBytes.suffix(32)
        } catch {
            print("[Crypto] Failed to derive shared keys: \(error)")
        }
    }

    private func encryptCTR(payload: Data, key: Data, sequence: UInt64) -> Data {
        var out = Data(repeating: 0, count: payload.count)
        var offset = 0
        var counter: UInt32 = 0
        while offset < payload.count {
            var inputBlock = Data(repeating: 0, count: 12)
            inputBlock.withUnsafeMutableBytes { ptr in
                ptr.storeBytes(of: sequence.bigEndian, as: UInt64.self)
                ptr.storeBytes(of: counter.bigEndian, toByteOffset: 8, as: UInt32.self)
            }
            var hasher = SHA256()
            hasher.update(data: key)
            hasher.update(data: inputBlock)
            let keystream = Data(hasher.finalize())
            let toXor = min(32, payload.count - offset)
            for i in 0..<toXor {
                out[offset + i] = payload[offset + i] ^ keystream[i]
            }
            offset += toXor
            counter += 1
        }
        return out
    }

    private func secureEnvelopePack(payload: Data, k_enc: Data, k_mac: Data, sequence: UInt64) -> Data {
        let ciphertext = encryptCTR(payload: payload, key: k_enc, sequence: sequence)
        var envelope = Data()
        var seqBytes = Data(repeating: 0, count: 8)
        seqBytes.withUnsafeMutableBytes { ptr in
            ptr.storeBytes(of: sequence.bigEndian, as: UInt64.self)
        }
        envelope.append(seqBytes)
        envelope.append(ciphertext)

        let macKey = SymmetricKey(data: k_mac)
        let signature = HMAC<SHA256>.authenticationCode(for: seqBytes + ciphertext, using: macKey)
        let signatureData = Data(signature)
        let truncatedTag = signatureData.prefix(8)
        envelope.append(truncatedTag)
        return envelope
    }

    private func secureEnvelopeUnpack(envelope: Data, k_enc: Data, k_mac: Data) throws -> Data {
        guard envelope.count >= 16 else {
            throw VDSPPHYError.decodeFailure("Envelope too short")
        }
        let sequenceBytes = envelope.prefix(8)
        let tagBytes = envelope.suffix(8)
        let ciphertext = envelope.subdata(in: 8..<(envelope.count - 8))

        let sequence = sequenceBytes.withUnsafeBytes { ptr in
            UInt64(bigEndian: ptr.load(as: UInt64.self))
        }

        let macKey = SymmetricKey(data: k_mac)
        let signature = HMAC<SHA256>.authenticationCode(for: sequenceBytes + ciphertext, using: macKey)
        let signatureData = Data(signature)
        let truncatedTag = signatureData.prefix(8)

        var isEqual = true
        for i in 0..<8 {
            if tagBytes[tagBytes.startIndex + i] != truncatedTag[truncatedTag.startIndex + i] {
                isEqual = false
            }
        }
        guard isEqual else {
            throw VDSPPHYError.decodeFailure("Integrity check failed: invalid HMAC tag")
        }

        return encryptCTR(payload: ciphertext, key: k_enc, sequence: sequence)
    }

    init(
        config: Config,
        dcssSymbolSamplesOverride: Int? = nil,
        preambleSyncThresholdOverride: Float? = nil
    ) {
        self.config = config
        let boundedSampleRate = min(max(config.sampleRateHz, 8_000), 192_000)
        sampleRateHz = boundedSampleRate
        let maxGainCap = config.bandStartHz >= 9000 ? Float(0.70) : Float(0.12)
        let boundedGain = min(max(config.txGainCap, 0), maxGainCap)
        let fftSize = Int(CYRINX_OFDM_FFT_SIZE)
        let requestedCP = max(Int(config.ofdmCPSamplesDefault), Int(config.ofdmCPSamplesMin))
        let clampedCP = min(max(8, requestedCP), fftSize - 1)
        let symbolSamples = min(max(dcssSymbolSamplesOverride ?? AcousticPHYLink.runtimeDCSSSymbolSamples(), 64), 4096)

        robustConfig = VDSPDCSSConfig(
            sampleRateHz: boundedSampleRate,
            symbolSamples: symbolSamples,
            symbolBins: 256,
            startHz: Float(config.bandStartHz),
            endHz: Float(config.bandEndHz),
            txGainCap: boundedGain
        )
        headerConfig = VDSPDCSSConfig(
            sampleRateHz: boundedSampleRate,
            symbolSamples: symbolSamples,
            symbolBins: 256,
            startHz: Float(config.bandStartHz),
            endHz: Float(config.bandEndHz),
            txGainCap: boundedGain
        )
        turboConfig = VDSPOFDMConfig(
            sampleRateHz: boundedSampleRate,
            fftSize: fftSize,
            cpSamples: clampedCP,
            bandStartHz: Float(config.bandStartHz),
            bandEndHz: Float(config.bandEndHz),
            txGainCap: boundedGain
        )

        preambleBlockL = AcousticPHYLink.makePreambleBlock(
            sampleRateHz: boundedSampleRate,
            bandStartHz: config.bandStartHz,
            bandEndHz: config.bandEndHz,
            txGainCap: boundedGain,
            root: 29
        )
        preambleBlockR = AcousticPHYLink.makePreambleBlock(
            sampleRateHz: boundedSampleRate,
            bandStartHz: config.bandStartHz,
            bandEndHz: config.bandEndHz,
            txGainCap: boundedGain,
            root: 31
        )
        preambleL = preambleBlockL + preambleBlockL
        preambleR = preambleBlockR + preambleBlockR

        preambleBlock = preambleBlockL
        preamble = preambleL
        preambleEnergy = max(1e-7, preamble.reduce(0) { $0 + ($1 * $1) })
        syncThreshold = min(
            max(preambleSyncThresholdOverride ?? AcousticPHYLink.runtimeSyncThreshold(), 0.05),
            0.98
        )
        forceRobustMode = AcousticPHYLink.runtimeForceRobustMode()
    }

    func encode(frame: [UInt8]) throws -> [Float] {
        if frame.isEmpty || frame.count > acousticFrameMaxBytes {
            throw VDSPPHYError.invalidConfiguration("invalid acoustic frame payload size")
        }

        var payloadToSend = frame
        lock.lock()
        let encryptKeys = (k_enc != nil && k_mac != nil)
        let localKEnc = k_enc
        let localKMac = k_mac
        let seq = txSequence
        if encryptKeys && frame[0] != 0xE1 {
            txSequence += 1
        }
        lock.unlock()

        if encryptKeys && frame[0] != 0xE1 {
            let packed = secureEnvelopePack(payload: Data(frame), k_enc: localKEnc!, k_mac: localKMac!, sequence: seq)
            payloadToSend = Array(packed)
        }

        let mode = selectBodyMode(for: payloadToSend)
        let header = AcousticPHYHeader(mode: mode, payloadLength: UInt16(payloadToSend.count)).encode()

        let localHeaderConfig: VDSPDCSSConfig
        let localRobustConfig: VDSPDCSSConfig
        let localTurboConfig: VDSPOFDMConfig

        lock.lock()
        localHeaderConfig = headerConfig
        localRobustConfig = robustConfig
        localTurboConfig = turboConfig
        lock.unlock()

        let headerStart = Date()
        let headerWave = try VDSPPHY.modulateDCSS(payload: header, config: localHeaderConfig)
        let headerDuration = Date().timeIntervalSince(headerStart)
        print("[prof] modulateDCSS (header) took=\(String(format: "%.4f", headerDuration))s")

        let bodyStart = Date()
        let bodyWave: [Float]
        switch mode {
        case .robustDCSS:
            bodyWave = try VDSPPHY.modulateDCSS(payload: payloadToSend, config: localRobustConfig)
        case .turboQPSK:
            bodyWave = try VDSPPHY.modulateOFDM(payload: payloadToSend, mode: 1, config: localTurboConfig)
        case .turbo16QAM:
            bodyWave = try VDSPPHY.modulateOFDM(payload: payloadToSend, mode: 2, config: localTurboConfig)
        case .turbo64QAM:
            bodyWave = try VDSPPHY.modulateOFDM(payload: payloadToSend, mode: 3, config: localTurboConfig)
        }
        let bodyDuration = Date().timeIntervalSince(bodyStart)
        print("[prof] body modulation (mode=\(mode)) took=\(String(format: "%.4f", bodyDuration))s")

        if config.channels == 2 {
            let totalLen = preambleL.count + headerWave.count + bodyWave.count
            var out = [Float](repeating: 0, count: totalLen * 2)
            for i in 0..<preambleL.count {
                out[i * 2] = preambleL[i]
                out[i * 2 + 1] = preambleR[i]
            }
            for i in 0..<headerWave.count {
                out[(preambleL.count + i) * 2] = headerWave[i]
                out[(preambleL.count + i) * 2 + 1] = 0.0
            }
            for i in 0..<bodyWave.count {
                out[(preambleL.count + headerWave.count + i) * 2] = bodyWave[i]
                out[(preambleL.count + headerWave.count + i) * 2 + 1] = 0.0
            }
            return out
        } else {
            var out: [Float] = []
            out.reserveCapacity(preamble.count + headerWave.count + bodyWave.count)
            out.append(contentsOf: preamble)
            out.append(contentsOf: headerWave)
            out.append(contentsOf: bodyWave)
            return out
        }
    }

    func ingest(samples: UnsafeBufferPointer<Float>) -> [AcousticDecodedFrame] {
        if samples.isEmpty {
            return []
        }
        rxLock.lock()
        if config.channels == 2 {
            let halfLen = samples.count / 2
            var left = [Float](repeating: 0, count: halfLen)
            var right = [Float](repeating: 0, count: halfLen)
            for i in 0..<halfLen {
                left[i] = samples[i * 2]
                right[i] = samples[i * 2 + 1]
            }
            rxBuffer.append(contentsOf: left)
            rxBufferRight.append(contentsOf: right)
        } else {
            rxBuffer.append(contentsOf: samples)
        }
        let decoded = decodeAvailableLocked()
        rxLock.unlock()
        return decoded
    }

    func ingest(samples: [Float]) -> [AcousticDecodedFrame] {
        samples.withUnsafeBufferPointer { ingest(samples: $0) }
    }

    // swiftlint:disable:next function_body_length cyclomatic_complexity
    private func decodeAvailableLocked() -> [AcousticDecodedFrame] {
        var decoded: [AcousticDecodedFrame] = []
        lock.lock()
        let localHeaderConfig = headerConfig
        let localRobustConfig = robustConfig
        let localTurboConfig = turboConfig
        let localKEnc = k_enc
        let localKMac = k_mac
        lock.unlock()

        let headerSamples = expectedDCSSSamples(payloadBytes: acousticHeaderBytes, config: localHeaderConfig)

        while true {
            guard let lockResult = findBestPreambleLock(in: rxBuffer, startAt: rxSearchStart) else {
                trimUnlockedBufferForResync()
                break
            }

            let syncStart = lockResult.index
            let baseStart = syncStart + preamble.count
            let maxHeaderEnd = baseStart + 32 + headerSamples

            if rxBuffer.count < maxHeaderEnd {
                if syncStart > 0 {
                    removeFirstSamples(syncStart)
                    rxSearchStart = 0
                }
                break
            }

            var decodedHeaderBytes: [UInt8]? = nil
            var packetHeader: AcousticPHYHeader? = nil
            var actualHeaderStart = baseStart
            var actualHeaderEnd = baseStart + headerSamples

            var shifts: [Int] = [0]
            for delta in 1...32 {
                shifts.append(-delta)
                shifts.append(delta)
            }

            for shift in shifts {
                let hStart = baseStart + shift
                let hEnd = hStart + headerSamples
                guard hStart >= 0 && hEnd <= rxBuffer.count else { continue }

                let headerWindow = Array(rxBuffer[hStart..<hEnd])
                if let decoded = try? VDSPPHY.demodulateDCSS(samples: headerWindow, config: localHeaderConfig),
                   let parsed = AcousticPHYHeader.decode(decoded),
                   parsed.payloadLength > 0,
                   parsed.payloadLength <= acousticFrameMaxBytes {
                    decodedHeaderBytes = decoded
                    packetHeader = parsed
                    actualHeaderStart = hStart
                    actualHeaderEnd = hEnd
                    break
                }
            }

            guard let finalHeader = packetHeader else {
                removeFirstSamples(syncStart + preamble.count / 2)
                rxSearchStart = 0
                continue
            }

            let bodySamples = expectedBodySamples(
                mode: finalHeader.mode,
                payloadBytes: Int(finalHeader.payloadLength)
            )
            if bodySamples == 0 {
                removeFirstSamples(syncStart + preamble.count / 2)
                rxSearchStart = 0
                continue
            }

            let bodyStart = actualHeaderEnd
            let bodyEnd = bodyStart + bodySamples
            if rxBuffer.count < bodyEnd {
                if syncStart > 0 {
                    removeFirstSamples(syncStart)
                    rxSearchStart = 0
                }
                break
            }

            let bodyWindow = Array(rxBuffer[bodyStart..<bodyEnd])
            let bodyPayload: [UInt8]?
            switch finalHeader.mode {
            case .robustDCSS:
                bodyPayload = try? VDSPPHY.demodulateDCSS(samples: bodyWindow, config: localRobustConfig)
            case .turboQPSK:
                bodyPayload = try? VDSPPHY.demodulateOFDM(
                    samples: bodyWindow,
                    mode: 1,
                    config: localTurboConfig,
                    expectedLength: Int(finalHeader.payloadLength)
                )
            case .turbo16QAM:
                bodyPayload = try? VDSPPHY.demodulateOFDM(
                    samples: bodyWindow,
                    mode: 2,
                    config: localTurboConfig,
                    expectedLength: Int(finalHeader.payloadLength)
                )
            case .turbo64QAM:
                bodyPayload = try? VDSPPHY.demodulateOFDM(
                    samples: bodyWindow,
                    mode: 3,
                    config: localTurboConfig,
                    expectedLength: Int(finalHeader.payloadLength)
                )
            }

            guard var frame = bodyPayload, frame.count == Int(finalHeader.payloadLength) else {
                removeFirstSamples(bodyEnd)
                rxSearchStart = 0
                continue
            }

            let decryptKeys = (localKEnc != nil && localKMac != nil)
            if decryptKeys && frame[0] != 0xE1 {
                do {
                    let unpacked = try secureEnvelopeUnpack(envelope: Data(frame), k_enc: localKEnc!, k_mac: localKMac!)
                    frame = Array(unpacked)
                } catch {
                    removeFirstSamples(bodyEnd)
                    rxSearchStart = 0
                    continue
                }
            }

            let report = channelReport(for: lockResult)
            decoded.append(AcousticDecodedFrame(frame: frame, report: report))
            removeFirstSamples(bodyEnd)
            rxSearchStart = 0
        }

        return decoded
    }

    private func selectBodyMode(for frame: [UInt8]) -> AcousticBodyMode {
        let coreHeaderStart = 2
        let coreHeaderBytes = 15
        if frame.count < (coreHeaderStart + coreHeaderBytes) {
            return .robustDCSS
        }
        let header = Array(frame[coreHeaderStart..<(coreHeaderStart + coreHeaderBytes)])
        let frameType = readBitsMSB(bytes: header, startBit: 4, bitCount: 4)
        let streamId = readBitsMSB(bytes: header, startBit: 70, bitCount: 12)
        let isControlPlane =
            frameType == UInt32(CYRINX_FRAME_ACK.rawValue)
            || frameType == UInt32(CYRINX_FRAME_CONTROL.rawValue)
            || streamId == 0
        if isControlPlane {
            return .robustDCSS
        }

        if let envMode = ProcessInfo.processInfo.environment["CYRINX_FORCE_BODY_MODE"]?.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() {
            switch envMode {
            case "64qam": return .turbo64QAM
            case "16qam": return .turbo16QAM
            case "qpsk": return .turboQPSK
            case "robust": return .robustDCSS
            default: break
            }
        }
        if forceRobustMode {
            return .robustDCSS
        }

        let gearID = readBitsMSB(bytes: header, startBit: 64, bitCount: 3)
        if gearID == UInt32(CYRINX_GEAR_G3_64QAM.rawValue) {
            return .turbo64QAM
        } else if gearID == UInt32(CYRINX_GEAR_G3_16QAM.rawValue) {
            return .turbo16QAM
        } else if gearID >= UInt32(CYRINX_GEAR_G3_QPSK.rawValue) {
            return .turboQPSK
        } else {
            return .robustDCSS
        }
    }

    private func expectedBodySamples(mode: AcousticBodyMode, payloadBytes: Int) -> Int {
        switch mode {
        case .robustDCSS:
            return expectedDCSSSamples(payloadBytes: payloadBytes, config: robustConfig)
        case .turboQPSK:
            return expectedOFDMSamples(payloadBytes: payloadBytes, mode: 1, config: turboConfig)
        case .turbo16QAM:
            return expectedOFDMSamples(payloadBytes: payloadBytes, mode: 2, config: turboConfig)
        case .turbo64QAM:
            return expectedOFDMSamples(payloadBytes: payloadBytes, mode: 3, config: turboConfig)
        }
    }

    private func expectedDCSSSamples(payloadBytes: Int, config: VDSPDCSSConfig) -> Int {
        if payloadBytes < 0 || config.symbolSamples <= 0 {
            return 0
        }
        return (payloadBytes + 2) * config.symbolSamples
    }

    private func expectedOFDMSamples(payloadBytes: Int, mode: UInt8, config: VDSPOFDMConfig) -> Int {
        if payloadBytes < 0 || config.fftSize <= 0 || config.cpSamples <= 0 {
            return 0
        }
        let activeCarrierCount = ofdmActiveCarrierCount(config: config)
        if activeCarrierCount <= 0 {
            return 0
        }
        let bitsPerSymbol: Int
        switch mode {
        case 2: bitsPerSymbol = 4
        case 3: bitsPerSymbol = 6
        default: bitsPerSymbol = 2
        }
        let bitCount = (payloadBytes + 2) * 8
        let symbolsCount = (bitCount + bitsPerSymbol - 1) / bitsPerSymbol
        let frameCount = max(1, (symbolsCount + activeCarrierCount - 1) / activeCarrierCount)
        return frameCount * (config.fftSize + config.cpSamples)
    }

    private func ofdmActiveCarrierCount(config: VDSPOFDMConfig) -> Int {
        let spacing = Float(config.sampleRateHz) / Float(config.fftSize)
        if spacing <= 0 {
            return 0
        }
        let start = max(1, Int(ceil(config.bandStartHz / spacing)))
        let end = min((config.fftSize / 2) - 1, Int(floor(config.bandEndHz / spacing)))
        if start > end {
            return 0
        }
        return (end - start) + 1
    }

    private struct PreambleLock {
        let index: Int
        let correlation: Float
        let snrDB: Float
        let evmPct: Float
    }

    private func findBestPreambleLock(in buffer: [Float], startAt: Int) -> PreambleLock? {
        if buffer.count < preamble.count {
            return nil
        }

        let searchLimit = buffer.count - preamble.count
        let lowerBound = max(0, min(startAt, searchLimit))
        
        var coarseStart = lowerBound
        while coarseStart <= searchLimit {
            var dot: Float = 0
            var segmentEnergy: Float = 0
            for idx in 0..<preamble.count {
                let sample = buffer[coarseStart + idx]
                let reference = preamble[idx]
                dot += sample * reference
                segmentEnergy += sample * sample
            }
            let norm = sqrt(max(segmentEnergy * preambleEnergy, 1e-7))
            let corr = dot / norm
            
            if corr >= syncThreshold * 0.5 {
                let fineStart = max(lowerBound, coarseStart - 8)
                let fineEnd = min(searchLimit, coarseStart + 8)
                
                var bestStart = coarseStart
                var bestCorr = corr
                var bestDot = dot
                
                for start in fineStart...fineEnd {
                    var candidateDot: Float = 0
                    var candidateEnergy: Float = 0
                    for idx in 0..<preamble.count {
                        let sample = buffer[start + idx]
                        let reference = preamble[idx]
                        candidateDot += sample * reference
                        candidateEnergy += sample * sample
                    }
                    let candidateNorm = sqrt(max(candidateEnergy * preambleEnergy, 1e-7))
                    let candidateCorr = candidateDot / candidateNorm
                    if candidateCorr > bestCorr {
                        bestCorr = candidateCorr
                        bestStart = start
                        bestDot = candidateDot
                    }
                }
                
                if bestCorr >= syncThreshold {
                    var finalStart = bestStart
                    var finalCorr = bestCorr
                    var finalDot = bestDot
                    
                    let windowSize = 48
                    let peakSearchLimit = min(bestStart + windowSize, searchLimit)
                    if bestStart + 1 <= peakSearchLimit {
                        for candidateStart in (bestStart + 1)...peakSearchLimit {
                            var candidateDot: Float = 0
                            var candidateEnergy: Float = 0
                            for idx in 0..<preamble.count {
                                let sample = buffer[candidateStart + idx]
                                let reference = preamble[idx]
                                candidateDot += sample * reference
                                candidateEnergy += sample * sample
                            }
                            let candidateNorm = sqrt(max(candidateEnergy * preambleEnergy, 1e-7))
                            let candidateCorr = candidateDot / candidateNorm
                            if candidateCorr > finalCorr {
                                finalCorr = candidateCorr
                                finalStart = candidateStart
                                finalDot = candidateDot
                            }
                        }
                    }
                    
                    // MIMO 2x2 channel sounding and SVD solver
                    if config.channels == 2 {
                        var dot11: Float = 0
                        var dot12: Float = 0
                        var dot21: Float = 0
                        var dot22: Float = 0
                        var energyY1: Float = 0
                        var energyY2: Float = 0
                        var energyX1: Float = 0
                        var energyX2: Float = 0
                        
                        for idx in 0..<preamble.count {
                            let y1 = buffer[finalStart + idx]
                            let y2 = (finalStart + idx < rxBufferRight.count) ? rxBufferRight[finalStart + idx] : 0.0
                            let x1 = preambleL[idx]
                            let x2 = preambleR[idx]
                            
                            dot11 += y1 * x1
                            dot12 += y1 * x2
                            dot21 += y2 * x1
                            dot22 += y2 * x2
                            
                            energyY1 += y1 * y1
                            energyY2 += y2 * y2
                            energyX1 += x1 * x1
                            energyX2 += x2 * x2
                        }
                        
                        let h11 = dot11 / sqrt(max(energyY1 * energyX1, 1e-7))
                        let h12 = dot12 / sqrt(max(energyY1 * energyX2, 1e-7))
                        let h21 = dot21 / sqrt(max(energyY2 * energyX1, 1e-7))
                        let h22 = dot22 / sqrt(max(energyY2 * energyX2, 1e-7))
                        
                        self.lastH11 = h11
                        self.lastH12 = h12
                        self.lastH21 = h21
                        self.lastH22 = h22
                        
                        // SVD Solver
                        let s1 = h11 * h11 + h12 * h12 + h21 * h21 + h22 * h22
                        let det = h11 * h22 - h12 * h21
                        let term = max(0.0, s1 * s1 - 4.0 * det * det)
                        let sqrtTerm = sqrt(term)
                        let l1 = (s1 + sqrtTerm) * 0.5
                        let l2 = max(0.0, (s1 - sqrtTerm) * 0.5)
                        let sigma1 = sqrt(l1)
                        let sigma2 = sqrt(l2)
                        
                        self.lastSigma1 = sigma1
                        self.lastSigma2 = sigma2
                        
                        let kappaDb = sigma2 > 1e-5 ? 20.0 * log10(sigma1 / sigma2) : 99.0
                        self.lastKappaDb = kappaDb
                        self.lastSpatialMode = (kappaDb < 6.0) ? 1 : 0
                    } else {
                        // Mono mode
                        var dot11: Float = 0
                        var energyY1: Float = 0
                        var energyX1: Float = 0
                        for idx in 0..<preamble.count {
                            let y1 = buffer[finalStart + idx]
                            let x1 = preambleL[idx]
                            dot11 += y1 * x1
                            energyY1 += y1 * y1
                            energyX1 += x1 * x1
                        }
                        let h11 = dot11 / sqrt(max(energyY1 * energyX1, 1e-7))
                        self.lastH11 = h11
                        self.lastH12 = 0
                        self.lastH21 = 0
                        self.lastH22 = 0
                        self.lastSigma1 = h11
                        self.lastSigma2 = 0
                        self.lastKappaDb = 99.0
                        self.lastSpatialMode = 0
                    }
                    
                    let scale = finalDot / preambleEnergy
                    var errorEnergy: Float = 0
                    for idx in 0..<preamble.count {
                        let estimate = scale * preamble[idx]
                        let err = buffer[finalStart + idx] - estimate
                        errorEnergy += err * err
                    }
                    
                    let signalPower = max(1e-7, (scale * scale * preambleEnergy) / Float(preamble.count))
                    let noisePower = max(1e-7, errorEnergy / Float(preamble.count))
                    let snr = 10.0 * log10(signalPower / noisePower)
                    let evm = sqrt(noisePower / signalPower) * 100.0
                    return PreambleLock(index: finalStart, correlation: finalCorr, snrDB: snr, evmPct: evm)
                }
            }
            
            coarseStart += 8
        }

        return nil
    }

    private func channelReport(for lockResult: PreambleLock) -> cyrinx_channel_report_t {
        let estimatedPER = max(0, min(1, (0.25 - lockResult.correlation) / 0.25))
        return cyrinx_channel_report_t(
            snr_db: lockResult.snrDB,
            evm_pct: lockResult.evmPct,
            cfo_hz: 0,
            per_2s: estimatedPER,
            crc_fail: 0
        )
    }

    private func trimUnlockedBufferForResync() {
        let maxBufferSamples = 72000 // 1.5 seconds
        let keepSamples = 48000 // 1.0 second
        if rxBuffer.count > maxBufferSamples {
            let dropped = rxBuffer.count - keepSamples
            removeFirstSamples(dropped)
            rxSearchStart = max(0, rxSearchStart - dropped)
        }
        let maxStart = max(0, rxBuffer.count - preamble.count)
        if rxSearchStart > maxStart {
            rxSearchStart = maxStart
        }
    }

    private func removeFirstSamples(_ count: Int) {
        rxBuffer.removeFirst(count)
        if config.channels == 2 {
            rxBufferRight.removeFirst(count)
        }
    }

    private static func makePreambleBlock(
        sampleRateHz: UInt32,
        bandStartHz: UInt32,
        bandEndHz: UInt32,
        txGainCap: Float,
        root: UInt16 = 29
    ) -> [Float] {
        let zcLength: UInt16 = 127
        let zcRoot: UInt16 = root
        var zc = [cyrinx_complex_f32_t](repeating: cyrinx_complex_f32_t(re: 0, im: 0), count: Int(zcLength))
        let rc = cyrinx_zc_generate(zcRoot, zcLength, &zc, zc.count)
        if rc != CYRINX_OK.rawValue {
            return fallbackPreamble(sampleRateHz: sampleRateHz, txGainCap: txGainCap)
        }

        let fs = Float(sampleRateHz)
        if fs <= 0 {
            return fallbackPreamble(sampleRateHz: 48_000, txGainCap: txGainCap)
        }

        let centerHz = Float(bandStartHz + bandEndHz) * 0.5
        let chipSpan = 4
        let maxCap = bandStartHz >= 9000 ? Float(0.70) : Float(0.12)
        let amplitude = min(max(txGainCap, 0), maxCap)
        var out = [Float]()
        out.reserveCapacity(zc.count * chipSpan)
        var sampleIndex = 0

        for chip in zc {
            let phaseOffset = atan2(chip.im, chip.re)
            for _ in 0..<chipSpan {
                let t = Float(sampleIndex) / fs
                let phase = (2.0 * Float.pi * centerHz * t) + phaseOffset
                out.append(amplitude * sin(phase))
                sampleIndex += 1
            }
        }
        return out
    }

    private static func fallbackPreamble(sampleRateHz: UInt32, txGainCap: Float) -> [Float] {
        let fs = max(Float(sampleRateHz), 8_000)
        let toneHz: Float = 20_500
        let amplitude = min(max(txGainCap, 0), 0.1)
        let count = 512
        return (0..<count).map { idx in
            let phase = 2.0 * Float.pi * toneHz * Float(idx) / fs
            return amplitude * sin(phase)
        }
    }

    private static func runtimeDCSSSymbolSamples() -> Int {
        let env = ProcessInfo.processInfo.environment["CYRINX_DCSS_SYMBOL_SAMPLES"] ?? ""
        let parsed = Int(env) ?? 256
        return min(max(parsed, 64), 4096)
    }

    private static func runtimeSyncThreshold() -> Float {
        let env = ProcessInfo.processInfo.environment["CYRINX_PREAMBLE_SYNC_THRESHOLD"] ?? ""
        let parsed = Float(env) ?? 0.52
        return min(max(parsed, 0.05), 0.98)
    }

    private static func runtimeForceRobustMode() -> Bool {
        let env = ProcessInfo.processInfo.environment["CYRINX_FORCE_ROBUST_MODE"]?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if env.isEmpty {
            return false
        }
        let normalized = env.lowercased()
        return normalized == "1" || normalized == "true" || normalized == "yes" || normalized == "on"
    }

    public static func generateSineTone(frequencyHz: Float, durationSecs: Float, sampleRateHz: Float, amplitude: Float) -> [Float] {
        let count = Int(sampleRateHz * durationSecs)
        return (0..<count).map { idx in
            let phase = 2.0 * Float.pi * frequencyHz * Float(idx) / sampleRateHz
            return amplitude * sin(phase)
        }
    }

    public static func calculateTHD(samples: [Float], sampleRateHz: Int, fundamentalHz: Float) -> Float {
        let n = 2048
        if samples.count < n { return 0.0 }

        var real = Array(samples[0..<n])
        var imag = [Float](repeating: 0, count: n)

        let fft = CyrinxFFT(size: n)
        fft.transform(real: &real, imag: &imag, forward: true)

        let binWidth = Float(sampleRateHz) / Float(n)

        func getPowerAtFreq(_ freq: Float) -> Float {
            let centerBin = Int(round(freq / binWidth))
            var powerSum: Float = 0
            for b in (centerBin - 1)...(centerBin + 1) {
                if b >= 0 && b < (n / 2) {
                    powerSum += real[b] * real[b] + imag[b] * imag[b]
                }
            }
            return powerSum
        }

        let pFund = getPowerAtFreq(fundamentalHz)
        if pFund <= 1e-9 { return 0.0 }

        var pHarmonics: Float = 0
        var harmonicMultiplier = 2
        while true {
            let harmFreq = fundamentalHz * Float(harmonicMultiplier)
            if harmFreq >= Float(sampleRateHz) / 2.0 {
                break
            }
            pHarmonics += getPowerAtFreq(harmFreq)
            harmonicMultiplier += 1
        }

        return sqrt(pHarmonics / pFund) * 100.0
    }
}

final class CyrinxFFT {
    let size: Int
    private let log2N: Int
    private let bitReversalTable: [Int]
    private let cosTable: [Float]
    private let sinTable: [Float]

    init(size: Int) {
        self.size = size
        var temp = size
        var count = 0
        while temp > 1 {
            precondition(temp % 2 == 0, "FFT size must be a power of 2")
            temp /= 2
            count += 1
        }
        self.log2N = count

        var revTable = [Int](repeating: 0, count: size)
        for i in 0..<size {
            var rev = 0
            var t = i
            for _ in 0..<count {
                rev = (rev << 1) | (t & 1)
                t >>= 1
            }
            revTable[i] = rev
        }
        self.bitReversalTable = revTable

        var cTable = [Float](repeating: 0, count: size / 2)
        var sTable = [Float](repeating: 0, count: size / 2)
        for k in 0..<(size / 2) {
            let angle = 2.0 * Double.pi * Double(k) / Double(size)
            cTable[k] = Float(cos(angle))
            sTable[k] = Float(sin(angle))
        }
        self.cosTable = cTable
        self.sinTable = sTable
    }

    func transform(real: inout [Float], imag: inout [Float], forward: Bool) {
        precondition(real.count == size && imag.count == size, "Input arrays must be of size \(size)")

        // 1. Bit-reversal sorting
        for i in 0..<size {
            let j = bitReversalTable[i]
            if i < j {
                real.swapAt(i, j)
                imag.swapAt(i, j)
            }
        }

        // 2. Butterfly stages
        var len = 2
        while len <= size {
            let halfLen = len / 2
            let twiddleStep = size / len

            for i in stride(from: 0, to: size, by: len) {
                for j in 0..<halfLen {
                    let k = i + j
                    let l = k + halfLen

                    let twiddleIdx = j * twiddleStep
                    let wr = cosTable[twiddleIdx]
                    let wi = forward ? -sinTable[twiddleIdx] : sinTable[twiddleIdx]

                    let tRe = real[l] * wr - imag[l] * wi
                    let tIm = real[l] * wi + imag[l] * wr

                    real[l] = real[k] - tRe
                    imag[l] = imag[k] - tIm

                    real[k] += tRe
                    imag[k] += tIm
                }
            }
            len <<= 1
        }
    }
}
// swiftlint:enable type_body_length

private func readBitsMSB(bytes: [UInt8], startBit: Int, bitCount: Int) -> UInt32 {
    if bytes.isEmpty || startBit < 0 || bitCount <= 0 {
        return 0
    }
    var value: UInt32 = 0
    for bitOffset in 0..<bitCount {
        let bitIndex = startBit + bitOffset
        let byteIndex = bitIndex / 8
        if byteIndex >= bytes.count {
            value <<= UInt32(bitCount - bitOffset)
            break
        }
        let shift = 7 - (bitIndex % 8)
        let bit = (bytes[byteIndex] >> UInt8(shift)) & 0x1
        value = (value << 1) | UInt32(bit)
    }
    return value
}

private func crc16CCITT(_ data: [UInt8]) -> UInt16 {
    var crc: UInt16 = 0xFFFF
    for byte in data {
        crc ^= UInt16(byte) << 8
        for _ in 0..<8 {
            if (crc & 0x8000) != 0 {
                crc = (crc << 1) ^ 0x1021
            } else {
                crc <<= 1
            }
        }
    }
    return crc
}
