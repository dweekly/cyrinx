@file:OptIn(ExperimentalCoroutinesApi::class)

package com.dweekly.cyrinx.chat

import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * Reproduces every one of the six pinned scenario scripts (../../../CONTRACT.md
 * section 3) and checks the EXACT resulting trace -- `eventSeq`, `virtualTimeMs`,
 * `client`, and the full event payload, row for row -- against that section's
 * tables. This is the "scenario scripts exactly per CONTRACT.md tables"
 * requirement; ChatTraceJsonTest separately checks JSON-lines rendering, and
 * ChatTraceGoldenComparisonTest separately checks (and, absent the fixture,
 * skips) byte-identity against the Swift-generated golden traces.
 *
 * A fixed test seed is used throughout; scenario behavior does not depend on
 * which seed is chosen (only the derived peer IDs and message IDs do), and
 * [expectedPeerIds] recomputes those independently so this test does not rely on
 * [SimulatedChatPair]'s internals for its expected values.
 */
class ChatScenarioExactTraceTest {
    private val seed = 777L

    /** Accumulates expected [ChatTraceEntry] rows in table order, auto-assigning
     * each client's own monotonic `eventSeq` so scenario transcriptions below
     * read as "client, virtualTimeMs, event" -- exactly CONTRACT.md section 3's
     * table columns -- without hand-counting `eventSeq`. */
    private class ExpectedTrace {
        private val nextSeq = mutableMapOf('A' to 0L, 'B' to 0L)
        val rows = mutableListOf<ChatTraceEntry>()

        fun add(client: Char, virtualTimeMs: Long, event: (eventSeq: Long) -> ChatEvent) {
            val seq = nextSeq.getValue(client)
            rows.add(ChatTraceEntry(seq, virtualTimeMs, client, event(seq)))
            nextSeq[client] = seq + 1
        }
    }

    private fun assertTraceMatches(scenarioLabel: String, expected: List<ChatTraceEntry>, actual: List<ChatTraceEntry>) {
        assertEquals("[$scenarioLabel] trace row count", expected.size, actual.size)
        for (i in expected.indices) {
            val e = expected[i]
            val a = actual[i]
            val where = "[$scenarioLabel] row $i (${e.client} @ ${e.virtualTimeMs}ms)"
            assertEquals("$where eventSeq", e.eventSeq, a.eventSeq)
            assertEquals("$where virtualTimeMs", e.virtualTimeMs, a.virtualTimeMs)
            assertEquals("$where client", e.client, a.client)
            assertEquals("$where event", e.event, a.event)
            // ChatPeer.equals() is deliberately id-only (CONTRACT.md section 1.1);
            // discoveredAtMs and displayName need their own explicit checks so a
            // wrong discoveredAtMs cannot slip past the event-equality check above.
            if (e.event is ChatEvent.PeerFound && a.event is ChatEvent.PeerFound) {
                assertEquals("$where peer.discoveredAtMs", e.event.peer.discoveredAtMs, a.event.peer.discoveredAtMs)
                assertEquals("$where peer.displayName", e.event.peer.displayName, a.event.peer.displayName)
            }
        }
    }

    // -- 3.1 happyPair -----------------------------------------------------------

    @Test
    fun happyPairMatchesContractTable() = runTest {
        val recorder = ChatTraceRecorder()
        val pair = createChatPair(ChatScenario.HAPPY_PAIR, seed, recorder)
        val (idA, idB) = expectedPeerIds(seed)

        pair.clientA.start()
        pair.clientB.start()
        advanceTimeBy(100)
        runCurrent()
        pair.clientA.connect(idB.toHexString())
        advanceTimeBy(200)
        runCurrent()
        val msg1Hex = pair.clientA.send("hello")
        settle()

        val expected = ExpectedTrace()
        expected.add('A', 50) { seq -> ChatEvent.PeerFound(seq, ChatPeer(idB, 50)) }
        expected.add('B', 50) { seq -> ChatEvent.PeerFound(seq, ChatPeer(idA, 50)) }
        expected.add('A', 100) { seq -> ChatEvent.ConnectionChanged(seq, ChatConnectionState.Connecting) }
        expected.add('A', 150) { seq -> ChatEvent.ConnectionChanged(seq, ChatConnectionState.Connected) }
        expected.add('B', 150) { seq -> ChatEvent.ConnectionChanged(seq, ChatConnectionState.Connected) }
        expected.add('A', 200) { seq ->
            ChatEvent.LinkBudgetChanged(seq, ChatLinkBudget(LinkBudgetClass.TEXT, null, null, 0.7, 0))
        }
        expected.add('A', 300) { seq -> ChatEvent.MessageStatusChanged(seq, msg1Hex, ChatMessageDisplayStatus.Queued) }
        expected.add('A', 320) { seq -> ChatEvent.MessageStatusChanged(seq, msg1Hex, ChatMessageDisplayStatus.Transmitting) }
        expected.add('B', 380) { seq ->
            ChatEvent.MessageReceived(
                seq,
                ChatMessage(msg1Hex.hexToByteArray(), ChatMessage.Direction.INCOMING, "hello", idA.toHexString(), 380, ChatMessageDisplayStatus.Delivered),
            )
        }
        expected.add('A', 400) { seq -> ChatEvent.MessageStatusChanged(seq, msg1Hex, ChatMessageDisplayStatus.Delivered) }

        assertTraceMatches("happyPair", expected.rows, recorder.entries())
    }

    // -- 3.2 peerLoss --------------------------------------------------------------

    @Test
    fun peerLossMatchesContractTable() = runTest {
        val recorder = ChatTraceRecorder()
        val pair = createChatPair(ChatScenario.PEER_LOSS, seed, recorder)
        val (idA, idB) = expectedPeerIds(seed)

        pair.clientA.start()
        pair.clientB.start()
        advanceTimeBy(100)
        runCurrent()
        pair.clientA.connect(idB.toHexString())
        settle()

        val expected = ExpectedTrace()
        expected.add('A', 50) { seq -> ChatEvent.PeerFound(seq, ChatPeer(idB, 50)) }
        expected.add('B', 50) { seq -> ChatEvent.PeerFound(seq, ChatPeer(idA, 50)) }
        expected.add('A', 100) { seq -> ChatEvent.ConnectionChanged(seq, ChatConnectionState.Connecting) }
        expected.add('A', 150) { seq -> ChatEvent.ConnectionChanged(seq, ChatConnectionState.Connected) }
        expected.add('B', 150) { seq -> ChatEvent.ConnectionChanged(seq, ChatConnectionState.Connected) }
        expected.add('A', 500) { seq ->
            ChatEvent.ConnectionChanged(seq, ChatConnectionState.Disconnected(ChatReasonStrings.PEER_SILENCE_TIMEOUT))
        }
        expected.add('B', 500) { seq ->
            ChatEvent.ConnectionChanged(seq, ChatConnectionState.Disconnected(ChatReasonStrings.PEER_SILENCE_TIMEOUT))
        }
        expected.add('A', 510) { seq -> ChatEvent.PeerLost(seq, idB.toHexString(), ChatReasonStrings.PEER_SILENCE_TIMEOUT) }
        expected.add('B', 510) { seq -> ChatEvent.PeerLost(seq, idA.toHexString(), ChatReasonStrings.PEER_SILENCE_TIMEOUT) }

        assertTraceMatches("peerLoss", expected.rows, recorder.entries())
    }

    // -- 3.3 degradedThenRecovered ---------------------------------------------------

    @Test
    fun degradedThenRecoveredMatchesContractTable() = runTest {
        val recorder = ChatTraceRecorder()
        val pair = createChatPair(ChatScenario.DEGRADED_THEN_RECOVERED, seed, recorder)
        val (idA, idB) = expectedPeerIds(seed)

        pair.clientA.start()
        pair.clientB.start()
        advanceTimeBy(100)
        runCurrent()
        pair.clientA.connect(idB.toHexString())
        settle()

        val expected = ExpectedTrace()
        expected.add('A', 50) { seq -> ChatEvent.PeerFound(seq, ChatPeer(idB, 50)) }
        expected.add('B', 50) { seq -> ChatEvent.PeerFound(seq, ChatPeer(idA, 50)) }
        expected.add('A', 100) { seq -> ChatEvent.ConnectionChanged(seq, ChatConnectionState.Connecting) }
        expected.add('A', 150) { seq -> ChatEvent.ConnectionChanged(seq, ChatConnectionState.Connected) }
        expected.add('B', 150) { seq -> ChatEvent.ConnectionChanged(seq, ChatConnectionState.Connected) }
        expected.add('A', 200) { seq ->
            ChatEvent.LinkBudgetChanged(seq, ChatLinkBudget(LinkBudgetClass.TEXT, null, null, 0.7, 0))
        }
        expected.add('A', 400) { seq -> ChatEvent.ConnectionChanged(seq, ChatConnectionState.Degraded) }
        expected.add('A', 410) { seq ->
            ChatEvent.LinkBudgetChanged(seq, ChatLinkBudget(LinkBudgetClass.CONTROL_ONLY, null, null, 0.4, 0))
        }
        expected.add('A', 700) { seq -> ChatEvent.ConnectionChanged(seq, ChatConnectionState.Connected) }
        expected.add('A', 710) { seq ->
            ChatEvent.LinkBudgetChanged(seq, ChatLinkBudget(LinkBudgetClass.TEXT, null, null, 0.65, 0))
        }

        assertTraceMatches("degradedThenRecovered", expected.rows, recorder.entries())
    }

    // -- 3.4 sendFailure -------------------------------------------------------------

    @Test
    fun sendFailureMatchesContractTable() = runTest {
        val recorder = ChatTraceRecorder()
        val pair = createChatPair(ChatScenario.SEND_FAILURE, seed, recorder)
        val (idA, idB) = expectedPeerIds(seed)

        pair.clientA.start()
        pair.clientB.start()
        advanceTimeBy(100)
        runCurrent()
        pair.clientA.connect(idB.toHexString())
        advanceTimeBy(200)
        runCurrent()
        val msg1Hex = pair.clientA.send("will-fail")
        settle()

        val expected = ExpectedTrace()
        expected.add('A', 50) { seq -> ChatEvent.PeerFound(seq, ChatPeer(idB, 50)) }
        expected.add('B', 50) { seq -> ChatEvent.PeerFound(seq, ChatPeer(idA, 50)) }
        expected.add('A', 100) { seq -> ChatEvent.ConnectionChanged(seq, ChatConnectionState.Connecting) }
        expected.add('A', 150) { seq -> ChatEvent.ConnectionChanged(seq, ChatConnectionState.Connected) }
        expected.add('B', 150) { seq -> ChatEvent.ConnectionChanged(seq, ChatConnectionState.Connected) }
        expected.add('A', 300) { seq -> ChatEvent.MessageStatusChanged(seq, msg1Hex, ChatMessageDisplayStatus.Queued) }
        expected.add('A', 320) { seq -> ChatEvent.MessageStatusChanged(seq, msg1Hex, ChatMessageDisplayStatus.Transmitting) }
        expected.add('A', 450) { seq ->
            ChatEvent.MessageStatusChanged(seq, msg1Hex, ChatMessageDisplayStatus.Failed(ChatReasonStrings.NO_ACKNOWLEDGMENT))
        }

        assertTraceMatches("sendFailure", expected.rows, recorder.entries())
        // "B never receives anything" (CONTRACT.md section 3.4).
        assertEquals(0, recorder.entries().count { it.event is ChatEvent.MessageReceived })
    }

    // -- 3.5 duplicateIncoming -----------------------------------------------------

    @Test
    fun duplicateIncomingMatchesContractTableAndSuppressesTheDuplicate() = runTest {
        val recorder = ChatTraceRecorder()
        val pair = createChatPair(ChatScenario.DUPLICATE_INCOMING, seed, recorder)
        val (idA, idB) = expectedPeerIds(seed)

        pair.clientA.start()
        pair.clientB.start()
        advanceTimeBy(100)
        runCurrent()
        pair.clientA.connect(idB.toHexString())
        advanceTimeBy(200)
        runCurrent()
        val msgDupHex = pair.clientA.send("dup-test")
        settle()

        val expected = ExpectedTrace()
        expected.add('A', 50) { seq -> ChatEvent.PeerFound(seq, ChatPeer(idB, 50)) }
        expected.add('B', 50) { seq -> ChatEvent.PeerFound(seq, ChatPeer(idA, 50)) }
        expected.add('A', 100) { seq -> ChatEvent.ConnectionChanged(seq, ChatConnectionState.Connecting) }
        expected.add('A', 150) { seq -> ChatEvent.ConnectionChanged(seq, ChatConnectionState.Connected) }
        expected.add('B', 150) { seq -> ChatEvent.ConnectionChanged(seq, ChatConnectionState.Connected) }
        expected.add('A', 300) { seq -> ChatEvent.MessageStatusChanged(seq, msgDupHex, ChatMessageDisplayStatus.Queued) }
        expected.add('A', 320) { seq -> ChatEvent.MessageStatusChanged(seq, msgDupHex, ChatMessageDisplayStatus.Transmitting) }
        expected.add('B', 330) { seq ->
            ChatEvent.MessageReceived(
                seq,
                ChatMessage(
                    msgDupHex.hexToByteArray(),
                    ChatMessage.Direction.INCOMING,
                    "dup-test",
                    idA.toHexString(),
                    330,
                    ChatMessageDisplayStatus.Delivered,
                ),
            )
        }
        // t=340: the fault-injected duplicate redelivery produces NO row -- it is
        // deliberately absent from `expected` here, matching CONTRACT.md section
        // 3.5's table ("no event: ... this row exists in the timeline to prove
        // the absence of a second messageReceived").
        expected.add('A', 400) { seq -> ChatEvent.MessageStatusChanged(seq, msgDupHex, ChatMessageDisplayStatus.Delivered) }

        assertTraceMatches("duplicateIncoming", expected.rows, recorder.entries())
        // The dedup assertion this scenario exists to pin (CONTRACT.md section
        // 3.5): exactly one messageReceived for msgDup, not zero, not two.
        assertEquals(1, recorder.entries().count { it.event is ChatEvent.MessageReceived })
    }

    // -- 3.6 slowLink ----------------------------------------------------------------

    @Test
    fun slowLinkMatchesContractTable() = runTest {
        val recorder = ChatTraceRecorder()
        val pair = createChatPair(ChatScenario.SLOW_LINK, seed, recorder)
        val (idA, idB) = expectedPeerIds(seed)

        pair.clientA.start()
        pair.clientB.start()
        advanceTimeBy(100)
        runCurrent()
        pair.clientA.connect(idB.toHexString())
        advanceTimeBy(200)
        runCurrent()
        val msg1Hex = pair.clientA.send("slow")
        settle()

        val expected = ExpectedTrace()
        expected.add('A', 50) { seq -> ChatEvent.PeerFound(seq, ChatPeer(idB, 50)) }
        expected.add('B', 50) { seq -> ChatEvent.PeerFound(seq, ChatPeer(idA, 50)) }
        expected.add('A', 100) { seq -> ChatEvent.ConnectionChanged(seq, ChatConnectionState.Connecting) }
        expected.add('A', 150) { seq -> ChatEvent.ConnectionChanged(seq, ChatConnectionState.Connected) }
        expected.add('B', 150) { seq -> ChatEvent.ConnectionChanged(seq, ChatConnectionState.Connected) }
        expected.add('A', 160) { seq ->
            ChatEvent.LinkBudgetChanged(seq, ChatLinkBudget(LinkBudgetClass.CONTROL_ONLY, null, null, 0.5, 0))
        }
        expected.add('A', 300) { seq -> ChatEvent.MessageStatusChanged(seq, msg1Hex, ChatMessageDisplayStatus.Queued) }
        expected.add('A', 320) { seq -> ChatEvent.MessageStatusChanged(seq, msg1Hex, ChatMessageDisplayStatus.Transmitting) }
        expected.add('B', 2450) { seq ->
            ChatEvent.MessageReceived(
                seq,
                ChatMessage(msg1Hex.hexToByteArray(), ChatMessage.Direction.INCOMING, "slow", idA.toHexString(), 2450, ChatMessageDisplayStatus.Delivered),
            )
        }
        expected.add('A', 2500) { seq -> ChatEvent.MessageStatusChanged(seq, msg1Hex, ChatMessageDisplayStatus.Delivered) }

        assertTraceMatches("slowLink", expected.rows, recorder.entries())
    }
}
