import CyrinxChatKit
import Foundation

/// One consumed `ChatEvent.messageGap(fromSequence, toSequence)`
/// (Apps/Chat/CONTRACT.md §1.7, §2's "Gap surfacing (pinned)") rendered for
/// display.
///
/// Surfaced as an inline caption row in the conversation's message list
/// (`ChatModel.conversationRows` below) -- **not** folded into `messages`
/// (whose element type stays `ChatMessage`-only, matching the model-trace
/// schema's own `messages` array; CONTRACT.md's model-trace cross-reference
/// paragraph) and **not** a `banner` (ORCHESTRATOR PINS #3 for this
/// sequence amendment: "banner priority rules unchanged").
public struct ChatMessageGapNotice: Identifiable, Equatable, Sendable {
    public let id: String
    public let fromSequence: UInt64
    public let toSequence: UInt64
    /// `ChatModel.messages.count` at the moment this notice was consumed --
    /// lets the UI interleave the caption at the point in the conversation
    /// timeline where the missing messages would have appeared, rather than
    /// always trailing at the end. See `ChatModel.conversationRows`.
    public let precedingMessageCount: Int

    public init(fromSequence: UInt64, toSequence: UInt64, precedingMessageCount: Int) {
        self.id = "messageGap-\(fromSequence)-\(toSequence)"
        self.fromSequence = fromSequence
        self.toSequence = toSequence
        self.precedingMessageCount = precedingMessageCount
    }

    /// "Messages missing: sequences X-Y" -- pinned verbatim by the C3-29
    /// sequence amendment (ORCHESTRATOR PINS #1). A single missing sequence
    /// (`fromSequence == toSequence`) renders "sequences X-X", not a
    /// special-cased singular form.
    public var text: String {
        "Messages missing: sequences \(fromSequence)-\(toSequence)"
    }
}

/// A single row rendered inside the conversation's scrolling message list:
/// either a `ChatMessage` bubble or a `messageGap` caption. Not part of
/// CONTRACT.md (a UI-model-layer concern only, this package's own addition)
/// -- see `ChatModel.conversationRows`.
public enum ChatConversationRow: Identifiable, Equatable, Sendable {
    case message(ChatMessage)
    case messageGapNotice(ChatMessageGapNotice)

    public var id: String {
        switch self {
        case .message(let message): return "message-\(message.id.hexString)"
        case .messageGapNotice(let notice): return notice.id
        }
    }
}
