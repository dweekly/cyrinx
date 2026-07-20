@file:OptIn(ExperimentalCoroutinesApi::class)

package com.dweekly.cyrinx.chat

import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.currentTime
import kotlinx.coroutines.test.runCurrent

/**
 * Shared test scaffolding for exercising [SimulatedChatPair] under
 * `kotlinx-coroutines-test`'s virtual time. `TestScope.currentTime` (an
 * extension property from `kotlinx-coroutines-test`) is exactly the
 * [VirtualTimeSource] this module's main source set cannot depend on directly --
 * see [VirtualTimeSource]'s doc comment for why that seam exists.
 */

/** Builds a [SimulatedChatPair] whose [VirtualTimeSource] and [kotlinx.coroutines.CoroutineScope]
 * are both backed by this [TestScope], so every scheduled `delay()` inside the
 * pair is driven by `advanceTimeBy`/`advanceUntilIdle`/`runCurrent`, never a real
 * wall-clock sleep. */
fun TestScope.createChatPair(
    scenario: ChatScenario,
    seed: Long,
    recorder: ChatTraceRecorder? = null,
): SimulatedChatPair = SimulatedChatPair.create(scenario, seed, this, VirtualTimeSource { currentTime }, recorder)

/**
 * Starts collecting `client.events` into a list that grows in place as events
 * arrive, using [TestScope.backgroundScope] so the collector is automatically
 * cancelled at the end of the enclosing `runTest` block (an infinite `collect`
 * would otherwise make `runTest` fail as a leaked/un-awaited child job).
 *
 * Calls [kotlinx.coroutines.test.TestCoroutineScheduler.runCurrent] once before
 * returning so the collector reaches its first suspension point -- and is
 * therefore registered as an active `MutableSharedFlow` subscriber -- before the
 * caller does anything else. This matters because `ChatEventBus`'s underlying
 * `MutableSharedFlow` is configured with `replay = 0`
 * (see ChatEventBus.kt): its `extraBufferCapacity` buffer only smooths over an
 * ALREADY-registered-but-lagging subscriber, not a future one, so a collector
 * that only starts running after events have already been emitted would see
 * nothing (see ChatEventBusTest's `collectWhileFlooding` doc comment, where this
 * was first discovered).
 */
fun TestScope.collectEvents(client: SimulatedChatTransportClient): List<ChatEvent> {
    val collected = mutableListOf<ChatEvent>()
    backgroundScope.launch { client.events.collect { collected.add(it) } }
    testScheduler.runCurrent()
    return collected
}

/**
 * `advanceUntilIdle()` alone was observed NOT to reliably guarantee that a
 * `backgroundScope`-launched `Flow` collector (as used by [collectEvents]) has
 * drained the very LAST event emitted during that call -- reproduced directly
 * while writing this suite (see the C3-28 spec-stage report's findings): a
 * `MutableSharedFlow.tryEmit` call that wakes a suspended collector can leave
 * that collector's actual resumption pending for one more scheduler pump, which
 * an immediately following `advanceUntilIdle()` does not always perform. The
 * root cause inside `kotlinx-coroutines-test`'s scheduler was not tracked down
 * further; empirically, one extra [runCurrent] after [advanceUntilIdle]
 * consistently surfaces the missing final event in every case this was
 * reproduced. Any test that inspects a list built by [collectEvents] after
 * driving time forward should call this instead of a bare `advanceUntilIdle()`.
 * (ChatScenarioExactTraceTest and ChatTraceGoldenComparisonTest capture events
 * synchronously via [ChatTraceRecorder] instead of a `Flow` collector, so they
 * are not subject to this and use this helper only for consistency.)
 */
suspend fun TestScope.settle() {
    advanceUntilIdle()
    runCurrent()
}

/** Independently recomputes the (peerAId, peerBId) pair
 * [SimulatedChatPair.create] derives from `seed`, per
 * ../../../CONTRACT.md section 2's "PRNG draw order contract" (draw 1's first 4
 * bytes -> A's id, draw 2's first 4 bytes -> B's id) -- used by scenario tests to
 * build expected events without depending on the pair's own internals. */
fun expectedPeerIds(seed: Long): Pair<ByteArray, ByteArray> {
    val prng = SplitMix64(seed)
    val idA = prng.next().toBigEndianBytes().copyOfRange(0, 4)
    val idB = prng.next().toBigEndianBytes().copyOfRange(0, 4)
    return idA to idB
}
