package com.dweekly.cyrinxhil

import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.max
import kotlin.math.sin
import kotlin.math.sqrt

private const val BASIC_TONE_SYNC_BYTE = 0x7E

private data class BasicToneConfig(
    val sampleRateHz: Int,
    val lowHz: Int,
    val highHz: Int,
    val symbolSamples: Int,
    val txGainCap: Float,
)

class BasicToneCodec(config: SessionConfig) {
    private val toneConfig = BasicToneConfig(
        sampleRateHz = config.sampleRateHz.coerceIn(8_000, 192_000),
        lowHz = minOf(config.bandStartHz, config.bandEndHz),
        highHz = maxOf(config.bandStartHz, config.bandEndHz),
        symbolSamples = config.dcssSymbolSamples.coerceIn(240, 4096),
        txGainCap = config.txGainCap.coerceIn(0.01f, 0.80f),
    )
    private val lowRef = makeReference(toneConfig.lowHz)
    private val highRef = makeReference(toneConfig.highHz)
    private val leaderSymbols = 6
    private val gapSymbols = 1
    private val minLeaderRms = 0.0025f
    private val preambleBits = IntArray(24) { idx -> if ((idx % 2) == 0) 1 else 0 } +
        byteToBits(BASIC_TONE_SYNC_BYTE)
    private val minPreambleScore = preambleBits.size - 4
    private val rxBuffer = ArrayList<Float>()

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

        val out = FloatArray(bits.size * toneConfig.symbolSamples)
        val totalSamples = (leaderSymbols + gapSymbols + bits.size) * toneConfig.symbolSamples
        val outWithLeader = FloatArray(totalSamples)
        var cursor = 0
        repeat(leaderSymbols) {
            System.arraycopy(lowRef, 0, outWithLeader, cursor, toneConfig.symbolSamples)
            cursor += toneConfig.symbolSamples
        }
        cursor += gapSymbols * toneConfig.symbolSamples
        for (bit in bits) {
            val ref = if (bit == 0) lowRef else highRef
            System.arraycopy(ref, 0, outWithLeader, cursor, toneConfig.symbolSamples)
            cursor += toneConfig.symbolSamples
        }
        return outWithLeader
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
        val minSamples = (leaderSymbols + gapSymbols + minBits) * toneConfig.symbolSamples

        while (rxBuffer.size >= minSamples) {
            val leaderStart = findLeaderStart() ?: break
            val start = leaderStart + ((leaderSymbols + gapSymbols) * toneConfig.symbolSamples)
            val payloadStartBit = preambleBits.size
            val length = decodeByteAt(start, payloadStartBit)
            if (length == null) {
                dropFront(leaderStart + toneConfig.symbolSamples)
                continue
            }
            if (scorePreambleAt(start) < minPreambleScore) {
                dropFront(leaderStart + toneConfig.symbolSamples)
                continue
            }
            val totalFrameBytes = length + 2
            val totalBits = preambleBits.size + (totalFrameBytes * 8)
            val frameEnd = start + (totalBits * toneConfig.symbolSamples)
            if (rxBuffer.size < frameEnd) {
                if (leaderStart > 0) {
                    dropFront(leaderStart)
                }
                break
            }

            val frame = ByteArray(totalFrameBytes)
            var valid = true
            for (index in 0 until totalFrameBytes) {
                val decodedByte = decodeByteAt(start, payloadStartBit + (index * 8))
                if (decodedByte == null) {
                    valid = false
                    break
                }
                frame[index] = decodedByte.toByte()
            }
            if (!valid) {
                dropFront(leaderStart + toneConfig.symbolSamples)
                continue
            }

            val expectedCrc = crc8(frame, 0, frame.size - 1)
            val actualCrc = frame.last().toInt() and 0xFF
            if (expectedCrc != actualCrc) {
                dropFront(leaderStart + toneConfig.symbolSamples)
                continue
            }

            decoded.add(frame.copyOfRange(1, frame.lastIndex))
            dropFront(frameEnd)
        }

        val keep = max(toneConfig.symbolSamples * 16, minSamples * 2)
        if (rxBuffer.size > keep) {
            dropFront(rxBuffer.size - keep)
        }
        return decoded
    }

    private fun findLeaderStart(): Int? {
        val maxStart = rxBuffer.size - ((leaderSymbols + gapSymbols + preambleBits.size) * toneConfig.symbolSamples)
        if (maxStart <= 0) {
            return null
        }

        var bestStart = -1
        var bestScore = Int.MIN_VALUE
        val coarseStep = max(8, toneConfig.symbolSamples / 6)
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
        val refineTo = minOf(maxStart, bestStart + coarseStep)
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
            val windowStart = start + (symbolIndex * toneConfig.symbolSamples)
            if (windowRms(windowStart) < minLeaderRms) {
                return Int.MIN_VALUE
            }
            val decodedBit = decodeBitAt(start, symbolIndex) ?: return Int.MIN_VALUE
            if (decodedBit == 0) {
                score += 1
            }
        }
        return score
    }

    private fun scorePreambleAt(start: Int): Int {
        var score = 0
        for (bitIndex in preambleBits.indices) {
            val decodedBit = decodeBitAt(start, bitIndex) ?: return Int.MIN_VALUE
            if (decodedBit == preambleBits[bitIndex]) {
                score += 1
            }
        }
        return score
    }

    private fun decodeByteAt(startSample: Int, startBit: Int): Int? {
        var value = 0
        for (bitOffset in 0 until 8) {
            val bit = decodeBitAt(startSample, startBit + bitOffset) ?: return null
            value = (value shl 1) or bit
        }
        return value and 0xFF
    }

    private fun decodeBitAt(startSample: Int, bitIndex: Int): Int? {
        val windowStart = startSample + (bitIndex * toneConfig.symbolSamples)
        val windowEnd = windowStart + toneConfig.symbolSamples
        if (windowStart < 0 || windowEnd > rxBuffer.size) {
            return null
        }

        var lowI = 0f
        var lowQ = 0f
        var highI = 0f
        var highQ = 0f
        for (idx in 0 until toneConfig.symbolSamples) {
            val sample = rxBuffer[windowStart + idx]
            lowI += sample * lowRef[idx]
            lowQ += sample * lowRef[idx + toneConfig.symbolSamples]
            highI += sample * highRef[idx]
            highQ += sample * highRef[idx + toneConfig.symbolSamples]
        }
        val lowMag = sqrt((lowI * lowI) + (lowQ * lowQ))
        val highMag = sqrt((highI * highI) + (highQ * highQ))
        return if (highMag > lowMag) 1 else 0
    }

    private fun makeReference(freqHz: Int): FloatArray {
        val out = FloatArray(toneConfig.symbolSamples * 2)
        val gain = toneConfig.txGainCap
        for (idx in 0 until toneConfig.symbolSamples) {
            val phase = (2.0 * PI.toFloat() * freqHz.toFloat() * idx.toFloat()) / toneConfig.sampleRateHz.toFloat()
            out[idx] = gain * sin(phase.toDouble()).toFloat()
            out[idx + toneConfig.symbolSamples] = gain * cos(phase.toDouble()).toFloat()
        }
        return out
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

    private fun dropFront(count: Int) {
        if (count <= 0 || rxBuffer.isEmpty()) {
            return
        }
        val clamped = minOf(count, rxBuffer.size)
        rxBuffer.subList(0, clamped).clear()
    }

    private fun windowRms(start: Int): Float {
        val end = start + toneConfig.symbolSamples
        if (start < 0 || end > rxBuffer.size) {
            return 0f
        }
        var energy = 0f
        for (index in start until end) {
            val sample = rxBuffer[index]
            energy += sample * sample
        }
        return sqrt(energy / toneConfig.symbolSamples.toFloat())
    }
}
