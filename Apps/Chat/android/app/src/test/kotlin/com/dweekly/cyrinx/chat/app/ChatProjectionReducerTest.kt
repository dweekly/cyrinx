package com.dweekly.cyrinx.chat.app

import com.dweekly.cyrinx.chat.ChatConnectionState
import com.dweekly.cyrinx.chat.ChatEvent
import com.dweekly.cyrinx.chat.ChatMessage
import com.dweekly.cyrinx.chat.ChatMessageDisplayStatus
import com.dweekly.cyrinx.chat.ChatPeer
import com.dweekly.cyrinx.chat.ChatReasonStrings
import com.dweekly.cyrinx.chat.hexToByteArray
import com.dweekly.cyrinx.chat.toHexString
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Direct, coroutine-free unit tests of [ChatProjection.reduce] against
 * hand-built [ChatEvent] sequences -- the "projection matrix," "banner map,"
 * and "gap caption" parts of this module's required JVM test suite. Full
 * end-to-end scenario coverage (driven through a real
 * [com.dweekly.cyrinx.chat.SimulatedChatPair] under virtual time) lives in
 * ChatViewModelTest; this class isolates the pure reduction rules themselves,
 * including edge cases (a deliberate eventSeq gap) the six pinned scenarios
 * never actually produce.
 */
class ChatProjectionReducerTest {
    private val peerA = ChatPeer(byteArrayOf(0x0A, 0x0A, 0x0A, 0x0A), discoveredAtMs = 50)
    private val peerB = ChatPeer(byteArrayOf(0x0B, 0x0B, 0x0B, 0x0B), discoveredAtMs = 60)

    // -- peers: sort order, upsert-by-idHex ---------------------------------

    @Test
    fun peerFoundInsertsSortedByDiscoveredAtMsThenIdHex() {
        var state = ChatUiState()
        state = ChatProjection.reduce(state, ChatEvent.PeerFound(0, peerB))
        state = ChatProjection.reduce(state, ChatEvent.PeerFound(1, peerA))

        assertEquals(listOf(peerA.id.toHexString(), peerB.id.toHexString()), state.peers.map { it.id.toHexString() })
    }

    @Test
    fun peerFoundForKnownIdHexReplacesRatherThanDuplicates() {
        var state = ChatUiState()
        state = ChatProjection.reduce(state, ChatEvent.PeerFound(0, peerA))
        val refreshed = ChatPeer(peerA.id, discoveredAtMs = 999)
        state = ChatProjection.reduce(state, ChatEvent.PeerUpdated(1, refreshed))

        assertEquals(1, state.peers.size)
        assertEquals(999L, state.peers.single().discoveredAtMs)
    }

    @Test
    fun peerLostRemovesFromPeers() {
        var state = ChatUiState()
        state = ChatProjection.reduce(state, ChatEvent.PeerFound(0, peerA))
        state = ChatProjection.reduce(state, ChatEvent.PeerFound(1, peerB))
        state = ChatProjection.reduce(state, ChatEvent.PeerLost(2, peerA.id.toHexString(), ChatReasonStrings.PEER_SILENCE_TIMEOUT))

        assertEquals(listOf(peerB.id.toHexString()), state.peers.map { it.id.toHexString() })
    }

    // -- banner map (pinned plain-language strings) --------------------------

    @Test
    fun disconnectedPeerSilenceTimeoutMapsToPinnedString() {
        val state = reduceOne(ChatEvent.ConnectionChanged(0, ChatConnectionState.Disconnected(ChatReasonStrings.PEER_SILENCE_TIMEOUT)))
        assertEquals("Peer stopped responding. Move the devices closer and reconnect.", state.banner)
    }

    @Test
    fun disconnectedUserInitiatedMapsToPinnedString() {
        val state = reduceOne(ChatEvent.ConnectionChanged(0, ChatConnectionState.Disconnected(ChatReasonStrings.USER_INITIATED)))
        assertEquals("Disconnected.", state.banner)
    }

    @Test
    fun disconnectedStoppedMapsToPinnedString() {
        val state = reduceOne(ChatEvent.ConnectionChanged(0, ChatConnectionState.Disconnected(ChatReasonStrings.STOPPED)))
        assertEquals("Session ended.", state.banner)
    }

    @Test
    fun disconnectedPeerLostReasonMapsToPinnedString() {
        val state = reduceOne(ChatEvent.ConnectionChanged(0, ChatConnectionState.Disconnected(ChatReasonStrings.PEER_LOST)))
        assertEquals("Peer lost.", state.banner)
    }

    @Test
    fun disconnectedUnknownReasonSurfacesVerbatim() {
        val state = reduceOne(ChatEvent.ConnectionChanged(0, ChatConnectionState.Disconnected("someFutureReason")))
        assertEquals("someFutureReason", state.banner)
    }

    @Test
    fun disconnectedNullReasonProducesNoBanner() {
        // The implicit initial state (CONTRACT.md section 1.7: "no event is
        // emitted for a client's implicit initial state") -- exercised here via
        // a connectionChanged carrying a null reason, which the six pinned
        // scenarios never actually do post-event, but the reducer must still
        // handle gracefully (falls through past the connection-disconnected
        // tier to "else none").
        val state = reduceOne(ChatEvent.ConnectionChanged(0, ChatConnectionState.Disconnected(null)))
        assertNull(state.banner)
    }

    @Test
    fun degradedProducesPinnedBannerText() {
        val state = reduceOne(ChatEvent.ConnectionChanged(0, ChatConnectionState.Degraded))
        assertEquals("Link degraded — move devices closer", state.banner)
    }

    @Test
    fun connectedProducesNoBanner() {
        val state = reduceOne(ChatEvent.ConnectionChanged(0, ChatConnectionState.Connected))
        assertNull(state.banner)
    }

    @Test
    fun clientFailedOutranksConnectionDisconnectedBanner() {
        var state = ChatUiState()
        state = ChatProjection.reduce(state, ChatEvent.ConnectionChanged(0, ChatConnectionState.Disconnected(ChatReasonStrings.STOPPED)))
        state = ChatProjection.reduce(state, ChatEvent.ClientFailed(1, "internalFault"))

        assertEquals("internalFault", state.banner)
    }

    @Test
    fun clientFailedIsStickyAcrossALaterConnectionChange() {
        var state = ChatUiState()
        state = ChatProjection.reduce(state, ChatEvent.ClientFailed(0, "internalFault"))
        state = ChatProjection.reduce(state, ChatEvent.ConnectionChanged(1, ChatConnectionState.Connected))

        // clientFailed has no defined recovery event (CONTRACT.md's ChatEvent
        // vocabulary), so it must remain the banner even after an otherwise
        // banner-clearing transition.
        assertEquals("internalFault", state.banner)
    }

    // -- peerLost "recovery guidance" cross-reference (design brief) --------

    @Test
    fun peerLostNamingTheConnectedPeerWhileStillConnectedSynthesizesPeerLostBanner() {
        var state = ChatUiState()
        state = ChatProjection.reduce(state, ChatEvent.PeerFound(0, peerB))
        state = state.copy(connectedPeerIdHex = peerB.id.toHexString())
        state = ChatProjection.reduce(state, ChatEvent.ConnectionChanged(1, ChatConnectionState.Connected))
        state = ChatProjection.reduce(state, ChatEvent.PeerLost(2, peerB.id.toHexString(), ChatReasonStrings.PEER_SILENCE_TIMEOUT))

        assertEquals("Peer lost.", state.banner)
        assertTrue(state.peers.none { it.id.toHexString() == peerB.id.toHexString() })
    }

    @Test
    fun peerLostForADifferentPeerDoesNotAffectBanner() {
        var state = ChatUiState()
        state = state.copy(connectedPeerIdHex = peerB.id.toHexString())
        state = ChatProjection.reduce(state, ChatEvent.ConnectionChanged(0, ChatConnectionState.Connected))
        state = ChatProjection.reduce(state, ChatEvent.PeerLost(1, peerA.id.toHexString(), ChatReasonStrings.PEER_SILENCE_TIMEOUT))

        assertNull(state.banner)
    }

    @Test
    fun peerLostRecoveryBannerIsSupersededByTheNextConnectionChange() {
        var state = ChatUiState()
        state = state.copy(connectedPeerIdHex = peerB.id.toHexString())
        state = ChatProjection.reduce(state, ChatEvent.ConnectionChanged(0, ChatConnectionState.Connected))
        state = ChatProjection.reduce(state, ChatEvent.PeerLost(1, peerB.id.toHexString(), ChatReasonStrings.PEER_SILENCE_TIMEOUT))
        assertEquals("Peer lost.", state.banner)

        state = ChatProjection.reduce(state, ChatEvent.ConnectionChanged(2, ChatConnectionState.Disconnected(ChatReasonStrings.USER_INITIATED)))
        assertEquals("Disconnected.", state.banner)
    }

    // -- messages: outgoing insertion is NOT the reducer's job; status update is --

    @Test
    fun messageStatusChangedUpdatesInPlaceByIdHex() {
        val msgIdHex = "aa".repeat(16)
        var state = ChatUiState(messages = listOf(outgoingMessage(msgIdHex, ChatMessageDisplayStatus.Queued)))
        state = ChatProjection.reduce(state, ChatEvent.MessageStatusChanged(0, msgIdHex, ChatMessageDisplayStatus.Transmitting))

        assertEquals(ChatMessageDisplayStatus.Transmitting, state.messages.single().status)
        assertEquals(0, state.droppedStatusUpdates)
    }

    @Test
    fun messageStatusChangedForUnknownIdHexIsIgnoredAndCounted() {
        var state = ChatUiState()
        state = ChatProjection.reduce(state, ChatEvent.MessageStatusChanged(0, "unknown".padEnd(32, '0'), ChatMessageDisplayStatus.Delivered))

        assertTrue(state.messages.isEmpty())
        assertEquals(1, state.droppedStatusUpdates)
    }

    @Test
    fun messageReceivedAppendsIncomingMessage() {
        val incoming =
            ChatMessage(
                id = byteArrayOf(0x01),
                sequence = 1L,
                direction = ChatMessage.Direction.INCOMING,
                body = "hi",
                senderPeerIdHex = peerB.id.toHexString(),
                sentAtWallClockMs = 100,
                status = ChatMessageDisplayStatus.Delivered,
            )
        val state = reduceOne(ChatEvent.MessageReceived(0, incoming))

        assertEquals(1, state.messages.size)
        assertEquals("hi", state.messages.single().body)
        assertEquals(1L, state.messages.single().sequence)
    }

    // -- gap detection (CONTRACT.md section 1.7) -----------------------------

    @Test
    fun noGapForContiguousEventSeqs() {
        var state = ChatUiState()
        state = ChatProjection.reduce(state, ChatEvent.PeerFound(0, peerA))
        state = ChatProjection.reduce(state, ChatEvent.PeerFound(1, peerB))

        assertFalse(state.eventSeqGapDetected)
        assertNull(state.gapCaption)
    }

    @Test
    fun gapIsDetectedAndCaptionSurfacedWhenAnEventSeqIsSkipped() {
        var state = ChatUiState()
        state = ChatProjection.reduce(state, ChatEvent.PeerFound(0, peerA))
        // eventSeq jumps 0 -> 2: one event (seq 1) was dropped by the bounded
        // drop-oldest buffer (CONTRACT.md section 1.7).
        state = ChatProjection.reduce(state, ChatEvent.PeerFound(2, peerB))

        assertTrue(state.eventSeqGapDetected)
        assertEquals(ChatBanner.GAP_CAPTION_TEXT, state.gapCaption)
    }

    @Test
    fun gapDetectionIsStickyAcrossSubsequentContiguousEvents() {
        var state = ChatUiState()
        state = ChatProjection.reduce(state, ChatEvent.PeerFound(0, peerA))
        state = ChatProjection.reduce(state, ChatEvent.PeerFound(2, peerB))
        assertTrue(state.eventSeqGapDetected)

        state = ChatProjection.reduce(state, ChatEvent.ConnectionChanged(3, ChatConnectionState.Connected))

        assertTrue("eventSeqGapDetected must stay true (sticky)", state.eventSeqGapDetected)
        assertEquals(ChatBanner.GAP_CAPTION_TEXT, state.gapCaption)
    }

    @Test
    fun firstEverEventDoesNotFalselyReportAGap() {
        // lastAppliedEventSeq starts at -1; eventSeq 0 -> gap = 0 - (-1) - 1 = 0.
        val state = reduceOne(ChatEvent.ConnectionChanged(0, ChatConnectionState.Connecting))
        assertFalse(state.eventSeqGapDetected)
    }

    // -- helpers --------------------------------------------------------------

    private fun reduceOne(event: ChatEvent): ChatUiState = ChatProjection.reduce(ChatUiState(), event)

    private fun outgoingMessage(idHex: String, status: ChatMessageDisplayStatus): ChatMessage =
        ChatMessage(
            id = idHex.hexToByteArray(),
            sequence = 1L,
            direction = ChatMessage.Direction.OUTGOING,
            body = "test",
            senderPeerIdHex = peerA.id.toHexString(),
            sentAtWallClockMs = 0,
            status = status,
        )

    // -- messageGap (CONTRACT.md section 4's "Model-trace cross-reference" /
    // orchestrator sequence amendment) -------------------------------------

    @Test
    fun messageGapIncrementsCounterAndAppendsExactCaptionText() {
        val state = reduceOne(ChatEvent.MessageGap(0, fromSequence = 5L, toSequence = 7L))

        assertEquals(1, state.messageGaps)
        assertEquals(
            listOf("Messages missing: sequences 5-7"),
            state.messageGapNotices.map { it.text },
        )
    }

    @Test
    fun messageGapForASingleMissingSequenceRendersXDashX() {
        val state = reduceOne(ChatEvent.MessageGap(0, fromSequence = 9L, toSequence = 9L))

        assertEquals("Messages missing: sequences 9-9", state.messageGapNotices.single().text)
    }

    @Test
    fun messageGapCounterAccumulatesAcrossMultipleGapEvents() {
        var state = ChatUiState()
        state = ChatProjection.reduce(state, ChatEvent.MessageGap(0, fromSequence = 2L, toSequence = 2L))
        state = ChatProjection.reduce(state, ChatEvent.MessageGap(1, fromSequence = 10L, toSequence = 11L))

        assertEquals(2, state.messageGaps)
        assertEquals(
            listOf("Messages missing: sequences 2-2", "Messages missing: sequences 10-11"),
            state.messageGapNotices.map { it.text },
        )
    }

    @Test
    fun messageGapDoesNotAffectTheBannerPriorityRules() {
        // Pin 3: "Gap caption is not an error: banner priority rules
        // unchanged." A messageGap consumed while a connection-disconnected
        // banner is already showing must not clear or replace it.
        var state = ChatUiState()
        state = ChatProjection.reduce(state, ChatEvent.ConnectionChanged(0, ChatConnectionState.Disconnected(ChatReasonStrings.STOPPED)))
        state = ChatProjection.reduce(state, ChatEvent.MessageGap(1, fromSequence = 1L, toSequence = 1L))

        assertEquals("Session ended.", state.banner)
        assertEquals(1, state.messageGaps)
    }

    @Test
    fun messageGapNoticeAnchorsAfterTheMessagesPresentAtConsumptionTime() {
        var state = ChatUiState()
        val incoming =
            ChatMessage(
                id = byteArrayOf(0x02),
                sequence = 1L,
                direction = ChatMessage.Direction.INCOMING,
                body = "first",
                senderPeerIdHex = peerB.id.toHexString(),
                sentAtWallClockMs = 0,
                status = ChatMessageDisplayStatus.Delivered,
            )
        state = ChatProjection.reduce(state, ChatEvent.MessageReceived(0, incoming))
        state = ChatProjection.reduce(state, ChatEvent.MessageGap(1, fromSequence = 3L, toSequence = 3L))

        assertEquals(1, state.messageGapNotices.single().afterMessageCount)
    }
}
