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
    /// CONTRACT.md §2's "Lifecycle cancellation (pinned)" bullet
    /// "`disconnect()` is terminal for the client instance": thrown by
    /// `connect()`/`send()` when THIS client is terminal (its own
    /// `disconnect()` or `stop()` already ran -- the only call still
    /// permitted afterward is `stop()` itself), and by `connect()` when
    /// its `toPeer:` TARGET is terminal ("a peer's connect() targeting a
    /// terminal client is rejected (thrown to the caller) and schedules
    /// nothing on either side").
    case terminal
    /// CONTRACT.md §2's "Command ownership (pinned)" bullet (C3-28
    /// round-5 fix): "Public commands ... are owned by one caller at a
    /// time ... implementations detect concurrent public-command entry
    /// deterministically and reject it as the concurrent-command
    /// transport-misuse error (thrown where the signature permits, a
    /// documented deterministic trap otherwise)." Thrown by `start()`,
    /// `connect()`, and `send()` (all three can throw) when a SECOND
    /// public command tries to enter this same client instance's command
    /// span while a FIRST one -- on any thread, including this one
    /// re-entrantly -- is still inside its own span (`SimulatedChat
    /// TransportClient`'s `commandLock`/`commandActive` guard, see that
    /// type's "Command ownership enforcement" doc comment). `stop()`,
    /// `disconnect()`, and `cancelSend(messageIdHex:)` cannot throw (the
    /// `ChatTransportClient` protocol signature doesn't permit it), so
    /// they report this same case through
    /// `SimulatedChatTransportClient.trapConcurrentCommandMisuse()`
    /// instead and return having done nothing -- **C3-28 round 6:** by
    /// default (`misuseHandler == nil`, every production caller) that is a
    /// `preconditionFailure` -- the "documented deterministic trap" the
    /// pin's parenthetical allows for a non-throwing signature -- not a
    /// silent no-op; `misuseHandler`, when installed, is a test-only
    /// escape hatch that substitutes for the trap so this same case can be
    /// observed without crashing the test process.
    case concurrentCommand
}
