@file:OptIn(ExperimentalCoroutinesApi::class)

package com.dweekly.cyrinx.chat

import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * Compares this module's generated `happyPair` and `peerLoss` traces
 * byte-for-byte against the golden trace files in ../fixtures/traces/
 * (../../../CONTRACT.md section 3's closing note / ../../../README.md's
 * directory map): "generated from the Swift implementation in the verify stage
 * and checked byte-identical from Kotlin."
 *
 * ../fixtures/traces/ is populated as of the verify stage (see README.md's
 * directory map and CONTRACT.md section 4's "Golden trace fixtures (pinned)").
 * Every test in this class SKIPS via `org.junit.Assume.assumeTrue` if that
 * directory (or the specific golden file) is ever absent, rather than failing,
 * so this module's test suite degrades gracefully instead of hard-failing in a
 * checkout that predates the verify stage.
 *
 * Pinned by ../../../CONTRACT.md section 4's "Golden trace fixtures (pinned)":
 * the committed goldens are `fixtures/traces/happyPair.jsonl` and
 * `fixtures/traces/peerLoss.jsonl`, generated with seed 1
 * ([GOLDEN_TRACE_SEED]) by `CyrinxChatKit`'s `chat-trace-gen` executable and
 * asserted byte-identical here.
 */
class ChatTraceGoldenComparisonTest {
    private val goldenTracesDir = File("../fixtures/traces")

    /** Not pinned by the brief -- see this class's doc comment. */
    private val goldenTraceSeed = GOLDEN_TRACE_SEED

    private fun assumeGoldenTracesDirPresent() {
        assumeTrue(
            "../fixtures/traces/ does not exist yet (lands in the verify stage; see README.md's directory map)",
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
        assumeGoldenTracesDirPresent()
        val goldenFile = File(goldenTracesDir, "happyPair.jsonl")
        assumeTrue("${goldenFile.path} not found", goldenFile.isFile)

        val actual = generateTrace(ChatScenario.HAPPY_PAIR)
        assertEquals(goldenFile.readText(), actual)
    }

    @Test
    fun peerLossTraceMatchesGoldenFile() = runTest {
        assumeGoldenTracesDirPresent()
        val goldenFile = File(goldenTracesDir, "peerLoss.jsonl")
        assumeTrue("${goldenFile.path} not found", goldenFile.isFile)

        val actual = generateTrace(ChatScenario.PEER_LOSS)
        assertEquals(goldenFile.readText(), actual)
    }

    companion object {
        /** DECISION (not pinned by the brief): arbitrary fixed seed chosen for
         * this module's golden-trace generation; see this class's doc comment. */
        const val GOLDEN_TRACE_SEED: Long = 1L
    }
}
