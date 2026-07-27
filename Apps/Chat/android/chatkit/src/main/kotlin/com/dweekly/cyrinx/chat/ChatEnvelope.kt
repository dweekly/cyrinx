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
 * 1..32 opaque bytes; `sequence` is a nonzero sender-local `u64` (../../../ENVELOPE.md
 * section 1.2, the C3-28 sequence amendment), represented here as a [Long] holding
 * the exact 64-bit wire bit pattern (so `0xFFFFFFFFFFFFFFFF` round-trips as `-1L`,
 * never through a `Double`/floating intermediate -- see ../../../ENVELOPE.md
 * section 8); `body` is already-validated UTF-8 text (0..2048 bytes when
 * re-encoded). This class does not enforce those bounds itself -- both are enforced,
 * with the exact named errors from ../../../ENVELOPE.md section 5, in
 * [ChatEnvelopeCodec.encode] and [ChatEnvelopeCodec.decode].
 *
 * `ByteArray` properties use structural (content) equality/hashing here, matching
 * CONTRACT.md's `ChatPeer`/`ChatMessage` convention of comparing opaque-ID byte
 * arrays by content rather than reference. Each is also defensively copied on the
 * way in and on the way out (every read returns a fresh copy), same rationale as
 * [ChatPeer.id]: a caller holding a reference to a constructor argument or a
 * previously-read property must not be able to mutate this instance's identity
 * out from under it (or, worse, out from under an already-encoded envelope's
 * in-flight bytes).
 */
class ChatEnvelope(
    val version: Int,
    val kind: ChatEnvelopeKind,
    messageId: ByteArray,
    replyToId: ByteArray?,
    senderId: ByteArray,
    val sequence: Long,
    val body: String,
) {
    private val messageIdBytes: ByteArray = messageId.copyOf()
    private val replyToIdBytes: ByteArray? = replyToId?.copyOf()
    private val senderIdBytes: ByteArray = senderId.copyOf()

    val messageId: ByteArray
        get() = messageIdBytes.copyOf()

    val replyToId: ByteArray?
        get() = replyToIdBytes?.copyOf()

    val senderId: ByteArray
        get() = senderIdBytes.copyOf()

    override fun equals(other: Any?): Boolean {
        if (this === other) return true
        if (other !is ChatEnvelope) return false
        return version == other.version &&
            kind == other.kind &&
            messageIdBytes.contentEquals(other.messageIdBytes) &&
            nullableContentEquals(replyToIdBytes, other.replyToIdBytes) &&
            senderIdBytes.contentEquals(other.senderIdBytes) &&
            sequence == other.sequence &&
            body == other.body
    }

    override fun hashCode(): Int {
        var result = version
        result = 31 * result + kind.hashCode()
        result = 31 * result + messageIdBytes.contentHashCode()
        result = 31 * result + (replyToIdBytes?.contentHashCode() ?: 0)
        result = 31 * result + senderIdBytes.contentHashCode()
        result = 31 * result + sequence.hashCode()
        result = 31 * result + body.hashCode()
        return result
    }

    override fun toString(): String =
        "ChatEnvelope(version=$version, kind=$kind, messageId=${messageIdBytes.toHexString()}, " +
            "replyToId=${replyToIdBytes?.toHexString()}, senderId=${senderIdBytes.toHexString()}, " +
            "sequence=${sequence.toULong()}, body.length=${body.length})"
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
