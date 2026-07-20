import Foundation

/// A single transport-emitted event, wrapping a monotonic `eventSeq` around
/// one of eight payload kinds. Apps/Chat/CONTRACT.md §1.7.
///
/// `eventSeq` is monotonic from 0, **per client instance** -- client A's and
/// client B's `eventSeq` sequences are both independently zero-based and
/// never compared to each other. No event is emitted for a client's
/// implicit initial state (no peers, `disconnected(reason: nil)`) -- events
/// represent *transitions*, not the zero state.
///
/// CONTRACT.md's own "DECISION (not pinned by the brief)" note: the brief
/// under-specifies whether `eventSeq` is a field on a wrapper type or
/// duplicated onto every case. CONTRACT.md pins Swift as a wrapper struct
/// (this type); Kotlin instead hoists `eventSeq` onto a sealed-class base
/// property. The semantic content is identical on both platforms.
public struct ChatEvent: Equatable, Sendable {
    public let eventSeq: UInt64
    public let kind: Kind

    public init(eventSeq: UInt64, kind: Kind) {
        self.eventSeq = eventSeq
        self.kind = kind
    }

    public enum Kind: Equatable, Sendable {
        case peerFound(ChatPeer)
        case peerUpdated(ChatPeer)
        case peerLost(peerIdHex: String, reason: String)
        case connectionChanged(ChatConnectionState)
        case linkBudgetChanged(ChatLinkBudget)
        case messageReceived(ChatMessage)
        case messageStatusChanged(messageIdHex: String, status: ChatMessageDisplayStatus)
        case clientFailed(reason: String)
    }
}
