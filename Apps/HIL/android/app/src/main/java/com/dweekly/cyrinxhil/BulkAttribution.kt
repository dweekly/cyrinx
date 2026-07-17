package com.dweekly.cyrinxhil

/** Pure, deterministic attribution and finite-burst metric helpers for the bulk PHY. */
object BulkAttribution {
    data class Candidate(
        val candidateId: Int,
        /** CRC-invalid blocks are null; non-null blocks retain their original block index. */
        val blocks: List<ByteArray?>,
        /** Slot derived only from an explicit capture-coordinate schedule origin. */
        val scheduledFrameIndex: Int? = null,
    )

    data class Result(
        val candidateId: Int,
        val expectedFrameIndex: Int?,
        val verifiedBlockIndices: List<Int>,
    ) {
        val verifiedBlocks: Int
            get() = verifiedBlockIndices.size
    }

    /**
     * Assign decoded candidates to scheduled frames one-to-one and count only CRC-valid blocks
     * that equal the expected bytes at the same frame and block position.
     *
     * A maximum-weight bipartite assignment prevents a duplicate chirp/demodulation from
     * receiving credit twice and avoids candidate-order-dependent greedy attribution. Missing
     * candidates simply leave their scheduled frames unassigned.
     */
    fun attributeByContentDiagnostic(
        expectedPayloads: List<ByteArray>,
        candidates: List<Candidate>,
        blockBytes: Int,
    ): List<Result> = attributeInternal(
        expectedPayloads,
        candidates,
        blockBytes,
        strictSchedule = false,
    )

    /**
     * Headline-grade attribution. A candidate may match only the scheduled frame slot derived
     * from its chirp time and an explicit schedule origin. Content never selects the time slot.
     */
    fun attributeStrict(
        expectedPayloads: List<ByteArray>,
        candidates: List<Candidate>,
        blockBytes: Int,
    ): List<Result> = attributeInternal(
        expectedPayloads,
        candidates,
        blockBytes,
        strictSchedule = true,
    )

    /** Return the nearest scheduled frame only when its chirp is within the declared tolerance. */
    fun scheduledFrameIndex(
        startSample: Int,
        scheduleOriginSample: Int,
        frameSamples: Int,
        gapSamples: Int,
        frameCount: Int,
        toleranceSamples: Int,
    ): Int? {
        require(frameSamples > 0 && gapSamples >= 0) { "invalid frame schedule" }
        require(frameCount > 0) { "frameCount must be positive" }
        require(toleranceSamples >= 0) { "toleranceSamples must be nonnegative" }
        val period = frameSamples.toLong() + gapSamples
        val delta = startSample.toLong() - scheduleOriginSample
        val nearest = kotlin.math.floor(delta.toDouble() / period + 0.5).toInt()
        if (nearest !in 0 until frameCount) {
            return null
        }
        val expectedStart = scheduleOriginSample.toLong() + nearest.toLong() * period
        return nearest.takeIf {
            kotlin.math.abs(startSample.toLong() - expectedStart) <= toleranceSamples.toLong()
        }
    }

    private fun attributeInternal(
        expectedPayloads: List<ByteArray>,
        candidates: List<Candidate>,
        blockBytes: Int,
        strictSchedule: Boolean,
    ): List<Result> {
        require(blockBytes > 0) { "blockBytes must be positive" }
        require(expectedPayloads.isNotEmpty()) { "at least one expected payload is required" }
        val payloadBytes = expectedPayloads.first().size
        require(payloadBytes > 0 && payloadBytes % blockBytes == 0) {
            "expected payload must contain complete blocks"
        }
        require(expectedPayloads.all { it.size == payloadBytes }) {
            "all expected payloads must have the same size"
        }
        val blocksPerFrame = payloadBytes / blockBytes
        require(candidates.all { it.blocks.size == blocksPerFrame }) {
            "candidate block count must match the expected payload geometry"
        }
        require(candidates.map { it.candidateId }.toSet().size == candidates.size) {
            "candidate IDs must be unique"
        }

        if (candidates.isEmpty()) {
            return emptyList()
        }

        val scores = Array(candidates.size) { candidateIndex ->
            IntArray(expectedPayloads.size) { frameIndex ->
                val scheduledFrame = candidates[candidateIndex].scheduledFrameIndex
                if (strictSchedule && scheduledFrame != frameIndex) {
                    0
                } else {
                    matchingBlockIndices(
                        expectedPayloads[frameIndex],
                        candidates[candidateIndex].blocks,
                        blockBytes,
                    ).size
                }
            }
        }
        val assignedFrames = maximumWeightAssignment(scores)
        return candidates.indices.map { candidateIndex ->
            val frameIndex = assignedFrames[candidateIndex]
            val matches = if (frameIndex >= 0 && scores[candidateIndex][frameIndex] > 0) {
                matchingBlockIndices(
                    expectedPayloads[frameIndex],
                    candidates[candidateIndex].blocks,
                    blockBytes,
                )
            } else {
                emptyList()
            }
            Result(
                candidateId = candidates[candidateIndex].candidateId,
                expectedFrameIndex = frameIndex.takeIf { matches.isNotEmpty() },
                verifiedBlockIndices = matches,
            )
        }
    }

    private fun matchingBlockIndices(
        expectedPayload: ByteArray,
        decodedBlocks: List<ByteArray?>,
        blockBytes: Int,
    ): List<Int> = decodedBlocks.indices.filter { blockIndex ->
        val decoded = decodedBlocks[blockIndex] ?: return@filter false
        if (decoded.size != blockBytes) {
            return@filter false
        }
        val expectedOffset = blockIndex * blockBytes
        decoded.indices.all { byteIndex ->
            decoded[byteIndex] == expectedPayload[expectedOffset + byteIndex]
        }
    }

    /** Hungarian minimum-cost assignment applied to negated nonnegative weights. */
    private fun maximumWeightAssignment(weights: Array<IntArray>): IntArray {
        val rows = weights.size
        val columns = weights.firstOrNull()?.size ?: 0
        if (rows == 0 || columns == 0) {
            return IntArray(rows) { -1 }
        }
        require(weights.all { it.size == columns }) { "assignment matrix must be rectangular" }
        val size = maxOf(rows, columns)
        val maxWeight = weights.maxOf { row -> row.maxOrNull() ?: 0 }
        val rowPotential = IntArray(size + 1)
        val columnPotential = IntArray(size + 1)
        val columnMatch = IntArray(size + 1)
        val previousColumn = IntArray(size + 1)

        for (row in 1..size) {
            columnMatch[0] = row
            var currentColumn = 0
            val minimum = IntArray(size + 1) { Int.MAX_VALUE }
            val used = BooleanArray(size + 1)
            do {
                used[currentColumn] = true
                val currentRow = columnMatch[currentColumn]
                var delta = Int.MAX_VALUE
                var nextColumn = 0
                for (column in 1..size) {
                    if (used[column]) {
                        continue
                    }
                    val weight = if (currentRow <= rows && column <= columns) {
                        weights[currentRow - 1][column - 1]
                    } else {
                        0
                    }
                    val reducedCost =
                        maxWeight - weight - rowPotential[currentRow] - columnPotential[column]
                    if (reducedCost < minimum[column]) {
                        minimum[column] = reducedCost
                        previousColumn[column] = currentColumn
                    }
                    if (minimum[column] < delta) {
                        delta = minimum[column]
                        nextColumn = column
                    }
                }
                for (column in 0..size) {
                    if (used[column]) {
                        rowPotential[columnMatch[column]] += delta
                        columnPotential[column] -= delta
                    } else {
                        minimum[column] -= delta
                    }
                }
                currentColumn = nextColumn
            } while (columnMatch[currentColumn] != 0)

            do {
                val nextColumn = previousColumn[currentColumn]
                columnMatch[currentColumn] = columnMatch[nextColumn]
                currentColumn = nextColumn
            } while (currentColumn != 0)
        }

        val assignment = IntArray(rows) { -1 }
        for (column in 1..size) {
            val row = columnMatch[column]
            if (row in 1..rows && column <= columns) {
                assignment[row - 1] = column - 1
            }
        }
        return assignment
    }
}

object BulkMeasurement {
    data class Metrics(
        val verifiedBlocks: Int,
        val totalBlocks: Int,
        val scheduledSpanSamples: Long,
        val grossSpanSamples: Long,
        val scheduledGoodputBps: Double,
        val grossGoodputBps: Double,
        val blockSuccessRate: Double,
    )

    /**
     * Score against the transmitted schedule, never the first/last successfully decoded chirp.
     */
    fun score(
        verifiedBlocks: Int,
        blocksPerFrame: Int,
        frameCount: Int,
        frameSamples: Int,
        gapSamples: Int,
        trailingPadSamples: Int,
        sampleRate: Int,
    ): Metrics {
        require(blocksPerFrame >= 0) { "blocksPerFrame must be nonnegative" }
        require(frameCount > 0) { "frameCount must be positive" }
        require(frameSamples > 0) { "frameSamples must be positive" }
        require(gapSamples >= 0 && trailingPadSamples >= 0) {
            "schedule padding must be nonnegative"
        }
        require(sampleRate > 0) { "sampleRate must be positive" }
        val totalBlocks = Math.multiplyExact(blocksPerFrame, frameCount)
        require(verifiedBlocks in 0..totalBlocks) {
            "verified blocks must fit within the scheduled payload"
        }
        val scheduledSamples =
            Math.multiplyExact(frameSamples.toLong(), frameCount.toLong()) +
                Math.multiplyExact(gapSamples.toLong(), (frameCount - 1).toLong())
        val grossSamples = scheduledSamples + trailingPadSamples
        val verifiedBits = verifiedBlocks.toDouble() * BulkDemod.CRC_BLOCK * 8.0
        return Metrics(
            verifiedBlocks = verifiedBlocks,
            totalBlocks = totalBlocks,
            scheduledSpanSamples = scheduledSamples,
            grossSpanSamples = grossSamples,
            scheduledGoodputBps = verifiedBits * sampleRate / scheduledSamples,
            grossGoodputBps = verifiedBits * sampleRate / grossSamples,
            blockSuccessRate = if (totalBlocks == 0) 0.0 else verifiedBlocks.toDouble() / totalBlocks,
        )
    }
}
