import Foundation

struct DTMFCodec {
    private let sampleRateHz: Int
    private let symbolSamples: Int
    private let txGainCap: Float
    private let leaderSymbols = 6
    private let gapSymbols = 2
    private let minLeaderRms: Float = 0.0025
    private let repetitionCount = 3
    private let syncNibbles: [UInt8] = [0x1, 0x2, 0x3, 0x4, 0xB, 0xC, 0xD, 0xE]
    private let rowHz = [697, 770, 852, 941]
    private let colHz = [1209, 1336, 1477, 1633]
    private let rowRefs: [(sin: [Float], cos: [Float])]
    private let colRefs: [(sin: [Float], cos: [Float])]
    private let symbolTable: [[Float]]
    private var rxBuffer: [Float] = []

    init(config: Config, symbolSamples: Int) {
        sampleRateHz = Int(min(max(config.sampleRateHz, 8_000), 192_000))
        self.symbolSamples = min(max(symbolSamples, 960), 8192)
        txGainCap = min(max(config.txGainCap, 0.01), 0.80)
        let symbolCount = self.symbolSamples
        let gain = txGainCap * 0.5
        var rows: [(sin: [Float], cos: [Float])] = []
        rows.reserveCapacity(rowHz.count)
        for freqHz in rowHz {
            rows.append(
                Self.makeReference(
                    freqHz: freqHz, sampleRateHz: sampleRateHz, symbolSamples: symbolCount, gain: gain))
        }
        rowRefs = rows
        var cols: [(sin: [Float], cos: [Float])] = []
        cols.reserveCapacity(colHz.count)
        for freqHz in colHz {
            cols.append(
                Self.makeReference(
                    freqHz: freqHz, sampleRateHz: sampleRateHz, symbolSamples: symbolCount, gain: gain))
        }
        colRefs = cols
        symbolTable = (0..<16).map { nibble in
            let row = max(0, min(3, nibble >> 2))
            let col = max(0, min(3, nibble & 0x3))
            var symbol = [Float](repeating: 0, count: symbolCount)
            for index in 0..<symbolCount {
                symbol[index] = rows[row].sin[index] + cols[col].sin[index]
            }
            return symbol
        }
    }

    mutating func encode(payload: Data) -> [Float] {
        let trimmed = Data(payload.prefix(255))
        var crcFrame = Data([UInt8(trimmed.count)])
        crcFrame.append(trimmed)
        let crc = UInt8(Self.crc8(crcFrame))

        var nibbles = syncNibbles
        let lenHi = UInt8((trimmed.count >> 4) & 0xF)
        let lenLo = UInt8(trimmed.count & 0xF)
        for _ in 0..<repetitionCount {
            nibbles.append(lenHi)
        }
        for _ in 0..<repetitionCount {
            nibbles.append(lenLo)
        }
        for byte in trimmed {
            let hi = UInt8((byte >> 4) & 0xF)
            let lo = UInt8(byte & 0xF)
            for _ in 0..<repetitionCount {
                nibbles.append(hi)
            }
            for _ in 0..<repetitionCount {
                nibbles.append(lo)
            }
        }
        let crcHi = UInt8((crc >> 4) & 0xF)
        let crcLo = UInt8(crc & 0xF)
        for _ in 0..<repetitionCount {
            nibbles.append(crcHi)
        }
        for _ in 0..<repetitionCount {
            nibbles.append(crcLo)
        }

        let totalSymbols = leaderSymbols + gapSymbols + nibbles.count
        var output = [Float](repeating: 0, count: totalSymbols * symbolSamples)
        var cursor = 0
        for _ in 0..<leaderSymbols {
            writeWaveform(symbolTable[0], into: &output, offset: cursor)
            cursor += symbolSamples
        }
        cursor += gapSymbols * symbolSamples
        for nibble in nibbles {
            writeWaveform(symbolTable[Int(nibble)], into: &output, offset: cursor)
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
        let minSymbols = leaderSymbols + gapSymbols + syncNibbles.count + (4 * repetitionCount)
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
                let lenHi = decodeRepeatedNibble(at: start, nibbleIndex: syncNibbles.count),
                let lenLo = decodeRepeatedNibble(at: start, nibbleIndex: syncNibbles.count + repetitionCount)
            else {
                dropFront(leaderStart + symbolSamples)
                continue
            }
            let length = Int((lenHi << 4) | lenLo)
            let totalNibbles =
                syncNibbles.count + (2 * repetitionCount) + (length * 2 * repetitionCount)
                + (2 * repetitionCount)
            let frameEnd = start + (totalNibbles * symbolSamples)
            if rxBuffer.count < frameEnd {
                if leaderStart > 0 {
                    dropFront(leaderStart)
                }
                break
            }

            var payload = Data(capacity: length)
            var nibbleIndex = syncNibbles.count + (2 * repetitionCount)
            var valid = true
            for _ in 0..<length {
                guard
                    let hi = decodeRepeatedNibble(at: start, nibbleIndex: nibbleIndex),
                    let lo = decodeRepeatedNibble(at: start, nibbleIndex: nibbleIndex + repetitionCount)
                else {
                    valid = false
                    break
                }
                payload.append((hi << 4) | lo)
                nibbleIndex += 2 * repetitionCount
            }
            guard valid else {
                dropFront(leaderStart + symbolSamples)
                continue
            }
            guard
                let crcHi = decodeRepeatedNibble(at: start, nibbleIndex: nibbleIndex),
                let crcLo = decodeRepeatedNibble(at: start, nibbleIndex: nibbleIndex + repetitionCount)
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
            guard let nibble = decodeNibbleAtSample(windowStart) else {
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
        let coarseStep = max(4, symbolSamples / 24)
        var candidate = from
        while candidate <= to {
            let score = scoreSync(at: candidate)
            if score > bestScore {
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
                if score > bestScore {
                    bestScore = score
                    bestStart = candidate
                }
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

    private func decodeRepeatedNibble(at start: Int, nibbleIndex: Int) -> UInt8? {
        var values = [UInt8]()
        values.reserveCapacity(repetitionCount)
        for offset in 0..<repetitionCount {
            guard let nibble = decodeNibble(at: start, nibbleIndex: nibbleIndex + offset) else {
                return nil
            }
            values.append(nibble)
        }
        return Self.majorityNibble(values)
    }

    private func decodeNibbleAtSample(_ windowStart: Int) -> UInt8? {
        let windowEnd = windowStart + symbolSamples
        if windowStart < 0 || windowEnd > rxBuffer.count {
            return nil
        }

        var rowMagnitudes = [Float](repeating: 0, count: 4)
        var colMagnitudes = [Float](repeating: 0, count: 4)
        for index in 0..<4 {
            var rowI: Float = 0
            var rowQ: Float = 0
            var colI: Float = 0
            var colQ: Float = 0
            for sampleIndex in 0..<symbolSamples {
                let sample = rxBuffer[windowStart + sampleIndex]
                rowI += sample * rowRefs[index].sin[sampleIndex]
                rowQ += sample * rowRefs[index].cos[sampleIndex]
                colI += sample * colRefs[index].sin[sampleIndex]
                colQ += sample * colRefs[index].cos[sampleIndex]
            }
            rowMagnitudes[index] = sqrt((rowI * rowI) + (rowQ * rowQ))
            colMagnitudes[index] = sqrt((colI * colI) + (colQ * colQ))
        }
        guard
            let row = rowMagnitudes.enumerated().max(by: { $0.element < $1.element })?.offset,
            let col = colMagnitudes.enumerated().max(by: { $0.element < $1.element })?.offset
        else {
            return nil
        }
        return UInt8(((row << 2) | col) & 0xF)
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
        gain: Float
    ) -> (sin: [Float], cos: [Float]) {
        let values = (0..<symbolSamples).map { index -> (Float, Float) in
            let phase = (2.0 * Float.pi * Float(freqHz) * Float(index)) / Float(sampleRateHz)
            return (gain * Foundation.sin(phase), gain * Foundation.cos(phase))
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

    private static func majorityNibble(_ values: [UInt8]) -> UInt8? {
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
