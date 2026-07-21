@file:OptIn(ExperimentalCoroutinesApi::class)

package com.dweekly.cyrinx.chat

import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * Regression coverage for the defensive-copy requirement on every public
 * `ByteArray` "identity" value this module exposes: [ChatPeer.id],
 * [ChatMessage.id], [ChatEnvelope.messageId]/`replyToId`/`senderId`, and
 * [SimulatedChatTransportClient.id]. Each is copied ON THE WAY IN (the
 * constructor argument's backing array is never aliased into the instance) and
 * ON THE WAY OUT (every property read returns a fresh copy), so a caller
 * mutating either an array it passed in, or an array it read back out, cannot
 * corrupt the instance's internal state -- `ByteArray` is mutable in both
 * Kotlin and Java, and every one of these values is used for equality,
 * hashing, or cross-client identity lookup, so an aliased reference would be a
 * real state-corruption vector, not just a style nit.
 *
 * DECISION (not pinned by the brief): ../../../CONTRACT.md does not itself
 * mandate defensive copying; this is a general `ByteArray`-mutability hygiene
 * rule this module's PR review pass adopted for every exposed identity value.
 */
class DefensiveCopyTest {
    @Test
    fun chatPeerIdIsCopiedOnTheWayInAndOut() {
        val source = byteArrayOf(1, 2, 3, 4)
        val peer = ChatPeer(source, 0)

        // Mutating the array the caller passed IN must not affect the peer.
        source[0] = 0xFF.toByte()
        assertEquals("01020304", peer.id.toHexString())

        // Mutating the array read back OUT must not affect a later read.
        val readBack = peer.id
        readBack[0] = 0xFF.toByte()
        assertEquals("01020304", peer.id.toHexString())
    }

    @Test
    fun chatMessageIdIsCopiedOnTheWayInAndOut() {
        val source = byteArrayOf(1, 2, 3, 4)
        val message =
            ChatMessage(source, ChatMessage.Direction.OUTGOING, "hi", "aabbccdd", 0, ChatMessageDisplayStatus.Queued)

        source[0] = 0xFF.toByte()
        assertEquals("01020304", message.id.toHexString())

        val readBack = message.id
        readBack[0] = 0xFF.toByte()
        assertEquals("01020304", message.id.toHexString())
    }

    @Test
    fun chatEnvelopeIdsAreCopiedOnTheWayInAndOut() {
        val messageId = ByteArray(ChatEnvelopeCodec.ID_LEN) { it.toByte() }
        val replyToId = ByteArray(ChatEnvelopeCodec.ID_LEN) { (it + 1).toByte() }
        val senderId = byteArrayOf(0xDE.toByte(), 0xAD.toByte(), 0xBE.toByte(), 0xEF.toByte())
        val expectedMessageIdHex = messageId.toHexString()
        val expectedReplyToIdHex = replyToId.toHexString()
        val expectedSenderIdHex = senderId.toHexString()

        val envelope =
            ChatEnvelope(ChatEnvelopeCodec.VERSION, ChatEnvelopeKind.TEXT, messageId, replyToId, senderId, "hi")

        // Mutate every array the caller passed IN.
        messageId[0] = 0xFF.toByte()
        replyToId[0] = 0xFF.toByte()
        senderId[0] = 0xFF.toByte()
        assertEquals(expectedMessageIdHex, envelope.messageId.toHexString())
        assertEquals(expectedReplyToIdHex, envelope.replyToId?.toHexString())
        assertEquals(expectedSenderIdHex, envelope.senderId.toHexString())

        // Mutate every array read back OUT.
        envelope.messageId[0] = 0xFF.toByte()
        envelope.replyToId?.let { it[0] = 0xFF.toByte() }
        envelope.senderId[0] = 0xFF.toByte()
        assertEquals(expectedMessageIdHex, envelope.messageId.toHexString())
        assertEquals(expectedReplyToIdHex, envelope.replyToId?.toHexString())
        assertEquals(expectedSenderIdHex, envelope.senderId.toHexString())
    }

    @Test
    fun simulatedChatTransportClientIdIsCopiedOnTheWayOut() =
        runTest {
            val pair = createChatPair(ChatScenario.HAPPY_PAIR, 8L, null)
            val expectedIdHex = pair.clientA.id.toHexString()

            val readBack = pair.clientA.id
            readBack[0] = 0xFF.toByte()

            assertEquals(expectedIdHex, pair.clientA.id.toHexString())
        }
}
