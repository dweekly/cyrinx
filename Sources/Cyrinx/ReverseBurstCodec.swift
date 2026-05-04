import Foundation

struct ReverseBurstCodec {
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
    private let symbolSearchRadius: Int
    private let symbolSearchStep: Int
    private let preambleBits: [UInt8]
    private let minPreambleScore: Int
    private let guardBytes: [UInt8] = [0x55, 0x2D]
    private let fixedPayloadBytes = 5
    private let repetitionCount = 5
    private var rxBuffer: [Float] = []

    init(config: Config, symbolSamples: Int) {
        sampleRateHz = Int(min(max(config.sampleRateHz, 8_000), 192_000))
        lowHz = Int(min(config.bandStartHz, config.bandEndHz))
        highHz = Int(max(config.bandStartHz, config.bandEndHz))
        self.symbolSamples = min(max(symbolSamples, 240), 4096)
        txGainCap = min(max(config.txGainCap, 0.01), 0.80)
        lowRef = Self.makeReference(
            freqHz: lowHz,
            sampleRateHz: sampleRateHz,
            symbolSamples: self.symbolSamples,
            txGainCap: txGainCap
        )
        highRef = Self.makeReference(
            freqHz: highHz,
            sampleRateHz: sampleRateHz,
            symbolSamples: self.symbolSamples,
            txGainCap: txGainCap
        )
        symbolSearchRadius = max(8, self.symbolSamples / 8)
        symbolSearchStep = max(1, self.symbolSamples / 48)
        let preamble = (0..<24).map { UInt8(($0 % 2) == 0 ? 1 : 0) }
        preambleBits = preamble + Self.byteToBits(0x7E)
        minPreambleScore = preambleBits.count - 4
    }

    mutating func encode(payload: Data) -> [Float] {
        let trimmed = payload.prefix(fixedPayloadBytes)
        var padded = [UInt8](repeating: 0, count: fixedPayloadBytes)
        for (index, byte) in trimmed.enumerated() {
            padded[index] = byte
        }
        let length = UInt8(trimmed.count)
        let crc = UInt8(Self.crc8(Array(padded) + [length]))
        var frameBytes = guardBytes
        for byte in padded {
            frameBytes.append(contentsOf: Array(repeating: byte, count: repetitionCount))
        }
        frameBytes.append(contentsOf: Array(repeating: length, count: repetitionCount))
        frameBytes.append(contentsOf: Array(repeating: crc, count: repetitionCount))

        var bits = preambleBits
        for byte in frameBytes {
            bits.append(contentsOf: Self.byteToBits(byte))
        }

        let totalSymbols = leaderSymbols + gapSymbols + bits.count
        var output = [Float](repeating: 0, count: totalSymbols * symbolSamples)
        var cursor = 0
        for _ in 0..<leaderSymbols {
            writeWaveform(lowRef.sin, into: &output, offset: cursor)
            cursor += symbolSamples
        }
        cursor += gapSymbols * symbolSamples
        for bit in bits {
            let ref = bit == 0 ? lowRef.sin : highRef.sin
            writeWaveform(ref, into: &output, offset: cursor)
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

    mutating func decodeWaveform(_ samples: [Float]) -> [Data] {
        samples.withUnsafeBufferPointer { ingest(samples: $0) }
    }

    private mutating func decodeAvailable() -> [Data] {
        var decoded: [Data] = []
        let frameBytes = guardBytes.count + ((fixedPayloadBytes + 2) * repetitionCount)
        let totalBits = preambleBits.count + (frameBytes * 8)
        let minSamples = (leaderSymbols + gapSymbols + totalBits) * symbolSamples

        while rxBuffer.count >= minSamples {
            guard let leaderStart = findLeaderStart() else {
                break
            }
            let nominalStart = leaderStart + ((leaderSymbols + gapSymbols) * symbolSamples)
            guard let refinedStart = refinePayloadStart(near: nominalStart) else {
                dropFront(leaderStart + symbolSamples)
                continue
            }

            let searchFrom = max(0, refinedStart - (symbolSamples / 2))
            let searchTo = min(rxBuffer.count - (totalBits * symbolSamples), refinedStart + (symbolSamples / 2))
            var accepted: (payload: Data, frameEnd: Int)?
            if searchFrom <= searchTo {
                for candidateStart in searchFrom...searchTo {
                    let preambleScore = scorePreamble(at: candidateStart)
                    if preambleScore < minPreambleScore - 1 {
                        continue
                    }
                    var frame = [UInt8](repeating: 0, count: frameBytes)
                    var valid = true
                    for index in 0..<frameBytes {
                        guard let byte = decodeByte(at: candidateStart, startBit: preambleBits.count + (index * 8)) else {
                            valid = false
                            break
                        }
                        frame[index] = byte
                    }
                    if !valid {
                        continue
                    }

                    let payloadTriples = frame[guardBytes.count..<(guardBytes.count + (fixedPayloadBytes * repetitionCount))]
                    var correctedPayload = [UInt8]()
                    correctedPayload.reserveCapacity(fixedPayloadBytes)
                    for index in 0..<fixedPayloadBytes {
                        let start = payloadTriples.startIndex + (index * repetitionCount)
                        let end = start + repetitionCount
                        correctedPayload.append(majorityByte(Array(payloadTriples[start..<end])))
                    }

                    let lengthBase = guardBytes.count + (fixedPayloadBytes * repetitionCount)
                    let length = Int(majorityByte(Array(frame[lengthBase..<(lengthBase + repetitionCount)])))
                    if length > fixedPayloadBytes {
                        continue
                    }

                    let crcBase = lengthBase + repetitionCount
                    let actual = Int(majorityByte(Array(frame[crcBase..<(crcBase + repetitionCount)])))
                    let expected = Self.crc8(correctedPayload + [UInt8(length)])
                    if expected != actual {
                        continue
                    }

                    accepted = (
                        Data(correctedPayload[0..<length]),
                        candidateStart + (totalBits * symbolSamples)
                    )
                    break
                }
            }

            guard let accepted else {
                dropFront(leaderStart + symbolSamples)
                continue
            }
            decoded.append(accepted.payload)
            dropFront(accepted.frameEnd)
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

    private func refinePayloadStart(near nominalStart: Int) -> Int? {
        let maxStart = rxBuffer.count - (preambleBits.count * symbolSamples)
        if maxStart <= 0 {
            return nil
        }
        let searchRadius = symbolSamples
        let from = max(0, nominalStart - searchRadius)
        let to = min(maxStart, nominalStart + searchRadius)
        if from > to {
            return nil
        }

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
        let nominalStart = start + (bitIndex * symbolSamples)
        guard nominalStart >= 0 else {
            return nil
        }

        let searchFrom = max(0, nominalStart - symbolSearchRadius)
        let searchTo = min(rxBuffer.count - symbolSamples, nominalStart + symbolSearchRadius)
        if searchFrom > searchTo {
            return nil
        }

        var bestBit: UInt8?
        var bestScore: Float = -.greatestFiniteMagnitude
        var candidateStart = searchFrom
        while candidateStart <= searchTo {
            let result = classifyBit(windowStart: candidateStart)
            let strength = abs(result.highMag - result.lowMag)
            if strength > bestScore {
                bestScore = strength
                bestBit = result.bit
            }
            candidateStart += symbolSearchStep
        }
        return bestBit
    }

    private func classifyBit(windowStart: Int) -> (bit: UInt8, lowMag: Float, highMag: Float) {
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
        return (highMag > lowMag ? 1 : 0, lowMag, highMag)
    }

    private mutating func dropFront(_ count: Int) {
        if count <= 0 || rxBuffer.isEmpty {
            return
        }
        rxBuffer.removeFirst(min(count, rxBuffer.count))
    }

    private func windowRms(start: Int) -> Float {
        let end = start + symbolSamples
        if start < 0 || end > rxBuffer.count {
            return 0
        }
        var energy: Float = 0
        for index in start..<end {
            energy += rxBuffer[index] * rxBuffer[index]
        }
        return sqrt(energy / Float(symbolSamples))
    }

    private func majorityByte(_ bytes: [UInt8]) -> UInt8 {
        precondition(!bytes.isEmpty)
        var value: UInt8 = 0
        for bitIndex in 0..<8 {
            let mask = UInt8(1 << UInt8(7 - bitIndex))
            let ones = bytes.reduce(0) { count, byte in count + ((byte & mask) == 0 ? 0 : 1) }
            if ones * 2 >= bytes.count {
                value |= mask
            }
        }
        return value
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
