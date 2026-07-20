package com.dweekly.cyrinx.chat

/**
 * Every event carries [eventSeq], monotonic from 0, per client instance (client
 * A's and client B's `eventSeq` sequences are both independently zero-based and
 * never compared to each other). Eight payload kinds. ../../../CONTRACT.md
 * section 1.7.
 *
 * No event is emitted for a client's implicit initial state (no peers,
 * `disconnected(reason: null)`) -- events represent transitions, not the zero
 * state.
 *
 * DECISION (not pinned by the brief, per ../../../CONTRACT.md section 1.7): pinned
 * here as a shared abstract property realized per-subclass (idiomatic Kotlin,
 * since sealed-class hierarchies commonly hoist a shared field to the base class
 * rather than wrapping) rather than a wrapper struct pairing `eventSeq` with a
 * nested payload enum (the Swift shape). The semantic content -- eventSeq plus
 * exactly these eight payload shapes with exactly these fields -- is identical on
 * both platforms; only which declaration carries `eventSeq` differs.
 */
sealed class ChatEvent {
    abstract val eventSeq: Long

    data class PeerFound(override val eventSeq: Long, val peer: ChatPeer) : ChatEvent()

    data class PeerUpdated(override val eventSeq: Long, val peer: ChatPeer) : ChatEvent()

    data class PeerLost(
        override val eventSeq: Long,
        val peerIdHex: String,
        val reason: String,
    ) : ChatEvent()

    data class ConnectionChanged(
        override val eventSeq: Long,
        val state: ChatConnectionState,
    ) : ChatEvent()

    data class LinkBudgetChanged(
        override val eventSeq: Long,
        val budget: ChatLinkBudget,
    ) : ChatEvent()

    data class MessageReceived(override val eventSeq: Long, val message: ChatMessage) : ChatEvent()

    data class MessageStatusChanged(
        override val eventSeq: Long,
        val messageIdHex: String,
        val status: ChatMessageDisplayStatus,
    ) : ChatEvent()

    data class ClientFailed(override val eventSeq: Long, val reason: String) : ChatEvent()
}
