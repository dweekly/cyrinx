# ADR 0002: Executor, thread, effect, and queue ownership

- **Status:** Accepted for Cyrinx 3.0
- **Date:** 2026-07-23
- **Plan:** C3-01

## Context

Audio callbacks have real-time constraints, while synchronization, decoding,
retry policy, event delivery, and application work can allocate or suspend.
“Thread-safe” is too weak a contract: it does not state which operations may
run concurrently, whether a resource is thread-affine, or what happens when a
bounded queue fills.

Swift actors and serialized Kotlin coroutine contexts provide mutual exclusion,
but neither promises a fixed operating-system thread. Audio APIs may separately
require thread affinity. Cyrinx must distinguish serialized mutation from
thread-affine device ownership.

## Decision

Every C session has one serial reducer executor. All commands, effect results,
timer firings, DSP-result commits, snapshot reads, and terminal transitions for
that session execute as ordered reducer turns. The C session API is not
concurrently callable unless a later declaration explicitly says otherwise.

The executor may use different operating-system threads over its lifetime. A
component that requires a fixed thread owns a dedicated platform executor and
communicates with the reducer through bounded queues and epoch-tagged effects.

| Execution context | Owns | May not do |
|---|---|---|
| Audio input callback | Its audio unit and one PCM-queue producer endpoint | Block, allocate in steady state, log, run protocol state, or wait for the reducer |
| Audio output callback | Its audio unit and one PCM-queue consumer endpoint | Block, allocate in steady state, synthesize policy, or call application code |
| Session reducer executor | All canonical session and protocol mutation | Run concurrently for the same session or wait on an audio callback |
| Optional DSP worker | A preallocated job workspace and immutable job input | Mutate session state or publish an event directly |
| Swift facade actor | Swift handle registry, continuations, and subscribers | Treat its cache as canonical after a generation gap |
| Kotlin serialized scope | Kotlin handle registry, continuations, and flows | Depend on a particular OS thread unless it owns a thread-affine dispatcher |
| Application executor | Application and UI state | Enter the C reducer reentrantly from a callback |

### Effect ordering

The reducer assigns an epoch and unique effect token to each requested effect.
A worker may compute concurrently, but its result is
committed only on the reducer executor and only if:

1. its epoch is still the observed run epoch and its token remains valid;
2. the owning transport still exists and, for a child-scoped effect, its
   owning connection or transfer still exists;
3. the result is the next admissible result for that state; and
4. cancellation or route discontinuity has not invalidated the job.

Independent pure DSP jobs may finish out of order. Their state effects may not
be committed out of order. A stale result is discarded and recorded in
diagnostics without emitting a false state transition.

Timers and clocks are injected effects. A timer callback enqueues a result; it
does not mutate the reducer directly. Stopping a transport invalidates
ordinary-work tokens before device teardown begins, retains only the teardown
tokens, and invalidates those at the final `stopped` commit. The public epoch
remains available to order that commit.

### Audio callback contract

The steady-state callback path uses preallocated, bounded storage. It performs
only bounded copies, index updates, and nonblocking queue operations. Platform
adapters must document any operation they claim is real-time safe.

Heavy DSP, FFT work, memory growth, string formatting, file I/O, system logging,
mutex waits, condition variables, semaphores, async suspension, and application
callbacks are outside the audio callback.

Arbitrary hardware callback lengths are accepted by stateful streaming
contexts. Bounded internal synchronization history is permitted and required;
the callback itself does not accumulate an unbounded capture.

### Queue and overflow contract

Every queue declares a capacity, producer, consumer, unit, and deterministic
overflow policy in the implementation PR that introduces it. No queue silently
becomes unbounded.

| Resource | Required full/empty behavior |
|---|---|
| PCM ingress full | Drop a declared whole block or suffix, advance the monotonic sample position, and enqueue a discontinuity marker; never block the callback |
| PCM egress empty | Render declared silence, advance the sample position, and record an underrun; never reuse stale samples |
| Command or outbound-message queue full | Reject before `accepted` with structured `queueFull`; do not create a transfer that appears accepted |
| Event queue full | Coalesce or drop nonterminal deltas, retain an overflow/gap indication, and require snapshot recovery; never drop the authoritative inbound-mailbox payload; never block the reducer |
| Reassembly or transfer storage exhausted | Reject before ownership transfer or terminate the affected transfer with explicit partial/failure evidence |
| Diagnostic ring full | Overwrite the oldest complete record and retain a dropped-record count |

Terminal state remains recoverable from a snapshot for every valid core
observation cursor even when its corresponding delta event could not be
retained. Cursor leases are bounded; the core reports explicit observation
loss before it invalidates a lagging cursor and evicts history. The generation,
snapshot, and cursor rules are in the
[semantic contract](../CYRINX_3_SEMANTIC_CONTRACT.md).

### Reentrancy and cancellation

Reducer callbacks do not invoke application code synchronously. Facades publish
events after the C call returns. Cancellation is submitted as another ordered
command; it does not race a transfer by mutating shared fields from the caller.
Once a terminal transfer outcome is committed, later cancellation is an
idempotent no-op.

## Consequences

- Serialization is portable across Swift actors, Kotlin coroutines, and C
  event loops without making a false same-thread promise.
- Audio glitches are observable as discontinuities or underruns rather than
  concealed by blocking or stale samples.
- Concurrent DSP remains possible while canonical mutation stays ordered.
- Queue capacities remain implementation- and profile-specific, but their
  ownership and failure semantics are fixed now.

## Rejected alternatives

- **A global mutex around all audio and session work:** rejected because a
  callback could block behind non-real-time work.
- **A “single-threaded coroutine” as the entire contract:** rejected because
  serialization and OS-thread affinity are different guarantees.
- **Drop events without a generation gap:** rejected because a facade could
  present a state that never existed canonically.
