package com.dweekly.cyrinxhil

import java.nio.ByteBuffer
import java.security.MessageDigest
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec
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
    TURBO_QPSK(1),
    TURBO_16QAM(2),
    TURBO_64QAM(3),
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
    private var peerDeviceSignature: Byte = 0x01.toByte()
    private var peerNotchMask = ByteArray(14) { 0xFF.toByte() }
    private var localNotchMask = ByteArray(14) { 0xFF.toByte() }
    private var peerPublicKey: ByteArray? = null
    private var kEnc: ByteArray? = null
    private var kMac: ByteArray? = null
    private var txSequence = 0L

    fun updatePeerSignature(signature: Byte) {
        synchronized(lock) {
            peerDeviceSignature = signature
        }
    }

    fun updatePeerNotchMask(mask: ByteArray) {
        synchronized(lock) {
            peerNotchMask = mask
        }
    }

    fun updateLocalNotchMask(mask: ByteArray) {
        synchronized(lock) {
            localNotchMask = mask
        }
    }

    fun updatePeerPublicKey(key: ByteArray) {
        synchronized(lock) {
            if (peerPublicKey?.contentEquals(key) == true) return
            
            val allZeros = key.all { it == 0.toByte() }
            if (allZeros) {
                peerPublicKey = null
                kEnc = null
                kMac = null
                return
            }
            
            peerPublicKey = key
            val privKey = config.localPrivateKey
            val sharedSecret = X25519.scalarMult(privKey, key)
            
            try {
                val derived = hkdfSHA256(sharedSecret, "CyrinxEncryptionSalt".toByteArray(Charsets.UTF_8), 64)
                kEnc = derived.copyOfRange(0, 32)
                kMac = derived.copyOfRange(32, 64)
            } catch (e: Exception) {
                // Fail-safe
            }
        }
    }

    private fun hkdfSHA256(secret: ByteArray, salt: ByteArray, length: Int): ByteArray {
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(salt, "HmacSHA256"))
        val prk = mac.doFinal(secret)
        
        mac.init(SecretKeySpec(prk, "HmacSHA256"))
        val okm = ByteArray(length)
        var offset = 0
        var t = ByteArray(0)
        var counter = 1
        while (offset < length) {
            mac.update(t)
            mac.update(ByteArray(0)) // info (empty)
            mac.update(counter.toByte())
            t = mac.doFinal()
            val toCopy = minOf(t.size, length - offset)
            System.arraycopy(t, 0, okm, offset, toCopy)
            offset += toCopy
            counter++
        }
        return okm
    }

    private fun encryptCTR(payload: ByteArray, key: ByteArray, sequence: Long): ByteArray {
        val out = ByteArray(payload.size)
        var offset = 0
        var counter = 0
        val md = MessageDigest.getInstance("SHA-256")
        while (offset < payload.size) {
            val inputBlock = ByteBuffer.allocate(12)
                .putLong(sequence)
                .putInt(counter)
                .array()
            md.update(key)
            md.update(inputBlock)
            val keystream = md.digest()
            val toXor = minOf(32, payload.size - offset)
            for (i in 0 until toXor) {
                out[offset + i] = (payload[offset + i].toInt() xor keystream[i].toInt()).toByte()
            }
            offset += toXor
            counter++
        }
        return out
    }

    private fun secureEnvelopePack(payload: ByteArray, k_enc: ByteArray, k_mac: ByteArray, sequence: Long): ByteArray {
        val ciphertext = encryptCTR(payload, k_enc, sequence)
        val envelope = ByteArray(8 + ciphertext.size + 8)
        val seqBytes = ByteBuffer.allocate(8).putLong(sequence).array()
        
        System.arraycopy(seqBytes, 0, envelope, 0, 8)
        System.arraycopy(ciphertext, 0, envelope, 8, ciphertext.size)
        
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(k_mac, "HmacSHA256"))
        mac.update(seqBytes)
        mac.update(ciphertext)
        val signature = mac.doFinal()
        System.arraycopy(signature, 0, envelope, 8 + ciphertext.size, 8)
        return envelope
    }

    private fun secureEnvelopeUnpack(envelope: ByteArray, k_enc: ByteArray, k_mac: ByteArray): ByteArray {
        if (envelope.size < 16) {
            throw IllegalArgumentException("Envelope too short")
        }
        val sequenceBytes = envelope.copyOfRange(0, 8)
        val tagBytes = envelope.copyOfRange(envelope.size - 8, envelope.size)
        val ciphertext = envelope.copyOfRange(8, envelope.size - 8)
        
        val sequence = ByteBuffer.wrap(sequenceBytes).long
        
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(k_mac, "HmacSHA256"))
        mac.update(sequenceBytes)
        mac.update(ciphertext)
        val signature = mac.doFinal()
        
        var isEqual = true
        for (i in 0 until 8) {
            if (tagBytes[i] != signature[i]) {
                isEqual = false
            }
        }
        if (!isEqual) {
            throw SecurityException("Integrity check failed: invalid HMAC tag")
        }
        
        return encryptCTR(ciphertext, k_enc, sequence)
    }


    private val sampleRateHz = config.sampleRateHz.coerceIn(8_000, 192_000)
    private val cappedGain = run {
        val maxCap = if (config.bandStartHz >= 9000) 0.70f else 0.12f
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

    // MIMO 2x2 tracking properties:
    var lastH11 = 0f
        private set
    var lastH12 = 0f
        private set
    var lastH21 = 0f
        private set
    var lastH22 = 0f
        private set
    var lastSigma1 = 0f
        private set
    var lastSigma2 = 0f
        private set
    var lastKappaDb = 0f
        private set
    var lastSpatialMode = 0
        private set

    private val preambleBlock = makePreambleBlock(
        sampleRateHz = sampleRateHz,
        bandStartHz = config.bandStartHz,
        bandEndHz = config.bandEndHz,
        txGainCap = cappedGain,
        root = 29
    )
    private val preambleBlockR = makePreambleBlock(
        sampleRateHz = sampleRateHz,
        bandStartHz = config.bandStartHz,
        bandEndHz = config.bandEndHz,
        txGainCap = cappedGain,
        root = 31
    )
    private val preamble = FloatArray(preambleBlock.size * 2).also {
        System.arraycopy(preambleBlock, 0, it, 0, preambleBlock.size)
        System.arraycopy(preambleBlock, 0, it, preambleBlock.size, preambleBlock.size)
    }
    private val preambleR = FloatArray(preambleBlockR.size * 2).also {
        System.arraycopy(preambleBlockR, 0, it, 0, preambleBlockR.size)
        System.arraycopy(preambleBlockR, 0, it, preambleBlockR.size, preambleBlockR.size)
    }
    private val preambleEnergy = max(1e-7f, preamble.fold(0f) { acc, v -> acc + (v * v) })
    private val syncThreshold = config.preambleSyncThreshold.coerceIn(0.05f, 0.98f)

    private val rxBuffer = FloatBuffer(131072)
    private val rxBufferRight = FloatBuffer(131072)
    private var rxSearchStart = 0

    fun encode(frame: ByteArray): FloatArray {
        require(frame.isNotEmpty() && frame.size <= ACOUSTIC_FRAME_MAX_BYTES) {
            "invalid acoustic frame payload size"
        }

        var payloadToSend = frame
        var encryptKeys = false
        var localKEnc: ByteArray? = null
        var localKMac: ByteArray? = null
        var seq = 0L

        synchronized(lock) {
            if (kEnc != null && kMac != null && frame.isNotEmpty() && frame[0] != 0xE1.toByte()) {
                encryptKeys = true
                localKEnc = kEnc
                localKMac = kMac
                seq = txSequence
                txSequence++
            }
        }

        if (encryptKeys) {
            payloadToSend = secureEnvelopePack(frame, localKEnc!!, localKMac!!, seq)
        }

        val mode = selectBodyMode(payloadToSend)
        val header = AcousticHeader(mode = mode, payloadLength = payloadToSend.size).encode()
        val headerWave = headerModem.modulate(header)

        val bodyWave = when (mode) {
            AcousticBodyMode.TURBO_QPSK -> modulateOfdm(payloadToSend, mode)
            AcousticBodyMode.TURBO_16QAM -> modulateOfdm(payloadToSend, mode)
            AcousticBodyMode.TURBO_64QAM -> modulateOfdm(payloadToSend, mode)
            else -> robustModem.modulate(payloadToSend)
        }


        if (config.channels == 2) {
            val totalLen = preamble.size + headerWave.size + bodyWave.size
            val out = FloatArray(totalLen * 2)
            for (i in 0 until preamble.size) {
                out[i * 2] = preamble[i]
                out[i * 2 + 1] = preambleR[i]
            }
            for (i in 0 until headerWave.size) {
                out[(preamble.size + i) * 2] = headerWave[i]
                out[(preamble.size + i) * 2 + 1] = 0.0f
            }
            for (i in 0 until bodyWave.size) {
                out[(preamble.size + headerWave.size + i) * 2] = bodyWave[i]
                out[(preamble.size + headerWave.size + i) * 2 + 1] = 0.0f
            }
            return out
        } else {
            val out = FloatArray(preamble.size + headerWave.size + bodyWave.size)
            var cursor = 0
            System.arraycopy(preamble, 0, out, cursor, preamble.size)
            cursor += preamble.size
            System.arraycopy(headerWave, 0, out, cursor, headerWave.size)
            cursor += headerWave.size
            System.arraycopy(bodyWave, 0, out, cursor, bodyWave.size)
            return out
        }
    }

    fun ingest(samples: FloatArray): List<AcousticDecodedFrame> {
        if (samples.isEmpty()) {
            return emptyList()
        }
        synchronized(lock) {
            if (config.channels == 2) {
                val halfLen = samples.size / 2
                val left = FloatArray(halfLen)
                val right = FloatArray(halfLen)
                for (i in 0 until halfLen) {
                    left[i] = samples[i * 2]
                    right[i] = samples[i * 2 + 1]
                }
                rxBuffer.addAll(left)
                rxBufferRight.addAll(right)
            } else {
                rxBuffer.addAll(samples)
            }
            return decodeAvailableLocked()
        }
    }

    private fun decodeAvailableLocked(): List<AcousticDecodedFrame> {
        val decoded = ArrayList<AcousticDecodedFrame>()
        val headerSamples = expectedDcssSamples(ACOUSTIC_HEADER_BYTES, headerConfig)

        // Prevent exponential CPU search bottleneck under queue overflow
        val maxBufferSize = sampleRateHz * 12
        if (rxBuffer.size > maxBufferSize) {
            val dropCount = rxBuffer.size - maxBufferSize
            dropFront(dropCount)
            rxSearchStart = max(0, rxSearchStart - dropCount)
        }

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
                dropFront(syncStart + preamble.size / 2)
                rxSearchStart = 0
                continue
            }
            val packetHeader = headerLock.header
            val headerStart = headerLock.start
            val headerEnd = headerLock.end

            val bodySamples = when (packetHeader.mode) {
                AcousticBodyMode.TURBO_QPSK,
                AcousticBodyMode.TURBO_16QAM,
                AcousticBodyMode.TURBO_64QAM -> expectedOfdmSamples(packetHeader.payloadLength, packetHeader.mode)
                else -> expectedDcssSamples(packetHeader.payloadLength, robustConfig)
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
                when (packetHeader.mode) {
                    AcousticBodyMode.TURBO_QPSK,
                    AcousticBodyMode.TURBO_16QAM,
                    AcousticBodyMode.TURBO_64QAM -> demodulateOfdm(bodyWindow, packetHeader.mode, packetHeader.payloadLength)
                    else -> robustModem.demodulate(bodyWindow)
                }
            } catch (t: Throwable) {
                android.util.Log.e("CyrinxHILAndroid", "body demodulation failed", t)
                null
            }
            var frame = bodyPayload
            if (bodyPayload == null || bodyPayload.size != packetHeader.payloadLength) {
                android.util.Log.w("CyrinxHILAndroid", "Demodulation failed or size mismatch: got ${bodyPayload?.size ?: "null"}, expected ${packetHeader.payloadLength}. Dropping to bodyEnd=$bodyEnd")
                dropFront(bodyEnd)
                rxSearchStart = 0
                continue
            }

            var decryptKeys = false
            var localKEnc: ByteArray? = null
            var localKMac: ByteArray? = null
            synchronized(lock) {
                if (kEnc != null && kMac != null && frame.isNotEmpty() && frame[0] != 0xE1.toByte()) {
                    decryptKeys = true
                    localKEnc = kEnc
                    localKMac = kMac
                }
            }

            if (decryptKeys) {
                try {
                    frame = secureEnvelopeUnpack(frame, localKEnc!!, localKMac!!)
                } catch (e: Exception) {
                    android.util.Log.e("CyrinxHILAndroid", "secureEnvelopeUnpack failed: ${e.message}. Dropping to bodyEnd=$bodyEnd")
                    dropFront(bodyEnd)
                    rxSearchStart = 0
                    continue
                }
            }

            decoded.add(
                AcousticDecodedFrame(
                    frame = frame,
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
        val shiftStep = 1
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
        val streamId = readBitsMsb(header, 70, 12)
        if (frameType == CyrinxConstants.FRAME_ACK || frameType == CyrinxConstants.FRAME_CONTROL || streamId == 0) {
            return AcousticBodyMode.ROBUST_DCSS
        }
        val forced = config.forceBodyMode?.trim()?.lowercase(java.util.Locale.US)
        if (forced != null) {
            when (forced) {
                "64qam" -> return AcousticBodyMode.TURBO_64QAM
                "16qam" -> return AcousticBodyMode.TURBO_16QAM
                "qpsk" -> return AcousticBodyMode.TURBO_QPSK
                "robust" -> return AcousticBodyMode.ROBUST_DCSS
            }
        }
        val gearId = readBitsMsb(header, 64, 3)
        return when (gearId) {
            4 -> AcousticBodyMode.TURBO_64QAM
            3 -> AcousticBodyMode.TURBO_16QAM
            2 -> AcousticBodyMode.TURBO_QPSK
            else -> AcousticBodyMode.ROBUST_DCSS
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

        var coarseStart = lowerBound
        while (coarseStart <= searchLimit) {
            var dot = 0f
            var segmentEnergy = 0f
            for (idx in preamble.indices) {
                val sample = buffer.array[coarseStart + idx]
                val reference = preamble[idx]
                dot += sample * reference
                segmentEnergy += sample * sample
            }
            val norm = sqrt(max(segmentEnergy * preambleEnergy, 1e-7f))
            val corr = dot / norm
            if (corr > maxCorrObserved) {
                maxCorrObserved = corr
                maxCorrIndex = coarseStart
            }

            if (corr >= syncThreshold * 0.5f) {
                val fineStart = max(lowerBound, coarseStart - 8)
                val fineEnd = min(searchLimit, coarseStart + 8)

                var bestStart = coarseStart
                var bestCorr = corr
                var bestDot = dot

                for (start in fineStart..fineEnd) {
                    var candidateDot = 0f
                    var candidateEnergy = 0f
                    for (idx in preamble.indices) {
                        val sample = buffer.array[start + idx]
                        val reference = preamble[idx]
                        candidateDot += sample * reference
                        candidateEnergy += sample * sample
                    }
                    val candidateNorm = sqrt(max(candidateEnergy * preambleEnergy, 1e-7f))
                    val candidateCorr = candidateDot / candidateNorm
                    if (candidateCorr > bestCorr) {
                        bestCorr = candidateCorr
                        bestStart = start
                        bestDot = candidateDot
                    }
                }

                if (bestCorr >= syncThreshold) {
                    val finalStart = bestStart
                    var finalStartPeak = bestStart
                    var finalCorr = bestCorr
                    var finalDot = bestDot

                    val windowSize = 48
                    val peakSearchLimit = min(bestStart + windowSize, searchLimit)
                    if (bestStart + 1 <= peakSearchLimit) {
                        for (candidateStart in (bestStart + 1)..peakSearchLimit) {
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
                            if (candidateCorr > finalCorr) {
                                finalCorr = candidateCorr
                                finalStartPeak = candidateStart
                                finalDot = candidateDot
                            }
                        }
                    }

                    // MIMO 2x2 channel sounding and SVD solver
                    if (config.channels == 2) {
                        var dot11 = 0f
                        var dot12 = 0f
                        var dot21 = 0f
                        var dot22 = 0f
                        var energyY1 = 0f
                        var energyY2 = 0f
                        var energyX1 = 0f
                        var energyX2 = 0f

                        for (idx in preamble.indices) {
                            val y1 = buffer.array[finalStartPeak + idx]
                            val y2 = if (finalStartPeak + idx < rxBufferRight.size) rxBufferRight.array[finalStartPeak + idx] else 0f
                            val x1 = preamble[idx]
                            val x2 = preambleR[idx]

                            dot11 += y1 * x1
                            dot12 += y1 * x2
                            dot21 += y2 * x1
                            dot22 += y2 * x2

                            energyY1 += y1 * y1
                            energyY2 += y2 * y2
                            energyX1 += x1 * x1
                            energyX2 += x2 * x2
                        }

                        val h11 = dot11 / sqrt(max(energyY1 * energyX1, 1e-7f))
                        val h12 = dot12 / sqrt(max(energyY1 * energyX2, 1e-7f))
                        val h21 = dot21 / sqrt(max(energyY2 * energyX1, 1e-7f))
                        val h22 = dot22 / sqrt(max(energyY2 * energyX2, 1e-7f))

                        this.lastH11 = h11
                        this.lastH12 = h12
                        this.lastH21 = h21
                        this.lastH22 = h22

                        // SVD Solver
                        val s1 = h11 * h11 + h12 * h12 + h21 * h21 + h22 * h22
                        val det = h11 * h22 - h12 * h21
                        val term = max(0f, s1 * s1 - 4f * det * det)
                        val sqrtTerm = sqrt(term)
                        val l1 = (s1 + sqrtTerm) * 0.5f
                        val l2 = max(0f, (s1 - sqrtTerm) * 0.5f)
                        val sigma1 = sqrt(l1)
                        val sigma2 = sqrt(l2)

                        this.lastSigma1 = sigma1
                        this.lastSigma2 = sigma2

                        val kappaDb = if (sigma2 > 1e-5f) 20f * log10(sigma1 / sigma2) else 99f
                        this.lastKappaDb = kappaDb
                        this.lastSpatialMode = if (kappaDb < 6f) 1 else 0
                    } else {
                        // Mono mode
                        var dot11 = 0f
                        var energyY1 = 0f
                        var energyX1 = 0f
                        for (idx in preamble.indices) {
                            val y1 = buffer.array[finalStartPeak + idx]
                            val x1 = preamble[idx]
                            dot11 += y1 * x1
                            energyY1 += y1 * y1
                            energyX1 += x1 * x1
                        }
                        val h11 = dot11 / sqrt(max(energyY1 * energyX1, 1e-7f))
                        this.lastH11 = h11
                        this.lastH12 = 0f
                        this.lastH21 = 0f
                        this.lastH22 = 0f
                        this.lastSigma1 = h11
                        this.lastSigma2 = 0f
                        this.lastKappaDb = 99f
                        this.lastSpatialMode = 0
                    }

                    val scale = finalDot / preambleEnergy
                    var errorEnergy = 0f
                    for (idx in preamble.indices) {
                        val estimate = scale * preamble[idx]
                        val err = buffer.array[finalStartPeak + idx] - estimate
                        errorEnergy += err * err
                    }

                    val signalPower = max(1e-7f, (scale * scale * preambleEnergy) / preamble.size)
                    val noisePower = max(1e-7f, errorEnergy / preamble.size)
                    val snrDb = 10.0f * log10(signalPower / noisePower)
                    val evmPct = sqrt(noisePower / signalPower) * 100.0f
                    android.util.Log.i("CyrinxHILAndroid", "Preamble lock found! start=$finalStartPeak (first crossing=$finalStart) corr=$finalCorr snrDb=$snrDb")
                    return PreambleLock(index = finalStartPeak, correlation = finalCorr, snrDb = snrDb, evmPct = evmPct)
                }
            }

            coarseStart += 8
        }

        if (maxCorrObserved >= syncThreshold * 0.7f) {
            android.util.Log.d("CyrinxHILAndroid", "maxCorrObserved=$maxCorrObserved at index $maxCorrIndex (threshold=$syncThreshold, searchSize=${searchLimit - lowerBound + 1}, bufferSize=${buffer.size})")
        }
        return null
    }

    private fun channelReport(lockResult: PreambleLock): ChannelReport {
        val estimatedPer = max(0f, min(1f, (0.25f - lockResult.correlation) / 0.25f))
        return ChannelReport(
            snrDb = lockResult.snrDb,
            evmPct = lockResult.evmPct,
            cfoHz = 0f,
            per2s = estimatedPer,
            crcFail = false,
        )
    }

    private fun trimUnlockedBufferForResync(headerWindow: Int) {
        val maxBufferSamples = 72000 // 1.5 seconds
        val keepSamples = 48000 // 1.0 second
        if (rxBuffer.size > maxBufferSamples) {
            val dropped = rxBuffer.size - keepSamples
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
        if (config.channels == 2) {
            rxBufferRight.dropFront(count)
        }
    }

    private fun makePreambleBlock(
        sampleRateHz: Int,
        bandStartHz: Int,
        bandEndHz: Int,
        txGainCap: Float,
        root: Int = 29
    ): FloatArray {
        val zc = zcGenerate(root = root, length = 127) ?: return fallbackPreamble(sampleRateHz, txGainCap)
        val fs = sampleRateHz.toFloat()
        if (fs <= 0f) {
            return fallbackPreamble(48_000, txGainCap)
        }

        val centerHz = (bandStartHz + bandEndHz) * 0.5f
        val chipSpan = 4
        val maxCap = if (bandStartHz >= 9000) 0.70f else 0.12f
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

    private fun modulateOfdm(payload: ByteArray, mode: AcousticBodyMode): FloatArray {
        val bits = BitPacking.encodeLengthPrefixed(payload)
        val bitsPerSymbol = when (mode) {
            AcousticBodyMode.TURBO_16QAM -> 4
            AcousticBodyMode.TURBO_64QAM -> 6
            else -> 2
        }
        val symbols = mapBitsToSymbols(bits, bitsPerSymbol)
        val sig = synchronized(lock) { peerDeviceSignature }
        val mask = synchronized(lock) { peerNotchMask }

        val activeBinsFiltered = mutableListOf<Int>()
        for (i in 0 until ofdmActiveBins.size) {
            val bin = ofdmActiveBins[i]
            val byteIdx = i / 8
            val bitIdx = i % 8
            if (byteIdx < mask.size) {
                val bit = (mask[byteIdx].toInt() ushr (7 - bitIdx)) and 1
                if (bit == 1) {
                    activeBinsFiltered.add(bin)
                }
            } else {
                activeBinsFiltered.add(bin)
            }
        }

        val activeCount = activeBinsFiltered.size
        if (activeCount <= 0) {
            return FloatArray(0)
        }
        val frameCount = ceil(symbols.size.toDouble() / activeCount.toDouble()).toInt()
        val symbolLength = ofdmFftSize + ofdmCpSamples
        val out = FloatArray(frameCount * symbolLength)

        val re = FloatArray(ofdmFftSize)
        val im = FloatArray(ofdmFftSize)
        val timeRe = FloatArray(ofdmFftSize)
        val timeIm = FloatArray(ofdmFftSize)

        val amplitude = min(max(cappedGain, 0f), 0.95f)
        val fftScale = 1.0f / ofdmFftSize.toFloat()
        val scale = amplitude * fftScale
        val binWidth = sampleRateHz.toFloat() / ofdmFftSize.toFloat()

        for (frameIdx in 0 until frameCount) {
            val start = frameIdx * activeCount
            val end = min(start + activeCount, symbols.size)

            re.fill(0f)
            im.fill(0f)

            for (idx in 0 until activeCount) {
                val symbol = if (start + idx < end) symbols[start + idx] else 0.toByte()
                val bin = activeBinsFiltered[idx]

                var eqFactor = 1.0f
                val freq = bin.toFloat() * binWidth
                if (sig.toInt() == 0x01) { // CYRINX_DEVICE_MACBOOK_PRO
                    val x = max(0.0f, min(1.0f, (freq - config.bandStartHz.toFloat()) / max(1.0f, config.bandEndHz.toFloat() - config.bandStartHz.toFloat())))
                    val dbBoost = 3.0f + 9.0f * x
                    eqFactor = java.lang.Math.pow(10.0, (dbBoost / 20.0).toDouble()).toFloat()
                } else if (sig.toInt() == 0x02) { // CYRINX_DEVICE_PIXEL_7A
                    val x = max(0.0f, min(1.0f, (freq - config.bandStartHz.toFloat()) / max(1.0f, config.bandEndHz.toFloat() - config.bandStartHz.toFloat())))
                    val dbBoost = 3.0f + 12.0f * x
                    eqFactor = java.lang.Math.pow(10.0, (dbBoost / 20.0).toDouble()).toFloat()
                }

                val point = when (bitsPerSymbol) {
                    4 -> map16Qam(symbol)
                    6 -> map64Qam(symbol)
                    else -> {
                        val norm = 0.70710677f
                        when (symbol.toInt() and 0x3) {
                            0 -> Pair(norm, norm)
                            1 -> Pair(-norm, norm)
                            2 -> Pair(norm, -norm)
                            else -> Pair(-norm, -norm)
                        }
                    }
                }

                re[bin] = point.first * eqFactor
                im[bin] = point.second * eqFactor

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

            var maxPeak = 0.0f
            for (i in 0 until ofdmFftSize) {
                val absV = abs(timeRe[i])
                if (absV > maxPeak) {
                    maxPeak = absV
                }
            }
            var finalScale = 0.0f
            if (maxPeak > 1e-7f) {
                finalScale = amplitude / maxPeak
            }

            val outStart = frameIdx * symbolLength
            val cpStart = ofdmFftSize - ofdmCpSamples
            for (i in 0 until ofdmCpSamples) {
                out[outStart + i] = timeRe[cpStart + i] * finalScale
            }
            for (i in 0 until ofdmFftSize) {
                out[outStart + ofdmCpSamples + i] = timeRe[i] * finalScale
            }
        }
        return out
    }

    private fun demodulateOfdm(samples: FloatArray, mode: AcousticBodyMode, expectedLength: Int? = null): ByteArray? {
        val symbolLength = ofdmFftSize + ofdmCpSamples
        if (samples.size < symbolLength) {
            return null
        }
        val frameCount = samples.size / symbolLength
        val mask = synchronized(lock) { localNotchMask }

        val activeBinsFiltered = mutableListOf<Int>()
        for (i in 0 until ofdmActiveBins.size) {
            val bin = ofdmActiveBins[i]
            val byteIdx = i / 8
            val bitIdx = i % 8
            if (byteIdx < mask.size) {
                val bit = (mask[byteIdx].toInt() ushr (7 - bitIdx)) and 1
                if (bit == 1) {
                    activeBinsFiltered.add(bin)
                }
            } else {
                activeBinsFiltered.add(bin)
            }
        }

        val activeCount = activeBinsFiltered.size
        if (activeCount <= 0) {
            return null
        }
        val bitsPerSymbol = when (mode) {
            AcousticBodyMode.TURBO_16QAM -> 4
            AcousticBodyMode.TURBO_64QAM -> 6
            else -> 2
        }

        val binFactors = FloatArray(ofdmFftSize)
        val multiplier = 2.0f * Math.PI.toFloat() / ofdmFftSize
        for (i in 0 until activeCount) {
            val bin = activeBinsFiltered[i]
            binFactors[bin] = bin * multiplier
        }

        val equalizedFrames = ArrayList<Pair<FloatArray, FloatArray>>(frameCount)
        var sharedTau = 0.0f
        var sharedConj = false

        for (frameIdx in 0 until frameCount) {
            val start = (frameIdx * symbolLength) + ofdmCpSamples
            val re = FloatArray(ofdmFftSize)
            val im = FloatArray(ofdmFftSize)

            System.arraycopy(samples, start, re, 0, ofdmFftSize)
            im.fill(0f)

            fft.transform(re, im, forward = true)

            var totalEnergy = 0f
            for (idx in 0 until activeCount) {
                val bin = activeBinsFiltered[idx]
                totalEnergy += re[bin] * re[bin] + im[bin] * im[bin]
            }
            val avgEnergy = totalEnergy / max(1, activeCount)
            val normFactor = sqrt(max(avgEnergy, 1e-7f))

            // Pre-divide active bins by normFactor once.
            val normRe = FloatArray(ofdmFftSize)
            val normIm = FloatArray(ofdmFftSize)
            for (i in 0 until activeCount) {
                val bin = activeBinsFiltered[i]
                normRe[bin] = re[bin] / normFactor
                normIm[bin] = im[bin] / normFactor
            }

            var bestTau = 0.0f
            var bestTheta = 0.0f
            var bestConj = false
            var minMSE = Float.MAX_VALUE

            if (frameIdx == 0) {
                val tauCoarse = floatArrayOf(-8.0f, -6.0f, -4.0f, -2.0f, 0.0f, 2.0f, 4.0f, 6.0f, 8.0f)
                val thetaCoarseIdx = intArrayOf(0, 4, 8, 12, 16)

                var bestCoarseTau = 0.0f
                var bestCoarseThetaIdx = 8
                var bestCoarseConj = false
                var minCoarseMSE = Float.MAX_VALUE

                for (conj in listOf(false, true)) {
                    for (tau in tauCoarse) {
                        for (thetaIdx in thetaCoarseIdx) {
                            val theta = -Math.PI.toFloat() / 4.0f + (thetaIdx * Math.PI.toFloat() / 32.0f)
                            var sumError = 0f

                            for (i in 0 until activeCount) {
                                val bin = activeBinsFiltered[i]
                                val r = normRe[bin]
                                val iVal = if (conj) -normIm[bin] else normIm[bin]

                                val phi = theta + binFactors[bin] * tau
                                val cosPhi = kotlin.math.cos(phi)
                                val sinPhi = kotlin.math.sin(phi)

                                val rRot = r * cosPhi + iVal * sinPhi
                                val iRot = iVal * cosPhi - r * sinPhi

                                val err = when (bitsPerSymbol) {
                                    4 -> {
                                        val d = 0.31622777f
                                        val absR = kotlin.math.abs(rRot)
                                        val errR = if (absR < 2.0f * d) kotlin.math.abs(absR - d) else kotlin.math.abs(absR - 3.0f * d)
                                        val absI = kotlin.math.abs(iRot)
                                        val errI = if (absI < 2.0f * d) kotlin.math.abs(absI - d) else kotlin.math.abs(absI - 3.0f * d)
                                        errR * errR + errI * errI
                                    }
                                    6 -> {
                                        val d = 0.15430335f
                                        val absR = kotlin.math.abs(rRot)
                                        val errR = when {
                                            absR < 2.0f * d -> kotlin.math.abs(absR - d)
                                            absR < 4.0f * d -> kotlin.math.abs(absR - 3.0f * d)
                                            absR < 6.0f * d -> kotlin.math.abs(absR - 5.0f * d)
                                            else -> kotlin.math.abs(absR - 7.0f * d)
                                        }
                                        val absI = kotlin.math.abs(iRot)
                                        val errI = when {
                                            absI < 2.0f * d -> kotlin.math.abs(absI - d)
                                            absI < 4.0f * d -> kotlin.math.abs(absI - 3.0f * d)
                                            absI < 6.0f * d -> kotlin.math.abs(absI - 5.0f * d)
                                            else -> kotlin.math.abs(absI - 7.0f * d)
                                        }
                                        errR * errR + errI * errI
                                    }
                                    else -> {
                                        val norm = 0.70710677f
                                        val errR = kotlin.math.abs(rRot) - norm
                                        val errI = kotlin.math.abs(iRot) - norm
                                        errR * errR + errI * errI
                                    }
                                }
                                sumError += err
                            }

                            val penalty = 0.0001f * (tau * tau) + 0.00005f * (theta * theta)
                            val score = sumError + penalty
                            if (score < minCoarseMSE) {
                                minCoarseMSE = score
                                bestCoarseTau = tau
                                bestCoarseThetaIdx = thetaIdx
                                bestCoarseConj = conj
                            }
                        }
                    }
                }

                // Fine search around the best coarse point
                val tauFine = ArrayList<Float>()
                for (tOffset in floatArrayOf(-1.0f, -0.75f, -0.5f, -0.25f, 0.0f, 0.25f, 0.5f, 0.75f, 1.0f)) {
                    val t = bestCoarseTau + tOffset
                    if (t >= -8.0f && t <= 8.0f) {
                        tauFine.add(t)
                    }
                }

                val thetaFineIdx = ArrayList<Int>()
                for (idxOffset in intArrayOf(-2, -1, 0, 1, 2)) {
                    val idx = bestCoarseThetaIdx + idxOffset
                    if (idx >= 0 && idx <= 16) {
                        thetaFineIdx.add(idx)
                    }
                }

                bestConj = bestCoarseConj
                for (tau in tauFine) {
                    for (thetaIdx in thetaFineIdx) {
                        val theta = -Math.PI.toFloat() / 4.0f + (thetaIdx * Math.PI.toFloat() / 32.0f)
                        var sumError = 0f

                        for (i in 0 until activeCount) {
                            val bin = activeBinsFiltered[i]
                            val r = normRe[bin]
                            val iVal = if (bestConj) -normIm[bin] else normIm[bin]

                            val phi = theta + binFactors[bin] * tau
                            val cosPhi = kotlin.math.cos(phi)
                            val sinPhi = kotlin.math.sin(phi)

                            val rRot = r * cosPhi + iVal * sinPhi
                            val iRot = iVal * cosPhi - r * sinPhi

                            val err = when (bitsPerSymbol) {
                                4 -> {
                                    val d = 0.31622777f
                                    val absR = kotlin.math.abs(rRot)
                                    val errR = if (absR < 2.0f * d) kotlin.math.abs(absR - d) else kotlin.math.abs(absR - 3.0f * d)
                                    val absI = kotlin.math.abs(iRot)
                                    val errI = if (absI < 2.0f * d) kotlin.math.abs(absI - d) else kotlin.math.abs(absI - 3.0f * d)
                                    errR * errR + errI * errI
                                }
                                6 -> {
                                    val d = 0.15430335f
                                    val absR = kotlin.math.abs(rRot)
                                    val errR = when {
                                        absR < 2.0f * d -> kotlin.math.abs(absR - d)
                                        absR < 4.0f * d -> kotlin.math.abs(absR - 3.0f * d)
                                        absR < 6.0f * d -> kotlin.math.abs(absR - 5.0f * d)
                                        else -> kotlin.math.abs(absR - 7.0f * d)
                                    }
                                    val absI = kotlin.math.abs(iRot)
                                    val errI = when {
                                        absI < 2.0f * d -> kotlin.math.abs(absI - d)
                                        absI < 4.0f * d -> kotlin.math.abs(absI - 3.0f * d)
                                        absI < 6.0f * d -> kotlin.math.abs(absI - 5.0f * d)
                                        else -> kotlin.math.abs(absI - 7.0f * d)
                                    }
                                    errR * errR + errI * errI
                                }
                                else -> {
                                    val norm = 0.70710677f
                                    val errR = kotlin.math.abs(rRot) - norm
                                    val errI = kotlin.math.abs(iRot) - norm
                                    errR * errR + errI * errI
                                }
                            }
                            sumError += err
                        }

                        val penalty = 0.0001f * (tau * tau) + 0.00005f * (theta * theta)
                        val score = sumError + penalty
                        if (score < minMSE) {
                            minMSE = score
                            bestTau = tau
                            bestTheta = theta
                        }
                    }
                }

                sharedTau = bestTau
                sharedConj = bestConj
            } else {
                bestTau = sharedTau
                bestConj = sharedConj
                for (thetaIdx in 0..16) {
                    val theta = -Math.PI.toFloat() / 4.0f + (thetaIdx * Math.PI.toFloat() / 32.0f)
                    var sumError = 0f

                    for (i in 0 until activeCount) {
                        val bin = activeBinsFiltered[i]
                        val r = normRe[bin]
                        val iVal = if (bestConj) -normIm[bin] else normIm[bin]

                        val phi = theta + binFactors[bin] * bestTau
                        val cosPhi = kotlin.math.cos(phi)
                        val sinPhi = kotlin.math.sin(phi)

                        val rRot = r * cosPhi + iVal * sinPhi
                        val iRot = iVal * cosPhi - r * sinPhi

                        val err = when (bitsPerSymbol) {
                            4 -> {
                                val d = 0.31622777f
                                val absR = kotlin.math.abs(rRot)
                                val errR = if (absR < 2.0f * d) kotlin.math.abs(absR - d) else kotlin.math.abs(absR - 3.0f * d)
                                val absI = kotlin.math.abs(iRot)
                                val errI = if (absI < 2.0f * d) kotlin.math.abs(absI - d) else kotlin.math.abs(absI - 3.0f * d)
                                errR * errR + errI * errI
                            }
                            6 -> {
                                val d = 0.15430335f
                                val absR = kotlin.math.abs(rRot)
                                val errR = when {
                                    absR < 2.0f * d -> kotlin.math.abs(absR - d)
                                    absR < 4.0f * d -> kotlin.math.abs(absR - 3.0f * d)
                                    absR < 6.0f * d -> kotlin.math.abs(absR - 5.0f * d)
                                    else -> kotlin.math.abs(absR - 7.0f * d)
                                }
                                val absI = kotlin.math.abs(iRot)
                                val errI = when {
                                    absI < 2.0f * d -> kotlin.math.abs(absI - d)
                                    absI < 4.0f * d -> kotlin.math.abs(absI - 3.0f * d)
                                    absI < 6.0f * d -> kotlin.math.abs(absI - 5.0f * d)
                                    else -> kotlin.math.abs(absI - 7.0f * d)
                                }
                                errR * errR + errI * errI
                            }
                            else -> {
                                val norm = 0.70710677f
                                val errR = kotlin.math.abs(rRot) - norm
                                val errI = kotlin.math.abs(iRot) - norm
                                errR * errR + errI * errI
                            }
                        }
                        sumError += err
                    }

                    val penalty = 0.0001f * (bestTau * bestTau) + 0.00005f * (theta * theta)
                    val score = sumError + penalty
                    if (score < minMSE) {
                        minMSE = score
                        bestTheta = theta
                    }
                }
            }

            android.util.Log.i(
                "CyrinxHILAndroid",
                "demodulateOfdm Frame $frameIdx: avgEnergy=$avgEnergy, bestTau=$bestTau, " +
                "bestTheta=$bestTheta, bestConj=$bestConj, minMSE=$minMSE"
            )

            val eqRe = FloatArray(ofdmFftSize)
            val eqIm = FloatArray(ofdmFftSize)
            for (i in 0 until ofdmFftSize) {
                eqRe[i] = re[i]
                eqIm[i] = if (bestConj) -im[i] else im[i]
            }
            for (i in 0 until activeCount) {
                val bin = activeBinsFiltered[i]
                val phi = bestTheta + binFactors[bin] * bestTau
                val cosPhi = kotlin.math.cos(phi)
                val sinPhi = kotlin.math.sin(phi)
                val r = re[bin]
                val iVal = if (bestConj) -im[bin] else im[bin]
                eqRe[bin] = r * cosPhi + iVal * sinPhi
                eqIm[bin] = iVal * cosPhi - r * sinPhi
            }

            equalizedFrames.add(Pair(eqRe, eqIm))
        }

        // Try both standard and conjugate-inverted constellations across 4 quadrant rotations
        for (conj in listOf(false, true)) {
            for (q in 0..3) {
                val bits = ByteArray(frameCount * activeCount * bitsPerSymbol)
                var bitIdx = 0

                for (frameIdx in 0 until frameCount) {
                    val (eqRe, eqIm) = equalizedFrames[frameIdx]
                    var activeEnergy = 0f
                    for (idx in 0 until activeCount) {
                        val bin = activeBinsFiltered[idx]
                        activeEnergy += eqRe[bin] * eqRe[bin] + eqIm[bin] * eqIm[bin]
                    }
                    val avgEnergy = activeEnergy / max(1, activeCount)
                    val normFactor = sqrt(max(avgEnergy, 1e-7f))

                    for (idx in 0 until activeCount) {
                        val bin = activeBinsFiltered[idx]
                        val reNorm = eqRe[bin] / normFactor
                        var imNorm = eqIm[bin] / normFactor

                        if (conj) {
                            imNorm = -imNorm
                        }

                        var rRot = reNorm
                        var iRot = imNorm

                        when (q) {
                            1 -> { // 90 degrees CCW: (x, y) -> (-y, x)
                                val tmp = rRot
                                rRot = -iRot
                                iRot = tmp
                            }
                            2 -> { // 180 degrees: (x, y) -> (-x, -y)
                                rRot = -rRot
                                iRot = -iRot
                            }
                            3 -> { // 270 degrees CCW: (x, y) -> (y, -x)
                                val tmp = rRot
                                rRot = iRot
                                iRot = -tmp
                            }
                        }

                        val symbol = when (bitsPerSymbol) {
                            4 -> demap16Qam(rRot, iRot)
                            6 -> demap64Qam(rRot, iRot)
                            else -> demapQpsk(rRot, iRot)
                        }.toInt()

                        for (shift in (bitsPerSymbol - 1) downTo 0) {
                            bits[bitIdx++] = ((symbol ushr shift) and 1).toByte()
                        }
                    }
                }

                val payload = BitPacking.decodeLengthPrefixed(bits)
                if (payload != null) {
                    if (expectedLength != null) {
                        if (payload.size == expectedLength) {
                            android.util.Log.i(
                                "CyrinxHILAndroid",
                                "OFDM Demod q=$q conj=$conj success expected size $expectedLength"
                            )
                            return payload
                        } else {
                            android.util.Log.d(
                                "CyrinxHILAndroid",
                                "OFDM Demod q=$q conj=$conj got ${payload.size} exp=$expectedLength"
                            )
                        }
                    } else {
                        android.util.Log.i(
                            "CyrinxHILAndroid",
                            "OFDM Demod q=$q conj=$conj success size ${payload.size}"
                        )
                        return payload
                    }
                }
            }
        }

        // Fallback to q = 0, conj = false
        val bits = ByteArray(frameCount * activeCount * bitsPerSymbol)
        var bitIdx = 0
        for (frameIdx in 0 until frameCount) {
            val (eqRe, eqIm) = equalizedFrames[frameIdx]
            var activeEnergy = 0f
            for (idx in 0 until activeCount) {
                val bin = activeBinsFiltered[idx]
                activeEnergy += eqRe[bin] * eqRe[bin] + eqIm[bin] * eqIm[bin]
            }
            val avgEnergy = activeEnergy / max(1, activeCount)
            val normFactor = sqrt(max(avgEnergy, 1e-7f))

            for (idx in 0 until activeCount) {
                val bin = activeBinsFiltered[idx]
                val reNorm = eqRe[bin] / normFactor
                val imNorm = eqIm[bin] / normFactor

                val symbol = when (bitsPerSymbol) {
                    4 -> demap16Qam(reNorm, imNorm)
                    6 -> demap64Qam(reNorm, imNorm)
                    else -> demapQpsk(reNorm, imNorm)
                }.toInt()

                for (shift in (bitsPerSymbol - 1) downTo 0) {
                    bits[bitIdx++] = ((symbol ushr shift) and 1).toByte()
                }
            }
        }
        return BitPacking.decodeLengthPrefixed(bits)
    }

    private fun expectedOfdmSamples(payloadBytes: Int, mode: AcousticBodyMode): Int {
        if (payloadBytes < 0 || ofdmFftSize <= 0 || ofdmCpSamples <= 0) {
            return 0
        }
        if (ofdmActiveCarrierCount <= 0) {
            return 0
        }
        val bitsPerSymbol = when (mode) {
            AcousticBodyMode.TURBO_16QAM -> 4
            AcousticBodyMode.TURBO_64QAM -> 6
            else -> 2
        }
        val bitCount = (payloadBytes + 2) * 8
        val symbolsCount = (bitCount + bitsPerSymbol - 1) / bitsPerSymbol
        val frameCount = max(1, (symbolsCount + ofdmActiveCarrierCount - 1) / ofdmActiveCarrierCount)
        return frameCount * (ofdmFftSize + ofdmCpSamples)
    }

    private fun demapQpsk(re: Float, im: Float): Byte {
        if (re >= 0f && im >= 0f) return 0
        if (re < 0f && im >= 0f) return 1
        if (re >= 0f && im < 0f) return 2
        return 3
    }

    private fun map16Qam(symbol: Byte): Pair<Float, Float> {
        val d = 0.31622777f
        val b = symbol.toInt() and 0xF
        val iBits = b and 0x3
        val qBits = (b ushr 2) and 0x3

        val re = when (iBits) {
            0 -> -3.0f * d
            1 -> -1.0f * d
            3 -> 1.0f * d
            else -> 3.0f * d
        }

        val im = when (qBits) {
            0 -> -3.0f * d
            1 -> -1.0f * d
            3 -> 1.0f * d
            else -> 3.0f * d
        }

        return Pair(re, im)
    }

    private fun demap16Qam(re: Float, im: Float): Byte {
        val d = 0.31622777f
        val iVal = if (re < -2.0f * d) {
            0
        } else if (re < 0.0f) {
            1
        } else if (re < 2.0f * d) {
            3
        } else {
            2
        }

        val qVal = if (im < -2.0f * d) {
            0
        } else if (im < 0.0f) {
            1
        } else if (im < 2.0f * d) {
            3
        } else {
            2
        }

        return (iVal or (qVal shl 2)).toByte()
    }

    private fun map64Qam(symbol: Byte): Pair<Float, Float> {
        val d = 0.15430335f
        val b = symbol.toInt() and 0x3F
        val iBits = b and 0x7
        val qBits = (b ushr 3) and 0x7

        val re = when (iBits) {
            0 -> -7.0f * d
            1 -> -5.0f * d
            2 -> -3.0f * d
            3 -> -1.0f * d
            4 -> 1.0f * d
            5 -> 3.0f * d
            6 -> 5.0f * d
            else -> 7.0f * d
        }

        val im = when (qBits) {
            0 -> -7.0f * d
            1 -> -5.0f * d
            2 -> -3.0f * d
            3 -> -1.0f * d
            4 -> 1.0f * d
            5 -> 3.0f * d
            6 -> 5.0f * d
            else -> 7.0f * d
        }

        return Pair(re, im)
    }

    private fun demap64Qam(re: Float, im: Float): Byte {
        val d = 0.15430335f
        val iVal = if (re < -6.0f * d) 0
        else if (re < -4.0f * d) 1
        else if (re < -2.0f * d) 2
        else if (re < 0.0f) 3
        else if (re < 2.0f * d) 4
        else if (re < 4.0f * d) 5
        else if (re < 6.0f * d) 6
        else 7

        val qVal = if (im < -6.0f * d) 0
        else if (im < -4.0f * d) 1
        else if (im < -2.0f * d) 2
        else if (im < 0.0f) 3
        else if (im < 2.0f * d) 4
        else if (im < 4.0f * d) 5
        else if (im < 6.0f * d) 6
        else 7

        return (iVal or (qVal shl 3)).toByte()
    }

    private fun mapBitsToSymbols(bits: ByteArray, bitsPerSymbol: Int): ByteArray {
        if (bits.isEmpty()) {
            return byteArrayOf()
        }
        val symbols = ByteArray((bits.size + bitsPerSymbol - 1) / bitsPerSymbol)
        var index = 0
        var symIdx = 0
        while (index < bits.size) {
            var symbol = 0
            for (shift in (bitsPerSymbol - 1) downTo 0) {
                val bit = if (index < bits.size) bits[index].toInt() and 1 else 0
                symbol = (symbol shl 1) or bit
                index++
            }
            symbols[symIdx++] = symbol.toByte()
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
                android.util.Log.e("CyrinxHILAndroid", "decodeLengthPrefixed: bits.size (${bits.size}) < 16")
                return null
            }
            val length = bitsToUInt16(bits, 0)
            val requiredBits = 16 + (length * 8)
            if (bits.size < requiredBits) {
                android.util.Log.w("CyrinxHILAndroid", "decodeLengthPrefixed: bits.size (${bits.size}) < required ($requiredBits), length prefix was $length")
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

    fun calculateTHD(samples: FloatArray, sampleRateHz: Int, fundamentalHz: Float): Float {
        val n = 2048
        if (samples.size < n) return 0f

        val real = FloatArray(n) { i -> samples[i] }
        val imag = FloatArray(n) { 0f }

        val fft = FFT(n)
        fft.transform(real, imag, forward = true)

        val binWidth = sampleRateHz.toFloat() / n.toFloat()

        fun getPowerAtFreq(freq: Float): Float {
            val centerBin = kotlin.math.round(freq / binWidth).toInt()
            var powerSum = 0f
            for (b in (centerBin - 1)..(centerBin + 1)) {
                if (b in 0 until (n / 2)) {
                    powerSum += real[b] * real[b] + imag[b] * imag[b]
                }
            }
            return powerSum
        }

        val pFund = getPowerAtFreq(fundamentalHz)
        if (pFund <= 1e-9f) return 0f

        var pHarmonics = 0f
        var harmonicMultiplier = 2
        while (true) {
            val harmFreq = fundamentalHz * harmonicMultiplier
            if (harmFreq >= sampleRateHz / 2.0f) {
                break
            }
            pHarmonics += getPowerAtFreq(harmFreq)
            harmonicMultiplier++
        }

        return kotlin.math.sqrt(pHarmonics / pFund) * 100.0f
    }

    fun generateSineTone(frequencyHz: Float, durationSecs: Float, sampleRateHz: Float, amplitude: Float): FloatArray {
        val count = (sampleRateHz * durationSecs).toInt()
        return FloatArray(count) { idx ->
            val phase = 2.0f * Math.PI.toFloat() * frequencyHz * idx / sampleRateHz
            amplitude * kotlin.math.sin(phase)
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
