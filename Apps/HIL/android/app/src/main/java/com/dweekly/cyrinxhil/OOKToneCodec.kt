package com.dweekly.cyrinxhil

import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sin
import kotlin.math.sqrt

class OOKToneCodec(config: SessionConfig) {
    private val sampleRateHz = config.sampleRateHz.coerceIn(8_000, 192_000)
    private val bitSamples = config.dcssSymbolSamples.coerceIn(480, 16_384)
    private val halfSamples = max(240, bitSamples / 2)
    private val txGainCap = config.txGainCap.coerceIn(0.01f, 0.80f)
    private val leaderToneHalves = 8
    private val leaderGapHalves = 4
    private val minActivityLevel = 0.003f
    private val preambleBits = IntArray(24) { idx -> if ((idx % 2) == 0) 1 else 0 } + byteToBits(0x7E)
    private val minPreambleScore = preambleBits.size - 4
    private val toneRef: FloatArray
    private val rxBuffer = ArrayList<Float>()

    init {
        val toneHz = min(max(config.bandStartHz, 700), config.bandEndHz)
        toneRef = makeReference(toneHz)
    }

    fun encode(payload: ByteArray): FloatArray {
        val trimmed = payload.copyOfRange(0, minOf(payload.size, 255))
        val frame = ByteArray(trimmed.size + 2)
        frame[0] = trimmed.size.toByte()
        System.arraycopy(trimmed, 0, frame, 1, trimmed.size)
        frame[frame.lastIndex] = crc8(frame, 0, frame.lastIndex).toByte()

        val bits = ArrayList<Int>(preambleBits.size + (frame.size * 8))
        for (bit in preambleBits) {
            bits.add(bit)
        }
        for (byte in frame) {
            for (bit in byteToBits(byte.toInt() and 0xFF)) {
                bits.add(bit)
            }
        }

        val totalHalfSymbols = leaderToneHalves + leaderGapHalves + (bits.size * 2)
        val out = FloatArray(totalHalfSymbols * halfSamples)
        var cursor = 0
        repeat(leaderToneHalves) {
            System.arraycopy(toneRef, 0, out, cursor, halfSamples)
            cursor += halfSamples
        }
        cursor += leaderGapHalves * halfSamples
        for (bit in bits) {
            if (bit == 0) {
                System.arraycopy(toneRef, 0, out, cursor, halfSamples)
            }
            cursor += halfSamples
            if (bit == 1) {
                System.arraycopy(toneRef, 0, out, cursor, halfSamples)
            }
            cursor += halfSamples
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
        val minBits = preambleBits.size + 16
        val minSamples = (leaderToneHalves + leaderGapHalves + (minBits * 2)) * halfSamples

        while (rxBuffer.size >= minSamples) {
            val leaderStart = findLeaderStart() ?: break
            val nominalStart = leaderStart + ((leaderToneHalves + leaderGapHalves) * halfSamples)
            val start = refinePayloadStart(nominalStart)
            if (start == null) {
                dropFront(leaderStart + halfSamples)
                continue
            }
            val payloadStartBit = preambleBits.size
            val length = decodeByteAt(start, payloadStartBit)
            if (length == null) {
                dropFront(leaderStart + halfSamples)
                continue
            }
            val totalFrameBytes = length + 2
            val totalBits = preambleBits.size + (totalFrameBytes * 8)
            val frameEnd = start + (totalBits * bitSamples)
            if (rxBuffer.size < frameEnd) {
                if (leaderStart > 0) {
                    dropFront(leaderStart)
                }
                break
            }

            val frame = ByteArray(totalFrameBytes)
            var valid = true
            for (index in 0 until totalFrameBytes) {
                val byte = decodeByteAt(start, payloadStartBit + (index * 8))
                if (byte == null) {
                    valid = false
                    break
                }
                frame[index] = byte.toByte()
            }
            if (!valid) {
                dropFront(leaderStart + halfSamples)
                continue
            }

            val expectedCrc = crc8(frame, 0, frame.size - 1)
            val actualCrc = frame.last().toInt() and 0xFF
            if (expectedCrc != actualCrc) {
                dropFront(leaderStart + halfSamples)
                continue
            }

            decoded.add(frame.copyOfRange(1, frame.lastIndex))
            dropFront(frameEnd)
        }

        val keep = max(bitSamples * 12, minSamples * 2)
        if (rxBuffer.size > keep) {
            dropFront(rxBuffer.size - keep)
        }
        return decoded
    }

    private fun findLeaderStart(): Int? {
        val maxStart = rxBuffer.size - ((leaderToneHalves + leaderGapHalves + (preambleBits.size * 2)) * halfSamples)
        if (maxStart <= 0) {
            return null
        }
        var bestStart = -1
        var bestScore = Int.MIN_VALUE
        val coarseStep = max(8, halfSamples / 4)
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
        return if (bestScore >= leaderToneHalves) bestStart else null
    }

    private fun scoreLeaderAt(start: Int): Int {
        var score = 0
        for (halfIndex in 0 until leaderToneHalves) {
            if (activityLevel(start + (halfIndex * halfSamples)) < minActivityLevel) {
                return Int.MIN_VALUE
            }
            score += 1
        }
        return score
    }

    private fun refinePayloadStart(nominalStart: Int): Int? {
        val maxStart = rxBuffer.size - (preambleBits.size * bitSamples)
        if (maxStart <= 0) {
            return null
        }
        val searchRadius = halfSamples
        val from = max(0, nominalStart - searchRadius)
        val to = min(maxStart, nominalStart + searchRadius)
        var bestStart = -1
        var bestScore = Int.MIN_VALUE
        for (candidate in from..to) {
            val score = scorePreambleAt(candidate)
            if (score > bestScore) {
                bestScore = score
                bestStart = candidate
            }
        }
        return if (bestScore >= minPreambleScore) bestStart else null
    }

    private fun scorePreambleAt(start: Int): Int {
        var score = 0
        for (index in preambleBits.indices) {
            val bit = decodeBitAt(start, index) ?: return Int.MIN_VALUE
            if (bit == preambleBits[index]) {
                score += 1
            }
        }
        return score
    }

    private fun decodeByteAt(start: Int, startBit: Int): Int? {
        var value = 0
        for (bitOffset in 0 until 8) {
            val bit = decodeBitAt(start, startBit + bitOffset) ?: return null
            value = (value shl 1) or bit
        }
        return value and 0xFF
    }

    private fun decodeBitAt(start: Int, bitIndex: Int): Int? {
        val firstHalf = start + (bitIndex * bitSamples)
        val secondHalf = firstHalf + halfSamples
        val first = activityLevel(firstHalf)
        val second = activityLevel(secondHalf)
        if (max(first, second) < (minActivityLevel * 0.5f)) {
            return null
        }
        return if (first > second) 0 else 1
    }

    private fun activityLevel(windowStart: Int): Float {
        val windowEnd = windowStart + halfSamples
        if (windowStart < 0 || windowEnd > rxBuffer.size) {
            return 0f
        }
        val rms = windowRms(windowStart)
        var iAcc = 0f
        var qAcc = 0f
        for (index in 0 until halfSamples) {
            val sample = rxBuffer[windowStart + index]
            iAcc += sample * toneRef[index]
            qAcc += sample * toneRef[index + halfSamples]
        }
        val tone = sqrt((iAcc * iAcc) + (qAcc * qAcc))
        return max(rms * 100.0f, tone)
    }

    private fun makeReference(freqHz: Int): FloatArray {
        val out = FloatArray(halfSamples * 2)
        for (index in 0 until halfSamples) {
            val phase = (2.0 * PI.toFloat() * freqHz.toFloat() * index.toFloat()) / sampleRateHz.toFloat()
            out[index] = txGainCap * sin(phase.toDouble()).toFloat()
            out[index + halfSamples] = txGainCap * cos(phase.toDouble()).toFloat()
        }
        return out
    }

    private fun dropFront(count: Int) {
        if (count <= 0 || rxBuffer.isEmpty()) {
            return
        }
        rxBuffer.subList(0, minOf(count, rxBuffer.size)).clear()
    }

    private fun windowRms(start: Int): Float {
        val end = start + halfSamples
        if (start < 0 || end > rxBuffer.size) {
            return 0f
        }
        var energy = 0f
        for (index in start until end) {
            val sample = rxBuffer[index]
            energy += sample * sample
        }
        return sqrt(energy / halfSamples.toFloat())
    }

    private fun byteToBits(value: Int): IntArray {
        return IntArray(8) { bitIndex -> (value ushr (7 - bitIndex)) and 0x1 }
    }

    private fun crc8(bytes: ByteArray, start: Int, length: Int): Int {
        var crc = 0
        for (index in 0 until length) {
            crc = crc xor (bytes[start + index].toInt() and 0xFF)
        }
        return crc and 0xFF
    }
}
