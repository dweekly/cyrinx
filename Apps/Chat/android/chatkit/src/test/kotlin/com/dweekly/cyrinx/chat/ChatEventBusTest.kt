@file:OptIn(ExperimentalCoroutinesApi::class)

package com.dweekly.cyrinx.chat

import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.Timeout
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread

/**
 * [ChatEventBus]'s bounded-buffer, drop-oldest, `eventSeq`-gap-detectable policy,
 * pinned in ../../../CONTRACT.md section 1.7. Tested in isolation from
 * [SimulatedChatTransportClient] / scenario / codec machinery, per this repo's
 * "write small test programs to validate a hypothesis" convention.
 */
class ChatEventBusTest {
    @get:Rule
    val testTimeout: Timeout = Timeout.seconds(30)

    private fun peerLostEvent(seq: Long): ChatEvent = ChatEvent.PeerLost(seq, "deadbeef", "test")

    @Test
    fun eventSeqIsMonotonicFromZero() = runTest {
        val bus = ChatEventBus()
        val collected = mutableListOf<ChatEvent>()
        val job = launch { bus.events.collect { collected.add(it) } }
        runCurrent()

        repeat(5) { bus.emit { seq -> peerLostEvent(seq) } }
        runCurrent()
        job.cancel()

        assertEquals(listOf(0L, 1L, 2L, 3L, 4L), collected.map { it.eventSeq })
    }

    @Test
    fun underCapacityNoEventsAreDropped() = runTest {
        val bus = ChatEventBus()
        val collected = mutableListOf<ChatEvent>()
        val job = launch { bus.events.collect { collected.add(it) } }
        runCurrent()

        repeat(ChatEventBus.EVENT_BUFFER_CAPACITY) { bus.emit { seq -> peerLostEvent(seq) } }
        runCurrent()
        job.cancel()

        assertEquals(ChatEventBus.EVENT_BUFFER_CAPACITY, collected.size)
        assertEquals((0 until ChatEventBus.EVENT_BUFFER_CAPACITY).map { it.toLong() }, collected.map { it.eventSeq })
    }

    /**
     * Drives the "producer emits faster than a consumer can drain" scenario
     * CONTRACT.md section 1.7 describes, floods BEFORE attaching any collector.
     * [ChatEventBus] is backed by a `Channel` (see ChatEventBus.kt), whose
     * buffer is producer-owned and persists independent of whether a consumer
     * is attached -- unlike the `MutableSharedFlow(replay = 0)` this class used
     * before, so this is no longer required for correctness, but it IS required
     * to get a deterministic, contract-faithful outcome from this specific
     * test: a `Channel` hands a value directly to an ALREADY-suspended receiver
     * rather than routing it through the buffer at all (a receiver that isn't
     * backlogged isn't a "drop" scenario in the first place), so flooding while
     * a collector is already parked in `receive()` makes exactly one event (the
     * very first) bypass the drop-oldest buffer entirely -- an artifact of
     * there being an idle receiver at flood-start, not a violation of the
     * bounded-buffer contract itself (a real consumer that is actually keeping
     * up, which is what "a receiver is already waiting" means, was never going
     * to have anything dropped on it). Flooding with zero attached collectors
     * removes that fast path and leaves only the buffer's own capacity/
     * drop-oldest policy to determine the outcome, which is what this test
     * actually means to pin. [ChatEventBus.emit] assigns `eventSeq` first
     * regardless, which is what makes the drop show up as an `eventSeq` gap
     * rather than a silent loss.
     */
    private fun TestScope.collectWhileFlooding(bus: ChatEventBus, totalEmitted: Int): List<ChatEvent> {
        repeat(totalEmitted) { bus.emit { seq -> peerLostEvent(seq) } }
        val collected = mutableListOf<ChatEvent>()
        val job = launch { bus.events.collect { collected.add(it) } }
        runCurrent() // drains everything still held in the buffer
        job.cancel()
        return collected
    }

    @Test
    fun overCapacityDropsOldestNotNewest() = runTest {
        val bus = ChatEventBus()
        val totalEmitted = ChatEventBus.EVENT_BUFFER_CAPACITY + 100
        val collected = collectWhileFlooding(bus, totalEmitted)

        assertEquals(
            "drop-oldest must retain exactly the buffer capacity's worth of the newest events",
            ChatEventBus.EVENT_BUFFER_CAPACITY,
            collected.size,
        )
        // The oldest 100 (eventSeq 0..99) were dropped; the newest 512
        // (eventSeq 100..611) survive, in original relative order, each with its
        // true originally assigned eventSeq (CONTRACT.md section 1.7: "dropping
        // never renumbers or reorders survivors").
        val expectedSurvivingSeqs = (100 until totalEmitted).map { it.toLong() }
        assertEquals(expectedSurvivingSeqs, collected.map { it.eventSeq })
    }

    @Test
    fun gapComputationDetectsExactDropCount() = runTest {
        val bus = ChatEventBus()
        val totalEmitted = ChatEventBus.EVENT_BUFFER_CAPACITY + 100
        val collected = collectWhileFlooding(bus, totalEmitted)

        // CONTRACT.md section 1.7: gap = currentEventSeq - previousEventSeq - 1.
        // The consumer's own last-seen eventSeq before this batch was -1 (none
        // observed yet); the first event it actually sees is eventSeq 100, so it
        // can compute that exactly 100 events were dropped without ever having
        // seen any of them.
        val firstSeenSeq = collected.first().eventSeq
        val gapBeforeFirstSeen = firstSeenSeq - (-1L) - 1
        assertEquals(100L, gapBeforeFirstSeen)

        // No gaps among the surviving, contiguously delivered events themselves.
        for (i in 1 until collected.size) {
            val gap = collected[i].eventSeq - collected[i - 1].eventSeq - 1
            assertEquals(0L, gap)
        }
    }

    @Test
    fun emitNeverSuspendsOrBlocksEvenFarOverCapacity() = runTest {
        val bus = ChatEventBus()
        // Emitting 10x capacity with no collector must complete synchronously and
        // promptly -- "never by blocking the producer indefinitely"
        // (CONTRACT.md section 1.7). runTest's own timeout would fail this test
        // if emit() ever suspended.
        repeat(ChatEventBus.EVENT_BUFFER_CAPACITY * 10) { bus.emit { seq -> peerLostEvent(seq) } }
        assertTrue(true)
    }

    // -- emit()/close() race (../../../CONTRACT.md section 2's "Quiescence
    // before completion" bullet) ---------------------------------------------

    /**
     * Reviewer-reproduced regression: [ChatEventBus.emit]'s "check closed, build
     * the event, trySend" sequence used to run OUTSIDE any lock, so a concurrent
     * [ChatEventBus.close] could close the underlying channel in the gap between
     * the closed-check and the `trySend`, making the (then-unguarded)
     * `check(accepted)` throw. Reproduced deterministically here exactly the way
     * the PR review did: block INSIDE the `build` lambda on a real background
     * thread while a second real thread races to call `close()`.
     *
     * Deliberately NOT a `runTest`/virtual-time test -- `TestCoroutineScheduler`
     * is single-threaded and cooperative, so it cannot exhibit this race at all;
     * this needs two genuinely concurrent real threads, hence plain
     * `java.lang.Thread` + `CountDownLatch` rather than coroutines. No
     * wall-clock sleep anywhere: `CountDownLatch.await` blocks on a real signal,
     * never a guessed duration. `closer` is not started until `buildEntered`
     * fires, which -- given `emit()`'s lock now wraps `build` itself (see
     * [ChatEventBus]'s `lock` doc comment) -- means the producer thread is
     * PROVABLY already holding the lock by the time `close()` even attempts to
     * acquire it, so this test's outcome (no exception, the in-flight emission
     * lands) is deterministic given the fix, not merely "usually passes."
     */
    @Test
    fun emitAndCloseCannotRaceEvenWhenBuildBlocks() {
        val bus = ChatEventBus()
        val buildEntered = CountDownLatch(1)
        val releaseBuild = CountDownLatch(1)
        var thrown: Throwable? = null
        // Fail-closed: a hit deadline on this latch must FAIL the test, not
        // silently let `build` fall through as if it had been released --
        // captured separately from `thrown` so a timeout here is reported as
        // a stalled test harness, not misattributed to emit() itself.
        var releaseBuildAwaitTimedOut = false

        val producer =
            thread(start = false) {
                try {
                    bus.emit { seq ->
                        buildEntered.countDown()
                        if (!releaseBuild.await(10, TimeUnit.SECONDS)) {
                            releaseBuildAwaitTimedOut = true
                        }
                        peerLostEvent(seq)
                    }
                } catch (t: Throwable) {
                    thrown = t
                }
            }
        producer.start()

        assertTrue(
            "producer must reach the blocking build() before we race close() against it",
            buildEntered.await(10, TimeUnit.SECONDS),
        )

        // Started only after the producer is provably inside emit()'s locked
        // section (blocked in `build`) -- close() must therefore block on the
        // same lock rather than racing ahead of emit()'s closed-check.
        val closer = thread(start = true) { bus.close() }

        releaseBuild.countDown()

        producer.join(10_000)
        closer.join(10_000)
        assertFalse("producer thread did not finish within 10s", producer.isAlive)
        assertFalse("closer thread did not finish within 10s", closer.isAlive)

        assertFalse(
            "releaseBuild latch must be signaled well within 10s -- a hit deadline here means the " +
                "test harness itself stalled, not a finding about emit()",
            releaseBuildAwaitTimedOut,
        )
        assertNull("emit() must never throw racing a concurrent close()", thrown)

        val collected = mutableListOf<ChatEvent>()
        runBlocking { bus.events.collect { collected.add(it) } }
        assertEquals(
            "the emission already in flight when close() raced it must still land, not be lost to the race",
            listOf(0L),
            collected.map { it.eventSeq },
        )
    }
}
