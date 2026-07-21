@file:OptIn(ExperimentalCoroutinesApi::class)

package com.dweekly.cyrinx.chat

import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Compares this module's generated `happyPair` and `peerLoss` traces
 * byte-for-byte against the golden trace files in ../fixtures/traces/
 * (../../../CONTRACT.md section 3's closing note / ../../../README.md's
 * directory map): "generated from the Swift implementation in the verify stage
 * and checked byte-identical from Kotlin."
 *
 * Pinned by ../../../CONTRACT.md section 4's amended "Golden trace fixtures
 * (pinned)": the committed goldens are `fixtures/traces/happyPair.jsonl` and
 * `fixtures/traces/peerLoss.jsonl`, generated with seed 1 ([GOLDEN_TRACE_SEED])
 * by `CyrinxChatKit`'s `chat-trace-gen` executable and asserted byte-identical
 * here. Both comparisons are merge-gate evidence, so BOTH FAIL -- never skip --
 * when a fixture file is missing (no `org.junit.Assume` anywhere in this class):
 * a missing golden is treated the same as a mismatched one, not as "not
 * applicable yet."
 */
class ChatTraceGoldenComparisonTest {
    private val goldenTracesDir = File("../fixtures/traces")

    /** Not pinned by the brief -- see this class's doc comment. */
    private val goldenTraceSeed = GOLDEN_TRACE_SEED

    private fun requireGoldenTracesDirPresent() {
        assertTrue(
            "${goldenTracesDir.path} does not exist -- golden trace fixtures are merge-gate " +
                "evidence (CONTRACT.md section 4's amended \"Golden trace fixtures (pinned)\"); " +
                "a missing fixture is a hard failure, not a skip",
            goldenTracesDir.isDirectory,
        )
    }

    private suspend fun TestScope.generateTrace(scenario: ChatScenario): String {
        val recorder = ChatTraceRecorder()
        val pair = createChatPair(scenario, goldenTraceSeed, recorder)

        pair.clientA.start()
        pair.clientB.start()
        advanceTimeBy(100)
        runCurrent()
        pair.clientA.connect(expectedPeerIds(goldenTraceSeed).second.toHexString())
        advanceTimeBy(200)
        runCurrent()
        if (scenario == ChatScenario.HAPPY_PAIR) {
            pair.clientA.send("hello")
        }
        settle()

        return recorder.toJsonLines()
    }

    @Test
    fun happyPairTraceMatchesGoldenFile() = runTest {
        requireGoldenTracesDirPresent()
        val goldenFile = File(goldenTracesDir, "happyPair.jsonl")
        assertTrue("${goldenFile.path} not found -- treated as a hard failure, not a skip", goldenFile.isFile)

        val actual = generateTrace(ChatScenario.HAPPY_PAIR)
        assertEquals(goldenFile.readText(), actual)
    }

    @Test
    fun peerLossTraceMatchesGoldenFile() = runTest {
        requireGoldenTracesDirPresent()
        val goldenFile = File(goldenTracesDir, "peerLoss.jsonl")
        assertTrue("${goldenFile.path} not found -- treated as a hard failure, not a skip", goldenFile.isFile)

        val actual = generateTrace(ChatScenario.PEER_LOSS)
        assertEquals(goldenFile.readText(), actual)
    }

    companion object {
        /** DECISION (not pinned by the brief): arbitrary fixed seed chosen for
         * this module's golden-trace generation; see this class's doc comment. */
        const val GOLDEN_TRACE_SEED: Long = 1L
    }
}
