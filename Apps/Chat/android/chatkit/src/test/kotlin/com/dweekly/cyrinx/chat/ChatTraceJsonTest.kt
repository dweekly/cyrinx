package com.dweekly.cyrinx.chat

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * [ChatTraceJson] rendering against literal expected JSON strings, checking the
 * canonical top-level field order (`eventSeq, virtualTimeMs, client, event`) and
 * each event `type`'s per-field order, both pinned in ../../../CONTRACT.md
 * section 4.
 */
class ChatTraceJsonTest {
    @Test
    fun peerFoundMatchesContractExample() {
        // The literal example line from CONTRACT.md section 4.
        val entry =
            ChatTraceEntry(
                eventSeq = 0,
                virtualTimeMs = 50,
                client = 'A',
                event = ChatEvent.PeerFound(0, ChatPeer("b1a2c3d4".hexToByteArray(), 50)),
            )
        assertEquals(
            """{"eventSeq": 0, "virtualTimeMs": 50, "client": "A", "event": {"type": "peerFound", "peer": {"idHex": "b1a2c3d4", "displayName": "Peer-B1A2", "discoveredAtMs": 50}}}""",
            ChatTraceJson.renderLine(entry),
        )
    }

    @Test
    fun connectionChangedConnectingHasNullReason() {
        // The literal example line from CONTRACT.md section 4.
        val entry = ChatTraceEntry(1, 100, 'A', ChatEvent.ConnectionChanged(1, ChatConnectionState.Connecting))
        assertEquals(
            """{"eventSeq": 1, "virtualTimeMs": 100, "client": "A", "event": {"type": "connectionChanged", "state": "connecting", "reason": null}}""",
            ChatTraceJson.renderLine(entry),
        )
    }

    @Test
    fun connectionChangedDisconnectedHasNonNullReason() {
        val entry =
            ChatTraceEntry(
                3,
                500,
                'A',
                ChatEvent.ConnectionChanged(3, ChatConnectionState.Disconnected("peerSilenceTimeout")),
            )
        assertEquals(
            """{"eventSeq": 3, "virtualTimeMs": 500, "client": "A", "event": {"type": "connectionChanged", "state": "disconnected", "reason": "peerSilenceTimeout"}}""",
            ChatTraceJson.renderLine(entry),
        )
    }

    @Test
    fun messageReceivedMatchesContractExampleShape() {
        val entry =
            ChatTraceEntry(
                2,
                380,
                'B',
                ChatEvent.MessageReceived(
                    2,
                    ChatMessage(
                        id = ByteArray(16) { 0x11 },
                        sequence = 1L,
                        direction = ChatMessage.Direction.INCOMING,
                        body = "hello",
                        senderPeerIdHex = "aabbccdd",
                        sentAtWallClockMs = 380,
                        status = ChatMessageDisplayStatus.Delivered,
                    ),
                ),
            )
        val expectedIdHex = ByteArray(16) { 0x11 }.toHexString()
        assertEquals(
            """{"eventSeq": 2, "virtualTimeMs": 380, "client": "B", "event": {"type": "messageReceived", """ +
                """"message": {"idHex": "$expectedIdHex", "sequence": 1, "direction": "incoming", "body": "hello", """ +
                """"senderPeerIdHex": "aabbccdd", "sentAtWallClockMs": 380, "status": "delivered"}}}""",
            ChatTraceJson.renderLine(entry),
        )
    }

    @Test
    fun messageGapFieldOrder() {
        // ../../../CONTRACT.md section 4's `messageGap` row: `fromSequence,
        // toSequence`, rendered as unsigned decimal (ENVELOPE.md section 8).
        val entry = ChatTraceEntry(6, 900, 'B', ChatEvent.MessageGap(6, 5L, 9L))
        assertEquals(
            """{"eventSeq": 6, "virtualTimeMs": 900, "client": "B", "event": {"type": "messageGap", """ +
                """"fromSequence": 5, "toSequence": 9}}""",
            ChatTraceJson.renderLine(entry),
        )
    }

    @Test
    fun messageGapRendersUnsignedNotSignedForHighBitSequences() {
        // sequence.toULong() must be used when rendering -- a naive
        // Long.toString() would print a negative number for a u64 value in the
        // top half of the range (ENVELOPE.md section 8's u64-not-Double rule
        // extends to trace rendering, not just JSON decode).
        val entry = ChatTraceEntry(0, 0, 'A', ChatEvent.MessageGap(0, -1L, -1L))
        assertEquals(
            """{"eventSeq": 0, "virtualTimeMs": 0, "client": "A", "event": {"type": "messageGap", """ +
                """"fromSequence": 18446744073709551615, "toSequence": 18446744073709551615}}""",
            ChatTraceJson.renderLine(entry),
        )
    }

    @Test
    fun messageStatusChangedFailedHasNonNullFailureReason() {
        val entry =
            ChatTraceEntry(
                5,
                450,
                'A',
                ChatEvent.MessageStatusChanged(5, "deadbeef", ChatMessageDisplayStatus.Failed("noAcknowledgment")),
            )
        assertEquals(
            """{"eventSeq": 5, "virtualTimeMs": 450, "client": "A", "event": {"type": "messageStatusChanged", """ +
                """"messageIdHex": "deadbeef", "status": "failed", "failureReason": "noAcknowledgment"}}""",
            ChatTraceJson.renderLine(entry),
        )
    }

    @Test
    fun messageStatusChangedNonFailedHasNullFailureReason() {
        val entry = ChatTraceEntry(4, 300, 'A', ChatEvent.MessageStatusChanged(4, "deadbeef", ChatMessageDisplayStatus.Queued))
        assertEquals(
            """{"eventSeq": 4, "virtualTimeMs": 300, "client": "A", "event": {"type": "messageStatusChanged", """ +
                """"messageIdHex": "deadbeef", "status": "queued", "failureReason": null}}""",
            ChatTraceJson.renderLine(entry),
        )
    }

    @Test
    fun linkBudgetChangedRendersNullableBoundsAsNull() {
        val entry =
            ChatTraceEntry(
                3,
                200,
                'A',
                ChatEvent.LinkBudgetChanged(3, ChatLinkBudget(LinkBudgetClass.TEXT, null, null, 0.7, 0)),
            )
        assertEquals(
            """{"eventSeq": 3, "virtualTimeMs": 200, "client": "A", "event": {"type": "linkBudgetChanged", """ +
                """"budget": {"classification": "text", "txLowerBoundBps": null, "rxLowerBoundBps": null, "confidence": 0.7, "ageMs": 0}}}""",
            ChatTraceJson.renderLine(entry),
        )
    }

    @Test
    fun linkBudgetChangedRendersNonNullBoundsAsNumbers() {
        val entry =
            ChatTraceEntry(
                0,
                0,
                'A',
                ChatEvent.LinkBudgetChanged(0, ChatLinkBudget(LinkBudgetClass.BULK, 1000, 2000, 0.9, 5)),
            )
        assertEquals(
            """{"eventSeq": 0, "virtualTimeMs": 0, "client": "A", "event": {"type": "linkBudgetChanged", """ +
                """"budget": {"classification": "bulk", "txLowerBoundBps": 1000, "rxLowerBoundBps": 2000, "confidence": 0.9, "ageMs": 5}}}""",
            ChatTraceJson.renderLine(entry),
        )
    }

    @Test
    fun peerLostFieldOrder() {
        val entry = ChatTraceEntry(4, 510, 'A', ChatEvent.PeerLost(4, "b1a2c3d4", "peerSilenceTimeout"))
        assertEquals(
            """{"eventSeq": 4, "virtualTimeMs": 510, "client": "A", "event": {"type": "peerLost", "peerIdHex": "b1a2c3d4", "reason": "peerSilenceTimeout"}}""",
            ChatTraceJson.renderLine(entry),
        )
    }

    @Test
    fun clientFailedFieldOrder() {
        val entry = ChatTraceEntry(9, 999, 'A', ChatEvent.ClientFailed(9, "transportFault"))
        assertEquals(
            """{"eventSeq": 9, "virtualTimeMs": 999, "client": "A", "event": {"type": "clientFailed", "reason": "transportFault"}}""",
            ChatTraceJson.renderLine(entry),
        )
    }

    @Test
    fun stringEscapingHandlesQuotesBackslashesAndControlChars() {
        val entry = ChatTraceEntry(0, 0, 'A', ChatEvent.ClientFailed(0, "line1\nline2\t\"quoted\"\\backslash"))
        assertEquals(
            """{"eventSeq": 0, "virtualTimeMs": 0, "client": "A", "event": {"type": "clientFailed", "reason": "line1\nline2\t\"quoted\"\\backslash"}}""",
            ChatTraceJson.renderLine(entry),
        )
    }

    @Test
    fun nonAsciiBodyIsEmittedAsRawUtf8NotEscaped() {
        val entry =
            ChatTraceEntry(
                0,
                0,
                'A',
                ChatEvent.MessageReceived(
                    0,
                    ChatMessage(ByteArray(16), 1L, ChatMessage.Direction.INCOMING, "Café 日本語 😀", "deadbeef", 0, ChatMessageDisplayStatus.Delivered),
                ),
            )
        val rendered = ChatTraceJson.renderLine(entry)
        assertEquals(true, rendered.contains("Café 日本語 😀"))
    }

    @Test
    fun recorderRendersJsonLinesNewlineTerminated() {
        val recorder = ChatTraceRecorder()
        recorder.sinkFor('A').invoke(0, 50, ChatEvent.ClientFailed(0, "x"))
        recorder.sinkFor('A').invoke(1, 60, ChatEvent.ClientFailed(1, "y"))
        val lines = recorder.toJsonLines()
        assertEquals(2, lines.trim().lines().size)
        assertEquals(true, lines.endsWith("\n"))
    }

    @Test
    fun emptyRecorderRendersEmptyString() {
        assertEquals("", ChatTraceRecorder().toJsonLines())
    }
}
