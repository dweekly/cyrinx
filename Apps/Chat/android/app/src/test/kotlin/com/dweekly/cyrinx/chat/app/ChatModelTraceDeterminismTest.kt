@file:OptIn(ExperimentalCoroutinesApi::class)

package com.dweekly.cyrinx.chat.app

import com.dweekly.cyrinx.chat.ChatScenario
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Determinism regression net for the model-trace recorder, independent of
 * whether `Apps/Chat/fixtures/model-traces/` goldens exist yet
 * (ChatModelTraceGoldenComparisonTest covers cross-implementation
 * byte-identity once they do): two independently-constructed
 * [ChatViewModel]/[ChatModelTraceRecorder] pairs, driven through the SAME
 * scenario at the SAME seed via the SAME [generateModelTrace] driver
 * (ChatViewModelTestSupport.kt), must produce byte-identical output. This is
 * the permanent, "ship the spike" regression test for the specific gate this
 * PR's own delivery checked manually (two local runs, byte-identical) --
 * kept in the suite so a future regression in [SplitMix64][com.dweekly.cyrinx.chat.SplitMix64]
 * seeding, [kotlinx.coroutines.flow.MutableStateFlow] update ordering, or the
 * JSON renderer's field iteration order fails a test immediately rather than
 * only showing up as a diff against a golden file someone happens to
 * regenerate.
 */
class ChatModelTraceDeterminismTest {
    @Test
    fun happyPairModelTraceIsByteIdenticalAcrossTwoIndependentRuns() = runTest {
        val first = generateModelTrace(ChatScenario.HAPPY_PAIR, SEED)
        val second = generateModelTrace(ChatScenario.HAPPY_PAIR, SEED)

        assertTrue("a real scenario must produce at least one trace line", first.isNotEmpty())
        assertEquals(first, second)
    }

    @Test
    fun peerLossModelTraceIsByteIdenticalAcrossTwoIndependentRuns() = runTest {
        val first = generateModelTrace(ChatScenario.PEER_LOSS, SEED)
        val second = generateModelTrace(ChatScenario.PEER_LOSS, SEED)

        assertTrue("a real scenario must produce at least one trace line", first.isNotEmpty())
        assertEquals(first, second)
    }

    companion object {
        /** Matches ChatModelTraceGoldenComparisonTest.GOLDEN_TRACE_SEED; not
         * reused directly across files to keep each test class's own
         * constants self-contained and independently auditable (this repo's
         * own convention -- see e.g. chatkit's ChatScenarioTimings doc
         * comment on per-scenario constants). */
        const val SEED: Long = 1L
    }
}
