package com.dweekly.cyrinx.chat

/**
 * Envelope v1 wire kind. `TEXT` (wire byte `0x01`) is the only kind defined in v1;
 * see ../../../ENVELOPE.md section 1.1 and the golden-vector JSON's `"kind": "text"`
 * field (../../../ENVELOPE.md section 8).
 */
enum class ChatEnvelopeKind(val wireByte: Int, val wireName: String) {
    TEXT(0x01, "text"),
    ;

    companion object {
        /** Returns null (not an exception) for an unrecognized byte; the codec turns
         * that into [ChatEnvelopeError.UnknownKind] itself so this helper stays a
         * pure lookup. */
        fun fromWireByte(byte: Int): ChatEnvelopeKind? = entries.firstOrNull { it.wireByte == byte }
    }
}

/**
 * A decoded (or about-to-be-encoded) chat envelope v1 value, per ../../../ENVELOPE.md
 * section 1. `messageId` and `replyToId` are 16-byte opaque IDs; `senderId` is
 * 1..32 opaque bytes; `body` is already-validated UTF-8 text (0..2048 bytes when
 * re-encoded). This class does not enforce those bounds itself -- both are enforced,
 * with the exact named errors from ../../../ENVELOPE.md section 5, in
 * [ChatEnvelopeCodec.encode] and [ChatEnvelopeCodec.decode].
 *
 * `ByteArray` properties use structural (content) equality/hashing here, matching
 * CONTRACT.md's `ChatPeer`/`ChatMessage` convention of comparing opaque-ID byte
 * arrays by content rather than reference.
 */
class ChatEnvelope(
    val version: Int,
    val kind: ChatEnvelopeKind,
    val messageId: ByteArray,
    val replyToId: ByteArray?,
    val senderId: ByteArray,
    val body: String,
) {
    override fun equals(other: Any?): Boolean {
        if (this === other) return true
        if (other !is ChatEnvelope) return false
        return version == other.version &&
            kind == other.kind &&
            messageId.contentEquals(other.messageId) &&
            nullableContentEquals(replyToId, other.replyToId) &&
            senderId.contentEquals(other.senderId) &&
            body == other.body
    }

    override fun hashCode(): Int {
        var result = version
        result = 31 * result + kind.hashCode()
        result = 31 * result + messageId.contentHashCode()
        result = 31 * result + (replyToId?.contentHashCode() ?: 0)
        result = 31 * result + senderId.contentHashCode()
        result = 31 * result + body.hashCode()
        return result
    }

    override fun toString(): String =
        "ChatEnvelope(version=$version, kind=$kind, messageId=${messageId.toHexString()}, " +
            "replyToId=${replyToId?.toHexString()}, senderId=${senderId.toHexString()}, " +
            "body.length=${body.length})"
}

/** Lowercase hex, no separators -- matches the golden-vector JSON's `*Hex` field
 * convention (../../../ENVELOPE.md section 8). */
fun ByteArray.toHexString(): String {
    val builder = StringBuilder(size * 2)
    for (byte in this) {
        builder.append(HEX_DIGITS[(byte.toInt() shr 4) and 0xF])
        builder.append(HEX_DIGITS[byte.toInt() and 0xF])
    }
    return builder.toString()
}

private val HEX_DIGITS = "0123456789abcdef".toCharArray()

private fun nullableContentEquals(a: ByteArray?, b: ByteArray?): Boolean {
    if (a == null || b == null) return a == null && b == null
    return a.contentEquals(b)
}

/** Inverse of [toHexString]. Throws [IllegalArgumentException] on odd length or a
 * non-hex character; this is a test/generation convenience, not part of the wire
 * codec itself (which never parses hex, only raw bytes). */
fun String.hexToByteArray(): ByteArray {
    require(length % 2 == 0) { "hex string must have even length, got $length" }
    val out = ByteArray(length / 2)
    for (i in out.indices) {
        val hi = Character.digit(this[i * 2], 16)
        val lo = Character.digit(this[i * 2 + 1], 16)
        require(hi >= 0 && lo >= 0) { "invalid hex character in \"$this\" at position ${i * 2}" }
        out[i] = ((hi shl 4) or lo).toByte()
    }
    return out
}
