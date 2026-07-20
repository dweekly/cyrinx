package com.dweekly.cyrinx.chat

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.launch
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CopyOnWriteArrayList

/**
 * Deterministic, in-process, paired [ChatTransportClient] driven by
 * `(scenarioName, seed)`. ../../../CONTRACT.md section 2. Construct a paired A/B
 * instance via [SimulatedChatPair.create]; do not construct this class directly
 * (its constructor is internal specifically to force pairing through the factory,
 * which is what wires each instance's [peer] reference and shares one
 * [SplitMix64] PRNG instance between them).
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
     * appears as `senderId` in envelopes this client sends. */
    val id: ByteArray,
    /** `'A'` or `'B'` -- this pair's role label, used only for [ChatTraceEntry]
     * generation (../../../CONTRACT.md section 4's `client` trace field) and
     * diagnostics; never used for equality, lookup, or protocol behavior. */
    val label: Char,
    private val scenario: ChatScenario,
    private val scope: CoroutineScope,
    private val timeSource: VirtualTimeSource,
    private val prng: SplitMix64,
    /** Optional synchronous hook invoked once per emitted event, at the exact
     * moment of emission (same call stack as [ChatEventBus.emit]'s `build`
     * lambda) -- see [SimulatedChatPair.create]'s `recorder` parameter and
     * [ChatTraceRecorder]'s doc comment for why trace capture must happen here
     * rather than by subscribing to [events] after the fact. */
    private val traceSink: ((eventSeq: Long, virtualTimeMs: Long, event: ChatEvent) -> Unit)? = null,
) : ChatTransportClient {
    /** Set by [SimulatedChatPair.create] immediately after both instances are
     * constructed; non-null for the rest of this client's lifetime. */
    internal var peer: SimulatedChatTransportClient? = null

    private val eventBus = ChatEventBus()

    /** Assigns `eventSeq`, offers the event to [eventBus], and -- if a
     * [traceSink] is attached -- records (eventSeq, current virtual time, event)
     * synchronously in the same call, so trace timestamps can never drift from
     * true emission time regardless of when a [events] subscriber later drains
     * the buffered flow. */
    private fun emit(build: (eventSeq: Long) -> ChatEvent) {
        eventBus.emit { seq ->
            val event = build(seq)
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
     * [cancelSend] can cancel exactly that call's remaining scheduled
     * transitions. */
    private val pendingSendJobs = ConcurrentHashMap<String, Job>()

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
                emit { seq -> ChatEvent.PeerFound(seq, ChatPeer(other.id, now)) }
            }
        backgroundJobs.add(job)
    }

    override suspend fun stop() {
        backgroundJobs.forEach { it.cancel() }
        backgroundJobs.clear()
        pendingSendJobs.values.forEach { it.cancel() }
        pendingSendJobs.clear()
    }

    override suspend fun connect(peerIdHex: String) {
        val other = requirePeer("connect()")
        val otherIdHex = other.id.toHexString()
        if (!peerIdHex.equals(otherIdHex, ignoreCase = true)) {
            throw ChatTransportError("connect(toPeer=$peerIdHex) does not match known peer $otherIdHex")
        }

        // t=100 in every scenario table: connecting fires synchronously at the
        // call itself (ChatScenarioTimings.CONNECTING_DELAY_MS == 0).
        emit { seq -> ChatEvent.ConnectionChanged(seq, ChatConnectionState.Connecting) }

        val selfIdHex = id.toHexString()
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
                        emit { seq ->
                            ChatEvent.ConnectionChanged(
                                seq,
                                ChatConnectionState.Disconnected(ChatReasonStrings.PEER_SILENCE_TIMEOUT),
                            )
                        }
                        other.emitFromPeer { seq ->
                            ChatEvent.ConnectionChanged(
                                seq,
                                ChatConnectionState.Disconnected(ChatReasonStrings.PEER_SILENCE_TIMEOUT),
                            )
                        }

                        delay(ChatScenarioTimings.PEER_LOSS_PEER_LOST_DELAY_MS)
                        val timeoutReason = ChatReasonStrings.PEER_SILENCE_TIMEOUT
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
        // DECISION (not pinned by the brief): none of the six scenario scripts
        // exercise a driver-initiated disconnect(), so this behavior is not
        // checked against a CONTRACT.md table row. A user-initiated disconnect is
        // modeled the same shape as the other `Disconnected` transitions.
        val reason = ChatReasonStrings.USER_INITIATED
        emit { seq -> ChatEvent.ConnectionChanged(seq, ChatConnectionState.Disconnected(reason)) }
    }

    override suspend fun send(body: String): String {
        val other = requirePeer("send()")
        val messageId = generateMessageId()
        val messageIdHex = messageId.toHexString()
        // Encoding validates senderId/body bounds and UTF-8, throwing the matching
        // ChatEnvelopeError (e.g. oversizeBody) -- see ENVELOPE.md section 3.
        val envelope =
            ChatEnvelope(ChatEnvelopeCodec.VERSION, ChatEnvelopeKind.TEXT, messageId, null, id, body)
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
                    ChatScenario.HAPPY_PAIR -> {
                        delay(ChatScenarioTimings.HAPPY_PAIR_DELIVER_DELAY_MS)
                        other.deliverEnvelope(encoded)

                        delay(ChatScenarioTimings.HAPPY_PAIR_DELIVERED_DELAY_MS)
                        markTerminal(messageIdHex)
                        emitStatus(ChatMessageDisplayStatus.Delivered)
                    }

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

                    ChatScenario.PEER_LOSS, ChatScenario.DEGRADED_THEN_RECOVERED -> {
                        // These two scenarios never call send() per CONTRACT.md
                        // section 3.
                    }
                }
            }
        pendingSendJobs[messageIdHex] = job
        backgroundJobs.add(job)
        return messageIdHex
    }

    override suspend fun cancelSend(messageIdHex: String) {
        // DECISION (not pinned by the brief): cancellation is not one of the six
        // scenario scripts. This cancels the message's remaining scheduled
        // transitions and, if it had not already reached a terminal status,
        // reports it `failed(reason="cancelled")` -- a no-op for an unknown or
        // already-terminal messageId.
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

    /** 16-byte message ID, drawn from the pair's shared [SplitMix64] instance
     * (continuing past the two peer-ID draws consumed at pair construction).
     * ../../../CONTRACT.md section 3's preamble explicitly leaves message-ID
     * generation "out of scope for this document to pin further" -- this is a
     * DECISION (not pinned by the brief); see the C3-28 spec-stage report's
     * `spec_issues` for the resulting cross-platform-byte-identity gap this
     * leaves in the (not yet implemented) Swift codec's own message-ID choice. */
    private fun generateMessageId(): ByteArray =
        prng.next().toBigEndianBytes() + prng.next().toBigEndianBytes()
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
         * Draws 1 and 2 from a fresh [SplitMix64] seeded from `seed` become client
         * A's and client B's simulated local peer IDs respectively (first 4 bytes
         * of each 8-byte big-endian draw), per ../../../CONTRACT.md section 2's
         * "PRNG draw order contract". The same [SplitMix64] instance is then
         * shared by both clients for any further, scenario-triggered draws (see
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
            val prng = SplitMix64(seed)
            val idA = prng.next().toBigEndianBytes().copyOfRange(0, 4)
            val idB = prng.next().toBigEndianBytes().copyOfRange(0, 4)

            val clientA =
                SimulatedChatTransportClient(
                    idA, 'A', scenario, scope, timeSource, prng, recorder?.sinkFor('A'),
                )
            val clientB =
                SimulatedChatTransportClient(
                    idB, 'B', scenario, scope, timeSource, prng, recorder?.sinkFor('B'),
                )
            clientA.peer = clientB
            clientB.peer = clientA

            return SimulatedChatPair(clientA, clientB)
        }
    }
}
