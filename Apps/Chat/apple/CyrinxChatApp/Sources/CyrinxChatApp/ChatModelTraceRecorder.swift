import Foundation

/// Records one JSON-lines record per `ChatModel.apply(_:)` call, per the
/// C3-29/C3-30 design brief's pinned model-trace schema, as amended by the
/// C3-29 sequence amendment (ORCHESTRATOR PINS #1; CONTRACT.md's
/// model-trace cross-reference paragraph):
///
/// ```
/// {"eventSeq": N, "connection": "connected", "budget": "text",
///  "peers": ["a1b2c3d4"], "messages": [{"idHex": "…", "sequence": 1,
///  "direction": "outgoing", "status": "delivered"}], "messageGaps": 0,
///  "banner": null, "gap": false}
/// ```
///
/// Canonical top-level field order: `eventSeq, connection, budget, peers,
/// messages, messageGaps, banner, gap`. `connection` is
/// `ChatConnectionState`'s CONTRACT.md §4 wire string (reason is
/// deliberately not included -- the brief's schema has no field for it);
/// `budget` is `LinkBudgetClass`'s §1.3 wire string (classification only,
/// not the numeric bounds/confidence/age); `peers` is `ChatModel.peers`
/// mapped to `idHex`, in that array's own (already pinned-sorted) order;
/// `messages` entries carry `idHex`/`sequence`/`direction`/`status`, in
/// that order, in `ChatModel.messages`' own append order (`sequence` is the
/// envelope `sequence` field, ENVELOPE.md §1.2 -- the sequence amendment's
/// own addition, positioned immediately after `idHex`); `messageGaps` is
/// `ChatModel.messageGaps`'s current count of consumed `messageGap` events
/// (the sequence amendment's other addition, positioned immediately before
/// `banner`); `banner` is the current plain-language string or `null`;
/// `gap` is `ChatModel.eventSeqGapDetected`'s current value.
///
/// A class, not a struct, so `ChatModel` can hold an optional reference to
/// one shared instance and append to it from `apply(_:)` without needing
/// the recorder itself to be part of `ChatModel`'s own `@Observable`
/// storage.
public final class ChatModelTraceRecorder {
    public private(set) var lines: [String] = []

    public init() {}

    func record(
        eventSeq: UInt64,
        connectionWire: String,
        budgetClassWire: String,
        peerIdHexes: [String],
        messageEntries: [(idHex: String, sequence: UInt64, direction: String, status: String)],
        messageGaps: Int,
        banner: String?,
        gap: Bool
    ) {
        let messagesJSON = messageEntries.map { entry in
            ChatModelTraceJSON.object([
                ("idHex", ChatModelTraceJSON.string(entry.idHex)),
                ("sequence", ChatModelTraceJSON.uint64(entry.sequence)),
                ("direction", ChatModelTraceJSON.string(entry.direction)),
                ("status", ChatModelTraceJSON.string(entry.status)),
            ])
        }
        let line = ChatModelTraceJSON.object([
            ("eventSeq", ChatModelTraceJSON.uint64(eventSeq)),
            ("connection", ChatModelTraceJSON.string(connectionWire)),
            ("budget", ChatModelTraceJSON.string(budgetClassWire)),
            ("peers", ChatModelTraceJSON.array(peerIdHexes.map(ChatModelTraceJSON.string))),
            ("messages", ChatModelTraceJSON.array(messagesJSON)),
            ("messageGaps", ChatModelTraceJSON.int(messageGaps)),
            ("banner", ChatModelTraceJSON.stringOrNull(banner)),
            ("gap", ChatModelTraceJSON.bool(gap)),
        ])
        lines.append(line)
    }

    /// All recorded lines joined with `\n`, plus one trailing `\n` --
    /// matching `chat-trace-gen`'s own file-writing convention (Apps/Chat/
    /// CyrinxChatKit/Sources/ChatTraceGen/main.swift).
    public func joinedText() -> String {
        lines.joined(separator: "\n") + "\n"
    }
}
