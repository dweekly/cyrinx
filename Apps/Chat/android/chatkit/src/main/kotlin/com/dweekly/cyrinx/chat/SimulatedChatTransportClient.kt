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
import java.util.concurrent.atomic.AtomicLong

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
 *
 * **Concurrency / atomic validation (C3-28 terminality review):** [scope] is
 * caller-supplied and MAY be backed by a genuinely multi-threaded dispatcher
 * (unlike CyrinxChatKit's Swift twin, which is single-sequential-caller by
 * construction). ../../../CONTRACT.md section 2's pinned "Atomic validation"
 * bullet -- "a validated effect can never interleave with an invalidation
 * between its check and its mutation" -- is therefore enforced here with an
 * actual per-client lock ([lifecycleLock]), not merely by argument: every
 * peer-driven effect's generation/terminality check and the mutation it
 * guards run inside that lock as one atomic unit ([runIfLive],
 * [emitConnectedIfBothLive]), and [disconnect]/[stop]'s own invalidation
 * (marking [terminal], bumping [generation]) takes the same lock. See
 * [lifecycleLock]'s doc comment for the full argument, and
 * SimulatedChatTransportClientTest's latch-based tests (reviewer probe P3)
 * for a real multi-threaded reproduction.
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
     * rest of this class needs to consult. Also consulted by [start] ("A
     * stopped client cannot be restarted," ../../../CONTRACT.md section 2's
     * pinned `start()` semantics). */
    private val stopped = AtomicBoolean(false)

    /** Guards [start] so a repeat call is a no-op ("start() is idempotent:
     * repeated calls change nothing and schedule nothing," pinned `start()`
     * semantics). */
    private val started = AtomicBoolean(false)

    /** Guards discovery-arming so it happens at most once per pair, from
     * whichever client's [start] call is the SECOND to observe the other
     * already started -- see [start] and [armDiscoveryForBothClients]. Checked
     * on BOTH sides (`!discoveryArmed && !other.discoveryArmed`), mirroring
     * CyrinxChatKit's Swift `scheduleDiscoveryIfBothStarted()`'s
     * `!discoveryScheduled && !peer.discoveryScheduled` double-check: this
     * class shares that Swift type's single-sequential-caller design
     * assumption (see the class doc comment), so this is defense-in-depth
     * against a same-instant double-arm rather than a proof of thread safety
     * under genuinely concurrent callers. */
    private val discoveryArmed = AtomicBoolean(false)

    /** Monotonically increasing per-client "lifecycle generation," advanced by
     * [disconnect] and [stop] -- ../../../CONTRACT.md section 2's "Target
     * ownership" bullet: "disconnect() and stop() advance the target client's
     * lifecycle generation, and every effect is validated against its target's
     * current generation at fire time; a stale effect is dropped silently."
     *
     * A peer-driven effect that will eventually mutate or emit on THIS client
     * (e.g. the peer's `connect()` scheduling this client's own `connected`
     * transition, or the peer's `send()` scheduling this client's own inbound
     * `messageReceived`) captures [currentGeneration] at the moment it is
     * scheduled and is re-validated against this client's CURRENT generation
     * (AND [terminal] -- see that field's doc comment for why generation
     * equality alone is not enough) immediately before it actually fires, via
     * [runIfLive]. A mismatch, or [terminal] having flipped, means this
     * client's own [disconnect]/[stop] ran sometime between scheduling and
     * firing, so the effect is stale and is silently dropped -- never
     * observed by a consumer of [events], and never even reaching [emit].
     * This is part of what fixes the target-ownership bugs a C3-28 PR review
     * demonstrated with focused harnesses: a sender-owned delivery job that
     * kept delivering `messageReceived` to a receiver after the RECEIVER's
     * own `disconnect()` (the effect was owned by the sender's job, not
     * validated against the receiver's own lifecycle), and a
     * connect()-initiator job that unconditionally emitted the PASSIVE peer's
     * `connected` transition even after that passive peer had itself
     * disconnected mid-handshake.
     *
     * Starts at 0 and only ever increases -- once advanced, a captured
     * generation value is never valid again, even if this client is somehow
     * exercised again later (none of the six CONTRACT.md scenarios do this,
     * but nothing prevents an ad hoc caller from invoking `disconnect()` more
     * than once across this client's lifetime, so staleness must stay
     * permanent rather than resettable). Only ever mutated while holding
     * [lifecycleLock] -- see that field's doc comment. */
    private val generation = AtomicLong(0L)

    /** True once this client's own [disconnect] or [stop] has run --
     * ../../../CONTRACT.md section 2's new pinned "`disconnect()` is terminal
     * for the client instance" bullet. Deliberately a SEPARATE flag from
     * [generation], not merely "generation != 0": a reviewer's round-3 probe
     * (P1/P2) demonstrated that generation equality alone is NOT a sufficient
     * staleness gate for an effect scheduled AFTER this client already went
     * terminal -- such an effect captures [currentGeneration] at schedule
     * time, but that capture happens to already equal the post-disconnect
     * generation value (nothing bumps it again before the effect fires), so a
     * generation-only check would wrongly treat it as still valid. [terminal]
     * closes that gap: once true, it never resets, so [runIfLive] (and
     * [emitConnectedIfBothLive]) reject EVERY subsequent effect against this
     * client regardless of which generation value they captured. Also
     * consulted directly by [connect] and [send] to reject being called on an
     * already-terminal client outright (../../../CONTRACT.md: "the only
     * permitted subsequent call is `stop()`"). Only ever mutated while
     * holding [lifecycleLock]; read both inside that lock (as part of an
     * atomic check-and-mutate) and outside it (as a cheap, monotonic
     * fast-path guard at the top of [connect]/[send] -- a `false` read there
     * can only ever be stale in the direction of "might reject one call late
     * that a perfectly-synchronized read would have rejected already," never
     * the reverse, since [terminal] only ever flips false-to-true). */
    private val terminal = AtomicBoolean(false)

    /** Serializes ../../../CONTRACT.md section 2's pinned "Atomic validation"
     * bullet: "Generation/terminality validation and the target mutation it
     * guards are atomic with respect to lifecycle invalidation: under a
     * caller-supplied concurrent scope, implementations serialize check and
     * mutation against `disconnect()`/`stop()` invalidation (per-client lock,
     * actor, or serial dispatcher), so a validated effect can never
     * interleave with an invalidation between its check and its mutation."
     *
     * Unlike CyrinxChatKit's Swift twin (which satisfies this requirement
     * structurally -- single-sequential-caller, no concurrent scope exists in
     * which an interleaving could occur, per that file's class doc comment),
     * this Kotlin class's [scope] is caller-supplied and MAY be backed by a
     * genuinely multi-threaded dispatcher, so the guarantee has to be
     * enforced with an actual lock. [runIfLive] and
     * [emitConnectedIfBothLive] hold this lock for their ENTIRE
     * check-then-mutate critical section (never merely for the check), and
     * [markTerminalAndRequestCancellation] (the shared core of [disconnect]
     * and [stop]) holds it for setting [terminal] and bumping [generation]
     * (plus requesting -- but not awaiting -- cancellation of this client's
     * own [backgroundJobs], see that method's doc comment) -- so whichever of
     * {a validated effect's check-and-mutate, an invalidation} reaches this
     * monitor first runs to completion before the other can even begin: an
     * effect that wins the race legitimately mutates before the
     * invalidation is visible to anyone; an invalidation that wins the race
     * is fully visible (both [terminal] and [generation]) to every
     * subsequently-arriving effect. Never held across a `delay(...)` or any
     * other suspension point (CONTRACT.md: "Keep lock scope tight; no lock
     * across delay/suspension points -- validate-and-mutate atomically at
     * fire time only") -- every block synchronized on this lock in this
     * class is plain, synchronous code. A plain JVM intrinsic monitor
     * (`synchronized`), matching [ChatEventBus.lock]'s own precedent and
     * rationale in this module. */
    private val lifecycleLock = Any()

    /** Snapshot of [generation], for a peer to capture at the moment it
     * schedules an effect targeting this client -- see [generation]'s doc
     * comment and [runIfLive]. */
    internal fun currentGeneration(): Long = generation.get()

    /** Snapshot of [terminal] -- exposed (module-visible) purely for test
     * assertions; production call sites consult [terminal] directly. */
    internal fun isTerminal(): Boolean = terminal.get()

    /**
     * Runs [action] -- a single-target peer-driven effect that mutates or
     * emits on THIS client -- ATOMICALLY with respect to this client's own
     * [disconnect]/[stop] invalidation: [action] runs if and only if, at the
     * moment this call actually acquires [lifecycleLock], this client is not
     * yet [terminal] AND its [generation] is still exactly
     * [expectedGeneration]. Both conditions are read, and [action] invoked,
     * inside the SAME critical section, so no interleaving invalidation can
     * land between the check and the mutation it guards (../../../
     * CONTRACT.md section 2's "Atomic validation" bullet; see
     * [lifecycleLock]'s doc comment). Checking [terminal] in addition to
     * [generation] is what fixes reviewer probes P1/P2: an effect scheduled
     * AFTER this client already went terminal captures an [expectedGeneration]
     * that trivially still matches at fire time (nothing bumps [generation]
     * again in between), so generation equality alone would wrongly let it
     * through -- terminality, not generation equality alone, is the gate.
     * Otherwise [action] is not invoked at all (not even to build an event).
     *
     * Call this ON THE TARGET client -- e.g. `other.runIfLive
     * (otherGenerationAtConnect) { other.emitFromPeer { ... } }` -- never on
     * `self` for a self-owned effect: a client's own scheduled work is already
     * correctly torn down by its own [disconnect]/[stop] via
     * [markTerminalAndRequestCancellation]'s cancellation sweep.
     *
     * [action] MUST be synchronous and non-suspending (every call site in
     * this class is) -- see [lifecycleLock]'s doc comment on lock scope.
     */
    internal fun runIfLive(expectedGeneration: Long, action: () -> Unit) {
        synchronized(lifecycleLock) {
            if (!terminal.get() && generation.get() == expectedGeneration) {
                action()
            }
        }
    }

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

    /** Current size of [backgroundJobs]. Exists solely for regression coverage
     * of the C3-28 terminality review's backgroundJobs-leak finding:
     * [launchBackgroundJob] used to be the only thing that ever ADDED to
     * [backgroundJobs], and nothing but [markTerminalAndRequestCancellation]
     * (i.e. only [disconnect]/[stop]) ever removed from it -- so a long-lived
     * client that is never disconnected/stopped grew this list by one entry
     * per `connect()`/`send()`/discovery job for the client's entire
     * lifetime, even though the overwhelming majority of those jobs complete
     * (and become garbage) almost immediately. [launchBackgroundJob]'s
     * `invokeOnCompletion` now removes each job the moment IT completes, so a
     * long session's [backgroundJobs] size stays bounded by the number of
     * jobs CURRENTLY in flight, not the number ever launched -- see
     * SimulatedChatTransportClientTest's
     * `backgroundJobsDoesNotGrowAfterEachCompletedSend`. Same "exposed
     * `internal` purely for test regression coverage" rationale as
     * [pendingSendJobCount]. */
    internal val backgroundJobCount: Int
        get() = backgroundJobs.size

    override val events: Flow<ChatEvent> = eventBus.events

    private fun requirePeer(caller: String): SimulatedChatTransportClient =
        peer ?: throw ChatTransportError("$caller called on a client with no paired peer")

    /**
     * Launches [block] on [scope], tracked in this client's own
     * [backgroundJobs] for [markTerminalAndRequestCancellation]'s cancellation
     * sweep -- and, unlike a bare `scope.launch { ... }.also { backgroundJobs
     * .add(it) }`, automatically REMOVES itself from [backgroundJobs] the
     * moment it completes (success, failure, or cancellation alike), via
     * `Job.invokeOnCompletion`. See [backgroundJobCount]'s doc comment for the
     * leak this fixes.
     *
     * Added to [backgroundJobs] BEFORE `invokeOnCompletion` is attached (not
     * after): `invokeOnCompletion`'s handler runs synchronously, immediately,
     * if the job has ALREADY completed by the time the handler is registered
     * (a real possibility for a job whose body happens to complete before
     * this function returns) -- attaching the handler after the job is
     * already in the list guarantees that even an immediate synchronous
     * removal finds (and removes) the entry, rather than racing ahead of the
     * `add` and leaving a permanently-orphaned entry behind.
     */
    private fun launchBackgroundJob(block: suspend CoroutineScope.() -> Unit): Job {
        val job = scope.launch(block = block)
        backgroundJobs.add(job)
        job.invokeOnCompletion { backgroundJobs.remove(job) }
        return job
    }

    /**
     * ../../../CONTRACT.md section 2's pinned `start()` semantics: idempotent
     * (a repeat call changes nothing and schedules nothing, guarded by
     * [started]); a stopped client cannot be restarted (guarded by [stopped]);
     * and discovery is armed only once BOTH clients of a pair have started --
     * "the moment the second client starts, each client's peerFound is
     * scheduled at its section 3 scenario offset relative to that moment."
     *
     * Calls are sequential, never concurrent, in this class's documented
     * single-sequential-caller usage (see the class doc comment), so whichever
     * client's `start()` call is the SECOND one is the only one that ever
     * observes `other.started == true` here -- that call is the one that arms
     * discovery for BOTH clients via [armDiscoveryForBothClients]. The FIRST
     * call simply records `started = true` and returns, having scheduled
     * nothing, and waits for the peer's own `start()` to arm it.
     */
    override suspend fun start() {
        if (stopped.get()) return
        if (!started.compareAndSet(false, true)) return

        val other = requirePeer("start()")
        if (other.started.get() && !discoveryArmed.get() && !other.discoveryArmed.get()) {
            discoveryArmed.set(true)
            other.discoveryArmed.set(true)
            armDiscoveryForBothClients(other)
        }
    }

    /**
     * Schedules `peerFound` for BOTH clients in the pair, [ChatScenarioTimings
     * .PEER_FOUND_DELAY_MS] from the current virtual time -- called exactly
     * once per pair, by whichever client's [start] call observes the other
     * already started. `this` is that (second-to-start) client; [other] is the
     * first.
     */
    private fun armDiscoveryForBothClients(other: SimulatedChatTransportClient) {
        val clientA = if (label == 'A') this else other
        val clientB = if (label == 'A') other else this
        // CyrinxChatKit's Swift `scheduleDiscoveryIfBothStarted()`: "A's
        // peerFound is always scheduled (and so always fires) before B's,
        // regardless of which client's start() happened to trigger this" --
        // every CONTRACT.md section 3 table lists A's peerFound (eventSeq 0)
        // before B's (eventSeq 0) at the same virtual time, and
        // kotlinx-coroutines-test resolves same-due-time tasks in scheduling
        // order, so scheduling order here IS firing order.
        scheduleDiscoveredPeerFound(target = clientA, discoveredPeer = clientB)
        scheduleDiscoveredPeerFound(target = clientB, discoveredPeer = clientA)
    }

    /**
     * Schedules [target]'s own `peerFound(discoveredPeer)` event. If [target]
     * is `this` client, the job is a self-owned scheduled action (added to
     * this client's own [backgroundJobs], so a later self [disconnect]/[stop]
     * cancels it directly, like every other self-owned scheduled action).
     * Otherwise [target] is the peer, and the effect is guarded by
     * [runIfLive] against a generation captured from [target] right now --
     * ../../../CONTRACT.md section 2's "Target ownership" bullet -- so
     * [target]'s own [disconnect]/[stop] between now and the delay elapsing
     * silently drops a `peerFound` the target itself already tore down,
     * rather than emitting it anyway.
     */
    private fun scheduleDiscoveredPeerFound(
        target: SimulatedChatTransportClient,
        discoveredPeer: SimulatedChatTransportClient,
    ) {
        val discoveredIdSnapshot = discoveredPeer.idBytes
        if (target === this) {
            launchBackgroundJob {
                delay(ChatScenarioTimings.PEER_FOUND_DELAY_MS)
                val now = timeSource.nowMs()
                emit { seq -> ChatEvent.PeerFound(seq, ChatPeer(discoveredIdSnapshot, now)) }
            }
        } else {
            val targetGenerationAtSchedule = target.currentGeneration()
            launchBackgroundJob {
                delay(ChatScenarioTimings.PEER_FOUND_DELAY_MS)
                val now = timeSource.nowMs()
                target.runIfLive(targetGenerationAtSchedule) {
                    target.emitFromPeer { seq -> ChatEvent.PeerFound(seq, ChatPeer(discoveredIdSnapshot, now)) }
                }
            }
        }
    }

    /**
     * The shared core of [disconnect] and [stop] -- ../../../CONTRACT.md
     * section 2's "Atomic validation" bullet: "disconnect()'s invalidation
     * (generation bump + terminal flag + cancellation sweep) takes the same
     * lock [as a validated effect's check-and-mutation]." Marks this client
     * [terminal] and bumps its [generation], and snapshots-then-clears
     * [backgroundJobs] while requesting cancellation of every job in that
     * snapshot (`Job.cancel()`, which does not suspend) -- ALL inside
     * [lifecycleLock], so a [runIfLive]/[emitConnectedIfBothLive] call
     * targeting this client either completes entirely first (and its
     * mutation legitimately lands) or observes the fully-updated
     * terminal/generation state (and correctly refuses); never a torn mix of
     * the two.
     *
     * Returns the cancelled jobs for the caller to `join()` -- deliberately
     * OUTSIDE this method and outside [lifecycleLock] (CONTRACT.md: "Keep
     * lock scope tight; no lock across delay/suspension points"): `Job.join()`
     * suspends, and holding a plain JVM monitor across a suspension point is
     * exactly what this lock must never do.
     *
     * Every job is cancelled (requested) as one batch here, rather than
     * cancel-then-immediately-join one at a time by the caller: requesting
     * every cancellation up front means a job that is itself mid-execution
     * (e.g. between two back-to-back `emit()` calls with no suspension point
     * in between) sees its own cancellation requested as early as possible,
     * minimizing -- though, per ordinary cooperative cancellation, not
     * eliminating the theoretical possibility of -- extra work happening
     * before it actually stops. `join()` never throws for a cancelled job
     * (unlike `Deferred.await`), so the caller needs no try/catch.
     *
     * NOT used by a scenario-scripted disconnect (see
     * [failNonterminalOutgoingSends]'s call sites in [connect]): that
     * codepath runs FROM WITHIN one of these same background jobs, and
     * cancelling-and-joining itself out from under itself would deadlock (a
     * coroutine cannot join itself) or, at best, abort the rest of that
     * scenario's own script (e.g. peerLoss's later `peerLost` events) -- nor
     * does a scripted disconnect mark this client [terminal] (only a real
     * [disconnect]/[stop] call does; ../../../CONTRACT.md section 2 does not
     * pin a scripted disconnect as terminal for the client instance the way
     * `disconnect()`/`stop()` are).
     */
    private fun markTerminalAndRequestCancellation(): List<Job> {
        synchronized(lifecycleLock) {
            terminal.set(true)
            generation.incrementAndGet()
            val jobs = backgroundJobs.toList()
            backgroundJobs.clear()
            jobs.forEach { it.cancel() }
            return jobs
        }
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
     * callers own those separately: [disconnect]/[stop] call
     * [markTerminalAndRequestCancellation] themselves (this client's own
     * background jobs), while a scripted disconnect
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

        // "Quiescence before completion": mark this client terminal, bump its
        // generation, and request cancellation of every one of its own
        // in-flight background jobs -- all atomically under lifecycleLock
        // (see markTerminalAndRequestCancellation's doc comment) -- then JOIN
        // them (a suspending wait, so done here, outside that lock) before
        // anything below can reach eventBus.close().
        val jobs = markTerminalAndRequestCancellation()
        jobs.forEach { it.join() }
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

    /**
     * CONTRACT.md section 2's new pinned "Both endpoints live for connection
     * establishment" bullet: "A connectionChanged(connected) transition fires
     * only if BOTH endpoints of the pair are still non-terminal at fire time:
     * either endpoint's disconnect()/stop() before the transition fires drops
     * the transition on both sides." (Reviewer probe P4: an active-side
     * `disconnect()` mid-handshake means NEITHER side ever emits `connected`
     * -- this also covers the passive side disconnecting mid-handshake,
     * which previously left the active side's own `connected` unaffected.)
     *
     * Unlike every other peer-driven effect in this class -- single-target,
     * gated by [runIfLive] on just the target -- this one mutates BOTH `this`
     * and [other] together, so the "both still live" check and both
     * emissions must be validated and fired as ONE atomic unit spanning both
     * clients' [lifecycleLock]s, a two-target counterpart to [runIfLive].
     * Always locks the `'A'`-labeled client's [lifecycleLock] FIRST and
     * [other]'s second, regardless of which of `this`/[other] is which role
     * -- a fixed, total lock ordering that keeps this provably deadlock-free
     * even if both sides of a pair somehow called `connect()` against each
     * other concurrently (CONTRACT.md's six scenarios never do this, but the
     * fixed order costs nothing and removes the assumption).
     *
     * [selfExpectedGeneration]/[otherExpectedGeneration] are captured by the
     * caller at `connect()` call time, mirroring every other peer-driven
     * effect's schedule-time capture.
     */
    private fun emitConnectedIfBothLive(
        other: SimulatedChatTransportClient,
        selfExpectedGeneration: Long,
        otherExpectedGeneration: Long,
    ) {
        val firstLock = if (label == 'A') lifecycleLock else other.lifecycleLock
        val secondLock = if (label == 'A') other.lifecycleLock else lifecycleLock
        synchronized(firstLock) {
            synchronized(secondLock) {
                val selfLive = !terminal.get() && generation.get() == selfExpectedGeneration
                val otherLive = !other.terminal.get() && other.generation.get() == otherExpectedGeneration
                if (selfLive && otherLive) {
                    emit { seq -> ChatEvent.ConnectionChanged(seq, ChatConnectionState.Connected) }
                    other.emitFromPeer { seq -> ChatEvent.ConnectionChanged(seq, ChatConnectionState.Connected) }
                }
            }
        }
    }

    override suspend fun connect(peerIdHex: String) {
        // ../../../CONTRACT.md section 2's new pinned "disconnect() is
        // terminal for the client instance" bullet: a terminal client
        // rejects connect() outright, before touching any state -- the only
        // call still permitted on it is stop(). Checked first (the CALLER's
        // own validity), ahead of the peer-identity validation below.
        if (terminal.get()) {
            throw ChatTransportError("connect() rejected: this client is terminal (disconnect()/stop() already ran)")
        }
        val other = requirePeer("connect()")
        val otherIdHex = other.idBytes.toHexString()
        if (!peerIdHex.equals(otherIdHex, ignoreCase = true)) {
            throw ChatTransportError("connect(toPeer=$peerIdHex) does not match known peer $otherIdHex")
        }
        // Same pinned bullet, continued: "a peer's connect() targeting a
        // terminal client throws and schedules nothing on either side."
        // Checked before any state mutation/emission below (mirroring how
        // the unknown-peer check above also rejects before touching state),
        // so a rejected connect() leaves BOTH clients exactly as it found
        // them -- reviewer probe P1 / CONTRACT.md's required
        // "disconnect-then-peer-connect (terminal target never reconnects)"
        // test.
        if (other.terminal.get()) {
            throw ChatTransportError("connect() rejected: target peer $otherIdHex is terminal (already disconnected/stopped)")
        }

        // t=100 in every scenario table: connecting fires synchronously at the
        // call itself (ChatScenarioTimings.CONNECTING_DELAY_MS == 0).
        emit { seq -> ChatEvent.ConnectionChanged(seq, ChatConnectionState.Connecting) }

        val selfIdHex = idBytes.toHexString()
        // ../../../CONTRACT.md section 2's "Target ownership" bullet: every
        // effect below that targets `other` (the passive peer) -- peerLoss's
        // scripted `disconnected` / `failNonterminalOutgoingSends` /
        // `peerLost` on `other` -- must be validated against OTHER's
        // generation (and terminality) at fire time, not just fired
        // unconditionally because THIS client's own job is still running.
        // Both generations are captured HERE, at connect() call time (before
        // the handshake delay even starts), and re-checked immediately
        // before each effect actually fires -- see runIfLive/
        // emitConnectedIfBothLive.
        val selfGenerationAtConnect = currentGeneration()
        val otherGenerationAtConnect = other.currentGeneration()
        launchBackgroundJob {
            delay(ChatScenarioTimings.CONNECTED_DELAY_MS)
            // "Both endpoints live for connection establishment": fires on
            // BOTH sides, or NEITHER (reviewer probe P4) -- see
            // emitConnectedIfBothLive's doc comment.
            emitConnectedIfBothLive(other, selfGenerationAtConnect, otherGenerationAtConnect)

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

                    other.runIfLive(otherGenerationAtConnect) {
                        other.emitFromPeer { seq -> ChatEvent.ConnectionChanged(seq, ChatConnectionState.Disconnected(timeoutReason)) }
                        other.failNonterminalOutgoingSends(ChatReasonStrings.PEER_LOST)
                    }

                    delay(ChatScenarioTimings.PEER_LOSS_PEER_LOST_DELAY_MS)
                    emit { seq -> ChatEvent.PeerLost(seq, otherIdHex, timeoutReason) }
                    other.runIfLive(otherGenerationAtConnect) {
                        other.emitFromPeer { seq -> ChatEvent.PeerLost(seq, selfIdHex, timeoutReason) }
                    }
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
    }

    override suspend fun disconnect() {
        // "a repeat disconnect() is a no-op." (../../../CONTRACT.md section 2). A
        // disconnect() called after stop() also lands here as a harmless
        // first-and-only flip of this flag: markTerminalAndRequestCancellation()
        // has nothing left to cancel, failNonterminalOutgoingSends() has nothing
        // left to terminalize, and the emit() below is silently dropped by the
        // already-closed eventBus (see emit()'s doc comment) -- so no explicit
        // `stopped` check is needed here for correctness.
        if (!disconnectedByUser.compareAndSet(false, true)) return

        // "Target ownership"/"Atomic validation": mark this client terminal
        // and bump its generation FIRST (atomically, under lifecycleLock --
        // see markTerminalAndRequestCancellation's doc comment), so any
        // peer-driven effect that checks it from here on (including this
        // client's own background jobs we're about to join below) sees this
        // client as terminal. ../../../CONTRACT.md section 2.
        val jobs = markTerminalAndRequestCancellation()
        jobs.forEach { it.join() }
        failNonterminalOutgoingSends(ChatReasonStrings.DISCONNECTED)
        emit { seq -> ChatEvent.ConnectionChanged(seq, ChatConnectionState.Disconnected(ChatReasonStrings.USER_INITIATED)) }
    }

    override suspend fun send(body: String): String {
        // ../../../CONTRACT.md section 2's new pinned "disconnect() is
        // terminal for the client instance" bullet: a terminal client
        // rejects send() outright, before touching any state -- the only
        // call still permitted on it is stop(). This is guaranteed
        // equivalent to (but more direct than) the `connectionState` check
        // just below in every reachable state -- a terminal client's
        // `connectionState` is always `Disconnected`, since [disconnect]/
        // [stop] set it there -- but stating it explicitly here matches
        // CONTRACT.md's wording directly rather than leaning on that
        // invariant implicitly. Deliberately NOT checking `other.terminal`
        // here: send() to a terminal RECEIVER is accepted (this client's own
        // transfer statuses proceed unaffected, per the "Target ownership"
        // bullet below); only delivery to that receiver is what gets
        // dropped, at fire time, via runIfLive.
        if (terminal.get()) {
            throw ChatTransportError("send() rejected: this client is terminal (disconnect()/stop() already ran)")
        }
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

        // ../../../CONTRACT.md section 2's "Target ownership" bullet: "a
        // client never emits messageReceived after its own disconnect()/
        // stop(), even for a message the peer's send() had already scheduled
        // (receiver-side inbound cancellation)." Captured HERE, at send() call
        // time, and re-checked immediately before each `other.deliverEnvelope`
        // call below fires -- this client's OWN transfer statuses
        // (transmitting/delivered/failed, emitted via `emitStatus` above and
        // below) are deliberately left UNGUARDED: "The sender's own transfer
        // statuses are unaffected by the receiver's disconnect."
        val otherGenerationAtSend = other.currentGeneration()

        val job =
            launchBackgroundJob {
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
                        other.runIfLive(otherGenerationAtSend) { other.deliverEnvelope(encoded) }

                        // Fault-injected retransmit of the exact same bytes; B's
                        // messageId dedup in deliverEnvelope() suppresses it, so
                        // this produces no second messageReceived event.
                        delay(ChatScenarioTimings.DUPLICATE_REDELIVER_DELAY_MS)
                        other.runIfLive(otherGenerationAtSend) { other.deliverEnvelope(encoded) }

                        delay(ChatScenarioTimings.DUPLICATE_DELIVERED_DELAY_MS)
                        markTerminal(messageIdHex)
                        emitStatus(ChatMessageDisplayStatus.Delivered)
                    }

                    ChatScenario.SLOW_LINK -> {
                        delay(ChatScenarioTimings.SLOW_LINK_DELIVER_DELAY_MS)
                        other.runIfLive(otherGenerationAtSend) { other.deliverEnvelope(encoded) }

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
                        other.runIfLive(otherGenerationAtSend) { other.deliverEnvelope(encoded) }

                        delay(ChatScenarioTimings.HAPPY_PAIR_DELIVERED_DELAY_MS)
                        markTerminal(messageIdHex)
                        emitStatus(ChatMessageDisplayStatus.Delivered)
                    }
                }
            }
        pendingSendJobs[messageIdHex] = job
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
