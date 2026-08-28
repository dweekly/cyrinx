package com.dweekly.cyrinx.chat

/**
 * Reads the simulator's current virtual time in milliseconds.
 *
 * DECISION (not pinned by the brief): ../../../CONTRACT.md section 2 point 3 says
 * the Kotlin realization uses "the coroutine test dispatcher's virtual time
 * (kotlinx-coroutines-test's `TestCoroutineScheduler`, driven via
 * `runTest { ... advanceTimeBy(...) ... }`)". `kotlinx-coroutines-test` is,
 * correctly, a test-only dependency of this module (see chatkit/build.gradle.kts)
 * and so is not visible from this main source set. [VirtualTimeSource] is the seam
 * that lets [SimulatedChatTransportClient] read "now" without depending on that
 * test-only artifact: production code supplies whatever `nowMs()` is appropriate,
 * and tests supply a lambda backed by `kotlinx-coroutines-test`'s `TestScope
 * .currentTime` (see ChatScenarioTest's `virtualTime()` helper). This mirrors
 * Swift's explicitly injected `VirtualClock` (CONTRACT.md section 2 point 3) while
 * keeping the Kotlin main module free of test-framework dependencies.
 *
 * [SimulatedChatTransportClient] itself never sleeps on wall-clock time and never
 * calls [nowMs] to decide *whether* to schedule something -- every scheduled delay
 * is computed as a fixed offset per ../../../CONTRACT.md section 3's tables
 * (see [ChatScenarioTimings]); [nowMs] is read only to stamp each emitted event's
 * `virtualTimeMs` (and `ChatPeer.discoveredAtMs` / `ChatMessage
 * .sentAtWallClockMs`) with the authoritative current time after a `delay()` call
 * resolves, rather than trusting hand-computed arithmetic.
 */
fun interface VirtualTimeSource {
    fun nowMs(): Long
}
