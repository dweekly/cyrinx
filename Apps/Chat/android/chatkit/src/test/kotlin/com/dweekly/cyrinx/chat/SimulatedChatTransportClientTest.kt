@file:OptIn(ExperimentalCoroutinesApi::class)

package com.dweekly.cyrinx.chat

import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.Job
import kotlinx.coroutines.asCoroutineDispatcher
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.currentTime
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Collections
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.concurrent.thread
import kotlin.coroutines.CoroutineContext

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

    /**
     * ../../../CONTRACT.md section 2's "Discovery precedes connection (pinned)"
     * bullet, C3-28 adversarial-review fix: "connect(idHex) is valid only for a
     * peer this client has observed via peerFound ... connecting to an
     * unobserved or unknown idHex throws the unknown-peer transport-misuse
     * error and mutates nothing on either side." Both clients `start()`, but
     * time is never advanced before the first `connect()` attempt, so
     * clientA's own `peerFound` (and therefore its own `discoveredPeer`) has
     * not fired yet even though clientB is a perfectly live, reachable peer
     * with a real, correct id -- exactly the gap this fix closes (previously
     * `connect()` validated `peerIdHex` against clientB's live identity
     * directly, with no discovered-peer tracking at all, so this exact call
     * would have wrongly succeeded).
     *
     * Asserts the rejected call mutates/emits nothing on EITHER side, then
     * advances time so discovery actually fires and asserts the only events
     * either client now has are the two `peerFound` events discovery itself
     * produces -- nothing leaked from the earlier rejected `connect()` (no
     * stray `connecting`/`connected`, no half-armed handshake).
     */
    @Test
    fun connectBeforeDiscoveryThrowsAndMutatesNothingOnEitherSide() =
        runTest {
            val pair = createChatPair(ChatScenario.HAPPY_PAIR, 8L, null)
            val (_, idB) = expectedPeerIds(8L)
            val aEvents = collectEvents(pair.clientA)
            val bEvents = collectEvents(pair.clientB)

            pair.clientA.start()
            pair.clientB.start()
            // Deliberately do NOT advance time: peerFound is scheduled for
            // ChatScenarioTimings.PEER_FOUND_DELAY_MS from here but has not
            // fired yet, so clientA has not yet observed clientB via its own
            // discoveredPeer.
            runCurrent()

            var caught: ChatTransportError? = null
            try {
                pair.clientA.connect(idB.toHexString())
            } catch (e: ChatTransportError) {
                caught = e
            }
            runCurrent()

            assertTrue(
                "connect() before this client's own peerFound must be rejected as transport misuse",
                caught != null,
            )
            assertTrue("a rejected pre-discovery connect() must emit nothing on the caller", aEvents.isEmpty())
            assertTrue("a rejected pre-discovery connect() must emit nothing on the target peer either", bEvents.isEmpty())

            // Advance well past discovery (t=100, matching every CONTRACT.md
            // table's own connect()-call column, comfortably beyond
            // PEER_FOUND_DELAY_MS=50) and confirm the ONLY events produced are
            // discovery's own two peerFound events -- nothing else leaked from
            // the earlier rejected connect() attempt.
            advanceTimeBy(100)
            runCurrent()
            assertEquals(
                "the only event the caller should have after discovery is its own peerFound",
                listOf(true),
                aEvents.map { it is ChatEvent.PeerFound },
            )
            assertEquals(
                "the only event the target peer should have after discovery is its own peerFound",
                listOf(true),
                bEvents.map { it is ChatEvent.PeerFound },
            )

            // The exact same connect() call must now succeed, proving the
            // earlier rejection left no residual state on either side.
            pair.clientA.connect(idB.toHexString())
            settle()
            assertTrue(
                "connect() with the same idHex must succeed once discovery has actually happened",
                aEvents.filterIsInstance<ChatEvent.ConnectionChanged>().any { it.state == ChatConnectionState.Connected } &&
                    bEvents.filterIsInstance<ChatEvent.ConnectionChanged>().any { it.state == ChatConnectionState.Connected },
            )
        }

    // CONTRACT.md section 2's "Discovery precedes connection (pinned)" bullet
    // also reads "(and not subsequently lost)" -- i.e. its prose suggests a
    // connect() after this client's own peerLost should also be rejected.
    // There is deliberately no `connectAfterPeerLostThrows`-style test here:
    // none of the six scripted scenarios (including peerLoss) ever call
    // connect() a second time -- peerLoss's own script drives its scripted
    // disconnect/peerLost entirely FROM WITHIN the one connect() call that
    // already succeeded -- so this exact case is not reachable by driving a
    // scenario script alone. Exercising it ad hoc (calling connect() again,
    // by hand, after peerLoss's scripted peerLost fires) would additionally
    // require deciding whether a SCRIPTED disconnect (as opposed to a real
    // disconnect()/stop() call) should also clear `discoveredPeer` or flip
    // `terminal` -- CyrinxChatKit's Swift reference implementation does
    // neither (its own `discoveredPeer` is set exactly once, in
    // `emitPeerFound()`, and never reset; a scripted disconnect does not
    // touch it or `isTerminal`), so a same-shape ad hoc Kotlin test would
    // currently observe the second connect() SUCCEED, not throw -- the
    // opposite of the contract prose's "(and not subsequently lost)" clause.
    // Rather than have Kotlin unilaterally diverge from Swift's actual
    // behavior to chase that stricter reading, this is left as an open
    // question for CONTRACT.md's authors (see this task's final report).

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

    // -- Target ownership (../../../CONTRACT.md section 2's "Target ownership"
    // bullet) -- a PR review demonstrated, with focused harnesses, that a
    // receiver could observe events after its own disconnect() because
    // scheduled effects were owned by the SCHEDULING client, not the TARGET
    // client. These tests pin the fix. ------------------------------------

    @Test
    fun receiverDisconnectPreventsInboundMessageReceivedButNotSendersOwnStatuses() =
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
            // t=300: queued. happyPair's own table: transmitting at t=320,
            // delivered-to-B at t=380 (60ms after transmitting).
            val msgHex = pair.clientA.send("late-for-b")
            advanceTimeBy(30) // t=330: past transmitting, well before delivery.
            runCurrent()

            pair.clientB.disconnect()
            settle()

            assertTrue(
                "B must never receive a message whose delivery was still in flight when B disconnected " +
                    "-- CONTRACT.md section 2's receiver-side inbound cancellation",
                bEvents.filterIsInstance<ChatEvent.MessageReceived>().isEmpty(),
            )

            // "The sender's own transfer statuses are unaffected by the
            // receiver's disconnect -- the simulator models no delivery-failure
            // backchannel." (CONTRACT.md section 2's "Target ownership" bullet).
            val statuses =
                aEvents.filterIsInstance<ChatEvent.MessageStatusChanged>()
                    .filter { it.messageIdHex == msgHex }
                    .map { it.status }
            assertEquals(
                listOf(
                    ChatMessageDisplayStatus.Queued,
                    ChatMessageDisplayStatus.Transmitting,
                    ChatMessageDisplayStatus.Delivered,
                ),
                statuses,
            )
        }

    /**
     * CONTRACT.md section 2's new pinned "Both endpoints live for connection
     * establishment" bullet flips this test's expectation from what it used
     * to pin: a `connectionChanged(connected)` transition now fires only if
     * BOTH endpoints are still non-terminal at fire time, so B (the passive
     * side) disconnecting mid-handshake now drops the transition on BOTH
     * sides -- A's own `connected` is suppressed too, not just B's mirrored
     * copy.
     */
    @Test
    fun passiveSideDisconnectMidHandshakeSuppressesConnectedOnBothSides() =
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
            // A's handshake schedules BOTH sides' connected transition for
            // t=150 (CONNECTED_DELAY_MS=50 later). B (the passive side)
            // disconnects mid-handshake, well before t=150.
            advanceTimeBy(20) // t=120
            runCurrent()

            pair.clientB.disconnect()
            settle()

            assertEquals(
                "B must never observe connectionChanged(connected) after its own disconnect(), " +
                    "even though A's connect() scheduled that transition (passive-side handshake cancellation)",
                listOf(ChatConnectionState.Disconnected(ChatReasonStrings.USER_INITIATED)),
                bEvents.filterIsInstance<ChatEvent.ConnectionChanged>().map { it.state },
            )

            // CONTRACT.md's "Both endpoints live for connection establishment"
            // bullet: "either endpoint's disconnect()/stop() before the
            // transition fires drops the transition on BOTH sides." A itself
            // never disconnected, but B (the OTHER endpoint) did, so A's own
            // side of the handshake must ALSO never reach connected.
            assertEquals(
                "A must never observe connectionChanged(connected) either, once its peer B " +
                    "went terminal before the joint handshake transition fired -- CONTRACT.md's " +
                    "\"Both endpoints live\" bullet, reviewer probe P4",
                listOf(ChatConnectionState.Connecting),
                aEvents.filterIsInstance<ChatEvent.ConnectionChanged>().map { it.state },
            )
        }

    @Test
    fun activeSideDisconnectMidHandshakeCancelsBothSidesConnectedTransition() =
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
            advanceTimeBy(20) // t=120, well before t=150's connected.
            runCurrent()

            pair.clientA.disconnect()
            settle()

            // A's own in-flight handshake job -- the SAME job that would have
            // emitted B's connected transition too -- is cancelled outright by
            // A's own disconnect(), so NEITHER side ever reaches `connected`.
            assertEquals(
                listOf(ChatConnectionState.Connecting, ChatConnectionState.Disconnected(ChatReasonStrings.USER_INITIATED)),
                aEvents.filterIsInstance<ChatEvent.ConnectionChanged>().map { it.state },
            )
            assertTrue(
                "B must never reach connected either -- the cancelled job was the only thing that would have emitted it",
                bEvents.filterIsInstance<ChatEvent.ConnectionChanged>().none { it.state == ChatConnectionState.Connected },
            )
        }

    // -- start() semantics (../../../CONTRACT.md section 2's pinned "start()
    // semantics (pinned)" block) --------------------------------------------

    @Test
    fun startIsIdempotentAndDoesNotScheduleASecondPeerFound() =
        runTest {
            val pair = createChatPair(ChatScenario.HAPPY_PAIR, 8L, null)
            val aEvents = collectEvents(pair.clientA)
            val bEvents = collectEvents(pair.clientB)

            pair.clientA.start()
            pair.clientB.start()
            pair.clientA.start() // repeat call: must be a no-op.
            pair.clientB.start() // repeat call: must be a no-op.
            settle()

            assertEquals(1, aEvents.filterIsInstance<ChatEvent.PeerFound>().size)
            assertEquals(1, bEvents.filterIsInstance<ChatEvent.PeerFound>().size)
        }

    @Test
    fun discoveryIsArmedOnlyOnceBothClientsHaveStarted() =
        runTest {
            val pair = createChatPair(ChatScenario.HAPPY_PAIR, 8L, null)
            val aEvents = collectEvents(pair.clientA)
            val bEvents = collectEvents(pair.clientB)

            pair.clientA.start()
            // Far past PEER_FOUND_DELAY_MS -- if A's start() (wrongly) armed
            // discovery on its own, this would already have fired.
            advanceTimeBy(1_000)
            runCurrent()

            assertTrue(
                "A alone must not discover anything until B also starts",
                aEvents.filterIsInstance<ChatEvent.PeerFound>().isEmpty(),
            )
            assertTrue(bEvents.filterIsInstance<ChatEvent.PeerFound>().isEmpty())

            pair.clientB.start()
            settle()

            val aFound = aEvents.filterIsInstance<ChatEvent.PeerFound>().single()
            val bFound = bEvents.filterIsInstance<ChatEvent.PeerFound>().single()
            // "the moment the second client starts, each client's peerFound is
            // scheduled at its section 3 scenario offset relative to THAT
            // moment" -- not relative to A's much-earlier start() call.
            assertEquals(1_050L, aFound.peer.discoveredAtMs)
            assertEquals(1_050L, bFound.peer.discoveredAtMs)
        }

    /**
     * "A stopped client cannot be restarted" (../../../CONTRACT.md section
     * 2's pinned `start()` semantics) is now a REJECTION, not a silent
     * no-op: round-4's fresh pin folds this case into "start() on a
     * terminal client is rejected as transport misuse and arms nothing on
     * either side" -- [stop] sets [SimulatedChatTransportClient.isTerminal]
     * true exactly like [SimulatedChatTransportClient.disconnect] does, and
     * `start()` now rejects any terminal client uniformly, whichever of the
     * two made it terminal.
     */
    @Test
    fun stoppedClientCannotBeRestarted() =
        runTest {
            val pair = createChatPair(ChatScenario.HAPPY_PAIR, 8L, null)
            val aEvents = collectEvents(pair.clientA)
            val bEvents = collectEvents(pair.clientB)

            pair.clientA.stop() // stop before ever starting.
            settle()

            var caught: ChatTransportError? = null
            try {
                pair.clientA.start() // must be rejected: "a stopped client cannot be restarted."
            } catch (e: ChatTransportError) {
                caught = e
            }
            pair.clientB.start()
            settle()

            assertTrue(
                "start() on an already-stopped client must be rejected as transport misuse",
                caught != null,
            )
            assertTrue(
                "a rejected start() must schedule nothing",
                aEvents.filterIsInstance<ChatEvent.PeerFound>().isEmpty(),
            )
            assertTrue(
                "B's peer (A) never really started from B's perspective either, so B must not discover anything",
                bEvents.filterIsInstance<ChatEvent.PeerFound>().isEmpty(),
            )
        }

    /**
     * Reviewer probe Q1 (round-4 fresh pins, both platforms): `A.disconnect();
     * A.start(); B.start(); advance` -> no `peerFound` on either client, and
     * `A.start()` itself throws. Exercises both fresh pins at once:
     * "start() on a terminal client is rejected as transport misuse and arms
     * nothing on either side" (A's own `start()` throws, and -- checked
     * BEFORE the [SimulatedChatTransportClient.start]'s `started` CAS --
     * never flips A's own `started` flag either), and "Discovery is armed
     * only once BOTH clients of a pair have started AND neither is
     * terminal" (B's subsequent `start()` sees A as never-started, since
     * A's own `start()` threw before ever recording it, so B does not arm
     * discovery either, even though B itself is perfectly healthy).
     */
    @Test
    fun disconnectThenStartRejectsAndArmsDiscoveryOnNeitherSide() =
        runTest {
            val pair = createChatPair(ChatScenario.HAPPY_PAIR, 8L, null)
            val aEvents = collectEvents(pair.clientA)
            val bEvents = collectEvents(pair.clientB)

            pair.clientA.disconnect()
            settle()

            var caught: ChatTransportError? = null
            try {
                pair.clientA.start()
            } catch (e: ChatTransportError) {
                caught = e
            }
            pair.clientB.start()
            settle()

            assertTrue(
                "start() on an already-disconnected (terminal) client must be rejected as transport misuse",
                caught != null,
            )
            assertTrue(
                "A's own start() must arm nothing, on either side",
                aEvents.filterIsInstance<ChatEvent.PeerFound>().isEmpty(),
            )
            assertTrue(
                "B's start() must not arm discovery either -- A never really started (its start() " +
                    "threw before flipping the started flag), so B alone is not 'both started'",
                bEvents.filterIsInstance<ChatEvent.PeerFound>().isEmpty(),
            )
        }

    // -- Terminality (C3-28 terminality review, ../../../CONTRACT.md section
    // 2's new pinned "disconnect() is terminal for the client instance" /
    // "Both endpoints live for connection establishment" / "Atomic
    // validation" bullets) --------------------------------------------------

    /**
     * Reviewer probe P1 / CONTRACT.md's required "disconnect-then-peer-connect
     * (terminal target never reconnects)" test: once B has disconnected, A's
     * connect() targeting B must be rejected outright (a transport-misuse
     * throw, per "a peer's connect() targeting a terminal client throws and
     * schedules nothing on either side"), and B must never emit anything at
     * all after its own disconnected(userInitiated) -- not even as a result of
     * a connect() attempt scheduled entirely AFTER B was already terminal.
     */
    @Test
    fun disconnectThenPeerConnectRejectsAndBEmitsNothingAfterDisconnect() =
        runTest {
            val pair = createChatPair(ChatScenario.HAPPY_PAIR, 8L, null)
            val (_, idB) = expectedPeerIds(8L)
            val bEvents = collectEvents(pair.clientB)

            pair.clientA.start()
            pair.clientB.start()
            settle()

            pair.clientB.disconnect()
            settle()
            val bEventsAfterDisconnect = bEvents.toList()

            var caught: ChatTransportError? = null
            try {
                pair.clientA.connect(idB.toHexString())
            } catch (e: ChatTransportError) {
                caught = e
            }
            settle()

            assertTrue(
                "A.connect() targeting an already-terminal B must be rejected as transport misuse",
                caught != null,
            )
            assertEquals(
                "B must emit nothing at all after its own disconnected(userInitiated), even from a " +
                    "peer's connect() attempt scheduled entirely AFTER B went terminal -- terminality, " +
                    "not generation equality alone, is the gate",
                bEventsAfterDisconnect,
                bEvents,
            )
            assertEquals(
                listOf(ChatConnectionState.Disconnected(ChatReasonStrings.USER_INITIATED)),
                bEvents.filterIsInstance<ChatEvent.ConnectionChanged>().map { it.state },
            )
        }

    /**
     * Reviewer probe P2: connect the pair normally, then B disconnects, then A
     * calls send(). The scheduled delivery effect targeting B captures B's
     * ALREADY-terminal (post-disconnect) generation at schedule time -- a
     * generation-equality-only check would wrongly treat that as still valid,
     * since nothing bumps B's generation again before the effect fires. B
     * must still receive nothing; A's own transfer statuses proceed exactly
     * as they would have if B had never disconnected (CONTRACT.md: "The
     * sender's own transfer statuses are unaffected by the receiver's
     * disconnect").
     */
    @Test
    fun sendToAlreadyTerminalReceiverDeliversNothingButSendersOwnStatusesProceed() =
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

            pair.clientB.disconnect()
            settle()

            val msgHex = pair.clientA.send("b-already-gone")
            settle()

            assertTrue(
                "B must never receive a message sent to it after B already went terminal",
                bEvents.filterIsInstance<ChatEvent.MessageReceived>().isEmpty(),
            )
            val statuses =
                aEvents.filterIsInstance<ChatEvent.MessageStatusChanged>()
                    .filter { it.messageIdHex == msgHex }
                    .map { it.status }
            assertEquals(
                "A's own transfer statuses proceed unaffected by B's earlier disconnect -- the " +
                    "simulator models no delivery-failure backchannel",
                listOf(
                    ChatMessageDisplayStatus.Queued,
                    ChatMessageDisplayStatus.Transmitting,
                    ChatMessageDisplayStatus.Delivered,
                ),
                statuses,
            )
        }

    @Test
    fun connectAfterOwnDisconnectIsRejected() =
        runTest {
            val pair = createChatPair(ChatScenario.HAPPY_PAIR, 8L, null)
            val (_, idB) = expectedPeerIds(8L)

            pair.clientA.start()
            pair.clientB.start()
            settle()

            pair.clientA.disconnect()
            settle()

            var caught: ChatTransportError? = null
            try {
                pair.clientA.connect(idB.toHexString())
            } catch (e: ChatTransportError) {
                caught = e
            }
            assertTrue(
                "connect() on an already-disconnected client must be rejected as transport misuse",
                caught != null,
            )
        }

    @Test
    fun sendAfterOwnDisconnectIsRejected() =
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

            pair.clientA.disconnect()
            settle()

            var caught: ChatTransportError? = null
            try {
                pair.clientA.send("too-late")
            } catch (e: ChatTransportError) {
                caught = e
            }
            assertTrue(
                "send() on an already-disconnected client must be rejected as transport misuse",
                caught != null,
            )
        }

    @Test
    fun stopAfterDisconnectCompletesTheEventsStreamCleanlyWithNoDuplicateDisconnectedEvent() =
        runTest {
            val pair = createChatPair(ChatScenario.HAPPY_PAIR, 8L, null)
            val aEvents = mutableListOf<ChatEvent>()
            val collectorJob = backgroundScope.launch { pair.clientA.events.collect { aEvents.add(it) } }
            runCurrent()

            pair.clientA.start()
            pair.clientB.start()
            settle()

            pair.clientA.disconnect()
            settle()
            val eventsAfterDisconnect = aEvents.toList()

            pair.clientA.stop()
            settle()

            assertEquals(
                "stop() after disconnect() must not emit a second connectionChanged(disconnected) " +
                    "-- the state is already disconnected",
                eventsAfterDisconnect,
                aEvents,
            )
            assertTrue(
                "events Flow must complete cleanly once stop() finishes, even after a prior disconnect()",
                collectorJob.isCompleted,
            )
            assertFalse(
                "the Flow must complete normally, not because something cancelled it",
                collectorJob.isCancelled,
            )
        }

    /**
     * backgroundJobs-leak regression coverage (C3-28 terminality review): every
     * scheduled job (discovery, connect() handshake, send()) must remove
     * itself from [SimulatedChatTransportClient.backgroundJobCount]'s backing
     * list the moment it completes, so a long-lived client's bookkeeping stays
     * bounded by jobs CURRENTLY in flight rather than growing by one entry per
     * call for the client's entire lifetime.
     */
    @Test
    fun backgroundJobsDoesNotGrowAfterEachCompletedSend() =
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
            settle()
            assertEquals(
                "the discovery + connect handshake jobs must have removed themselves once complete",
                0,
                pair.clientA.backgroundJobCount,
            )

            pair.clientA.send("first")
            settle()
            assertEquals(0, pair.clientA.backgroundJobCount)

            pair.clientA.send("second")
            settle()
            assertEquals(
                "a long-lived session's backgroundJobs registry must not grow per completed send -- " +
                    "each job must remove itself on completion",
                0,
                pair.clientA.backgroundJobCount,
            )
        }

    // -- Atomic validation under genuine concurrency (reviewer probe P3) ----
    // Deliberately real (non-virtual) threads: kotlinx-coroutines-test's
    // TestCoroutineScheduler is single-threaded and cooperative, so it cannot
    // exhibit an interleaving race at all -- every other test in this class
    // runs under that virtual scheduler and could not, by construction, catch
    // a regression in SimulatedChatTransportClient's locking. No wall-clock
    // sleep anywhere in either test below: every wait is on a real signal
    // (CountDownLatch, Thread.join, or polling the real Thread.State), never a
    // guessed duration -- matching ChatEventBusTest's
    // `emitAndCloseCannotRaceEvenWhenBuildBlocks` precedent for the same kind
    // of lock-race regression test in this module.

    /**
     * A peer-driven effect captures a target's generation as "valid" (mirroring
     * how connect()/send() capture `otherGenerationAt...` at schedule time),
     * is then PAUSED before it reaches its atomic check-and-mutate call,
     * disconnect() runs to completion on a genuinely concurrent thread while
     * it is paused, and only THEN is the effect allowed to proceed. It must
     * not mutate.
     */
    @Test
    fun disconnectWinsRaceAgainstAnEffectPausedBeforeItsAtomicCheck() {
        val executor = Executors.newFixedThreadPool(4)
        val scope = CoroutineScope(executor.asCoroutineDispatcher() + Job())
        try {
            val pair = SimulatedChatPair.create(ChatScenario.HAPPY_PAIR, 40L, scope, VirtualTimeSource { 0L })

            // Captured "at schedule time," mirroring otherGenerationAtConnect/
            // otherGenerationAtSend -- BEFORE B goes terminal.
            val capturedGeneration = pair.clientB.currentGeneration()
            val mutated = AtomicBoolean(false)
            val readyToProceed = CountDownLatch(1)
            val effectDone = CountDownLatch(1)

            val effectThread =
                thread(start = false) {
                    readyToProceed.await(10, TimeUnit.SECONDS)
                    pair.clientB.runIfLive(capturedGeneration) { mutated.set(true) }
                    effectDone.countDown()
                }
            effectThread.start()

            // B disconnects to completion WHILE the effect thread is parked
            // before its atomic check -- exactly the scheduling a real
            // dispatcher could produce, forced deterministic here via the
            // latch rather than left to chance.
            runBlocking { pair.clientB.disconnect() }
            readyToProceed.countDown()

            assertTrue(effectDone.await(10, TimeUnit.SECONDS))
            effectThread.join(10_000)

            assertFalse(
                "a peer-driven effect whose generation was captured before disconnect() must still " +
                    "be rejected at fire time once disconnect() has already marked the target terminal " +
                    "-- terminality, not generation equality alone, is the gate",
                mutated.get(),
            )
        } finally {
            scope.cancel()
            executor.shutdown()
        }
    }

    /**
     * The complementary direction: proves disconnect()'s own invalidation
     * genuinely CANNOT interleave with an already-in-flight validated effect
     * -- it must wait for the effect's atomic action to finish before its own
     * critical section (marking terminal, bumping generation) can run, the
     * same lock-ordering guarantee ChatEventBusTest's
     * `emitAndCloseCannotRaceEvenWhenBuildBlocks` proves for [ChatEventBus]'s
     * own lock. "Is disconnect() still blocked" is proven by polling the real
     * `Thread.State` (`BLOCKED` is reachable only by genuinely contending for
     * a monitor another thread currently holds) -- never by timing.
     */
    @Test
    fun disconnectCannotInterleaveWithAnInFlightValidatedEffect() {
        val executor = Executors.newFixedThreadPool(4)
        val scope = CoroutineScope(executor.asCoroutineDispatcher() + Job())
        try {
            val pair = SimulatedChatPair.create(ChatScenario.HAPPY_PAIR, 41L, scope, VirtualTimeSource { 0L })

            val capturedGeneration = pair.clientB.currentGeneration()
            val order = Collections.synchronizedList(mutableListOf<String>())
            val actionEntered = CountDownLatch(1)
            val releaseAction = CountDownLatch(1)

            val effectThread =
                thread(start = false) {
                    pair.clientB.runIfLive(capturedGeneration) {
                        order.add("effect-start")
                        actionEntered.countDown()
                        releaseAction.await(10, TimeUnit.SECONDS)
                        order.add("effect-end")
                    }
                }
            effectThread.start()
            assertTrue(
                "the effect must reach its locked action before we race disconnect() against it",
                actionEntered.await(10, TimeUnit.SECONDS),
            )

            val disconnectThread =
                thread(start = true) {
                    runBlocking { pair.clientB.disconnect() }
                    order.add("disconnect-end")
                }

            // Poll (no wall-clock sleep) until disconnect() is OBSERVABLY
            // blocked trying to enter the same monitor the in-flight effect
            // is holding.
            var spins = 0
            while (disconnectThread.state != Thread.State.BLOCKED && disconnectThread.isAlive) {
                Thread.onSpinWait()
                spins++
                check(spins < 50_000_000) {
                    "disconnect() thread never entered BLOCKED state contending for lifecycleLock"
                }
            }
            assertEquals(
                "disconnect() must be genuinely blocked on the same lock the in-flight effect " +
                    "holds, not merely 'not yet scheduled'",
                Thread.State.BLOCKED,
                disconnectThread.state,
            )
            assertEquals(listOf("effect-start"), order.toList())

            releaseAction.countDown()
            effectThread.join(10_000)
            disconnectThread.join(10_000)

            assertEquals(
                "the in-flight effect's action must run to completion BEFORE disconnect()'s own " +
                    "invalidation can proceed -- they can never interleave",
                listOf("effect-start", "effect-end", "disconnect-end"),
                order.toList(),
            )
            assertTrue("B must be terminal once disconnect() actually completes", pair.clientB.isTerminal())
        } finally {
            scope.cancel()
            executor.shutdown()
        }
    }

    // -- Linearized admission and registration (round-4 fresh pin, reviewer
    // probes Q2/Q3) -- genuinely concurrent, real-thread reproductions of
    // the exact TOCTOU hole a round-4 reviewer demonstrated in the
    // PREVIOUS `launchBackgroundJob` design: a `disconnect()` that runs its
    // entire sweep, seeing an empty registry, while the admitting call
    // (`connect()`/`send()`) is paused -- via [HoldingDispatcher], not a
    // guessed sleep -- at the exact moment its handshake/send job's `Job
    // .start()` first attempts to dispatch. That pause point is AFTER this
    // client's own terminal-admission check and its job's
    // `backgroundJobs`/`pendingSendJobs` registration have ALREADY run
    // (atomically, under `lifecycleLock`, per [launchBackgroundJob]'s doc
    // comment) but BEFORE a single line of the job's own body has executed
    // -- i.e. it reproduces the shape of the round-3 TOCTOU bug (a
    // concurrent `disconnect()` racing a not-yet-run scheduled job) while
    // proving the NEW design closes it: the job is always found (and
    // cancelled/terminalized) by the sweep, never orphaned. Uses
    // [ChatTraceRecorder] (a synchronous, in-call-stack sink -- see that
    // class's doc comment) rather than a `Flow` collector, so every
    // assertion below reflects EXACTLY what has been emitted so far with no
    // separate collector-thread draining/timing to reason about.

    /**
     * Reviewer probe Q2: `connect()`'s handshake job is held at its first
     * dispatch (AFTER `Connecting` has already fired and the job is already
     * durably registered in `backgroundJobs`). A concurrent `disconnect()`
     * is run to completion while it is held -- it must find and cancel that
     * job. Only THEN is the hold released. Assertion: no event of any kind,
     * on EITHER client, follows `disconnect()`'s own `Disconnected` event --
     * in particular no `connectionChanged(connected)` and no
     * `linkBudgetChanged`, which the held job's body would otherwise have
     * gone on to emit had it ever run a single line.
     */
    @Test
    fun disconnectFindsAndCancelsAConnectHandshakeJobHeldAtItsFirstDispatch() {
        val readyLatch = CountDownLatch(1)
        val releaseLatch = CountDownLatch(1)
        val executor = Executors.newFixedThreadPool(4)
        val holdingDispatcher = HoldingDispatcher(executor.asCoroutineDispatcher())
        val scope = CoroutineScope(holdingDispatcher + Job())
        try {
            val recorder = ChatTraceRecorder()
            val pair = SimulatedChatPair.create(ChatScenario.HAPPY_PAIR, 8L, scope, VirtualTimeSource { 0L }, recorder)
            val (_, idB) = expectedPeerIds(8L)

            // Discover for real first -- dispatch is not yet armed to hold, so
            // this proceeds at its ordinary (short) real-wall-clock pace.
            // connect() now requires this client to have actually observed its
            // peer via peerFound (CONTRACT.md section 2's "Discovery precedes
            // connection (pinned)" bullet) before the handshake-hold below is
            // even reachable -- previously connect() validated `peerIdHex`
            // against the peer's live identity directly, with no
            // discovered-peer tracking, so this setup phase was unnecessary.
            runBlocking {
                pair.clientA.start()
                pair.clientB.start()
            }
            spinUntil("discovery jobs never settled") {
                pair.clientA.backgroundJobCount == 0 && pair.clientB.backgroundJobCount == 0
            }

            // NOW arm the hold, so it catches connect()'s handshake job's
            // first dispatch specifically, not any leftover dispatch from the
            // discovery jobs above (already fully settled per the spin-wait).
            holdingDispatcher.armNextDispatch(readyLatch, releaseLatch)

            val connectThread =
                thread(start = false) {
                    runBlocking { pair.clientA.connect(idB.toHexString()) }
                }
            connectThread.start()

            assertTrue(
                "connect()'s handshake job must reach its first dispatch (Job.start()) before we " +
                    "race disconnect() against it",
                readyLatch.await(10, TimeUnit.SECONDS),
            )
            // onAdmitted() -- which emits Connecting -- always runs strictly
            // BEFORE launchBackgroundJob calls Job.start(), on the SAME
            // (connectThread) thread, so by the time readyLatch has fired,
            // Connecting is guaranteed already recorded.
            assertEquals(
                "sanity: Connecting must have already fired (atomically admitted) before the job's " +
                    "own dispatch could ever be reached",
                listOf(ChatConnectionState.Connecting),
                recorder.entries().filter { it.client == 'A' }
                    .mapNotNull { (it.event as? ChatEvent.ConnectionChanged)?.state },
            )

            runBlocking { pair.clientA.disconnect() }
            val entriesRightAfterDisconnect = recorder.entries()

            releaseLatch.countDown()
            connectThread.join(10_000)

            assertEquals(
                "nothing may fire, on either client, after disconnect() has returned -- the " +
                    "handshake job was already atomically registered (its Job.start() had already " +
                    "been reached) when disconnect()'s sweep ran, but it had not yet executed a " +
                    "single line of its own body, so the sweep's cancellation must have prevented " +
                    "every one of its scheduled emissions, including linkBudgetChanged",
                entriesRightAfterDisconnect,
                recorder.entries(),
            )
            assertEquals(
                listOf(ChatConnectionState.Connecting, ChatConnectionState.Disconnected(ChatReasonStrings.USER_INITIATED)),
                recorder.entries().filter { it.client == 'A' }
                    .mapNotNull { (it.event as? ChatEvent.ConnectionChanged)?.state },
            )
            assertTrue(
                "B must never observe connected either -- the handshake job that would have emitted " +
                    "it on both sides was cancelled before it ran a single line",
                recorder.entries().filter { it.client == 'B' }
                    .none { (it.event as? ChatEvent.ConnectionChanged)?.state == ChatConnectionState.Connected },
            )
        } finally {
            scope.cancel()
            executor.shutdown()
        }
    }

    /**
     * Reviewer probe Q3: same shape as Q2, but for `send()`. The pair is
     * first connected for real (an ordinary, un-held dispatch -- "under a
     * real dispatcher it elapses in real (short) wall-clock time," per the
     * class doc comment), then `send()`'s own background job is held at its
     * first dispatch -- AFTER `Queued` has fired and the message is already
     * durably registered in `pendingSendJobs` -- while a concurrent
     * `disconnect()` races it. The message must end up terminalized
     * `failed("disconnected")` by `disconnect()`'s own sweep
     * (`failNonterminalOutgoingSends`) -- never left sitting in
     * `pendingSendJobs` with no further status transition, and never
     * progressing to `transmitting`/`delivered` once the hold is released.
     */
    @Test
    fun disconnectTerminalizesASendJobHeldAtItsFirstDispatchNeverLeavingItPending() {
        val readyLatch = CountDownLatch(1)
        val releaseLatch = CountDownLatch(1)
        val executor = Executors.newFixedThreadPool(4)
        val holdingDispatcher = HoldingDispatcher(executor.asCoroutineDispatcher())
        val scope = CoroutineScope(holdingDispatcher + Job())
        try {
            val recorder = ChatTraceRecorder()
            val pair = SimulatedChatPair.create(ChatScenario.HAPPY_PAIR, 8L, scope, VirtualTimeSource { 0L }, recorder)
            val (_, idB) = expectedPeerIds(8L)

            // Discover for real first -- dispatch is not yet armed to hold, so
            // this proceeds at its ordinary (short) real-wall-clock pace.
            // connect() now requires this client to have actually observed its
            // peer via peerFound (CONTRACT.md section 2's "Discovery precedes
            // connection (pinned)" bullet) before it can succeed at all.
            runBlocking {
                pair.clientA.start()
                pair.clientB.start()
            }
            spinUntil("discovery jobs never settled") {
                pair.clientA.backgroundJobCount == 0 && pair.clientB.backgroundJobCount == 0
            }

            // Connect for real first -- dispatch is not yet armed to hold,
            // so this proceeds at its ordinary (short) real-wall-clock pace.
            runBlocking { pair.clientA.connect(idB.toHexString()) }
            spinUntil("connect()'s handshake job never settled") { pair.clientA.backgroundJobCount == 0 }
            assertTrue(
                "sanity: A must be connected before send() is exercised",
                recorder.entries().any {
                    it.client == 'A' && (it.event as? ChatEvent.ConnectionChanged)?.state == ChatConnectionState.Connected
                },
            )

            // NOW arm the hold, so it catches send()'s job's first dispatch
            // specifically, not any leftover dispatch from the connect()
            // handshake job above (already fully settled per the spin-wait).
            holdingDispatcher.armNextDispatch(readyLatch, releaseLatch)

            // send() itself does not RETURN until launchBackgroundJob's
            // Job.start() call does -- which is exactly what the held
            // dispatch blocks -- so the calling thread is stuck inside
            // send() itself at this point, not merely inside some
            // fire-and-forget background job; messageIdHex is therefore
            // independently recomputed (CONTRACT.md section 2's
            // "Message-ID stream (pinned)", via the SAME helper
            // ChatScenarioTestSupport's other message-ID tests use) rather
            // than read off send()'s return value.
            val messageIdHex = expectedFirstMessageId(8L, 'A')
            val sendThread =
                thread(start = false) {
                    runBlocking { pair.clientA.send("held-by-dispatcher") }
                }
            sendThread.start()

            assertTrue(
                "send()'s background job must reach its first dispatch (Job.start()) before we " +
                    "race disconnect() against it",
                readyLatch.await(10, TimeUnit.SECONDS),
            )
            assertEquals(
                "sanity: Queued must have already fired (atomically admitted, together with this " +
                    "message's pendingSendJobs registration) before the job's own dispatch could " +
                    "ever be reached",
                1,
                pair.clientA.pendingSendJobCount,
            )

            runBlocking { pair.clientA.disconnect() }
            val entriesRightAfterDisconnect = recorder.entries()
            assertEquals(
                "disconnect()'s own sweep must have already terminalized the message -- it can never " +
                    "return leaving an admitted send sitting in pendingSendJobs",
                0,
                pair.clientA.pendingSendJobCount,
            )

            releaseLatch.countDown()
            sendThread.join(10_000)

            assertEquals(
                "nothing may fire, on either client, after disconnect() has returned -- in particular " +
                    "no transmitting/delivered for the held send, which never ran a single line of " +
                    "its own body",
                entriesRightAfterDisconnect,
                recorder.entries(),
            )
            assertEquals(
                "the held send must be terminalized failed(\"disconnected\") by the sweep, never left " +
                    "pending and never allowed to progress past queued on its own",
                listOf(ChatMessageDisplayStatus.Queued, ChatMessageDisplayStatus.Failed(ChatReasonStrings.DISCONNECTED)),
                recorder.entries().filter { it.client == 'A' }
                    .mapNotNull { (it.event as? ChatEvent.MessageStatusChanged) }
                    .filter { it.messageIdHex == messageIdHex }
                    .map { it.status },
            )
        } finally {
            scope.cancel()
            executor.shutdown()
        }
    }
}

/**
 * A [CoroutineDispatcher] wrapping [delegate] that can be armed, via
 * [armNextDispatch], to block the calling thread's NEXT [dispatch] call --
 * counting down [readyLatch] to signal "the dispatch attempt has begun,"
 * then awaiting [releaseLatch] -- before delegating; every OTHER call
 * (unarmed, or after the armed one has already fired once) passes straight
 * through with no pause at all. Re-armable (a fresh pair of latches each
 * time) so a single dispatcher/scope can be used across an initial
 * (un-held) setup phase and then a specifically-held call later in the
 * same test, without the FIRST dispatch ever attempted (e.g. an unrelated
 * setup job's) accidentally consuming the hold meant for a LATER one.
 *
 * Used by the Q2/Q3 regression tests above to pause a
 * [SimulatedChatTransportClient]'s `Job.start()` call (inside
 * [SimulatedChatTransportClient.launchBackgroundJob]) at the exact moment
 * its underlying coroutine would first be scheduled to run -- i.e. AFTER
 * that job has already been atomically admitted into
 * `backgroundJobs`/`pendingSendJobs` under `lifecycleLock`, but BEFORE a
 * single line of its body executes -- so a concurrent `disconnect()`
 * racing against the held dispatch can be proven to find (and cancel) the
 * already-registered job, and the job's body can be proven to never run at
 * all once released.
 */
private class HoldingDispatcher(
    private val delegate: CoroutineDispatcher,
) : CoroutineDispatcher() {
    private val armed = AtomicBoolean(false)

    @Volatile
    private var readyLatch: CountDownLatch? = null

    @Volatile
    private var releaseLatch: CountDownLatch? = null

    fun armNextDispatch(readyLatch: CountDownLatch, releaseLatch: CountDownLatch) {
        this.readyLatch = readyLatch
        this.releaseLatch = releaseLatch
        armed.set(true)
    }

    override fun dispatch(context: CoroutineContext, block: Runnable) {
        if (armed.compareAndSet(true, false)) {
            readyLatch!!.countDown()
            releaseLatch!!.await(10, TimeUnit.SECONDS)
        }
        delegate.dispatch(context, block)
    }
}

/**
 * Busy-polls (no wall-clock sleep -- [Thread.onSpinWait], matching this
 * file's existing `disconnectThread.state != Thread.State.BLOCKED` polling
 * idiom above) until [predicate] is true. Bounded by ELAPSED REAL TIME
 * ([timeoutMs]), not an iteration count: unlike the lock-contention polls
 * elsewhere in this file (which resolve at memory-access speed, so a huge
 * fixed spin-count bound is a safe proxy for "effectively never"), this
 * helper is also used to wait out a chain of the production code's own
 * REAL `delay()` calls settling (tens of milliseconds under a real
 * dispatcher -- see the class doc comment) -- a fixed spin-count bound
 * checked with zero actual pausing between checks can exhaust itself in
 * far less than that on a fast CPU and fail spuriously, which a
 * wall-clock deadline does not.
 */
private fun spinUntil(description: String, timeoutMs: Long = 10_000, predicate: () -> Boolean) {
    val deadlineNanos = System.nanoTime() + timeoutMs * 1_000_000
    while (!predicate()) {
        Thread.onSpinWait()
        check(System.nanoTime() < deadlineNanos) { description }
    }
}
