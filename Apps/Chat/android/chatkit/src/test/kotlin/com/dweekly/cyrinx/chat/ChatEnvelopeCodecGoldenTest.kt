package com.dweekly.cyrinx.chat

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/** Like [org.junit.Assert.fail], but typed `Nothing` so it unifies cleanly inside
 * a `try { ... } catch { ... }` expression whose success branch produces a
 * non-Unit value (`org.junit.Assert.fail`'s Java signature returns `void`, which
 * Kotlin sees as `Unit`, breaking that unification). */
private fun failTest(message: String): Nothing = throw AssertionError(message)

/**
 * Every golden vector in ../fixtures/chat-envelope-golden.json (22 vectors: 6
 * `expect: "decode"`, 16 `expect: "error"` -- the C3-28 sequence amendment added
 * `sequence_max_u64_accepted`, `reject_sequence_zero`, and `truncated_mid_sequence`
 * to the pre-amendment 19), checked against [ChatEnvelopeCodec] per
 * ../../../ENVELOPE.md section 8's schema. For each `decode` vector: decoded
 * field values match (including `sequence`, compared via [JsonAccess.uLong] so
 * `sequence_max_u64_accepted`'s `18446744073709551615` round-trips exactly rather
 * than through a lossy `Double`), AND re-encoding the decoded value reproduces
 * `bytes_hex` exactly (ENVELOPE.md section 3's canonical-encoding guarantee). For
 * each `error` vector: decoding raises exactly the named [ChatEnvelopeError].
 */
class ChatEnvelopeCodecGoldenTest {
    private val goldenFile = File("../fixtures/chat-envelope-golden.json")

    private fun loadVectors(): List<Map<String, Any?>> {
        assertTrue("golden fixture not found at ${goldenFile.absolutePath}", goldenFile.isFile)
        val root = JsonAccess.obj(MiniJson.parse(goldenFile.readText()))
        assertEquals("cyrinx-chat-envelope-golden-v1", root["schema"])
        return JsonAccess.arr(root["vectors"]).map { JsonAccess.obj(it) }
    }

    @Test
    fun goldenVectorCountIs22() {
        val vectors = loadVectors()
        assertEquals(22, vectors.size)
        assertEquals(6, vectors.count { it["expect"] == "decode" })
        assertEquals(16, vectors.count { it["expect"] == "error" })
    }

    @Test
    fun everyDecodeVectorDecodesAndReencodesCanonically() {
        val vectors = loadVectors().filter { it["expect"] == "decode" }
        assertTrue("expected at least one decode vector", vectors.isNotEmpty())

        for (vector in vectors) {
            val name = JsonAccess.str(vector["name"])
            val bytes = JsonAccess.str(vector["bytes_hex"]).hexToByteArray()
            val decodedJson = JsonAccess.obj(vector["decoded"])

            val envelope =
                try {
                    ChatEnvelopeCodec.decode(bytes)
                } catch (e: ChatEnvelopeError) {
                    failTest("[$name] expected decode success but got ${e.errorName}: ${e.message}")
                }

            assertEquals("[$name] version", JsonAccess.int(decodedJson["version"]), envelope.version)
            assertEquals("[$name] kind", JsonAccess.str(decodedJson["kind"]), envelope.kind.wireName)
            assertEquals("[$name] messageIdHex", JsonAccess.str(decodedJson["messageIdHex"]), envelope.messageId.toHexString())

            val expectedReplyToHex = JsonAccess.strOrNull(decodedJson["replyToIdHex"])
            if (expectedReplyToHex == null) {
                assertNull("[$name] replyToId should be null", envelope.replyToId)
            } else {
                assertEquals("[$name] replyToIdHex", expectedReplyToHex, envelope.replyToId?.toHexString())
            }

            assertEquals("[$name] senderIdHex", JsonAccess.str(decodedJson["senderIdHex"]), envelope.senderId.toHexString())
            assertEquals("[$name] sequence", JsonAccess.uLong(decodedJson["sequence"]), envelope.sequence)
            assertEquals("[$name] body", JsonAccess.str(decodedJson["body"]), envelope.body)

            // ENVELOPE.md section 3: canonical encoding round-trip.
            val reencoded = ChatEnvelopeCodec.encode(envelope)
            assertArrayEquals("[$name] re-encode must reproduce bytes_hex exactly", bytes, reencoded)
        }
    }

    @Test
    fun everyErrorVectorRaisesExactlyTheNamedError() {
        val vectors = loadVectors().filter { it["expect"] == "error" }
        assertTrue("expected at least one error vector", vectors.isNotEmpty())

        for (vector in vectors) {
            val name = JsonAccess.str(vector["name"])
            val bytes = JsonAccess.str(vector["bytes_hex"]).hexToByteArray()
            val expectedError = JsonAccess.str(vector["error"])

            val thrown =
                try {
                    val decoded = ChatEnvelopeCodec.decode(bytes)
                    failTest("[$name] expected $expectedError but decode succeeded: $decoded")
                } catch (e: ChatEnvelopeError) {
                    e
                }
            assertEquals("[$name] error name", expectedError, thrown.errorName)
        }
    }

    @Test
    fun bodyMax2048VectorIsExactlyMaxEnvelopeLen() {
        val vector = loadVectors().single { it["name"] == "body_max_2048" }
        val bytes = JsonAccess.str(vector["bytes_hex"]).hexToByteArray()
        assertEquals(ChatEnvelopeCodec.MAX_ENVELOPE_LEN, bytes.size)
        assertEquals(2126, ChatEnvelopeCodec.MAX_ENVELOPE_LEN)
    }
}
