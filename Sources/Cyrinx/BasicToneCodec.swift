import Foundation

private let basicToneSyncByte: UInt8 = 0x7E

struct BasicToneCodec {
    private let sampleRateHz: Int
    private let lowHz: Int
    private let highHz: Int
    private let symbolSamples: Int
    private let txGainCap: Float
    private let lowRef: (sin: [Float], cos: [Float])
    private let highRef: (sin: [Float], cos: [Float])
    private let leaderSymbols = 6
    private let gapSymbols = 1
    private let minLeaderRms: Float = 0.0025
    private let preambleBits: [UInt8]
    private let minPreambleScore: Int
    private var rxBuffer: [Float] = []

    init(config: Config, symbolSamples: Int) {
        sampleRateHz = Int(min(max(config.sampleRateHz, 8_000), 192_000))
        lowHz = Int(min(config.bandStartHz, config.bandEndHz))
        highHz = Int(max(config.bandStartHz, config.bandEndHz))
        self.symbolSamples = min(max(symbolSamples, 240), 4096)
        txGainCap = min(max(config.txGainCap, 0.01), 0.80)
        lowRef = BasicToneCodec.makeReference(
            freqHz: lowHz,
            sampleRateHz: sampleRateHz,
            symbolSamples: self.symbolSamples,
            txGainCap: txGainCap
        )
        highRef = BasicToneCodec.makeReference(
            freqHz: highHz,
            sampleRateHz: sampleRateHz,
            symbolSamples: self.symbolSamples,
            txGainCap: txGainCap
        )
        let preamble = (0..<24).map { UInt8(($0 % 2) == 0 ? 1 : 0) }
        preambleBits = preamble + BasicToneCodec.byteToBits(basicToneSyncByte)
        minPreambleScore = preambleBits.count - 4
    }

    mutating func encode(payload: Data) -> [Float] {
        let trimmed = Data(payload.prefix(255))
        var frame = Data([UInt8(trimmed.count)])
        frame.append(trimmed)
        frame.append(UInt8(Self.crc8(frame)))

        var bits = preambleBits
        for byte in frame {
            bits.append(contentsOf: Self.byteToBits(byte))
        }

        let totalSymbols = leaderSymbols + gapSymbols + bits.count
        var output = [Float](repeating: 0, count: totalSymbols * symbolSamples)
        var cursor = 0
        for _ in 0..<leaderSymbols {
            output.replaceSubrange(cursor..<(cursor + symbolSamples), with: lowRef.sin)
            cursor += symbolSamples
        }
        cursor += gapSymbols * symbolSamples
        for bit in bits {
            let ref = bit == 0 ? lowRef.sin : highRef.sin
            output.replaceSubrange(cursor..<(cursor + symbolSamples), with: ref)
            cursor += symbolSamples
        }
        return output
    }

    mutating func ingest(samples: UnsafeBufferPointer<Float>) -> [Data] {
        if samples.isEmpty {
            return []
        }
        rxBuffer.append(contentsOf: samples)
        return decodeAvailable()
    }

    private mutating func decodeAvailable() -> [Data] {
        var decoded: [Data] = []
        let minBits = preambleBits.count + 16
        let minSamples = (leaderSymbols + gapSymbols + minBits) * symbolSamples

        while rxBuffer.count >= minSamples {
            guard let leaderStart = findLeaderStart() else {
                break
            }
            let start = leaderStart + ((leaderSymbols + gapSymbols) * symbolSamples)
            let payloadStartBit = preambleBits.count
            guard let length = decodeByte(at: start, startBit: payloadStartBit) else {
                dropFront(leaderStart + symbolSamples)
                continue
            }
            if scorePreamble(at: start) < minPreambleScore {
                dropFront(leaderStart + symbolSamples)
                continue
            }
            let totalFrameBytes = Int(length) + 2
            let totalBits = preambleBits.count + (totalFrameBytes * 8)
            let frameEnd = start + (totalBits * symbolSamples)
            if rxBuffer.count < frameEnd {
                if leaderStart > 0 {
                    dropFront(leaderStart)
                }
                break
            }

            var frame = [UInt8](repeating: 0, count: totalFrameBytes)
            var valid = true
            for index in 0..<totalFrameBytes {
                guard let byte = decodeByte(at: start, startBit: payloadStartBit + (index * 8)) else {
                    valid = false
                    break
                }
                frame[index] = byte
            }
            if !valid {
                dropFront(leaderStart + symbolSamples)
                continue
            }

            let expected = Self.crc8(frame.dropLast())
            let actual = Int(frame.last ?? 0)
            if expected != actual {
                dropFront(leaderStart + symbolSamples)
                continue
            }

            decoded.append(Data(frame[1..<frame.count - 1]))
            dropFront(frameEnd)
        }

        let keep = max(symbolSamples * 16, minSamples * 2)
        if rxBuffer.count > keep {
            dropFront(rxBuffer.count - keep)
        }
        return decoded
    }

    private func findLeaderStart() -> Int? {
        let maxStart = rxBuffer.count - ((leaderSymbols + gapSymbols + preambleBits.count) * symbolSamples)
        if maxStart <= 0 {
            return nil
        }

        var bestStart = -1
        var bestScore = Int.min
        let coarseStep = max(8, symbolSamples / 6)
        var coarse = 0
        while coarse <= maxStart {
            let score = scoreLeader(at: coarse)
            if score > bestScore {
                bestScore = score
                bestStart = coarse
            }
            coarse += coarseStep
        }

        if bestStart < 0 {
            return nil
        }

        let refineFrom = max(0, bestStart - coarseStep)
        let refineTo = min(maxStart, bestStart + coarseStep)
        for candidate in refineFrom...refineTo {
            let score = scoreLeader(at: candidate)
            if score > bestScore {
                bestScore = score
                bestStart = candidate
            }
        }
        return bestScore >= leaderSymbols ? bestStart : nil
    }

    private func scoreLeader(at start: Int) -> Int {
        var score = 0
        for symbolIndex in 0..<leaderSymbols {
            let windowStart = start + (symbolIndex * symbolSamples)
            if windowRms(start: windowStart) < minLeaderRms {
                return Int.min
            }
            guard let bit = decodeBit(at: start, bitIndex: symbolIndex) else {
                return Int.min
            }
            if bit == 0 {
                score += 1
            }
        }
        return score
    }

    private func scorePreamble(at start: Int) -> Int {
        var score = 0
        for (index, expectedBit) in preambleBits.enumerated() {
            guard let bit = decodeBit(at: start, bitIndex: index) else {
                return Int.min
            }
            if bit == expectedBit {
                score += 1
            }
        }
        return score
    }

    private func decodeByte(at start: Int, startBit: Int) -> UInt8? {
        var value: UInt8 = 0
        for bitOffset in 0..<8 {
            guard let bit = decodeBit(at: start, bitIndex: startBit + bitOffset) else {
                return nil
            }
            value = (value << 1) | bit
        }
        return value
    }

    private func decodeBit(at start: Int, bitIndex: Int) -> UInt8? {
        let windowStart = start + (bitIndex * symbolSamples)
        let windowEnd = windowStart + symbolSamples
        if windowStart < 0 || windowEnd > rxBuffer.count {
            return nil
        }

        var lowI: Float = 0
        var lowQ: Float = 0
        var highI: Float = 0
        var highQ: Float = 0
        for idx in 0..<symbolSamples {
            let sample = rxBuffer[windowStart + idx]
            lowI += sample * lowRef.sin[idx]
            lowQ += sample * lowRef.cos[idx]
            highI += sample * highRef.sin[idx]
            highQ += sample * highRef.cos[idx]
        }
        let lowMag = sqrt((lowI * lowI) + (lowQ * lowQ))
        let highMag = sqrt((highI * highI) + (highQ * highQ))
        return highMag > lowMag ? 1 : 0
    }

    private mutating func dropFront(_ count: Int) {
        if count <= 0 || rxBuffer.isEmpty {
            return
        }
        let clamped = min(count, rxBuffer.count)
        rxBuffer.removeFirst(clamped)
    }

    private func windowRms(start: Int) -> Float {
        let end = start + symbolSamples
        if start < 0 || end > rxBuffer.count {
            return 0
        }
        var energy: Float = 0
        for index in start..<end {
            let sample = rxBuffer[index]
            energy += sample * sample
        }
        return sqrt(energy / Float(symbolSamples))
    }

    private static func makeReference(
        freqHz: Int,
        sampleRateHz: Int,
        symbolSamples: Int,
        txGainCap: Float
    ) -> (sin: [Float], cos: [Float]) {
        let gain = txGainCap
        let values = (0..<symbolSamples).map { index -> (Float, Float) in
            let phase = (2.0 * Float.pi * Float(freqHz) * Float(index)) / Float(sampleRateHz)
            return (gain * Foundation.sin(phase), gain * Foundation.cos(phase))
        }
        return (values.map(\.0), values.map(\.1))
    }

    private static func byteToBits(_ value: UInt8) -> [UInt8] {
        (0..<8).map { bitIndex in (value >> UInt8(7 - bitIndex)) & 0x1 }
    }

    private static func crc8<S: Sequence>(_ bytes: S) -> Int where S.Element == UInt8 {
        var crc = 0
        for byte in bytes {
            crc ^= Int(byte)
        }
        return crc & 0xFF
    }
}
