import Foundation

struct NibbleToneCodec {
    private let sampleRateHz: Int
    private let symbolSamples: Int
    private let txGainCap: Float
    private let leaderSymbols = 4
    private let gapSymbols = 1
    private let minLeaderRms: Float = 0.0025
    private let syncNibbles: [UInt8] = [0xA, 0x5, 0xC, 0x3]
    private let toneRefs: [(sin: [Float], cos: [Float])]
    private var rxBuffer: [Float] = []

    init(config: Config, symbolSamples: Int) {
        sampleRateHz = Int(min(max(config.sampleRateHz, 8_000), 192_000))
        self.symbolSamples = min(max(symbolSamples, 240), 4096)
        txGainCap = min(max(config.txGainCap, 0.01), 0.80)
        let lowHz = 700
        let highHz = min(3_400, (sampleRateHz / 2) - 500)
        let span = max(15, highHz - lowHz)
        let symbolCount = self.symbolSamples
        let gain = txGainCap
        var refs: [(sin: [Float], cos: [Float])] = []
        refs.reserveCapacity(16)
        for index in 0..<16 {
            let freq = lowHz + ((span * index) / 15)
            refs.append(Self.makeReference(
                freqHz: freq,
                sampleRateHz: sampleRateHz,
                symbolSamples: symbolCount,
                txGainCap: gain
            ))
        }
        toneRefs = refs
    }

    mutating func encode(payload: Data) -> [Float] {
        let trimmed = Data(payload.prefix(255))
        var frame = Data([UInt8(trimmed.count)])
        frame.append(trimmed)
        frame.append(UInt8(Self.crc8(frame)))

        var nibbles = syncNibbles
        nibbles.append(UInt8((trimmed.count >> 4) & 0xF))
        nibbles.append(UInt8(trimmed.count & 0xF))
        for byte in trimmed {
            nibbles.append(UInt8((byte >> 4) & 0xF))
            nibbles.append(UInt8(byte & 0xF))
        }
        let crc = UInt8(Self.crc8(frame.dropLast()))
        nibbles.append(UInt8((crc >> 4) & 0xF))
        nibbles.append(UInt8(crc & 0xF))

        let totalSymbols = leaderSymbols + gapSymbols + nibbles.count
        var output = [Float](repeating: 0, count: totalSymbols * symbolSamples)
        var cursor = 0
        for _ in 0..<leaderSymbols {
            writeWaveform(toneRefs[0].sin, into: &output, offset: cursor)
            cursor += symbolSamples
        }
        cursor += gapSymbols * symbolSamples
        for nibble in nibbles {
            let ref = toneRefs[Int(nibble)].sin
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
        let minSymbols = leaderSymbols + gapSymbols + syncNibbles.count + 4
        let minSamples = minSymbols * symbolSamples

        while rxBuffer.count >= minSamples {
            guard let leaderStart = findLeaderStart() else {
                break
            }
            let nominalStart = leaderStart + ((leaderSymbols + gapSymbols) * symbolSamples)
            guard let start = refineSyncStart(near: nominalStart) else {
                dropFront(leaderStart + symbolSamples)
                continue
            }
            guard
                let lenHi = decodeNibble(at: start, nibbleIndex: syncNibbles.count),
                let lenLo = decodeNibble(at: start, nibbleIndex: syncNibbles.count + 1)
            else {
                dropFront(leaderStart + symbolSamples)
                continue
            }
            let length = Int((lenHi << 4) | lenLo)
            let totalNibbles = syncNibbles.count + 2 + (length * 2) + 2
            let frameEnd = start + (totalNibbles * symbolSamples)
            if rxBuffer.count < frameEnd {
                if leaderStart > 0 {
                    dropFront(leaderStart)
                }
                break
            }

            var payload = Data(capacity: length)
            var payloadNibbleIndex = syncNibbles.count + 2
            var valid = true
            for _ in 0..<length {
                guard
                    let hi = decodeNibble(at: start, nibbleIndex: payloadNibbleIndex),
                    let lo = decodeNibble(at: start, nibbleIndex: payloadNibbleIndex + 1)
                else {
                    valid = false
                    break
                }
                payload.append((hi << 4) | lo)
                payloadNibbleIndex += 2
            }
            guard valid else {
                dropFront(leaderStart + symbolSamples)
                continue
            }
            guard
                let crcHi = decodeNibble(at: start, nibbleIndex: payloadNibbleIndex),
                let crcLo = decodeNibble(at: start, nibbleIndex: payloadNibbleIndex + 1)
            else {
                dropFront(leaderStart + symbolSamples)
                continue
            }

            let actualCRC = Int((crcHi << 4) | crcLo)
            var crcFrame = Data([UInt8(length)])
            crcFrame.append(payload)
            let expectedCRC = Self.crc8(crcFrame)
            if expectedCRC != actualCRC {
                dropFront(leaderStart + symbolSamples)
                continue
            }

            decoded.append(payload)
            dropFront(frameEnd)
        }

        let keep = max(symbolSamples * 16, minSamples * 2)
        if rxBuffer.count > keep {
            dropFront(rxBuffer.count - keep)
        }
        return decoded
    }

    private func findLeaderStart() -> Int? {
        let maxStart = rxBuffer.count - ((leaderSymbols + gapSymbols + syncNibbles.count) * symbolSamples)
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
            guard let nibble = decodeNibbleAtSample(start + (symbolIndex * symbolSamples)) else {
                return Int.min
            }
            if nibble == 0 {
                score += 1
            }
        }
        return score
    }

    private func refineSyncStart(near nominalStart: Int) -> Int? {
        let maxStart = rxBuffer.count - (syncNibbles.count * symbolSamples)
        if maxStart <= 0 {
            return nil
        }
        let searchRadius = symbolSamples
        let from = max(0, nominalStart - searchRadius)
        let to = min(maxStart, nominalStart + searchRadius)
        var bestStart = -1
        var bestScore = Int.min
        for candidate in from...to {
            let score = scoreSync(at: candidate)
            if score > bestScore {
                bestScore = score
                bestStart = candidate
            }
        }
        return bestScore >= syncNibbles.count - 1 ? bestStart : nil
    }

    private func scoreSync(at start: Int) -> Int {
        var score = 0
        for (index, expected) in syncNibbles.enumerated() {
            guard let nibble = decodeNibble(at: start, nibbleIndex: index) else {
                return Int.min
            }
            if nibble == expected {
                score += 1
            }
        }
        return score
    }

    private func decodeNibble(at start: Int, nibbleIndex: Int) -> UInt8? {
        decodeNibbleAtSample(start + (nibbleIndex * symbolSamples))
    }

    private func decodeNibbleAtSample(_ windowStart: Int) -> UInt8? {
        let windowEnd = windowStart + symbolSamples
        if windowStart < 0 || windowEnd > rxBuffer.count {
            return nil
        }

        var bestNibble = 0
        var bestMagnitude = -Float.greatestFiniteMagnitude
        for (index, ref) in toneRefs.enumerated() {
            var iAcc: Float = 0
            var qAcc: Float = 0
            for sampleIndex in 0..<symbolSamples {
                let sample = rxBuffer[windowStart + sampleIndex]
                iAcc += sample * ref.sin[sampleIndex]
                qAcc += sample * ref.cos[sampleIndex]
            }
            let magnitude = sqrt((iAcc * iAcc) + (qAcc * qAcc))
            if magnitude > bestMagnitude {
                bestMagnitude = magnitude
                bestNibble = index
            }
        }
        return UInt8(bestNibble & 0xF)
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
        let values = (0..<symbolSamples).map { index -> (Float, Float) in
            let phase = (2.0 * Float.pi * Float(freqHz) * Float(index)) / Float(sampleRateHz)
            return (txGainCap * Foundation.sin(phase), txGainCap * Foundation.cos(phase))
        }
        return (values.map(\.0), values.map(\.1))
    }

    private static func crc8<S: Sequence>(_ bytes: S) -> Int where S.Element == UInt8 {
        var crc = 0
        for byte in bytes {
            crc ^= Int(byte)
        }
        return crc & 0xFF
    }
}
