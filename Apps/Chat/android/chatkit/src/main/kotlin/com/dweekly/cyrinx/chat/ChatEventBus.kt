package com.dweekly.cyrinx.chat

import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.receiveAsFlow
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong

/**
 * The per-client-instance event stream backing [ChatTransportClient.events]:
 * bounded buffer of [EVENT_BUFFER_CAPACITY] events, drop-oldest, with `eventSeq`
 * assigned monotonically from 0 before an event is offered to the buffer so a slow
 * consumer can detect drops as `eventSeq` gaps even though it never observed the
 * dropped events themselves. ../../../CONTRACT.md section 1.7's
 * "`eventSeq` gap-detection contract and bounded-buffer policy", plus section 2's
 * "Lifecycle cancellation (pinned)" requirement that `stop()` "finishes the event
 * stream" and that "no event of any kind may be observed after the stream
 * finishes."
 *
 * Backed by a [Channel] (capacity [EVENT_BUFFER_CAPACITY], [BufferOverflow
 * .DROP_OLDEST]) rather than a `MutableSharedFlow`: a `Channel`'s buffer is
 * producer-owned and persists regardless of whether a consumer is currently
 * attached (matching Swift's `AsyncStream(bufferingPolicy: .bufferingNewest(512))`,
 * which has the same "buffer exists independent of an active iterator" property),
 * and -- the property this class actually needs -- a `Channel` can be [close]d,
 * which lets already-buffered events still drain to a collector before the
 * resulting `Flow` completes on its own, with no consumer-side polling or
 * cancellation required. `MutableSharedFlow` has no equivalent completion signal.
 *
 * Kept as its own small class (rather than inlined into
 * [SimulatedChatTransportClient]) so the buffer/drop/gap-detection/completion
 * behavior itself is unit-testable without any scenario, PRNG, or codec machinery
 * -- see ChatEventBusTest.
 */
class ChatEventBus {
    private val nextEventSeq = AtomicLong(0)

    /** Guards [close] against double-closing the underlying [channel] (calling
     * `close()` on an already-closed `Channel` is itself harmless/idempotent, but
     * this flag is also consulted by [emit] to skip building and offering a new
     * event at all once closed -- see [emit]'s doc comment). Only ever read/written
     * while holding [lock]; see that field's doc comment for why the flag alone
     * (without the lock) was not enough. */
    private val closed = AtomicBoolean(false)

    /**
     * Serializes [emit]'s entire "check closed, build the event, trySend" sequence
     * against [close] -- ../../../CONTRACT.md section 2's "Quiescence before
     * completion" bullet: "every emission either lands before the completion or
     * its producer was already cancelled and joined ... Losing an emission to a
     * close race is a contract violation, not tolerated backpressure."
     *
     * Before this lock existed, [emit] read [closed], then called the caller-
     * supplied `build` lambda, then called `channel.trySend` -- three separate
     * steps with no atomicity between them. A concurrent [close] could run
     * entirely in the gap between the `closed` read and the `trySend` call: it
     * would see `closed` still false (racing ahead of [emit]'s check), close the
     * underlying [channel], and then [emit]'s own `trySend` -- now against an
     * already-closed channel -- would fail, making the unguarded
     * `check(accepted)` throw `IllegalStateException`. A C3-28 PR review
     * reproduced this deterministically by blocking INSIDE the `build` lambda on
     * a real background thread while a second thread raced to call [close] (see
     * ChatEventBusTest's `emitAndCloseCannotRaceEvenWhenBuildBlocks`).
     *
     * Wrapping [emit]'s full check-build-send sequence AND [close] in this one
     * `synchronized` block eliminates the race entirely rather than merely
     * avoiding the crash: at most one of {an [emit] call, a [close] call} is
     * ever inside its own critical section at a time, so whichever one actually
     * acquires the monitor first runs to completion before the other can even
     * begin -- there is no longer any window in which [close] can observe
     * `closed == false` while an [emit] call is itself past that same check.
     * `synchronized` (a plain JVM intrinsic lock), not a suspend-friendly
     * `Mutex`, because both [emit] and [close] are ordinary (non-`suspend`)
     * functions, and the caller-supplied `build` lambda they wrap is expected to
     * be synchronous, non-blocking event construction in every real call site in
     * this module (see [SimulatedChatTransportClient.emit]) -- only the
     * regression test above deliberately blocks inside it, and only to prove
     * this lock actually serializes against [close] under that exact adversarial
     * condition. The JVM intrinsic lock is reentrant, so a `build` lambda that
     * (hypothetically) re-entered [emit] on the SAME thread would not deadlock
     * against itself, though no call site here does this.
     */
    private val lock = Any()

    // capacity=EVENT_BUFFER_CAPACITY + onBufferOverflow=DROP_OLDEST is the literal
    // Kotlin realization CONTRACT.md section 1.7 names for the bounded,
    // drop-oldest, never-blocks-the-producer policy (mirroring Swift's
    // `AsyncStream(bufferingPolicy: .bufferingNewest(512))`).
    private val channel =
        Channel<ChatEvent>(
            capacity = EVENT_BUFFER_CAPACITY,
            onBufferOverflow = BufferOverflow.DROP_OLDEST,
        )

    val events: Flow<ChatEvent> = channel.receiveAsFlow()

    /**
     * Assigns the next `eventSeq` and offers `build(eventSeq)`'s result to the
     * bounded buffer. Never suspends and never fails: under `DROP_OLDEST`,
     * `trySend` always succeeds (it makes room by discarding the oldest buffered
     * event rather than rejecting the new one), which is exactly the "never by
     * blocking the producer indefinitely" requirement in CONTRACT.md section 1.7
     * -- `check(accepted)` below exists purely as a should-never-fire assertion of
     * that guarantee (with [lock] serializing against [close], it genuinely never
     * fires; see [lock]'s doc comment for the race it used to be reachable
     * through).
     *
     * A silent no-op once [close] has been called: `build` is not even invoked, so
     * a caller relying on `emit`'s lambda for a side effect (as
     * [SimulatedChatTransportClient.emit] does, for `connectionState` tracking and
     * trace capture) gets that same "nothing observed after the stream finishes"
     * guarantee for free, regardless of which of this bus's callers race to emit
     * after close.
     */
    fun emit(build: (eventSeq: Long) -> ChatEvent) {
        synchronized(lock) {
            if (closed.get()) return
            val seq = nextEventSeq.getAndIncrement()
            val accepted = channel.trySend(build(seq)).isSuccess
            check(accepted) { "Channel.trySend unexpectedly failed under DROP_OLDEST" }
        }
    }

    /**
     * Completes [events] for every already-attached and future collector, once
     * whatever is already buffered has drained -- CONTRACT.md section 2's
     * "Lifecycle cancellation (pinned)": `stop()` "finishes the event stream ...
     * No event of any kind may be observed after the stream finishes." Idempotent
     * (a repeat call is a no-op, matching `stop()`'s own "a repeat stop() is a
     * no-op"). Serialized against [emit] via [lock] -- see that field's doc
     * comment.
     */
    fun close() {
        synchronized(lock) {
            if (closed.compareAndSet(false, true)) {
                channel.close()
            }
        }
    }

    companion object {
        /** Bounded buffer size, pinned in ../../../CONTRACT.md section 1.7. */
        const val EVENT_BUFFER_CAPACITY: Int = 512
    }
}
