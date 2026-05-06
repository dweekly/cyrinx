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
        let nyquistGuardHz = max(1_000, (sampleRateHz / 2) - 500)
        let fallbackLowHz = 700
        let fallbackHighHz = min(3_400, nyquistGuardHz)
        let requestedLowHz = Int(config.bandStartHz)
        let requestedHighHz = Int(config.bandEndHz)
        let usesConfiguredAudibleBand = requestedLowHz >= 300 && requestedLowHz < 8_000 && requestedHighHz > requestedLowHz
        let lowHz = usesConfiguredAudibleBand
            ? min(max(requestedLowHz, 300), max(300, nyquistGuardHz - 450))
            : fallbackLowHz
        let highHz = usesConfiguredAudibleBand
            ? max(lowHz + 450, min(nyquistGuardHz, requestedHighHz))
            : fallbackHighHz
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
        let minSymbols = syncNibbles.count + 4
        let minSamples = minSymbols * symbolSamples

        while rxBuffer.count >= minSamples {
            guard let start = findSyncStart() else {
                break
            }
            guard
                let lenHi = decodeNibble(at: start, nibbleIndex: syncNibbles.count),
                let lenLo = decodeNibble(at: start, nibbleIndex: syncNibbles.count + 1)
            else {
                dropFront(start + symbolSamples)
                continue
            }
            let length = Int((lenHi << 4) | lenLo)
            let totalNibbles = syncNibbles.count + 2 + (length * 2) + 2
            let frameEnd = start + (totalNibbles * symbolSamples)
            if rxBuffer.count < frameEnd {
                if start > 0 {
                    dropFront(start)
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
                dropFront(start + symbolSamples)
                continue
            }
            guard
                let crcHi = decodeNibble(at: start, nibbleIndex: payloadNibbleIndex),
                let crcLo = decodeNibble(at: start, nibbleIndex: payloadNibbleIndex + 1)
            else {
                dropFront(start + symbolSamples)
                continue
            }

            let actualCRC = Int((crcHi << 4) | crcLo)
            var crcFrame = Data([UInt8(length)])
            crcFrame.append(payload)
            let expectedCRC = Self.crc8(crcFrame)
            if expectedCRC != actualCRC {
                dropFront(start + symbolSamples)
                continue
            }

            decoded.append(payload)
            dropFront(frameEnd)
        }

        let maxFrameNibbles = syncNibbles.count + 2 + (255 * 2) + 2
        let keep = max(symbolSamples * maxFrameNibbles, minSamples * 2)
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
        let refineStep = max(1, coarseStep / 32)
        var candidate = refineFrom
        while candidate <= refineTo {
            let score = scoreLeader(at: candidate)
            if score > bestScore {
                bestScore = score
                bestStart = candidate
            }
            candidate += refineStep
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
            guard let decoded = decodeNibbleAtSample(start + (symbolIndex * symbolSamples)) else {
                return Int.min
            }
            if decoded.nibble == 0 {
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
        let coarseStep = max(16, symbolSamples / 6)
        var candidate = from
        while candidate <= to {
            let score = scoreSync(at: candidate)
            if score > bestScore || (score == bestScore && isCloser(candidate, than: bestStart, to: nominalStart)) {
                bestScore = score
                bestStart = candidate
            }
            candidate += coarseStep
        }
        if bestStart >= 0 {
            let fineFrom = max(from, bestStart - coarseStep)
            let fineTo = min(to, bestStart + coarseStep)
            for candidate in fineFrom...fineTo {
                let score = scoreSync(at: candidate)
                if score > bestScore || (score == bestScore && isCloser(candidate, than: bestStart, to: nominalStart)) {
                    bestScore = score
                    bestStart = candidate
                }
            }
        }
        return bestScore >= syncNibbles.count - 1 ? bestStart : nil
    }

    private func isCloser(_ candidate: Int, than current: Int, to target: Int) -> Bool {
        if current < 0 {
            return true
        }
        return abs(candidate - target) < abs(current - target)
    }

    private func scoreSync(at start: Int) -> Int {
        scoreSyncWithMagnitude(at: start).score
    }

    private func findSyncStart() -> Int? {
        let maxStart = rxBuffer.count - (syncNibbles.count * symbolSamples)
        if maxStart <= 0 {
            return nil
        }
        let coarseStep = max(4, symbolSamples / 24)
        var bestStart = -1
        var bestScore = Int.min
        var bestMagnitude = -Float.greatestFiniteMagnitude
        var candidate = 0
        while candidate <= maxStart {
            if windowRms(start: candidate) >= minLeaderRms {
                let scored = scoreSyncWithMagnitude(at: candidate)
                if scored.score > bestScore || (scored.score == bestScore && scored.magnitude > bestMagnitude) {
                    bestScore = scored.score
                    bestMagnitude = scored.magnitude
                    bestStart = candidate
                }
            }
            candidate += coarseStep
        }
        if bestStart >= 0 {
            let fineFrom = max(0, bestStart - coarseStep)
            let fineTo = min(maxStart, bestStart + coarseStep)
            for candidate in fineFrom...fineTo {
                let scored = scoreSyncWithMagnitude(at: candidate)
                if scored.score > bestScore || (scored.score == bestScore && scored.magnitude > bestMagnitude) {
                    bestScore = scored.score
                    bestMagnitude = scored.magnitude
                    bestStart = candidate
                }
            }
        }
        return bestScore >= syncNibbles.count - 1 ? bestStart : nil
    }

    private func scoreSyncWithMagnitude(at start: Int) -> (score: Int, magnitude: Float) {
        var score = 0
        var magnitude: Float = 0
        for (index, expected) in syncNibbles.enumerated() {
            guard let decoded = decodeNibbleWithMagnitude(at: start, nibbleIndex: index) else {
                return (Int.min, 0)
            }
            if decoded.nibble == expected {
                score += 1
            }
            magnitude += decoded.magnitude
        }
        return (score, magnitude)
    }

    private func decodeNibble(at start: Int, nibbleIndex: Int) -> UInt8? {
        decodeNibbleWithMagnitude(at: start, nibbleIndex: nibbleIndex)?.nibble
    }

    private func decodeNibbleWithMagnitude(at start: Int, nibbleIndex: Int) -> (nibble: UInt8, magnitude: Float)? {
        decodeNibbleAtSample(start + (nibbleIndex * symbolSamples))
    }

    private func decodeNibbleAtSample(_ windowStart: Int) -> (nibble: UInt8, magnitude: Float)? {
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
        return (UInt8(bestNibble & 0xF), bestMagnitude)
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
