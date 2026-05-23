package com.dweekly.cyrinxhil

import android.os.SystemClock
import kotlin.math.min
import kotlin.random.Random

private data class FrameHeader(
    val version: Int,
    val frameType: Int,
    val sessionId: Int,
    val seq: Int,
    val ack: Int,
    val gearId: Int,
    val fecRate: Int,
    val streamId: Int,
    val priority: Int,
    val payloadLen: Int,
    val flags: Int,
)

class CyrinxTransportSession(
    private val config: SessionConfig,
    private val txSink: FrameTxSink,
    private val log: (String) -> Unit = {},
) {
    private val signal = Object()

    private var started = false
    private var sessionId = Random.nextInt(0x1000000)

    private var currentGear = 0
    private var nextTxSeq = 0
    private var lastRxSeq = 0

    var peerMicsCount = 1
        private set
    var peerSpeakersCount = 1
        private set
    private var lastReplyMs = 0L

    private var awaitingAck = false
    private var awaitingAckSeq = 0
    private var ackDeadlineMs = 0L

    private var reassemblyBuffer = ByteArray(0)
    private var reassemblyStreamId = 0
    private var reassemblyPriority = 0
    private var reassemblyFlags = 0
    private var reassemblyActive = false

    private val rxQueue = ArrayDeque<CyrinxReceivedMessage>()

    private var lastReport = ChannelReport()

    private val metricsState = CyrinxMetrics(currentGear = 0)

    fun start(): Int {
        synchronized(signal) {
            if (started) {
                return CyrinxStatus.OK
            }
            started = true
            currentGear = 0
            metricsState.currentGear = 0
            log("event=discovery")
            return CyrinxStatus.OK
        }
    }

    fun stop() {
        synchronized(signal) {
            started = false
            awaitingAck = false
            rxQueue.clear()
            reassemblyBuffer = ByteArray(0)
            reassemblyActive = false
            signal.notifyAll()
        }
    }

    fun metrics(): CyrinxMetrics {
        synchronized(signal) {
            return metricsState.copy()
        }
    }

    fun send(
        data: ByteArray,
        streamId: Int,
        qos: QoS,
        priority: Int = 1,
        streamFlags: Int = 0,
    ): Int {
        if (data.isEmpty() || data.size > CyrinxConstants.MAX_LOGICAL_MESSAGE) {
            return CyrinxStatus.ERR_INVALID_ARGUMENT
        }
        if (streamId <= CyrinxConstants.STREAM_CONTROL || streamId > CyrinxConstants.STREAM_ID_MAX) {
            return CyrinxStatus.ERR_INVALID_ARGUMENT
        }
        if (priority !in 0..CyrinxConstants.STREAM_PRIORITY_MAX) {
            return CyrinxStatus.ERR_INVALID_ARGUMENT
        }
        synchronized(signal) {
            if (!started) {
                return CyrinxStatus.ERR_NOT_RUNNING
            }
        }

        val appFlags = streamFlags and (CyrinxConstants.STREAM_FLAG_FIN or CyrinxConstants.STREAM_FLAG_RST)
        var sent = 0
        var fragmentIndex = 0

        while (sent < data.size) {
            val remaining = data.size - sent
            val fragLen = min(remaining, CyrinxConstants.MAX_FRAME_PAYLOAD)

            var flags = 0
            if (fragmentIndex == 0) {
                flags = flags or CyrinxConstants.FLAG_FRAG_START
            }
            if (sent + fragLen == data.size) {
                flags = flags or CyrinxConstants.FLAG_FRAG_END
                flags = flags or appFlags
            }

            val seq: Int
            synchronized(signal) {
                seq = nextTxSeq and 0xFFFF
                nextTxSeq = (nextTxSeq + 1) and 0xFFFF
            }

            var attempts = 0
            var delivered = false

            do {
                synchronized(signal) {
                    metricsState.retransmissionActive = attempts > 0
                }

                val rc = sendInternal(
                    frameType = CyrinxConstants.FRAME_DATA,
                    seq = seq,
                    ack = synchronized(signal) { lastRxSeq },
                    streamId = streamId,
                    priority = priority,
                    flags = flags,
                    payload = data.copyOfRange(sent, sent + fragLen),
                    trackAck = qos == QoS.RELIABLE,
                )
                if (rc != CyrinxStatus.OK) {
                    synchronized(signal) {
                        metricsState.retransmissionActive = false
                    }
                    return rc
                }

                if (qos != QoS.RELIABLE) {
                    delivered = true
                    break
                }

                while (true) {
                    val now = SystemClock.elapsedRealtime()
                    val done: Boolean
                    synchronized(signal) {
                        done = !awaitingAck || now >= ackDeadlineMs
                    }
                    if (done) {
                        break
                    }
                    Thread.sleep(1)
                }

                synchronized(signal) {
                    if (!awaitingAck) {
                        delivered = true
                    }
                }

                if (delivered) {
                    break
                }

                attempts += 1
                synchronized(signal) {
                    metricsState.txRetries += 1
                    metricsState.per2s = 1.0f
                }
            } while (attempts <= 4)

            synchronized(signal) {
                metricsState.retransmissionActive = false
            }

            if (!delivered) {
                synchronized(signal) {
                    awaitingAck = false
                    metricsState.linkResets += 1
                    currentGear = 0
                    metricsState.currentGear = 0
                }
                log("event=failed timeout")
                return CyrinxStatus.ERR_TIMEOUT
            }

            sent += fragLen
            fragmentIndex += 1
            synchronized(signal) {
                metricsState.per2s *= 0.95f
            }
        }

        return CyrinxStatus.OK
    }

    fun receive(timeoutMs: Long): CyrinxReceivedMessage? {
        synchronized(signal) {
            if (!started) {
                return null
            }
            if (rxQueue.isNotEmpty()) {
                return rxQueue.removeFirst()
            }

            if (timeoutMs == 0L) {
                return null
            }

            val deadline = SystemClock.elapsedRealtime() + timeoutMs
            while (rxQueue.isEmpty()) {
                val remaining = deadline - SystemClock.elapsedRealtime()
                if (remaining <= 0L) {
                    return null
                }
                signal.wait(min(remaining, 50L))
            }
            return rxQueue.removeFirst()
        }
    }

    fun ingestFrame(frame: ByteArray, report: ChannelReport?): Int {
        synchronized(signal) {
            if (!started) {
                return CyrinxStatus.ERR_NOT_RUNNING
            }
        }

        if (frame.size < (CyrinxConstants.FIXED_PREAMBLE_BYTES + CyrinxConstants.HEADER_BYTES + CyrinxConstants.FRAME_CRC_BYTES)) {
            return CyrinxStatus.ERR_INVALID_ARGUMENT
        }

        if ((frame[0].toInt() and 0xFF) != CyrinxConstants.MAGIC0 || (frame[1].toInt() and 0xFF) != CyrinxConstants.MAGIC1) {
            return CyrinxStatus.ERR_CRC
        }

        val expectedCrc = readU32BE(frame, frame.size - 4)
        val actualCrc = crc32c(frame, 0, frame.size - 4)
        if (expectedCrc != actualCrc) {
            synchronized(signal) {
                metricsState.crcFailures += 1
            }
            return CyrinxStatus.ERR_CRC
        }

        val headerBytes = frame.copyOfRange(CyrinxConstants.FIXED_PREAMBLE_BYTES, CyrinxConstants.FIXED_PREAMBLE_BYTES + CyrinxConstants.HEADER_BYTES)
        val header = parseHeader(headerBytes) ?: run {
            synchronized(signal) {
                metricsState.crcFailures += 1
            }
            return CyrinxStatus.ERR_CRC
        }

        val payloadStart = CyrinxConstants.FIXED_PREAMBLE_BYTES + CyrinxConstants.HEADER_BYTES
        val payloadEnd = payloadStart + header.payloadLen
        if (payloadEnd + CyrinxConstants.FRAME_CRC_BYTES != frame.size) {
            return CyrinxStatus.ERR_INVALID_ARGUMENT
        }
        val payload = frame.copyOfRange(payloadStart, payloadEnd)

        if (header.frameType == CyrinxConstants.FRAME_DATA) {
            if (header.streamId == CyrinxConstants.STREAM_CONTROL && payload.size == 4 && payload[0] == 0xE1.toByte()) {
                // Handshake message! Save peer capacities.
                synchronized(signal) {
                    peerMicsCount = payload[2].toInt() and 0xFF
                    peerSpeakersCount = payload[3].toInt() and 0xFF
                }
                log("handshake received from peer: mics=$peerMicsCount, speakers=$peerSpeakersCount")

                // Reply with our own capacities if peer initiated
                val now = SystemClock.elapsedRealtime()
                if (now - lastReplyMs > 1000) {
                    lastReplyMs = now
                    val capPayload = byteArrayOf(
                        0xE1.toByte(),
                        1.toByte(),
                        config.channels.toByte(),
                        config.channels.toByte()
                    )
                    val seq = synchronized(signal) {
                        val s = nextTxSeq
                        nextTxSeq = (nextTxSeq + 1) and 0xFFFF
                        s
                    }
                    sendInternal(
                        frameType = CyrinxConstants.FRAME_DATA,
                        seq = seq,
                        ack = header.seq,
                        streamId = CyrinxConstants.STREAM_CONTROL,
                        priority = 3,
                        flags = 0,
                        payload = capPayload,
                        trackAck = false
                    )
                }

                // Send immediate ACK
                val lastReportSnapshot = synchronized(signal) { lastReport }
                val ackPayload = encodeAckReport(lastReportSnapshot)
                val seq = synchronized(signal) {
                    val s = nextTxSeq
                    nextTxSeq = (nextTxSeq + 1) and 0xFFFF
                    s
                }
                sendInternal(
                    frameType = CyrinxConstants.FRAME_ACK,
                    seq = seq,
                    ack = header.seq,
                    streamId = CyrinxConstants.STREAM_CONTROL,
                    priority = 3,
                    flags = 0,
                    payload = ackPayload,
                    trackAck = false
                )
                return CyrinxStatus.OK
            }
        }

        synchronized(signal) {
            if (report != null) {
                applyChannelReportLocked(report)
            }
            lastRxSeq = header.seq
            metricsState.rxFrames += 1
            val oldGear = currentGear
            var triggerBroadcast = false
            if (config.role == Role.SLAVE) {
                if (currentGear != header.gearId) {
                    currentGear = header.gearId
                    metricsState.currentGear = header.gearId
                    log("slave shifting gear from $oldGear to $currentGear")
                }
                if (oldGear == 0) {
                    log("event=linked")
                    triggerBroadcast = true
                }
            } else {
                if (currentGear == 0) {
                    currentGear = 1
                    metricsState.currentGear = 1
                    log("event=linked")
                    triggerBroadcast = true
                }
            }
            if (triggerBroadcast) {
                val capPayload = byteArrayOf(
                    0xE1.toByte(),
                    1.toByte(),
                    config.channels.toByte(),
                    config.channels.toByte()
                )
                val seq = nextTxSeq
                nextTxSeq = (nextTxSeq + 1) and 0xFFFF
                sendInternal(
                    frameType = CyrinxConstants.FRAME_DATA,
                    seq = seq,
                    ack = header.seq,
                    streamId = CyrinxConstants.STREAM_CONTROL,
                    priority = 3,
                    flags = 0,
                    payload = capPayload,
                    trackAck = false
                )
            }
        }

        if (header.frameType == CyrinxConstants.FRAME_ACK) {
            synchronized(signal) {
                if (awaitingAck && header.ack == awaitingAckSeq) {
                    awaitingAck = false
                    signal.notifyAll()
                }
            }
            if (payload.size >= CyrinxConstants.ACK_REPORT_PAYLOAD_BYTES) {
                synchronized(signal) {
                    applyChannelReportLocked(decodeAckReport(payload))
                }
            }
            return CyrinxStatus.OK
        }

        if (header.frameType == CyrinxConstants.FRAME_DATA) {
            synchronized(signal) {
                if ((header.flags and CyrinxConstants.FLAG_FRAG_START) != 0) {
                    reassemblyBuffer = ByteArray(0)
                    reassemblyActive = true
                    reassemblyStreamId = header.streamId
                    reassemblyPriority = header.priority
                    reassemblyFlags = 0
                } else if (!reassemblyActive || reassemblyStreamId != header.streamId) {
                    reassemblyBuffer = ByteArray(0)
                    reassemblyActive = true
                    reassemblyStreamId = header.streamId
                    reassemblyPriority = header.priority
                    reassemblyFlags = 0
                }

                if (reassemblyBuffer.size + payload.size > CyrinxConstants.MAX_LOGICAL_MESSAGE) {
                    reassemblyBuffer = ByteArray(0)
                    reassemblyActive = false
                    return CyrinxStatus.ERR_BUFFER_TOO_SMALL
                }

                reassemblyBuffer += payload
                reassemblyFlags = reassemblyFlags or (header.flags and (CyrinxConstants.STREAM_FLAG_FIN or CyrinxConstants.STREAM_FLAG_RST))

                if ((header.flags and CyrinxConstants.FLAG_FRAG_END) != 0) {
                    rxQueue.addLast(
                        CyrinxReceivedMessage(
                            data = reassemblyBuffer,
                            streamId = reassemblyStreamId,
                            priority = reassemblyPriority,
                            flags = reassemblyFlags,
                        ),
                    )
                    reassemblyBuffer = ByteArray(0)
                    reassemblyActive = false
                    reassemblyFlags = 0
                    signal.notifyAll()
                }
            }

            val ackPayload = synchronized(signal) { encodeAckReport(lastReport) }
            return sendInternal(
                frameType = CyrinxConstants.FRAME_ACK,
                seq = synchronized(signal) {
                    val seq = nextTxSeq and 0xFFFF
                    nextTxSeq = (nextTxSeq + 1) and 0xFFFF
                    seq
                },
                ack = header.seq,
                streamId = CyrinxConstants.STREAM_CONTROL,
                priority = 3,
                flags = 0,
                payload = ackPayload,
                trackAck = false,
            )
        }

        return CyrinxStatus.OK
    }

    private fun sendInternal(
        frameType: Int,
        seq: Int,
        ack: Int,
        streamId: Int,
        priority: Int,
        flags: Int,
        payload: ByteArray,
        trackAck: Boolean,
    ): Int {
        if (payload.size > CyrinxConstants.MAX_FRAME_PAYLOAD) {
            return CyrinxStatus.ERR_INVALID_ARGUMENT
        }

        val header = FrameHeader(
            version = 1,
            frameType = frameType,
            sessionId = sessionId and 0xFFFFFF,
            seq = seq and 0xFFFF,
            ack = ack and 0xFFFF,
            gearId = synchronized(signal) { currentGear },
            fecRate = fecRateForGear(synchronized(signal) { currentGear }),
            streamId = streamId and 0x0FFF,
            priority = priority and 0x03,
            payloadLen = payload.size,
            flags = flags and 0xFF,
        )
        val headerBytes = serializeHeader(header) ?: return CyrinxStatus.ERR_INTERNAL

        val frame = ByteArray(CyrinxConstants.FIXED_PREAMBLE_BYTES + CyrinxConstants.HEADER_BYTES + payload.size + CyrinxConstants.FRAME_CRC_BYTES)
        frame[0] = CyrinxConstants.MAGIC0.toByte()
        frame[1] = CyrinxConstants.MAGIC1.toByte()
        System.arraycopy(headerBytes, 0, frame, CyrinxConstants.FIXED_PREAMBLE_BYTES, CyrinxConstants.HEADER_BYTES)
        if (payload.isNotEmpty()) {
            System.arraycopy(payload, 0, frame, CyrinxConstants.FIXED_PREAMBLE_BYTES + CyrinxConstants.HEADER_BYTES, payload.size)
        }

        val crc = crc32c(frame, 0, frame.size - 4)
        writeU32BE(frame, frame.size - 4, crc)

        if (trackAck) {
            synchronized(signal) {
                awaitingAck = true
                awaitingAckSeq = seq and 0xFFFF
                ackDeadlineMs = SystemClock.elapsedRealtime() + CyrinxConstants.TIMEOUT_MS
            }
        }

        val rc = txSink.sendFrame(frame)
        if (rc != CyrinxStatus.OK) {
            return CyrinxStatus.ERR_INTERNAL
        }

        synchronized(signal) {
            metricsState.txFrames += 1
            updateGoodputLocked(payload.size)
        }

        return CyrinxStatus.OK
    }

    private fun updateGoodputLocked(payloadBytes: Int) {
        val gearBps = when (currentGear) {
            2 -> 4000f
            3 -> 8000f
            4 -> 12_000f
            1 -> 450f
            else -> 150f
        }
        val sample = payloadBytes * 8f
        metricsState.goodputBps = (0.8f * metricsState.goodputBps) + (0.2f * min(sample, gearBps))
    }

    private fun applyChannelReportLocked(report: ChannelReport) {
        lastReport = report
        metricsState.snrDb = report.snrDb
        metricsState.evmPct = report.evmPct
        metricsState.cfoHz = report.cfoHz
        metricsState.per2s = report.per2s
        if (report.crcFail) {
            metricsState.crcFailures += 1
        }
    }

    private fun fecRateForGear(gear: Int): Int =
        when (gear) {
            1 -> 1
            2, 3 -> 2
            4 -> 3
            else -> 0
        }

    private fun serializeHeader(header: FrameHeader): ByteArray? {
        val out = ByteArray(CyrinxConstants.HEADER_BYTES)
        var bitPos = 0
        bitPos = writeBits(out, bitPos, header.version and 0xF, 4)
        bitPos = writeBits(out, bitPos, header.frameType and 0xF, 4)
        bitPos = writeBits(out, bitPos, header.sessionId and 0xFFFFFF, 24)
        bitPos = writeBits(out, bitPos, header.seq and 0xFFFF, 16)
        bitPos = writeBits(out, bitPos, header.ack and 0xFFFF, 16)
        bitPos = writeBits(out, bitPos, header.gearId and 0x7, 3)
        bitPos = writeBits(out, bitPos, header.fecRate and 0x7, 3)
        bitPos = writeBits(out, bitPos, header.streamId and 0xFFF, 12)
        bitPos = writeBits(out, bitPos, header.priority and 0x3, 2)
        bitPos = writeBits(out, bitPos, header.payloadLen and 0xFFF, 12)
        bitPos = writeBits(out, bitPos, header.flags and 0xFF, 8)
        if (bitPos != 104) {
            return null
        }
        val crc = crc16ccitt(out, 0, CyrinxConstants.HEADER_NOCRC_BYTES)
        bitPos = writeBits(out, bitPos, crc and 0xFFFF, 16)
        return if (bitPos == 120) out else null
    }

    private fun parseHeader(bytes: ByteArray): FrameHeader? {
        if (bytes.size != CyrinxConstants.HEADER_BYTES) {
            return null
        }
        val expectedCrc = crc16ccitt(bytes, 0, CyrinxConstants.HEADER_NOCRC_BYTES)
        var bitPos = 0
        val version = readBits(bytes, bitPos, 4)
        bitPos += 4
        val frameType = readBits(bytes, bitPos, 4)
        bitPos += 4
        val sessionId = readBits(bytes, bitPos, 24)
        bitPos += 24
        val seq = readBits(bytes, bitPos, 16)
        bitPos += 16
        val ack = readBits(bytes, bitPos, 16)
        bitPos += 16
        val gearId = readBits(bytes, bitPos, 3)
        bitPos += 3
        val fecRate = readBits(bytes, bitPos, 3)
        bitPos += 3
        val streamId = readBits(bytes, bitPos, 12)
        bitPos += 12
        val priority = readBits(bytes, bitPos, 2)
        bitPos += 2
        val payloadLen = readBits(bytes, bitPos, 12)
        bitPos += 12
        val flags = readBits(bytes, bitPos, 8)
        bitPos += 8
        val crc = readBits(bytes, bitPos, 16)
        bitPos += 16

        if (bitPos != 120 || crc != expectedCrc) {
            return null
        }

        return FrameHeader(
            version = version,
            frameType = frameType,
            sessionId = sessionId,
            seq = seq,
            ack = ack,
            gearId = gearId,
            fecRate = fecRate,
            streamId = streamId,
            priority = priority,
            payloadLen = payloadLen,
            flags = flags,
        )
    }

    private fun encodeAckReport(report: ChannelReport): ByteArray {
        val out = ByteArray(CyrinxConstants.ACK_REPORT_PAYLOAD_BYTES)
        val snr = (report.snrDb * 10f).toInt().toShort().toInt() and 0xFFFF
        val evm = (report.evmPct * 10f).toInt().toShort().toInt() and 0xFFFF
        val cfo = (report.cfoHz * 10f).toInt().toShort().toInt() and 0xFFFF
        val per = (report.per2s * 1000f).toInt().coerceIn(0, 0xFFFF)

        writeU16BE(out, 0, snr)
        writeU16BE(out, 2, evm)
        writeU16BE(out, 4, cfo)
        writeU16BE(out, 6, per)
        out[8] = if (report.crcFail) 1 else 0
        out[9] = 0
        return out
    }

    private fun decodeAckReport(bytes: ByteArray): ChannelReport {
        val snr = readU16BE(bytes, 0).toShort().toInt()
        val evm = readU16BE(bytes, 2).toShort().toInt()
        val cfo = readU16BE(bytes, 4).toShort().toInt()
        val per = readU16BE(bytes, 6)
        val crcFail = (bytes[8].toInt() and 0xFF) != 0
        return ChannelReport(
            snrDb = snr / 10f,
            evmPct = evm / 10f,
            cfoHz = cfo / 10f,
            per2s = per / 1000f,
            crcFail = crcFail,
        )
    }

    private fun writeBits(buffer: ByteArray, bitPos: Int, value: Int, bits: Int): Int {
        var pos = bitPos
        for (i in 0 until bits) {
            val shift = bits - 1 - i
            val bit = (value ushr shift) and 0x1
            val byteIndex = pos / 8
            val bitIndex = 7 - (pos % 8)
            if (bit == 1) {
                buffer[byteIndex] = (buffer[byteIndex].toInt() or (1 shl bitIndex)).toByte()
            }
            pos += 1
        }
        return pos
    }

    private fun readBits(buffer: ByteArray, bitPos: Int, bits: Int): Int {
        var value = 0
        var pos = bitPos
        for (i in 0 until bits) {
            val byteIndex = pos / 8
            val bitIndex = 7 - (pos % 8)
            val bit = (buffer[byteIndex].toInt() ushr bitIndex) and 0x1
            value = (value shl 1) or bit
            pos += 1
        }
        return value
    }

    private fun writeU16BE(out: ByteArray, offset: Int, value: Int) {
        out[offset] = ((value ushr 8) and 0xFF).toByte()
        out[offset + 1] = (value and 0xFF).toByte()
    }

    private fun readU16BE(input: ByteArray, offset: Int): Int {
        val hi = input[offset].toInt() and 0xFF
        val lo = input[offset + 1].toInt() and 0xFF
        return (hi shl 8) or lo
    }

    private fun writeU32BE(out: ByteArray, offset: Int, value: Int) {
        out[offset] = ((value ushr 24) and 0xFF).toByte()
        out[offset + 1] = ((value ushr 16) and 0xFF).toByte()
        out[offset + 2] = ((value ushr 8) and 0xFF).toByte()
        out[offset + 3] = (value and 0xFF).toByte()
    }

    private fun readU32BE(input: ByteArray, offset: Int): Int {
        val b0 = input[offset].toInt() and 0xFF
        val b1 = input[offset + 1].toInt() and 0xFF
        val b2 = input[offset + 2].toInt() and 0xFF
        val b3 = input[offset + 3].toInt() and 0xFF
        return (b0 shl 24) or (b1 shl 16) or (b2 shl 8) or b3
    }

    private fun crc16ccitt(data: ByteArray, start: Int, len: Int): Int {
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

    private fun crc32c(data: ByteArray, start: Int, len: Int): Int {
        var crc = 0xFFFFFFFF.toInt()
        for (i in 0 until len) {
            crc = crc xor (data[start + i].toInt() and 0xFF)
            for (j in 0 until 8) {
                val mask = if ((crc and 1) != 0) 0xFFFFFFFF.toInt() else 0
                crc = (crc ushr 1) xor (0x82F63B78.toInt() and mask)
            }
        }
        return crc.inv()
    }
}
