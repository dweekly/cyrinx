package com.dweekly.cyrinxhil

import kotlin.math.PI
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sin
import kotlin.math.cos
import kotlin.math.sqrt

class NibbleToneCodec(config: SessionConfig) {
    private val sampleRateHz = config.sampleRateHz.coerceIn(8_000, 192_000)
    private val symbolSamples = config.dcssSymbolSamples.coerceIn(240, 4096)
    private val txGainCap = config.txGainCap.coerceIn(0.01f, 0.80f)
    private val nyquistGuardHz = max(1_000, (sampleRateHz / 2) - 500)
    private val usesConfiguredAudibleBand = config.bandStartHz >= 300 &&
        config.bandStartHz < 8_000 &&
        config.bandEndHz > config.bandStartHz
    private val lowHz = if (usesConfiguredAudibleBand) {
        config.bandStartHz.coerceIn(300, max(300, nyquistGuardHz - 450))
    } else {
        700
    }
    private val highHz = if (usesConfiguredAudibleBand) {
        max(lowHz + 450, min(nyquistGuardHz, config.bandEndHz))
    } else {
        min(3_400, nyquistGuardHz)
    }
    private val leaderSymbols = 4
    private val gapSymbols = 1
    private val minLeaderRms = 0.0025f
    private val repetitionCount = 3
    private val symbolsPerNibble = 2 * repetitionCount
    private val syncSymbols = intArrayOf(2, 2, 1, 1, 3, 0, 0, 3)
    private val toneRefs = Array(4) { index ->
        val span = max(15, highHz - lowHz)
        val freq = lowHz + ((span * index) / 3)
        makeReference(freq)
    }
    private val rxBuffer = ArrayList<Float>()

    fun encode(payload: ByteArray): FloatArray {
        val trimmed = payload.copyOfRange(0, minOf(payload.size, 255))
        val crcInput = ByteArray(trimmed.size + 1)
        crcInput[0] = trimmed.size.toByte()
        System.arraycopy(trimmed, 0, crcInput, 1, trimmed.size)
        val crc = crc8(crcInput, 0, crcInput.size)

        val nibbles = ArrayList<Int>(2 + (trimmed.size * 2))
        nibbles.add((trimmed.size ushr 4) and 0xF)
        nibbles.add(trimmed.size and 0xF)
        for (byte in trimmed) {
            nibbles.add((byte.toInt() ushr 4) and 0xF)
            nibbles.add(byte.toInt() and 0xF)
        }
        val symbols = ArrayList<Int>(syncSymbols.size + ((nibbles.size + 2) * symbolsPerNibble))
        for (symbol in syncSymbols) {
            symbols.add(symbol)
        }
        for (nibble in nibbles) {
            appendRepeatedNibble(nibble, symbols)
        }
        appendRepeatedNibble((crc ushr 4) and 0xF, symbols)
        appendRepeatedNibble(crc and 0xF, symbols)

        val totalSymbols = leaderSymbols + gapSymbols + symbols.size
        val out = FloatArray(totalSymbols * symbolSamples)
        var cursor = 0
        repeat(leaderSymbols) {
            System.arraycopy(toneRefs[0], 0, out, cursor, symbolSamples)
            cursor += symbolSamples
        }
        cursor += gapSymbols * symbolSamples
        for (symbol in symbols) {
            System.arraycopy(toneRefs[symbol], 0, out, cursor, symbolSamples)
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
        val minSymbols = syncSymbols.size + (4 * symbolsPerNibble)
        val minSamples = minSymbols * symbolSamples

        while (rxBuffer.size >= minSamples) {
            val start = findSyncStart() ?: break
            val lenHi = decodeRepeatedNibbleAt(start, syncSymbols.size)
            val lenLo = decodeRepeatedNibbleAt(start, syncSymbols.size + symbolsPerNibble)
            if (lenHi == null || lenLo == null) {
                dropFront(start + symbolSamples)
                continue
            }
            val length = ((lenHi shl 4) or lenLo) and 0xFF
            val totalFrameNibbles = 2 + (length * 2) + 2
            val totalSymbols = syncSymbols.size + (totalFrameNibbles * symbolsPerNibble)
            val frameEnd = start + (totalSymbols * symbolSamples)
            if (rxBuffer.size < frameEnd) {
                if (start > 0) {
                    dropFront(start)
                }
                break
            }

            val payload = ByteArray(length)
            var symbolIndex = syncSymbols.size + (2 * symbolsPerNibble)
            var valid = true
            for (i in 0 until length) {
                val hi = decodeRepeatedNibbleAt(start, symbolIndex)
                val lo = decodeRepeatedNibbleAt(start, symbolIndex + symbolsPerNibble)
                if (hi == null || lo == null) {
                    valid = false
                    break
                }
                payload[i] = (((hi shl 4) or lo) and 0xFF).toByte()
                symbolIndex += 2 * symbolsPerNibble
            }
            if (!valid) {
                dropFront(start + symbolSamples)
                continue
            }

            val crcHi = decodeRepeatedNibbleAt(start, symbolIndex)
            val crcLo = decodeRepeatedNibbleAt(start, symbolIndex + symbolsPerNibble)
            if (crcHi == null || crcLo == null) {
                dropFront(start + symbolSamples)
                continue
            }
            val actualCrc = ((crcHi shl 4) or crcLo) and 0xFF
            val crcInput = ByteArray(length + 1)
            crcInput[0] = length.toByte()
            System.arraycopy(payload, 0, crcInput, 1, payload.size)
            val expectedCrc = crc8(crcInput, 0, crcInput.size)
            if (expectedCrc != actualCrc) {
                dropFront(start + symbolSamples)
                continue
            }

            decoded.add(payload)
            dropFront(frameEnd)
        }

        val maxFrameNibbles = 2 + (255 * 2) + 2
        val maxFrameSymbols = syncSymbols.size + (maxFrameNibbles * symbolsPerNibble)
        val keep = max(symbolSamples * maxFrameSymbols, minSamples * 2)
        if (rxBuffer.size > keep) {
            dropFront(rxBuffer.size - keep)
        }
        return decoded
    }

    private fun findSyncStart(): Int? {
        val maxStart = rxBuffer.size - (syncSymbols.size * symbolSamples)
        if (maxStart <= 0) {
            return null
        }
        val coarseStep = max(4, symbolSamples / 24)
        var bestStart = -1
        var bestScore = Int.MIN_VALUE
        var bestMagnitude = -Float.MAX_VALUE
        var candidate = 0
        while (candidate <= maxStart) {
            if (windowRms(candidate) >= minLeaderRms) {
                val scored = scoreSyncWithMagnitude(candidate)
                if (scored.score > bestScore || (scored.score == bestScore && scored.magnitude > bestMagnitude)) {
                    bestScore = scored.score
                    bestMagnitude = scored.magnitude
                    bestStart = candidate
                }
            }
            candidate += coarseStep
        }
        if (bestStart >= 0) {
            val fineFrom = max(0, bestStart - coarseStep)
            val fineTo = min(maxStart, bestStart + coarseStep)
            for (fineCandidate in fineFrom..fineTo) {
                val scored = scoreSyncWithMagnitude(fineCandidate)
                if (scored.score > bestScore || (scored.score == bestScore && scored.magnitude > bestMagnitude)) {
                    bestScore = scored.score
                    bestMagnitude = scored.magnitude
                    bestStart = fineCandidate
                }
            }
        }
        return if (bestScore >= syncSymbols.size - 1) bestStart else null
    }

    private data class SyncScore(val score: Int, val magnitude: Float)

    private fun scoreSyncWithMagnitude(start: Int): SyncScore {
        var score = 0
        var magnitude = 0f
        for (index in syncSymbols.indices) {
            val windowStart = start + (index * symbolSamples)
            val decoded = decodeSymbolAtSample(windowStart) ?: return SyncScore(Int.MIN_VALUE, 0f)
            if (decoded.symbol == syncSymbols[index]) {
                score += 1
            }
            magnitude += decoded.magnitude
        }
        return SyncScore(score, magnitude)
    }

    private fun decodeRepeatedNibbleAt(start: Int, symbolIndex: Int): Int? {
        val high = decodeRepeatedSymbolAt(start, symbolIndex) ?: return null
        val low = decodeRepeatedSymbolAt(start, symbolIndex + repetitionCount) ?: return null
        return (((high and 0x3) shl 2) or (low and 0x3)) and 0xF
    }

    private fun decodeRepeatedSymbolAt(start: Int, symbolIndex: Int): Int? {
        val values = IntArray(repetitionCount)
        for (offset in 0 until repetitionCount) {
            values[offset] = decodeSymbolAt(start, symbolIndex + offset) ?: return null
        }
        return majoritySymbol(values)
    }

    private fun decodeSymbolAt(start: Int, symbolIndex: Int): Int? {
        return decodeSymbolAtSample(start + (symbolIndex * symbolSamples))?.symbol
    }

    private data class SymbolScore(val symbol: Int, val magnitude: Float)

    private fun decodeSymbolAtSample(windowStart: Int): SymbolScore? {
        val windowEnd = windowStart + symbolSamples
        if (windowStart < 0 || windowEnd > rxBuffer.size) {
            return null
        }
        var bestSymbol = 0
        var bestMagnitude = -Float.MAX_VALUE
        for (index in toneRefs.indices) {
            val ref = toneRefs[index]
            var iAcc = 0f
            var qAcc = 0f
            for (sampleIndex in 0 until symbolSamples) {
                val sample = rxBuffer[windowStart + sampleIndex]
                iAcc += sample * ref[sampleIndex]
                qAcc += sample * ref[sampleIndex + symbolSamples]
            }
            val magnitude = sqrt((iAcc * iAcc) + (qAcc * qAcc))
            if (magnitude > bestMagnitude) {
                bestMagnitude = magnitude
                bestSymbol = index
            }
        }
        return SymbolScore(bestSymbol and 0x3, bestMagnitude)
    }

    private fun appendRepeatedNibble(nibble: Int, symbols: MutableList<Int>) {
        val high = (nibble ushr 2) and 0x3
        val low = nibble and 0x3
        repeat(repetitionCount) { symbols.add(high) }
        repeat(repetitionCount) { symbols.add(low) }
    }

    private fun majoritySymbol(values: IntArray): Int? {
        if (values.isEmpty()) {
            return null
        }
        val counts = HashMap<Int, Int>()
        for (value in values) {
            counts[value] = (counts[value] ?: 0) + 1
        }
        return counts.entries.maxWithOrNull { lhs, rhs ->
            if (lhs.value == rhs.value) {
                rhs.key.compareTo(lhs.key)
            } else {
                lhs.value.compareTo(rhs.value)
            }
        }?.key
    }

    private fun makeReference(freqHz: Int): FloatArray {
        val out = FloatArray(symbolSamples * 2)
        for (index in 0 until symbolSamples) {
            val phase = (2.0 * PI.toFloat() * freqHz.toFloat() * index.toFloat()) / sampleRateHz.toFloat()
            out[index] = txGainCap * sin(phase.toDouble()).toFloat()
            out[index + symbolSamples] = txGainCap * cos(phase.toDouble()).toFloat()
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
}
