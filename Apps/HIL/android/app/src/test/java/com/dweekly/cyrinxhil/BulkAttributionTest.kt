package com.dweekly.cyrinxhil

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class BulkAttributionTest {
    @Test
    fun exactPositionRejectsSetMembershipFalsePositive() {
        val expected = listOf(byteArrayOf(1, 2, 3, 4))
        val candidates = listOf(
            BulkAttribution.Candidate(
                candidateId = 0,
                // Block [3, 4] is valid expected content, but belongs at index 1, not index 0.
                blocks = listOf(byteArrayOf(3, 4), null),
                scheduledFrameIndex = 0,
            ),
        )

        val result = BulkAttribution.attributeStrict(expected, candidates, blockBytes = 2).single()

        assertNull(result.expectedFrameIndex)
        assertEquals(0, result.verifiedBlocks)
    }

    @Test
    fun missedLeadingFramesDoNotShiftPayloadIdentity() {
        val expected = listOf(
            byteArrayOf(1, 2, 3, 4),
            byteArrayOf(5, 6, 7, 8),
            byteArrayOf(9, 10, 11, 12),
        )
        val start = 10_000 + 2 * (100 + 25)
        val scheduledFrame = BulkAttribution.scheduledFrameIndex(
            startSample = start,
            scheduleOriginSample = 10_000,
            frameSamples = 100,
            gapSamples = 25,
            frameCount = 3,
            toleranceSamples = 2,
        )
        val candidates = listOf(
            BulkAttribution.Candidate(
                candidateId = 7,
                blocks = listOf(byteArrayOf(9, 10), byteArrayOf(11, 12)),
                scheduledFrameIndex = scheduledFrame,
            ),
        )

        val result = BulkAttribution.attributeStrict(expected, candidates, blockBytes = 2).single()

        assertEquals(2, result.expectedFrameIndex)
        assertEquals(listOf(0, 1), result.verifiedBlockIndices)
    }

    @Test
    fun duplicateDetectionsReceiveCreditOnlyOnce() {
        val expected = listOf(byteArrayOf(1, 2, 3, 4))
        val candidates = listOf(
            BulkAttribution.Candidate(0, listOf(byteArrayOf(1, 2), null), 0),
            BulkAttribution.Candidate(1, listOf(byteArrayOf(1, 2), byteArrayOf(3, 4)), 0),
        )

        val results = BulkAttribution.attributeStrict(expected, candidates, blockBytes = 2)

        assertEquals(2, results.sumOf { it.verifiedBlocks })
        assertEquals(1, results.count { it.expectedFrameIndex == 0 })
        assertEquals(2, results.single { it.expectedFrameIndex == 0 }.verifiedBlocks)
    }

    @Test
    fun diagnosticGlobalAssignmentAvoidsGreedyUndercount() {
        val frame0 = byteArrayOf(1, 2, 3, 4)
        val frame1 = byteArrayOf(1, 2, 7, 8)
        val candidates = listOf(
            // Scores three against frame 0 and two against frame 1.
            BulkAttribution.Candidate(
                0,
                listOf(byteArrayOf(1), byteArrayOf(2), byteArrayOf(3), byteArrayOf(99)),
            ),
            // Scores two only against frame 0.
            BulkAttribution.Candidate(
                1,
                listOf(null, null, byteArrayOf(3), byteArrayOf(4)),
            ),
        )

        val results = BulkAttribution.attributeByContentDiagnostic(
            listOf(frame0, frame1),
            candidates,
            blockBytes = 1,
        )

        assertEquals(4, results.sumOf { it.verifiedBlocks })
        assertEquals(1, results.single { it.candidateId == 0 }.expectedFrameIndex)
        assertEquals(0, results.single { it.candidateId == 1 }.expectedFrameIndex)
    }

    @Test
    fun swappedPayloadsReceiveNoStrictCredit() {
        val expected = listOf(byteArrayOf(1, 2), byteArrayOf(3, 4))
        val swapped = listOf(
            BulkAttribution.Candidate(0, listOf(byteArrayOf(3, 4)), scheduledFrameIndex = 0),
            BulkAttribution.Candidate(1, listOf(byteArrayOf(1, 2)), scheduledFrameIndex = 1),
        )

        val strict = BulkAttribution.attributeStrict(expected, swapped, blockBytes = 2)
        val diagnostic = BulkAttribution.attributeByContentDiagnostic(expected, swapped, blockBytes = 2)

        assertEquals(0, strict.sumOf { it.verifiedBlocks })
        assertEquals(2, diagnostic.sumOf { it.verifiedBlocks })
    }

    @Test
    fun explicitOriginMapsLaterFramesWhenEndpointsAreMissing() {
        val mapped = listOf(2, 3).map { frameIndex ->
            BulkAttribution.scheduledFrameIndex(
                startSample = 50_000 + frameIndex * 125 + 2,
                scheduleOriginSample = 50_000,
                frameSamples = 100,
                gapSamples = 25,
                frameCount = 5,
                toleranceSamples = 4,
            )
        }

        assertEquals(listOf(2, 3), mapped)
        assertNull(
            BulkAttribution.scheduledFrameIndex(
                startSample = 50_000 + 2 * 125 + 5,
                scheduleOriginSample = 50_000,
                frameSamples = 100,
                gapSamples = 25,
                frameCount = 5,
                toleranceSamples = 4,
            ),
        )
    }

    @Test
    fun scheduledDenominatorDoesNotShrinkWhenEndpointFramesAreMissed() {
        val allDecoded = BulkMeasurement.score(
            verifiedBlocks = 10,
            blocksPerFrame = 2,
            frameCount = 5,
            frameSamples = 100,
            gapSamples = 25,
            trailingPadSamples = 50,
            sampleRate = 100,
        )
        val endpointsMissed = BulkMeasurement.score(
            verifiedBlocks = 6,
            blocksPerFrame = 2,
            frameCount = 5,
            frameSamples = 100,
            gapSamples = 25,
            trailingPadSamples = 50,
            sampleRate = 100,
        )

        assertEquals(600L, allDecoded.scheduledSpanSamples)
        assertEquals(650L, allDecoded.grossSpanSamples)
        assertEquals(allDecoded.scheduledSpanSamples, endpointsMissed.scheduledSpanSamples)
        assertEquals(allDecoded.grossSpanSamples, endpointsMissed.grossSpanSamples)
        assertEquals(allDecoded.scheduledGoodputBps * 0.6, endpointsMissed.scheduledGoodputBps, 1e-9)
    }

    @Test
    fun noDetectionsProducesZeroGoodputOverFullSchedule() {
        val metrics = BulkMeasurement.score(
            verifiedBlocks = 0,
            blocksPerFrame = 75,
            frameCount = 5,
            frameSamples = 192_000,
            gapSamples = 12_000,
            trailingPadSamples = 16_000,
            sampleRate = 48_000,
        )

        assertEquals(0.0, metrics.scheduledGoodputBps, 0.0)
        assertEquals(1_008_000L, metrics.scheduledSpanSamples)
        assertEquals(375, metrics.totalBlocks)
    }
}
