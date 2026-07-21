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
 * returning so the collector reaches its first suspension point before the
 * caller does anything else. `ChatEventBus` is backed by a `Channel` (see
 * ChatEventBus.kt), whose buffer is producer-owned and persists regardless of
 * whether a consumer is attached -- unlike the `MutableSharedFlow(replay = 0)`
 * this class used before, a collector that starts only after events have
 * already been emitted still sees everything still held in the buffer. This
 * early `runCurrent()` is therefore no longer strictly required for
 * correctness, but is kept for determinism: it guarantees every test's
 * collector is registered and pumping before the scenario driver proceeds,
 * rather than depending on `advanceUntilIdle()`/`runCurrent()` calls later in
 * the test to eventually catch it up.
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
 * while writing this suite (see the C3-28 spec-stage report's findings): the
 * event bus's producer-side offer call (originally `MutableSharedFlow.tryEmit`,
 * now `Channel.trySend` -- see ChatEventBus.kt) that wakes a suspended collector
 * can leave that collector's actual resumption pending for one more scheduler
 * pump, which an immediately following `advanceUntilIdle()` does not always
 * perform. The root cause inside `kotlinx-coroutines-test`'s scheduler was not
 * tracked down further; empirically, one extra [runCurrent] after
 * [advanceUntilIdle] consistently surfaces the missing final event in every case
 * this was reproduced. Any test that inspects a list built by [collectEvents]
 * after driving time forward should call this instead of a bare
 * `advanceUntilIdle()`. (ChatScenarioExactTraceTest and
 * ChatTraceGoldenComparisonTest capture events synchronously via
 * [ChatTraceRecorder] instead of a `Flow` collector, so they are not subject to
 * this and use this helper only for consistency.)
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

/** The big-endian u64 reading of the ASCII bytes `MSGIDA__` (`role == 'A'`) or
 * `MSGIDB__` (`role == 'B'`), computed byte-by-byte from the literal ASCII tag
 * rather than a copy-pasted hex constant, so this helper cannot silently drift
 * from ../../../CONTRACT.md section 2's "Message-ID stream (pinned)" wording
 * even if [SimulatedChatPair]'s own `MESSAGE_ID_ROLE_TAG_A`/`_B` constants ever
 * did. */
private fun messageIdRoleTag(role: Char): Long {
    val tag = if (role == 'A') "MSGIDA__" else "MSGIDB__"
    var v = 0L
    for (ch in tag) {
        v = (v shl 8) or (ch.code.toLong() and 0xFF)
    }
    return v
}

/** Independently recomputes the message ID a client with the given `role`
 * (`'A'` or `'B'`) produces for its FIRST `send()` call under `seed`, per
 * ../../../CONTRACT.md section 2's "Message-ID stream (pinned)": a fresh
 * [SplitMix64] seeded with `seed XOR roleTag`, two consecutive draws,
 * big-endian-concatenated. Used by scenario tests to check the message-ID
 * bytes themselves without depending on [SimulatedChatPair]'s internals. */
fun expectedFirstMessageId(seed: Long, role: Char): String {
    val prng = SplitMix64(seed xor messageIdRoleTag(role))
    return (prng.next().toBigEndianBytes() + prng.next().toBigEndianBytes()).toHexString()
}
