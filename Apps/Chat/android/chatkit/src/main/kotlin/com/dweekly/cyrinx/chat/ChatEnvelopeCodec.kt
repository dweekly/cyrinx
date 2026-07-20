package com.dweekly.cyrinx.chat

import java.nio.ByteBuffer
import java.nio.charset.CharacterCodingException
import java.nio.charset.CodingErrorAction
import java.nio.charset.StandardCharsets

/**
 * Envelope v1 strict codec, big-endian, sequential parse. Byte-layout, error
 * taxonomy, and the "canonical encoding" round-trip guarantee are pinned in
 * ../../../ENVELOPE.md; this object is the Kotlin realization checked against
 * ../../../fixtures/chat-envelope-golden.json (19 vectors), not against the Swift
 * codec directly (per ENVELOPE.md's opening paragraph).
 */
object ChatEnvelopeCodec {
    /** Only defined envelope version. ../../../ENVELOPE.md section 1.1. */
    const val VERSION: Int = 1

    /** Length in bytes of both `messageId` and `replyToId`. ../../../ENVELOPE.md
     * section 1.1/1.2. */
    const val ID_LEN: Int = 16

    /** Inclusive lower bound of `senderIdLen`. ../../../ENVELOPE.md section 1.2. */
    const val MIN_SENDER_ID_LEN: Int = 1

    /** Inclusive upper bound of `senderIdLen`. ../../../ENVELOPE.md section 1.2. */
    const val MAX_SENDER_ID_LEN: Int = 32

    /** Inclusive upper bound of `bodyLen`. ../../../ENVELOPE.md section 1.2. */
    const val MAX_BODY_LEN: Int = 2048

    /** `flags` bit0 marks `replyToId` presence; bits 1-7 are undefined in v1 and
     * must be zero. ../../../ENVELOPE.md section 1.1. */
    private const val FLAG_REPLY_TO_PRESENT: Int = 0x01
    private const val FLAG_DEFINED_BITS_MASK: Int = FLAG_REPLY_TO_PRESENT

    /** 3 (version+kind+flags) + 16 (messageId) + 16 (replyToId) + 1 (senderIdLen)
     * + 32 (max senderId) + 2 (bodyLen) + 2048 (max body) = 2118, the concrete
     * instance the `body_max_2048` golden vector hand-verifies.
     * ../../../ENVELOPE.md section 4. */
    const val MAX_ENVELOPE_LEN: Int = 3 + ID_LEN + ID_LEN + 1 + MAX_SENDER_ID_LEN + 2 + MAX_BODY_LEN

    /**
     * Decodes one envelope from `data`, per the 11-step strict sequential parse
     * order in ../../../ENVELOPE.md section 2. Throws the matching
     * [ChatEnvelopeError] on the first violation; never partially recovers.
     */
    fun decode(data: ByteArray): ChatEnvelope {
        var offset = 0

        fun takeByte(field: String): Int {
            if (offset >= data.size) throw ChatEnvelopeError.Truncated("buffer ended while reading $field")
            return data[offset++].toInt() and 0xFF
        }

        fun takeBytes(n: Int, field: String): ByteArray {
            if (offset + n > data.size) {
                throw ChatEnvelopeError.Truncated("buffer ended while reading $field ($n bytes)")
            }
            val out = data.copyOfRange(offset, offset + n)
            offset += n
            return out
        }

        // Step 1: version.
        val version = takeByte("version")
        if (version != VERSION) throw ChatEnvelopeError.UnknownVersion(version)

        // Step 2: kind.
        val kindByte = takeByte("kind")
        val kind = ChatEnvelopeKind.fromWireByte(kindByte) ?: throw ChatEnvelopeError.UnknownKind(kindByte)

        // Step 3: flags.
        val flags = takeByte("flags")
        if (flags and FLAG_DEFINED_BITS_MASK.inv() != 0) {
            throw ChatEnvelopeError.Malformed("flags byte 0x${flags.toString(16)} sets undefined bits")
        }
        val hasReplyTo = (flags and FLAG_REPLY_TO_PRESENT) != 0

        // Step 4: messageId.
        val messageId = takeBytes(ID_LEN, "messageId")

        // Step 5: replyToId, iff flags bit0 set.
        val replyToId = if (hasReplyTo) takeBytes(ID_LEN, "replyToId") else null

        // Step 6: senderIdLen, range-checked BEFORE reading senderId bytes (see
        // ENVELOPE.md section 2 step 6's non-obvious rule: senderIdLen=33 with zero
        // trailing bytes is malformed, not truncated).
        val senderIdLen = takeByte("senderIdLen")
        if (senderIdLen < MIN_SENDER_ID_LEN || senderIdLen > MAX_SENDER_ID_LEN) {
            throw ChatEnvelopeError.Malformed(
                "senderIdLen $senderIdLen outside $MIN_SENDER_ID_LEN..$MAX_SENDER_ID_LEN",
            )
        }

        // Step 7: senderId.
        val senderId = takeBytes(senderIdLen, "senderId")

        // Step 8: bodyLen (u16 big-endian), range-checked before reading body bytes
        // (same reasoning as step 6).
        val bodyLenHi = takeByte("bodyLen")
        val bodyLenLo = takeByte("bodyLen")
        val bodyLen = (bodyLenHi shl 8) or bodyLenLo
        if (bodyLen > MAX_BODY_LEN) throw ChatEnvelopeError.OversizeBody(bodyLen)

        // Step 9: body bytes.
        val bodyBytes = takeBytes(bodyLen, "body")

        // Step 10: strict UTF-8 validation. java.nio's REPORT-mode decoder rejects
        // lone/invalid lead bytes, truncated multi-byte sequences, overlong
        // encodings, and encoded surrogate halves -- verified against every
        // reject_invalid_utf8_* golden vector and cross-checked against a standalone
        // javac/java probe (see the C3-28 spec-stage report) before being wired in
        // here, per this repo's "write small test programs to validate a hypothesis"
        // convention.
        val body =
            try {
                StandardCharsets.UTF_8.newDecoder()
                    .onMalformedInput(CodingErrorAction.REPORT)
                    .onUnmappableCharacter(CodingErrorAction.REPORT)
                    .decode(ByteBuffer.wrap(bodyBytes))
                    .toString()
            } catch (e: CharacterCodingException) {
                throw ChatEnvelopeError.InvalidUtf8("body is not valid UTF-8: ${e.message}")
            }

        // Step 11: no trailing bytes.
        if (offset != data.size) {
            val trailing = data.size - offset
            throw ChatEnvelopeError.Malformed("$trailing trailing byte(s) after a complete envelope")
        }

        return ChatEnvelope(version, kind, messageId, replyToId, senderId, body)
    }

    /**
     * Encodes `envelope` to its unique canonical byte form. Throws the matching
     * [ChatEnvelopeError] if any bound is violated; per ../../../ENVELOPE.md
     * section 3, an encoder only ever needs to guard `unknownVersion`,
     * `unknownKind`, `malformed` (bad ID/senderId lengths), `oversizeBody`, and
     * `invalidUtf8` -- `truncated` and trailing-byte `malformed` only arise when
     * parsing an externally supplied buffer.
     */
    fun encode(envelope: ChatEnvelope): ByteArray {
        if (envelope.version != VERSION) throw ChatEnvelopeError.UnknownVersion(envelope.version)
        // ChatEnvelopeKind is a closed enum with only TEXT defined, so an
        // "unknownKind" value cannot be constructed in Kotlin; this branch exists
        // only so the encoder's guard list textually matches ENVELOPE.md section 3's
        // five named guards, and to stay correct if a v2 kind is ever added here
        // without updating this check.
        if (ChatEnvelopeKind.fromWireByte(envelope.kind.wireByte) == null) {
            throw ChatEnvelopeError.UnknownKind(envelope.kind.wireByte)
        }
        if (envelope.messageId.size != ID_LEN) {
            val actual = envelope.messageId.size
            throw ChatEnvelopeError.Malformed("messageId must be exactly $ID_LEN bytes, got $actual")
        }
        envelope.replyToId?.let {
            if (it.size != ID_LEN) {
                throw ChatEnvelopeError.Malformed("replyToId must be exactly $ID_LEN bytes, got ${it.size}")
            }
        }
        val senderIdLen = envelope.senderId.size
        if (senderIdLen < MIN_SENDER_ID_LEN || senderIdLen > MAX_SENDER_ID_LEN) {
            throw ChatEnvelopeError.Malformed(
                "senderIdLen $senderIdLen outside $MIN_SENDER_ID_LEN..$MAX_SENDER_ID_LEN",
            )
        }

        val bodyBytes =
            try {
                val encoder =
                    StandardCharsets.UTF_8.newEncoder()
                        .onMalformedInput(CodingErrorAction.REPORT)
                        .onUnmappableCharacter(CodingErrorAction.REPORT)
                val buffer = encoder.encode(java.nio.CharBuffer.wrap(envelope.body))
                ByteArray(buffer.remaining()).also { buffer.get(it) }
            } catch (e: CharacterCodingException) {
                throw ChatEnvelopeError.InvalidUtf8("body is not valid UTF-8: ${e.message}")
            }
        if (bodyBytes.size > MAX_BODY_LEN) throw ChatEnvelopeError.OversizeBody(bodyBytes.size)

        val flags = if (envelope.replyToId != null) FLAG_REPLY_TO_PRESENT else 0
        val out = ByteArray(MAX_ENVELOPE_LEN)
        var offset = 0

        fun putByte(v: Int) {
            out[offset++] = v.toByte()
        }

        fun putBytes(bytes: ByteArray) {
            System.arraycopy(bytes, 0, out, offset, bytes.size)
            offset += bytes.size
        }

        putByte(VERSION)
        putByte(envelope.kind.wireByte)
        putByte(flags)
        putBytes(envelope.messageId)
        envelope.replyToId?.let { putBytes(it) }
        putByte(senderIdLen)
        putBytes(envelope.senderId)
        putByte((bodyBytes.size shr 8) and 0xFF)
        putByte(bodyBytes.size and 0xFF)
        putBytes(bodyBytes)

        return out.copyOfRange(0, offset)
    }
}
