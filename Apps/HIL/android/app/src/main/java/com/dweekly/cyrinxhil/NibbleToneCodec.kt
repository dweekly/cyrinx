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
        val minSymbols = syncNibbles.size + 4
        val minSamples = minSymbols * symbolSamples

        while (rxBuffer.size >= minSamples) {
            val start = findSyncStart() ?: break
            val lenHi = decodeNibbleAt(start, syncNibbles.size)
            val lenLo = decodeNibbleAt(start, syncNibbles.size + 1)
            if (lenHi == null || lenLo == null) {
                dropFront(start + symbolSamples)
                continue
            }
            val length = ((lenHi shl 4) or lenLo) and 0xFF
            val totalNibbles = syncNibbles.size + 2 + (length * 2) + 2
            val frameEnd = start + (totalNibbles * symbolSamples)
            if (rxBuffer.size < frameEnd) {
                if (start > 0) {
                    dropFront(start)
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
                dropFront(start + symbolSamples)
                continue
            }

            val crcHi = decodeNibbleAt(start, nibbleIndex)
            val crcLo = decodeNibbleAt(start, nibbleIndex + 1)
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

        val maxFrameNibbles = syncNibbles.size + 2 + (255 * 2) + 2
        val keep = max(symbolSamples * maxFrameNibbles, minSamples * 2)
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
        val coarseStep = max(16, symbolSamples / 6)
        var candidate = from
        while (candidate <= to) {
            val score = scoreSyncAt(candidate)
            if (score > bestScore || (score == bestScore && isCloser(candidate, bestStart, nominalStart))) {
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
                if (score > bestScore || (score == bestScore && isCloser(fineCandidate, bestStart, nominalStart))) {
                    bestScore = score
                    bestStart = fineCandidate
                }
            }
        }
        return if (bestScore >= syncNibbles.size - 1) bestStart else null
    }

    private fun isCloser(candidate: Int, current: Int, target: Int): Boolean {
        if (current < 0) {
            return true
        }
        return kotlin.math.abs(candidate - target) < kotlin.math.abs(current - target)
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

    private fun findSyncStart(): Int? {
        val maxStart = rxBuffer.size - (syncNibbles.size * symbolSamples)
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
        return if (bestScore >= syncNibbles.size - 1) bestStart else null
    }

    private data class SyncScore(val score: Int, val magnitude: Float)

    private fun scoreSyncWithMagnitude(start: Int): SyncScore {
        var score = 0
        var magnitude = 0f
        for (index in syncNibbles.indices) {
            val windowStart = start + (index * symbolSamples)
            val nibble = decodeNibbleAtSample(windowStart) ?: return SyncScore(Int.MIN_VALUE, 0f)
            if (nibble == syncNibbles[index]) {
                score += 1
            }
            magnitude += magnitudeForNibbleAtSample(windowStart, syncNibbles[index])
        }
        return SyncScore(score, magnitude)
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

    private fun magnitudeForNibbleAtSample(windowStart: Int, nibble: Int): Float {
        val windowEnd = windowStart + symbolSamples
        if (windowStart < 0 || windowEnd > rxBuffer.size) {
            return 0f
        }
        val ref = toneRefs[nibble and 0xF]
        var iAcc = 0f
        var qAcc = 0f
        for (sampleIndex in 0 until symbolSamples) {
            val sample = rxBuffer[windowStart + sampleIndex]
            iAcc += sample * ref[sampleIndex]
            qAcc += sample * ref[sampleIndex + symbolSamples]
        }
        return sqrt((iAcc * iAcc) + (qAcc * qAcc))
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
