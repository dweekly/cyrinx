package com.dweekly.cyrinxhil

import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sin
import kotlin.math.sqrt

class DTMFCodec(config: SessionConfig) {
    private val sampleRateHz = config.sampleRateHz.coerceIn(8_000, 192_000)
    private val symbolSamples = config.dcssSymbolSamples.coerceIn(960, 8192)
    private val txGainCap = config.txGainCap.coerceIn(0.01f, 0.80f)
    private val leaderSymbols = 6
    private val gapSymbols = 2
    private val minLeaderRms = 0.0025f
    private val repetitionCount = 3
    private val syncNibbles = intArrayOf(0x1, 0x2, 0x3, 0x4, 0xB, 0xC, 0xD, 0xE)
    private val rowHz = intArrayOf(697, 770, 852, 941)
    private val colHz = intArrayOf(1209, 1336, 1477, 1633)
    private val rowRefs = rowHz.map { makeReference(it, txGainCap * 0.5f) }
    private val colRefs = colHz.map { makeReference(it, txGainCap * 0.5f) }
    private val rxBuffer = ArrayList<Float>()

    fun encode(payload: ByteArray): FloatArray {
        val trimmed = payload.copyOfRange(0, minOf(payload.size, 255))
        val crcInput = ByteArray(trimmed.size + 1)
        crcInput[0] = trimmed.size.toByte()
        System.arraycopy(trimmed, 0, crcInput, 1, trimmed.size)
        val crc = crc8(crcInput, 0, crcInput.size)

        val nibbles = ArrayList<Int>(syncNibbles.size + (4 * repetitionCount) + (trimmed.size * 2 * repetitionCount))
        for (nibble in syncNibbles) {
            nibbles.add(nibble)
        }
        repeat(repetitionCount) { nibbles.add((trimmed.size ushr 4) and 0xF) }
        repeat(repetitionCount) { nibbles.add(trimmed.size and 0xF) }
        for (byte in trimmed) {
            repeat(repetitionCount) { nibbles.add((byte.toInt() ushr 4) and 0xF) }
            repeat(repetitionCount) { nibbles.add(byte.toInt() and 0xF) }
        }
        repeat(repetitionCount) { nibbles.add((crc ushr 4) and 0xF) }
        repeat(repetitionCount) { nibbles.add(crc and 0xF) }

        val totalSymbols = leaderSymbols + gapSymbols + nibbles.size
        val out = FloatArray(totalSymbols * symbolSamples)
        var cursor = 0
        repeat(leaderSymbols) {
            val symbol = synthesizeSymbol(0)
            System.arraycopy(symbol, 0, out, cursor, symbolSamples)
            cursor += symbolSamples
        }
        cursor += gapSymbols * symbolSamples
        for (nibble in nibbles) {
            val symbol = synthesizeSymbol(nibble)
            System.arraycopy(symbol, 0, out, cursor, symbolSamples)
            cursor += symbolSamples
        }
        return out
    }

    fun ingest(samples: FloatArray): List<ByteArray> {
        if (samples.isEmpty()) {
            return emptyList()
        }
        for (sample in samples) {
            rxBuffer.add(sample)
        }
        return decodeAvailable()
    }

    private fun decodeAvailable(): List<ByteArray> {
        val decoded = ArrayList<ByteArray>()
        val minSymbols = leaderSymbols + gapSymbols + syncNibbles.size + (4 * repetitionCount)
        val minSamples = minSymbols * symbolSamples

        while (rxBuffer.size >= minSamples) {
            val leaderStart = findLeaderStart() ?: break
            val nominalStart = leaderStart + ((leaderSymbols + gapSymbols) * symbolSamples)
            val start = refineSyncStart(nominalStart)
            if (start == null) {
                dropFront(leaderStart + symbolSamples)
                continue
            }
            val lenHi = decodeRepeatedNibbleAt(start, syncNibbles.size)
            val lenLo = decodeRepeatedNibbleAt(start, syncNibbles.size + repetitionCount)
            if (lenHi == null || lenLo == null) {
                dropFront(leaderStart + symbolSamples)
                continue
            }
            val length = ((lenHi shl 4) or lenLo) and 0xFF
            val totalNibbles = syncNibbles.size + (2 * repetitionCount) + (length * 2 * repetitionCount) + (2 * repetitionCount)
            val frameEnd = start + (totalNibbles * symbolSamples)
            if (rxBuffer.size < frameEnd) {
                if (leaderStart > 0) {
                    dropFront(leaderStart)
                }
                break
            }

            val payload = ByteArray(length)
            var nibbleIndex = syncNibbles.size + (2 * repetitionCount)
            var valid = true
            for (i in 0 until length) {
                val hi = decodeRepeatedNibbleAt(start, nibbleIndex)
                val lo = decodeRepeatedNibbleAt(start, nibbleIndex + repetitionCount)
                if (hi == null || lo == null) {
                    valid = false
                    break
                }
                payload[i] = (((hi shl 4) or lo) and 0xFF).toByte()
                nibbleIndex += 2 * repetitionCount
            }
            if (!valid) {
                dropFront(leaderStart + symbolSamples)
                continue
            }

            val crcHi = decodeRepeatedNibbleAt(start, nibbleIndex)
            val crcLo = decodeRepeatedNibbleAt(start, nibbleIndex + repetitionCount)
            if (crcHi == null || crcLo == null) {
                dropFront(leaderStart + symbolSamples)
                continue
            }
            val actualCrc = ((crcHi shl 4) or crcLo) and 0xFF
            val crcInput = ByteArray(length + 1)
            crcInput[0] = length.toByte()
            System.arraycopy(payload, 0, crcInput, 1, payload.size)
            val expectedCrc = crc8(crcInput, 0, crcInput.size)
            if (expectedCrc != actualCrc) {
                dropFront(leaderStart + symbolSamples)
                continue
            }

            decoded.add(payload)
            dropFront(frameEnd)
        }

        val keep = max(symbolSamples * 16, minSamples * 2)
        if (rxBuffer.size > keep) {
            dropFront(rxBuffer.size - keep)
        }
        return decoded
    }

    private fun synthesizeSymbol(nibble: Int): FloatArray {
        val row = (nibble ushr 2) and 0x3
        val col = nibble and 0x3
        val out = FloatArray(symbolSamples)
        val rowRef = rowRefs[row]
        val colRef = colRefs[col]
        for (i in 0 until symbolSamples) {
            out[i] = rowRef[i] + colRef[i]
        }
        return out
    }

    private fun findLeaderStart(): Int? {
        val maxStart = rxBuffer.size - ((leaderSymbols + gapSymbols + syncNibbles.size) * symbolSamples)
        if (maxStart <= 0) {
            return null
        }
        var bestStart = -1
        var bestScore = Int.MIN_VALUE
        val coarseStep = max(8, symbolSamples / 6)
        var coarse = 0
        while (coarse <= maxStart) {
            val score = scoreLeaderAt(coarse)
            if (score > bestScore) {
                bestScore = score
                bestStart = coarse
            }
            coarse += coarseStep
        }
        if (bestStart < 0) {
            return null
        }
        val refineFrom = max(0, bestStart - coarseStep)
        val refineTo = min(maxStart, bestStart + coarseStep)
        val refineStep = max(1, coarseStep / 32)
        var candidate = refineFrom
        while (candidate <= refineTo) {
            val score = scoreLeaderAt(candidate)
            if (score > bestScore) {
                bestScore = score
                bestStart = candidate
            }
            candidate += refineStep
        }
        return if (bestScore >= leaderSymbols) bestStart else null
    }

    private fun scoreLeaderAt(start: Int): Int {
        var score = 0
        for (symbolIndex in 0 until leaderSymbols) {
            val windowStart = start + (symbolIndex * symbolSamples)
            if (windowRms(windowStart) < minLeaderRms) {
                return Int.MIN_VALUE
            }
            val nibble = decodeNibbleAtSample(windowStart) ?: return Int.MIN_VALUE
            if (nibble == 0) {
                score += 1
            }
        }
        return score
    }

    private fun refineSyncStart(nominalStart: Int): Int? {
        val maxStart = rxBuffer.size - (syncNibbles.size * symbolSamples)
        if (maxStart <= 0) {
            return null
        }
        val searchRadius = symbolSamples
        val from = max(0, nominalStart - searchRadius)
        val to = min(maxStart, nominalStart + searchRadius)
        var bestStart = -1
        var bestScore = Int.MIN_VALUE
        val coarseStep = max(4, symbolSamples / 24)
        var candidate = from
        while (candidate <= to) {
            val score = scoreSyncAt(candidate)
            if (score > bestScore) {
                bestScore = score
                bestStart = candidate
            }
            candidate += coarseStep
        }
        if (bestStart >= 0) {
            val fineFrom = max(from, bestStart - coarseStep)
            val fineTo = min(to, bestStart + coarseStep)
            for (fineCandidate in fineFrom..fineTo) {
                val score = scoreSyncAt(fineCandidate)
                if (score > bestScore) {
                    bestScore = score
                    bestStart = fineCandidate
                }
            }
        }
        return if (bestScore >= syncNibbles.size - 1) bestStart else null
    }

    private fun scoreSyncAt(start: Int): Int {
        var score = 0
        for (index in syncNibbles.indices) {
            val nibble = decodeNibbleAt(start, index) ?: return Int.MIN_VALUE
            if (nibble == syncNibbles[index]) {
                score += 1
            }
        }
        return score
    }

    private fun decodeNibbleAt(start: Int, nibbleIndex: Int): Int? {
        return decodeNibbleAtSample(start + (nibbleIndex * symbolSamples))
    }

    private fun decodeRepeatedNibbleAt(start: Int, nibbleIndex: Int): Int? {
        val values = IntArray(repetitionCount)
        for (offset in 0 until repetitionCount) {
            values[offset] = decodeNibbleAt(start, nibbleIndex + offset) ?: return null
        }
        return majorityNibble(values)
    }

    private fun decodeNibbleAtSample(windowStart: Int): Int? {
        val windowEnd = windowStart + symbolSamples
        if (windowStart < 0 || windowEnd > rxBuffer.size) {
            return null
        }

        val rowMagnitudes = FloatArray(4)
        val colMagnitudes = FloatArray(4)
        for (index in 0 until 4) {
            var rowI = 0f
            var rowQ = 0f
            var colI = 0f
            var colQ = 0f
            val rowRef = rowRefs[index]
            val colRef = colRefs[index]
            for (sampleIndex in 0 until symbolSamples) {
                val sample = rxBuffer[windowStart + sampleIndex]
                rowI += sample * rowRef[sampleIndex]
                rowQ += sample * rowRef[sampleIndex + symbolSamples]
                colI += sample * colRef[sampleIndex]
                colQ += sample * colRef[sampleIndex + symbolSamples]
            }
            rowMagnitudes[index] = sqrt((rowI * rowI) + (rowQ * rowQ))
            colMagnitudes[index] = sqrt((colI * colI) + (colQ * colQ))
        }
        val row = rowMagnitudes.indices.maxByOrNull { rowMagnitudes[it] } ?: return null
        val col = colMagnitudes.indices.maxByOrNull { colMagnitudes[it] } ?: return null
        return ((row shl 2) or col) and 0xF
    }

    private fun makeReference(freqHz: Int, gain: Float): FloatArray {
        val out = FloatArray(symbolSamples * 2)
        for (index in 0 until symbolSamples) {
            val phase = (2.0 * PI.toFloat() * freqHz.toFloat() * index.toFloat()) / sampleRateHz.toFloat()
            out[index] = gain * sin(phase.toDouble()).toFloat()
            out[index + symbolSamples] = gain * cos(phase.toDouble()).toFloat()
        }
        return out
    }

    private fun windowRms(start: Int): Float {
        val end = start + symbolSamples
        if (start < 0 || end > rxBuffer.size) {
            return 0f
        }
        var energy = 0f
        for (index in start until end) {
            val sample = rxBuffer[index]
            energy += sample * sample
        }
        return sqrt(energy / symbolSamples.toFloat())
    }

    private fun dropFront(count: Int) {
        if (count <= 0 || rxBuffer.isEmpty()) {
            return
        }
        rxBuffer.subList(0, minOf(count, rxBuffer.size)).clear()
    }

    private fun crc8(bytes: ByteArray, start: Int, length: Int): Int {
        var crc = 0
        for (index in 0 until length) {
            crc = crc xor (bytes[start + index].toInt() and 0xFF)
        }
        return crc and 0xFF
    }

    private fun majorityNibble(values: IntArray): Int? {
        if (values.isEmpty()) {
            return null
        }
        val counts = HashMap<Int, Int>()
        for (value in values) {
            counts[value] = (counts[value] ?: 0) + 1
        }
        return counts.maxWithOrNull(
            compareBy<Map.Entry<Int, Int>> { it.value }
                .thenByDescending { it.key }
        )?.key
    }
}
