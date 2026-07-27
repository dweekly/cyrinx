import Foundation

/// Minimal, hand-rolled JSON text builder for `ChatTraceRecord`. A
/// `Codable`-driven `JSONEncoder` was deliberately not used here: this
/// package needs exact, guaranteed field order and formatting (CONTRACT.md
/// §4's canonical field order, spelled out per event `type`) to satisfy the
/// byte-identical trace requirement, and `JSONEncoder`'s key ordering is an
/// implementation detail of `Codable` synthesis, not a documented contract
/// this package should depend on. Formatting matches CONTRACT.md §4's own
/// JSON example verbatim: `": "` after keys, `", "` between fields, no
/// other whitespace.
enum ChatTraceJSON {
    static func string(_ value: String) -> String {
        var out = "\""
        for scalar in value.unicodeScalars {
            switch scalar {
            case "\"":
                out += "\\\""
            case "\\":
                out += "\\\\"
            case "\n":
                out += "\\n"
            case "\r":
                out += "\\r"
            case "\t":
                out += "\\t"
            default:
                if scalar.value < 0x20 {
                    out += String(format: "\\u%04x", scalar.value)
                } else {
                    out.unicodeScalars.append(scalar)
                }
            }
        }
        out += "\""
        return out
    }

    static func stringOrNull(_ value: String?) -> String {
        value.map(string) ?? "null"
    }

    static func int(_ value: Int) -> String { String(value) }
    static func intOrNull(_ value: Int?) -> String { value.map { String($0) } ?? "null" }
    static func int64(_ value: Int64) -> String { String(value) }
    static func uint64(_ value: UInt64) -> String { String(value) }

    /// `Double` -> JSON number text via Swift's default `String`
    /// description, which produces the shortest round-trippable
    /// representation. Verified empirically (see the C3-28 spec-stage
    /// report) against every `confidence` value CONTRACT.md §3's tables
    /// pin (0.7, 0.4, 0.65, 0.5): all format as the plain literal with no
    /// floating-point noise.
    static func double(_ value: Double) -> String { String(value) }

    /// Builds `{"key1": value1, "key2": value2, ...}` from already-encoded
    /// `(key, JSON-value-text)` pairs, in the given order.
    static func object(_ fields: [(String, String)]) -> String {
        "{" + fields.map { "\(string($0.0)): \($0.1)" }.joined(separator: ", ") + "}"
    }

    static func encodePeer(_ peer: ChatPeer) -> String {
        object([
            ("idHex", string(peer.id.hexString)),
            ("displayName", string(peer.displayName)),
            ("discoveredAtMs", int64(peer.discoveredAtMs)),
        ])
    }

    static func encodeLinkBudget(_ budget: ChatLinkBudget) -> String {
        object([
            ("classification", string(budget.classification.rawValue)),
            ("txLowerBoundBps", intOrNull(budget.txLowerBoundBps)),
            ("rxLowerBoundBps", intOrNull(budget.rxLowerBoundBps)),
            ("confidence", double(budget.confidence)),
            ("ageMs", int(budget.ageMs)),
        ])
    }

    static func encodeMessage(_ message: ChatMessage) -> String {
        let (statusStr, _) = statusStrings(message.status)
        return object([
            ("idHex", string(message.id.hexString)),
            ("sequence", uint64(message.sequence)),
            ("direction", string(message.direction == .incoming ? "incoming" : "outgoing")),
            ("body", string(message.body)),
            ("senderPeerIdHex", string(message.senderPeerIdHex)),
            ("sentAtWallClockMs", int64(message.sentAtWallClockMs)),
            ("status", string(statusStr)),
        ])
    }

    static func connectionStateStrings(_ state: ChatConnectionState) -> (state: String, reason: String?) {
        switch state {
        case .disconnected(let reason): return ("disconnected", reason)
        case .connecting: return ("connecting", nil)
        case .connected: return ("connected", nil)
        case .degraded: return ("degraded", nil)
        }
    }

    static func statusStrings(
        _ status: ChatMessageDisplayStatus
    ) -> (status: String, failureReason: String?) {
        switch status {
        case .queued: return ("queued", nil)
        case .transmitting: return ("transmitting", nil)
        case .delivered: return ("delivered", nil)
        case .failed(let reason): return ("failed", reason)
        }
    }

    /// Encodes a `ChatEvent.Kind` payload as `{"type": ..., <fields>}`,
    /// per-type field order from CONTRACT.md §4's table.
    static func encodeEventKind(_ kind: ChatEvent.Kind) -> String {
        switch kind {
        case .peerFound(let peer):
            return object([("type", string("peerFound")), ("peer", encodePeer(peer))])
        case .peerUpdated(let peer):
            return object([("type", string("peerUpdated")), ("peer", encodePeer(peer))])
        case .peerLost(let peerIdHex, let reason):
            return object([
                ("type", string("peerLost")),
                ("peerIdHex", string(peerIdHex)),
                ("reason", string(reason)),
            ])
        case .connectionChanged(let state):
            let (stateStr, reason) = connectionStateStrings(state)
            return object([
                ("type", string("connectionChanged")),
                ("state", string(stateStr)),
                ("reason", stringOrNull(reason)),
            ])
        case .linkBudgetChanged(let budget):
            return object([("type", string("linkBudgetChanged")), ("budget", encodeLinkBudget(budget))])
        case .messageReceived(let message):
            return object([("type", string("messageReceived")), ("message", encodeMessage(message))])
        case .messageGap(let fromSequence, let toSequence):
            return object([
                ("type", string("messageGap")),
                ("fromSequence", uint64(fromSequence)),
                ("toSequence", uint64(toSequence)),
            ])
        case .messageStatusChanged(let messageIdHex, let status):
            let (statusStr, failureReason) = statusStrings(status)
            return object([
                ("type", string("messageStatusChanged")),
                ("messageIdHex", string(messageIdHex)),
                ("status", string(statusStr)),
                ("failureReason", stringOrNull(failureReason)),
            ])
        case .clientFailed(let reason):
            return object([("type", string("clientFailed")), ("reason", string(reason))])
        }
    }
}

/// One JSON-lines trace record: `(eventSeq, virtualTimeMs, event)` per
/// CONTRACT.md §4, plus the `client` ("A"/"B") field CONTRACT.md pins
/// between `virtualTimeMs` and `event` in the canonical top-level field
/// order.
public struct ChatTraceRecord: Equatable, Sendable {
    public let eventSeq: UInt64
    public let virtualTimeMs: Int64
    public let client: ChatClientRole
    public let event: ChatEvent.Kind

    public init(eventSeq: UInt64, virtualTimeMs: Int64, client: ChatClientRole, event: ChatEvent.Kind) {
        self.eventSeq = eventSeq
        self.virtualTimeMs = virtualTimeMs
        self.client = client
        self.event = event
    }

    /// One canonical JSON-lines line (no trailing newline). Field order:
    /// `eventSeq, virtualTimeMs, client, event` (CONTRACT.md §4).
    public func canonicalJSONLine() -> String {
        ChatTraceJSON.object([
            ("eventSeq", ChatTraceJSON.uint64(eventSeq)),
            ("virtualTimeMs", ChatTraceJSON.int64(virtualTimeMs)),
            ("client", ChatTraceJSON.string(client.rawValue)),
            ("event", ChatTraceJSON.encodeEventKind(event)),
        ])
    }
}
