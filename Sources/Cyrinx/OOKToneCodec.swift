import Foundation

struct OOKToneCodec {
    private let sampleRateHz: Int
    private let bitSamples: Int
    private let halfSamples: Int
    private let txGainCap: Float
    private let toneRef: (sin: [Float], cos: [Float])
    private let leaderToneHalves = 8
    private let leaderGapHalves = 4
    private let minActivityLevel: Float = 0.003
    private let preambleBits: [UInt8]
    private let minPreambleScore: Int
    private var rxBuffer: [Float] = []

    init(config: Config, symbolSamples: Int) {
        sampleRateHz = Int(min(max(config.sampleRateHz, 8_000), 192_000))
        bitSamples = min(max(symbolSamples, 480), 16384)
        halfSamples = max(240, bitSamples / 2)
        txGainCap = min(max(config.txGainCap, 0.01), 0.80)
        let toneHz = Int(min(max(config.bandStartHz, 700), config.bandEndHz))
        toneRef = Self.makeReference(freqHz: toneHz, sampleRateHz: sampleRateHz, halfSamples: halfSamples, gain: txGainCap)
        let preamble = (0..<24).map { UInt8(($0 % 2) == 0 ? 1 : 0) }
        preambleBits = preamble + Self.byteToBits(0x7E)
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

        let totalHalfSymbols = leaderToneHalves + leaderGapHalves + (bits.count * 2)
        var output = [Float](repeating: 0, count: totalHalfSymbols * halfSamples)
        var cursor = 0
        for _ in 0..<leaderToneHalves {
            writeWaveform(toneRef.sin, into: &output, offset: cursor)
            cursor += halfSamples
        }
        cursor += leaderGapHalves * halfSamples
        for bit in bits {
            let firstTone = bit == 0
            if firstTone {
                writeWaveform(toneRef.sin, into: &output, offset: cursor)
            }
            cursor += halfSamples
            if !firstTone {
                writeWaveform(toneRef.sin, into: &output, offset: cursor)
            }
            cursor += halfSamples
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

    mutating func decodeWaveform(_ samples: [Float]) -> [Data] {
        samples.withUnsafeBufferPointer { ingest(samples: $0) }
    }

    private mutating func decodeAvailable() -> [Data] {
        var decoded: [Data] = []
        let minBits = preambleBits.count + 16
        let minSamples = (leaderToneHalves + leaderGapHalves + (minBits * 2)) * halfSamples

        while rxBuffer.count >= minSamples {
            guard let leaderStart = findLeaderStart() else {
                break
            }
            let nominalStart = leaderStart + ((leaderToneHalves + leaderGapHalves) * halfSamples)
            guard let start = refinePayloadStart(near: nominalStart) else {
                dropFront(leaderStart + halfSamples)
                continue
            }
            let payloadStartBit = preambleBits.count
            guard let length = decodeByte(at: start, startBit: payloadStartBit) else {
                dropFront(leaderStart + halfSamples)
                continue
            }
            let totalFrameBytes = Int(length) + 2
            let totalBits = preambleBits.count + (totalFrameBytes * 8)
            let frameEnd = start + (totalBits * bitSamples)
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
                dropFront(leaderStart + halfSamples)
                continue
            }

            let expected = Self.crc8(frame.dropLast())
            let actual = Int(frame.last ?? 0)
            if expected != actual {
                dropFront(leaderStart + halfSamples)
                continue
            }

            decoded.append(Data(frame[1..<frame.count - 1]))
            dropFront(frameEnd)
        }

        let keep = max(bitSamples * 12, minSamples * 2)
        if rxBuffer.count > keep {
            dropFront(rxBuffer.count - keep)
        }
        return decoded
    }

    private func findLeaderStart() -> Int? {
        let maxStart = rxBuffer.count - ((leaderToneHalves + leaderGapHalves + (preambleBits.count * 2)) * halfSamples)
        if maxStart <= 0 {
            return nil
        }
        var bestStart = -1
        var bestScore = Int.min
        let coarseStep = max(8, halfSamples / 4)
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
        return bestScore >= leaderToneHalves ? bestStart : nil
    }

    private func scoreLeader(at start: Int) -> Int {
        var score = 0
        for halfIndex in 0..<leaderToneHalves {
            if activityLevel(at: start + (halfIndex * halfSamples)) < minActivityLevel {
                return Int.min
            }
            score += 1
        }
        return score
    }

    private func refinePayloadStart(near nominalStart: Int) -> Int? {
        let maxStart = rxBuffer.count - (preambleBits.count * bitSamples)
        if maxStart <= 0 {
            return nil
        }
        let searchRadius = halfSamples
        let from = max(0, nominalStart - searchRadius)
        let to = min(maxStart, nominalStart + searchRadius)
        var bestStart = -1
        var bestScore = Int.min
        for candidate in from...to {
            let score = scorePreamble(at: candidate)
            if score > bestScore {
                bestScore = score
                bestStart = candidate
            }
        }
        return bestScore >= minPreambleScore ? bestStart : nil
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
        let firstHalfStart = start + (bitIndex * bitSamples)
        let secondHalfStart = firstHalfStart + halfSamples
        let first = activityLevel(at: firstHalfStart)
        let second = activityLevel(at: secondHalfStart)
        if max(first, second) < (minActivityLevel * 0.5) {
            return nil
        }
        return first > second ? 0 : 1
    }

    private func activityLevel(at windowStart: Int) -> Float {
        let windowEnd = windowStart + halfSamples
        if windowStart < 0 || windowEnd > rxBuffer.count {
            return 0
        }
        let rms = windowRms(start: windowStart)
        var iAcc: Float = 0
        var qAcc: Float = 0
        for sampleIndex in 0..<halfSamples {
            let sample = rxBuffer[windowStart + sampleIndex]
            iAcc += sample * toneRef.sin[sampleIndex]
            qAcc += sample * toneRef.cos[sampleIndex]
        }
        let tone = sqrt((iAcc * iAcc) + (qAcc * qAcc))
        return max(rms * 100.0, tone)
    }

    private mutating func dropFront(_ count: Int) {
        if count <= 0 || rxBuffer.isEmpty {
            return
        }
        rxBuffer.removeFirst(min(count, rxBuffer.count))
    }

    private func windowRms(start: Int) -> Float {
        let end = start + halfSamples
        if start < 0 || end > rxBuffer.count {
            return 0
        }
        var energy: Float = 0
        for index in start..<end {
            let sample = rxBuffer[index]
            energy += sample * sample
        }
        return sqrt(energy / Float(halfSamples))
    }

    private static func makeReference(
        freqHz: Int,
        sampleRateHz: Int,
        halfSamples: Int,
        gain: Float
    ) -> (sin: [Float], cos: [Float]) {
        let values = (0..<halfSamples).map { index -> (Float, Float) in
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
