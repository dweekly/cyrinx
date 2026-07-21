/// Simulator-level *transport-misuse* error cases for
/// `SimulatedChatTransportClient` -- i.e. exactly the type CONTRACT.md §2's
/// "Send precondition (pinned)" means by "throws/raises the
/// transport-misuse 'not connected' error" (`.notConnected` below). NOT
/// part of the six-case `ChatEnvelopeError` wire taxonomy (ENVELOPE.md §5)
/// -- these are ad hoc client/protocol-usage errors (calling an API method
/// when it wasn't valid to do so, e.g. connecting to an unknown peer or
/// sending while disconnected) that CONTRACT.md leaves otherwise unpinned.
/// None of the six pinned scenario scripts (CONTRACT.md §3) exercise these
/// paths; they exist so the simulated client behaves sensibly (throws
/// rather than crashing or silently doing nothing) when driven outside
/// those six scripts.
public enum ChatSimulatedTransportError: Error, Equatable, Sendable {
    /// `connect(toPeer:)` was called with an `idHex` that does not match
    /// this client's currently discovered peer (or no peer has been
    /// discovered yet).
    case unknownPeer
    /// `send(body:)` was called while `connectionState` was neither
    /// `.connected` nor `.degraded` -- CONTRACT.md §2's "Send precondition
    /// (pinned)": accepted only in those two states.
    case notConnected
}
