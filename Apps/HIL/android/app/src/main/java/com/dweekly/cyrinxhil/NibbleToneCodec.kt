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
    private val lowHz = 700
    private val highHz = min(3_400, (sampleRateHz / 2) - 500)
    private val leaderSymbols = 4
    private val gapSymbols = 1
    private val minLeaderRms = 0.0025f
    private val syncNibbles = intArrayOf(0xA, 0x5, 0xC, 0x3)
    private val toneRefs = Array(16) { index ->
        val span = max(15, highHz - lowHz)
        val freq = lowHz + ((span * index) / 15)
        makeReference(freq)
    }
    private val rxBuffer = ArrayList<Float>()

    fun encode(payload: ByteArray): FloatArray {
        val trimmed = payload.copyOfRange(0, minOf(payload.size, 255))
        val crcInput = ByteArray(trimmed.size + 1)
        crcInput[0] = trimmed.size.toByte()
        System.arraycopy(trimmed, 0, crcInput, 1, trimmed.size)
        val crc = crc8(crcInput, 0, crcInput.size)

        val nibbles = ArrayList<Int>(syncNibbles.size + 2 + (trimmed.size * 2) + 2)
        for (nibble in syncNibbles) {
            nibbles.add(nibble)
        }
        nibbles.add((trimmed.size ushr 4) and 0xF)
        nibbles.add(trimmed.size and 0xF)
        for (byte in trimmed) {
            nibbles.add((byte.toInt() ushr 4) and 0xF)
            nibbles.add(byte.toInt() and 0xF)
        }
        nibbles.add((crc ushr 4) and 0xF)
        nibbles.add(crc and 0xF)

        val totalSymbols = leaderSymbols + gapSymbols + nibbles.size
        val out = FloatArray(totalSymbols * symbolSamples)
        var cursor = 0
        repeat(leaderSymbols) {
            System.arraycopy(toneRefs[0], 0, out, cursor, symbolSamples)
            cursor += symbolSamples
        }
        cursor += gapSymbols * symbolSamples
        for (nibble in nibbles) {
            System.arraycopy(toneRefs[nibble], 0, out, cursor, symbolSamples)
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
        val minSymbols = leaderSymbols + gapSymbols + syncNibbles.size + 4
        val minSamples = minSymbols * symbolSamples

        while (rxBuffer.size >= minSamples) {
            val leaderStart = findLeaderStart() ?: break
            val nominalStart = leaderStart + ((leaderSymbols + gapSymbols) * symbolSamples)
            val start = refineSyncStart(nominalStart)
            if (start == null) {
                dropFront(leaderStart + symbolSamples)
                continue
            }
            val lenHi = decodeNibbleAt(start, syncNibbles.size)
            val lenLo = decodeNibbleAt(start, syncNibbles.size + 1)
            if (lenHi == null || lenLo == null) {
                dropFront(leaderStart + symbolSamples)
                continue
            }
            val length = ((lenHi shl 4) or lenLo) and 0xFF
            val totalNibbles = syncNibbles.size + 2 + (length * 2) + 2
            val frameEnd = start + (totalNibbles * symbolSamples)
            if (rxBuffer.size < frameEnd) {
                if (leaderStart > 0) {
                    dropFront(leaderStart)
                }
                break
            }

            val payload = ByteArray(length)
            var nibbleIndex = syncNibbles.size + 2
            var valid = true
            for (i in 0 until length) {
                val hi = decodeNibbleAt(start, nibbleIndex)
                val lo = decodeNibbleAt(start, nibbleIndex + 1)
                if (hi == null || lo == null) {
                    valid = false
                    break
                }
                payload[i] = (((hi shl 4) or lo) and 0xFF).toByte()
                nibbleIndex += 2
            }
            if (!valid) {
                dropFront(leaderStart + symbolSamples)
                continue
            }

            val crcHi = decodeNibbleAt(start, nibbleIndex)
            val crcLo = decodeNibbleAt(start, nibbleIndex + 1)
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
        for (candidate in refineFrom..refineTo) {
            val score = scoreLeaderAt(candidate)
            if (score > bestScore) {
                bestScore = score
                bestStart = candidate
            }
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
        for (candidate in from..to) {
            val score = scoreSyncAt(candidate)
            if (score > bestScore) {
                bestScore = score
                bestStart = candidate
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

    private fun decodeNibbleAtSample(windowStart: Int): Int? {
        val windowEnd = windowStart + symbolSamples
        if (windowStart < 0 || windowEnd > rxBuffer.size) {
            return null
        }
        var bestNibble = 0
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
                bestNibble = index
            }
        }
        return bestNibble and 0xF
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
