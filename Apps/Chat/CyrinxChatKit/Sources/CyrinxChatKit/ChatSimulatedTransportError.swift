/// Simulator-level error cases for `SimulatedChatTransportClient`. NOT part
/// of the six-case `ChatEnvelopeError` wire taxonomy (ENVELOPE.md §5) --
/// these are ad hoc client/protocol-usage errors (e.g. connecting to an
/// unknown peer) that CONTRACT.md leaves unpinned. None of the six pinned
/// scenario scripts (CONTRACT.md §3) exercise these paths; they exist so
/// the simulated client behaves sensibly (throws rather than crashing or
/// silently doing nothing) when driven outside those six scripts.
public enum ChatSimulatedTransportError: Error, Equatable, Sendable {
    /// `connect(toPeer:)` was called with an `idHex` that does not match
    /// this client's currently discovered peer (or no peer has been
    /// discovered yet).
    case unknownPeer
    /// `send(body:)` was called while not in the `.connected` state.
    case notConnected
}
