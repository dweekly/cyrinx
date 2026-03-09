import CCyrinx
import Foundation

private let acousticFrameMaxBytes = 2048
private let acousticHeaderBytes = 7
private let acousticHeaderMagic0: UInt8 = 0xAC
private let acousticHeaderMagic1: UInt8 = 0x51

private enum AcousticBodyMode: UInt8 {
    case robustDCSS = 0
    case turboOFDM = 1
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
    private let robustConfig: VDSPDCSSConfig
    private let headerConfig: VDSPDCSSConfig
    private let turboConfig: VDSPOFDMConfig
    private let lock = NSLock()
    private var rxBuffer: [Float] = []
    private var rxSearchStart: Int = 0

    init(
        config: Config,
        dcssSymbolSamplesOverride: Int? = nil,
        preambleSyncThresholdOverride: Float? = nil
    ) {
        let boundedSampleRate = min(max(config.sampleRateHz, 8_000), 192_000)
        sampleRateHz = boundedSampleRate
        let boundedGain = min(max(config.txGainCap, 0), 0.12)
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

        preambleBlock = AcousticPHYLink.makePreambleBlock(
            sampleRateHz: boundedSampleRate,
            bandStartHz: config.bandStartHz,
            bandEndHz: config.bandEndHz,
            txGainCap: boundedGain
        )
        preamble = preambleBlock + preambleBlock
        preambleEnergy = max(1e-7, preamble.reduce(0) { $0 + ($1 * $1) })
        syncThreshold = min(max(preambleSyncThresholdOverride ?? AcousticPHYLink.runtimeSyncThreshold(), 0.10), 0.98)
        forceRobustMode = AcousticPHYLink.runtimeForceRobustMode()
    }

    func encode(frame: [UInt8]) throws -> [Float] {
        if frame.isEmpty || frame.count > acousticFrameMaxBytes {
            throw VDSPPHYError.invalidConfiguration("invalid acoustic frame payload size")
        }

        let mode = selectBodyMode(for: frame)
        let header = AcousticPHYHeader(mode: mode, payloadLength: UInt16(frame.count)).encode()
        let headerWave = try VDSPPHY.modulateDCSS(payload: header, config: headerConfig)
        let bodyWave: [Float]
        switch mode {
        case .robustDCSS:
            bodyWave = try VDSPPHY.modulateDCSS(payload: frame, config: robustConfig)
        case .turboOFDM:
            bodyWave = try VDSPPHY.modulateOFDMQPSK(payload: frame, config: turboConfig)
        }

        var out: [Float] = []
        out.reserveCapacity(preamble.count + headerWave.count + bodyWave.count)
        out.append(contentsOf: preamble)
        out.append(contentsOf: headerWave)
        out.append(contentsOf: bodyWave)
        return out
    }

    func ingest(samples: UnsafeBufferPointer<Float>) -> [AcousticDecodedFrame] {
        if samples.isEmpty {
            return []
        }
        lock.lock()
        rxBuffer.append(contentsOf: samples)
        let decoded = decodeAvailableLocked()
        lock.unlock()
        return decoded
    }

    func ingest(samples: [Float]) -> [AcousticDecodedFrame] {
        samples.withUnsafeBufferPointer { ingest(samples: $0) }
    }

    // swiftlint:disable:next function_body_length cyclomatic_complexity
    private func decodeAvailableLocked() -> [AcousticDecodedFrame] {
        var decoded: [AcousticDecodedFrame] = []
        let headerSamples = expectedDCSSSamples(payloadBytes: acousticHeaderBytes, config: headerConfig)

        while true {
            guard let lockResult = findBestPreambleLock(in: rxBuffer, startAt: rxSearchStart) else {
                trimUnlockedBufferForResync()
                break
            }

            let syncStart = lockResult.index
            let headerStart = syncStart + preamble.count
            let headerEnd = headerStart + headerSamples

            if rxBuffer.count < headerEnd {
                if syncStart > 0 {
                    rxBuffer.removeFirst(syncStart)
                    rxSearchStart = 0
                }
                break
            }

            let headerWindow = Array(rxBuffer[headerStart..<headerEnd])
            guard
                let decodedHeaderBytes = try? VDSPPHY.demodulateDCSS(
                    samples: headerWindow,
                    config: headerConfig
                ),
                let packetHeader = AcousticPHYHeader.decode(decodedHeaderBytes),
                packetHeader.payloadLength > 0,
                packetHeader.payloadLength <= acousticFrameMaxBytes
            else {
                rxBuffer.removeFirst(syncStart + 1)
                rxSearchStart = 0
                continue
            }

            let bodySamples = expectedBodySamples(
                mode: packetHeader.mode,
                payloadBytes: Int(packetHeader.payloadLength)
            )
            if bodySamples == 0 {
                rxBuffer.removeFirst(syncStart + 1)
                rxSearchStart = 0
                continue
            }

            let bodyStart = headerEnd
            let bodyEnd = bodyStart + bodySamples
            if rxBuffer.count < bodyEnd {
                if syncStart > 0 {
                    rxBuffer.removeFirst(syncStart)
                    rxSearchStart = 0
                }
                break
            }

            let bodyWindow = Array(rxBuffer[bodyStart..<bodyEnd])
            let bodyPayload: [UInt8]?
            switch packetHeader.mode {
            case .robustDCSS:
                bodyPayload = try? VDSPPHY.demodulateDCSS(samples: bodyWindow, config: robustConfig)
            case .turboOFDM:
                bodyPayload = try? VDSPPHY.demodulateOFDMQPSK(samples: bodyWindow, config: turboConfig)
            }

            guard let frame = bodyPayload, frame.count == Int(packetHeader.payloadLength) else {
                rxBuffer.removeFirst(syncStart + 1)
                rxSearchStart = 0
                continue
            }

            let report = channelReport(for: lockResult)
            decoded.append(AcousticDecodedFrame(frame: frame, report: report))
            rxBuffer.removeFirst(bodyEnd)
            rxSearchStart = 0
        }

        return decoded
    }

    private func selectBodyMode(for frame: [UInt8]) -> AcousticBodyMode {
        if forceRobustMode {
            return .robustDCSS
        }
        let coreHeaderStart = 2
        let coreHeaderBytes = 15
        if frame.count < (coreHeaderStart + coreHeaderBytes) {
            return .robustDCSS
        }

        let header = Array(frame[coreHeaderStart..<(coreHeaderStart + coreHeaderBytes)])
        let frameType = readBitsMSB(bytes: header, startBit: 4, bitCount: 4)
        let isControlPlane =
            frameType == UInt32(CYRINX_FRAME_ACK.rawValue)
            || frameType == UInt32(CYRINX_FRAME_CONTROL.rawValue)
        if isControlPlane {
            return .robustDCSS
        }

        let gearID = readBitsMSB(bytes: header, startBit: 64, bitCount: 3)
        return gearID >= UInt32(CYRINX_GEAR_G3_QPSK.rawValue) ? .turboOFDM : .robustDCSS
    }

    private func expectedBodySamples(mode: AcousticBodyMode, payloadBytes: Int) -> Int {
        switch mode {
        case .robustDCSS:
            return expectedDCSSSamples(payloadBytes: payloadBytes, config: robustConfig)
        case .turboOFDM:
            return expectedOFDMSamples(payloadBytes: payloadBytes, config: turboConfig)
        }
    }

    private func expectedDCSSSamples(payloadBytes: Int, config: VDSPDCSSConfig) -> Int {
        if payloadBytes < 0 || config.symbolSamples <= 0 {
            return 0
        }
        return (payloadBytes + 2) * config.symbolSamples
    }

    private func expectedOFDMSamples(payloadBytes: Int, config: VDSPOFDMConfig) -> Int {
        if payloadBytes < 0 || config.fftSize <= 0 || config.cpSamples <= 0 {
            return 0
        }
        let activeCarrierCount = ofdmActiveCarrierCount(config: config)
        if activeCarrierCount <= 0 {
            return 0
        }
        let bitCount = (payloadBytes + 2) * 8
        let qpskSymbols = (bitCount + 1) / 2
        let frameCount = max(1, (qpskSymbols + activeCarrierCount - 1) / activeCarrierCount)
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
        for start in lowerBound...searchLimit {
            var dot: Float = 0
            var segmentEnergy: Float = 0

            for idx in 0..<preamble.count {
                let sample = buffer[start + idx]
                let reference = preamble[idx]
                dot += sample * reference
                segmentEnergy += sample * sample
            }

            let norm = sqrt(max(segmentEnergy * preambleEnergy, 1e-7))
            let corr = dot / norm
            if corr < syncThreshold {
                continue
            }

            let scale = dot / preambleEnergy
            var errorEnergy: Float = 0
            for idx in 0..<preamble.count {
                let estimate = scale * preamble[idx]
                let err = buffer[start + idx] - estimate
                errorEnergy += err * err
            }

            let signalPower = max(1e-7, (scale * scale * preambleEnergy) / Float(preamble.count))
            let noisePower = max(1e-7, errorEnergy / Float(preamble.count))
            let snr = 10.0 * log10(signalPower / noisePower)
            let evm = sqrt(noisePower / signalPower) * 100.0
            return PreambleLock(index: start, correlation: corr, snrDB: snr, evmPct: evm)
        }

        return nil
    }

    private func channelReport(for lockResult: PreambleLock) -> cyrinx_channel_report_t {
        let estimatedPER = max(0, min(1, 1.0 - lockResult.correlation))
        return cyrinx_channel_report_t(
            snr_db: lockResult.snrDB,
            evm_pct: lockResult.evmPct,
            cfo_hz: 0,
            per_2s: estimatedPER,
            crc_fail: 0
        )
    }

    private func trimUnlockedBufferForResync() {
        let headerWindow = expectedDCSSSamples(payloadBytes: acousticHeaderBytes, config: headerConfig)
        let keep = max(preamble.count * 2, headerWindow)
        if rxBuffer.count > keep {
            let dropped = rxBuffer.count - keep
            rxBuffer.removeFirst(dropped)
            rxSearchStart = max(0, rxSearchStart - dropped)
        }
        let maxStart = max(0, rxBuffer.count - preamble.count)
        if rxSearchStart > maxStart {
            rxSearchStart = maxStart
        }
    }

    private static func makePreambleBlock(
        sampleRateHz: UInt32,
        bandStartHz: UInt32,
        bandEndHz: UInt32,
        txGainCap: Float
    ) -> [Float] {
        let zcLength: UInt16 = 127
        let zcRoot: UInt16 = 29
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
        let amplitude = min(max(txGainCap, 0), 0.12)
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
        return min(max(parsed, 0.10), 0.98)
    }

    private static func runtimeForceRobustMode() -> Bool {
        let env = ProcessInfo.processInfo.environment["CYRINX_FORCE_ROBUST_MODE"]?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if env.isEmpty {
            return false
        }
        let normalized = env.lowercased()
        return normalized == "1" || normalized == "true" || normalized == "yes" || normalized == "on"
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
