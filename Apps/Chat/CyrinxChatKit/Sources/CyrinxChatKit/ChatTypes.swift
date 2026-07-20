import Foundation

/// A discovered chat peer. Apps/Chat/CONTRACT.md §1.1.
///
/// The acoustic link is unauthenticated (Apps/Chat/README.md's security
/// notice): `id` is an ephemeral transport peer ID, not a verified
/// identity. Equality is by `id` only -- two values with the same `id` but
/// a stale `discoveredAtMs` are still "the same peer" for dictionary/set
/// purposes; `peerUpdated` (see `ChatEvent.Kind`) exists precisely to carry
/// a refreshed value for an already-known `id`.
public struct ChatPeer: Equatable, Identifiable, Sendable {
    public let id: Data
    public var discoveredAtMs: Int64

    /// `"Peer-" + uppercase-hex(id[0:2])`, e.g. an id starting `0xb1 0xa2...`
    /// becomes `"Peer-B1A2"` (CONTRACT.md §1.1). Purely cosmetic -- never
    /// used for equality or lookup.
    public var displayName: String {
        "Peer-" + id.prefix(2).map { String(format: "%02X", $0) }.joined()
    }

    public init(id: Data, discoveredAtMs: Int64) {
        self.id = id
        self.discoveredAtMs = discoveredAtMs
    }

    public static func == (lhs: Self, rhs: Self) -> Bool { lhs.id == rhs.id }
}

/// UI-facing projection of connection state. Apps/Chat/CONTRACT.md §1.2.
///
/// The transport owns protocol state; this enum is a coarse view for
/// display, not the source of truth.
public enum ChatConnectionState: Equatable, Sendable {
    case disconnected(reason: String?)
    case connecting
    case connected
    case degraded
}

/// Coarse, wire/trace-string-form directional link budget classification.
/// Apps/Chat/CONTRACT.md §1.3. `rawValue` is the cross-language-identical
/// wire string used in JSON-lines traces (CONTRACT.md §4) and any other
/// serialized form.
public enum LinkBudgetClass: String, Equatable, Sendable {
    case controlOnly
    case text
    case thumbnail
    case bulk
}

/// The numeric `LinkEstimate` backing referenced by
/// `docs/CYRINX_3_PLAN.md` Phase F's product scope ("coarse directional
/// link budget ... backed by the numeric `LinkEstimate`"). Apps/Chat/
/// CONTRACT.md §1.4.
public struct ChatLinkBudget: Equatable, Sendable {
    public var classification: LinkBudgetClass
    /// Conservative outbound lower bound in bits/second; `nil` when not yet
    /// estimated.
    public var txLowerBoundBps: Int?
    /// Conservative inbound lower bound in bits/second; `nil` when not yet
    /// estimated.
    public var rxLowerBoundBps: Int?
    public var confidence: Double
    /// How stale this estimate is, in virtual/monotonic ms.
    public var ageMs: Int

    public init(
        classification: LinkBudgetClass,
        txLowerBoundBps: Int?,
        rxLowerBoundBps: Int?,
        confidence: Double,
        ageMs: Int
    ) {
        self.classification = classification
        self.txLowerBoundBps = txLowerBoundBps
        self.rxLowerBoundBps = rxLowerBoundBps
        self.confidence = confidence
        self.ageMs = ageMs
    }
}

/// Outgoing-message display status. Apps/Chat/CONTRACT.md §1.5.
///
/// Honest-delivery caveat (must appear in every surface that renders this
/// status, per the C3-28 design brief): `.delivered` means **transport
/// acknowledgment only**.
///
/// - Queue acceptance (`ChatTransportClient.send(body:)` returning a
///   `messageIdHex`) is *never* delivery. A message can sit in `.queued` or
///   `.transmitting` indefinitely and still ultimately `.failed`.
/// - Transport acknowledgment is *not authenticated identity*. The acoustic
///   link has no authentication (Apps/Chat/README.md's security notice), so
///   `.delivered` tells you a peer's transport acked receipt of these
///   bytes -- it does not tell you which peer, cryptographically, actually
///   received or read them.
public enum ChatMessageDisplayStatus: Equatable, Sendable {
    case queued
    case transmitting
    case delivered
    case failed(reason: String)
}

/// A chat message, incoming or outgoing. Apps/Chat/CONTRACT.md §1.6.
public struct ChatMessage: Equatable, Identifiable, Sendable {
    public enum Direction: Equatable, Sendable {
        case incoming
        case outgoing
    }

    /// Same value as the envelope's `messageId` (ENVELOPE.md §1.1).
    public let id: Data
    public var direction: Direction
    /// Decoded UTF-8 text.
    public var body: String
    /// Lowercase hex.
    public var senderPeerIdHex: String
    /// Display metadata only -- see Apps/Chat/ENVELOPE.md §6. Peer clocks
    /// are not synchronized; never use this for cross-peer ordering.
    public var sentAtWallClockMs: Int64
    public var status: ChatMessageDisplayStatus

    public init(
        id: Data,
        direction: Direction,
        body: String,
        senderPeerIdHex: String,
        sentAtWallClockMs: Int64,
        status: ChatMessageDisplayStatus
    ) {
        self.id = id
        self.direction = direction
        self.body = body
        self.senderPeerIdHex = senderPeerIdHex
        self.sentAtWallClockMs = sentAtWallClockMs
        self.status = status
    }
}
