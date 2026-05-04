import Foundation

struct MorseToneCodec {
    private let sampleRateHz: Int
    private let unitSamples: Int
    private let txGainCap: Float
    private let toneRef: (sin: [Float], cos: [Float])
    private let leaderToneUnits = 12
    private let leaderGapUnits = 6
    private let trailingGapUnits = 8
    private let minActivityLevel: Float = 0.002
    private let maxPayloadBytes = 32
    private var rxBuffer: [Float] = []

    private static let charToMorse: [Character: String] = [
        "0": "-----", "1": ".----", "2": "..---", "3": "...--", "4": "....-",
        "5": ".....", "6": "-....", "7": "--...", "8": "---..", "9": "----.",
        "A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".", "F": "..-."
    ]
    private static let morseToChar: [String: Character] = {
        var out: [String: Character] = [:]
        for (ch, pattern) in charToMorse {
            out[pattern] = ch
        }
        return out
    }()

    init(config: Config, symbolSamples: Int) {
        sampleRateHz = Int(min(max(config.sampleRateHz, 8_000), 192_000))
        unitSamples = min(max(symbolSamples, 1_200), 8_192)
        txGainCap = min(max(config.txGainCap, 0.01), 0.80)
        let requestedToneHz = Int(min(config.bandStartHz, config.bandEndHz))
        let toneHz = requestedToneHz > 4_000 ? 1_200 : max(700, requestedToneHz)
        toneRef = Self.makeReference(freqHz: toneHz, sampleRateHz: sampleRateHz, unitSamples: unitSamples, gain: txGainCap)
    }

    mutating func encode(payload: Data) -> [Float] {
        let trimmed = Array(payload.prefix(maxPayloadBytes))
        var hex = String(format: "%02X", trimmed.count)
        for byte in trimmed {
            hex += String(format: "%02X", byte)
        }

        var units: [Bool] = []
        units.reserveCapacity((hex.count * 20) + leaderToneUnits + leaderGapUnits + trailingGapUnits)
        appendUnits(active: true, count: leaderToneUnits, into: &units)
        appendUnits(active: false, count: leaderGapUnits, into: &units)

        let chars = Array(hex)
        for (charIndex, char) in chars.enumerated() {
            guard let pattern = Self.charToMorse[char] else {
                continue
            }
            for (symbolIndex, symbol) in pattern.enumerated() {
                appendUnits(active: true, count: symbol == "." ? 1 : 3, into: &units)
                if symbolIndex + 1 < pattern.count {
                    appendUnits(active: false, count: 1, into: &units)
                }
            }
            if charIndex + 1 < chars.count {
                appendUnits(active: false, count: 3, into: &units)
            }
        }
        appendUnits(active: false, count: trailingGapUnits, into: &units)

        var output = [Float](repeating: 0, count: units.count * unitSamples)
        var cursor = 0
        for active in units {
            if active {
                writeWaveform(toneRef.sin, into: &output, offset: cursor)
            }
            cursor += unitSamples
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
        let minUnits = leaderToneUnits + leaderGapUnits + 20
        let minSamples = minUnits * unitSamples

        while rxBuffer.count >= minSamples {
            guard let leaderStart = findLeaderStart() else {
                break
            }
            let payloadStart = leaderStart + ((leaderToneUnits + leaderGapUnits) * unitSamples)
            guard let accepted = decodeFrame(start: payloadStart, leaderStart: leaderStart) else {
                dropFront(leaderStart + unitSamples)
                continue
            }
            decoded.append(accepted.payload)
            dropFront(accepted.frameEnd)
        }

        let keep = max(unitSamples * 24, minSamples * 2)
        if rxBuffer.count > keep {
            dropFront(rxBuffer.count - keep)
        }
        return decoded
    }

    private func decodeFrame(start: Int, leaderStart: Int) -> (payload: Data, frameEnd: Int)? {
        let threshold = estimateThreshold(leaderStart: leaderStart)
        let maxUnits = (rxBuffer.count - start) / unitSamples
        guard maxUnits > 0 else {
            return nil
        }

        var states = [Bool]()
        states.reserveCapacity(maxUnits)
        for unitIndex in 0..<maxUnits {
            let activity = activityLevel(at: start + (unitIndex * unitSamples))
            states.append(activity >= threshold)
        }

        var decodedChars = [Character]()
        var currentPattern = ""
        var unitIndex = 0
        var expectedChars: Int?

        while unitIndex < states.count {
            let state = states[unitIndex]
            var runLength = 1
            while unitIndex + runLength < states.count, states[unitIndex + runLength] == state {
                runLength += 1
            }

            if state {
                currentPattern.append(runLength >= 3 ? "-" : ".")
            } else if runLength >= 3 {
                guard !currentPattern.isEmpty, let decoded = Self.morseToChar[currentPattern] else {
                    return nil
                }
                decodedChars.append(decoded)
                currentPattern.removeAll(keepingCapacity: true)

                if decodedChars.count == 2, expectedChars == nil {
                    let prefix = String(decodedChars[0...1])
                    guard let length = Int(prefix, radix: 16), length <= maxPayloadBytes else {
                        return nil
                    }
                    expectedChars = 2 + (length * 2)
                }
                if let expectedChars, decodedChars.count >= expectedChars {
                    let hex = String(decodedChars)
                    guard let payload = Self.decodeHexPayload(hex) else {
                        return nil
                    }
                    let frameEnd = start + ((unitIndex + runLength) * unitSamples)
                    return (payload, frameEnd)
                }
            }

            unitIndex += runLength
        }

        return nil
    }

    private func findLeaderStart() -> Int? {
        let leaderUnits = leaderToneUnits + leaderGapUnits
        let maxStart = rxBuffer.count - (leaderUnits * unitSamples)
        if maxStart <= 0 {
            return nil
        }

        var bestStart = -1
        var bestScore = Float.leastNormalMagnitude
        let coarseStep = max(16, unitSamples / 4)
        var candidate = 0
        while candidate <= maxStart {
            let score = leaderScore(at: candidate)
            if score > bestScore {
                bestScore = score
                bestStart = candidate
            }
            candidate += coarseStep
        }

        if bestStart < 0 {
            return nil
        }

        let refineFrom = max(0, bestStart - coarseStep)
        let refineTo = min(maxStart, bestStart + coarseStep)
        for refined in refineFrom...refineTo {
            let score = leaderScore(at: refined)
            if score > bestScore {
                bestScore = score
                bestStart = refined
            }
        }

        return bestScore >= Float(leaderToneUnits) * minActivityLevel ? bestStart : nil
    }

    private func leaderScore(at start: Int) -> Float {
        var score: Float = 0
        for unitIndex in 0..<leaderToneUnits {
            let activity = activityLevel(at: start + (unitIndex * unitSamples))
            if activity < minActivityLevel {
                return -.greatestFiniteMagnitude
            }
            score += activity
        }
        for unitIndex in 0..<leaderGapUnits {
            let activity = activityLevel(at: start + ((leaderToneUnits + unitIndex) * unitSamples))
            score -= activity * 0.5
        }
        return score
    }

    private func estimateThreshold(leaderStart: Int) -> Float {
        var leaderAcc: Float = 0
        var gapAcc: Float = 0
        for unitIndex in 0..<leaderToneUnits {
            leaderAcc += activityLevel(at: leaderStart + (unitIndex * unitSamples))
        }
        for unitIndex in 0..<leaderGapUnits {
            gapAcc += activityLevel(at: leaderStart + ((leaderToneUnits + unitIndex) * unitSamples))
        }
        let leaderAvg = leaderAcc / Float(leaderToneUnits)
        let gapAvg = gapAcc / Float(max(1, leaderGapUnits))
        return max(minActivityLevel, gapAvg + ((leaderAvg - gapAvg) * 0.25))
    }

    private func activityLevel(at windowStart: Int) -> Float {
        let windowEnd = windowStart + unitSamples
        if windowStart < 0 || windowEnd > rxBuffer.count {
            return 0
        }
        let rms = windowRms(start: windowStart)
        var iAcc: Float = 0
        var qAcc: Float = 0
        for sampleIndex in 0..<unitSamples {
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
        let end = start + unitSamples
        if start < 0 || end > rxBuffer.count {
            return 0
        }
        var energy: Float = 0
        for index in start..<end {
            let sample = rxBuffer[index]
            energy += sample * sample
        }
        return sqrt(energy / Float(unitSamples))
    }

    private func appendUnits(active: Bool, count: Int, into units: inout [Bool]) {
        for _ in 0..<count {
            units.append(active)
        }
    }

    private static func makeReference(
        freqHz: Int,
        sampleRateHz: Int,
        unitSamples: Int,
        gain: Float
    ) -> (sin: [Float], cos: [Float]) {
        let values = (0..<unitSamples).map { index -> (Float, Float) in
            let phase = (2.0 * Float.pi * Float(freqHz) * Float(index)) / Float(sampleRateHz)
            return (gain * Foundation.sin(phase), gain * Foundation.cos(phase))
        }
        return (values.map(\.0), values.map(\.1))
    }

    private static func decodeHexPayload(_ hex: String) -> Data? {
        guard hex.count >= 2, let length = Int(String(hex.prefix(2)), radix: 16) else {
            return nil
        }
        let payloadHex = String(hex.dropFirst(2))
        guard payloadHex.count >= length * 2 else {
            return nil
        }

        var bytes = [UInt8]()
        bytes.reserveCapacity(length)
        var cursor = payloadHex.startIndex
        for _ in 0..<length {
            let next = payloadHex.index(cursor, offsetBy: 2)
            guard let byte = UInt8(payloadHex[cursor..<next], radix: 16) else {
                return nil
            }
            bytes.append(byte)
            cursor = next
        }
        return Data(bytes)
    }
}
