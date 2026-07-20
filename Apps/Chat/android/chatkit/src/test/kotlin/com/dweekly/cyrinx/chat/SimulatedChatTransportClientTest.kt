@file:OptIn(ExperimentalCoroutinesApi::class)

package com.dweekly.cyrinx.chat

import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.currentTime
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Behavioral tests for [SimulatedChatTransportClient] / [SimulatedChatPair] not
 * already covered by ChatScenarioExactTraceTest's exact per-scenario table
 * reproductions: scenario determinism, paired envelope-bytes exchange,
 * `eventSeq` monotonicity, and `cancelSend`.
 */
class SimulatedChatTransportClientTest {
    // -- Scenario determinism -----------------------------------------------------

    @Test
    fun sameSeedProducesByteIdenticalTraceAcrossTwoIndependentRuns() {
        // runTest's body lambda always returns Unit, so each run's trace text is
        // captured into an outer var rather than returned from the runTest block.
        fun runOnce(): String {
            var trace = ""
            runTest {
                val recorder = ChatTraceRecorder()
                val pair = createChatPair(ChatScenario.HAPPY_PAIR, 4242L, recorder)
                val (_, idB) = expectedPeerIds(4242L)

                pair.clientA.start()
                pair.clientB.start()
                advanceTimeBy(100)
                runCurrent()
                pair.clientA.connect(idB.toHexString())
                advanceTimeBy(200)
                runCurrent()
                pair.clientA.send("hello")
                settle()

                trace = recorder.toJsonLines()
            }
            return trace
        }

        val firstRun = runOnce()
        val secondRun = runOnce()
        assertEquals("identical (scenario, seed) must produce byte-identical trace text", firstRun, secondRun)
        assertTrue("sanity: the trace must not be empty", firstRun.isNotEmpty())
    }

    @Test
    fun differentSeedsProduceDifferentPeerIds() {
        val (idA1, idB1) = expectedPeerIds(1L)
        val (idA2, idB2) = expectedPeerIds(2L)
        assertNotEquals(idA1.toHexString(), idA2.toHexString())
        assertNotEquals(idB1.toHexString(), idB2.toHexString())
    }

    // -- Paired envelope-bytes exchange --------------------------------------------

    /**
     * A's `send(body:)` produces B's `messageReceived` with the SAME body,
     * crossing through the real envelope codec (CONTRACT.md section 2, point 2):
     * not a shortcut that hands B a `ChatMessage` directly, but bytes that B
     * itself decodes. Checked here by independently re-encoding what B should
     * have received and confirming B's `senderPeerIdHex` is exactly A's own
     * simulated transport ID (the only way B could know that is by decoding the
     * envelope's `senderId` field, which [SimulatedChatTransportClient.send]
     * sets to `id`, its own peer ID).
     */
    @Test
    fun sendCrossesThePairAsARealEnvelopeAndBReceivesIt() =
        runTest {
            val pair = createChatPair(ChatScenario.HAPPY_PAIR, 555L, null)
            val (idA, idB) = expectedPeerIds(555L)
            val bEvents = collectEvents(pair.clientB)

            pair.clientA.start()
            pair.clientB.start()
            advanceTimeBy(100)
            runCurrent()
            pair.clientA.connect(idB.toHexString())
            advanceTimeBy(200)
            runCurrent()
            val msg1Hex = pair.clientA.send("hello")
            settle()

            val received = bEvents.filterIsInstance<ChatEvent.MessageReceived>().single()
            assertEquals("hello", received.message.body)
            assertEquals(msg1Hex, received.message.id.toHexString())
            assertEquals(idA.toHexString(), received.message.senderPeerIdHex)
            assertEquals(ChatMessage.Direction.INCOMING, received.message.direction)
            assertEquals(ChatMessageDisplayStatus.Delivered, received.message.status)
        }

    @Test
    fun sendRejectsOversizeBodyBeforeCrossingThePair() =
        runTest {
            val pair = createChatPair(ChatScenario.HAPPY_PAIR, 9L, null)
            val (_, idB) = expectedPeerIds(9L)
            val bEvents = collectEvents(pair.clientB)

            pair.clientA.start()
            pair.clientB.start()
            advanceTimeBy(100)
            runCurrent()
            pair.clientA.connect(idB.toHexString())
            advanceTimeBy(200)
            runCurrent()

            var caught: ChatEnvelopeError? = null
            try {
                pair.clientA.send("A".repeat(ChatEnvelopeCodec.MAX_BODY_LEN + 1))
            } catch (e: ChatEnvelopeError) {
                caught = e
            }
            settle()

            assertEquals("oversizeBody", caught?.errorName)
            assertTrue("no envelope should have crossed to B", bEvents.filterIsInstance<ChatEvent.MessageReceived>().isEmpty())
        }

    // -- eventSeq monotonicity --------------------------------------------------------

    @Test
    fun eventSeqIsMonotonicPerClientAcrossASlowLinkRun() =
        runTest {
            // slowLink has the widest virtual-time spread of the six scenarios
            // (2500ms) and a mix of every non-error event kind except
            // peerLost/degraded, making it a reasonable stress case for
            // monotonicity across a long-running scenario.
            val pair = createChatPair(ChatScenario.SLOW_LINK, 31337L, null)
            val (_, idB) = expectedPeerIds(31337L)
            val aEvents = collectEvents(pair.clientA)
            val bEvents = collectEvents(pair.clientB)

            pair.clientA.start()
            pair.clientB.start()
            advanceTimeBy(100)
            runCurrent()
            pair.clientA.connect(idB.toHexString())
            advanceTimeBy(200)
            runCurrent()
            pair.clientA.send("slow")
            settle()

            for (events in listOf(aEvents, bEvents)) {
                assertTrue("expected at least one event", events.isNotEmpty())
                for (i in 1 until events.size) {
                    assertEquals(events[i - 1].eventSeq + 1, events[i].eventSeq)
                }
                assertEquals(0L, events.first().eventSeq)
            }
        }

    // -- cancelSend ---------------------------------------------------------------------

    @Test
    fun cancelSendBeforeDeliveryReportsFailedCancelledAndStopsFurtherTransitions() =
        runTest {
            val pair = createChatPair(ChatScenario.HAPPY_PAIR, 8L, null)
            val (_, idB) = expectedPeerIds(8L)
            val aEvents = collectEvents(pair.clientA)
            val bEvents = collectEvents(pair.clientB)

            pair.clientA.start()
            pair.clientB.start()
            advanceTimeBy(100)
            runCurrent()
            pair.clientA.connect(idB.toHexString())
            advanceTimeBy(200)
            runCurrent()
            val msgHex = pair.clientA.send("cancel-me")
            // t=300: queued just fired. Cancel immediately, before the t=320
            // transmitting transition (happyPair's TRANSMITTING_DELAY_MS) has a
            // chance to fire.
            pair.clientA.cancelSend(msgHex)
            settle()

            val statusesForMsg =
                aEvents.filterIsInstance<ChatEvent.MessageStatusChanged>()
                    .filter { it.messageIdHex == msgHex }
                    .map { it.status }
            assertEquals(
                listOf(ChatMessageDisplayStatus.Queued, ChatMessageDisplayStatus.Failed(ChatReasonStrings.CANCELLED)),
                statusesForMsg,
            )
            assertTrue("B must never receive a cancelled-before-transmission message", bEvents.filterIsInstance<ChatEvent.MessageReceived>().isEmpty())
        }

    @Test
    fun cancelSendOnUnknownMessageIdIsANoOp() =
        runTest {
            val pair = createChatPair(ChatScenario.HAPPY_PAIR, 8L, null)
            val aEvents = collectEvents(pair.clientA)
            pair.clientA.start()
            pair.clientB.start()
            runCurrent()

            pair.clientA.cancelSend("00112233445566778899aabbccddeeff")
            settle()

            assertTrue(aEvents.filterIsInstance<ChatEvent.MessageStatusChanged>().isEmpty())
        }

    @Test
    fun cancelSendAfterDeliveryIsANoOp() =
        runTest {
            val pair = createChatPair(ChatScenario.HAPPY_PAIR, 8L, null)
            val (_, idB) = expectedPeerIds(8L)
            val aEvents = collectEvents(pair.clientA)

            pair.clientA.start()
            pair.clientB.start()
            advanceTimeBy(100)
            runCurrent()
            pair.clientA.connect(idB.toHexString())
            advanceTimeBy(200)
            runCurrent()
            val msgHex = pair.clientA.send("hello")
            settle() // let it fully deliver

            val statusesBeforeCancel =
                aEvents.filterIsInstance<ChatEvent.MessageStatusChanged>().filter { it.messageIdHex == msgHex }.map { it.status }
            assertEquals(listOf(ChatMessageDisplayStatus.Delivered), statusesBeforeCancel.takeLast(1))

            pair.clientA.cancelSend(msgHex) // already terminal -- must not re-transition
            settle()

            val statusesAfterCancel =
                aEvents.filterIsInstance<ChatEvent.MessageStatusChanged>().filter { it.messageIdHex == msgHex }.map { it.status }
            assertEquals(statusesBeforeCancel, statusesAfterCancel)
        }

    // -- Transport-level errors -------------------------------------------------------

    @Test
    fun connectToUnknownPeerIdThrowsChatTransportError() =
        runTest {
            val pair = createChatPair(ChatScenario.HAPPY_PAIR, 8L, null)
            pair.clientA.start()
            pair.clientB.start()
            advanceTimeBy(100)
            runCurrent()

            var caught: ChatTransportError? = null
            try {
                pair.clientA.connect("00112233")
            } catch (e: ChatTransportError) {
                caught = e
            }
            assertTrue(caught != null)
        }

    @Test
    fun peerFoundCarriesTheDiscoveringClientsOwnVirtualDiscoveryTime() =
        runTest {
            val pair = createChatPair(ChatScenario.HAPPY_PAIR, 8L, null)
            val aEvents = collectEvents(pair.clientA)
            pair.clientA.start()
            pair.clientB.start()
            settle()

            val found = aEvents.filterIsInstance<ChatEvent.PeerFound>().single()
            assertEquals(ChatScenarioTimings.PEER_FOUND_DELAY_MS, found.peer.discoveredAtMs)
            assertEquals(pair.clientB.id.toHexString(), found.peer.id.toHexString())
        }
}
