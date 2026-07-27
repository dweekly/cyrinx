@file:OptIn(ExperimentalCoroutinesApi::class)

package com.dweekly.cyrinx.chat.app

import com.dweekly.cyrinx.chat.ChatConnectionState
import com.dweekly.cyrinx.chat.ChatEvent
import com.dweekly.cyrinx.chat.ChatMessageDisplayStatus
import com.dweekly.cyrinx.chat.ChatReasonStrings
import com.dweekly.cyrinx.chat.ChatScenario
import com.dweekly.cyrinx.chat.LinkBudgetClass
import com.dweekly.cyrinx.chat.toHexString
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * End-to-end [ChatViewModel] tests driven through a real, injected
 * [com.dweekly.cyrinx.chat.SimulatedChatPair] under `kotlinx-coroutines-test`
 * virtual time -- the "projection matrix," "send-failure surfacing," and
 * "recreation/resubscribe" parts of this module's required JVM test suite
 * (ChatProjectionReducerTest separately covers the pure reduction rules,
 * including the banner map and gap-caption edge cases the six scenarios never
 * themselves produce). Every test constructs [ChatViewModel] with
 * `scope = backgroundScope` -- see ChatViewModelTestSupport.kt's note on why.
 */
class ChatViewModelTest {
    private fun config(scenario: ChatScenario, seed: Long = 1L, simulated: Boolean = true) =
        ChatLaunchConfig(scenario, seed, simulated)

    // -- projection matrix: one test per pinned scenario ---------------------

    @Test
    fun happyPairEndToEndProjectsAConnectedPeerAndADeliveredMessage() = runTest {
        val recorder = ChatModelTraceRecorder()
        val viewModel =
            ChatViewModel(config(ChatScenario.HAPPY_PAIR), testVirtualTimeSource(), backgroundScope, recorder)

        advanceTimeBy(100)
        runCurrent()
        val peerIdHex = viewModel.uiState.value.peers.single().id.toHexString()
        viewModel.connectToPeer(peerIdHex)
        runCurrent()
        advanceTimeBy(200)
        runCurrent()
        viewModel.sendMessage("hello")
        // happyPair's own final event lands at absolute t=400 (CONTRACT.md
        // section 3.1) from t=300; 150ms clears it with margin. See
        // ChatViewModelTestSupport.kt's `backgroundScope` note for why this is
        // a bounded advanceTimeBy, not advanceUntilIdle().
        advanceTimeBy(150)
        runCurrent()

        val state = viewModel.uiState.value
        assertEquals(ChatConnectionState.Connected, state.connection)
        assertEquals(listOf(peerIdHex), state.peers.map { it.id.toHexString() })
        assertEquals(LinkBudgetClass.TEXT, state.budget.classification)
        assertNull(state.banner)
        assertFalse(state.eventSeqGapDetected)
        assertEquals(0, state.droppedStatusUpdates)

        val message = state.messages.single()
        assertEquals("hello", message.body)
        assertEquals(ChatMessageDisplayStatus.Delivered, message.status)

        // The model-trace recorder produced one line per consumed client-A
        // event, ending at the same final projection checked above.
        val lines = recorder.toJsonLines().trimEnd('\n').split('\n')
        assertTrue(lines.isNotEmpty())
        assertTrue(lines.last().contains("\"connection\": \"connected\""))
    }

    @Test
    fun peerLossEndToEndRemovesThePeerAndSurfacesThePinnedBanner() = runTest {
        val viewModel = ChatViewModel(config(ChatScenario.PEER_LOSS), testVirtualTimeSource(), backgroundScope)

        advanceTimeBy(100)
        runCurrent()
        val peerIdHex = viewModel.uiState.value.peers.single().id.toHexString()
        viewModel.connectToPeer(peerIdHex)
        runCurrent()
        // peerLoss's own final event lands at absolute t=510 (CONTRACT.md
        // section 3.2) from t=100; 450ms clears it with margin. See
        // ChatViewModelTestSupport.kt's `backgroundScope` note for why this is
        // a bounded advanceTimeBy, not advanceUntilIdle().
        advanceTimeBy(450)
        runCurrent()

        val state = viewModel.uiState.value
        assertEquals(ChatConnectionState.Disconnected(ChatReasonStrings.PEER_SILENCE_TIMEOUT), state.connection)
        assertEquals("Peer stopped responding. Move the devices closer and reconnect.", state.banner)
        assertTrue("lost peer must be removed from the peer list", state.peers.isEmpty())
    }

    @Test
    fun degradedThenRecoveredPassesThroughAnObservableDegradedBannerBeforeRecovering() = runTest {
        val viewModel = ChatViewModel(config(ChatScenario.DEGRADED_THEN_RECOVERED), testVirtualTimeSource(), backgroundScope)

        advanceTimeBy(100)
        runCurrent()
        val peerIdHex = viewModel.uiState.value.peers.single().id.toHexString()
        viewModel.connectToPeer(peerIdHex)
        runCurrent()

        // t=100 (connect) -> t=410 (just past the controlOnly link-budget event,
        // still well before t=700's recovery) -- CONTRACT.md section 3.3.
        advanceTimeBy(310)
        runCurrent()
        val degradedState = viewModel.uiState.value
        assertEquals(ChatConnectionState.Degraded, degradedState.connection)
        assertEquals(LinkBudgetClass.CONTROL_ONLY, degradedState.budget.classification)
        assertEquals("Link degraded — move devices closer", degradedState.banner)

        // t=410 -> t=760: well past t=710's recovered linkBudgetChanged
        // (CONTRACT.md section 3.3's final event). See
        // ChatViewModelTestSupport.kt's `backgroundScope` note for why this is
        // a bounded advanceTimeBy, not advanceUntilIdle().
        advanceTimeBy(350)
        runCurrent()
        val recoveredState = viewModel.uiState.value
        assertEquals(ChatConnectionState.Connected, recoveredState.connection)
        assertEquals(LinkBudgetClass.TEXT, recoveredState.budget.classification)
        assertNull(recoveredState.banner)
    }

    @Test
    fun sendFailureLeavesAFailedMessageRowRatherThanRemovingIt() = runTest {
        val viewModel = ChatViewModel(config(ChatScenario.SEND_FAILURE), testVirtualTimeSource(), backgroundScope)

        advanceTimeBy(100)
        runCurrent()
        val peerIdHex = viewModel.uiState.value.peers.single().id.toHexString()
        viewModel.connectToPeer(peerIdHex)
        runCurrent()
        advanceTimeBy(200)
        runCurrent()
        viewModel.sendMessage("will-fail")
        // sendFailure's own final event lands at absolute t=450 (CONTRACT.md
        // section 3.4) from t=300; 200ms clears it with margin. See
        // ChatViewModelTestSupport.kt's `backgroundScope` note for why this is
        // a bounded advanceTimeBy, not advanceUntilIdle().
        advanceTimeBy(200)
        runCurrent()

        val message = viewModel.uiState.value.messages.single()
        assertEquals("will-fail", message.body)
        assertEquals(ChatMessageDisplayStatus.Failed(ChatReasonStrings.NO_ACKNOWLEDGMENT), message.status)
    }

    @Test
    fun slowLinkKeepsTheMessageInTransmittingMidScenarioRatherThanSkippingToATerminalState() = runTest {
        val viewModel = ChatViewModel(config(ChatScenario.SLOW_LINK), testVirtualTimeSource(), backgroundScope)

        advanceTimeBy(100)
        runCurrent()
        val peerIdHex = viewModel.uiState.value.peers.single().id.toHexString()
        viewModel.connectToPeer(peerIdHex)
        runCurrent()
        advanceTimeBy(200)
        runCurrent()
        viewModel.sendMessage("slow")
        // t=300 (send) -> t=1000: well after `transmitting` (t=320) but well
        // before `delivered` (t=2500) -- CONTRACT.md section 3.6's whole point.
        advanceTimeBy(700)
        runCurrent()

        assertEquals(ChatMessageDisplayStatus.Transmitting, viewModel.uiState.value.messages.single().status)

        // t=1000 -> t=2600: well past t=2500's final delivered transition
        // (CONTRACT.md section 3.6). See ChatViewModelTestSupport.kt's
        // `backgroundScope` note for why this is a bounded advanceTimeBy, not
        // advanceUntilIdle().
        advanceTimeBy(1600)
        runCurrent()
        assertEquals(ChatMessageDisplayStatus.Delivered, viewModel.uiState.value.messages.single().status)
    }

    @Test
    fun duplicateIncomingStillProjectsANormalDeliveredSendFromClientAsOwnPerspective() = runTest {
        // The dedup rule itself (client B suppresses the fault-injected
        // redelivery, producing exactly one messageReceived) is client B's own
        // behavior and is already directly covered by chatkit's own
        // ChatScenarioExactTraceTest; this app's ChatViewModel only ever
        // observes client A's event stream (see ChatViewModel's class doc
        // comment), so from A's own perspective this scenario is
        // indistinguishable from a normal successful send -- this test exists
        // for projection-matrix completeness across all six scenarios, not to
        // re-prove the dedup rule.
        val viewModel = ChatViewModel(config(ChatScenario.DUPLICATE_INCOMING), testVirtualTimeSource(), backgroundScope)

        advanceTimeBy(100)
        runCurrent()
        val peerIdHex = viewModel.uiState.value.peers.single().id.toHexString()
        viewModel.connectToPeer(peerIdHex)
        runCurrent()
        advanceTimeBy(200)
        runCurrent()
        viewModel.sendMessage("dup-test")
        // duplicateIncoming's own final event lands at absolute t=400
        // (CONTRACT.md section 3.5) from t=300; 150ms clears it with margin.
        // See ChatViewModelTestSupport.kt's `backgroundScope` note for why
        // this is a bounded advanceTimeBy, not advanceUntilIdle().
        advanceTimeBy(150)
        runCurrent()

        val message = viewModel.uiState.value.messages.single()
        assertEquals("dup-test", message.body)
        assertEquals(ChatMessageDisplayStatus.Delivered, message.status)
    }

    // -- send-failure surfacing (transport-misuse, not the sendFailure SCENARIO) --

    @Test
    fun sendMessageWhileNotConnectedSurfacesATransientErrorNotAMessageRow() = runTest {
        val viewModel = ChatViewModel(config(ChatScenario.HAPPY_PAIR), testVirtualTimeSource(), backgroundScope)

        // No connect() at all: connection is still the initial Disconnected(null).
        viewModel.sendMessage("too early")
        runCurrent()

        val state = viewModel.uiState.value
        assertTrue("a rejected send must not create a message row", state.messages.isEmpty())
        assertNotNull("a rejected send must surface a transient error", state.transientError)
    }

    @Test
    fun connectToAnUnknownPeerSurfacesATransientErrorAndDoesNotRecordAConnectedPeer() = runTest {
        val viewModel = ChatViewModel(config(ChatScenario.HAPPY_PAIR), testVirtualTimeSource(), backgroundScope)

        advanceTimeBy(100)
        runCurrent()
        viewModel.connectToPeer("00000000")
        runCurrent()

        val state = viewModel.uiState.value
        assertNotNull(state.transientError)
        assertNull(state.connectedPeerIdHex)
    }

    // -- recreation / resubscribe ----------------------------------------------

    @Test
    fun aFreshCollectorAfterAPriorOneDetachesImmediatelyObservesCurrentStateAndKeepsUpdating() = runTest {
        val viewModel = ChatViewModel(config(ChatScenario.HAPPY_PAIR), testVirtualTimeSource(), backgroundScope)

        advanceTimeBy(100)
        runCurrent()

        // "Old UI" (e.g. an Activity before a rotation) collects for a while,
        // then detaches -- StateFlow keeps no back-pressure/history for it.
        val firstCollected = mutableListOf<ChatUiState>()
        val firstJob = backgroundScope.launch { viewModel.uiState.collect { firstCollected.add(it) } }
        runCurrent()
        firstJob.cancel()
        assertTrue(firstCollected.isNotEmpty())

        // State keeps advancing with NO collector attached at all.
        val peerIdHex = viewModel.uiState.value.peers.single().id.toHexString()
        viewModel.connectToPeer(peerIdHex)
        runCurrent()
        advanceTimeBy(50)
        runCurrent()
        assertEquals(ChatConnectionState.Connected, viewModel.uiState.value.connection)

        // "Recreated UI" (e.g. the Activity after rotation) attaches a BRAND
        // NEW collector and must see the CURRENT state immediately, not the
        // initial one -- this is the property that makes ViewModel/StateFlow
        // survive configuration changes correctly.
        val secondCollected = mutableListOf<ChatUiState>()
        val secondJob = backgroundScope.launch { viewModel.uiState.collect { secondCollected.add(it) } }
        runCurrent()
        assertEquals(ChatConnectionState.Connected, secondCollected.first().connection)

        // ... and it keeps receiving further updates after "recreation."
        advanceTimeBy(50)
        runCurrent()
        assertTrue("the post-recreation collector must keep receiving updates", secondCollected.size > 1)
        secondJob.cancel()
    }

    // -- messageGap consumption (sequence amendment) --------------------------
    // These drive ChatViewModel's REAL applyEvent collection loop through
    // FakeChatTransportClient (this module's stand-in for chatkit's own
    // test-only injection seam -- see that class's doc comment), since none
    // of the six CONTRACT.md section 3 scenarios ever produce a `messageGap`.

    @Test
    fun messageGapEventIncrementsTheCounterAndAppendsTheExactSystemCaption() = runTest {
        val fake = FakeChatTransportClient()
        val viewModel =
            ChatViewModel(
                config(ChatScenario.HAPPY_PAIR),
                testVirtualTimeSource(),
                backgroundScope,
                transportClientOverride = fake,
                overrideLocalPeerIdHex = "aabbccdd",
            )
        runCurrent()

        fake.emit(ChatEvent.ConnectionChanged(0, ChatConnectionState.Connected))
        fake.emit(ChatEvent.MessageGap(1, fromSequence = 4L, toSequence = 4L))
        runCurrent()

        val state = viewModel.uiState.value
        assertEquals(1, state.messageGaps)
        assertEquals(listOf("Messages missing: sequences 4-4"), state.messageGapNotices.map { it.text })
        // Pin 3: banner priority rules are unchanged -- a plain `connected`
        // transition produces no banner, and the messageGap must not add one.
        assertNull(state.banner)
    }

    @Test
    fun multipleMessageGapEventsAccumulateTheCounterAndEachOwnCaption() = runTest {
        val fake = FakeChatTransportClient()
        val viewModel =
            ChatViewModel(
                config(ChatScenario.HAPPY_PAIR),
                testVirtualTimeSource(),
                backgroundScope,
                transportClientOverride = fake,
                overrideLocalPeerIdHex = "aabbccdd",
            )
        runCurrent()

        fake.emit(ChatEvent.MessageGap(0, fromSequence = 1L, toSequence = 1L))
        fake.emit(ChatEvent.MessageGap(1, fromSequence = 10L, toSequence = 12L))
        runCurrent()

        val state = viewModel.uiState.value
        assertEquals(2, state.messageGaps)
        assertEquals(
            listOf("Messages missing: sequences 1-1", "Messages missing: sequences 10-12"),
            state.messageGapNotices.map { it.text },
        )
    }

    @Test
    fun messageGapConsumptionThroughTheModelTraceRecorderIsByteIdenticalAcrossTwoIndependentRuns() = runTest {
        val script =
            listOf(
                ChatEvent.ConnectionChanged(0, ChatConnectionState.Connected),
                ChatEvent.MessageGap(1, fromSequence = 6L, toSequence = 6L),
                ChatEvent.ConnectionChanged(2, ChatConnectionState.Degraded),
            )

        val first = generateModelTraceFromScript(script)
        val second = generateModelTraceFromScript(script)

        assertTrue("a scripted messageGap run must produce at least one trace line", first.isNotEmpty())
        assertEquals(first, second)
        // Sanity: the new top-level field actually appears, and in the pinned
        // position immediately before "banner" (CONTRACT.md section 4's
        // "Model-trace cross-reference" paragraph).
        assertTrue(first.contains("\"messageGaps\": 1, \"banner\""))
    }

    // -- chat.simulated = false (live adapter not available until C3-31) ------

    @Test
    fun simulatedFalseYieldsAnExplanatoryBannerAndSafeNoOpActions() = runTest {
        val viewModel = ChatViewModel(config(ChatScenario.HAPPY_PAIR, simulated = false), testVirtualTimeSource(), backgroundScope)

        val initial = viewModel.uiState.value
        assertEquals(ChatViewModel.LIVE_TRANSPORT_NOT_YET_AVAILABLE_REASON, initial.banner)
        assertEquals(ChatConnectionState.Disconnected(ChatViewModel.LIVE_TRANSPORT_NOT_YET_AVAILABLE_REASON), initial.connection)

        // Must not throw with no transport client wired up.
        viewModel.sendMessage("hello")
        viewModel.connectToPeer("aabbccdd")
        viewModel.disconnect()
        runCurrent()

        assertTrue(viewModel.uiState.value.messages.isEmpty())
    }
}
