import Foundation

struct NibbleToneCodec {
    private let sampleRateHz: Int
    private let symbolSamples: Int
    private let txGainCap: Float
    private let leaderSymbols = 4
    private let gapSymbols = 1
    private let minLeaderRms: Float = 0.0025
    private let repetitionCount = 3
    private let symbolsPerNibble: Int
    private let syncSymbols: [UInt8] = [2, 2, 1, 1, 3, 0, 0, 3]
    private let toneRefs: [(sin: [Float], cos: [Float])]
    private var rxBuffer: [Float] = []

    init(config: Config, symbolSamples: Int) {
        sampleRateHz = Int(min(max(config.sampleRateHz, 8_000), 192_000))
        self.symbolSamples = min(max(symbolSamples, 240), 4096)
        txGainCap = min(max(config.txGainCap, 0.01), 0.80)
        symbolsPerNibble = 2 * repetitionCount
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
        refs.reserveCapacity(4)
        for index in 0..<4 {
            let freq = lowHz + ((span * index) / 3)
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
        let crc = UInt8(Self.crc8(frame))

        var nibbles: [UInt8] = []
        nibbles.append(UInt8((trimmed.count >> 4) & 0xF))
        nibbles.append(UInt8(trimmed.count & 0xF))
        for byte in trimmed {
            nibbles.append(UInt8((byte >> 4) & 0xF))
            nibbles.append(UInt8(byte & 0xF))
        }
        let crcHi = UInt8((crc >> 4) & 0xF)
        let crcLo = UInt8(crc & 0xF)

        var symbols = syncSymbols
        for nibble in nibbles {
            appendRepeatedNibble(nibble, to: &symbols)
        }
        appendRepeatedNibble(crcHi, to: &symbols)
        appendRepeatedNibble(crcLo, to: &symbols)

        let totalSymbols = leaderSymbols + gapSymbols + symbols.count
        var output = [Float](repeating: 0, count: totalSymbols * symbolSamples)
        var cursor = 0
        for _ in 0..<leaderSymbols {
            writeWaveform(toneRefs[0].sin, into: &output, offset: cursor)
            cursor += symbolSamples
        }
        cursor += gapSymbols * symbolSamples
        for symbol in symbols {
            let ref = toneRefs[Int(symbol)].sin
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
        let minSymbols = syncSymbols.count + (4 * symbolsPerNibble)
        let minSamples = minSymbols * symbolSamples

        while rxBuffer.count >= minSamples {
            guard let start = findSyncStart() else {
                break
            }
            guard
                let lenHi = decodeRepeatedNibble(at: start, symbolIndex: syncSymbols.count),
                let lenLo = decodeRepeatedNibble(at: start, symbolIndex: syncSymbols.count + symbolsPerNibble)
            else {
                dropFront(start + symbolSamples)
                continue
            }
            let length = Int((lenHi << 4) | lenLo)
            let totalFrameNibbles = 2 + (length * 2) + 2
            let totalSymbols = syncSymbols.count + (totalFrameNibbles * symbolsPerNibble)
            let frameEnd = start + (totalSymbols * symbolSamples)
            if rxBuffer.count < frameEnd {
                if start > 0 {
                    dropFront(start)
                }
                break
            }

            var payload = Data(capacity: length)
            var payloadSymbolIndex = syncSymbols.count + (2 * symbolsPerNibble)
            var valid = true
            for _ in 0..<length {
                guard
                    let hi = decodeRepeatedNibble(at: start, symbolIndex: payloadSymbolIndex),
                    let lo = decodeRepeatedNibble(at: start, symbolIndex: payloadSymbolIndex + symbolsPerNibble)
                else {
                    valid = false
                    break
                }
                payload.append((hi << 4) | lo)
                payloadSymbolIndex += 2 * symbolsPerNibble
            }
            guard valid else {
                dropFront(start + symbolSamples)
                continue
            }
            guard
                let crcHi = decodeRepeatedNibble(at: start, symbolIndex: payloadSymbolIndex),
                let crcLo = decodeRepeatedNibble(at: start, symbolIndex: payloadSymbolIndex + symbolsPerNibble)
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

        let maxFrameNibbles = 2 + (255 * 2) + 2
        let maxFrameSymbols = syncSymbols.count + (maxFrameNibbles * symbolsPerNibble)
        let keep = max(symbolSamples * maxFrameSymbols, minSamples * 2)
        if rxBuffer.count > keep {
            dropFront(rxBuffer.count - keep)
        }
        return decoded
    }

    private func findSyncStart() -> Int? {
        let maxStart = rxBuffer.count - (syncSymbols.count * symbolSamples)
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
        return bestScore >= syncSymbols.count - 1 ? bestStart : nil
    }

    private func scoreSyncWithMagnitude(at start: Int) -> (score: Int, magnitude: Float) {
        var score = 0
        var magnitude: Float = 0
        for (index, expected) in syncSymbols.enumerated() {
            guard let decoded = decodeSymbolWithMagnitude(at: start, symbolIndex: index) else {
                return (Int.min, 0)
            }
            if decoded.symbol == expected {
                score += 1
            }
            magnitude += decoded.magnitude
        }
        return (score, magnitude)
    }

    private func decodeRepeatedNibble(at start: Int, symbolIndex: Int) -> UInt8? {
        guard
            let high = decodeRepeatedSymbol(at: start, symbolIndex: symbolIndex),
            let low = decodeRepeatedSymbol(at: start, symbolIndex: symbolIndex + repetitionCount)
        else {
            return nil
        }
        return ((high & 0x3) << 2) | (low & 0x3)
    }

    private func decodeRepeatedSymbol(at start: Int, symbolIndex: Int) -> UInt8? {
        var values: [UInt8] = []
        values.reserveCapacity(repetitionCount)
        for offset in 0..<repetitionCount {
            guard let symbol = decodeSymbol(at: start, symbolIndex: symbolIndex + offset) else {
                return nil
            }
            values.append(symbol)
        }
        return Self.majoritySymbol(values)
    }

    private func decodeSymbol(at start: Int, symbolIndex: Int) -> UInt8? {
        decodeSymbolWithMagnitude(at: start, symbolIndex: symbolIndex)?.symbol
    }

    private func decodeSymbolWithMagnitude(at start: Int, symbolIndex: Int) -> (symbol: UInt8, magnitude: Float)? {
        decodeSymbolAtSample(start + (symbolIndex * symbolSamples))
    }

    private func decodeSymbolAtSample(_ windowStart: Int) -> (symbol: UInt8, magnitude: Float)? {
        let windowEnd = windowStart + symbolSamples
        if windowStart < 0 || windowEnd > rxBuffer.count {
            return nil
        }

        var bestSymbol = 0
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
                bestSymbol = index
            }
        }
        return (UInt8(bestSymbol & 0x3), bestMagnitude)
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

    private func appendRepeatedNibble(_ nibble: UInt8, to symbols: inout [UInt8]) {
        let high = (nibble >> 2) & 0x3
        let low = nibble & 0x3
        for _ in 0..<repetitionCount {
            symbols.append(high)
        }
        for _ in 0..<repetitionCount {
            symbols.append(low)
        }
    }

    private static func crc8<S: Sequence>(_ bytes: S) -> Int where S.Element == UInt8 {
        var crc = 0
        for byte in bytes {
            crc ^= Int(byte)
        }
        return crc & 0xFF
    }

    private static func majoritySymbol(_ values: [UInt8]) -> UInt8? {
        guard !values.isEmpty else {
            return nil
        }
        var counts = [UInt8: Int]()
        for value in values {
            counts[value, default: 0] += 1
        }
        return counts.max { lhs, rhs in
            if lhs.value == rhs.value {
                return lhs.key > rhs.key
            }
            return lhs.value < rhs.value
        }?.key
    }
}
