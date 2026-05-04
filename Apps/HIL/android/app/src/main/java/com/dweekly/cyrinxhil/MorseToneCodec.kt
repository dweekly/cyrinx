package com.dweekly.cyrinxhil

import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sin
import kotlin.math.sqrt

class MorseToneCodec(config: SessionConfig) {
    private val sampleRateHz = config.sampleRateHz.coerceIn(8_000, 192_000)
    private val unitSamples = config.dcssSymbolSamples.coerceIn(1_200, 8_192)
    private val txGainCap = config.txGainCap.coerceIn(0.01f, 0.80f)
    private val toneHz = run {
        val requested = min(config.bandStartHz, config.bandEndHz)
        if (requested > 4_000) 1_200 else max(700, requested)
    }
    private val leaderToneUnits = 12
    private val leaderGapUnits = 6
    private val trailingGapUnits = 8
    private val minActivityLevel = 0.002f
    private val maxPayloadBytes = 32
    private val toneRef = makeReference(toneHz)
    private val rxBuffer = ArrayList<Float>()

    fun encode(payload: ByteArray): FloatArray {
        val trimmed = payload.copyOfRange(0, minOf(payload.size, maxPayloadBytes))
        val hex = buildString {
            append("%02X".format(trimmed.size))
            for (byte in trimmed) {
                append("%02X".format(byte.toInt() and 0xFF))
            }
        }

        val units = ArrayList<Boolean>((hex.length * 20) + leaderToneUnits + leaderGapUnits + trailingGapUnits)
        appendUnits(units, true, leaderToneUnits)
        appendUnits(units, false, leaderGapUnits)
        for (charIndex in hex.indices) {
            val pattern = CHAR_TO_MORSE[hex[charIndex]] ?: continue
            for (symbolIndex in pattern.indices) {
                appendUnits(units, true, if (pattern[symbolIndex] == '.') 1 else 3)
                if (symbolIndex + 1 < pattern.length) {
                    appendUnits(units, false, 1)
                }
            }
            if (charIndex + 1 < hex.length) {
                appendUnits(units, false, 3)
            }
        }
        appendUnits(units, false, trailingGapUnits)

        val out = FloatArray(units.size * unitSamples)
        var cursor = 0
        for (active in units) {
            if (active) {
                System.arraycopy(toneRef, 0, out, cursor, unitSamples)
            }
            cursor += unitSamples
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
        val minUnits = leaderToneUnits + leaderGapUnits + 20
        val minSamples = minUnits * unitSamples

        while (rxBuffer.size >= minSamples) {
            val leaderStart = findLeaderStart() ?: break
            val payloadStart = leaderStart + ((leaderToneUnits + leaderGapUnits) * unitSamples)
            val frame = decodeFrame(payloadStart, leaderStart)
            if (frame == null) {
                dropFront(leaderStart + unitSamples)
                continue
            }
            decoded.add(frame.first)
            dropFront(frame.second)
        }

        val keep = max(unitSamples * 24, minSamples * 2)
        if (rxBuffer.size > keep) {
            dropFront(rxBuffer.size - keep)
        }
        return decoded
    }

    private fun decodeFrame(start: Int, leaderStart: Int): Pair<ByteArray, Int>? {
        val threshold = estimateThreshold(leaderStart)
        val maxUnits = (rxBuffer.size - start) / unitSamples
        if (maxUnits <= 0) {
            return null
        }

        val states = BooleanArray(maxUnits)
        for (unitIndex in 0 until maxUnits) {
            states[unitIndex] = activityLevel(start + (unitIndex * unitSamples)) >= threshold
        }

        val decodedChars = ArrayList<Char>()
        val currentPattern = StringBuilder()
        var unitIndex = 0
        var expectedChars: Int? = null

        while (unitIndex < states.size) {
            val state = states[unitIndex]
            var runLength = 1
            while (unitIndex + runLength < states.size && states[unitIndex + runLength] == state) {
                runLength += 1
            }

            if (state) {
                currentPattern.append(if (runLength >= 3) '-' else '.')
            } else if (runLength >= 3) {
                if (currentPattern.isEmpty()) {
                    return null
                }
                val decoded = MORSE_TO_CHAR[currentPattern.toString()] ?: return null
                decodedChars.add(decoded)
                currentPattern.setLength(0)

                if (decodedChars.size == 2 && expectedChars == null) {
                    val prefix = decodedChars.joinToString("").take(2)
                    val length = prefix.toIntOrNull(16) ?: return null
                    if (length > maxPayloadBytes) {
                        return null
                    }
                    expectedChars = 2 + (length * 2)
                }
                val expected = expectedChars
                if (expected != null && decodedChars.size >= expected) {
                    val hex = decodedChars.joinToString("")
                    val payload = decodeHexPayload(hex) ?: return null
                    val frameEnd = start + ((unitIndex + runLength) * unitSamples)
                    return payload to frameEnd
                }
            }

            unitIndex += runLength
        }

        return null
    }

    private fun findLeaderStart(): Int? {
        val leaderUnits = leaderToneUnits + leaderGapUnits
        val maxStart = rxBuffer.size - (leaderUnits * unitSamples)
        if (maxStart <= 0) {
            return null
        }

        var bestStart = -1
        var bestScore = -Float.MAX_VALUE
        val coarseStep = max(16, unitSamples / 4)
        var candidate = 0
        while (candidate <= maxStart) {
            val score = leaderScore(candidate)
            if (score > bestScore) {
                bestScore = score
                bestStart = candidate
            }
            candidate += coarseStep
        }
        if (bestStart < 0) {
            return null
        }

        val refineFrom = max(0, bestStart - coarseStep)
        val refineTo = min(maxStart, bestStart + coarseStep)
        for (refined in refineFrom..refineTo) {
            val score = leaderScore(refined)
            if (score > bestScore) {
                bestScore = score
                bestStart = refined
            }
        }
        return if (bestScore >= (leaderToneUnits * minActivityLevel)) bestStart else null
    }

    private fun leaderScore(start: Int): Float {
        var score = 0f
        for (unitIndex in 0 until leaderToneUnits) {
            val activity = activityLevel(start + (unitIndex * unitSamples))
            if (activity < minActivityLevel) {
                return -Float.MAX_VALUE
            }
            score += activity
        }
        for (unitIndex in 0 until leaderGapUnits) {
            val activity = activityLevel(start + ((leaderToneUnits + unitIndex) * unitSamples))
            score -= activity * 0.5f
        }
        return score
    }

    private fun estimateThreshold(leaderStart: Int): Float {
        var leaderAcc = 0f
        var gapAcc = 0f
        for (unitIndex in 0 until leaderToneUnits) {
            leaderAcc += activityLevel(leaderStart + (unitIndex * unitSamples))
        }
        for (unitIndex in 0 until leaderGapUnits) {
            gapAcc += activityLevel(leaderStart + ((leaderToneUnits + unitIndex) * unitSamples))
        }
        val leaderAvg = leaderAcc / leaderToneUnits.toFloat()
        val gapAvg = gapAcc / max(1, leaderGapUnits).toFloat()
        return max(minActivityLevel, gapAvg + ((leaderAvg - gapAvg) * 0.25f))
    }

    private fun activityLevel(windowStart: Int): Float {
        val windowEnd = windowStart + unitSamples
        if (windowStart < 0 || windowEnd > rxBuffer.size) {
            return 0f
        }
        val rms = windowRms(windowStart)
        var iAcc = 0f
        var qAcc = 0f
        for (index in 0 until unitSamples) {
            val sample = rxBuffer[windowStart + index]
            iAcc += sample * toneRef[index]
            qAcc += sample * toneRef[index + unitSamples]
        }
        val tone = sqrt((iAcc * iAcc) + (qAcc * qAcc))
        return max(rms * 100.0f, tone)
    }

    private fun windowRms(start: Int): Float {
        val end = start + unitSamples
        if (start < 0 || end > rxBuffer.size) {
            return 0f
        }
        var energy = 0f
        for (index in start until end) {
            val sample = rxBuffer[index]
            energy += sample * sample
        }
        return sqrt(energy / unitSamples.toFloat())
    }

    private fun dropFront(count: Int) {
        if (count <= 0 || rxBuffer.isEmpty()) {
            return
        }
        rxBuffer.subList(0, minOf(count, rxBuffer.size)).clear()
    }

    private fun makeReference(freqHz: Int): FloatArray {
        val out = FloatArray(unitSamples * 2)
        for (index in 0 until unitSamples) {
            val phase = (2.0 * PI.toFloat() * freqHz.toFloat() * index.toFloat()) / sampleRateHz.toFloat()
            out[index] = txGainCap * sin(phase.toDouble()).toFloat()
            out[index + unitSamples] = txGainCap * cos(phase.toDouble()).toFloat()
        }
        return out
    }

    private fun appendUnits(units: MutableList<Boolean>, active: Boolean, count: Int) {
        repeat(count) { units.add(active) }
    }

    private fun decodeHexPayload(hex: String): ByteArray? {
        if (hex.length < 2) {
            return null
        }
        val length = hex.substring(0, 2).toIntOrNull(16) ?: return null
        val payloadHex = hex.substring(2)
        if (payloadHex.length < (length * 2)) {
            return null
        }
        val out = ByteArray(length)
        for (index in 0 until length) {
            val start = index * 2
            val end = start + 2
            val value = payloadHex.substring(start, end).toIntOrNull(16) ?: return null
            out[index] = (value and 0xFF).toByte()
        }
        return out
    }

    companion object {
        private val CHAR_TO_MORSE = mapOf(
            '0' to "-----", '1' to ".----", '2' to "..---", '3' to "...--", '4' to "....-",
            '5' to ".....", '6' to "-....", '7' to "--...", '8' to "---..", '9' to "----.",
            'A' to ".-", 'B' to "-...", 'C' to "-.-.", 'D' to "-..", 'E' to ".", 'F' to "..-."
        )
        private val MORSE_TO_CHAR = CHAR_TO_MORSE.entries.associate { (key, value) -> value to key }
    }
}
