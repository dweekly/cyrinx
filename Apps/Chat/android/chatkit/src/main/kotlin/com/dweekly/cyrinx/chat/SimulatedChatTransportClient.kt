package com.dweekly.cyrinx.chat

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
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
 * [runIfBothLive], [emitConnectedIfBothLive]), and [disconnect]/[stop]'s own
 * invalidation (marking [terminal], bumping [generation]) takes the same
 * lock. See [lifecycleLock]'s doc comment for the full argument, and
 * SimulatedChatTransportClientTest's latch-based tests (reviewer probe P3)
 * for a real multi-threaded reproduction.
 *
 * **Linearized admission and registration (round-4 fresh pin, reviewer
 * probes Q1-Q3):** ../../../CONTRACT.md section 2's "Linearized admission
 * and registration" bullet: every public command's admission (its
 * terminality check) is atomic with the registration of whatever work it
 * spawns ([backgroundJobs], and -- via its `onAdmitted` callback --
 * [pendingSendJobs] for [send], or the synchronous `Connecting`/`Queued`
 * self-emission for [connect]/[send]). [launchBackgroundJob] is the shared
 * mechanism that makes this atomic: see that method's doc comment for the
 * TOCTOU hole this closes and how. (Round-4 additionally treated `start()`,
 * `connect()`, `send()`, `disconnect()`, and `stop()` as safe to invoke
 * concurrently, from different threads, against the same pair -- round-5's
 * "Command ownership" pin below supersedes that: this class's tests, e.g.
 * SimulatedChatTransportClientTest's `disconnectFindsAndCancels...`/
 * `disconnectTerminalizesASend...`, still use real, genuinely concurrent
 * threads to exercise this linearized-admission machinery, now via a
 * concurrent [disconnect] racing an ALREADY-ADMITTED job that is held mid-
 * execution on the dispatcher's own worker thread -- see
 * `HoldingDispatcher`'s round-6 rework in SimulatedChatTransportClientTest
 * -- strictly AFTER the admitting command's own call has already fully
 * returned to its caller, per round-6's full-call span below, rather than
 * via two public commands whose spans themselves overlap.)
 *
 * **Command ownership (round-5 fresh pin, round-6 widened to the COMPLETE
 * call):** ../../../CONTRACT.md section 2's "Command ownership (pinned)"
 * bullet: "Public commands ... are owned by one caller at a time: the
 * owner ... invokes them strictly sequentially, never concurrently. This
 * is an enforced model, not an honor rule. ... Ownership spans the
 * COMPLETE public call -- from entry to the call's return to its caller,
 * across any internal suspension -- identically on both platforms."
 * [commandInFlight] is a per-instance, non-blocking CAS guard every public
 * command acquires at entry and is rejected outright
 * (`ChatTransportError.concurrentCommand`, never queued/blocked to wait its
 * turn) if it cannot: see [commandInFlight]'s own doc comment for exactly
 * where every command releases it (as of round-6, uniformly: its own outer
 * `finally`, covering the WHOLE function, for every one of the six public
 * commands -- round-5 had narrowed [connect]/[send]'s span to release
 * early, which round-6 reverses; see that field's doc comment for why).
 * This is a NEW, additional layer on top of -- not a replacement for --
 * [lifecycleLock]'s atomic-validation guarantee above: "Scheduled simulator
 * work may still race a command internally; that interleaving is what the
 * atomic-validation and linearized-admission bullets govern, and Kotlin
 * retains its internal locks as defense in depth" (../../../CONTRACT.md
 * section 2).
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

    /** This client's own view of the peer it has actually observed via its own
     * `peerFound` event -- ../../../CONTRACT.md section 2's "Discovery precedes
     * connection (pinned)" bullet: "`connect(idHex)` is valid only for a peer
     * this client has observed via `peerFound` ... connecting to an unobserved
     * or unknown `idHex` throws the unknown-peer transport-misuse error and
     * mutates nothing on either side." [connect] validates its `peerIdHex`
     * argument against THIS field, never against [peer]'s live identity
     * directly -- see [connect]'s doc comment.
     *
     * Set exactly once per pair (never re-set, never cleared back to `null`),
     * inside [scheduleDiscoveredPeerFound]'s [runIfBothLive]-guarded action, at
     * the exact same synchronous point this client's own `peerFound` event is
     * built and emitted -- so a caller that has observed `peerFound` (via
     * [events]) is guaranteed [connect] already accepts the corresponding
     * `idHex`, and a [connect] call racing a not-yet-fired `peerFound` is
     * guaranteed to be rejected. Mirrors CyrinxChatKit's Swift twin's own
     * `discoveredPeer` field exactly, including that a scripted `peerLost`
     * (the `PEER_LOSS` scenario's own internal script, not a real
     * [disconnect]/[stop]) does NOT clear it back to `null` -- Swift's
     * reference implementation never does either, so this field, once set,
     * stays set for the rest of this client's lifetime. (CONTRACT.md's own
     * prose above also says "and not subsequently lost," which the Swift
     * reference implementation does not actually enforce; matching Swift's
     * shape here keeps both platforms identical rather than introducing a new
     * cross-platform divergence to chase a stricter reading of the prose --
     * see this class's test suite for the open question this leaves.)
     *
     * `@Volatile` because it is written under [lifecycleLock] (via
     * [runIfBothLive], from [scheduleDiscoveredPeerFound]) but read directly,
     * without acquiring any lock, by [connect] -- the same cross-thread
     * visibility pattern as [connectionState] above. */
    @Volatile
    private var discoveredPeer: ChatPeer? = null

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
     * rest of this class needs to consult. [stop] (like [disconnect]) also sets
     * [terminal] true via [markTerminalAndRequestCancellation] -- "A stopped
     * client cannot be restarted" (../../../CONTRACT.md section 2's pinned
     * `start()` semantics) therefore falls out of [start]'s own [terminal]
     * check (round-4 fresh pin: "start() on a terminal client is rejected as
     * transport misuse"), not a direct read of this flag. */
    private val stopped = AtomicBoolean(false)

    /** Guards [start] so a repeat call is a no-op ("start() is idempotent:
     * repeated calls change nothing and schedule nothing," pinned `start()`
     * semantics). */
    private val started = AtomicBoolean(false)

    /** The single canonical per-pair arbiter for discovery-arming: BY
     * CONVENTION only the `'A'`-labeled client's copy of this flag is ever
     * read or written (see [start]'s `arbiter` local) -- the `'B'`-labeled
     * client's own copy is inert. A single [AtomicBoolean.compareAndSet] on
     * one shared instance, rather than the round-3 double-checked pair
     * (`!discoveryArmed.get() && !other.discoveryArmed.get()` then two
     * separate `.set(true)` calls), because that pair is TWO non-atomic
     * steps: two callers racing to arm the SAME pair could both observe
     * "neither armed yet" and both proceed to schedule discovery, double-
     * firing `peerFound`. Round-4's fresh pin -- "The simulator accepts
     * public commands from any thread under this rule" -- puts genuinely
     * concurrent `start()`/`start()` calls (not just `start()`/`disconnect()`)
     * in scope, so arming itself, not merely its downstream effects, must be
     * race-free: exactly one `compareAndSet(false, true)` can ever succeed
     * per pair, so exactly one caller ever reaches
     * [armDiscoveryForBothClients]. See [start] and
     * [armDiscoveryForBothClients]. */
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
     * ../../../CONTRACT.md section 2's pinned "Command ownership (pinned)"
     * bullet: at most one public command's own serialized span may be in
     * flight on THIS client instance at a time. `false` = free, `true` = a
     * public command currently owns the span. Every public command
     * ([start], [connect], [send], [cancelSend], [disconnect], [stop])
     * CAS's this `false -> true` at its very first line; a call that loses
     * the CAS is rejected immediately -- `ChatTransportError
     * .concurrentCommand`, thrown before touching any other state -- rather
     * than queued to wait its turn: "implementations detect concurrent
     * public-command entry deterministically and reject it ... instead of
     * corrupting state." This is a non-blocking try-lock, not a `Mutex`: a
     * caller that violates the "one owner, sequential calls" contract gets a
     * deterministic exception, not a silent wait.
     *
     * **Round-6 fresh pin -- full-call span, identically on both
     * platforms.** Every public command now releases [commandInFlight] in
     * ONE place only: its own outer `finally`, covering every return/throw
     * path, with NOTHING else released early anywhere -- "Ownership spans
     * the COMPLETE public call -- from entry to the call's return to its
     * caller, across any internal suspension -- identically on both
     * platforms: a sequence rejected on one platform is rejected on the
     * other." Round-5 special-cased [connect]/[send] to release EARLY,
     * inside [launchBackgroundJob]'s `onAdmitted` callback, specifically so
     * `Job.start()`'s call a few lines later (which can appear to block the
     * calling thread under a test's instrumented dispatcher -- see
     * `HoldingDispatcher` in SimulatedChatTransportClientTest) could never
     * hold a DIFFERENT public command hostage. That early release was itself
     * the bug: it let a genuinely concurrent second command be ADMITTED
     * while [connect]/[send]'s own call had not yet returned to ITS caller
     * -- an overlap CyrinxChatKit's Swift twin (whose `commandLock`/
     * `commandActive` guard is held for the whole `async` function, and
     * whose analogous "schedule the deferred work" step,
     * `scheduleOwned(atMs:)`, is a plain synchronous append to a virtual
     * clock's queue with no dispatch concept to race) rejects outright.
     * Kotlin now matches that by construction rather than by narrowing the
     * span around a dispatch call: see [launchBackgroundJob]'s doc comment
     * for how `Job.start()` moved OUT of that method entirely -- every
     * caller below now gets back an un-started [Job] (or list of jobs) from
     * its own `OwningCommandSpan` helper, still fully inside the `try` this
     * field guards, and starts it only AFTER the `finally` has already run:
     *
     * ```kotlin
     * if (!commandInFlight.compareAndSet(false, true)) throw ...
     * val job: Job
     * try {
     *     job = xOwningCommandSpan(...)   // admits + registers; never starts
     * } finally {
     *     commandInFlight.set(false)      // the ONLY release site, on every path
     * }
     * job.start()                          // strictly outside the guarded span
     * ```
     *
     * This satisfies CONTRACT.md's OWN two requirements at once, which the
     * round-5 design could not: "ownership spans the complete public call ...
     * identically on both platforms" (there is now exactly one release site,
     * covering the whole function, exactly like [disconnect]/[stop]/[start]/
     * [cancelSend] always did) AND "starting scheduled work inside the span
     * must never synchronously execute or trap that work's body on the
     * caller's thread; the work's first dispatch happens outside the span"
     * (the `job.start()` call above is textually and temporally after this
     * flag is already `false`, so a slow or adversarial `dispatch()`
     * implementation delays only THIS command's own return to ITS caller,
     * never a DIFFERENT command's admission on this same instance).
     *
     * A separate primitive from [lifecycleLock]: [lifecycleLock] is
     * per-effect (guards ONE `runIfLive`/`runIfBothLive` check-and-mutate
     * critical section against invalidation) and is NEVER held across a
     * suspension point; [commandInFlight] is per-COMMAND-INVOCATION (guards
     * an entire public call, potentially spanning [disconnect]/[stop]'s
     * `Job.join()` suspension) and answers a different question ("is
     * another public command already running on this instance?" vs. "is
     * this specific target still live?"). Both are held simultaneously
     * during [launchBackgroundJob]'s admission critical section with no
     * conflict.
     */
    private val commandInFlight = AtomicBoolean(false)

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
     * (otherGenerationAtSend) { other.deliverEnvelope(encoded) }` -- never on
     * `self` for a self-owned effect: a client's own scheduled work is already
     * correctly torn down by its own [disconnect]/[stop] via
     * [markTerminalAndRequestCancellation]'s cancellation sweep. (A job that
     * lives in the TARGET's own [backgroundJobs] -- e.g. one admitted via
     * [launchOwnedBackgroundJobIfLive] -- needs no [runIfLive] re-check of
     * its own at all: the target's own cancellation sweep already finds it
     * directly. See that method's doc comment.)
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

    /**
     * Two-target counterpart to [runIfLive]: runs [action] iff, at the
     * moment this call acquires BOTH [clientA]'s and [clientB]'s
     * [lifecycleLock]s, NEITHER is [terminal] and each is still exactly at
     * its respective expected generation. Used wherever a peer-driven
     * effect requires BOTH pair members to still be live, not just a single
     * target -- [emitConnectedIfBothLive] (the `connectionChanged(connected)`
     * transition, ../../../CONTRACT.md section 2's "Both endpoints live for
     * connection establishment" bullet) and [scheduleDiscoveredPeerFound]
     * (round-4 fresh pin: "a scheduled `peerFound` is dropped at fire time
     * if either endpoint has become terminal") both delegate here.
     *
     * Always locks the caller-designated `'A'`-labeled client's
     * [lifecycleLock] FIRST, [clientB]'s second -- callers always pass the
     * pair's actual `'A'`-labeled instance as [clientA] and the actual
     * `'B'`-labeled instance as [clientB] (never swapped based on which one
     * happens to be `this`), so this ordering is a single fixed, global
     * order for the whole pair, not merely "consistent relative to the
     * caller" -- trivially deadlock-free even under callers racing from
     * both sides at once.
     *
     * Returns whether [action] actually ran -- [emitConnectedIfBothLive]
     * propagates this as its own return value so [connect] can tell whether
     * the joint `connected` transition it just tried to fire was admitted or
     * dropped: ../../../CONTRACT.md section 2's "Post-connect script
     * admission" bullet (round-5 fresh pin) gates the REST of that
     * scenario's script on exactly this outcome. [scheduleDiscoveredPeerFound]
     * ignores the return value -- a dropped `peerFound` has no further script
     * to gate.
     */
    private fun runIfBothLive(
        clientA: SimulatedChatTransportClient,
        clientAExpectedGeneration: Long,
        clientB: SimulatedChatTransportClient,
        clientBExpectedGeneration: Long,
        action: () -> Unit,
    ): Boolean =
        synchronized(clientA.lifecycleLock) {
            synchronized(clientB.lifecycleLock) {
                val aLive = !clientA.terminal.get() && clientA.generation.get() == clientAExpectedGeneration
                val bLive = !clientB.terminal.get() && clientB.generation.get() == clientBExpectedGeneration
                if (aLive && bLive) {
                    action()
                    true
                } else {
                    false
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

    /**
     * Test-only hook, invoked (awaited) at most once per [send] call, INSIDE
     * [sendOwningCommandSpan] -- i.e. still fully inside [send]'s own
     * [commandInFlight]-guarded span, still under the try this class's
     * `finally` releases it in -- immediately after this message's `Queued`
     * self-emission and `pendingSendJobs` registration have already landed,
     * and before [send] returns (or its background job is ever started).
     * Mirrors CyrinxChatKit's Swift twin's
     * `testOnlyAfterQueuedEmissionLatch` (see
     * `SimulatedChatTransportClient.swift`'s `send(body:)` and
     * `SimulatedChatTransportClientCommandOwnershipTests.swift`) exactly: a
     * deterministic way for this class's own command-ownership regressions
     * to hold [send]'s guarded span open across a genuine suspension, so a
     * concurrent SECOND public command attempted while it is held can be
     * proven rejected -- CONTRACT.md section 2's round-6-pinned "Command
     * ownership (pinned)" bullet: "a sequence rejected on one platform is
     * rejected on the other." `null` (every production caller and all six
     * canonical scenario scripts -- SimulatedChatPair.create never sets
     * this) makes this an immediate no-op with no actual suspension, so it
     * has zero effect outside SimulatedChatTransportClientTest's own
     * overlap-parity tests.
     */
    internal var testOnlyAfterQueuedEmissionLatch: (suspend (messageIdHex: String) -> Unit)? = null

    override val events: Flow<ChatEvent> = eventBus.events

    private fun requirePeer(caller: String): SimulatedChatTransportClient =
        peer ?: throw ChatTransportError("$caller called on a client with no paired peer")

    /**
     * Launches [block] on [scope] as a LAZY job -- it cannot run a single
     * line of [block] until [Job.start] is explicitly called below -- tracked
     * in this client's own [backgroundJobs] for
     * [markTerminalAndRequestCancellation]'s cancellation sweep, and, unlike a
     * bare `scope.launch { ... }.also { backgroundJobs.add(it) }`,
     * automatically REMOVES itself from [backgroundJobs] the moment it
     * completes (success, failure, or cancellation alike), via
     * `Job.invokeOnCompletion`. See [backgroundJobCount]'s doc comment for the
     * leak this fixes.
     *
     * **Linearized admission and registration (../../../CONTRACT.md section
     * 2's pinned bullet of that name; reviewer probes Q2/Q3):** the
     * terminal-admission check and the [backgroundJobs] insertion -- plus
     * whatever extra bookkeeping/emission [onAdmitted] performs, e.g.
     * [send]'s `pendingSendJobs` registration and `Queued` emission, or
     * [connect]'s `Connecting` emission -- happen together, inside ONE
     * [lifecycleLock] critical section, BEFORE [job] is ever started. The
     * previous design (`scope.launch(block = block)`, eager
     * `CoroutineStart.DEFAULT`) had a genuine TOCTOU hole here: the eager
     * launch call dispatches [block] for execution as part of the launch
     * call itself, so a concurrent [disconnect]/[stop] could run its ENTIRE
     * sweep -- see an empty [backgroundJobs], find nothing to cancel, return
     * -- in the gap between that launch call returning and the
     * (then-unguarded) `backgroundJobs.add(job)` a few lines later, leaving
     * the newly-added job to run to completion as an orphan un-owned by any
     * sweep and firing events (including `linkBudgetChanged`) after
     * `disconnect()` had already returned. [CoroutineStart.LAZY] closes that
     * hole structurally, not just narrows it: nothing in [job] can execute
     * until [Job.start] runs, and that happens only AFTER this method's
     * critical section has already fully decided [job]'s fate --
     * either admitted (terminal was false: [job] is now durably present in
     * [backgroundJobs], where the very NEXT sweep is guaranteed to find it)
     * or rejected (terminal was already true: [job] is cancelled having
     * never run a line of [block], and this method throws instead of
     * returning it -- "on terminal admission throw without launching").
     * There is no observable middle state by the time [lifecycleLock] is
     * released.
     *
     * [onAdmitted] runs synchronously INSIDE that same critical section,
     * given the already-constructed (but not yet started) [job] so a caller
     * like [send] can key its own bookkeeping (`pendingSendJobs[messageIdHex]
     * = job`) by the exact [Job] instance [markTerminalAndRequestCancellation]
     * will later cancel. Like every other block run under [lifecycleLock] in
     * this class, [onAdmitted] MUST be synchronous and non-suspending.
     *
     * [caller] names the public method this admission is on behalf of, for
     * the rejection [ChatTransportError]'s message only.
     *
     * Added to [backgroundJobs] BEFORE `invokeOnCompletion` is attached (not
     * after): `invokeOnCompletion`'s handler runs synchronously, immediately,
     * if the job has ALREADY completed by the time the handler is registered
     * -- attaching the handler after the job is already in the list
     * guarantees that even an immediate synchronous removal finds (and
     * removes) the entry, rather than racing ahead of the `add` and leaving
     * a permanently-orphaned entry behind.
     *
     * **Round-6 fresh pin: [Job.start] is NOT called here.** Round-5 called
     * it here, LAST, after [lifecycleLock] was already released -- reasoning
     * that "starting a job is a fast, non-suspending call" so it was safe to
     * fire immediately. That reasoning held for a REAL dispatcher but not for
     * an adversarial/instrumented one (a caller-supplied `scope` "MAY be
     * backed by a genuinely multi-threaded dispatcher" -- this class's own
     * doc comment), and more importantly it put `Job.start()`'s call INSIDE
     * whichever public command's [commandInFlight] span was still open at
     * that point for every caller EXCEPT [connect]/[send] (which round-5
     * worked around by releasing early -- itself the round-6 bug; see
     * [commandInFlight]'s doc comment). Returning the constructed-but-
     * unstarted [job] instead and making EVERY caller start it only after
     * its OWN [commandInFlight] release closes that gap uniformly for
     * [start], [connect], and [send] alike, per CONTRACT.md's round-6-pinned
     * "starting scheduled work inside the span must never synchronously
     * execute or trap that work's body on the caller's thread; the work's
     * first dispatch happens outside the span." The atomicity guarantee this
     * method exists for is UNCHANGED: by the time this method returns, [job]
     * is unconditionally either durably registered in [backgroundJobs] (so
     * the very next [disconnect]/[stop] sweep is guaranteed to find, cancel,
     * and join it, even though it has not been started yet -- `Job.cancel()`
     * on an unstarted `CoroutineStart.LAZY` job is well-defined and prevents
     * [Job.start] from ever running a line of [block]) or this method has
     * thrown without registering anything at all. Callers MUST call
     * `.start()` on the returned [job] themselves, strictly after their own
     * [commandInFlight] release.
     */
    private fun launchBackgroundJob(
        caller: String,
        onAdmitted: (job: Job) -> Unit = {},
        block: suspend CoroutineScope.() -> Unit,
    ): Job {
        val job = scope.launch(start = CoroutineStart.LAZY, block = block)
        synchronized(lifecycleLock) {
            if (terminal.get()) {
                job.cancel()
                throw ChatTransportError("$caller rejected: this client is terminal (disconnect()/stop() already ran)")
            }
            backgroundJobs.add(job)
            onAdmitted(job)
        }
        job.invokeOnCompletion { backgroundJobs.remove(job) }
        return job
    }

    /**
     * Cross-instance counterpart to [launchBackgroundJob]: when called as
     * `other.launchOwnedBackgroundJobIfLive { ... }`, admits [block] into
     * `other`'s OWN [backgroundJobs] -- never the caller's -- guarded by
     * `other`'s OWN terminality at the moment of this call.
     * ../../../CONTRACT.md section 2's round-6-pinned "Post-admission
     * script locality" bullet: "each client's post-connect scripted steps
     * are that client's OWN local timeline: they are validated against the
     * owning client's terminality/generation only, not re-checked against
     * the peer." [connect]'s `PEER_LOSS` branch calls this as
     * `other.launchOwnedBackgroundJobIfLive { ... }` to schedule B's own
     * half of ../../../CONTRACT.md section 3.2's both-targeted
     * silence-timeout duo AS B's OWN job -- so A's [disconnect]/[stop]
     * (whose [markTerminalAndRequestCancellation] sweep cancels only THAT
     * client's own [backgroundJobs] entries) can never cancel B's half out
     * from under it. This fixes a reproducible bug an adversarial C3-28
     * round-6 follow-up review found: embedding B's half INLINE inside A's
     * own handshake job (the previous design) meant A's own `disconnect()`
     * between joint-connected admission and the scripted fire time
     * silently cancelled the WHOLE coroutine -- including B's
     * supposedly-independent script -- before it ever reached B's half,
     * even though B itself was still perfectly live. CyrinxChatKit's Swift
     * twin never had this bug: `scheduleScenarioPostConnect(connectedAtMs:)`
     * schedules each target's own copy of every post-connect step via
     * `scheduleOwned` called ON that target, never on the
     * connect()-initiating side (`SimulatedChatTransportClient.swift`) --
     * this method is Kotlin's equivalent for the one post-connect script
     * this class has that targets `other` at all.
     *
     * Unlike [launchBackgroundJob] (whose terminal-admission rejection
     * THROWS -- correct for a public command's own caller-facing
     * rejection), rejection here is SILENT: returns `null` and schedules
     * nothing. The caller here is another client's internal post-connect
     * scheduling code, not a public command awaiting a definite
     * accept/reject answer, so silently dropping mirrors
     * [runIfLive]/[runIfBothLive]'s drop-not-throw precedent for every
     * other peer-driven effect in this class instead.
     *
     * Starts [block] IMMEDIATELY (unlike every [launchBackgroundJob] call
     * site in this class, which defers `Job.start()` until after an outer
     * public command's own [commandInFlight] release -- see that field's
     * round-6 doc comment): every call site of THIS method is reached from
     * deep inside an ALREADY fully-dispatched background coroutine (e.g.
     * [connect]'s own handshake job, itself started long after [connect]'s
     * own [commandInFlight] release), never from within a public command's
     * own guarded span -- so there is no enclosing span left to escape.
     *
     * No [runIfLive]-style re-check is needed inside [block] itself: once
     * admitted into the TARGET's (`other`'s) own [backgroundJobs], that
     * target's own FUTURE [disconnect]/[stop] sweep cancels this exact job
     * directly (`Job.cancel()`) -- identically to how every other
     * self-owned scheduled step in this class needs no re-check either.
     * This differs from a cross-owned effect like message delivery
     * ([send]'s `other.runIfLive(otherGenerationAtSend) { other
     * .deliverEnvelope(...) }`), which still needs an explicit fire-time
     * generation re-check because that job lives in the SENDER's
     * [backgroundJobs], not the receiver's: here, this job's own
     * cancellation IS the target's own lifecycle sweep, not a peer's.
     */
    private fun launchOwnedBackgroundJobIfLive(block: suspend CoroutineScope.() -> Unit): Job? {
        val job = scope.launch(start = CoroutineStart.LAZY, block = block)
        val admitted =
            synchronized(lifecycleLock) {
                if (terminal.get()) {
                    false
                } else {
                    backgroundJobs.add(job)
                    true
                }
            }
        if (!admitted) {
            job.cancel()
            return null
        }
        job.invokeOnCompletion { backgroundJobs.remove(job) }
        job.start()
        return job
    }

    /**
     * ../../../CONTRACT.md section 2's pinned `start()` semantics: idempotent
     * (a repeat call changes nothing and schedules nothing, guarded by
     * [started]); discovery is armed only once BOTH clients of a pair have
     * started AND NEITHER is terminal -- "the moment the second client
     * starts, each client's peerFound is scheduled at its section 3 scenario
     * offset relative to that moment." Round-4 fresh pins, reviewer probe
     * Q1: `start()` on an already-terminal client (its own [disconnect]/
     * [stop] already ran) is rejected outright, as transport misuse, arming
     * nothing on either side -- checked BEFORE the [started] CAS, so a
     * rejected `start()` never flips [started] true either (a subsequent
     * peer `start()` therefore correctly still sees this client as
     * never-started, not merely as started-but-terminal); and the arming
     * condition itself now also requires neither side to be terminal (a
     * stopped/disconnected client can still have `started == true` from
     * before it went terminal). A stopped client cannot be restarted, which
     * now simply falls out of [terminal] being permanently true after
     * [stop] too (see [stopped]'s doc comment).
     *
     * **Any-thread callers (round-4 fresh pin, reviewer probe Q1; narrowed by
     * round-5's "Command ownership" pin):** `start()` may be invoked from any
     * thread -- there is nothing thread-affine about this class -- but
     * round-5 now rejects two calls to `start()` that genuinely OVERLAP on
     * the SAME client instance (see [commandInFlight]). The scenario this
     * paragraph originally documented -- `pair.clientA.start()` racing
     * `pair.clientB.start()` -- is unaffected either way: those are two
     * DIFFERENT instances, each with its own [commandInFlight], so nothing
     * about round-5 changes how they race each other.
     * [discoveryArmed]'s single-CAS-arbiter design (see that field's doc
     * comment) is what makes "exactly one caller ever arms a given pair"
     * hold even when both clients' `start()` calls race each other;
     * [launchBackgroundJob]'s own admission check (reached via
     * [armDiscoveryForBothClients]) separately guards against a `start()`
     * that wins the arming race but then loses a race against its OWN
     * concurrent [disconnect]/[stop] before it finishes scheduling.
     */
    override suspend fun start() {
        // ../../../CONTRACT.md section 2's round-6-pinned "Command ownership
        // (pinned)" bullet -- see [commandInFlight]'s doc comment. Held for
        // this method's ENTIRE body, released in the ONE `finally` below on
        // every path -- [startOwningCommandSpan] admits and registers any
        // discovery jobs it schedules but never starts them; they are
        // started below, strictly AFTER this release, so `Job.start()`'s
        // dispatch can never trap a different concurrent command open on
        // this same instance (round-6 fix -- see [commandInFlight]'s and
        // [launchBackgroundJob]'s doc comments).
        if (!commandInFlight.compareAndSet(false, true)) {
            throw ChatTransportError.concurrentCommand("start()")
        }
        val discoveryJobs: List<Job>
        try {
            discoveryJobs = startOwningCommandSpan()
        } finally {
            commandInFlight.set(false)
        }
        discoveryJobs.forEach { it.start() }
    }

    private suspend fun startOwningCommandSpan(): List<Job> {
        if (terminal.get()) {
            throw ChatTransportError("start() rejected: this client is terminal (disconnect()/stop() already ran)")
        }
        if (!started.compareAndSet(false, true)) return emptyList()

        val other = requirePeer("start()")
        // The 'A'-labeled client's discoveryArmed flag is the single
        // canonical arbiter for this pair (see that field's doc comment):
        // whichever caller's compareAndSet actually flips it false->true is
        // the ONLY one that ever proceeds to armDiscoveryForBothClients,
        // race-free even if both clients' start() calls run concurrently on
        // different threads.
        val arbiter = if (label == 'A') this else other
        return if (other.started.get() && !terminal.get() && !other.terminal.get() &&
            arbiter.discoveryArmed.compareAndSet(false, true)
        ) {
            armDiscoveryForBothClients(other)
        } else {
            emptyList()
        }
    }

    /**
     * Schedules `peerFound` for BOTH clients in the pair, [ChatScenarioTimings
     * .PEER_FOUND_DELAY_MS] from the current virtual time -- called exactly
     * once per pair, by whichever client's [start] call won [discoveryArmed]'s
     * arbiter CAS. `this` is that (second-to-start, or race-winning) client;
     * [other] is the first. Returns both admitted-but-unstarted [Job]s (in
     * A-then-B order) for [start] to `.start()` itself, strictly after its
     * own [commandInFlight] release -- see [launchBackgroundJob]'s round-6
     * doc comment.
     */
    private fun armDiscoveryForBothClients(other: SimulatedChatTransportClient): List<Job> {
        val clientA = if (label == 'A') this else other
        val clientB = if (label == 'A') other else this
        // CyrinxChatKit's Swift `scheduleDiscoveryIfBothStarted()`: "A's
        // peerFound is always scheduled (and so always fires) before B's,
        // regardless of which client's start() happened to trigger this" --
        // every CONTRACT.md section 3 table lists A's peerFound (eventSeq 0)
        // before B's (eventSeq 0) at the same virtual time, and
        // kotlinx-coroutines-test resolves same-due-time tasks in scheduling
        // order, so scheduling (and therefore starting) order here IS firing
        // order.
        val jobA = scheduleDiscoveredPeerFound(target = clientA, discoveredClient = clientB)
        val jobB = scheduleDiscoveredPeerFound(target = clientB, discoveredClient = clientA)
        return listOf(jobA, jobB)
    }

    /**
     * Schedules [target]'s own `peerFound(discoveredClient)` event, gated at
     * FIRE TIME on BOTH [target] and [discoveredClient] still being live (via
     * [runIfBothLive]) -- round-4 fresh pin: "a scheduled `peerFound` is
     * dropped at fire time if either endpoint has become terminal." This
     * covers both directions uniformly, whether [target] is `this` client
     * (a self-owned scheduled action, ALSO torn down by this client's own
     * [disconnect]/[stop] cancelling the job outright via
     * [markTerminalAndRequestCancellation]'s sweep -- [runIfBothLive] is
     * still checked for the OTHER, [discoveredClient]'s, sake) or the peer
     * (../../../CONTRACT.md section 2's "Target ownership" bullet: `target`'s
     * own [disconnect]/[stop] between now and the delay elapsing must
     * silently drop a `peerFound` it already tore down, rather than
     * emitting it anyway). The job itself is always launched on `this`
     * (added to `this.backgroundJobs`), matching every other scheduled
     * effect in this class -- see [launchBackgroundJob].
     *
     * Also records `target`'s own [discoveredPeer] the moment its `peerFound`
     * actually fires (inside the same [runIfBothLive]-guarded critical
     * section as the emission itself) -- ../../../CONTRACT.md section 2's
     * "Discovery precedes connection (pinned)" bullet: this is what later lets
     * `target.connect(idHex)` validate against a peer it has actually
     * observed rather than against [discoveredClient]'s live identity
     * directly. See [discoveredPeer]'s doc comment. Returns the
     * admitted-but-unstarted [Job] -- see [launchBackgroundJob]'s round-6
     * doc comment for why this method no longer starts it itself.
     */
    private fun scheduleDiscoveredPeerFound(
        target: SimulatedChatTransportClient,
        discoveredClient: SimulatedChatTransportClient,
    ): Job {
        val discoveredIdSnapshot = discoveredClient.idBytes
        val targetGenerationAtSchedule = target.currentGeneration()
        val discoveredClientGenerationAtSchedule = discoveredClient.currentGeneration()
        val clientA = if (target.label == 'A') target else discoveredClient
        val clientB = if (target.label == 'A') discoveredClient else target
        val clientAExpectedGeneration =
            if (target.label == 'A') targetGenerationAtSchedule else discoveredClientGenerationAtSchedule
        val clientBExpectedGeneration =
            if (target.label == 'A') discoveredClientGenerationAtSchedule else targetGenerationAtSchedule

        return launchBackgroundJob(caller = "start()") {
            delay(ChatScenarioTimings.PEER_FOUND_DELAY_MS)
            val now = timeSource.nowMs()
            runIfBothLive(clientA, clientAExpectedGeneration, clientB, clientBExpectedGeneration) {
                val chatPeer = ChatPeer(discoveredIdSnapshot, now)
                target.discoveredPeer = chatPeer
                target.emitFromPeer { seq -> ChatEvent.PeerFound(seq, chatPeer) }
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
        // ../../../CONTRACT.md section 2's round-5-pinned "Command ownership
        // (pinned)" bullet -- see [commandInFlight]'s doc comment. Held for
        // this method's ENTIRE body, INCLUDING the suspending `job.join()`
        // wait below (this method's own serialized span is what a concurrent
        // caller must wait its OWN prior command out for -- there is no early
        // "admission complete" point to release at, unlike [connect]/[send]).
        if (!commandInFlight.compareAndSet(false, true)) {
            throw ChatTransportError.concurrentCommand("stop()")
        }
        try {
            stopOwningCommandSpan()
        } finally {
            commandInFlight.set(false)
        }
    }

    private suspend fun stopOwningCommandSpan() {
        // "A repeat stop() is a no-op." (../../../CONTRACT.md section 2).
        if (!stopped.compareAndSet(false, true)) return

        // ../../../CONTRACT.md section 2's round-6-pinned "Cancellation-safe
        // terminal completion" bullet: once this CAS commits (this call is
        // now THE one call that owns finishing stop()), the ENTIRE tail --
        // awaiting admitted work, terminalizing nonterminal sends, the
        // terminal emission, stream completion -- runs inside
        // withContext(NonCancellable), so it completes even if the CALLER of
        // stop() is cancelled while this suspends below (e.g. at `it.join()`
        // waiting on a held background job). Without this, the OLD (broken)
        // behavior was: a cancelled caller propagates a CancellationException
        // out of the cancellable `it.join()` below, unwinding this function
        // with `stopped` already permanently true but NONE of the tail run --
        // pendingSendJobs stays populated forever, no failed(...) status ever
        // reaches the event stream, and the stream never closes, because a
        // REPEAT stop() call immediately early-returns on the same
        // already-true `stopped` flag (see SimulatedChatTransportClientTest's
        // `stopCompletesItsTailEvenWhenTheCallersCoroutineIsCancelledMidJoin`
        // reviewer regression, which reproduces exactly this with a held send
        // and an explicitly-cancelled caller coroutine).
        withContext(NonCancellable) {
            // "Quiescence before completion": mark this client terminal, bump
            // its generation, and request cancellation of every one of its
            // own in-flight background jobs -- all atomically under
            // lifecycleLock (see markTerminalAndRequestCancellation's doc
            // comment) -- then JOIN them (a suspending wait, now
            // non-cancellable) before anything below can reach
            // eventBus.close().
            val jobs = markTerminalAndRequestCancellation()
            jobs.forEach { it.join() }
            failNonterminalOutgoingSends(ChatReasonStrings.STOPPED)
            // "emits connectionChanged(disconnected, reason: "stopped") unless
            // the state is already disconnected" (../../../CONTRACT.md
            // section 2).
            if (connectionState !is ChatConnectionState.Disconnected) {
                emit { seq ->
                    ChatEvent.ConnectionChanged(seq, ChatConnectionState.Disconnected(ChatReasonStrings.STOPPED))
                }
            }
            // "then finishes the event stream" -- must be LAST: every emission
            // above has to actually reach eventBus before it stops accepting
            // new events.
            eventBus.close()
        }
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
     *
     * Returns whether the joint `connected` transition actually fired.
     * ../../../CONTRACT.md section 2's round-5-pinned "Post-connect script
     * admission" bullet: "The section 3 post-connect timeline ... is
     * admitted only by a successful joint `connected` emission. If the
     * handshake is dropped ... the remainder of the scenario script is
     * cancelled on BOTH sides." [connect]'s own caller uses this return
     * value to gate every scenario-scripted step that follows -- see that
     * call site.
     */
    private fun emitConnectedIfBothLive(
        other: SimulatedChatTransportClient,
        selfExpectedGeneration: Long,
        otherExpectedGeneration: Long,
    ): Boolean {
        val clientA = if (label == 'A') this else other
        val clientB = if (label == 'A') other else this
        val clientAExpectedGeneration = if (label == 'A') selfExpectedGeneration else otherExpectedGeneration
        val clientBExpectedGeneration = if (label == 'A') otherExpectedGeneration else selfExpectedGeneration
        return runIfBothLive(clientA, clientAExpectedGeneration, clientB, clientBExpectedGeneration) {
            emit { seq -> ChatEvent.ConnectionChanged(seq, ChatConnectionState.Connected) }
            other.emitFromPeer { seq -> ChatEvent.ConnectionChanged(seq, ChatConnectionState.Connected) }
        }
    }

    override suspend fun connect(peerIdHex: String) {
        // ../../../CONTRACT.md section 2's round-6-pinned "Command ownership
        // (pinned)" bullet: a genuinely concurrent public-command entry on
        // THIS instance is rejected outright, deterministically -- never
        // queued to wait its turn. Checked before every other validation
        // below (including the terminal check), matching "detect concurrent
        // public-command entry ... instead of corrupting state." Released in
        // the ONE `finally` below, covering every path -- see
        // [commandInFlight]'s doc comment for why the handshake job's own
        // `Job.start()` is deliberately called AFTER that release, not
        // before it (round-6 fix: the guard now spans the COMPLETE call).
        if (!commandInFlight.compareAndSet(false, true)) {
            throw ChatTransportError.concurrentCommand("connect()")
        }
        val handshakeJob: Job
        try {
            handshakeJob = connectOwningCommandSpan(peerIdHex)
        } finally {
            commandInFlight.set(false)
        }
        // Strictly outside the guarded span: see [launchBackgroundJob]'s and
        // [commandInFlight]'s round-6 doc comments.
        handshakeJob.start()
    }

    private suspend fun connectOwningCommandSpan(peerIdHex: String): Job {
        // ../../../CONTRACT.md section 2's new pinned "disconnect() is
        // terminal for the client instance" bullet: a terminal client
        // rejects connect() outright, before touching any state -- the only
        // call still permitted on it is stop(). Checked first (the CALLER's
        // own validity), ahead of the discovery validation below.
        if (terminal.get()) {
            throw ChatTransportError("connect() rejected: this client is terminal (disconnect()/stop() already ran)")
        }
        val other = requirePeer("connect()")
        val otherIdHex = other.idBytes.toHexString()
        // ../../../CONTRACT.md section 2's "Discovery precedes connection
        // (pinned)" bullet: "connect(idHex) is valid only for a peer this
        // client has observed via peerFound ... connecting to an unobserved
        // or unknown idHex throws the unknown-peer transport-misuse error and
        // mutates nothing on either side." Validated against THIS client's own
        // [discoveredPeer] -- set only once its own peerFound has actually
        // fired (see that field's doc comment) -- not against `other`'s live
        // identity directly, so a connect() racing ahead of discovery is
        // rejected even though `other` itself is perfectly reachable. Checked
        // before any state mutation/emission below (mirroring the terminal
        // check above), so a rejected connect() mutates nothing on either
        // side.
        val discovered = discoveredPeer
        if (discovered == null || !peerIdHex.equals(discovered.id.toHexString(), ignoreCase = true)) {
            val discoveredDescription = discovered?.id?.toHexString() ?: "<none discovered yet>"
            throw ChatTransportError("connect(toPeer=$peerIdHex) does not match discovered peer $discoveredDescription")
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

        val selfIdHex = idBytes.toHexString()
        // ../../../CONTRACT.md section 2's "Target ownership" bullet, and
        // its round-6-pinned "Post-admission script locality" bullet:
        // otherGenerationAtConnect is captured HERE, at connect() call time
        // (before the handshake delay even starts), and used ONLY by
        // emitConnectedIfBothLive's own joint "both endpoints live" check
        // below -- the sole effect in this method that still needs a
        // fire-time generation re-check against `other`, because it mutates
        // `other` from a job that lives in THIS (A's) own backgroundJobs.
        // peerLoss's own both-targeted post-connect script (the one place
        // this method used to also validate against `other`'s generation at
        // a LATER fire time, deep inside the when(scenario) block below) is
        // scheduled differently: B's half is admitted directly into B's OWN
        // backgroundJobs via other.launchOwnedBackgroundJobIfLive, so B's
        // own future disconnect()/stop() sweep is what invalidates it --
        // see that method's doc comment for why no separate generation
        // re-check is needed there.
        val selfGenerationAtConnect = currentGeneration()
        val otherGenerationAtConnect = other.currentGeneration()

        // t=100 in every scenario table: connecting fires synchronously at
        // the call itself (ChatScenarioTimings.CONNECTING_DELAY_MS == 0) --
        // from [launchBackgroundJob]'s `onAdmitted` callback so it is
        // atomic with THIS client's own terminal-admission check and the
        // handshake job's [backgroundJobs] registration
        // (../../../CONTRACT.md section 2's "Linearized admission and
        // registration," reviewer probe Q2): a genuinely concurrent
        // [disconnect] can never both return having seen an empty
        // [backgroundJobs] registry AND have this `Connecting` event (or
        // anything scheduled below) still fire afterward. The returned job
        // is NOT started here -- see [launchBackgroundJob]'s round-6 doc
        // comment -- [connect] starts it once this whole call has released
        // [commandInFlight].
        return launchBackgroundJob(
            caller = "connect()",
            onAdmitted = { _ ->
                emit { seq -> ChatEvent.ConnectionChanged(seq, ChatConnectionState.Connecting) }
            },
        ) {
            delay(ChatScenarioTimings.CONNECTED_DELAY_MS)
            // "Both endpoints live for connection establishment": fires on
            // BOTH sides, or NEITHER (reviewer probe P4) -- see
            // emitConnectedIfBothLive's doc comment. Its Boolean return is
            // ../../../CONTRACT.md section 2's round-5-pinned "Post-connect
            // script admission" bullet: the scenario script below runs ONLY
            // when the joint `connected` actually fired -- a dropped
            // handshake (either endpoint terminal at fire time) cancels the
            // remainder of the script on BOTH sides, so nothing scripted
            // below may fire when this is false.
            val jointConnectedEmitted = emitConnectedIfBothLive(other, selfGenerationAtConnect, otherGenerationAtConnect)
            if (!jointConnectedEmitted) return@launchBackgroundJob

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
                    val timeoutReason = ChatReasonStrings.PEER_SILENCE_TIMEOUT
                    // ../../../CONTRACT.md section 2's round-6-pinned
                    // "Post-admission script locality" bullet: peerLoss's
                    // silence-timeout duo (section 3.2's disconnected +
                    // peerLost pair, scripted for BOTH clients) is the one
                    // both-targeted post-connect script in this class. B's
                    // half must run as ITS OWN job, admitted into B's OWN
                    // backgroundJobs -- scheduled here, via
                    // launchOwnedBackgroundJobIfLive called ON `other`,
                    // rather than nested inline below inside THIS (A's) own
                    // handshake job. Scheduled immediately, right after the
                    // joint `connected` admission above (no suspension in
                    // between), matching how CyrinxChatKit's Swift twin's
                    // `becomeConnected()` calls `scheduleScenarioPostConnect`
                    // synchronously before returning -- see
                    // launchOwnedBackgroundJobIfLive's doc comment for the
                    // bug this fixes and why no other.runIfLive re-check is
                    // needed inside the new job's own body below.
                    other.launchOwnedBackgroundJobIfLive {
                        delay(ChatScenarioTimings.PEER_LOSS_SILENCE_TIMEOUT_DELAY_MS)
                        other.emitFromPeer { seq -> ChatEvent.ConnectionChanged(seq, ChatConnectionState.Disconnected(timeoutReason)) }
                        other.failNonterminalOutgoingSends(ChatReasonStrings.PEER_LOST)

                        delay(ChatScenarioTimings.PEER_LOSS_PEER_LOST_DELAY_MS)
                        other.emitFromPeer { seq -> ChatEvent.PeerLost(seq, selfIdHex, timeoutReason) }
                    }

                    delay(ChatScenarioTimings.PEER_LOSS_SILENCE_TIMEOUT_DELAY_MS)
                    emit { seq -> ChatEvent.ConnectionChanged(seq, ChatConnectionState.Disconnected(timeoutReason)) }
                    // "every nonterminal outgoing message on that client
                    // transitions to messageStatusChanged(failed,
                    // failureReason: "peerLost") immediately after the
                    // scripted disconnect event" (../../../CONTRACT.md
                    // section 2's "Lifecycle cancellation (pinned)") -- for
                    // THIS client, right after THIS client's own disconnect
                    // event above; B's own nonterminal sends get the same
                    // treatment right after ITS OWN disconnect event, inside
                    // B's own job scheduled above (each client's own
                    // eventSeq stream is independent).
                    failNonterminalOutgoingSends(ChatReasonStrings.PEER_LOST)

                    delay(ChatScenarioTimings.PEER_LOSS_PEER_LOST_DELAY_MS)
                    emit { seq -> ChatEvent.PeerLost(seq, otherIdHex, timeoutReason) }
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
        // ../../../CONTRACT.md section 2's round-5-pinned "Command ownership
        // (pinned)" bullet -- see [commandInFlight]'s doc comment. Held for
        // this method's ENTIRE body, INCLUDING the suspending `job.join()`
        // wait below -- this is deliberately what lets
        // SimulatedChatTransportClientTest's `disconnect...vs...` real-thread
        // regression tests prove a concurrent [cancelSend]/[stop] is rejected
        // while THIS call is genuinely still waiting on a held job.
        if (!commandInFlight.compareAndSet(false, true)) {
            throw ChatTransportError.concurrentCommand("disconnect()")
        }
        try {
            disconnectOwningCommandSpan()
        } finally {
            commandInFlight.set(false)
        }
    }

    private suspend fun disconnectOwningCommandSpan() {
        // "a repeat disconnect() is a no-op." (../../../CONTRACT.md section 2). A
        // disconnect() called after stop() also lands here as a harmless
        // first-and-only flip of this flag: markTerminalAndRequestCancellation()
        // has nothing left to cancel, failNonterminalOutgoingSends() has nothing
        // left to terminalize, and the emit() below is silently dropped by the
        // already-closed eventBus (see emit()'s doc comment) -- so no explicit
        // `stopped` check is needed here for correctness.
        if (!disconnectedByUser.compareAndSet(false, true)) return

        // ../../../CONTRACT.md section 2's round-6-pinned "Cancellation-safe
        // terminal completion" bullet -- see stopOwningCommandSpan's doc
        // comment for the full rationale and the bug this fixes (a cancelled
        // caller previously left `disconnectedByUser` permanently true with
        // the tail below never run: pendingSendJobs never cleared, no
        // failed("disconnected") ever emitted, and a repeat disconnect() call
        // early-returning forever after). Wrapping the ENTIRE tail --
        // "Target ownership"/"Atomic validation" invalidation, awaiting
        // admitted work, terminalizing sends, the terminal emission -- in
        // withContext(NonCancellable) means it always runs to completion once
        // this CAS above has committed, regardless of the CALLER's own
        // cancellation.
        withContext(NonCancellable) {
            // "Target ownership"/"Atomic validation": mark this client
            // terminal and bump its generation FIRST (atomically, under
            // lifecycleLock -- see markTerminalAndRequestCancellation's doc
            // comment), so any peer-driven effect that checks it from here on
            // (including this client's own background jobs we're about to
            // join below) sees this client as terminal.
            // ../../../CONTRACT.md section 2.
            val jobs = markTerminalAndRequestCancellation()
            jobs.forEach { it.join() }
            failNonterminalOutgoingSends(ChatReasonStrings.DISCONNECTED)
            emit { seq ->
                ChatEvent.ConnectionChanged(seq, ChatConnectionState.Disconnected(ChatReasonStrings.USER_INITIATED))
            }
        }
    }

    /** [sendOwningCommandSpan]'s admission result: the drawn `messageIdHex` plus
     * the admitted-but-unstarted [job] backing its scheduled status
     * transitions -- [send] starts [job] itself, strictly after releasing
     * [commandInFlight] (round-6 fix; see that field's doc comment). */
    private class SendAdmission(val messageIdHex: String, val job: Job)

    override suspend fun send(body: String): String {
        // ../../../CONTRACT.md section 2's round-6-pinned "Command ownership
        // (pinned)" bullet: rejected deterministically, before touching any
        // state (including the message-ID stream) -- see [commandInFlight]'s
        // doc comment. "Message-ID draws ... execute inside the command's
        // serialized span" is what this specifically protects: only ONE
        // caller can ever be inside this CAS's guarded span (now the WHOLE
        // function, released in the ONE `finally` below) at a time, so
        // [generateMessageId]'s two draws can never interleave with another
        // concurrent send()'s draws, even under many genuinely concurrent
        // callers (SimulatedChatTransportClientTest's 16-thread/500-send
        // regression).
        if (!commandInFlight.compareAndSet(false, true)) {
            throw ChatTransportError.concurrentCommand("send()")
        }
        val admission: SendAdmission
        try {
            admission = sendOwningCommandSpan(body)
        } finally {
            commandInFlight.set(false)
        }
        // Strictly outside the guarded span: see [launchBackgroundJob]'s and
        // [commandInFlight]'s round-6 doc comments.
        admission.job.start()
        return admission.messageIdHex
    }

    private suspend fun sendOwningCommandSpan(body: String): SendAdmission {
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

        // t=300 in every scenario table that sends: queued fires
        // synchronously at the call itself (QUEUED_DELAY_MS == 0), before
        // send() returns -- "Returns on queue acceptance, NOT delivery"
        // (CONTRACT.md section 1.8) -- from [launchBackgroundJob]'s
        // `onAdmitted` callback, TOGETHER with this message's own
        // `pendingSendJobs` registration, atomically with THIS client's
        // terminal-admission check (../../../CONTRACT.md section 2's
        // "Linearized admission and registration," reviewer probe Q3): a
        // send admitted here is GUARANTEED present in [pendingSendJobs] by
        // the time any subsequent [disconnect]/[stop] sweep runs
        // ([failNonterminalOutgoingSends]), so this message is always
        // either terminalized `failed("disconnected"/"stopped")` by that
        // sweep, or -- if the terminal check below instead loses the race
        // and throws -- rejected outright with no `Queued` event at all; it
        // can never end up silently orphaned in [pendingSendJobs] with no
        // further status transition.
        val job = launchBackgroundJob(
            caller = "send()",
            onAdmitted = { job ->
                pendingSendJobs[messageIdHex] = job
                emitStatus(ChatMessageDisplayStatus.Queued)
            },
        ) {
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
        // Test-only suspension point -- see [testOnlyAfterQueuedEmissionLatch]'s
        // doc comment. `null` (every non-test caller) makes this an
        // immediate no-op with no actual suspension. Still fully inside
        // [send]'s own commandInFlight-guarded span (this function's caller,
        // [send], has not released it yet).
        testOnlyAfterQueuedEmissionLatch?.invoke(messageIdHex)
        return SendAdmission(messageIdHex, job)
    }

    override suspend fun cancelSend(messageIdHex: String) {
        // ../../../CONTRACT.md section 2's round-5-pinned "Command ownership
        // (pinned)" bullet -- see [commandInFlight]'s doc comment. Held for
        // this method's ENTIRE body: it is already fully synchronous (no
        // `Job.start()`/`Job.join()` inside it), so "whole function" and
        // "this command's own synchronous mutation section" coincide exactly
        // -- there is no early-release point to carve out.
        if (!commandInFlight.compareAndSet(false, true)) {
            throw ChatTransportError.concurrentCommand("cancelSend()")
        }
        try {
            cancelSendOwningCommandSpan(messageIdHex)
        } finally {
            commandInFlight.set(false)
        }
    }

    private fun cancelSendOwningCommandSpan(messageIdHex: String) {
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
