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
private const val CYRINX_OFDM_FFT_SIZE = 1024

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

    fun demodulate(samples: FloatArray): ByteArray? {
        if (samples.size < config.symbolSamples) {
            return null
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

    private fun unprefixLengthBytes(bytes: ByteArray): ByteArray? {
        if (bytes.size < 2) {
            return null
        }
        val length = ((bytes[0].toInt() and 0xFF) shl 8) or (bytes[1].toInt() and 0xFF)
        val required = 2 + length
        if (bytes.size < required) {
            return null
        }
        return bytes.copyOfRange(2, required)
    }
}

class AcousticPhyLink(private val config: SessionConfig) {
    private val lock = Object()

    private val sampleRateHz = config.sampleRateHz.coerceIn(8_000, 192_000)
    private val cappedGain = run {
        val maxCap = if (config.bandStartHz >= 18000) 0.70f else 0.12f
        min(max(config.txGainCap, 0f), maxCap)
    }

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

    private val ofdmFftSize = CYRINX_OFDM_FFT_SIZE
    private val ofdmCpSamples = 96
    private val fft = FFT(ofdmFftSize)
    private val ofdmActiveBins = run {
        val binWidth = sampleRateHz.toFloat() / ofdmFftSize.toFloat()
        val start = max(1, ceil(config.bandStartHz.toFloat() / binWidth).toInt())
        val end = min((ofdmFftSize / 2) - 1, kotlin.math.floor(config.bandEndHz.toFloat() / binWidth).toInt())
        if (start <= end) {
            IntArray(end - start + 1) { i -> start + i }
        } else {
            intArrayOf()
        }
    }
    private val ofdmActiveCarrierCount = ofdmActiveBins.size

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

    private val rxBuffer = FloatBuffer(131072)
    private var rxSearchStart = 0

    fun encode(frame: ByteArray): FloatArray {
        require(frame.isNotEmpty() && frame.size <= ACOUSTIC_FRAME_MAX_BYTES) {
            "invalid acoustic frame payload size"
        }

        val mode = selectBodyMode(frame)
        val header = AcousticHeader(mode = mode, payloadLength = frame.size).encode()
        val headerWave = headerModem.modulate(header)

        val bodyWave = if (mode == AcousticBodyMode.TURBO_OFDM) {
            modulateOfdm(frame)
        } else {
            robustModem.modulate(frame)
        }

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
            rxBuffer.addAll(samples)
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
            val minRequired = syncStart + preamble.size + headerSamples + 32
            if (rxBuffer.size < minRequired) {
                if (syncStart > 0) {
                    dropFront(syncStart)
                    rxSearchStart = 0
                }
                break
            }

            val headerLock = decodeHeaderAround(syncStart, headerSamples)
            if (headerLock == null) {
                dropFront(syncStart + 1)
                rxSearchStart = 0
                continue
            }
            val packetHeader = headerLock.header
            val headerStart = headerLock.start
            val headerEnd = headerLock.end

            val bodySamples = if (packetHeader.mode == AcousticBodyMode.TURBO_OFDM) {
                expectedOfdmSamples(packetHeader.payloadLength)
            } else {
                expectedDcssSamples(packetHeader.payloadLength, robustConfig)
            }
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
                if (packetHeader.mode == AcousticBodyMode.TURBO_OFDM) {
                    demodulateOfdm(bodyWindow)
                } else {
                    robustModem.demodulate(bodyWindow)
                }
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
            val decodedHeader = headerModem.demodulate(headerWindow) ?: continue
            val packetHeader = AcousticHeader.decode(decodedHeader) ?: continue
            if (packetHeader.payloadLength <= 0 || packetHeader.payloadLength > ACOUSTIC_FRAME_MAX_BYTES) {
                continue
            }
            android.util.Log.i("CyrinxHILAndroid", "Header DECODED SUCCESSFULLY! shift=$shift length=${packetHeader.payloadLength} mode=${packetHeader.mode}")
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
            AcousticBodyMode.TURBO_OFDM
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

    private fun findBestPreambleLock(buffer: FloatBuffer, startAt: Int): PreambleLock? {
        if (buffer.size < preamble.size) {
            return null
        }
        val searchLimit = buffer.size - preamble.size
        val lowerBound = startAt.coerceIn(0, searchLimit)

        var maxCorrObserved = 0f
        var maxCorrIndex = -1

        for (start in lowerBound..searchLimit) {
            var dot = 0f
            var segmentEnergy = 0f
            for (idx in preamble.indices) {
                val sample = buffer.array[start + idx]
                val reference = preamble[idx]
                dot += sample * reference
                segmentEnergy += sample * sample
            }
            val norm = sqrt(max(segmentEnergy * preambleEnergy, 1e-7f))
            val corr = dot / norm
            if (corr > maxCorrObserved) {
                maxCorrObserved = corr
                maxCorrIndex = start
            }
            if (corr < syncThreshold) {
                continue
            }

            // We crossed the syncThreshold! Search a local window ahead (48 samples / 12 chips)
            // to find the absolute maximum peak and prevent locking on rising-edge sidelobes.
            var bestStart = start
            var bestCorr = corr
            var bestDot = dot

            val windowSize = 48
            val peakSearchLimit = min(start + windowSize, searchLimit)
            for (candidateStart in (start + 1)..peakSearchLimit) {
                var candidateDot = 0f
                var candidateEnergy = 0f
                for (idx in preamble.indices) {
                    val sample = buffer.array[candidateStart + idx]
                    val reference = preamble[idx]
                    candidateDot += sample * reference
                    candidateEnergy += sample * sample
                }
                val candidateNorm = sqrt(max(candidateEnergy * preambleEnergy, 1e-7f))
                val candidateCorr = candidateDot / candidateNorm
                if (candidateCorr > bestCorr) {
                    bestCorr = candidateCorr
                    bestStart = candidateStart
                    bestDot = candidateDot
                }
            }

            val scale = bestDot / preambleEnergy
            var errorEnergy = 0f
            for (idx in preamble.indices) {
                val estimate = scale * preamble[idx]
                val err = buffer.array[bestStart + idx] - estimate
                errorEnergy += err * err
            }

            val signalPower = max(1e-7f, (scale * scale * preambleEnergy) / preamble.size)
            val noisePower = max(1e-7f, errorEnergy / preamble.size)
            val snrDb = 10.0f * log10(signalPower / noisePower)
            val evmPct = sqrt(noisePower / signalPower) * 100.0f
            android.util.Log.i("CyrinxHILAndroid", "Preamble lock found! start=$bestStart (first crossing=$start) corr=$bestCorr snrDb=$snrDb")
            return PreambleLock(index = bestStart, correlation = bestCorr, snrDb = snrDb, evmPct = evmPct)
        }
        if (maxCorrObserved > 0.01f) {
            android.util.Log.d("CyrinxHILAndroid", "maxCorrObserved=$maxCorrObserved at index $maxCorrIndex (threshold=$syncThreshold, searchSize=${searchLimit - lowerBound + 1}, bufferSize=${buffer.size})")
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
        rxBuffer.dropFront(count)
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
        val maxCap = if (bandStartHz >= 18000) 0.70f else 0.12f
        val amplitude = min(max(txGainCap, 0f), maxCap)
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

    private fun sliceToFloatArray(source: FloatBuffer, start: Int, end: Int): FloatArray {
        return source.slice(start, end)
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

    private fun modulateOfdm(payload: ByteArray): FloatArray {
        val bits = BitPacking.encodeLengthPrefixed(payload)
        val qpskSymbols = mapBitsToQpsk(bits)
        val activeCount = ofdmActiveCarrierCount
        if (activeCount <= 0) {
            return FloatArray(0)
        }
        val frameCount = ceil(qpskSymbols.size.toDouble() / activeCount.toDouble()).toInt()
        val symbolLength = ofdmFftSize + ofdmCpSamples
        val out = FloatArray(frameCount * symbolLength)

        val re = FloatArray(ofdmFftSize)
        val im = FloatArray(ofdmFftSize)
        val timeRe = FloatArray(ofdmFftSize)
        val timeIm = FloatArray(ofdmFftSize)

        val amplitude = min(max(cappedGain, 0f), 0.95f)
        val fftScale = 1.0f / ofdmFftSize.toFloat()
        val scale = amplitude * fftScale

        for (frameIdx in 0 until frameCount) {
            val start = frameIdx * activeCount
            val end = min(start + activeCount, qpskSymbols.size)

            re.fill(0f)
            im.fill(0f)

            for (idx in 0 until activeCount) {
                val symbol = if (start + idx < end) qpskSymbols[start + idx] else 0.toByte()
                val bin = ofdmActiveBins[idx]

                val norm = 0.70710677f
                when (symbol.toInt() and 0x3) {
                    0 -> {
                        re[bin] = norm
                        im[bin] = norm
                    }
                    1 -> {
                        re[bin] = -norm
                        im[bin] = norm
                    }
                    2 -> {
                        re[bin] = norm
                        im[bin] = -norm
                    }
                    else -> {
                        re[bin] = -norm
                        im[bin] = -norm
                    }
                }

                val mirror = (ofdmFftSize - bin) % ofdmFftSize
                if (mirror != bin) {
                    re[mirror] = re[bin]
                    im[mirror] = -im[bin]
                }
            }

            timeRe.fill(0f)
            timeIm.fill(0f)
            System.arraycopy(re, 0, timeRe, 0, ofdmFftSize)
            System.arraycopy(im, 0, timeIm, 0, ofdmFftSize)
            fft.transform(timeRe, timeIm, forward = false)

            val outStart = frameIdx * symbolLength
            val cpStart = ofdmFftSize - ofdmCpSamples
            for (i in 0 until ofdmCpSamples) {
                out[outStart + i] = timeRe[cpStart + i] * scale
            }
            for (i in 0 until ofdmFftSize) {
                out[outStart + ofdmCpSamples + i] = timeRe[i] * scale
            }
        }
        return out
    }

    private fun demodulateOfdm(samples: FloatArray): ByteArray? {
        val symbolLength = ofdmFftSize + ofdmCpSamples
        if (samples.size < symbolLength) {
            return null
        }
        val frameCount = samples.size / symbolLength
        val activeCount = ofdmActiveCarrierCount
        val totalBits = frameCount * activeCount * 2
        val bits = ByteArray(totalBits)
        var bitIdx = 0

        val re = FloatArray(ofdmFftSize)
        val im = FloatArray(ofdmFftSize)

        for (frameIdx in 0 until frameCount) {
            val start = (frameIdx * symbolLength) + ofdmCpSamples

            System.arraycopy(samples, start, re, 0, ofdmFftSize)
            im.fill(0f)

            fft.transform(re, im, forward = true)

            for (idx in 0 until activeCount) {
                val bin = ofdmActiveBins[idx]
                val symbol = demapQpsk(re[bin], im[bin]).toInt()
                bits[bitIdx++] = ((symbol ushr 1) and 1).toByte()
                bits[bitIdx++] = (symbol and 1).toByte()
            }
        }

        return BitPacking.decodeLengthPrefixed(bits)
    }

    private fun expectedOfdmSamples(payloadBytes: Int): Int {
        if (payloadBytes < 0 || ofdmFftSize <= 0 || ofdmCpSamples <= 0) {
            return 0
        }
        if (ofdmActiveCarrierCount <= 0) {
            return 0
        }
        val bitCount = (payloadBytes + 2) * 8
        val qpskSymbols = (bitCount + 1) / 2
        val frameCount = max(1, (qpskSymbols + ofdmActiveCarrierCount - 1) / ofdmActiveCarrierCount)
        return frameCount * (ofdmFftSize + ofdmCpSamples)
    }

    private fun demapQpsk(re: Float, im: Float): Byte {
        if (re >= 0f && im >= 0f) return 0
        if (re < 0f && im >= 0f) return 1
        if (re >= 0f && im < 0f) return 2
        return 3
    }

    private fun mapBitsToQpsk(bits: ByteArray): ByteArray {
        if (bits.isEmpty()) {
            return byteArrayOf()
        }
        val symbols = ByteArray((bits.size + 1) / 2)
        var index = 0
        var symIdx = 0
        while (index < bits.size) {
            val high = bits[index].toInt() and 1
            val low = if (index + 1 < bits.size) (bits[index + 1].toInt() and 1) else 0
            symbols[symIdx++] = ((high shl 1) or low).toByte()
            index += 2
        }
        return symbols
    }

    private object BitPacking {
        fun encodeLengthPrefixed(payload: ByteArray): ByteArray {
            val prefix = prefixLengthBytes(payload)
            val bits = ByteArray(prefix.size * 8)
            var bitIdx = 0
            for (byte in prefix) {
                val b = byte.toInt() and 0xFF
                for (shift in 7 downTo 0) {
                    bits[bitIdx++] = ((b ushr shift) and 1).toByte()
                }
            }
            return bits
        }

        fun decodeLengthPrefixed(bits: ByteArray): ByteArray? {
            if (bits.size < 16) {
                return null
            }
            val length = bitsToUInt16(bits, 0)
            val requiredBits = 16 + (length * 8)
            if (bits.size < requiredBits) {
                return null
            }
            return bitsToBytes(bits, 16, requiredBits)
        }

        fun prefixLengthBytes(payload: ByteArray): ByteArray {
            val clampedSize = min(payload.size, 0xFFFF)
            val out = ByteArray(clampedSize + 2)
            out[0] = ((clampedSize ushr 8) and 0xFF).toByte()
            out[1] = (clampedSize and 0xFF).toByte()
            System.arraycopy(payload, 0, out, 2, clampedSize)
            return out
        }

        private fun bitsToUInt16(bits: ByteArray, startOffset: Int): Int {
            var value = 0
            for (i in 0 until 16) {
                value = (value shl 1) or (bits[startOffset + i].toInt() and 1)
            }
            return value
        }

        private fun bitsToBytes(bits: ByteArray, startOffset: Int, endOffset: Int): ByteArray {
            val bitCount = endOffset - startOffset
            val byteCount = bitCount / 8
            val out = ByteArray(byteCount)
            for (idx in 0 until byteCount) {
                var value = 0
                for (bitIdx in 0 until 8) {
                    value = (value shl 1) or (bits[startOffset + (idx * 8) + bitIdx].toInt() and 1)
                }
                out[idx] = value.toByte()
            }
            return out
        }
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

class FloatBuffer(initialCapacity: Int = 131072) {
    var array = FloatArray(initialCapacity)
    var size = 0
        private set

    fun isEmpty(): Boolean = size == 0

    fun addAll(values: FloatArray) {
        ensureCapacity(size + values.size)
        System.arraycopy(values, 0, array, size, values.size)
        size += values.size
    }

    fun slice(start: Int, end: Int): FloatArray {
        val len = end - start
        val result = FloatArray(len)
        System.arraycopy(array, start, result, 0, len)
        return result
    }

    fun dropFront(count: Int) {
        if (count <= 0) return
        val toDrop = min(count, size)
        val remaining = size - toDrop
        if (remaining > 0) {
            System.arraycopy(array, toDrop, array, 0, remaining)
        }
        size = remaining
    }

    fun clear() {
        size = 0
    }

    private fun ensureCapacity(minCapacity: Int) {
        if (minCapacity > array.size) {
            var newCap = array.size * 2
            if (newCap < minCapacity) {
                newCap = minCapacity
            }
            val newArray = FloatArray(newCap)
            System.arraycopy(array, 0, newArray, 0, size)
            array = newArray
        }
    }
}
