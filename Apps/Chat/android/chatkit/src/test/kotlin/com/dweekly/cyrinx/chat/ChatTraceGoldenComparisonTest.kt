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
 * ../fixtures/traces/ does not exist in this commit (README.md's directory map:
 * "not yet created -- lands in the verify stage, after CyrinxChatKit exists").
 * Every test in this class SKIPS via `org.junit.Assume.assumeTrue` when that
 * directory is absent, rather than failing, so this module's test suite stays
 * green before the verify stage populates the fixture.
 *
 * DECISION (not pinned by the brief): neither CONTRACT.md nor README.md pins the
 * exact golden trace filenames or the seed used to generate them. This test
 * expects `../fixtures/traces/happyPair.jsonl` and `../fixtures/traces/peerLoss
 * .jsonl` (the scenario's own [ChatScenario.wireName] plus `.jsonl`), generated
 * with seed [GOLDEN_TRACE_SEED]. Whoever wires the verify stage's Swift trace
 * generation needs to either match this naming/seed choice or this test's
 * constants need to move to match whatever the Swift side actually picks --
 * flagged in the C3-28 spec-stage report's `spec_issues` as a genuine
 * cross-platform coordination gap this document leaves open.
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
