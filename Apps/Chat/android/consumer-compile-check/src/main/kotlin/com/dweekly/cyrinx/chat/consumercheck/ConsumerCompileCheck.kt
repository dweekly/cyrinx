package com.dweekly.cyrinx.chat.consumercheck

import com.dweekly.cyrinx.chat.ChatEnvelope
import com.dweekly.cyrinx.chat.ChatEnvelopeCodec
import com.dweekly.cyrinx.chat.ChatEnvelopeKind
import com.dweekly.cyrinx.chat.ChatScenario
import com.dweekly.cyrinx.chat.SimulatedChatPair
import com.dweekly.cyrinx.chat.VirtualTimeSource
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers

/**
 * Not invoked by anything -- this module's `check` task (see build.gradle.kts)
 * only needs this file to COMPILE against `:chatkit`'s public API under a
 * JVM-17 toolchain, which is the whole regression net `:consumer-compile-check`
 * exists for. Deliberately touches two different corners of that public API:
 * [ChatEnvelopeCodec] (the wire codec, ../../../../ENVELOPE.md) and
 * [SimulatedChatPair] (the simulated transport, ../../../../CONTRACT.md
 * section 2), so a future change that breaks either's binary/source
 * compatibility for a JVM-17-toolchain consumer fails this module's
 * `compileKotlin` task, not just `:chatkit`'s own same-toolchain test suite.
 */
fun buildEnvelopeAndPairAgainstChatkitPublicApi(): SimulatedChatPair {
    val envelope =
        ChatEnvelope(
            version = ChatEnvelopeCodec.VERSION,
            kind = ChatEnvelopeKind.TEXT,
            messageId = ByteArray(ChatEnvelopeCodec.ID_LEN),
            replyToId = null,
            senderId = byteArrayOf(0x01),
            body = "consumer-compile-check",
        )
    val encoded = ChatEnvelopeCodec.encode(envelope)
    check(ChatEnvelopeCodec.decode(encoded).body == envelope.body) {
        "round-trip through the public codec API must be lossless"
    }

    val scope = CoroutineScope(Dispatchers.Default)
    return SimulatedChatPair.create(
        scenario = ChatScenario.HAPPY_PAIR,
        seed = 1L,
        scope = scope,
        timeSource = VirtualTimeSource { 0L },
    )
}
