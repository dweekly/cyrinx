@file:OptIn(ExperimentalCoroutinesApi::class)

package com.dweekly.cyrinx.chat.app

import com.dweekly.cyrinx.chat.ChatScenario
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assume
import org.junit.Test
import java.io.File

/**
 * Compares this module's generated model-level trace (the design brief's
 * "Model-trace schema (pinned)" section -- see ChatModelTrace.kt) against the
 * golden files `Apps/Chat/fixtures/model-traces/{happyPair,peerLoss}.jsonl`,
 * generated from the Swift `ChatModel` in C3-29's verify stage. Driven via
 * [generateModelTrace] (ChatViewModelTestSupport.kt), which follows
 * CONTRACT.md section 3's driver rows exactly, at [GOLDEN_TRACE_SEED].
 *
 * Mirrors chatkit's own `ChatTraceGoldenComparisonTest` shape for the OLD
 * wire-level trace, but with the design brief's amended bootstrap rule for
 * this NEW schema (design brief, "Shared model-trace fixtures" note): before
 * the fixtures directory exists at all (pre-stack, C3-29 has not landed yet
 * on this branch), this comparison is SKIPPED ([Assume]) rather than failed;
 * once the directory exists, a missing individual golden file is a hard
 * FAILURE, never a skip -- it is merge-gate evidence at that point, exactly
 * like chatkit's existing (already-populated) `fixtures/traces/` goldens.
 */
class ChatModelTraceGoldenComparisonTest {
    private val goldenModelTracesDir = File("../fixtures/model-traces")

    private fun assumeGoldenDirectoryPresent() {
        Assume.assumeTrue(
            "${goldenModelTracesDir.path} does not exist yet -- C3-29 (Apple) has not generated the " +
                "model-trace goldens on this branch yet; skipped, not failed, per the design brief's " +
                "bootstrap rule (mirrors the C3-28 trace bootstrap). Once this directory exists, a " +
                "missing individual file below is a hard failure, not a skip.",
            goldenModelTracesDir.isDirectory,
        )
    }

    @Test
    fun happyPairModelTraceMatchesGoldenFileWhenPresent() = runTest {
        assumeGoldenDirectoryPresent()
        val goldenFile = File(goldenModelTracesDir, "happyPair.jsonl")
        assertTrue("${goldenFile.path} not found -- treated as a hard failure, not a skip", goldenFile.isFile)

        assertEquals(goldenFile.readText(), generateModelTrace(ChatScenario.HAPPY_PAIR, GOLDEN_TRACE_SEED))
    }

    @Test
    fun peerLossModelTraceMatchesGoldenFileWhenPresent() = runTest {
        assumeGoldenDirectoryPresent()
        val goldenFile = File(goldenModelTracesDir, "peerLoss.jsonl")
        assertTrue("${goldenFile.path} not found -- treated as a hard failure, not a skip", goldenFile.isFile)

        assertEquals(goldenFile.readText(), generateModelTrace(ChatScenario.PEER_LOSS, GOLDEN_TRACE_SEED))
    }

    companion object {
        /** DECISION (not pinned by the brief beyond "at seed 1" -- the design
         * brief's "Model-trace schema" section pins the driver as "scenario
         * happyPair and peerLoss at seed 1," matching CONTRACT.md section 4's
         * own golden-trace seed for the wire-level trace goldens). */
        const val GOLDEN_TRACE_SEED: Long = 1L
    }
}
