@file:OptIn(ExperimentalCoroutinesApi::class)

package com.dweekly.cyrinx.chat

import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * [ChatEventBus]'s bounded-buffer, drop-oldest, `eventSeq`-gap-detectable policy,
 * pinned in ../../../CONTRACT.md section 1.7. Tested in isolation from
 * [SimulatedChatTransportClient] / scenario / codec machinery, per this repo's
 * "write small test programs to validate a hypothesis" convention.
 */
class ChatEventBusTest {
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
     * Drives the "producer emits faster than a slow-but-actively-subscribed
     * consumer can drain" scenario CONTRACT.md section 1.7 describes: a
     * collector must already be registered (suspended awaiting its next value)
     * before the flood of `emit()` calls, matching how `MutableSharedFlow`'s
     * `extraBufferCapacity` actually behaves (`replay = 0` means the buffer
     * only smooths over an ALREADY-active-but-lagging subscriber, not a future
     * one -- a collector that starts only after every emission has already
     * happened sees nothing, since nothing is replayed to new subscribers).
     * [ChatEventBus.emit] assigns `eventSeq` first regardless, which is what
     * makes the drop show up as an `eventSeq` gap rather than a silent loss.
     */
    private fun TestScope.collectWhileFlooding(bus: ChatEventBus, totalEmitted: Int): List<ChatEvent> {
        val collected = mutableListOf<ChatEvent>()
        val job = launch { bus.events.collect { collected.add(it) } }
        runCurrent() // let the collector reach its first suspension point, registering it as a subscriber
        repeat(totalEmitted) { bus.emit { seq -> peerLostEvent(seq) } }
        runCurrent() // let the now-registered collector drain whatever the drop-oldest buffer retained
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
}
