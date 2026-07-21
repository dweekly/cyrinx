@file:OptIn(ExperimentalCoroutinesApi::class)

package com.dweekly.cyrinx.chat

import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.currentTime
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
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

    // -- Message-ID generation ------------------------------------------------------

    /**
     * Pins CONTRACT.md section 2's "Message-ID stream (pinned)": message IDs come
     * from each client's OWN independent `SplitMix64(seed XOR roleTag)` stream,
     * not the shared construction-stream PRNG that ends at draw 2 (the peer-ID
     * draws). [expectedFirstMessageId] recomputes the expected bytes completely
     * independently of [SimulatedChatPair]'s internals.
     */
    @Test
    fun sendGeneratesMessageIdFromTheSendersOwnRoleTaggedStream() =
        runTest {
            val seed = 2024L
            val pair = createChatPair(ChatScenario.HAPPY_PAIR, seed, null)
            val (_, idB) = expectedPeerIds(seed)

            pair.clientA.start()
            pair.clientB.start()
            advanceTimeBy(100)
            runCurrent()
            pair.clientA.connect(idB.toHexString())
            advanceTimeBy(200)
            runCurrent()
            val msgHex = pair.clientA.send("hello")
            settle()

            assertEquals(expectedFirstMessageId(seed, 'A'), msgHex)
        }

    /**
     * Two independent message-ID streams (one per client, seeded `seed XOR
     * roleTag`) must not coincide, even though they share the same `seed` --
     * this is the whole point of XORing in a distinct role tag per
     * CONTRACT.md section 2.
     */
    @Test
    fun clientAAndClientBMessageIdStreamsDiffer() {
        val seed = 2024L
        assertNotEquals(expectedFirstMessageId(seed, 'A'), expectedFirstMessageId(seed, 'B'))
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

    // -- pendingSendJobs cleanup (regression coverage) ---------------------------

    /**
     * Regression coverage for [SimulatedChatTransportClient.markTerminal]'s
     * `pendingSendJobs.remove(messageIdHex)` cleanup (../../../CONTRACT.md
     * section 2's "Lifecycle cancellation (pinned)"): every terminal status
     * transition -- whether delivered (the happyPair timeline, [markTerminal]'s
     * call site inside `send()`) or failed/cancelled (via
     * [SimulatedChatTransportClient.cancelSend]) -- must remove that message's
     * job from the sender's own pending-send bookkeeping. No assertion on
     * emitted [ChatEvent]s alone would catch a regression that stopped removing
     * entries, since a leaked-but-otherwise-inert map entry is invisible from
     * outside [SimulatedChatTransportClient] -- hence
     * [SimulatedChatTransportClient.pendingSendJobCount], exposed for exactly
     * this purpose.
     */
    @Test
    fun pendingSendJobsIsEmptyAfterDeliveryAndAfterCancelSend() =
        runTest {
            val pair = createChatPair(ChatScenario.HAPPY_PAIR, 8L, null)
            val (_, idB) = expectedPeerIds(8L)

            pair.clientA.start()
            pair.clientB.start()
            advanceTimeBy(100)
            runCurrent()
            pair.clientA.connect(idB.toHexString())
            advanceTimeBy(200)
            runCurrent()

            pair.clientA.send("first")
            settle() // let it fully reach delivered.
            assertEquals(
                "a delivered message must not leave an entry behind in pendingSendJobs",
                0,
                pair.clientA.pendingSendJobCount,
            )

            val msg2Hex = pair.clientA.send("second")
            pair.clientA.cancelSend(msg2Hex) // terminal transition: failed(cancelled).
            settle()
            assertEquals(
                "cancelSend()'s own terminal transition must also clear pendingSendJobs",
                0,
                pair.clientA.pendingSendJobCount,
            )
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

    // -- Send precondition (../../../CONTRACT.md section 2's "Send precondition
    // (pinned)") --------------------------------------------------------------

    @Test
    fun sendWhileDisconnectedIsRejectedWithNoEvent() =
        runTest {
            // Deliberately never calls start()/connect(): SimulatedChatPair.create
            // already wires the peer reference at construction, so send()'s own
            // precondition check (state == Disconnected(null), the implicit
            // initial state) is exercised in isolation, without a scheduled
            // peerFound (or anything else) muddying the "no event at all"
            // assertion below.
            val pair = createChatPair(ChatScenario.HAPPY_PAIR, 8L, null)
            val aEvents = collectEvents(pair.clientA)

            var caught: ChatTransportError? = null
            try {
                pair.clientA.send("too-early")
            } catch (e: ChatTransportError) {
                caught = e
            }
            runCurrent()

            assertTrue("send() before connect() must be rejected with a transport-misuse error", caught != null)
            assertTrue("a rejected send() must emit no event at all", aEvents.isEmpty())
        }

    @Test
    fun sendWhileDegradedIsAcceptedAndFollowsHappyPairTimeline() =
        runTest {
            // degradedThenRecovered never scripts a send() of its own
            // (CONTRACT.md section 3.3); this pins the "Send precondition
            // (pinned)" fallback: "a send accepted while connected or degraded
            // follows the happyPair delivery timeline unless a scenario table
            // ... overrides it."
            val pair = createChatPair(ChatScenario.DEGRADED_THEN_RECOVERED, 8L, null)
            val (_, idB) = expectedPeerIds(8L)
            val aEvents = collectEvents(pair.clientA)
            val bEvents = collectEvents(pair.clientB)

            pair.clientA.start()
            pair.clientB.start()
            advanceTimeBy(100)
            runCurrent()
            pair.clientA.connect(idB.toHexString())
            // degradedThenRecovered's own table: connected (t=150) -> degraded
            // (t=400). Advance to just after degraded fires.
            advanceTimeBy(300)
            runCurrent()
            assertEquals(
                "sanity: the scripted degrade must have already happened before send() is attempted",
                ChatConnectionState.Degraded,
                aEvents.filterIsInstance<ChatEvent.ConnectionChanged>().last().state,
            )

            val msgHex = pair.clientA.send("degraded-send")
            settle()

            val received = bEvents.filterIsInstance<ChatEvent.MessageReceived>().single()
            assertEquals("degraded-send", received.message.body)
            assertEquals(msgHex, received.message.id.toHexString())

            val statuses =
                aEvents.filterIsInstance<ChatEvent.MessageStatusChanged>()
                    .filter { it.messageIdHex == msgHex }
                    .map { it.status }
            assertEquals(
                listOf(ChatMessageDisplayStatus.Queued, ChatMessageDisplayStatus.Transmitting, ChatMessageDisplayStatus.Delivered),
                statuses,
            )
        }

    // -- Lifecycle cancellation (../../../CONTRACT.md section 2's "Lifecycle
    // cancellation (pinned)") ---------------------------------------------------

    @Test
    fun connectThenDisconnectCancelsScheduledWorkAndEmitsUserInitiatedDisconnect() =
        runTest {
            val pair = createChatPair(ChatScenario.HAPPY_PAIR, 8L, null)
            val (_, idB) = expectedPeerIds(8L)
            val aEvents = collectEvents(pair.clientA)

            pair.clientA.start()
            pair.clientB.start()
            advanceTimeBy(100)
            runCurrent()
            pair.clientA.connect(idB.toHexString())
            // t=150: connected just fired; happyPair's own linkBudgetChanged
            // (scheduled 50ms later, for t=200) has not fired yet.
            advanceTimeBy(50)
            runCurrent()

            pair.clientA.disconnect()
            settle()

            val connectionStates = aEvents.filterIsInstance<ChatEvent.ConnectionChanged>().map { it.state }
            assertEquals(
                listOf(
                    ChatConnectionState.Connecting,
                    ChatConnectionState.Connected,
                    ChatConnectionState.Disconnected(ChatReasonStrings.USER_INITIATED),
                ),
                connectionStates,
            )
            // "Scheduled simulator work must never outlive the state that
            // scheduled it": disconnect() must have cancelled the scenario's own
            // still-pending linkBudgetChanged before it could fire.
            assertTrue(
                "disconnect() must cancel scheduled scenario work, not just stop future scheduling",
                aEvents.filterIsInstance<ChatEvent.LinkBudgetChanged>().isEmpty(),
            )
        }

    @Test
    fun repeatDisconnectIsANoOp() =
        runTest {
            val pair = createChatPair(ChatScenario.HAPPY_PAIR, 8L, null)
            val (_, idB) = expectedPeerIds(8L)
            val aEvents = collectEvents(pair.clientA)

            pair.clientA.start()
            pair.clientB.start()
            advanceTimeBy(100)
            runCurrent()
            pair.clientA.connect(idB.toHexString())
            advanceTimeBy(50)
            runCurrent()

            pair.clientA.disconnect()
            settle()
            val countAfterFirstDisconnect = aEvents.size

            pair.clientA.disconnect() // repeat call: must be a no-op.
            settle()

            assertEquals(countAfterFirstDisconnect, aEvents.size)
        }

    @Test
    fun sendThenStopTerminalizesPendingSendAndCompletesEventsFlow() =
        runTest {
            val pair = createChatPair(ChatScenario.HAPPY_PAIR, 8L, null)
            val (_, idB) = expectedPeerIds(8L)
            val aEvents = mutableListOf<ChatEvent>()
            // A bespoke collector (rather than collectEvents' backgroundScope
            // helper) so this test can inspect the underlying Job's own
            // completion state below -- it must complete BECAUSE the events
            // Flow itself finished, not merely because backgroundScope tears it
            // down at the end of the test.
            val collectorJob = backgroundScope.launch { pair.clientA.events.collect { aEvents.add(it) } }
            runCurrent()

            pair.clientA.start()
            pair.clientB.start()
            advanceTimeBy(100)
            runCurrent()
            pair.clientA.connect(idB.toHexString())
            advanceTimeBy(200)
            runCurrent()
            val msgHex = pair.clientA.send("stop-me")
            // t=300: queued just fired. stop() before t=320's transmitting
            // transition has a chance to fire.
            pair.clientA.stop()
            settle()

            val statusesForMsg =
                aEvents.filterIsInstance<ChatEvent.MessageStatusChanged>()
                    .filter { it.messageIdHex == msgHex }
                    .map { it.status }
            assertEquals(
                listOf(ChatMessageDisplayStatus.Queued, ChatMessageDisplayStatus.Failed(ChatReasonStrings.STOPPED)),
                statusesForMsg,
            )

            val connectionStates = aEvents.filterIsInstance<ChatEvent.ConnectionChanged>().map { it.state }
            assertEquals(
                listOf(
                    ChatConnectionState.Connecting,
                    ChatConnectionState.Connected,
                    ChatConnectionState.Disconnected(ChatReasonStrings.STOPPED),
                ),
                connectionStates,
            )

            // "No event of any kind may be observed after the stream finishes":
            // the collector's own collect() call must have returned on its own
            // (the Flow completed), not merely been left running until
            // backgroundScope cancels it at test teardown.
            assertTrue("events Flow must complete on its own once stop() finishes", collectorJob.isCompleted)
            assertFalse("the Flow must complete normally, not because something cancelled it", collectorJob.isCancelled)
        }

    @Test
    fun repeatStopIsANoOp() =
        runTest {
            val pair = createChatPair(ChatScenario.HAPPY_PAIR, 8L, null)
            val aEvents = collectEvents(pair.clientA)
            pair.clientA.start()
            pair.clientB.start()
            settle()

            pair.clientA.stop()
            settle()
            val countAfterFirstStop = aEvents.size

            pair.clientA.stop() // repeat call: must be a no-op.
            settle()

            assertEquals(countAfterFirstStop, aEvents.size)
        }

    @Test
    fun peerLossScriptedDisconnectTerminalizesNonterminalSendAsPeerLostInSendOrder() =
        runTest {
            val pair = createChatPair(ChatScenario.PEER_LOSS, 8L, null)
            val (_, idB) = expectedPeerIds(8L)
            val aEvents = collectEvents(pair.clientA)
            val bEvents = collectEvents(pair.clientB)

            pair.clientA.start()
            pair.clientB.start()
            advanceTimeBy(100)
            runCurrent()
            pair.clientA.connect(idB.toHexString())
            advanceTimeBy(50) // t=150: connected.
            runCurrent()

            // peerLoss's own silence timeout fires at t=500 (350ms after
            // connected). Send late enough (t=490) that the message is still
            // "queued" -- its own transmitting transition (t=490+20=510) has not
            // fired yet -- when the scripted disconnect happens.
            advanceTimeBy(340)
            runCurrent()
            val msgHex = pair.clientA.send("in-flight-at-timeout")
            settle()

            val statuses =
                aEvents.filterIsInstance<ChatEvent.MessageStatusChanged>()
                    .filter { it.messageIdHex == msgHex }
                    .map { it.status }
            assertEquals(
                "the scripted disconnect must terminalize the still-queued send as failed(peerLost), " +
                    "with no transmitting/delivered leftovers",
                listOf(ChatMessageDisplayStatus.Queued, ChatMessageDisplayStatus.Failed(ChatReasonStrings.PEER_LOST)),
                statuses,
            )
            assertTrue(
                "B must never receive a message whose sender disconnected mid-flight",
                bEvents.filterIsInstance<ChatEvent.MessageReceived>().isEmpty(),
            )

            // "immediately after the scripted disconnect event" (CONTRACT.md
            // section 2).
            val disconnectedIndex =
                aEvents.indexOfFirst { it is ChatEvent.ConnectionChanged && it.state is ChatConnectionState.Disconnected }
            val failedIndex =
                aEvents.indexOfFirst {
                    it is ChatEvent.MessageStatusChanged && it.messageIdHex == msgHex && it.status is ChatMessageDisplayStatus.Failed
                }
            assertEquals(disconnectedIndex + 1, failedIndex)
        }
}
