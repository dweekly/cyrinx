package com.dweekly.cyrinx.chat.app

import com.dweekly.cyrinx.chat.ChatConnectionState
import com.dweekly.cyrinx.chat.ChatLinkBudget
import com.dweekly.cyrinx.chat.ChatMessage
import com.dweekly.cyrinx.chat.ChatPeer
import com.dweekly.cyrinx.chat.LinkBudgetClass

/**
 * One consumed [com.dweekly.cyrinx.chat.ChatEvent.MessageGap]'s rendered
 * system caption -- orchestrator pin (CONTRACT.md section 4's "Model-trace
 * cross-reference" paragraph, sequence amendment): "A consumed messageGap
 * ALSO appends a system caption in the UI conversation: text exactly
 * `Messages missing: sequences X-Y`." [afterMessageCount] anchors this
 * caption's position among [ChatUiState.messages] at the moment it was
 * surfaced (how many message rows existed then), so `ui/MessageList.kt` can
 * interleave it as its own message-list row, in original consumption order,
 * without [ChatUiState.messages] itself needing a non-[ChatMessage] element
 * type. See [com.dweekly.cyrinx.chat.app.ChatBanner.messageGapNoticeText] for
 * the exact text format and [ChatProjection]'s `applyMessageGap` for
 * construction.
 */
data class ChatMessageGapNotice(
    val text: String,
    val afterMessageCount: Int,
)

/**
 * The shared ChatModel/ChatViewModel projection, pinned by
 * `/private/tmp/.../C3_29_30_DESIGN_BRIEF.md`'s "Shared ChatModel/ChatViewModel
 * projection (pinned -- the C3-30 merge gate)" section: identical semantics on
 * both platforms. [peers], [connection], [budget], [messages], [banner], and
 * [eventSeqGapDetected] are exactly the pinned fields; everything below that
 * comment is this platform's own UI-support state, not part of the pinned merge
 * gate (mirrors CONTRACT.md's own "DECISION (not pinned by the brief)" style for
 * anything the brief left to the implementation).
 *
 * Produced by [ChatProjection.reduce] (event-driven fields) and
 * [ChatViewModel] (the two user-action fields the brief calls out separately:
 * outgoing-message insertion at `send()` acceptance, and transient action
 * errors) -- never mutated in place; every transition returns a new immutable
 * copy, consumed as `StateFlow<ChatUiState>`.
 */
data class ChatUiState(
    /** Sorted by (discoveredAtMs ascending, idHex ascending) -- see
     * [ChatProjection.upsertPeerSorted]. */
    val peers: List<ChatPeer> = emptyList(),
    val connection: ChatConnectionState = ChatConnectionState.Disconnected(null),
    val budget: ChatLinkBudget = ChatLinkBudget(LinkBudgetClass.CONTROL_ONLY, null, null, 0.0, 0),
    /** Append-only in event order; outgoing rows are inserted by
     * [ChatViewModel.sendMessage] at `send()` acceptance, incoming rows by
     * [ChatProjection.reduce] on `messageReceived`. */
    val messages: List<ChatMessage> = emptyList(),
    /** Derived plain-language banner text, or `null` for "no banner" -- see
     * [ChatBanner]. */
    val banner: String? = null,
    /** Sticky: true once ANY eventSeq gap has ever been observed on this
     * client's own event stream (CONTRACT.md section 1.7). Never resets to
     * false. */
    val eventSeqGapDetected: Boolean = false,
    /** Small non-banner caption text shown once [eventSeqGapDetected] flips
     * true ("surfaced as a small 'events dropped' caption, not a banner," per
     * the design brief). DECISION (not pinned by the brief): the brief pins
     * the BEHAVIOR (a caption exists, is not a banner, and is sticky) but not
     * this exact string. */
    val gapCaption: String? = null,
    /** Sequence amendment (CONTRACT.md section 4's "Model-trace cross-
     * reference" paragraph): sticky count of `messageGap` [com.dweekly.cyrinx.chat.ChatEvent]s
     * consumed so far, default `0`. Distinct from [eventSeqGapDetected]/
     * [gapCaption] above -- those track THIS client's own bounded-event-buffer
     * drops (CONTRACT.md section 1.7); this tracks missing envelope
     * `sequence` numbers the receiver gave up on (CONTRACT.md section 2's
     * "Gap surfacing (pinned)"). Mirrors the model-trace schema's own
     * top-level `messageGaps` field (see [com.dweekly.cyrinx.chat.app.ChatModelTraceEntry]). */
    val messageGaps: Int = 0,
    /** One entry per consumed `messageGap` event, in consumption order -- see
     * [ChatMessageGapNotice]'s doc comment. Sticky (never cleared), matching
     * [messageGaps]'s own accumulate-only semantics. */
    val messageGapNotices: List<ChatMessageGapNotice> = emptyList(),
    /** Count of `messageStatusChanged` events whose `messageIdHex` matched no
     * known message (design brief: "unknown idHex is ignored (already-terminal
     * races) but counted in droppedStatusUpdates"). Not part of the pinned
     * projection fields proper, but explicitly named by the brief, so tracked
     * here rather than discarded. Exposed for diagnostics/testing, not shown
     * as user-facing chrome in this stage. */
    val droppedStatusUpdates: Int = 0,
    /** The idHex most recently accepted by a successful `connect()` call --
     * i.e. "the connected peer" the design brief's peerLost/banner-recovery
     * rule refers to. Set by [ChatViewModel.connectToPeer] on acceptance
     * (mirrors the send()-acceptance pattern for consistency), not derived
     * from any single event. See [ChatProjection.applyPeerLost]. */
    val connectedPeerIdHex: String? = null,
    /** clientFailed's raw (unmapped) reason -- highest banner priority. Sticky
     * once set: CONTRACT.md's `clientFailed` is a client-instance-ending
     * condition with no defined recovery event, so nothing in this reducer
     * ever clears it back to null. */
    val clientFailedReason: String? = null,
    /** Internal seam for the peerLost/banner-recovery rule: set to the literal
     * string `"peerLost"` (../../../CONTRACT.md section 5's own
     * [com.dweekly.cyrinx.chat.ChatReasonStrings.PEER_LOST] constant, reused
     * here as a banner-mapping-table KEY, not a message-failure reason) when a
     * `peerLost` event names [connectedPeerIdHex] while [connection] is not
     * already `Disconnected`. Cleared on the next `connectionChanged` (that
     * event's own reason, if any, supersedes it). See [ChatBanner]. */
    val peerLostRecoveryReason: String? = null,
    /** Transient user-facing error from a synchronously-thrown transport-misuse
     * exception (`send()`'s notConnected/terminal/concurrentCommand, or an
     * equivalent `connect()` failure) -- design brief: "surface as a transient
     * composer error string, not a message row." DECISION (not pinned by the
     * brief): the brief's wording is specific to send() failures; this field
     * is reused for connect() failures too (both render through the same
     * `ChatTestTags.ERROR_BANNER` surface -- CONTRACT.md section 5 pins only
     * one `errorBanner` identifier, not a send-specific and connect-specific
     * pair), rather than introducing a second untagged error surface. Cleared
     * by [ChatViewModel] on the next successful action. */
    val transientError: String? = null,
    /** This client's own last-consumed eventSeq, used by [ChatProjection] to
     * compute [eventSeqGapDetected] (CONTRACT.md section 1.7's
     * `gap = currentEventSeq - previousEventSeq - 1`). `-1` before any event
     * has been consumed, matching that formula's `gap == 0` result for the
     * very first event (`eventSeq == 0`). Internal bookkeeping, not
     * user-facing. */
    val lastAppliedEventSeq: Long = -1,
)
