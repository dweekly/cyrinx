package com.dweekly.cyrinx.chat.app

import com.dweekly.cyrinx.chat.ChatConnectionState
import com.dweekly.cyrinx.chat.ChatEvent
import com.dweekly.cyrinx.chat.ChatMessage
import com.dweekly.cyrinx.chat.ChatMessageDisplayStatus
import com.dweekly.cyrinx.chat.ChatPeer
import com.dweekly.cyrinx.chat.ChatReasonStrings
import com.dweekly.cyrinx.chat.hexToByteArray
import com.dweekly.cyrinx.chat.toHexString

/**
 * Pure event -> state reducer implementing the design brief's pinned
 * "Shared ChatModel/ChatViewModel projection" section. Deliberately a plain,
 * side-effect-free function of `(ChatUiState, ChatEvent) -> ChatUiState` --
 * everything about lifecycle, coroutines, or the transport client lives in
 * [ChatViewModel] instead, so this class's behavior (the actual merge-gate
 * surface both platforms must agree on) is directly unit-testable against
 * hand-built [ChatEvent] sequences with no coroutine scope, dispatcher, or
 * transport client involved -- see ChatProjectionReducerTest.
 */
object ChatProjection {
    /**
     * Applies one consumed [ChatEvent] to [state], returning the new state.
     * Always assigns [ChatUiState.lastAppliedEventSeq] and recomputes
     * [ChatUiState.eventSeqGapDetected]/[ChatUiState.gapCaption] first (CONTRACT.md
     * section 1.7's gap-detection contract applies uniformly to every event kind),
     * then applies the event-kind-specific projection rule.
     */
    fun reduce(state: ChatUiState, event: ChatEvent): ChatUiState {
        val withGap = applyGapDetection(state, event.eventSeq)
        val next =
            when (event) {
                is ChatEvent.PeerFound -> withGap.copy(peers = upsertPeerSorted(withGap.peers, event.peer))
                is ChatEvent.PeerUpdated -> withGap.copy(peers = upsertPeerSorted(withGap.peers, event.peer))
                is ChatEvent.PeerLost -> applyPeerLost(withGap, event.peerIdHex, event.reason)
                is ChatEvent.ConnectionChanged -> applyConnectionChanged(withGap, event.state)
                is ChatEvent.LinkBudgetChanged -> recomputeBanner(withGap.copy(budget = event.budget))
                is ChatEvent.MessageReceived -> withGap.copy(messages = withGap.messages + event.message)
                is ChatEvent.MessageStatusChanged -> applyMessageStatusChanged(withGap, event.messageIdHex, event.status)
                is ChatEvent.ClientFailed -> recomputeBanner(withGap.copy(clientFailedReason = event.reason))
            }
        return next.copy(lastAppliedEventSeq = event.eventSeq)
    }

    /** CONTRACT.md section 1.7: `gap = currentEventSeq - previousEventSeq - 1`;
     * `gap == 0` means no loss, `gap > 0` means exactly `gap` events were
     * dropped between the two observed. [ChatUiState.eventSeqGapDetected] and
     * [ChatUiState.gapCaption] are sticky -- once set, never cleared. */
    private fun applyGapDetection(state: ChatUiState, eventSeq: Long): ChatUiState {
        if (state.eventSeqGapDetected) return state
        val gap = eventSeq - state.lastAppliedEventSeq - 1
        return if (gap > 0) {
            state.copy(eventSeqGapDetected = true, gapCaption = ChatBanner.GAP_CAPTION_TEXT)
        } else {
            state
        }
    }

    /** Insert-or-replace-by-idHex ([ChatPeer.equals] is id-only, per
     * CONTRACT.md section 1.1), then re-sort by (discoveredAtMs ascending,
     * idHex ascending) -- the design brief's pinned peer ordering. */
    fun upsertPeerSorted(peers: List<ChatPeer>, peer: ChatPeer): List<ChatPeer> {
        val withoutExisting = peers.filterNot { it.id.contentEquals(peer.id) }
        return (withoutExisting + peer).sortedWith(
            compareBy({ it.discoveredAtMs }, { it.id.toHexString() }),
        )
    }

    private fun applyPeerLost(state: ChatUiState, peerIdHex: String, reason: String): ChatUiState {
        val peersAfterRemoval = state.peers.filterNot { it.id.toHexString() == peerIdHex }
        // Design brief: "peerLost removes AND, if it names the connected peer,
        // surfaces recovery guidance (see banner rules)." Only synthesizes the
        // recovery reason while `connection` has not already gone Disconnected
        // (in which case that transition's own mapped reason already takes
        // priority -- see recomputeBanner/ChatBanner.mapReason).
        val namesConnectedPeer = peerIdHex == state.connectedPeerIdHex
        val alreadyDisconnected = state.connection is ChatConnectionState.Disconnected
        val withPeers = state.copy(peers = peersAfterRemoval)
        return if (namesConnectedPeer && !alreadyDisconnected) {
            recomputeBanner(withPeers.copy(peerLostRecoveryReason = ChatReasonStrings.PEER_LOST))
        } else {
            withPeers
        }
    }

    private fun applyConnectionChanged(state: ChatUiState, newState: ChatConnectionState): ChatUiState =
        recomputeBanner(
            // A fresh connectionChanged event always supersedes any earlier
            // synthesized peerLost-recovery banner (see ChatUiState
            // .peerLostRecoveryReason's doc comment).
            state.copy(connection = newState, peerLostRecoveryReason = null),
        )

    /**
     * Banner priority (design brief, pinned): clientFailed reason; connection
     * disconnected w/ reason (mapped); [ChatUiState.peerLostRecoveryReason]
     * (mapped -- the peerLost/"see banner rules" cross-reference, slotted
     * immediately after the connection-disconnected tier since it is the
     * closest analog the brief describes; DECISION, not itself given an
     * explicit priority slot by the brief -- see ChatUiState's doc comment);
     * degraded; else none.
     */
    private fun recomputeBanner(state: ChatUiState): ChatUiState {
        val disconnectedReason = (state.connection as? ChatConnectionState.Disconnected)?.reason
        val banner =
            when {
                state.clientFailedReason != null -> state.clientFailedReason
                disconnectedReason != null -> ChatBanner.mapReason(disconnectedReason)
                state.peerLostRecoveryReason != null -> ChatBanner.mapReason(state.peerLostRecoveryReason)
                state.connection is ChatConnectionState.Degraded -> ChatBanner.DEGRADED_TEXT
                else -> null
            }
        return state.copy(banner = banner)
    }

    /** "messageStatusChanged updates in place by idHex; unknown idHex is
     * ignored (already-terminal races) but counted in droppedStatusUpdates." */
    private fun applyMessageStatusChanged(
        state: ChatUiState,
        messageIdHex: String,
        status: ChatMessageDisplayStatus,
    ): ChatUiState {
        val index = state.messages.indexOfFirst { it.id.toHexString() == messageIdHex }
        if (index < 0) {
            return state.copy(droppedStatusUpdates = state.droppedStatusUpdates + 1)
        }
        val updated = state.messages[index].withStatus(status)
        return state.copy(messages = state.messages.toMutableList().also { it[index] = updated })
    }
}

/** [ChatMessage] has no mutating setter (every field is `val`) -- this rebuilds
 * an equal message with only [status] replaced, matching CONTRACT.md's
 * `messageStatusChanged` semantics ("updates in place by idHex"). */
fun ChatMessage.withStatus(newStatus: ChatMessageDisplayStatus): ChatMessage =
    ChatMessage(id, direction, body, senderPeerIdHex, sentAtWallClockMs, newStatus)

/**
 * Inserts a new OUTGOING [ChatMessage] with [ChatMessageDisplayStatus.Queued]
 * for [idHex] if (and only if) no message with that idHex is already present --
 * the design brief's "Outgoing appended at send() acceptance (status queued,
 * body as composed, idHex from send's return)" rule, applied by
 * [ChatViewModel.sendMessage] rather than [ChatProjection.reduce] because the
 * message BODY is only known to the sender at `send()` call time, not
 * recoverable from the wire-level `messageStatusChanged(queued)` event alone
 * (that event carries only idHex + status). Idempotent by construction so it is
 * safe regardless of whether the corresponding `messageStatusChanged(queued)`
 * transport event has already been processed by [ChatProjection.reduce] by the
 * time this runs (see ChatViewModel.sendMessage's doc comment for why that
 * ordering is not otherwise guaranteed, and why an already-present row here is
 * harmless either way).
 */
fun ChatUiState.withOutgoingQueuedIfAbsent(
    idHex: String,
    body: String,
    senderPeerIdHex: String,
    sentAtWallClockMs: Long,
): ChatUiState {
    if (messages.any { it.id.toHexString() == idHex }) return this
    val message =
        ChatMessage(
            id = idHex.hexToByteArray(),
            direction = ChatMessage.Direction.OUTGOING,
            body = body,
            senderPeerIdHex = senderPeerIdHex,
            sentAtWallClockMs = sentAtWallClockMs,
            status = ChatMessageDisplayStatus.Queued,
        )
    return copy(messages = messages + message)
}
