package com.dweekly.cyrinx.chat.app

import com.dweekly.cyrinx.chat.ChatReasonStrings

/**
 * The design brief's pinned plain-language banner map (both platforms, EN only
 * for 3.0): "peerSilenceTimeout" -> "Peer stopped responding. Move the devices
 * closer and reconnect."; "userInitiated" -> "Disconnected."; "stopped" ->
 * "Session ended."; "peerLost" -> "Peer lost."; unknown reason -> the raw reason
 * string verbatim.
 *
 * The four known keys are [ChatReasonStrings]' own constants (already pinned,
 * byte-identical with Swift, by CONTRACT.md's own scenario-script reason
 * literals) rather than re-declared string literals here, so this map cannot
 * silently drift from the vocabulary CONTRACT.md's scenarios actually emit.
 */
object ChatBanner {
    /** Pinned degraded-connection banner text (design brief, verbatim). */
    const val DEGRADED_TEXT: String = "Link degraded — move devices closer"

    /** DECISION (not pinned by the brief): exact caption wording for
     * [ChatUiState.gapCaption] -- the brief pins that a small, non-banner
     * caption must exist and be sticky once any eventSeq gap is observed, not
     * this literal string. */
    const val GAP_CAPTION_TEXT: String = "Some events were dropped — state may be stale"

    /**
     * Maps a `ChatConnectionState.Disconnected.reason` (or, per
     * [ChatUiState.peerLostRecoveryReason], the reused literal
     * [ChatReasonStrings.PEER_LOST]) to its pinned plain-language string, or
     * the raw [reason] verbatim for anything outside the four pinned keys --
     * CONTRACT.md section 1.2's "the `reason` vocabulary ... is free-text, not
     * a closed enum ... not an exhaustive product vocabulary."
     */
    fun mapReason(reason: String): String =
        when (reason) {
            ChatReasonStrings.PEER_SILENCE_TIMEOUT -> "Peer stopped responding. Move the devices closer and reconnect."
            ChatReasonStrings.USER_INITIATED -> "Disconnected."
            ChatReasonStrings.STOPPED -> "Session ended."
            ChatReasonStrings.PEER_LOST -> "Peer lost."
            else -> reason
        }
}
