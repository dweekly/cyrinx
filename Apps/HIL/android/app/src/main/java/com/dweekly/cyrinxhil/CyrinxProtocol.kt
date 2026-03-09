package com.dweekly.cyrinxhil

object CyrinxStatus {
    const val OK = 0
    const val ERR_INVALID_ARGUMENT = -1
    const val ERR_NOT_RUNNING = -2
    const val ERR_BUFFER_TOO_SMALL = -3
    const val ERR_TIMEOUT = -4
    const val ERR_CRC = -5
    const val ERR_BUSY = -6
    const val ERR_UNSUPPORTED = -7
    const val ERR_STATE = -8
    const val ERR_INTERNAL = -9
}

enum class Role {
    MASTER,
    SLAVE,
}

enum class QoS {
    BEST_EFFORT,
    RELIABLE,
}

object CyrinxConstants {
    const val MAGIC0 = 0xC7
    const val MAGIC1 = 0x58

    const val MAX_FRAME_PAYLOAD = 1024
    const val MAX_LOGICAL_MESSAGE = 4096

    const val HEADER_BYTES = 15
    const val HEADER_NOCRC_BYTES = 13
    const val FIXED_PREAMBLE_BYTES = 2
    const val FRAME_CRC_BYTES = 4

    const val STREAM_CONTROL = 0
    const val STREAM_ID_MAX = 4095
    const val STREAM_PRIORITY_MAX = 3

    const val FLAG_FRAG_START = 0x01
    const val FLAG_FRAG_END = 0x02
    const val STREAM_FLAG_FIN = 0x04
    const val STREAM_FLAG_RST = 0x08

    const val TIMEOUT_MS = 1200L

    const val FRAME_DATA = 0
    const val FRAME_ACK = 1
    const val FRAME_CONTROL = 2

    const val ACK_REPORT_PAYLOAD_BYTES = 10
}

data class ChannelReport(
    val snrDb: Float = 0f,
    val evmPct: Float = 0f,
    val cfoHz: Float = 0f,
    val per2s: Float = 0f,
    val crcFail: Boolean = false,
)

data class CyrinxReceivedMessage(
    val data: ByteArray,
    val streamId: Int,
    val priority: Int,
    val flags: Int,
)

data class CyrinxMetrics(
    var snrDb: Float = 0f,
    var evmPct: Float = 0f,
    var cfoHz: Float = 0f,
    var per2s: Float = 0f,
    var goodputBps: Float = 0f,
    var txRetries: Int = 0,
    var txFrames: Int = 0,
    var rxFrames: Int = 0,
    var crcFailures: Int = 0,
    var linkResets: Int = 0,
    var currentGear: Int = 0,
    var retransmissionActive: Boolean = false,
)

data class SessionConfig(
    val role: Role,
    val sampleRateHz: Int = 48_000,
    val bandStartHz: Int = 18_500,
    val bandEndHz: Int = 21_000,
    val txGainCap: Float = 0.70f,
)

interface FrameTxSink {
    fun sendFrame(frame: ByteArray): Int
}
