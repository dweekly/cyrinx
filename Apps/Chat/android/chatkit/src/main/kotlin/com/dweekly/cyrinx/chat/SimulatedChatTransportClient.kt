package com.dweekly.cyrinx.chat

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.launch
import java.util.Collections
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Deterministic, in-process, paired [ChatTransportClient] driven by
 * `(scenarioName, seed)`. ../../../CONTRACT.md section 2. Construct a paired A/B
 * instance via [SimulatedChatPair.create]; do not construct this class directly
 * (its constructor is internal specifically to force pairing through the factory,
 * which is what wires each instance's [peer] reference, derives each client's
 * simulated peer ID from a construction-time [SplitMix64] draw, and seeds each
 * client's own independent message-ID [SplitMix64] stream per
 * ../../../CONTRACT.md section 2's "Message-ID stream (pinned)").
 *
 * The six pinned scenario scripts (../../../CONTRACT.md section 3) are
 * transcribed directly in [connect] and [send] using the incremental delays in
 * [ChatScenarioTimings]. Every scheduled transition runs through ordinary
 * `kotlinx.coroutines.delay`, so under a real dispatcher it elapses in real
 * (short) wall-clock time and under `kotlinx-coroutines-test`'s virtual
 * dispatcher it is driven instantly and deterministically by
 * `advanceTimeBy`/`advanceUntilIdle` -- this class never sleeps, blocks, or reads
 * a wall clock itself (see [VirtualTimeSource]'s doc comment).
 */
class SimulatedChatTransportClient internal constructor(
    /** This client's own simulated transport peer ID (4 bytes, PRNG-derived per
     * ../../../CONTRACT.md section 2's "PRNG draw order contract"). The same value
     * appears as `senderId` in envelopes this client sends. Defensively copied on
     * the way in and on the way out (every [id] read returns a fresh copy), same
     * rationale as [ChatPeer.id]. */
    id: ByteArray,
    /** `'A'` or `'B'` -- this pair's role label, used only for [ChatTraceEntry]
     * generation (../../../CONTRACT.md section 4's `client` trace field) and
     * diagnostics; never used for equality, lookup, or protocol behavior. */
    val label: Char,
    private val scenario: ChatScenario,
    private val scope: CoroutineScope,
    private val timeSource: VirtualTimeSource,
    /** This client's own independent message-ID [SplitMix64] stream, seeded at
     * construction with `seed XOR roleTag` (see [SimulatedChatPair.create]'s
     * `MESSAGE_ID_ROLE_TAG_A`/`MESSAGE_ID_ROLE_TAG_B`) -- NOT the shared
     * construction-stream PRNG that draws the two clients' peer IDs, which ends
     * at draw 2 per ../../../CONTRACT.md section 2. */
    private val messageIdPrng: SplitMix64,
    /** Optional synchronous hook invoked once per emitted event, at the exact
     * moment of emission (same call stack as [ChatEventBus.emit]'s `build`
     * lambda) -- see [SimulatedChatPair.create]'s `recorder` parameter and
     * [ChatTraceRecorder]'s doc comment for why trace capture must happen here
     * rather than by subscribing to [events] after the fact. */
    private val traceSink: ((eventSeq: Long, virtualTimeMs: Long, event: ChatEvent) -> Unit)? = null,
) : ChatTransportClient {
    private val idBytes: ByteArray = id.copyOf()

    val id: ByteArray
        get() = idBytes.copyOf()

    /** Set by [SimulatedChatPair.create] immediately after both instances are
     * constructed; non-null for the rest of this client's lifetime. */
    internal var peer: SimulatedChatTransportClient? = null

    private val eventBus = ChatEventBus()

    /** This client's own view of its `ChatConnectionState`, updated by [emit]
     * whenever a [ChatEvent.ConnectionChanged] is actually delivered to
     * [eventBus] (self-driven via [emit] or peer-driven via [emitFromPeer], which
     * routes through the same private [emit]) -- the source [send]'s precondition
     * check (../../../CONTRACT.md section 2's "Send precondition (pinned)")
     * consults. Starts `Disconnected(reason: null)`, matching this client's
     * implicit initial state (../../../CONTRACT.md section 1.7: "No event is
     * emitted for a client's implicit initial state"). Updated only when the
     * underlying [eventBus] actually accepts the event (i.e. never after
     * [ChatEventBus.close]), so this field also never drifts from "what a
     * consumer of [events] could actually have observed." */
    @Volatile
    private var connectionState: ChatConnectionState = ChatConnectionState.Disconnected(null)

    /** Guards [disconnect] so a repeat call is a no-op, per ../../../CONTRACT.md
     * section 2's "Lifecycle cancellation (pinned)": "a repeat disconnect() is a
     * no-op." */
    private val disconnectedByUser = AtomicBoolean(false)

    /** Guards [stop] so a repeat call is a no-op ("a repeat stop() is a no-op"),
     * per the same pinned section. Once true, [eventBus] is also closed, which
     * independently makes every OTHER method's attempted emissions silent no-ops
     * too (see [ChatEventBus.emit]'s doc comment) -- this flag exists only to
     * make `stop()` ITSELF idempotent (skip re-running its cancellation/
     * terminalization work), not as a general "is this client stopped" gate the
     * rest of this class needs to consult. */
    private val stopped = AtomicBoolean(false)

    /** Assigns `eventSeq`, offers the event to [eventBus], and -- if a
     * [traceSink] is attached -- records (eventSeq, current virtual time, event)
     * synchronously in the same call, so trace timestamps can never drift from
     * true emission time regardless of when a [events] subscriber later drains
     * the buffered flow. Also updates [connectionState] for a
     * [ChatEvent.ConnectionChanged] payload -- see that field's doc comment.
     * [ChatEventBus.emit] itself no-ops (without invoking this lambda at all)
     * once [eventBus] is closed, so none of this runs after [stop]. */
    private fun emit(build: (eventSeq: Long) -> ChatEvent) {
        eventBus.emit { seq ->
            val event = build(seq)
            if (event is ChatEvent.ConnectionChanged) {
                connectionState = event.state
            }
            traceSink?.invoke(seq, timeSource.nowMs(), event)
            event
        }
    }

    /** MessageIds (lowercase hex) this client has already delivered a
     * `messageReceived` for -- the duplicateIncoming dedup set, per
     * ../../../CONTRACT.md section 3.5. */
    private val receivedMessageIds = ConcurrentHashMap.newKeySet<String>()

    /** MessageIds (lowercase hex) that have already reached a terminal outgoing
     * status (`delivered` or `failed`); consulted by [cancelSend] to avoid
     * re-transitioning an already-terminal message. */
    private val terminalMessageIds = ConcurrentHashMap.newKeySet<String>()

    /** The background job for each in-flight `send()` call, by messageId hex, so
     * [cancelSend] and [failNonterminalOutgoingSends] can cancel exactly that
     * call's remaining scheduled transitions. A [LinkedHashMap] (synchronized for
     * safe concurrent access) rather than a [ConcurrentHashMap] specifically
     * because it must preserve SEND ORDER: ../../../CONTRACT.md section 2's
     * "Lifecycle cancellation (pinned)" requires every nonterminal-outgoing-
     * message termination (`disconnect()`, `stop()`, or a scripted disconnect) to
     * fire "in send order," and [ConcurrentHashMap] has no defined iteration
     * order. */
    private val pendingSendJobs: MutableMap<String, Job> = Collections.synchronizedMap(LinkedHashMap())

    /** Current size of [pendingSendJobs]. Exists solely for regression coverage
     * of ../../../CONTRACT.md's lifecycle-cancellation cleanup: [markTerminal]
     * removes a message's entry from [pendingSendJobs] on every terminal
     * transition (delivered, failed, or cancelled), and no assertion on emitted
     * [ChatEvent]s alone can distinguish "cleaned up" from "leaked but no longer
     * scheduled" -- see SimulatedChatTransportClientTest's
     * `pendingSendJobsIsEmptyAfterDeliveryAndAfterCancelSend`. Not part of the
     * public [ChatTransportClient] surface; Kotlin `internal` visibility is
     * module-wide, so this is reachable from this module's test source set
     * without weakening [pendingSendJobs] itself past `private`. */
    internal val pendingSendJobCount: Int
        get() = synchronized(pendingSendJobs) { pendingSendJobs.size }

    private val backgroundJobs = CopyOnWriteArrayList<Job>()

    override val events: Flow<ChatEvent> = eventBus.events

    private fun requirePeer(caller: String): SimulatedChatTransportClient =
        peer ?: throw ChatTransportError("$caller called on a client with no paired peer")

    override suspend fun start() {
        val other = requirePeer("start()")
        val job =
            scope.launch {
                delay(ChatScenarioTimings.PEER_FOUND_DELAY_MS)
                val now = timeSource.nowMs()
                emit { seq -> ChatEvent.PeerFound(seq, ChatPeer(other.idBytes, now)) }
            }
        backgroundJobs.add(job)
    }

    /** Cancels every scheduled action for this client -- every entry in
     * [backgroundJobs] (handshake steps, message status transitions, link-budget
     * events -- anything scheduled via `scope.launch` anywhere in this class,
     * including every `send()` job, which is added to both this list and
     * [pendingSendJobs]). ../../../CONTRACT.md section 2's "Lifecycle
     * cancellation (pinned)": "Scheduled simulator work must never outlive the
     * state that scheduled it." Shared by [disconnect] and [stop] only -- a
     * scenario-scripted disconnect (see [failNonterminalOutgoingSends]'s call
     * sites in [connect]) must NOT call this, since that codepath runs FROM
     * WITHIN one of these same background jobs and cancelling it out from under
     * itself would abort the rest of that scenario's own script (e.g. peerLoss's
     * later `peerLost` events). */
    private fun cancelBackgroundJobs() {
        backgroundJobs.forEach { it.cancel() }
        backgroundJobs.clear()
    }

    /**
     * Cancels this client's own pending, nonterminal `send()` jobs and emits
     * `messageStatusChanged(failed, failureReason: reason)` for each, IN SEND
     * ORDER -- the shared machinery behind [disconnect], [stop], and every
     * scenario-scripted disconnect transition inside [connect] (e.g. peerLoss's
     * silence timeout). ../../../CONTRACT.md section 2's "Lifecycle cancellation
     * (pinned)".
     *
     * Deliberately does NOT touch [backgroundJobs] or [connectionState] itself --
     * callers own those separately: [disconnect]/[stop] call [cancelBackgroundJobs]
     * themselves (this client's own background jobs), while a scripted disconnect
     * must not (see that method's doc comment), and the connectionChanged event
     * that actually changes [connectionState] is a peer/caller-specific emission
     * this method has no opinion about the wording of.
     *
     * Callable on `other` (as `other.failNonterminalOutgoingSends(...)`) exactly
     * like [emitFromPeer] and [deliverEnvelope] -- internal (module-visible), not
     * part of the public [ChatTransportClient] surface, but reachable
     * cross-instance since Kotlin `internal` visibility is module-wide, not
     * instance-scoped.
     */
    internal fun failNonterminalOutgoingSends(reason: String) {
        val pendingInSendOrder: List<String>
        synchronized(pendingSendJobs) {
            pendingInSendOrder = pendingSendJobs.keys.toList()
            pendingSendJobs.values.forEach { it.cancel() }
            pendingSendJobs.clear()
        }
        for (messageIdHex in pendingInSendOrder) {
            markTerminal(messageIdHex)
            emit { seq -> ChatEvent.MessageStatusChanged(seq, messageIdHex, ChatMessageDisplayStatus.Failed(reason)) }
        }
    }

    override suspend fun stop() {
        // "A repeat stop() is a no-op." (../../../CONTRACT.md section 2).
        if (!stopped.compareAndSet(false, true)) return

        cancelBackgroundJobs()
        failNonterminalOutgoingSends(ChatReasonStrings.STOPPED)
        // "emits connectionChanged(disconnected, reason: "stopped") unless the
        // state is already disconnected" (../../../CONTRACT.md section 2).
        if (connectionState !is ChatConnectionState.Disconnected) {
            emit { seq -> ChatEvent.ConnectionChanged(seq, ChatConnectionState.Disconnected(ChatReasonStrings.STOPPED)) }
        }
        // "then finishes the event stream" -- must be LAST: every emission above
        // has to actually reach eventBus before it stops accepting new events.
        eventBus.close()
    }

    override suspend fun connect(peerIdHex: String) {
        val other = requirePeer("connect()")
        val otherIdHex = other.idBytes.toHexString()
        if (!peerIdHex.equals(otherIdHex, ignoreCase = true)) {
            throw ChatTransportError("connect(toPeer=$peerIdHex) does not match known peer $otherIdHex")
        }

        // t=100 in every scenario table: connecting fires synchronously at the
        // call itself (ChatScenarioTimings.CONNECTING_DELAY_MS == 0).
        emit { seq -> ChatEvent.ConnectionChanged(seq, ChatConnectionState.Connecting) }

        val selfIdHex = idBytes.toHexString()
        val job =
            scope.launch {
                delay(ChatScenarioTimings.CONNECTED_DELAY_MS)
                emit { seq -> ChatEvent.ConnectionChanged(seq, ChatConnectionState.Connected) }
                other.emitFromPeer { seq -> ChatEvent.ConnectionChanged(seq, ChatConnectionState.Connected) }

                when (scenario) {
                    ChatScenario.HAPPY_PAIR -> {
                        delay(ChatScenarioTimings.HAPPY_PAIR_LINK_BUDGET_DELAY_MS)
                        emit { seq ->
                            ChatEvent.LinkBudgetChanged(
                                seq,
                                ChatLinkBudget(
                                    LinkBudgetClass.TEXT,
                                    null,
                                    null,
                                    ChatScenarioTimings.HAPPY_PAIR_LINK_BUDGET_CONFIDENCE,
                                    ChatScenarioTimings.LINK_BUDGET_FRESH_AGE_MS,
                                ),
                            )
                        }
                    }

                    ChatScenario.PEER_LOSS -> {
                        delay(ChatScenarioTimings.PEER_LOSS_SILENCE_TIMEOUT_DELAY_MS)
                        val timeoutReason = ChatReasonStrings.PEER_SILENCE_TIMEOUT
                        emit { seq -> ChatEvent.ConnectionChanged(seq, ChatConnectionState.Disconnected(timeoutReason)) }
                        // "every nonterminal outgoing message on that client
                        // transitions to messageStatusChanged(failed,
                        // failureReason: "peerLost") immediately after the
                        // scripted disconnect event" (../../../CONTRACT.md
                        // section 2's "Lifecycle cancellation (pinned)") -- for
                        // THIS client, right after THIS client's own disconnect
                        // event above; the peer's own nonterminal sends get the
                        // same treatment right after ITS own disconnect event
                        // below, not here (each client's own eventSeq stream is
                        // independent).
                        failNonterminalOutgoingSends(ChatReasonStrings.PEER_LOST)

                        other.emitFromPeer { seq -> ChatEvent.ConnectionChanged(seq, ChatConnectionState.Disconnected(timeoutReason)) }
                        other.failNonterminalOutgoingSends(ChatReasonStrings.PEER_LOST)

                        delay(ChatScenarioTimings.PEER_LOSS_PEER_LOST_DELAY_MS)
                        emit { seq -> ChatEvent.PeerLost(seq, otherIdHex, timeoutReason) }
                        other.emitFromPeer { seq -> ChatEvent.PeerLost(seq, selfIdHex, timeoutReason) }
                    }

                    ChatScenario.DEGRADED_THEN_RECOVERED -> {
                        delay(ChatScenarioTimings.DEGRADED_LINK_BUDGET_TEXT_DELAY_MS)
                        emit { seq ->
                            ChatEvent.LinkBudgetChanged(
                                seq,
                                ChatLinkBudget(
                                    LinkBudgetClass.TEXT,
                                    null,
                                    null,
                                    ChatScenarioTimings.DEGRADED_LINK_BUDGET_TEXT_CONFIDENCE,
                                    ChatScenarioTimings.LINK_BUDGET_FRESH_AGE_MS,
                                ),
                            )
                        }

                        delay(ChatScenarioTimings.DEGRADED_DEGRADE_DELAY_MS)
                        emit { seq -> ChatEvent.ConnectionChanged(seq, ChatConnectionState.Degraded) }

                        delay(ChatScenarioTimings.DEGRADED_LINK_BUDGET_LOW_DELAY_MS)
                        emit { seq ->
                            ChatEvent.LinkBudgetChanged(
                                seq,
                                ChatLinkBudget(
                                    LinkBudgetClass.CONTROL_ONLY,
                                    null,
                                    null,
                                    ChatScenarioTimings.DEGRADED_LINK_BUDGET_LOW_CONFIDENCE,
                                    ChatScenarioTimings.LINK_BUDGET_FRESH_AGE_MS,
                                ),
                            )
                        }

                        delay(ChatScenarioTimings.DEGRADED_RECOVER_DELAY_MS)
                        emit { seq -> ChatEvent.ConnectionChanged(seq, ChatConnectionState.Connected) }

                        delay(ChatScenarioTimings.DEGRADED_LINK_BUDGET_RECOVERED_DELAY_MS)
                        emit { seq ->
                            ChatEvent.LinkBudgetChanged(
                                seq,
                                ChatLinkBudget(
                                    LinkBudgetClass.TEXT,
                                    null,
                                    null,
                                    ChatScenarioTimings.DEGRADED_LINK_BUDGET_RECOVERED_CONFIDENCE,
                                    ChatScenarioTimings.LINK_BUDGET_FRESH_AGE_MS,
                                ),
                            )
                        }
                    }

                    ChatScenario.SEND_FAILURE, ChatScenario.DUPLICATE_INCOMING -> {
                        // No connect()-triggered follow-on events in these two
                        // scenarios' tables beyond `connected` itself.
                    }

                    ChatScenario.SLOW_LINK -> {
                        delay(ChatScenarioTimings.SLOW_LINK_LINK_BUDGET_DELAY_MS)
                        emit { seq ->
                            ChatEvent.LinkBudgetChanged(
                                seq,
                                ChatLinkBudget(
                                    LinkBudgetClass.CONTROL_ONLY,
                                    null,
                                    null,
                                    ChatScenarioTimings.SLOW_LINK_LINK_BUDGET_CONFIDENCE,
                                    ChatScenarioTimings.LINK_BUDGET_FRESH_AGE_MS,
                                ),
                            )
                        }
                    }
                }
            }
        backgroundJobs.add(job)
    }

    override suspend fun disconnect() {
        // "a repeat disconnect() is a no-op." (../../../CONTRACT.md section 2). A
        // disconnect() called after stop() also lands here as a harmless
        // first-and-only flip of this flag: cancelBackgroundJobs() has nothing
        // left to cancel, failNonterminalOutgoingSends() has nothing left to
        // terminalize, and the emit() below is silently dropped by the already-
        // closed eventBus (see emit()'s doc comment) -- so no explicit `stopped`
        // check is needed here for correctness.
        if (!disconnectedByUser.compareAndSet(false, true)) return

        cancelBackgroundJobs()
        failNonterminalOutgoingSends(ChatReasonStrings.DISCONNECTED)
        emit { seq -> ChatEvent.ConnectionChanged(seq, ChatConnectionState.Disconnected(ChatReasonStrings.USER_INITIATED)) }
    }

    override suspend fun send(body: String): String {
        val other = requirePeer("send()")

        // "send(body:) is accepted only while the connection state is connected
        // or degraded; in any other state it throws/raises the transport-misuse
        // 'not connected' error and emits no event." (../../../CONTRACT.md
        // section 2's "Send precondition (pinned)"). Checked FIRST, before
        // drawing a message ID or touching the codec, so a rejected send has zero
        // side effects -- not even consuming a PRNG draw that a subsequent,
        // accepted send would otherwise have gotten.
        val stateAtSend = connectionState
        if (stateAtSend != ChatConnectionState.Connected && stateAtSend != ChatConnectionState.Degraded) {
            throw ChatTransportError("send() rejected: not connected or degraded (state=$stateAtSend)")
        }

        val messageId = generateMessageId()
        val messageIdHex = messageId.toHexString()
        // Encoding validates senderId/body bounds and UTF-8, throwing the matching
        // ChatEnvelopeError (e.g. oversizeBody) -- see ENVELOPE.md section 3.
        val envelope =
            ChatEnvelope(ChatEnvelopeCodec.VERSION, ChatEnvelopeKind.TEXT, messageId, null, idBytes, body)
        val encoded = ChatEnvelopeCodec.encode(envelope)

        fun emitStatus(status: ChatMessageDisplayStatus) {
            emit { seq -> ChatEvent.MessageStatusChanged(seq, messageIdHex, status) }
        }

        // t=300 in every scenario table that sends: queued fires synchronously at
        // the call itself (ChatScenarioTimings.QUEUED_DELAY_MS == 0), before
        // send() returns -- "Returns on queue acceptance, NOT delivery"
        // (CONTRACT.md section 1.8).
        emitStatus(ChatMessageDisplayStatus.Queued)

        val job =
            scope.launch {
                delay(ChatScenarioTimings.TRANSMITTING_DELAY_MS)
                emitStatus(ChatMessageDisplayStatus.Transmitting)

                when (scenario) {
                    ChatScenario.SEND_FAILURE -> {
                        delay(ChatScenarioTimings.SEND_FAILURE_FAILED_DELAY_MS)
                        markTerminal(messageIdHex)
                        emitStatus(ChatMessageDisplayStatus.Failed(ChatReasonStrings.NO_ACKNOWLEDGMENT))
                    }

                    ChatScenario.DUPLICATE_INCOMING -> {
                        delay(ChatScenarioTimings.DUPLICATE_DELIVER_DELAY_MS)
                        other.deliverEnvelope(encoded)

                        // Fault-injected retransmit of the exact same bytes; B's
                        // messageId dedup in deliverEnvelope() suppresses it, so
                        // this produces no second messageReceived event.
                        delay(ChatScenarioTimings.DUPLICATE_REDELIVER_DELAY_MS)
                        other.deliverEnvelope(encoded)

                        delay(ChatScenarioTimings.DUPLICATE_DELIVERED_DELAY_MS)
                        markTerminal(messageIdHex)
                        emitStatus(ChatMessageDisplayStatus.Delivered)
                    }

                    ChatScenario.SLOW_LINK -> {
                        delay(ChatScenarioTimings.SLOW_LINK_DELIVER_DELAY_MS)
                        other.deliverEnvelope(encoded)

                        delay(ChatScenarioTimings.SLOW_LINK_DELIVERED_DELAY_MS)
                        markTerminal(messageIdHex)
                        emitStatus(ChatMessageDisplayStatus.Delivered)
                    }

                    // happyPair's own table (../../../CONTRACT.md section 3.1),
                    // AND the generic fallback the "Send precondition (pinned)"
                    // text pins for any send() accepted while connected/degraded
                    // with no scenario-specific script of its own: "follows the
                    // happyPair delivery timeline unless a scenario table or a
                    // lifecycle rule ... overrides it." peerLoss and
                    // degradedThenRecovered never call send() in their own
                    // CONTRACT.md tables, so an off-script send() during either
                    // (e.g. this module's degraded-accepts-send regression test)
                    // falls through to this same branch. A peerLoss scripted
                    // disconnect firing before this timeline completes still
                    // overrides it via failNonterminalOutgoingSends() above.
                    ChatScenario.HAPPY_PAIR, ChatScenario.PEER_LOSS, ChatScenario.DEGRADED_THEN_RECOVERED -> {
                        delay(ChatScenarioTimings.HAPPY_PAIR_DELIVER_DELAY_MS)
                        other.deliverEnvelope(encoded)

                        delay(ChatScenarioTimings.HAPPY_PAIR_DELIVERED_DELAY_MS)
                        markTerminal(messageIdHex)
                        emitStatus(ChatMessageDisplayStatus.Delivered)
                    }
                }
            }
        pendingSendJobs[messageIdHex] = job
        backgroundJobs.add(job)
        return messageIdHex
    }

    override suspend fun cancelSend(messageIdHex: String) {
        // Pinned by ../../../CONTRACT.md section 2's "Behavior outside the six
        // scenario tables (pinned)": cancelSend() cancels the message's remaining
        // scheduled status transitions and emits
        // messageStatusChanged(failed, failureReason: "cancelled"), unless the
        // messageId is unknown or already terminal, in which case it is a no-op.
        if (messageIdHex in terminalMessageIds) return
        val job = pendingSendJobs.remove(messageIdHex) ?: return
        job.cancel()
        markTerminal(messageIdHex)
        val cancelledStatus = ChatMessageDisplayStatus.Failed(ChatReasonStrings.CANCELLED)
        emit { seq -> ChatEvent.MessageStatusChanged(seq, messageIdHex, cancelledStatus) }
    }

    /** Called by [peer]'s scheduled coroutines to emit an event on THIS client's
     * own event bus (own `eventSeq` sequence) -- e.g. both sides of a
     * `connectionChanged(connected)` transition, or `peerLost` on both ends of a
     * timeout. Internal (module-visible), not part of the public
     * [ChatTransportClient] surface. */
    internal fun emitFromPeer(build: (eventSeq: Long) -> ChatEvent) {
        emit(build)
    }

    /** Decodes and, unless it is a dedup'd duplicate, delivers `bytes` as an
     * incoming message on this client -- the "envelope bytes actually cross the
     * codec boundary" mechanism CONTRACT.md section 2 point 2 pins as the
     * simulated pairing's core behavior. Called on the RECEIVING client by the
     * SENDING client's scheduled coroutine. */
    internal fun deliverEnvelope(bytes: ByteArray) {
        val decoded = ChatEnvelopeCodec.decode(bytes)
        val messageIdHex = decoded.messageId.toHexString()
        // Set.add() returns false if the element was already present -- this IS
        // the duplicateIncoming dedup-by-messageId check (CONTRACT.md section 3.5).
        if (!receivedMessageIds.add(messageIdHex)) return

        val now = timeSource.nowMs()
        val message =
            ChatMessage(
                id = decoded.messageId,
                direction = ChatMessage.Direction.INCOMING,
                body = decoded.body,
                senderPeerIdHex = decoded.senderId.toHexString(),
                sentAtWallClockMs = now,
                status = ChatMessageDisplayStatus.Delivered,
            )
        emit { seq -> ChatEvent.MessageReceived(seq, message) }
    }

    private fun markTerminal(messageIdHex: String) {
        terminalMessageIds.add(messageIdHex)
        pendingSendJobs.remove(messageIdHex)
    }

    /** 16-byte message ID, drawn from this client's own independent
     * [messageIdPrng] -- NOT the shared construction-stream PRNG that derives the
     * two clients' peer IDs (that stream ends at draw 2 per ../../../CONTRACT.md
     * section 2's "PRNG draw order contract"). Per section 2's "Message-ID stream
     * (pinned)": each `send()` draws two consecutive u64 values from
     * [messageIdPrng]; the 16-byte message ID is the big-endian serialization of
     * the first draw followed by the big-endian serialization of the second. */
    private fun generateMessageId(): ByteArray =
        messageIdPrng.next().toBigEndianBytes() + messageIdPrng.next().toBigEndianBytes()
}

/**
 * A constructed, paired A/B [SimulatedChatTransportClient] instance for one
 * `(scenario, seed)`. ../../../CONTRACT.md section 2 point 1: "Two
 * `SimulatedChatTransportClient` instances (A and B) are constructed together and
 * share an in-process channel."
 */
class SimulatedChatPair private constructor(
    val clientA: SimulatedChatTransportClient,
    val clientB: SimulatedChatTransportClient,
) {
    companion object {
        /**
         * Big-endian u64 reading of the ASCII bytes `MSGIDA__`, XORed into `seed`
         * to seed client A's independent message-ID [SplitMix64] stream. Pinned in
         * ../../../CONTRACT.md section 2's "Message-ID stream (pinned)".
         */
        private const val MESSAGE_ID_ROLE_TAG_A: Long = 0x4D53474944415F5FL

        /**
         * Big-endian u64 reading of the ASCII bytes `MSGIDB__`, XORed into `seed`
         * to seed client B's independent message-ID [SplitMix64] stream. Pinned in
         * ../../../CONTRACT.md section 2's "Message-ID stream (pinned)".
         */
        private const val MESSAGE_ID_ROLE_TAG_B: Long = 0x4D53474944425F5FL

        /**
         * Draws 1 and 2 from a fresh, throwaway [SplitMix64] seeded from `seed`
         * become client A's and client B's simulated local peer IDs respectively
         * (first 4 bytes of each 8-byte big-endian draw), per
         * ../../../CONTRACT.md section 2's "PRNG draw order contract" -- that
         * stream ends at draw 2 and is discarded, never shared with either client.
         * Each client then gets its OWN independent message-ID [SplitMix64],
         * seeded with `seed XOR roleTag` per section 2's "Message-ID stream
         * (pinned)" (see [MESSAGE_ID_ROLE_TAG_A]/[MESSAGE_ID_ROLE_TAG_B] and
         * [SimulatedChatTransportClient.generateMessageId]).
         */
        fun create(
            scenario: ChatScenario,
            seed: Long,
            scope: CoroutineScope,
            timeSource: VirtualTimeSource,
            /** When non-null, both clients' emitted events are recorded into it as
             * they fire -- see [ChatTraceRecorder]. */
            recorder: ChatTraceRecorder? = null,
        ): SimulatedChatPair {
            val idPrng = SplitMix64(seed)
            val idA = idPrng.next().toBigEndianBytes().copyOfRange(0, 4)
            val idB = idPrng.next().toBigEndianBytes().copyOfRange(0, 4)

            val messageIdPrngA = SplitMix64(seed xor MESSAGE_ID_ROLE_TAG_A)
            val messageIdPrngB = SplitMix64(seed xor MESSAGE_ID_ROLE_TAG_B)

            val clientA =
                SimulatedChatTransportClient(
                    idA, 'A', scenario, scope, timeSource, messageIdPrngA, recorder?.sinkFor('A'),
                )
            val clientB =
                SimulatedChatTransportClient(
                    idB, 'B', scenario, scope, timeSource, messageIdPrngB, recorder?.sinkFor('B'),
                )
            clientA.peer = clientB
            clientB.peer = clientA

            return SimulatedChatPair(clientA, clientB)
        }
    }
}
