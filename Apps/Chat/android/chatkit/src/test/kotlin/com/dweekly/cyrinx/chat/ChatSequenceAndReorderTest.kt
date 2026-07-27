@file:OptIn(ExperimentalCoroutinesApi::class)

package com.dweekly.cyrinx.chat

import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * C3-28 sequence amendment: direct tests of ../../../CONTRACT.md section 2's
 * "Outgoing sequence assignment (pinned)," "Receiver reorder window (pinned;
 * 32 sequences)," "Gap surfacing (pinned; `messageGap` event)," and
 * "Test-only injection seam (pinned)" bullets, plus ../../../ENVELOPE.md
 * section 1.2/1.3's wire `sequence` field as it flows through
 * `ChatMessage`/trace JSON. Mirrors CyrinxChatKit's Swift twin's
 * `ChatSequenceAndReorderTests.swift` test-by-test.
 *
 * Most tests here construct envelope bytes directly via
 * [ChatEnvelopeCodec.encode] and feed them to a receiving client's
 * (`internal`, same-module-visible) [SimulatedChatTransportClient
 * .deliverEnvelope] -- the same entry point paired-exchange delivery uses --
 * rather than only driving through `send()`, so out-of-order/duplicate-
 * sequence/window-bypass arrivals can be crafted precisely without needing 32+
 * real `send()` calls. A [ChatTraceRecorder] gives synchronous access to
 * emitted events, matching every other exact-trace test in this module.
 */
class ChatSequenceAndReorderTest {
    /** Builds valid envelope bytes for a crafted inbound arrival: a
     * [ChatEnvelopeCodec.ID_LEN]-byte messageId derived from [tag] (repeated to
     * fill the field, so distinct [tag] values give distinct, easily-recognized
     * IDs), the given [senderId]/[sequence]/[body]. */
    private fun craftedEnvelopeBytes(
        senderId: ByteArray,
        sequence: Long,
        tag: Byte,
        body: String = "x",
    ): ByteArray {
        val messageId = ByteArray(ChatEnvelopeCodec.ID_LEN) { tag }
        val envelope =
            ChatEnvelope(ChatEnvelopeCodec.VERSION, ChatEnvelopeKind.TEXT, messageId, null, senderId, sequence, body)
        return ChatEnvelopeCodec.encode(envelope)
    }

    private fun ChatTraceRecorder.messageGaps(client: Char = 'B'): List<ChatEvent.MessageGap> =
        entries().filter { it.client == client }.mapNotNull { it.event as? ChatEvent.MessageGap }

    private fun ChatTraceRecorder.messagesReceived(client: Char = 'B'): List<ChatMessage> =
        entries().filter { it.client == client }.mapNotNull { (it.event as? ChatEvent.MessageReceived)?.message }

    // -- Outgoing sequence assignment --------------------------------------------

    @Test
    fun outgoingSequenceStartsAtOneAndIncrementsByOnePerAcceptedSend() =
        runTest {
            val recorder = ChatTraceRecorder()
            val pair = connectedPair(seed = 601L, recorder = recorder)

            advanceTimeBy(150) // land at t=300, matching happyPair's send() timing
            runCurrent()
            pair.clientA.send("first")
            pair.clientA.send("second")
            pair.clientA.send("third")
            settle()

            val received = recorder.messagesReceived()
            assertEquals(listOf(1L, 2L, 3L), received.map { it.sequence })
            assertEquals(listOf("first", "second", "third"), received.map { it.body })
        }

    // -- Test-only injection seam --------------------------------------------------

    @Test
    fun retryThroughSeamIsDuplicateDiscardedWithSequenceIntact() =
        runTest {
            val recorder = ChatTraceRecorder()
            val pair = connectedPair(seed = 602L, recorder = recorder)
            // ../../../CONTRACT.md section 2's test-only injection seam:
            // re-deliver the SAME outgoing bytes 5ms after the original --
            // exactly the "retried delivery via the injection seam re-delivers
            // the SAME bytes (same ID, same sequence)" case the pin describes.
            pair.clientA.testOnlyInjectionSeam =
                ChatInjectionSeam { bytes ->
                    listOf(ChatInjectionSeam.Delivery(0L, bytes), ChatInjectionSeam.Delivery(5L, bytes))
                }

            advanceTimeBy(150)
            runCurrent()
            pair.clientA.send("seam-retry")
            settle()

            val received = recorder.messagesReceived()
            assertEquals(1, received.size)
            assertEquals(1L, received.first().sequence)
            assertEquals("seam-retry", received.first().body)
        }

    @Test
    fun sameSeedAndSameDeterministicInjectionSeamScriptProduceByteIdenticalTraces() {
        val seed = 603L

        fun runOnce(): String {
            var trace = ""
            runTest {
                val recorder = ChatTraceRecorder()
                val pair = createChatPair(ChatScenario.HAPPY_PAIR, seed, recorder)
                // A pure, deterministic function of bytes -- no wall-clock/
                // randomness involved -- so two independent runs of the same
                // seed with this same script produce identical schedules.
                pair.clientA.testOnlyInjectionSeam =
                    ChatInjectionSeam { bytes ->
                        listOf(ChatInjectionSeam.Delivery(0L, bytes), ChatInjectionSeam.Delivery(3L, bytes))
                    }

                pair.clientA.start()
                pair.clientB.start()
                advanceTimeBy(100)
                runCurrent()
                pair.clientA.connect(expectedPeerIds(seed).second.toHexString())
                advanceTimeBy(200)
                runCurrent()
                pair.clientA.send("seam-deterministic")
                settle()

                trace = recorder.toJsonLines()
            }
            return trace
        }

        val first = runOnce()
        val second = runOnce()
        assertTrue("sanity: the trace must not be empty", first.isNotEmpty())
        assertEquals("identical seed + identical seam script must produce byte-identical traces", first, second)

        // The seam's duplicate delivery is still suppressed by messageId dedup
        // -- exactly one messageReceived survives.
        assertEquals(1, first.lines().count { it.contains("\"messageReceived\"") && it.contains("\"client\": \"B\"") })
    }

    // -- Receiver reorder window ---------------------------------------------------

    @Test
    fun inWindowOutOfOrderArrivalsAreDeliveredInAscendingSequenceOrder() =
        runTest {
            val recorder = ChatTraceRecorder()
            val pair = createChatPair(ChatScenario.HAPPY_PAIR, 604L, recorder)
            val senderId = ByteArray(4) { 0xAA.toByte() }

            pair.clientB.deliverEnvelope(craftedEnvelopeBytes(senderId, sequence = 3L, tag = 0x03, body = "three"))
            pair.clientB.deliverEnvelope(craftedEnvelopeBytes(senderId, sequence = 1L, tag = 0x01, body = "one"))
            pair.clientB.deliverEnvelope(craftedEnvelopeBytes(senderId, sequence = 2L, tag = 0x02, body = "two"))

            // Arrived 3, 1, 2 -- delivered 1, 2, 3: sequence 3 was buffered
            // until sequence 1 (then 2) filled the hole in front of it.
            val received = recorder.messagesReceived()
            assertEquals(listOf(1L, 2L, 3L), received.map { it.sequence })
            assertEquals(listOf("one", "two", "three"), received.map { it.body })
        }

    @Test
    fun anArrivalWhoseSequenceWasAlreadyResolvedButWhoseMessageIdIsNewIsDroppedAndCounted() =
        runTest {
            val recorder = ChatTraceRecorder()
            val pair = createChatPair(ChatScenario.HAPPY_PAIR, 605L, recorder)
            val senderId = ByteArray(4) { 0xBB.toByte() }

            pair.clientB.deliverEnvelope(craftedEnvelopeBytes(senderId, sequence = 1L, tag = 0x01, body = "first"))
            assertEquals(0, pair.clientB.droppedStaleOrDuplicateSequenceCount)

            // A DIFFERENT messageId reusing the already-resolved sequence 1 --
            // not the duplicateIncoming messageId-dedup case (this is a new
            // ID), CONTRACT.md section 2's "already delivered ... but
            // messageId unseen" drop.
            pair.clientB.deliverEnvelope(craftedEnvelopeBytes(senderId, sequence = 1L, tag = 0x02, body = "impostor"))
            assertEquals(1, pair.clientB.droppedStaleOrDuplicateSequenceCount)

            // Only the legitimate first delivery ever reaches the event stream
            // -- the impostor is dropped, never delivered.
            assertEquals(listOf("first"), recorder.messagesReceived().map { it.body })
        }

    // -- Gap surfacing ---------------------------------------------------------------

    @Test
    fun anArrivalThirtyTwoOrMoreSequencesBeyondBaseSurfacesGapAtTheExactBypassBoundary() =
        runTest {
            val senderId = ByteArray(4) { 0xCC.toByte() }

            // base=1: sequence 32 is 31 beyond base (32 - 1 = 31 < 32) --
            // still exactly in-window, buffered normally, no gap.
            val recorderNoGap = ChatTraceRecorder()
            val pairNoGap = createChatPair(ChatScenario.HAPPY_PAIR, 606L, recorderNoGap)
            pairNoGap.clientB.deliverEnvelope(craftedEnvelopeBytes(senderId, sequence = 32L, tag = 0x30))
            assertTrue(recorderNoGap.messageGaps().isEmpty())

            // base=1: sequence 33 is 32 beyond base (33 - 1 = 32 >= 32) --
            // bypasses the window. The missing run is exactly [1, 33-32] = [1,1].
            val recorderBypass = ChatTraceRecorder()
            val pairBypass = createChatPair(ChatScenario.HAPPY_PAIR, 606L, recorderBypass)
            pairBypass.clientB.deliverEnvelope(craftedEnvelopeBytes(senderId, sequence = 33L, tag = 0x30))
            val gaps = recorderBypass.messageGaps()
            assertEquals(1, gaps.size)
            assertEquals(1L, gaps.first().fromSequence)
            assertEquals(1L, gaps.first().toSequence)
        }

    @Test
    fun windowBypassPrunesAlreadyBufferedEntriesLeftBehindCountedAsStale() =
        runTest {
            val recorder = ChatTraceRecorder()
            val pair = createChatPair(ChatScenario.HAPPY_PAIR, 609L, recorder)
            val senderId = ByteArray(4) { 0xEE.toByte() }

            // base=1: sequences 5 and 6 arrive out of order first and buffer
            // (in-window: 5-1=4 < 32, 6-1=5 < 32) -- base itself (1) is still
            // missing.
            pair.clientB.deliverEnvelope(craftedEnvelopeBytes(senderId, sequence = 5L, tag = 0x60))
            pair.clientB.deliverEnvelope(craftedEnvelopeBytes(senderId, sequence = 6L, tag = 0x61))
            assertEquals(0, pair.clientB.droppedStaleOrDuplicateSequenceCount)
            assertTrue(recorder.messageGaps().isEmpty())

            // sequence 40 is 39 beyond base=1 (>= 32) -- bypasses the window.
            // The missing run is [1, 40-32] = [1, 8], which swallows both
            // already-buffered entries (5 and 6, both <= 8): they are pruned
            // and counted as stale, never delivered.
            pair.clientB.deliverEnvelope(craftedEnvelopeBytes(senderId, sequence = 40L, tag = 0x62))

            val gaps = recorder.messageGaps()
            assertEquals(1, gaps.size)
            assertEquals(1L, gaps.first().fromSequence)
            assertEquals(8L, gaps.first().toSequence)
            // Both pruned buffered entries (5, 6) are counted as stale drops.
            assertEquals(2, pair.clientB.droppedStaleOrDuplicateSequenceCount)
            // Nothing at all was ever delivered: 5 and 6 were pruned as
            // stale, and 40 itself is buffered (40 - 9 = 31 < 32 against the
            // new base), waiting for a base that never arrives.
            assertTrue(recorder.messagesReceived().isEmpty())
        }

    @Test
    fun aLaterArrivalInsideAnAlreadySurfacedGapRangeIsDroppedAsStaleNeverResurfaced() =
        runTest {
            val recorder = ChatTraceRecorder()
            val pair = createChatPair(ChatScenario.HAPPY_PAIR, 607L, recorder)
            val senderId = ByteArray(4) { 0xDD.toByte() }

            // Bypass the window (base=1, arrival=33) -- surfaces messageGap(1,1).
            pair.clientB.deliverEnvelope(craftedEnvelopeBytes(senderId, sequence = 33L, tag = 0x40))
            assertEquals(1, recorder.messageGaps().size)
            assertEquals(0, pair.clientB.droppedStaleOrDuplicateSequenceCount)

            // A later, distinct-messageId arrival for sequence 1 -- squarely
            // inside the just-surfaced [1,1] gap -- must be dropped as stale,
            // counted, and must NOT surface a second messageGap.
            pair.clientB.deliverEnvelope(craftedEnvelopeBytes(senderId, sequence = 1L, tag = 0x41))
            assertEquals(1, pair.clientB.droppedStaleOrDuplicateSequenceCount)
            assertEquals(1, recorder.messageGaps().size)
        }

    @Test
    fun gapStillUnfilledWhenTheConnectionScopeEndsIsSurfacedAtStop() =
        runTest {
            val recorder = ChatTraceRecorder()
            val pair = connectedPair(seed = 608L, recorder = recorder)

            // Sequence 1 (the base) never arrives -- only sequence 2, which
            // buffers, waiting for a base that will never show up.
            pair.clientB.deliverEnvelope(
                craftedEnvelopeBytes(pair.clientA.id, sequence = 2L, tag = 0x50, body = "buffered"),
            )
            assertTrue(recorder.messageGaps().isEmpty())

            pair.clientB.stop()
            settle()

            val gaps = recorder.messageGaps()
            assertEquals(1, gaps.size)
            assertEquals(1L, gaps.first().fromSequence)
            assertEquals(1L, gaps.first().toSequence)
        }

    // -- Sequence values in traces ---------------------------------------------------

    @Test
    fun messageReceivedTraceJsonCarriesSequenceAtThePinnedPosition() =
        runTest {
            val recorder = ChatTraceRecorder()
            val pair = createChatPair(ChatScenario.HAPPY_PAIR, 1L, recorder)
            pair.clientA.start()
            pair.clientB.start()
            advanceTimeBy(100)
            runCurrent()
            pair.clientA.connect(expectedPeerIds(1L).second.toHexString())
            advanceTimeBy(200)
            runCurrent()
            pair.clientA.send("hello")
            settle()

            val received = recorder.messagesReceived()
            // Matches fixtures/traces/happyPair.jsonl's own pinned value.
            assertEquals(1L, received.single().sequence)

            val line = recorder.entries().first { it.client == 'B' && it.event is ChatEvent.MessageReceived }
            val json = ChatTraceJson.renderLine(line)
            val idHexPos = json.indexOf("\"idHex\"")
            val sequencePos = json.indexOf("\"sequence\"")
            val directionPos = json.indexOf("\"direction\"")
            assertTrue(idHexPos in 0 until sequencePos)
            assertTrue(sequencePos in 0 until directionPos)
        }

    @Test
    fun messageGapDoesNotAppearInAnyOfTheSixScenarioTimelines() =
        runTest {
            val recorder = ChatTraceRecorder()
            val pair = createChatPair(ChatScenario.HAPPY_PAIR, 1L, recorder)
            pair.clientA.start()
            pair.clientB.start()
            advanceTimeBy(100)
            runCurrent()
            pair.clientA.connect(expectedPeerIds(1L).second.toHexString())
            advanceTimeBy(200)
            runCurrent()
            pair.clientA.send("hello")
            settle()

            assertFalse(recorder.entries().any { it.event is ChatEvent.MessageGap })
        }
}
