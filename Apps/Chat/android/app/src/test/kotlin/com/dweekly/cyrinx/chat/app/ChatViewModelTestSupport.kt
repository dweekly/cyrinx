@file:OptIn(ExperimentalCoroutinesApi::class)

package com.dweekly.cyrinx.chat.app

import com.dweekly.cyrinx.chat.ChatScenario
import com.dweekly.cyrinx.chat.VirtualTimeSource
import com.dweekly.cyrinx.chat.toHexString
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.currentTime
import kotlinx.coroutines.test.runCurrent

/**
 * Shared test scaffolding for [ChatViewModel] tests, mirroring
 * `chatkit`'s own `ChatScenarioTestSupport.kt`
 * (`Apps/Chat/android/chatkit/src/test/.../ChatScenarioTestSupport.kt`) --
 * duplicated locally rather than shared cross-module because chatkit's test
 * source set is not a build artifact this module can depend on (only its
 * `main` source set is, via `implementation(project(":chatkit"))`).
 */

/** [VirtualTimeSource] backed by this [TestScope]'s own virtual clock, so every
 * `delay()` inside [ChatViewModel]'s injected [SimulatedChatPair][com.dweekly.cyrinx.chat.SimulatedChatPair]
 * is driven by `advanceTimeBy`, never a real wall-clock sleep. */
fun TestScope.testVirtualTimeSource(): VirtualTimeSource = VirtualTimeSource { currentTime }

// IMPORTANT for every ChatViewModel test in this module: always construct it
// with `scope = backgroundScope` (this TestScope's own `backgroundScope`
// property from kotlinx-coroutines-test), never `scope = this`.
// ChatViewModel's internal event-collection loop
// (`transportClient.events.collect(::applyEvent)`) runs for the ViewModel's
// entire lifetime -- it never completes on its own, only when its owning
// scope is cancelled. `runTest { ... }`'s own coroutine (`this`) is expected
// to run to COMPLETION by the end of the test block, so launching an
// infinite collector directly on it fails the test with an "uncompleted
// child job" error; `backgroundScope`'s children are auto-cancelled when the
// test ends instead, which is exactly what a ViewModel-owned scope needs
// (mirrors ChatViewModel.onCleared's own `scope.cancel()` in production).
//
// CONSEQUENCE, empirically confirmed against this exact module (and matching
// kotlinx-coroutines-test's own documented `TestScope.backgroundScope`
// contract, https://kotlinlang.org/api/kotlinx.coroutines/kotlinx-coroutines-test/kotlinx.coroutines.test/-test-scope/background-scope.html :
// "advanceUntilIdle... will stop advancing the virtual time once only the
// coroutines in this scope are left unprocessed"): once a ChatViewModel
// built this way has consumed its immediate foreground work, EVERYTHING it
// still has scheduled (SimulatedChatPair's own delay()-based scenario
// timeline, this ViewModel's own connectToPeer/sendMessage launches) lives
// ENTIRELY in backgroundScope -- so `advanceUntilIdle()` stops immediately
// rather than draining it, unlike chatkit's own tests (which pass `this`,
// the foreground TestScope, directly to SimulatedChatPair.create and so have
// no such background-only tail). Every test in this module therefore drives
// time with EXPLICIT, bounded `advanceTimeBy(n)` calls sized to each pinned
// scenario's own CONTRACT.md section 3 table (never `advanceUntilIdle()`),
// which is also a better fit for AGENTS.md's "no wall-clock sleeps; bounded
// waits asserted fail-closed" convention regardless of this specific
// scheduler nuance: the exact duration each test advances by is auditable
// against the table it corresponds to, not an open-ended "until nothing is
// left."

/**
 * Drives [scenario] at [seed] through a freshly-constructed [ChatViewModel]
 * exactly per CONTRACT.md section 3's driver rows (connect at t=100, then --
 * for `happyPair` only -- send at t=300), attached to a fresh
 * [ChatModelTraceRecorder], and returns the recorded model-level trace
 * (design brief's "Model-trace schema (pinned)"). Shared by
 * ChatModelTraceGoldenComparisonTest and ChatModelTraceDeterminismTest so
 * both exercise the exact same driver.
 *
 * Advances a further 300ms after the send/no-send branch (see this file's
 * `backgroundScope` note above for why this is a bounded `advanceTimeBy`,
 * not `advanceUntilIdle()`) -- generous enough to reach EITHER pinned
 * scenario's own final event: `peerLoss` ends at absolute t=510
 * (CONTRACT.md section 3.2) with no `send()` call at all (still at t=300
 * going into this final advance), and `happyPair` ends at t=400 (section
 * 3.1) from a `send()` call also made at t=300 -- 300ms covers both with
 * margin to spare.
 */
suspend fun TestScope.generateModelTrace(scenario: ChatScenario, seed: Long): String {
    val recorder = ChatModelTraceRecorder()
    val viewModel =
        ChatViewModel(
            config = ChatLaunchConfig(scenario, seed, simulated = true),
            timeSource = testVirtualTimeSource(),
            scope = backgroundScope,
            modelTraceRecorder = recorder,
        )
    advanceTimeBy(100)
    runCurrent()
    val peerIdHex = viewModel.uiState.value.peers.single().id.toHexString()
    viewModel.connectToPeer(peerIdHex)
    runCurrent()
    advanceTimeBy(200)
    runCurrent()
    if (scenario == ChatScenario.HAPPY_PAIR) {
        viewModel.sendMessage("hello")
    }
    advanceTimeBy(300)
    runCurrent()
    return recorder.toJsonLines()
}
