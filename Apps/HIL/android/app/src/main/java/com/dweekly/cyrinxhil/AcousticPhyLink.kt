package com.dweekly.cyrinxhil

import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.atan2
import kotlin.math.ceil
import kotlin.math.cos
import kotlin.math.ln
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sin
import kotlin.math.sqrt

private const val ACOUSTIC_FRAME_MAX_BYTES = 2048
private const val ACOUSTIC_HEADER_BYTES = 7
private const val ACOUSTIC_HEADER_MAGIC0 = 0xAC
private const val ACOUSTIC_HEADER_MAGIC1 = 0x51

private enum class AcousticBodyMode(val value: Int) {
    ROBUST_DCSS(0),
    TURBO_OFDM(1),
    ;

    companion object {
        fun fromValue(value: Int): AcousticBodyMode? = entries.firstOrNull { it.value == value }
    }
}

private data class AcousticHeader(
    val mode: AcousticBodyMode,
    val payloadLength: Int,
) {
    fun encode(): ByteArray {
        val out = ByteArray(ACOUSTIC_HEADER_BYTES)
        out[0] = ACOUSTIC_HEADER_MAGIC0.toByte()
        out[1] = ACOUSTIC_HEADER_MAGIC1.toByte()
        out[2] = mode.value.toByte()
        out[3] = ((payloadLength ushr 8) and 0xFF).toByte()
        out[4] = (payloadLength and 0xFF).toByte()
        val crc = crc16Ccitt(out, 0, 5)
        out[5] = ((crc ushr 8) and 0xFF).toByte()
        out[6] = (crc and 0xFF).toByte()
        return out
    }

    companion object {
        fun decode(bytes: ByteArray): AcousticHeader? {
            if (bytes.size != ACOUSTIC_HEADER_BYTES) {
                return null
            }
            if ((bytes[0].toInt() and 0xFF) != ACOUSTIC_HEADER_MAGIC0 || (bytes[1].toInt() and 0xFF) != ACOUSTIC_HEADER_MAGIC1) {
                return null
            }
            val mode = AcousticBodyMode.fromValue(bytes[2].toInt() and 0xFF) ?: return null
            val payloadLength = ((bytes[3].toInt() and 0xFF) shl 8) or (bytes[4].toInt() and 0xFF)
            val expected = crc16Ccitt(bytes, 0, 5)
            val actual = ((bytes[5].toInt() and 0xFF) shl 8) or (bytes[6].toInt() and 0xFF)
            if (expected != actual) {
                return null
            }
            return AcousticHeader(mode = mode, payloadLength = payloadLength)
        }
    }
}

data class AcousticDecodedFrame(
    val frame: ByteArray,
    val report: ChannelReport,
)

private data class DcssConfig(
    val sampleRateHz: Int,
    val symbolSamples: Int = 256,
    val symbolBins: Int = 256,
    val startHz: Float,
    val endHz: Float,
    val txGainCap: Float,
)

private class DcssModem(private val config: DcssConfig) {
    private val lookup: Array<FloatArray> = makeShiftedLookup(config)

    fun modulate(payload: ByteArray): FloatArray {
        val framed = prefixLengthBytes(payload)
        val out = FloatArray(framed.size * config.symbolSamples)
        var cursor = 0
        for (byte in framed) {
            val symbol = byte.toInt() and 0xFF
            val symbolWave = lookup[symbol % config.symbolBins]
            System.arraycopy(symbolWave, 0, out, cursor, config.symbolSamples)
            cursor += config.symbolSamples
        }
        return out
    }

    fun demodulate(samples: FloatArray): ByteArray {
        if (samples.size < config.symbolSamples) {
            throw IllegalStateException("not enough samples for D-CSS demod")
        }
        val symbolCount = samples.size / config.symbolSamples
        val decoded = ByteArray(symbolCount)
        for (symbolIndex in 0 until symbolCount) {
            val windowStart = symbolIndex * config.symbolSamples
            val bestShift = bestShift(samples, windowStart)
            decoded[symbolIndex] = (bestShift and 0xFF).toByte()
        }
        return unprefixLengthBytes(decoded)
    }

    private fun bestShift(samples: FloatArray, windowStart: Int): Int {
        var best = 0
        var bestScore = Float.NEGATIVE_INFINITY
        for (shift in 0 until config.symbolBins) {
            var dot = 0f
            val ref = lookup[shift]
            for (idx in 0 until config.symbolSamples) {
                dot += samples[windowStart + idx] * ref[idx]
            }
            val score = abs(dot)
            if (score > bestScore) {
                bestScore = score
                best = shift
            }
        }
        return best
    }

    private fun makeShiftedLookup(config: DcssConfig): Array<FloatArray> {
        val base = makeBaseChirp(config)
        return Array(config.symbolBins) { shift ->
            val rotation = shift % config.symbolSamples
            FloatArray(config.symbolSamples) { idx -> base[(idx + rotation) % config.symbolSamples] }
        }
    }

    private fun makeBaseChirp(config: DcssConfig): FloatArray {
        val fs = config.sampleRateHz.toFloat()
        val duration = config.symbolSamples / fs
        val sweep = config.endHz - config.startHz
        val chirpRate = sweep / duration
        val gain = min(max(config.txGainCap, 0f), 0.95f)
        return FloatArray(config.symbolSamples) { n ->
            val t = n / fs
            val phase = (2.0f * PI.toFloat()) * ((config.startHz * t) + (0.5f * chirpRate * t * t))
            gain * sin(phase)
        }
    }

    private fun prefixLengthBytes(payload: ByteArray): ByteArray {
        val clamped = payload.copyOfRange(0, min(payload.size, 0xFFFF))
        val out = ByteArray(clamped.size + 2)
        out[0] = ((clamped.size ushr 8) and 0xFF).toByte()
        out[1] = (clamped.size and 0xFF).toByte()
        System.arraycopy(clamped, 0, out, 2, clamped.size)
        return out
    }

    private fun unprefixLengthBytes(bytes: ByteArray): ByteArray {
        if (bytes.size < 2) {
            throw IllegalStateException("missing D-CSS length prefix")
        }
        val length = ((bytes[0].toInt() and 0xFF) shl 8) or (bytes[1].toInt() and 0xFF)
        val required = 2 + length
        if (bytes.size < required) {
            throw IllegalStateException("incomplete D-CSS payload")
        }
        return bytes.copyOfRange(2, required)
    }
}

class AcousticPhyLink(private val config: SessionConfig) {
    private val lock = Object()

    private val sampleRateHz = config.sampleRateHz.coerceIn(8_000, 192_000)
    private val cappedGain = min(max(config.txGainCap, 0f), 0.12f)

    private val dcssSymbolSamples = config.dcssSymbolSamples.coerceIn(64, 4096)
    private val robustConfig = DcssConfig(
        sampleRateHz = sampleRateHz,
        symbolSamples = dcssSymbolSamples,
        symbolBins = 256,
        startHz = config.bandStartHz.toFloat(),
        endHz = config.bandEndHz.toFloat(),
        txGainCap = cappedGain,
    )
    private val headerConfig = robustConfig

    private val robustModem = DcssModem(robustConfig)
    private val headerModem = DcssModem(headerConfig)

    private val preambleBlock = makePreambleBlock(
        sampleRateHz = sampleRateHz,
        bandStartHz = config.bandStartHz,
        bandEndHz = config.bandEndHz,
        txGainCap = cappedGain,
    )
    private val preamble = FloatArray(preambleBlock.size * 2).also {
        System.arraycopy(preambleBlock, 0, it, 0, preambleBlock.size)
        System.arraycopy(preambleBlock, 0, it, preambleBlock.size, preambleBlock.size)
    }
    private val preambleEnergy = max(1e-7f, preamble.fold(0f) { acc, v -> acc + (v * v) })
    private val syncThreshold = config.preambleSyncThreshold.coerceIn(0.10f, 0.98f)

    private val rxBuffer = ArrayList<Float>()
    private var rxSearchStart = 0

    fun encode(frame: ByteArray): FloatArray {
        require(frame.isNotEmpty() && frame.size <= ACOUSTIC_FRAME_MAX_BYTES) {
            "invalid acoustic frame payload size"
        }

        val mode = selectBodyMode(frame)
        val header = AcousticHeader(mode = mode, payloadLength = frame.size).encode()
        val headerWave = headerModem.modulate(header)

        // Android port currently emits robust D-CSS body mode for interoperability.
        val bodyWave = robustModem.modulate(frame)

        val out = FloatArray(preamble.size + headerWave.size + bodyWave.size)
        var cursor = 0
        System.arraycopy(preamble, 0, out, cursor, preamble.size)
        cursor += preamble.size
        System.arraycopy(headerWave, 0, out, cursor, headerWave.size)
        cursor += headerWave.size
        System.arraycopy(bodyWave, 0, out, cursor, bodyWave.size)
        return out
    }

    fun ingest(samples: FloatArray): List<AcousticDecodedFrame> {
        if (samples.isEmpty()) {
            return emptyList()
        }
        synchronized(lock) {
            for (sample in samples) {
                rxBuffer.add(sample)
            }
            return decodeAvailableLocked()
        }
    }

    private fun decodeAvailableLocked(): List<AcousticDecodedFrame> {
        val decoded = ArrayList<AcousticDecodedFrame>()
        val headerSamples = expectedDcssSamples(ACOUSTIC_HEADER_BYTES, headerConfig)

        while (true) {
            val lockResult = findBestPreambleLock(rxBuffer, rxSearchStart)
            if (lockResult == null) {
                trimUnlockedBufferForResync(headerSamples)
                break
            }

            val syncStart = lockResult.index
            val headerLock = decodeHeaderAround(syncStart, headerSamples)
            if (headerLock == null) {
                dropFront(syncStart + 1)
                rxSearchStart = 0
                continue
            }
            val packetHeader = headerLock.header
            val headerStart = headerLock.start
            val headerEnd = headerLock.end

            if (rxBuffer.size < headerEnd) {
                if (syncStart > 0) {
                    dropFront(syncStart)
                    rxSearchStart = 0
                }
                break
            }

            if (packetHeader.mode != AcousticBodyMode.ROBUST_DCSS) {
                // Turbo mode is intentionally not decoded in this Android port.
                dropFront(syncStart + 1)
                rxSearchStart = 0
                continue
            }

            val bodySamples = expectedDcssSamples(packetHeader.payloadLength, robustConfig)
            val bodyStart = headerEnd
            val bodyEnd = bodyStart + bodySamples
            if (rxBuffer.size < bodyEnd) {
                if (syncStart > 0) {
                    dropFront(syncStart)
                    rxSearchStart = 0
                }
                break
            }

            val bodyWindow = sliceToFloatArray(rxBuffer, bodyStart, bodyEnd)
            val bodyPayload = try {
                robustModem.demodulate(bodyWindow)
            } catch (_: Throwable) {
                null
            }
            if (bodyPayload == null || bodyPayload.size != packetHeader.payloadLength) {
                dropFront(syncStart + 1)
                rxSearchStart = 0
                continue
            }

            decoded.add(
                AcousticDecodedFrame(
                    frame = bodyPayload,
                    report = channelReport(lockResult),
                ),
            )
            dropFront(bodyEnd)
            rxSearchStart = 0
        }

        return decoded
    }

    private data class HeaderLock(
        val start: Int,
        val end: Int,
        val header: AcousticHeader,
    )

    private fun decodeHeaderAround(syncStart: Int, headerSamples: Int): HeaderLock? {
        val baseStart = syncStart + preamble.size
        val maxShiftSamples = 32
        val shiftStep = 8
        val shifts = ArrayList<Int>()
        shifts.add(0)
        var delta = shiftStep
        while (delta <= maxShiftSamples) {
            shifts.add(-delta)
            shifts.add(delta)
            delta += shiftStep
        }

        for (shift in shifts) {
            val headerStart = baseStart + shift
            val headerEnd = headerStart + headerSamples
            if (headerStart < 0 || headerEnd > rxBuffer.size) {
                continue
            }
            val headerWindow = sliceToFloatArray(rxBuffer, headerStart, headerEnd)
            val decodedHeader = try {
                headerModem.demodulate(headerWindow)
            } catch (_: Throwable) {
                null
            } ?: continue
            val packetHeader = AcousticHeader.decode(decodedHeader) ?: continue
            if (packetHeader.payloadLength <= 0 || packetHeader.payloadLength > ACOUSTIC_FRAME_MAX_BYTES) {
                continue
            }
            return HeaderLock(start = headerStart, end = headerEnd, header = packetHeader)
        }
        return null
    }

    private fun selectBodyMode(frame: ByteArray): AcousticBodyMode {
        if (frame.size < (2 + 15)) {
            return AcousticBodyMode.ROBUST_DCSS
        }
        val header = frame.copyOfRange(2, 17)
        val frameType = readBitsMsb(header, 4, 4)
        if (frameType == CyrinxConstants.FRAME_ACK || frameType == CyrinxConstants.FRAME_CONTROL) {
            return AcousticBodyMode.ROBUST_DCSS
        }
        val gearId = readBitsMsb(header, 64, 3)
        return if (gearId >= 2) {
            AcousticBodyMode.ROBUST_DCSS
        } else {
            AcousticBodyMode.ROBUST_DCSS
        }
    }

    private fun expectedDcssSamples(payloadBytes: Int, dcssConfig: DcssConfig): Int {
        if (payloadBytes < 0 || dcssConfig.symbolSamples <= 0) {
            return 0
        }
        return (payloadBytes + 2) * dcssConfig.symbolSamples
    }

    private data class PreambleLock(
        val index: Int,
        val correlation: Float,
        val snrDb: Float,
        val evmPct: Float,
    )

    private fun findBestPreambleLock(buffer: List<Float>, startAt: Int): PreambleLock? {
        if (buffer.size < preamble.size) {
            return null
        }
        val searchLimit = buffer.size - preamble.size
        val lowerBound = startAt.coerceIn(0, searchLimit)

        fun evaluateAt(start: Int): PreambleLock? {
            var dot = 0f
            var segmentEnergy = 0f
            for (idx in preamble.indices) {
                val sample = buffer[start + idx]
                val reference = preamble[idx]
                dot += sample * reference
                segmentEnergy += sample * sample
            }
            val norm = sqrt(max(segmentEnergy * preambleEnergy, 1e-7f))
            val corr = dot / norm
            if (corr < syncThreshold) {
                return null
            }

            val scale = dot / preambleEnergy
            var errorEnergy = 0f
            for (idx in preamble.indices) {
                val estimate = scale * preamble[idx]
                val err = buffer[start + idx] - estimate
                errorEnergy += err * err
            }

            val signalPower = max(1e-7f, (scale * scale * preambleEnergy) / preamble.size)
            val noisePower = max(1e-7f, errorEnergy / preamble.size)
            val snrDb = 10.0f * log10(signalPower / noisePower)
            val evmPct = sqrt(noisePower / signalPower) * 100.0f
            return PreambleLock(index = start, correlation = corr, snrDb = snrDb, evmPct = evmPct)
        }

        val coarseStride = if ((searchLimit - lowerBound) > 256) 4 else 1
        var coarse = lowerBound
        while (coarse <= searchLimit) {
            val coarseLock = evaluateAt(coarse)
            if (coarseLock != null) {
                val refineFrom = max(lowerBound, coarse - (coarseStride - 1))
                val refineTo = min(searchLimit, coarse + (coarseStride - 1))
                for (start in refineFrom..refineTo) {
                    val refined = evaluateAt(start)
                    if (refined != null) {
                        return refined
                    }
                }
                return coarseLock
            }
            coarse += coarseStride
        }
        return null
    }

    private fun channelReport(lockResult: PreambleLock): ChannelReport {
        val estimatedPer = max(0f, min(1f, 1.0f - lockResult.correlation))
        return ChannelReport(
            snrDb = lockResult.snrDb,
            evmPct = lockResult.evmPct,
            cfoHz = 0f,
            per2s = estimatedPer,
            crcFail = false,
        )
    }

    private fun trimUnlockedBufferForResync(headerWindow: Int) {
        val keep = max(preamble.size * 2, headerWindow)
        if (rxBuffer.size > keep) {
            val dropped = rxBuffer.size - keep
            dropFront(dropped)
            rxSearchStart = max(0, rxSearchStart - dropped)
        }
        val maxStart = max(0, rxBuffer.size - preamble.size)
        if (rxSearchStart > maxStart) {
            rxSearchStart = maxStart
        }
    }

    private fun dropFront(count: Int) {
        if (count <= 0 || rxBuffer.isEmpty()) {
            return
        }
        val clamped = min(count, rxBuffer.size)
        rxBuffer.subList(0, clamped).clear()
    }

    private fun makePreambleBlock(
        sampleRateHz: Int,
        bandStartHz: Int,
        bandEndHz: Int,
        txGainCap: Float,
    ): FloatArray {
        val zc = zcGenerate(root = 29, length = 127) ?: return fallbackPreamble(sampleRateHz, txGainCap)
        val fs = sampleRateHz.toFloat()
        if (fs <= 0f) {
            return fallbackPreamble(48_000, txGainCap)
        }

        val centerHz = (bandStartHz + bandEndHz) * 0.5f
        val chipSpan = 4
        val amplitude = min(max(txGainCap, 0f), 0.12f)
        val out = FloatArray(zc.size * chipSpan)
        var sampleIndex = 0
        for (chip in zc) {
            val phaseOffset = atan2(chip.second, chip.first)
            repeat(chipSpan) {
                val t = sampleIndex / fs
                val phase = (2.0f * PI.toFloat() * centerHz * t) + phaseOffset
                out[sampleIndex] = amplitude * sin(phase)
                sampleIndex += 1
            }
        }
        return out
    }

    private fun fallbackPreamble(sampleRateHz: Int, txGainCap: Float): FloatArray {
        val fs = max(sampleRateHz.toFloat(), 8_000f)
        val toneHz = 20_500f
        val amplitude = min(max(txGainCap, 0f), 0.1f)
        val count = 512
        return FloatArray(count) { idx ->
            val phase = 2.0f * PI.toFloat() * toneHz * idx / fs
            amplitude * sin(phase)
        }
    }

    private fun zcGenerate(root: Int, length: Int): List<Pair<Float, Float>>? {
        if (length <= 0 || root == 0) {
            return null
        }
        val n = length.toFloat()
        return List(length) { idx ->
            val nn = idx.toFloat()
            val phase = -(PI.toFloat()) * root.toFloat() * nn * (nn + 1.0f) / n
            Pair(cos(phase), sin(phase))
        }
    }

    private fun sliceToFloatArray(source: List<Float>, start: Int, end: Int): FloatArray {
        val out = FloatArray(end - start)
        for (i in start until end) {
            out[i - start] = source[i]
        }
        return out
    }

    private fun readBitsMsb(bytes: ByteArray, startBit: Int, bitCount: Int): Int {
        if (bytes.isEmpty() || startBit < 0 || bitCount <= 0) {
            return 0
        }
        var value = 0
        for (bitOffset in 0 until bitCount) {
            val bitIndex = startBit + bitOffset
            val byteIndex = bitIndex / 8
            if (byteIndex >= bytes.size) {
                value = value shl (bitCount - bitOffset)
                break
            }
            val shift = 7 - (bitIndex % 8)
            val bit = (bytes[byteIndex].toInt() ushr shift) and 0x1
            value = (value shl 1) or bit
        }
        return value
    }
}

private fun crc16Ccitt(data: ByteArray, start: Int, len: Int): Int {
    var crc = 0xFFFF
    for (i in 0 until len) {
        crc = crc xor ((data[start + i].toInt() and 0xFF) shl 8)
        for (j in 0 until 8) {
            crc = if ((crc and 0x8000) != 0) {
                ((crc shl 1) xor 0x1021) and 0xFFFF
            } else {
                (crc shl 1) and 0xFFFF
            }
        }
    }
    return crc and 0xFFFF
}

private fun log10(value: Float): Float = (ln(value.toDouble()) / ln(10.0)).toFloat()
