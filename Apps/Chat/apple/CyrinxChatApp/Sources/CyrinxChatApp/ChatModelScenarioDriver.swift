import CyrinxChatKit
import Foundation

/// Drives one of CONTRACT.md §3's six pinned scenario timelines against a
/// freshly constructed `SimulatedChatTransportClient` pair and a
/// caller-supplied `ChatModel` attached to one side, reproducing the exact
/// "driver:" actions and virtual-clock offsets `CyrinxChatKit`'s own
/// (package-internal) `ChatScenarioRunner` already uses for every one of
/// the six tables.
///
/// This logic is duplicated here, not imported, because `ChatScenarioRunner`
/// and the scenario facts it reads off `ChatScenario`
/// (`hasSendStep`/`canonicalSendBody`/`scriptEndMs`, Apps/Chat/CyrinxChatKit/
/// Sources/CyrinxChatKit/ChatScenario.swift) are package-internal to
/// `CyrinxChatKit`, not part of its public API surface -- a separate SwiftPM
/// module (this package) can only see what that package marks `public`.
/// Every fact below is cited to CONTRACT.md §3 directly, matching how
/// `ChatScenarioRunner` itself already cites the identical values. Shared by
/// both `chat-model-trace-gen` (the production tool) and this package's own
/// Swift Testing model-test suite, so the driver logic lives in exactly one
/// place.
public enum ChatModelScenarioDriver {
    /// CONTRACT.md §3.1-3.6: `A.connect(toPeer:)` fires at t=100 in every
    /// one of the six scenario tables.
    public static let connectAtMs: Int64 = 100
    /// CONTRACT.md §3.1, 3.4-3.6: `A.send(body:)` fires at t=300 in every
    /// scenario whose table has a send row.
    public static let sendAtMs: Int64 = 300

    /// Per-scenario facts needed to drive it, that `CyrinxChatKit` does not
    /// expose publicly. See `facts(for:)` for the CONTRACT.md §3 citation
    /// backing each value.
    public struct Facts: Equatable, Sendable {
        public let hasSendStep: Bool
        public let sendBody: String
        /// A safe virtual-time target past the table's last pinned event,
        /// so every scheduled action has fired by the time the driver stops
        /// advancing.
        public let scriptEndMs: Int64
        /// Client A's own event count strictly BEFORE the scripted send's
        /// `messageStatusChanged(queued)` event -- i.e. every post-connect
        /// step CONTRACT.md §3's table places before the send row (`0` when
        /// `hasSendStep` is false). `run(...)` drains exactly this many
        /// events BEFORE issuing the scripted send through `ChatModel.send
        /// (_:)`, so that call's own direct, out-of-band `messages` append
        /// (see `ChatModel.send(_:)`'s doc comment: it mutates `messages`
        /// directly, not via `apply(_:)`) lands at the correct point in the
        /// replayed sequence instead of jumping ahead of every event that
        /// logically precedes it.
        public let clientAEventCountBeforeSend: Int
        /// Client A's own exact pinned event count -- CONTRACT.md §3's
        /// "Total events: X (A) + Y (B)" line for this scenario.
        public let clientAEventCount: Int
        /// Client B's own exact pinned event count -- same source.
        public let clientBEventCount: Int
    }

    public static func facts(for scenario: ChatScenario) -> Facts {
        switch scenario {
        case .happyPair:
            // CONTRACT.md §3.1: "Total events: 7 (A) + 3 (B) = 10." A's 4
            // pre-send events: peerFound(0), connecting(1), connected(2),
            // linkBudgetChanged(3) -- eventSeq4 is the send's own queued.
            return Facts(
                hasSendStep: true, sendBody: "hello", scriptEndMs: 500,
                clientAEventCountBeforeSend: 4, clientAEventCount: 7, clientBEventCount: 3
            )
        case .peerLoss:
            // CONTRACT.md §3.2: "Total events: 5 (A) + 4 (B) = 9." No send.
            return Facts(
                hasSendStep: false, sendBody: "", scriptEndMs: 600,
                clientAEventCountBeforeSend: 0, clientAEventCount: 5, clientBEventCount: 4
            )
        case .degradedThenRecovered:
            // CONTRACT.md §3.3: "Total events: 8 (A) + 2 (B) = 10." No send.
            return Facts(
                hasSendStep: false, sendBody: "", scriptEndMs: 800,
                clientAEventCountBeforeSend: 0, clientAEventCount: 8, clientBEventCount: 2
            )
        case .sendFailure:
            // CONTRACT.md §3.4: "Total events: 6 (A) + 2 (B) = 8." A's 3
            // pre-send events: peerFound(0), connecting(1), connected(2) --
            // no linkBudgetChanged row in this table before the send.
            return Facts(
                hasSendStep: true, sendBody: "will-fail", scriptEndMs: 550,
                clientAEventCountBeforeSend: 3, clientAEventCount: 6, clientBEventCount: 2
            )
        case .duplicateIncoming:
            // CONTRACT.md §3.5: "Total events: 6 (A) + 3 (B) = 9." (B's 3
            // already reflects transport-layer dedup -- exactly one
            // messageReceived, not two.) A's 3 pre-send events, same shape
            // as sendFailure's.
            return Facts(
                hasSendStep: true, sendBody: "dup-test", scriptEndMs: 500,
                clientAEventCountBeforeSend: 3, clientAEventCount: 6, clientBEventCount: 3
            )
        case .slowLink:
            // CONTRACT.md §3.6: "Total events: 7 (A) + 3 (B) = 10." A's 4
            // pre-send events: peerFound(0), connecting(1), connected(2),
            // linkBudgetChanged(3, controlOnly@160).
            return Facts(
                hasSendStep: true, sendBody: "slow", scriptEndMs: 2600,
                clientAEventCountBeforeSend: 4, clientAEventCount: 7, clientBEventCount: 3
            )
        }
    }

    /// Runs `scenario`'s canonical driver script against a fresh pair.
    /// `makeModel` receives whichever client `attachTo` selects and must
    /// return a `ChatModel` wrapping it; when `attachTo == .a`, the scripted
    /// send (if any) is issued through that model's own `send(_:)` --
    /// exercising the real "append at acceptance" application path exactly
    /// as the production app would -- rather than calling the transport
    /// directly; client A's own `send(body:)` is used when attached to B,
    /// since B's model only ever observes the exchange, never originates
    /// it.
    ///
    /// Deliberately never calls `stop()` on either client: no
    /// lifecycle-cleanup event (CONTRACT.md §2's `stop()`-only
    /// `connectionChanged(disconnected, reason: "stopped")`) ever reaches
    /// the model this way. A caller that also wants to exercise
    /// `stop()`/`teardown()` drives that itself, afterward, over the
    /// returned pair.
    ///
    /// `@MainActor`: `ChatModel` (built by `makeModel`, and returned) is
    /// itself `@MainActor`-isolated, so this function has to be too --
    /// otherwise `makeModel`'s call site can't construct a `ChatModel`
    /// without an extra hop. `clock`/`clientA`/`clientB` are marked
    /// `nonisolated(unsafe)`: `SimulatedChatTransportClient`/`VirtualClock`
    /// are plain, non-`Sendable`, single-sequential-caller types by design
    /// (see their own doc comments), and this function IS that single
    /// sequential caller -- every use of them below is strictly sequential,
    /// so the annotation states an existing invariant rather than
    /// introducing a new risk (verified empirically: `swift build` fails
    /// with Swift 6's "sending risks a data race" at every one of these
    /// call sites without it, matching `ChatModel.transport`'s own,
    /// identically-justified annotation).
    @discardableResult
    @MainActor
    public static func run(
        scenario: ChatScenario,
        seed: UInt64,
        attachTo: ChatClientRole = .a,
        makeModel: (any ChatTransportClient) -> ChatModel
    ) async throws -> (
        model: ChatModel, clientA: SimulatedChatTransportClient, clientB: SimulatedChatTransportClient
    ) {
        nonisolated(unsafe) let clock = VirtualClock()
        nonisolated(unsafe) let (clientA, clientB) = SimulatedChatTransportClient.makePair(
            scenario: scenario, seed: seed, clock: clock
        )
        let model = makeModel(attachTo == .a ? clientA : clientB)
        let scenarioFacts = facts(for: scenario)

        // "0 | -> | -- | driver: A.start(), B.start(); simulator pairs A+B"
        // (identical opening row in every one of CONTRACT.md §3.1-3.6).
        try await clientA.start()
        try await clientB.start()

        // "100 | -> | -- | driver: A.connect(toPeer: peerB.idHex)".
        clock.advance(toMs: connectAtMs)
        try await clientA.connect(toPeer: clientB.localPeerId.hexString)

        var alreadyConsumedByA = 0
        if scenarioFacts.hasSendStep {
            // "300 | -> | -- | driver: A.send(body: ...) -> returns msg1/msgDup".
            clock.advance(toMs: sendAtMs)
            if attachTo == .a {
                // Drain every event that logically precedes the send FIRST
                // -- see `Facts.clientAEventCountBeforeSend`'s doc comment
                // for why this ordering matters.
                await model.consumeExactly(scenarioFacts.clientAEventCountBeforeSend)
                alreadyConsumedByA = scenarioFacts.clientAEventCountBeforeSend
                await model.send(scenarioFacts.sendBody)
            } else {
                _ = try await clientA.send(body: scenarioFacts.sendBody)
            }
        }

        clock.advance(toMs: scenarioFacts.scriptEndMs)

        let expectedCount = attachTo == .a ? scenarioFacts.clientAEventCount : scenarioFacts.clientBEventCount
        await model.consumeExactly(expectedCount - alreadyConsumedByA)

        return (model, clientA, clientB)
    }
}
