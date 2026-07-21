package com.dweekly.cyrinx.chat

/**
 * UI-facing projection of connection state only -- the transport owns protocol
 * state; this is a coarse view for display, not the source of truth.
 * ../../../CONTRACT.md section 1.2.
 *
 * DECISION (not pinned by the brief, per ../../../CONTRACT.md section 1.2): the
 * `reason` vocabulary for [Disconnected.reason] is free-text, not a closed enum.
 * The six scenario scripts in CONTRACT.md section 3 use a small, consistent set of
 * reason strings (see [ChatReasonStrings]) as illustrative values, not an
 * exhaustive product vocabulary.
 */
sealed class ChatConnectionState {
    data class Disconnected(val reason: String?) : ChatConnectionState()

    object Connecting : ChatConnectionState()

    object Connected : ChatConnectionState()

    object Degraded : ChatConnectionState()

    /** Wire/trace string form used in the JSON-lines trace's `state` field.
     * ../../../CONTRACT.md section 4. */
    val wireName: String
        get() =
            when (this) {
                is Disconnected -> "disconnected"
                Connecting -> "connecting"
                Connected -> "connected"
                Degraded -> "degraded"
            }
}

/**
 * The illustrative `reason` strings CONTRACT.md section 1.2 pins for the six
 * scenario scripts (not an exhaustive product vocabulary -- see
 * [ChatConnectionState]'s class doc).
 */
object ChatReasonStrings {
    const val USER_INITIATED: String = "userInitiated"
    const val PEER_SILENCE_TIMEOUT: String = "peerSilenceTimeout"
    const val NO_ACKNOWLEDGMENT: String = "noAcknowledgment"
    const val TRANSPORT_FAULT: String = "transportFault"

    /** Used by [SimulatedChatTransportClient.cancelSend] -- not one of CONTRACT.md's
     * four illustrative reason strings (cancellation is not one of the six pinned
     * scenarios), added here as the same kind of free-text reason value.
     * DECISION (not pinned by the brief). */
    const val CANCELLED: String = "cancelled"

    /** `messageStatusChanged(failed, failureReason: "disconnected")` for every
     * nonterminal outgoing message when [SimulatedChatTransportClient.disconnect]
     * runs. Pinned exactly by ../../../CONTRACT.md section 2's "Lifecycle
     * cancellation (pinned)". */
    const val DISCONNECTED: String = "disconnected"

    /** `messageStatusChanged(failed, failureReason: "stopped")` for every
     * nonterminal outgoing message when [SimulatedChatTransportClient.stop] runs.
     * Pinned exactly by ../../../CONTRACT.md section 2's "Lifecycle cancellation
     * (pinned)". */
    const val STOPPED: String = "stopped"

    /** `messageStatusChanged(failed, failureReason: "peerLost")` for every
     * nonterminal outgoing message immediately after a scenario-scripted
     * disconnect (for example peerLoss's silence-timeout `connectionChanged
     * (disconnected, ...)`). Pinned exactly by ../../../CONTRACT.md section 2's
     * "Lifecycle cancellation (pinned)" -- distinct from [PEER_SILENCE_TIMEOUT],
     * which is the *connection* state's reason string, not the message failure's. */
    const val PEER_LOST: String = "peerLost"
}
