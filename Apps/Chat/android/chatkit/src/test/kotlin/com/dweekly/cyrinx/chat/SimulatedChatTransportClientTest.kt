@file:OptIn(ExperimentalCoroutinesApi::class)

package com.dweekly.cyrinx.chat

import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.Job
import kotlinx.coroutines.asCoroutineDispatcher
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.currentTime
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.withTimeout
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.Timeout
import java.util.Collections
import java.util.concurrent.CountDownLatch
import java.util.concurrent.CyclicBarrier
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger
import kotlin.concurrent.thread
import kotlin.coroutines.CoroutineContext

/**
 * Behavioral tests for [SimulatedChatTransportClient] / [SimulatedChatPair] not
 * already covered by ChatScenarioExactTraceTest's exact per-scenario table
 * reproductions: scenario determinism, paired envelope-bytes exchange,
 * `eventSeq` monotonicity, and `cancelSend`.
 */
class SimulatedChatTransportClientTest {
    @get:Rule
    val testTimeout: Timeout = Timeout.seconds(90)

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

    /**
     * C3-28 round-6 follow-up regression: an adversarial verifier reproduced
     * that peerLoss's both-targeted silence-timeout duo (../../../CONTRACT.md
     * section 3.2's `disconnected`+`peerLost` pair, scripted for BOTH
     * clients at t=500/510) was NOT actually B's own independent local
     * timeline the way ../../../CONTRACT.md section 2's round-6-pinned
     * "Post-admission script locality" bullet requires: B's half of the
     * script was embedded INLINE inside A's own `connect()` handshake job,
     * registered only in A's own `backgroundJobs`. Calling `A.disconnect()`
     * strictly after the joint `connected` admission but strictly before
     * the scripted t=500 timeout cancelled A's own handshake job -- and, as
     * a side effect, the WHOLE coroutine carrying B's supposedly-
     * independent script too -- so B silently never got its scripted
     * silence-timeout `disconnected`/`peerLost` duo, even though B itself
     * never disconnected and remained perfectly live. Swift's twin never
     * had this bug: it schedules each side's own copy of every post-connect
     * step independently, on the actual target
     * (`SimulatedChatTransportClient.swift`'s `scheduleOwned`).
     *
     * This test pins the fix: B's scripted duo must still fire, at its
     * pinned virtual times, unaffected by A's own disconnect(); A's own
     * timeline must stop at its own user-initiated disconnect() and emit
     * nothing scripted afterward (A's own script IS legitimately cancelled
     * by A's own disconnect() -- only B's independent half must survive).
     */
    @Test
    fun peerLossADisconnectBeforeSilenceTimeoutDoesNotCancelBsIndependentScript() =
        runTest {
            val recorder = ChatTraceRecorder()
            val pair = createChatPair(ChatScenario.PEER_LOSS, 8L, recorder)
            val (idA, idB) = expectedPeerIds(8L)

            pair.clientA.start()
            pair.clientB.start()
            advanceTimeBy(100)
            runCurrent()
            pair.clientA.connect(idB.toHexString())
            advanceTimeBy(50) // t=150: joint `connected` just admitted on both sides.
            runCurrent()

            // Strictly after admission (t=150), strictly before the scripted
            // t=500 silence timeout.
            advanceTimeBy(100) // t=250.
            runCurrent()
            pair.clientA.disconnect()
            settle()

            // Advance well past the scripted t=510 peerLost.
            advanceTimeBy(400) // t=650.
            settle()

            val bEntries = recorder.entries().filter { it.client == 'B' }
            val bDisconnected =
                bEntries.single {
                    it.event is ChatEvent.ConnectionChanged &&
                        (it.event as ChatEvent.ConnectionChanged).state is ChatConnectionState.Disconnected
                }
            assertEquals(
                "B's scripted silence-timeout disconnected must fire at its pinned virtual time " +
                    "(CONTRACT.md section 3.2), unaffected by A's own earlier disconnect()",
                500L,
                bDisconnected.virtualTimeMs,
            )
            assertEquals(
                ChatConnectionState.Disconnected(ChatReasonStrings.PEER_SILENCE_TIMEOUT),
                (bDisconnected.event as ChatEvent.ConnectionChanged).state,
            )

            val bPeerLost = bEntries.single { it.event is ChatEvent.PeerLost }
            assertEquals(
                "B's scripted peerLost must fire at its pinned virtual time, unaffected by A's own " +
                    "earlier disconnect()",
                510L,
                bPeerLost.virtualTimeMs,
            )
            val bPeerLostEvent = bPeerLost.event as ChatEvent.PeerLost
            assertEquals(idA.toHexString(), bPeerLostEvent.peerIdHex)
            assertEquals(ChatReasonStrings.PEER_SILENCE_TIMEOUT, bPeerLostEvent.reason)

            // "immediately after the scripted disconnect event" (CONTRACT.md
            // section 2) -- B's own timeline.
            val bDisconnectedIndex = bEntries.indexOf(bDisconnected)
            val bPeerLostIndex = bEntries.indexOf(bPeerLost)
            assertEquals(bDisconnectedIndex + 1, bPeerLostIndex)

            val aEntries = recorder.entries().filter { it.client == 'A' }
            val aConnectionStates =
                aEntries.mapNotNull { (it.event as? ChatEvent.ConnectionChanged)?.state }
            assertEquals(
                "A's own timeline stops at its own user-initiated disconnect(); the scripted " +
                    "peerSilenceTimeout never fires on A because A's own disconnect() legitimately " +
                    "cancelled A's own still-pending script",
                listOf(
                    ChatConnectionState.Connecting,
                    ChatConnectionState.Connected,
                    ChatConnectionState.Disconnected(ChatReasonStrings.USER_INITIATED),
                ),
                aConnectionStates,
            )
            assertTrue(
                "A must never emit peerLost -- its own disconnect() cancelled its own script before " +
                    "the scripted silence timeout",
                aEntries.none { it.event is ChatEvent.PeerLost },
            )
            assertEquals(
                "nothing may fire on A after its own disconnect() event",
                ChatConnectionState.Disconnected(ChatReasonStrings.USER_INITIATED),
                (aEntries.last().event as ChatEvent.ConnectionChanged).state,
            )
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

    /**
     * Reviewer probe R3 (both platforms; this is the Kotlin-side
     * regression): a dropped handshake must cancel the ENTIRE remainder of
     * a scenario's own post-connect script, on BOTH sides, not merely the
     * `connected` transition itself -- ../../../CONTRACT.md section 2's
     * round-5-pinned "Post-connect script admission" bullet. Unlike
     * [passiveSideDisconnectMidHandshakeSuppressesConnectedOnBothSides]
     * above (which uses `happyPair`, whose only post-connect step is a
     * single `linkBudgetChanged`), this uses `degradedThenRecovered`
     * specifically because its own table (../../../CONTRACT.md section 3.3)
     * has the richest post-connect script of any scenario -- text ->
     * degraded -> controlOnly -> connected (again) -> text again, spanning
     * t=200 through t=710 -- so "the WHOLE script is cancelled, not just the
     * first step" is actually exercised, not merely asserted. B disconnects
     * at t=120, well before the t=150 handshake fires; `settle()` then
     * drives virtual time all the way through and past t=710 (idle), so
     * every one of those later scripted steps has had its chance to fire
     * and provably did not.
     */
    @Test
    fun degradedThenRecoveredDroppedHandshakeCancelsWholeScriptOnBothSides() =
        runTest {
            val pair = createChatPair(ChatScenario.DEGRADED_THEN_RECOVERED, 8L, null)
            val (_, idB) = expectedPeerIds(8L)
            val aEvents = collectEvents(pair.clientA)
            val bEvents = collectEvents(pair.clientB)

            pair.clientA.start()
            pair.clientB.start()
            advanceTimeBy(100)
            runCurrent()
            pair.clientA.connect(idB.toHexString())
            // The joint connected transition is scheduled for t=150
            // (CONNECTED_DELAY_MS=50 after t=100's connect() call). B
            // disconnects at t=120, well before it fires.
            advanceTimeBy(20) // t=120
            runCurrent()

            pair.clientB.disconnect()
            // Advance through the ENTIRE rest of degradedThenRecovered's own
            // script and beyond (connected -> text -> degraded ->
            // controlOnly -> connected again -> text again, ending at
            // t=710) -- "Post-connect script admission" (round-5 fresh
            // pin): none of it may fire on EITHER side once the joint
            // connected handshake itself never happened.
            settle()

            assertEquals(
                "A must never observe connected -- B (the OTHER endpoint) went terminal before the " +
                    "joint handshake transition fired, dropping it (and the rest of the script) on " +
                    "BOTH sides",
                listOf(ChatConnectionState.Connecting),
                aEvents.filterIsInstance<ChatEvent.ConnectionChanged>().map { it.state },
            )
            assertEquals(
                "B's only connectionChanged is its own disconnect() -- it never reached connected " +
                    "either, and no scripted step ever ran on B's side afterward",
                listOf(ChatConnectionState.Disconnected(ChatReasonStrings.USER_INITIATED)),
                bEvents.filterIsInstance<ChatEvent.ConnectionChanged>().map { it.state },
            )
            assertTrue(
                "no scripted degraded/recovered step (t=400/t=700 in degradedThenRecovered's own " +
                    "table) may fire on either side once the handshake itself was dropped",
                (aEvents + bEvents).none {
                    it is ChatEvent.ConnectionChanged && it.state == ChatConnectionState.Degraded
                },
            )
            assertTrue(
                "no scripted linkBudgetChanged step (t=200/410/710 in degradedThenRecovered's own " +
                    "table) may fire on either side once the handshake itself was dropped",
                (aEvents + bEvents).none { it is ChatEvent.LinkBudgetChanged },
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
            // Fail-closed: a hit deadline on readyToProceed must FAIL the
            // test rather than let the effect thread silently proceed as if
            // it had actually been released on schedule.
            val readyToProceedSignaled = AtomicBoolean(false)

            val effectThread =
                thread(start = false) {
                    readyToProceedSignaled.set(readyToProceed.await(10, TimeUnit.SECONDS))
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
            assertFalse("effect thread did not finish within 10s", effectThread.isAlive)

            assertTrue(
                "readyToProceed latch must be signaled well within 10s -- a hit deadline here is a " +
                    "stalled test harness, not a finding about disconnect()/runIfLive",
                readyToProceedSignaled.get(),
            )
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
            // Fail-closed: a hit deadline on releaseAction must FAIL the
            // test rather than let the effect's action silently proceed as
            // if it had actually been released on schedule.
            val releaseActionSignaled = AtomicBoolean(false)

            val effectThread =
                thread(start = false) {
                    pair.clientB.runIfLive(capturedGeneration) {
                        order.add("effect-start")
                        actionEntered.countDown()
                        releaseActionSignaled.set(releaseAction.await(10, TimeUnit.SECONDS))
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
            assertFalse("effect thread did not finish within 10s", effectThread.isAlive)
            assertFalse("disconnect thread did not finish within 10s", disconnectThread.isAlive)

            assertTrue(
                "releaseAction latch must be signaled well within 10s -- a hit deadline here is a " +
                    "stalled test harness, not a finding about disconnect()/runIfLive",
                releaseActionSignaled.get(),
            )
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
    // (`connect()`/`send()`) has already returned but its handshake/send
    // job's BODY is still held -- via [HoldingDispatcher], not a guessed
    // sleep -- at the exact moment that body would first run. That pause
    // point is AFTER this client's own terminal-admission check and its
    // job's `backgroundJobs`/`pendingSendJobs` registration have ALREADY run
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
    //
    // Round-6 rework (both tests below): CONTRACT.md section 2's round-6
    // fresh "Command ownership" pin now holds [commandInFlight] for the
    // COMPLETE `connect()`/`send()` call, including `Job.start()` (see
    // SimulatedChatTransportClient.commandInFlight's and
    // .launchBackgroundJob's doc comments). In these tests specifically,
    // [HoldingDispatcher] captures the
    // job's Runnable before submitting it to the delegate executor. It
    // blocks neither the calling thread nor a coroutine worker, so these two
    // tests no longer need a SEPARATE thread for `connect()`/`send()` itself:
    // both run to completion directly on the test's own thread, and
    // only `disconnect()` -- which races the still-held job body, not
    // `connect()`/`send()`'s own span -- needs its own thread (to poll its
    // `Job.join()` park state before releasing the hold, exactly as round-5
    // already did for `disconnect()`).

    @Test
    fun holdingDispatcherDoesNotOccupyTheDelegateWorkerWhileARunnableIsHeld() {
        val executor = Executors.newSingleThreadExecutor()
        val holdingDispatcher = HoldingDispatcher(executor.asCoroutineDispatcher())
        val scope = CoroutineScope(holdingDispatcher + Job())
        try {
            val firstRan = CountDownLatch(1)
            val secondRan = CountDownLatch(1)
            val heldDispatch = holdingDispatcher.armNextDispatch()

            scope.launch { firstRan.countDown() }
            heldDispatch.awaitCaptured("the armed runnable was not captured within 10s")

            // A one-thread delegate is intentional. If the hold blocked
            // inside that worker (the old implementation), this unarmed
            // second runnable could not run until the first was released.
            scope.launch { secondRan.countDown() }
            assertTrue(
                "holding one runnable must not consume the delegate's only worker",
                secondRan.await(10, TimeUnit.SECONDS),
            )
            assertEquals("the captured first runnable must still be held", 1L, firstRan.count)

            heldDispatch.release()
            assertTrue(
                "the released first runnable did not run within 10s",
                firstRan.await(10, TimeUnit.SECONDS),
            )
        } finally {
            holdingDispatcher.releaseAll()
            scope.cancel()
            executor.shutdown()
        }
    }

    /**
     * Reviewer probe Q2: `connect()`'s handshake job's body is held at its
     * first line (AFTER `Connecting` has already fired, the job is already
     * durably registered in `backgroundJobs`, and `connect()` itself has
     * already fully returned to this test). A concurrent, SEQUENTIAL
     * `disconnect()` is run to completion while that body is held -- it
     * must find and cancel that job. Only THEN is the hold released.
     * Assertion: no event of any kind, on EITHER client, survives beyond
     * `disconnect()`'s own `Disconnected` event -- in particular no
     * `connectionChanged(connected)` and no `linkBudgetChanged`, which the
     * held job's body would otherwise have gone on to emit had it ever run
     * a single line.
     */
    @Test
    fun disconnectFindsAndCancelsAConnectHandshakeJobHeldAtItsFirstDispatch() {
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
            // body at its first line specifically, not any leftover dispatch
            // from the discovery jobs above (already fully settled per the
            // spin-wait).
            val heldDispatch = holdingDispatcher.armNextDispatch()

            // This test's capturing dispatcher makes Job.start() return
            // promptly while command ownership remains held through it, so no
            // separate thread is needed for connect() itself.
            runBlocking { pair.clientA.connect(idB.toHexString()) }

            heldDispatch.awaitCaptured(
                "connect()'s handshake job's body must reach its held first line before we race " +
                    "disconnect() against it",
            )
            // onAdmitted() -- which emits Connecting -- always runs strictly
            // BEFORE connect() releases commandInFlight and starts the job,
            // so by the time connect() has even returned (let alone by the
            // time the held dispatch is captured), Connecting is guaranteed already
            // recorded.
            assertEquals(
                "sanity: Connecting must have already fired (atomically admitted) before connect() " +
                    "itself returned, well before the job's body could ever reach its held first line",
                listOf(ChatConnectionState.Connecting),
                recorder.entries().filter { it.client == 'A' }
                    .mapNotNull { (it.event as? ChatEvent.ConnectionChanged)?.state },
            )

            // connect() has ALREADY fully returned by this point (round-6's
            // full-call span: see SimulatedChatTransportClient
            // .commandInFlight's doc comment) -- disconnect() below is a
            // plain SEQUENTIAL call on this same client, not racing
            // connect()'s own span at all. What it races is the job's BODY,
            // whose Runnable is still captured before delegate submission.
            val disconnectThread =
                thread(start = true) {
                    runBlocking { pair.clientA.disconnect() }
                }

            // Poll (no wall-clock sleep) until disconnect() is OBSERVABLY
            // parked waiting on the held job's Job.join() -- proving its own
            // synchronous cancellation sweep (markTerminalAndRequestCancellation,
            // which requests Job.cancel() on the held job) has ALREADY run.
            // Empirically verified (temporary debug instrumentation, since
            // reverted): a thread parked inside `runBlocking { ... }` on a
            // suspending `Job.join()` reports `Thread.State.TIMED_WAITING`,
            // never plain `WAITING` -- runBlocking's own event-loop park
            // (`BlockingCoroutine.joinBlocking`) always calls
            // `LockSupport.parkNanos(...)`, even with no delayed task
            // pending (i.e. an effectively-infinite timeout), and the JVM's
            // reported Thread.State reflects WHICH LockSupport method was
            // called, not the actual duration -- so TIMED_WAITING is the
            // correct (and only ever observed) signal here, not evidence of
            // some other, unrelated wait. `BLOCKED` is kept alongside it for
            // parity with this file's other lock-contention polls (e.g.
            // disconnectCannotInterleaveWithAnInFlightValidatedEffect's
            // `synchronized`-monitor contention), though it is not the state
            // actually produced by this specific `runBlocking`/`Job.join()`
            // combination.
            spinUntil("disconnect() never entered its join-wait for the held job") {
                disconnectThread.state == Thread.State.TIMED_WAITING ||
                    disconnectThread.state == Thread.State.WAITING ||
                    disconnectThread.state == Thread.State.BLOCKED
            }

            heldDispatch.release()
            disconnectThread.join(10_000)
            assertFalse("disconnect thread did not finish within 10s", disconnectThread.isAlive)

            assertEquals(
                "nothing may fire, on either client, once both threads have settled -- the " +
                    "handshake job was already atomically registered (its Job.start() had already " +
                    "been reached) when disconnect()'s sweep ran, but it had not yet executed a " +
                    "single line of its own body, so the sweep's cancellation must have prevented " +
                    "every one of its scheduled emissions, including linkBudgetChanged",
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
            holdingDispatcher.releaseAll()
            scope.cancel()
            executor.shutdown()
        }
    }

    /**
     * Reviewer probe Q3: same shape as Q2, but for `send()`. The pair is
     * first connected for real (an ordinary, un-held dispatch -- "under a
     * real dispatcher it elapses in real (short) wall-clock time," per the
     * class doc comment), then `send()`'s own background job's BODY is held
     * at its first line -- AFTER `Queued` has fired, the message is already
     * durably registered in `pendingSendJobs`, and `send()` itself has
     * already fully returned to this test -- while a concurrent, SEQUENTIAL
     * `disconnect()` races the still-held job body. The message must end up
     * terminalized `failed("disconnected")` by `disconnect()`'s own sweep
     * (`failNonterminalOutgoingSends`) -- never left sitting in
     * `pendingSendJobs` with no further status transition, and never
     * progressing to `transmitting`/`delivered` once the hold is released.
     */
    @Test
    fun disconnectTerminalizesASendJobHeldAtItsFirstDispatchNeverLeavingItPending() {
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

            // NOW arm the hold, so it catches send()'s job's body at its
            // first line specifically, not any leftover dispatch from the
            // connect() handshake job above (already fully settled per the
            // spin-wait).
            val heldDispatch = holdingDispatcher.armNextDispatch()

            // This test's capturing dispatcher makes send()'s Job.start()
            // return promptly while command ownership remains held through
            // it. The Runnable is captured before delegate submission, so
            // this direct call completes before the held job body executes
            // its first line and messageIdHex can be read from its return.
            val messageIdHex = runBlocking { pair.clientA.send("held-by-dispatcher") }
            assertEquals(
                "sanity: send()'s returned messageIdHex must match the pinned per-role message-ID " +
                    "stream's first draw",
                expectedFirstMessageId(8L, 'A'),
                messageIdHex,
            )

            heldDispatch.awaitCaptured(
                "send()'s background job's body must reach its held first line before we race " +
                    "disconnect() against it",
            )
            assertEquals(
                "sanity: Queued must have already fired (atomically admitted, together with this " +
                    "message's pendingSendJobs registration) before send() itself returned, well " +
                    "before the job's body could ever reach its held first line",
                1,
                pair.clientA.pendingSendJobCount,
            )

            // disconnect() is a plain SEQUENTIAL call here too (send() has
            // ALREADY fully returned, per round-6's full-call span) -- it
            // still needs its own thread only because it suspends on
            // Job.join() for the held job body, and this test must poll that
            // park state (from a DIFFERENT thread) before releasing the hold.
            val disconnectThread =
                thread(start = true) {
                    runBlocking { pair.clientA.disconnect() }
                }
            // See Q2's sibling spinUntil above for why TIMED_WAITING (not
            // WAITING) is the state actually produced by a thread parked
            // inside `runBlocking { ... Job.join() ... }`.
            spinUntil("disconnect() never entered its join-wait for the held job") {
                disconnectThread.state == Thread.State.TIMED_WAITING ||
                    disconnectThread.state == Thread.State.WAITING ||
                    disconnectThread.state == Thread.State.BLOCKED
            }
            // The held job's own cancellation was already REQUESTED
            // (markTerminalAndRequestCancellation, which runs synchronously
            // before the join-wait we just observed disconnect() enter) --
            // but pendingSendJobs itself is only cleared by
            // failNonterminalOutgoingSends, which runs AFTER that join-wait
            // completes, so it still shows this message as pending at this
            // exact instant. This is a genuine, meaningful mid-flight
            // snapshot (unlike the entries-based "gap" check the original
            // buggy version attempted, which is no longer expressible now
            // that disconnect() provably cannot complete before the hold is
            // released -- there is no gap left to observe).
            assertEquals(
                "sanity: the held job's cancellation has been requested, but disconnect() has not " +
                    "yet run failNonterminalOutgoingSends (that happens AFTER the join-wait this " +
                    "client is currently parked in) -- pendingSendJobs is cleared only once " +
                    "disconnect() actually completes, right below",
                1,
                pair.clientA.pendingSendJobCount,
            )

            heldDispatch.release()
            disconnectThread.join(10_000)
            assertFalse("disconnect thread did not finish within 10s", disconnectThread.isAlive)

            assertEquals(
                "disconnect()'s own sweep must have fully terminalized the message once it actually " +
                    "completes -- it can never leave an admitted send sitting in pendingSendJobs",
                0,
                pair.clientA.pendingSendJobCount,
            )
            assertTrue("A must be terminal once disconnect() actually completes", pair.clientA.isTerminal())
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
            holdingDispatcher.releaseAll()
            scope.cancel()
            executor.shutdown()
        }
    }

    // -- Cancellation-safe terminal completion (round-6 fresh pin, reviewer
    // regression) -- ../../../CONTRACT.md section 2's round-6-pinned
    // "Cancellation-safe terminal completion" bullet: once disconnect()/
    // stop() commits its invalidation (terminal flag + generation bump),
    // the REST of its own tail -- awaiting admitted work, terminalizing
    // nonterminal sends, the terminal emission, stream completion -- must
    // complete even if the CALLER of the terminal command is cancelled
    // mid-call. Before this fix, disconnectOwningCommandSpan/
    // stopOwningCommandSpan's `jobs.forEach { it.join() }` wait was an
    // ordinary CANCELLABLE suspension point: cancelling the caller while
    // parked there threw a CancellationException straight out of
    // disconnect()/stop(), leaving `disconnectedByUser`/`stopped` already
    // permanently true (committed) but NONE of the tail run --
    // pendingSendJobs stuck non-empty forever, no failed(...) status ever
    // emitted, no terminal connectionChanged (nor, for stop(), a closed
    // event stream) -- and a REPEAT call thereafter early-returning as a
    // silent no-op FOREVER, because the idempotence flag was already
    // (wrongly) set. The fix wraps the whole tail in
    // `withContext(NonCancellable)`; these two tests reproduce the
    // reviewer's exact recipe: hold an admitted send, launch disconnect()/
    // stop() until it is genuinely suspended in that join, cancel THAT
    // CALLER's own coroutine, release the held send, and prove the tail
    // still completes (pendingSendJobCount reaches 0, the terminal
    // failed(...) status is emitted, and -- for stop() -- the event stream
    // closes), then invoke the terminal command again to demonstrate the
    // (now-harmless) repeat-call no-op path.

    /**
     * Reviewer regression: `disconnect()`'s caller is cancelled while
     * genuinely parked inside the (now non-cancellable) `jobs.forEach {
     * it.join() }` wait for a held, admitted send. The tail must still run
     * to completion once the hold is released, regardless.
     */
    @Test
    fun disconnectCompletesItsTailEvenWhenTheCallersCoroutineIsCancelledMidJoin() {
        val executor = Executors.newFixedThreadPool(4)
        val holdingDispatcher = HoldingDispatcher(executor.asCoroutineDispatcher())
        val scope = CoroutineScope(holdingDispatcher + Job())
        val callerExecutor = Executors.newFixedThreadPool(2)
        val callerScope = CoroutineScope(callerExecutor.asCoroutineDispatcher() + Job())
        try {
            val recorder = ChatTraceRecorder()
            val pair = SimulatedChatPair.create(ChatScenario.HAPPY_PAIR, 95L, scope, VirtualTimeSource { 0L }, recorder)
            val (_, idB) = expectedPeerIds(95L)

            runBlocking {
                pair.clientA.start()
                pair.clientB.start()
            }
            spinUntil("discovery jobs never settled") {
                pair.clientA.backgroundJobCount == 0 && pair.clientB.backgroundJobCount == 0
            }
            runBlocking { pair.clientA.connect(idB.toHexString()) }
            spinUntil("connect()'s handshake job never settled") { pair.clientA.backgroundJobCount == 0 }

            // Hold an ADMITTED send: Queued has fired and the message is
            // durably registered in pendingSendJobs, but its background
            // job's body is paused before it can ever reach
            // transmitting/delivered.
            val heldDispatch = holdingDispatcher.armNextDispatch()
            val messageIdHex = runBlocking { pair.clientA.send("held-for-cancellation-probe") }
            heldDispatch.awaitCaptured(
                "send()'s background job's body must reach its held first line before we exercise " +
                    "the cancellation probe",
            )
            assertEquals(1, pair.clientA.pendingSendJobCount)

            // Launch disconnect() until it commits its invalidation --
            // markTerminalAndRequestCancellation() sets `terminal` true
            // synchronously, INSIDE the withContext(NonCancellable) region,
            // immediately before the join() wait for the held send begins
            // -- so polling isTerminal() is a precise, non-flaky signal that
            // this call is now at (or a handful of CPU instructions from)
            // that exact suspension point.
            val disconnectCallerJob = callerScope.launch { pair.clientA.disconnect() }
            spinUntil("disconnect() never committed its invalidation before the join-wait") {
                pair.clientA.isTerminal()
            }

            // Cancel the CALLER of disconnect() mid-call, while it is
            // suspended inside disconnect()'s own withContext(NonCancellable)
            // tail. ../../../CONTRACT.md section 2's round-6-pinned
            // "Cancellation-safe terminal completion" bullet: this must NOT
            // stop the tail from completing.
            disconnectCallerJob.cancel()
            assertTrue("disconnect()'s caller Job must reflect its own cancel request", disconnectCallerJob.isCancelled)

            // Release the held send -- disconnect()'s NonCancellable tail
            // can now actually finish joining it.
            heldDispatch.release()

            // Job.join() never throws for a cancelled Job (unlike
            // Deferred.await()); withTimeout makes this a bounded,
            // fail-closed wait (a hit deadline FAILS this test via
            // TimeoutCancellationException, not a silently-tolerated hang),
            // per this file's own concurrency-probe contract.
            runBlocking { withTimeout(10_000) { disconnectCallerJob.join() } }

            // The tail ran to completion regardless of the caller's own
            // cancellation.
            assertEquals(
                "disconnect()'s sweep must still terminalize the held send even though ITS OWN " +
                    "caller was cancelled mid-join",
                0,
                pair.clientA.pendingSendJobCount,
            )
            assertEquals(
                listOf(ChatMessageDisplayStatus.Queued, ChatMessageDisplayStatus.Failed(ChatReasonStrings.DISCONNECTED)),
                recorder.entries().filter { it.client == 'A' }
                    .mapNotNull { (it.event as? ChatEvent.MessageStatusChanged) }
                    .filter { it.messageIdHex == messageIdHex }
                    .map { it.status },
            )
            assertTrue(
                "the terminal connectionChanged(disconnected, userInitiated) event must still be " +
                    "emitted even though disconnect()'s own caller was cancelled mid-join",
                recorder.entries().filter { it.client == 'A' }.any {
                    (it.event as? ChatEvent.ConnectionChanged)?.state ==
                        ChatConnectionState.Disconnected(ChatReasonStrings.USER_INITIATED)
                },
            )
            assertTrue("A must be terminal once disconnect()'s tail actually completes", pair.clientA.isTerminal())

            // Reviewer regression recipe's final step: invoke the terminal
            // command again. The tail above already ran to completion on
            // its own (a non-cancellable region, not a repeatable
            // completion op), so this is the ordinary, harmless "a repeat
            // disconnect() is a no-op" path -- included to demonstrate the
            // recipe exactly as specified, not because anything new is left
            // to complete.
            runBlocking { pair.clientA.disconnect() }
            assertEquals(0, pair.clientA.pendingSendJobCount)
        } finally {
            holdingDispatcher.releaseAll()
            scope.cancel()
            executor.shutdown()
            callerScope.cancel()
            callerExecutor.shutdown()
        }
    }

    /**
     * Reviewer regression: same shape as the `disconnect()` test above, but
     * for `stop()` -- additionally proves the event STREAM still closes
     * (per CONTRACT.md's "then finishes the event stream") even though
     * `stop()`'s own caller was cancelled mid-join.
     */
    @Test
    fun stopCompletesItsTailEvenWhenTheCallersCoroutineIsCancelledMidJoin() {
        val executor = Executors.newFixedThreadPool(4)
        val holdingDispatcher = HoldingDispatcher(executor.asCoroutineDispatcher())
        val scope = CoroutineScope(holdingDispatcher + Job())
        val callerExecutor = Executors.newFixedThreadPool(2)
        val callerScope = CoroutineScope(callerExecutor.asCoroutineDispatcher() + Job())
        try {
            val recorder = ChatTraceRecorder()
            val pair = SimulatedChatPair.create(ChatScenario.HAPPY_PAIR, 96L, scope, VirtualTimeSource { 0L }, recorder)
            val (_, idB) = expectedPeerIds(96L)

            runBlocking {
                pair.clientA.start()
                pair.clientB.start()
            }
            spinUntil("discovery jobs never settled") {
                pair.clientA.backgroundJobCount == 0 && pair.clientB.backgroundJobCount == 0
            }
            runBlocking { pair.clientA.connect(idB.toHexString()) }
            spinUntil("connect()'s handshake job never settled") { pair.clientA.backgroundJobCount == 0 }

            val heldDispatch = holdingDispatcher.armNextDispatch()
            val messageIdHex = runBlocking { pair.clientA.send("held-for-cancellation-probe") }
            heldDispatch.awaitCaptured(
                "send()'s background job's body must reach its held first line before we exercise " +
                    "the cancellation probe",
            )
            assertEquals(1, pair.clientA.pendingSendJobCount)

            val stopCallerJob = callerScope.launch { pair.clientA.stop() }
            spinUntil("stop() never committed its invalidation before the join-wait") {
                pair.clientA.isTerminal()
            }

            stopCallerJob.cancel()
            assertTrue("stop()'s caller Job must reflect its own cancel request", stopCallerJob.isCancelled)

            heldDispatch.release()
            runBlocking { withTimeout(10_000) { stopCallerJob.join() } }

            assertEquals(
                "stop()'s sweep must still terminalize the held send even though ITS OWN caller " +
                    "was cancelled mid-join",
                0,
                pair.clientA.pendingSendJobCount,
            )
            assertEquals(
                listOf(ChatMessageDisplayStatus.Queued, ChatMessageDisplayStatus.Failed(ChatReasonStrings.STOPPED)),
                recorder.entries().filter { it.client == 'A' }
                    .mapNotNull { (it.event as? ChatEvent.MessageStatusChanged) }
                    .filter { it.messageIdHex == messageIdHex }
                    .map { it.status },
            )
            assertTrue(
                "the terminal connectionChanged(disconnected, stopped) event must still be emitted " +
                    "even though stop()'s own caller was cancelled mid-join",
                recorder.entries().filter { it.client == 'A' }.any {
                    (it.event as? ChatEvent.ConnectionChanged)?.state ==
                        ChatConnectionState.Disconnected(ChatReasonStrings.STOPPED)
                },
            )
            assertTrue("A must be terminal once stop()'s tail actually completes", pair.clientA.isTerminal())

            // "stream closed": a fresh collector must see the stream as
            // ALREADY completed (never hang) -- withTimeout makes this
            // fail-closed, per this file's own concurrency-probe contract.
            val remainingEvents = runBlocking { withTimeout(10_000) { pair.clientA.events.toList() } }
            assertTrue(
                "the event stream must have closed with the terminal failed(\"stopped\") status " +
                    "still in it, by the time stop() has completed, even though its own caller was " +
                    "cancelled mid-join",
                remainingEvents.any { event ->
                    (event as? ChatEvent.MessageStatusChanged)?.let {
                        it.messageIdHex == messageIdHex && it.status == ChatMessageDisplayStatus.Failed(ChatReasonStrings.STOPPED)
                    } == true
                },
            )

            // Reviewer regression recipe's final step: invoke the terminal
            // command again -- the ordinary, harmless "a repeat stop() is a
            // no-op" path.
            runBlocking { pair.clientA.stop() }
            assertEquals(0, pair.clientA.pendingSendJobCount)
        } finally {
            holdingDispatcher.releaseAll()
            scope.cancel()
            executor.shutdown()
            callerScope.cancel()
            callerExecutor.shutdown()
        }
    }

    // -- Overlap parity (round-6 fresh pin, mirroring CyrinxChatKit's Swift
    // twin's SimulatedChatTransportClientCommandOwnershipTests) --
    // ../../../CONTRACT.md section 2's round-6-pinned "Command ownership
    // (pinned)" bullet: "Ownership spans the COMPLETE public call -- from
    // entry to the call's return to its caller ... a sequence rejected on
    // one platform is rejected on the other." Q2/Q3 above race a public
    // command against an ALREADY-ADMITTED job's held BODY, strictly AFTER
    // the admitting call has fully returned -- they do not exercise two
    // public CALLS overlapping. These two tests do: [send]'s own
    // test-only [SimulatedChatTransportClient.testOnlyAfterQueuedEmissionLatch]
    // hook (Kotlin's analogue of Swift's `testOnlyAfterQueuedEmissionLatch`)
    // holds a `send()` call suspended INSIDE its own guarded span -- still
    // holding `commandInFlight` -- so a genuinely concurrent second command
    // attempted while it is held is proven rejected, exactly the sequence
    // Swift's `commandLock`/`commandActive` guard (held for the whole
    // `async` function body, including that same test-only await point)
    // already rejects by construction.

    /**
     * Overlap parity: send-vs-send. A second `send()` attempted while a
     * first is suspended mid-span (past its `Queued` emission and
     * `pendingSendJobs` registration, at the test-only latch) is rejected
     * with the concurrent-command error; the winner then completes its
     * ordinary, uninterrupted `queued -> transmitting -> delivered`
     * lifecycle once released, and its message-ID draw is untouched by the
     * rejected racer (which never reaches [SimulatedChatTransportClient
     * .generateMessageId] at all).
     */
    @Test
    fun sendConcurrentWithAnInFlightSendIsRejectedAsConcurrentCommand() {
        val enteredLatch = CountDownLatch(1)
        val releaseLatch = CountDownLatch(1)
        val executor = Executors.newFixedThreadPool(4)
        val scope = CoroutineScope(executor.asCoroutineDispatcher() + Job())
        try {
            val recorder = ChatTraceRecorder()
            val pair = SimulatedChatPair.create(ChatScenario.HAPPY_PAIR, 97L, scope, VirtualTimeSource { 0L }, recorder)
            val (_, idB) = expectedPeerIds(97L)

            runBlocking {
                pair.clientA.start()
                pair.clientB.start()
            }
            spinUntil("discovery jobs never settled") {
                pair.clientA.backgroundJobCount == 0 && pair.clientB.backgroundJobCount == 0
            }
            runBlocking { pair.clientA.connect(idB.toHexString()) }
            spinUntil("connect()'s handshake job never settled") { pair.clientA.backgroundJobCount == 0 }

            // Real, dedicated (not shared-pool) OS thread below is what
            // makes blocking on `releaseLatch.await` directly inside this
            // suspend hook safe -- CONTRACT.md's concurrency-probe
            // exception: "never block a cooperative/async executor's
            // threads on synchronous primitives" is about a SHARED
            // dispatcher's threads, not a thread created solely to drive
            // this one blocking call.
            pair.clientA.testOnlyAfterQueuedEmissionLatch = {
                enteredLatch.countDown()
                check(releaseLatch.await(10, TimeUnit.SECONDS)) {
                    "releaseLatch was never counted down within 10s"
                }
            }

            val winnerThread =
                thread(start = true) {
                    runBlocking { pair.clientA.send("winner") }
                }

            assertTrue(
                "the winner's send() must reach the held test-only latch, still inside its own " +
                    "commandInFlight span, before we race a second send() against it",
                enteredLatch.await(10, TimeUnit.SECONDS),
            )

            var caught: ChatTransportError? = null
            try {
                runBlocking { pair.clientA.send("racer") }
            } catch (e: ChatTransportError) {
                caught = e
            }
            assertTrue("a concurrent send() must be rejected while the winner's span is held", caught != null)
            assertTrue(
                "...specifically the concurrent-command error, not some other ChatTransportError",
                caught!!.isConcurrentCommand,
            )

            releaseLatch.countDown()
            winnerThread.join(10_000)
            assertFalse("winning send thread did not finish within 10s", winnerThread.isAlive)
            // The winner's send() call itself has already returned (its
            // full-call span included Job.start()), but
            // its background job's queued->transmitting->delivered
            // transitions still need real wall-clock time to run under this
            // REAL dispatcher (see the class doc comment) -- wait for them
            // to settle before asserting the full status list.
            spinUntil("winner's send job never settled") { pair.clientA.pendingSendJobCount == 0 }

            val winnerMessageIdHex = expectedFirstMessageId(97L, 'A')
            assertEquals(
                "no corruption: exactly one message ever appears in the event stream -- the rejected " +
                    "racer left zero trace (it never reached generateMessageId at all, since the " +
                    "concurrent-command guard rejects before any messageId-specific logic runs)",
                setOf(winnerMessageIdHex),
                recorder.entries().mapNotNull { (it.event as? ChatEvent.MessageStatusChanged)?.messageIdHex }.toSet(),
            )
            assertEquals(
                listOf(ChatMessageDisplayStatus.Queued, ChatMessageDisplayStatus.Transmitting, ChatMessageDisplayStatus.Delivered),
                recorder.entries().filter { it.client == 'A' }
                    .mapNotNull { (it.event as? ChatEvent.MessageStatusChanged) }
                    .filter { it.messageIdHex == winnerMessageIdHex }
                    .map { it.status },
            )
        } finally {
            scope.cancel()
            executor.shutdown()
        }
    }

    /**
     * Overlap parity: send-vs-disconnect. A `disconnect()` attempted while a
     * `send()` is suspended mid-span (at the same test-only latch as above)
     * is rejected with the concurrent-command error -- it never touches
     * `connectionState` or emits `connectionChanged` -- and the winning
     * `send()` completes its ordinary lifecycle once released, exactly as
     * if the rejected `disconnect()` attempt had never happened at all.
     */
    @Test
    fun disconnectConcurrentWithAnInFlightSendIsRejectedAsConcurrentCommand() {
        val enteredLatch = CountDownLatch(1)
        val releaseLatch = CountDownLatch(1)
        val executor = Executors.newFixedThreadPool(4)
        val scope = CoroutineScope(executor.asCoroutineDispatcher() + Job())
        try {
            val recorder = ChatTraceRecorder()
            val pair = SimulatedChatPair.create(ChatScenario.HAPPY_PAIR, 98L, scope, VirtualTimeSource { 0L }, recorder)
            val (_, idB) = expectedPeerIds(98L)

            runBlocking {
                pair.clientA.start()
                pair.clientB.start()
            }
            spinUntil("discovery jobs never settled") {
                pair.clientA.backgroundJobCount == 0 && pair.clientB.backgroundJobCount == 0
            }
            runBlocking { pair.clientA.connect(idB.toHexString()) }
            spinUntil("connect()'s handshake job never settled") { pair.clientA.backgroundJobCount == 0 }

            pair.clientA.testOnlyAfterQueuedEmissionLatch = {
                enteredLatch.countDown()
                check(releaseLatch.await(10, TimeUnit.SECONDS)) {
                    "releaseLatch was never counted down within 10s"
                }
            }

            val winnerThread =
                thread(start = true) {
                    runBlocking { pair.clientA.send("winner") }
                }

            assertTrue(
                "the winner's send() must reach the held test-only latch, still inside its own " +
                    "commandInFlight span, before we race disconnect() against it",
                enteredLatch.await(10, TimeUnit.SECONDS),
            )

            var caught: ChatTransportError? = null
            try {
                runBlocking { pair.clientA.disconnect() }
            } catch (e: ChatTransportError) {
                caught = e
            }
            assertTrue("a concurrent disconnect() must be rejected while the winner's span is held", caught != null)
            assertTrue(
                "...specifically the concurrent-command error, not some other ChatTransportError",
                caught!!.isConcurrentCommand,
            )
            assertEquals(
                "the rejected disconnect() must not have touched connectionState at all",
                ChatConnectionState.Connected,
                recorder.entries().filter { it.client == 'A' }
                    .mapNotNull { (it.event as? ChatEvent.ConnectionChanged)?.state }
                    .last(),
            )

            releaseLatch.countDown()
            winnerThread.join(10_000)
            assertFalse("winning send thread did not finish within 10s", winnerThread.isAlive)
            spinUntil("winner's send job never settled") { pair.clientA.pendingSendJobCount == 0 }

            val winnerMessageIdHex = expectedFirstMessageId(98L, 'A')
            assertEquals(
                "the winner's send must complete its ordinary, uninterrupted lifecycle, exactly as " +
                    "if the rejected disconnect() attempt had never happened",
                listOf(ChatMessageDisplayStatus.Queued, ChatMessageDisplayStatus.Transmitting, ChatMessageDisplayStatus.Delivered),
                recorder.entries().filter { it.client == 'A' }
                    .mapNotNull { (it.event as? ChatEvent.MessageStatusChanged) }
                    .filter { it.messageIdHex == winnerMessageIdHex }
                    .map { it.status },
            )
            assertTrue(
                "the rejected disconnect() must never have emitted its own connectionChanged " +
                    "(disconnected, userInitiated) -- only a LATER, legitimate one would",
                recorder.entries().filter { it.client == 'A' }
                    .none { (it.event as? ChatEvent.ConnectionChanged)?.state == ChatConnectionState.Disconnected(ChatReasonStrings.USER_INITIATED) },
            )
        } finally {
            scope.cancel()
            executor.shutdown()
        }
    }

    // -- Command ownership (round-5 fresh pin, reviewer probes R2/R3) -------
    // ../../../CONTRACT.md section 2's round-5-pinned "Command ownership
    // (pinned)" bullet: public commands are owned by one caller at a time,
    // enforced (not honor-ruled) via SimulatedChatTransportClient's
    // `commandInFlight` CAS guard -- a losing caller is rejected outright
    // with `ChatTransportError.concurrentCommand`, never queued to wait its
    // turn. The tests below are genuinely concurrent, real-thread
    // reproductions (same rationale as the P3/Q2/Q3 tests above: a virtual,
    // single-threaded `TestCoroutineScheduler` cannot exhibit a real
    // interleaving race at all).

    /**
     * Reviewer probe R2: 16 real threads, each issuing 500 `send()` calls
     * back-to-back through the PUBLIC API on the SAME connected client
     * instance -- 8000 attempts total, hammering `commandInFlight`'s CAS as
     * hard as a JVM thread pool reasonably can. Every single attempt must
     * either succeed (returning a `messageIdHex`) or throw specifically the
     * concurrent-command [ChatTransportError] -- never anything else, never
     * silently corrupt or skip a draw. ../../../CONTRACT.md section 2's
     * "Message-ID draws ... execute inside the command's serialized span":
     * the set of message IDs actually returned by the SUCCESSFUL calls must
     * be exactly the pinned per-role [SplitMix64] stream's first `N` draws
     * (`N` = however many calls actually won the race) -- no duplicates (two
     * winners would mean two callers interleaved inside the guarded span),
     * no gaps or substitutions (a skipped or reordered draw would mean the
     * stream was consumed out of order). Verified as a SET (matching
     * [expectedMessageIdStreamPrefix]'s own "first N draws" framing) rather
     * than by asserting each thread's own completion order matches draw
     * order: `commandInFlight` is released only at [send]'s own return
     * (round-6's full-call span; see that field's doc comment), which
     * serializes each winner's draw+admission with every OTHER caller's,
     * but says nothing about which of 16 racer THREADS the JVM scheduler
     * happens to run next once its own span has ended -- two winners' OWN
     * downstream `send()` returns can therefore still complete in either
     * real-time order even though their DRAWS themselves can never
     * interleave -- set equality is the invariant CONTRACT.md actually
     * pins, not a specific cross-thread completion ordering this class
     * makes no promise about.
     */
    @Test
    fun concurrentSendCallsNeverCorruptThePinnedMessageIdStream() {
        val threadCount = 16
        val sendsPerThread = 500
        val seed = 91L
        val executor = Executors.newFixedThreadPool(threadCount)
        val scope = CoroutineScope(executor.asCoroutineDispatcher() + Job())
        try {
            val pair = SimulatedChatPair.create(ChatScenario.HAPPY_PAIR, seed, scope, VirtualTimeSource { 0L })
            val (_, idB) = expectedPeerIds(seed)

            runBlocking {
                pair.clientA.start()
                pair.clientB.start()
            }
            spinUntil("discovery jobs never settled") {
                pair.clientA.backgroundJobCount == 0 && pair.clientB.backgroundJobCount == 0
            }
            runBlocking { pair.clientA.connect(idB.toHexString()) }
            spinUntil("connect()'s handshake job never settled") { pair.clientA.backgroundJobCount == 0 }

            val successfulIds = Collections.synchronizedList(mutableListOf<String>())
            val concurrentRejectionCount = AtomicInteger(0)
            val unexpectedFailures = Collections.synchronizedList(mutableListOf<Throwable>())
            // Every thread blocks here until all 16 are actually ready, so
            // the CAS contention below is as genuinely simultaneous as the
            // JVM scheduler allows, rather than trickling in one at a time.
            val startBarrier = CyclicBarrier(threadCount)

            val senderThreads =
                (0 until threadCount).map { threadIndex ->
                    thread(start = false, name = "r2-sender-$threadIndex") {
                        startBarrier.await(10, TimeUnit.SECONDS)
                        repeat(sendsPerThread) {
                            try {
                                val idHex = runBlocking { pair.clientA.send("r2-stress") }
                                successfulIds.add(idHex)
                            } catch (e: ChatTransportError) {
                                if (e.isConcurrentCommand) {
                                    concurrentRejectionCount.incrementAndGet()
                                } else {
                                    unexpectedFailures.add(e)
                                }
                            } catch (e: Throwable) {
                                unexpectedFailures.add(e)
                            }
                        }
                    }
                }
            senderThreads.forEach { it.start() }
            senderThreads.forEach {
                it.join(60_000)
                assertFalse("sender thread ${it.name} did not finish within 60s", it.isAlive)
            }

            assertTrue(
                "every send() attempt must fail ONLY with the concurrent-command error, never any " +
                    "other exception (corruption, unexpected state): $unexpectedFailures",
                unexpectedFailures.isEmpty(),
            )
            assertEquals(
                "every one of the ${threadCount * sendsPerThread} attempts must be accounted for as " +
                    "either a success or a concurrent-command rejection",
                threadCount * sendsPerThread,
                successfulIds.size + concurrentRejectionCount.get(),
            )
            assertTrue(
                "commandInFlight contention across $threadCount real threads must have produced at " +
                    "least one rejection -- otherwise this test is not actually exercising concurrent " +
                    "entry at all",
                concurrentRejectionCount.get() > 0,
            )

            val successList = successfulIds.toList()
            assertEquals(
                "no duplicate message IDs may ever appear among successful sends -- a duplicate would " +
                    "mean two concurrent callers both drew (and both returned) the SAME two SplitMix64 " +
                    "values, i.e. the guarded span let them interleave",
                successList.size,
                successList.toSet().size,
            )

            val expectedPrefix = expectedMessageIdStreamPrefix(seed, 'A', successList.size)
            assertEquals(
                "the successful sends' IDs must be EXACTLY the pinned message-ID stream's first N " +
                    "draws (N = number of successes) -- no gaps (a draw silently skipped), no " +
                    "substitutions (a draw from later/earlier in the stream than N would allow), and " +
                    "no unexpected values (anything not drawn from this client's own role-tagged " +
                    "SplitMix64 stream at all)",
                expectedPrefix.toSet(),
                successList.toSet(),
            )
        } finally {
            scope.cancel()
            executor.shutdown()
        }
    }

    /**
     * Sets up a connected pair whose `connect()` handshake job's BODY is
     * held at its first line (same technique as
     * [disconnectFindsAndCancelsAConnectHandshakeJobHeldAtItsFirstDispatch]
     * above, post-round-6 rework), used by both concurrent-command-vs-
     * `disconnect()` tests below to force a genuine, deterministic overlap:
     * `disconnect()` is guaranteed to be durably parked inside its own
     * `Job.join()` wait -- i.e. still holding `commandInFlight` (only
     * released in its own outer `finally`, AFTER that join completes) --
     * for as long as the caller likes, before releasing the hold. The
     * specific job type held (`connect()`'s handshake, vs. Q3's `send()`
     * job) is irrelevant to these two tests: `commandInFlight`'s CAS guard
     * rejects a concurrent command BEFORE it ever inspects
     * messageId/handshake-specific state, so any held job that keeps
     * `disconnect()` parked on `Job.join()` demonstrates the same
     * guarantee. This test's capturing dispatcher makes `connect()`'s
     * Job.start() return promptly while command ownership remains held
     * through it, so it can run directly on this thread.
     */
    private fun setUpPairWithDisconnectParkedOnAHeldHandshakeJob(
        seed: Long,
        holdingDispatcher: HoldingDispatcher,
        scope: CoroutineScope,
    ): Pair<SimulatedChatPair, HeldDispatch> {
        val pair = SimulatedChatPair.create(ChatScenario.HAPPY_PAIR, seed, scope, VirtualTimeSource { 0L })
        val (_, idB) = expectedPeerIds(seed)

        runBlocking {
            pair.clientA.start()
            pair.clientB.start()
        }
        spinUntil("discovery jobs never settled") {
            pair.clientA.backgroundJobCount == 0 && pair.clientB.backgroundJobCount == 0
        }

        val heldDispatch = holdingDispatcher.armNextDispatch()
        runBlocking { pair.clientA.connect(idB.toHexString()) }
        heldDispatch.awaitCaptured(
            "connect()'s handshake job's body must reach its held first line before we race " +
                "disconnect() against it",
        )
        return pair to heldDispatch
    }

    /**
     * De-flaked replacement for polling [disconnectThread]'s raw
     * `java.lang.Thread.State` (see the CI-observed failure this fixes,
     * below). Waits until [pair]'s A client's `disconnect()` -- running on
     * [disconnectThread] -- has DEFINITELY already acquired
     * `commandInFlight` and will keep holding it for as long as this test
     * likes (i.e. until it itself calls `heldDispatch.release()`), by
     * polling [SimulatedChatTransportClient.isTerminal] instead of the
     * racing thread's JVM-reported state.
     *
     * This is sound, not merely convenient: `disconnect()`'s only write to
     * `terminal` happens inside `markTerminalAndRequestCancellation()`,
     * strictly AFTER `commandInFlight`'s `compareAndSet` has already
     * committed in that SAME call's program order, and strictly BEFORE the
     * `Job.join()` suspension that then holds `commandInFlight` for the
     * rest of the held-job race (see `disconnect()`'s and
     * `markTerminalAndRequestCancellation()`'s own doc comments in
     * SimulatedChatTransportClient.kt). Both fields are `AtomicBoolean`s,
     * so observing `isTerminal() == true` here also guarantees (via the
     * JMM's happens-before-through-a-single-writer-thread's-own-atomic-
     * writes transitivity) that this thread's earlier `commandInFlight` CAS
     * is visible too -- there is no window in which this predicate can fire
     * before `commandInFlight` is actually held.
     *
     * Root cause this replaces: CI run 30299948910 (a 2-core GitHub-hosted
     * ubuntu runner) failed
     * `cancelSendConcurrentWithAnInFlightDisconnectIsRejectedAsConcurrentCommand`
     * at its `caught != null` assertion -- `cancelSend()` had NOT been
     * rejected, meaning `commandInFlight` was NOT actually held yet when it
     * ran. The old predicate (`disconnectThread.state in {TIMED_WAITING,
     * WAITING, BLOCKED}`) is only a PROXY for "parked in the held job's
     * `Job.join()` wait", and `BLOCKED` in particular is not even the state
     * that specific wait produces (see
     * [disconnectFindsAndCancelsAConnectHandshakeJobHeldAtItsFirstDispatch]'s
     * own comment -- "kept alongside it for parity ... though it is not
     * the state actually produced by this specific runBlocking/Job.join()
     * combination"). On a resource-constrained runner a freshly spawned,
     * merely-scheduled-but-not-yet-executing real OS thread can transiently
     * report one of those same three states for reasons that have nothing
     * to do with the held job (thread/JVM scheduling and startup pauses),
     * i.e. strictly BEFORE `disconnect()` has run even its own first line
     * -- so a fast racing reader on the SAME (test) thread could slip
     * `cancelSend()` in before `commandInFlight` was actually set,
     * observing it as free. On fast/idle machines and in local loops that
     * startup window is reliably too small to ever be caught mid-poll,
     * which is why this only reproduced on a constrained CI runner. Polling
     * an actual invariant of `disconnect()`'s own state machine, rather
     * than a same-shape-but-unrelated JVM thread-state proxy, makes the
     * intended interleaving guaranteed rather than probable.
     */
    private fun spinUntilDisconnectDurablyHoldsCommandInFlight(
        pair: SimulatedChatPair,
        disconnectThread: Thread,
    ) {
        spinUntil("disconnect() never actually acquired commandInFlight (isTerminal() never flipped true)") {
            pair.clientA.isTerminal()
        }
        // Sanity companion to the predicate above, not a substitute for it:
        // commandInFlight cannot have been released yet either (the same
        // `disconnectOwningCommandSpan()` call that set `terminal` is still
        // parked in `Job.join()` on the still-held job -- nothing in this
        // test has released it), so disconnect() cannot have returned and
        // disconnectThread cannot have exited.
        assertTrue(
            "disconnect() must still be alive, parked in its own join() wait for the held job, once " +
                "isTerminal() is observed true -- it cannot have already returned since the held job " +
                "has not been released",
            disconnectThread.isAlive,
        )
    }

    /**
     * Reviewer probe (round-5 "Command ownership" pin, R2's sibling
     * coverage for `cancelSend()`): `cancelSend()` invoked concurrently
     * with an `disconnect()` that is genuinely, durably in flight on the
     * SAME client instance (parked in its own `Job.join()` wait for a held
     * job -- see [setUpPairWithDisconnectParkedOnAHeldHandshakeJob]) must be
     * rejected deterministically with the concurrent-command error, never
     * silently accepted or corrupting state.
     */
    @Test
    fun cancelSendConcurrentWithAnInFlightDisconnectIsRejectedAsConcurrentCommand() {
        val executor = Executors.newFixedThreadPool(4)
        val holdingDispatcher = HoldingDispatcher(executor.asCoroutineDispatcher())
        val scope = CoroutineScope(holdingDispatcher + Job())
        try {
            val (pair, heldDispatch) =
                setUpPairWithDisconnectParkedOnAHeldHandshakeJob(92L, holdingDispatcher, scope)

            val disconnectThread =
                thread(start = true) {
                    runBlocking { pair.clientA.disconnect() }
                }
            // Deterministic handshake -- see
            // spinUntilDisconnectDurablyHoldsCommandInFlight's own doc
            // comment for why this polls disconnect()'s own isTerminal()
            // flip rather than disconnectThread's raw JVM-reported
            // Thread.State (the latter is what a 2-core GitHub Actions
            // runner tripped in run 30299948910: caught == null below,
            // because the old proxy predicate could fire before
            // commandInFlight was actually held).
            spinUntilDisconnectDurablyHoldsCommandInFlight(pair, disconnectThread)

            // disconnect() has NOT released commandInFlight yet -- its own
            // serialized span holds it across the WHOLE join() wait (see
            // commandInFlight's doc comment) -- so a concurrent
            // cancelSend() on this SAME instance, attempted right now, must
            // be rejected deterministically. The messageId argument is
            // irrelevant (any string, even an unknown one) since the
            // concurrent-command guard is checked before any messageId
            // lookup.
            var caught: ChatTransportError? = null
            try {
                runBlocking { pair.clientA.cancelSend("irrelevant-messageid") }
            } catch (e: ChatTransportError) {
                caught = e
            }
            assertTrue("cancelSend() concurrent with an in-flight disconnect() must throw", caught != null)
            assertTrue(
                "...specifically the concurrent-command error, not some other ChatTransportError",
                caught!!.isConcurrentCommand,
            )

            heldDispatch.release()
            disconnectThread.join(10_000)
            assertFalse("disconnect thread did not finish within 10s", disconnectThread.isAlive)
            assertTrue("A must be terminal once disconnect() actually completes", pair.clientA.isTerminal())

            // The rejected cancelSend() must not have taken effect: once
            // disconnect() releases commandInFlight, a FRESH cancelSend()
            // for a real (but by-now-terminal) messageId is simply the
            // ordinary "unknown/no-op" behavior already covered by
            // cancelSendOnUnknownMessageIdIsANoOp -- nothing here is left
            // to assert beyond "disconnect() itself completed cleanly."
        } finally {
            holdingDispatcher.releaseAll()
            scope.cancel()
            executor.shutdown()
        }
    }

    /**
     * Reviewer probe (round-5 "Command ownership" pin, R2's sibling
     * coverage for `stop()`): `stop()` invoked concurrently with an
     * `disconnect()` that is genuinely, durably in flight on the SAME
     * client instance must be rejected deterministically with the
     * concurrent-command error -- then, once `disconnect()` has actually
     * completed and released `commandInFlight`, a SECOND, non-concurrent
     * `stop()` call is the ordinary "the only permitted subsequent call
     * after disconnect() is stop()" path (../../../CONTRACT.md section 2's
     * "disconnect() is terminal for the client instance" bullet) and must
     * succeed, completing the event stream exactly like
     * [stopAfterDisconnectCompletesTheEventsStreamCleanlyWithNoDuplicateDisconnectedEvent]
     * already proves for the SEQUENTIAL (non-racing) case.
     */
    @Test
    fun stopConcurrentWithAnInFlightDisconnectIsRejectedAsConcurrentCommandThenSucceedsAfterwards() {
        val executor = Executors.newFixedThreadPool(4)
        val holdingDispatcher = HoldingDispatcher(executor.asCoroutineDispatcher())
        val scope = CoroutineScope(holdingDispatcher + Job())
        try {
            val (pair, heldDispatch) =
                setUpPairWithDisconnectParkedOnAHeldHandshakeJob(93L, holdingDispatcher, scope)

            val disconnectThread =
                thread(start = true) {
                    runBlocking { pair.clientA.disconnect() }
                }
            // Deterministic handshake -- see
            // spinUntilDisconnectDurablyHoldsCommandInFlight's own doc
            // comment (same shared race as
            // cancelSendConcurrentWithAnInFlightDisconnectIsRejectedAsConcurrentCommand,
            // which is where the flaky Thread.State-polling predicate this
            // replaces was actually observed to fail on a 2-core CI
            // runner).
            spinUntilDisconnectDurablyHoldsCommandInFlight(pair, disconnectThread)

            // disconnect() still owns commandInFlight -- a concurrent
            // stop() attempted right now, on this SAME instance, must be
            // rejected deterministically.
            var caught: ChatTransportError? = null
            try {
                runBlocking { pair.clientA.stop() }
            } catch (e: ChatTransportError) {
                caught = e
            }
            assertTrue("stop() concurrent with an in-flight disconnect() must throw", caught != null)
            assertTrue(
                "...specifically the concurrent-command error, not some other ChatTransportError",
                caught!!.isConcurrentCommand,
            )

            heldDispatch.release()
            disconnectThread.join(10_000)
            assertFalse("disconnect thread did not finish within 10s", disconnectThread.isAlive)
            assertTrue("A must be terminal once disconnect() actually completes", pair.clientA.isTerminal())

            // NOW commandInFlight is free again (disconnect()'s own outer
            // finally already released it) -- a real, non-concurrent
            // stop() must succeed: "the only permitted subsequent call is
            // stop()".
            runBlocking { pair.clientA.stop() }
            // Nothing has collected `events` until now, so this drains the
            // WHOLE buffered channel -- including connect()'s own
            // `Connecting` (emitted before the hold, per
            // setUpPairWithDisconnectParkedOnAHeldHandshakeJob) and
            // disconnect()'s own `Disconnected(userInitiated)`, both still
            // sitting in the buffer from earlier in this test. The
            // assertion is that stop() did NOT add a THIRD/duplicate
            // connectionChanged on top of those two -- not that there are
            // none at all.
            val events = mutableListOf<ChatEvent>()
            runBlocking { pair.clientA.events.collect { events.add(it) } }
            assertEquals(
                "stop() after disconnect() must not emit a second/duplicate connectionChanged(disconnected) " +
                    "on top of connect()'s own Connecting and disconnect()'s own Disconnected(userInitiated)",
                listOf(ChatConnectionState.Connecting, ChatConnectionState.Disconnected(ChatReasonStrings.USER_INITIATED)),
                events.filterIsInstance<ChatEvent.ConnectionChanged>().map { it.state },
            )
        } finally {
            holdingDispatcher.releaseAll()
            scope.cancel()
            executor.shutdown()
        }
    }
}

/**
 * A [CoroutineDispatcher] that can capture the next dispatched [Runnable]
 * before forwarding it to [delegate]. Capturing is synchronous and
 * non-blocking: no coroutine-executor worker waits on a [CountDownLatch].
 * The owning test waits, with an asserted deadline, for [HeldDispatch] to
 * report capture and later calls [HeldDispatch.release] to submit the
 * runnable. Unarmed dispatches pass straight through.
 *
 * [releaseAll] is an idempotent failure-path cleanup used from each test's
 * `finally` block. It prevents a failed assertion or JUnit timeout from
 * leaving a captured coroutine permanently undispatched.
 */
private class HoldingDispatcher(
    private val delegate: CoroutineDispatcher,
) : CoroutineDispatcher() {
    private val lock = Any()
    private var armedDispatch: HeldDispatch? = null
    private val allHeldDispatches = mutableSetOf<HeldDispatch>()

    fun armNextDispatch(): HeldDispatch =
        synchronized(lock) {
            check(armedDispatch == null) { "a dispatch is already armed" }
            HeldDispatch(delegate).also {
                armedDispatch = it
                allHeldDispatches.add(it)
            }
        }

    override fun dispatch(context: CoroutineContext, block: Runnable) {
        val heldDispatch =
            synchronized(lock) {
                armedDispatch.also { armedDispatch = null }
            }
        if (heldDispatch == null) {
            delegate.dispatch(context, block)
        } else {
            heldDispatch.capture(context, block)
        }
    }

    fun releaseAll() {
        val heldDispatches =
            synchronized(lock) {
                armedDispatch = null
                allHeldDispatches.toList().also { allHeldDispatches.clear() }
            }
        heldDispatches.forEach { it.release() }
    }
}

private class HeldDispatch(
    private val delegate: CoroutineDispatcher,
) {
    private data class CapturedDispatch(
        val context: CoroutineContext,
        val block: Runnable,
    )

    private val lock = Any()
    private val capturedSignal = CountDownLatch(1)
    private var wasCaptured = false
    private var wasReleased = false
    private var capturedDispatch: CapturedDispatch? = null

    fun awaitCaptured(description: String) {
        assertTrue(description, capturedSignal.await(10, TimeUnit.SECONDS))
    }

    fun release() {
        val dispatch =
            synchronized(lock) {
                if (wasReleased) {
                    null
                } else {
                    wasReleased = true
                    capturedDispatch.also { capturedDispatch = null }
                }
            }
        dispatch?.submit()
    }

    fun capture(context: CoroutineContext, block: Runnable) {
        val captured = CapturedDispatch(context, block)
        val dispatchImmediately =
            synchronized(lock) {
                check(!wasCaptured) { "a held dispatch may capture only one runnable" }
                wasCaptured = true
                if (wasReleased) {
                    captured
                } else {
                    capturedDispatch = captured
                    null
                }
            }
        capturedSignal.countDown()
        dispatchImmediately?.submit()
    }

    private fun CapturedDispatch.submit() {
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
