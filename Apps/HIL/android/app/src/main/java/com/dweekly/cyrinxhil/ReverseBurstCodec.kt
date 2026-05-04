package com.dweekly.cyrinxhil

import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.max
import kotlin.math.sin
import kotlin.math.sqrt

class ReverseBurstCodec(config: SessionConfig) {
    private val sampleRateHz = config.sampleRateHz.coerceIn(8_000, 192_000)
    private val lowHz = minOf(config.bandStartHz, config.bandEndHz)
    private val highHz = maxOf(config.bandStartHz, config.bandEndHz)
    private val symbolSamples = config.dcssSymbolSamples.coerceIn(240, 4096)
    private val txGainCap = config.txGainCap.coerceIn(0.01f, 0.80f)
    private val lowRef = makeReference(lowHz)
    private val highRef = makeReference(highHz)
    private val leaderSymbols = 6
    private val gapSymbols = 1
    private val minLeaderRms = 0.0025f
    private val symbolSearchRadius = max(8, symbolSamples / 8)
    private val symbolSearchStep = max(1, symbolSamples / 48)
    private val guardBytes = intArrayOf(0x55, 0x2D)
    private val fixedPayloadBytes = 5
    private val repetitionCount = 5
    private val preambleBits = IntArray(24) { idx -> if ((idx % 2) == 0) 1 else 0 } + byteToBits(0x7E)
    private val minPreambleScore = preambleBits.size - 4
    private val rxBuffer = ArrayList<Float>()

    fun encode(payload: ByteArray): FloatArray {
        val trimmed = payload.copyOfRange(0, minOf(payload.size, fixedPayloadBytes))
        val padded = ByteArray(fixedPayloadBytes)
        System.arraycopy(trimmed, 0, padded, 0, trimmed.size)
        val length = trimmed.size and 0xFF
        val crcInput = ByteArray(fixedPayloadBytes + 1)
        System.arraycopy(padded, 0, crcInput, 0, fixedPayloadBytes)
        crcInput[fixedPayloadBytes] = length.toByte()
        val frame = ArrayList<Byte>(guardBytes.size + ((fixedPayloadBytes + 2) * repetitionCount))
        frame.add(guardBytes[0].toByte())
        frame.add(guardBytes[1].toByte())
        for (value in padded) {
            repeat(repetitionCount) { frame.add(value) }
        }
        repeat(repetitionCount) { frame.add(length.toByte()) }
        val crc = crc8(crcInput, 0, crcInput.size).toByte()
        repeat(repetitionCount) { frame.add(crc) }

        val bits = ArrayList<Int>(preambleBits.size + (frame.size * 8))
        for (bit in preambleBits) {
            bits.add(bit)
        }
        for (byte in frame) {
            for (bit in byteToBits(byte.toInt() and 0xFF)) {
                bits.add(bit)
            }
        }

        val totalSamples = (leaderSymbols + gapSymbols + bits.size) * symbolSamples
        val out = FloatArray(totalSamples)
        var cursor = 0
        repeat(leaderSymbols) {
            System.arraycopy(lowRef, 0, out, cursor, symbolSamples)
            cursor += symbolSamples
        }
        cursor += gapSymbols * symbolSamples
        for (bit in bits) {
            val ref = if (bit == 0) lowRef else highRef
            System.arraycopy(ref, 0, out, cursor, symbolSamples)
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
        val frameBytes = guardBytes.size + ((fixedPayloadBytes + 2) * repetitionCount)
        val totalBits = preambleBits.size + (frameBytes * 8)
        val minSamples = (leaderSymbols + gapSymbols + totalBits) * symbolSamples

        while (rxBuffer.size >= minSamples) {
            val leaderStart = findLeaderStart() ?: break
            val nominalStart = leaderStart + ((leaderSymbols + gapSymbols) * symbolSamples)
            val refinedStart = refinePayloadStart(nominalStart)
            if (refinedStart == null) {
                dropFront(leaderStart + symbolSamples)
                continue
            }
            val searchFrom = max(0, refinedStart - (symbolSamples / 2))
            val searchTo = minOf(rxBuffer.size - (totalBits * symbolSamples), refinedStart + (symbolSamples / 2))
            var acceptedPayload: ByteArray? = null
            var acceptedFrameEnd = -1
            if (searchFrom <= searchTo) {
                for (candidateStart in searchFrom..searchTo) {
                    val preambleScore = scorePreambleAt(candidateStart)
                    if (preambleScore < minPreambleScore - 1) {
                        continue
                    }
                    val frame = ByteArray(frameBytes)
                    var valid = true
                    for (index in 0 until frameBytes) {
                        val value = decodeByteAt(candidateStart, preambleBits.size + (index * 8))
                        if (value == null) {
                            valid = false
                            break
                        }
                        frame[index] = value.toByte()
                    }
                    if (!valid) {
                        continue
                    }

                    val payloadStart = guardBytes.size
                    val correctedPayload = ByteArray(fixedPayloadBytes)
                    for (index in 0 until fixedPayloadBytes) {
                        val base = payloadStart + (index * repetitionCount)
                        correctedPayload[index] = majorityByte(frame.copyOfRange(base, base + repetitionCount))
                    }
                    val lengthBase = payloadStart + (fixedPayloadBytes * repetitionCount)
                    val length = (majorityByte(frame.copyOfRange(lengthBase, lengthBase + repetitionCount)).toInt() and 0xFF).coerceAtMost(fixedPayloadBytes)
                    val crcInput = ByteArray(fixedPayloadBytes + 1)
                    System.arraycopy(correctedPayload, 0, crcInput, 0, fixedPayloadBytes)
                    crcInput[fixedPayloadBytes] = length.toByte()
                    val expectedCrc = crc8(crcInput, 0, crcInput.size)
                    val crcBase = lengthBase + repetitionCount
                    val actualCrc = majorityByte(frame.copyOfRange(crcBase, crcBase + repetitionCount)).toInt() and 0xFF
                    if (expectedCrc != actualCrc) {
                        continue
                    }

                    acceptedPayload = correctedPayload.copyOfRange(0, length)
                    acceptedFrameEnd = candidateStart + (totalBits * symbolSamples)
                    break
                }
            }

            if (acceptedPayload == null) {
                dropFront(leaderStart + symbolSamples)
                continue
            }

            decoded.add(acceptedPayload)
            dropFront(acceptedFrameEnd)
        }

        val keep = max(symbolSamples * 16, minSamples * 2)
        if (rxBuffer.size > keep) {
            dropFront(rxBuffer.size - keep)
        }
        return decoded
    }

    private fun findLeaderStart(): Int? {
        val maxStart = rxBuffer.size - ((leaderSymbols + gapSymbols + preambleBits.size) * symbolSamples)
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
            val windowStart = start + (symbolIndex * symbolSamples)
            if (windowRms(windowStart) < minLeaderRms) {
                return Int.MIN_VALUE
            }
            val bit = decodeBitAt(start, symbolIndex) ?: return Int.MIN_VALUE
            if (bit == 0) {
                score += 1
            }
        }
        return score
    }

    private fun scorePreambleAt(start: Int): Int {
        var score = 0
        for (bitIndex in preambleBits.indices) {
            val bit = decodeBitAt(start, bitIndex) ?: return Int.MIN_VALUE
            if (bit == preambleBits[bitIndex]) {
                score += 1
            }
        }
        return score
    }

    private fun refinePayloadStart(nominalStart: Int): Int? {
        val maxStart = rxBuffer.size - (preambleBits.size * symbolSamples)
        if (maxStart <= 0) {
            return null
        }
        val searchRadius = symbolSamples
        val from = max(0, nominalStart - searchRadius)
        val to = minOf(maxStart, nominalStart + searchRadius)
        if (from > to) {
            return null
        }
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

    private fun decodeByteAt(startSample: Int, startBit: Int): Int? {
        var value = 0
        for (bitOffset in 0 until 8) {
            val bit = decodeBitAt(startSample, startBit + bitOffset) ?: return null
            value = (value shl 1) or bit
        }
        return value and 0xFF
    }

    private fun decodeBitAt(startSample: Int, bitIndex: Int): Int? {
        val nominalStart = startSample + (bitIndex * symbolSamples)
        if (nominalStart < 0) {
            return null
        }
        val searchFrom = max(0, nominalStart - symbolSearchRadius)
        val searchTo = minOf(rxBuffer.size - symbolSamples, nominalStart + symbolSearchRadius)
        if (searchFrom > searchTo) {
            return null
        }
        var bestBit: Int? = null
        var bestScore = Float.NEGATIVE_INFINITY
        var candidateStart = searchFrom
        while (candidateStart <= searchTo) {
            val result = classifyBit(candidateStart)
            val strength = abs(result.second - result.third)
            if (strength > bestScore) {
                bestScore = strength
                bestBit = result.first
            }
            candidateStart += symbolSearchStep
        }
        return bestBit
    }

    private fun classifyBit(windowStart: Int): Triple<Int, Float, Float> {
        var lowI = 0f
        var lowQ = 0f
        var highI = 0f
        var highQ = 0f
        for (idx in 0 until symbolSamples) {
            val sample = rxBuffer[windowStart + idx]
            lowI += sample * lowRef[idx]
            lowQ += sample * lowRef[idx + symbolSamples]
            highI += sample * highRef[idx]
            highQ += sample * highRef[idx + symbolSamples]
        }
        val lowMag = sqrt((lowI * lowI) + (lowQ * lowQ))
        val highMag = sqrt((highI * highI) + (highQ * highQ))
        return Triple(if (highMag > lowMag) 1 else 0, lowMag, highMag)
    }

    private fun makeReference(freqHz: Int): FloatArray {
        val out = FloatArray(symbolSamples * 2)
        for (idx in 0 until symbolSamples) {
            val phase = (2.0 * PI.toFloat() * freqHz.toFloat() * idx.toFloat()) / sampleRateHz.toFloat()
            out[idx] = txGainCap * sin(phase.toDouble()).toFloat()
            out[idx + symbolSamples] = txGainCap * cos(phase.toDouble()).toFloat()
        }
        return out
    }

    private fun byteToBits(value: Int): IntArray {
        return IntArray(8) { bitIndex -> (value ushr (7 - bitIndex)) and 0x1 }
    }

    private fun majorityByte(bytes: ByteArray): Byte {
        var value = 0
        for (bitIndex in 0 until 8) {
            val mask = 1 shl (7 - bitIndex)
            var ones = 0
            for (byte in bytes) {
                if ((byte.toInt() and mask) != 0) {
                    ones += 1
                }
            }
            if ((ones * 2) >= bytes.size) {
                value = value or mask
            }
        }
        return value.toByte()
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
        rxBuffer.subList(0, minOf(count, rxBuffer.size)).clear()
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
}
