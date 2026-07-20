package com.dweekly.cyrinx.chat

import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

/**
 * Encoder-side bounds checks (../../../ENVELOPE.md section 3: "the encoder
 * enforces the same bounds as the decoder ... and raises the matching named
 * error"), not covered by the golden vectors (which are all decoder-input byte
 * buffers). Complements ChatEnvelopeCodecGoldenTest.
 */
class ChatEnvelopeCodecBoundsTest {
    private val messageId = ByteArray(ChatEnvelopeCodec.ID_LEN) { it.toByte() }
    private val senderId = byteArrayOf(0xDE.toByte(), 0xAD.toByte(), 0xBE.toByte(), 0xEF.toByte())

    private fun envelope(
        senderId: ByteArray = this.senderId,
        replyToId: ByteArray? = null,
        body: String = "hi",
    ) = ChatEnvelope(ChatEnvelopeCodec.VERSION, ChatEnvelopeKind.TEXT, messageId, replyToId, senderId, body)

    @Test
    fun encodeRejectsSenderIdTooShort() {
        val error = assertThrows(ChatEnvelopeError.Malformed::class.java) {
            ChatEnvelopeCodec.encode(envelope(senderId = ByteArray(0)))
        }
        assertEquals("malformed", error.errorName)
    }

    @Test
    fun encodeRejectsSenderIdTooLong() {
        val error = assertThrows(ChatEnvelopeError.Malformed::class.java) {
            ChatEnvelopeCodec.encode(envelope(senderId = ByteArray(ChatEnvelopeCodec.MAX_SENDER_ID_LEN + 1)))
        }
        assertEquals("malformed", error.errorName)
    }

    @Test
    fun encodeAcceptsSenderIdAtMinBound() {
        val encoded = ChatEnvelopeCodec.encode(envelope(senderId = ByteArray(ChatEnvelopeCodec.MIN_SENDER_ID_LEN)))
        assertEquals(ChatEnvelopeCodec.MIN_SENDER_ID_LEN, ChatEnvelopeCodec.decode(encoded).senderId.size)
    }

    @Test
    fun encodeAcceptsSenderIdAtMaxBound() {
        val encoded = ChatEnvelopeCodec.encode(envelope(senderId = ByteArray(ChatEnvelopeCodec.MAX_SENDER_ID_LEN)))
        assertEquals(ChatEnvelopeCodec.MAX_SENDER_ID_LEN, ChatEnvelopeCodec.decode(encoded).senderId.size)
    }

    @Test
    fun encodeRejectsOversizeBody() {
        val oversizeBody = "A".repeat(ChatEnvelopeCodec.MAX_BODY_LEN + 1)
        val error = assertThrows(ChatEnvelopeError.OversizeBody::class.java) {
            ChatEnvelopeCodec.encode(envelope(body = oversizeBody))
        }
        assertEquals("oversizeBody", error.errorName)
    }

    @Test
    fun encodeAcceptsBodyAtExactMaxBound() {
        val maxBody = "A".repeat(ChatEnvelopeCodec.MAX_BODY_LEN)
        val encoded = ChatEnvelopeCodec.encode(envelope(body = maxBody))
        assertEquals(maxBody, ChatEnvelopeCodec.decode(encoded).body)
    }

    @Test
    fun encodeRejectsMessageIdWrongLength() {
        val badEnvelope =
            ChatEnvelope(ChatEnvelopeCodec.VERSION, ChatEnvelopeKind.TEXT, ByteArray(15), null, senderId, "hi")
        val error = assertThrows(ChatEnvelopeError.Malformed::class.java) {
            ChatEnvelopeCodec.encode(badEnvelope)
        }
        assertEquals("malformed", error.errorName)
    }

    @Test
    fun encodeRejectsReplyToIdWrongLength() {
        val error = assertThrows(ChatEnvelopeError.Malformed::class.java) {
            ChatEnvelopeCodec.encode(envelope(replyToId = ByteArray(15)))
        }
        assertEquals("malformed", error.errorName)
    }

    @Test
    fun encodeRejectsUnknownVersion() {
        val badEnvelope = ChatEnvelope(2, ChatEnvelopeKind.TEXT, messageId, null, senderId, "hi")
        val error = assertThrows(ChatEnvelopeError.UnknownVersion::class.java) {
            ChatEnvelopeCodec.encode(badEnvelope)
        }
        assertEquals("unknownVersion", error.errorName)
    }

    @Test
    fun decodeRejectsEmptyBuffer() {
        val error = assertThrows(ChatEnvelopeError.Truncated::class.java) {
            ChatEnvelopeCodec.decode(ByteArray(0))
        }
        assertEquals("truncated", error.errorName)
    }

    @Test
    fun encodeThenDecodeRoundTripsWithReplyTo() {
        val replyToId = ByteArray(ChatEnvelopeCodec.ID_LEN) { (it + 1).toByte() }
        val encoded = ChatEnvelopeCodec.encode(envelope(replyToId = replyToId, body = "ack"))
        val decoded = ChatEnvelopeCodec.decode(encoded)
        assertEquals(envelope(replyToId = replyToId, body = "ack"), decoded)
    }

    @Test
    fun encodeThenDecodeRoundTripsEmptyBody() {
        val encoded = ChatEnvelopeCodec.encode(envelope(body = ""))
        assertEquals("", ChatEnvelopeCodec.decode(encoded).body)
    }
}
