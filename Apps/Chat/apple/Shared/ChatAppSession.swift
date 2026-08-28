import CyrinxChatApp
import CyrinxChatKit
import Foundation
import Observation

/// Owns one simulated `ChatTransportClient` pair (`SimulatedChatTransportClient
/// .makePair`) and the `ChatModel` wrapping client A, and continuously
/// advances the shared `VirtualClock` in step with real elapsed wall-clock
/// time so CONTRACT.md §3's scenario scripts (peer discovery, post-connect
/// link-budget/degraded/recovered/peer-loss timelines) play out at a
/// natural pace in the running app -- unlike `CyrinxChatApp`'s own tests
/// and `chat-model-trace-gen`, which jump the clock straight to fixed
/// checkpoints, a live, interactive app has no fixed checkpoints to jump
/// to; the user decides when to connect and send.
///
/// Every outgoing message the user actually composes is sent through
/// `ChatModel.send(_:)` with whatever text they typed. CONTRACT.md §3's
/// scripted send bodies ("hello", "will-fail", ...) are specific to the
/// deterministic test/trace-gen harness (`ChatModelScenarioDriver`), not a
/// constraint on this live session: `SimulatedChatTransportClient`'s
/// per-scenario send outcome (deliver normally / fail / duplicate-then-
/// dedup / slow) is keyed only by scenario, never by body text (Apps/Chat/
/// CyrinxChatKit/Sources/CyrinxChatKit/ChatScenario.swift's `sendOutcome`),
/// so the user can compose and send as many messages as they like and each
/// one follows that scenario's scripted outcome shape.
///
/// **Narrator send (this app's own addition, not part of CONTRACT.md).**
/// When attached to B (`ChatAppOnlyLaunchArgs.attachTo`), no CONTRACT.md §3
/// scenario script ever has A originate a message on its own -- `send()`
/// is always an external driver action, never something
/// `SimulatedChatTransportClient` does autonomously. So that this sample's
/// "receive" flow (attach to B, watch a message arrive without the user
/// doing anything) has something to show, this session plays the part of
/// that external driver for whichever side the user is NOT attached to:
/// once the ATTACHED side's own connection first reaches `.connected`, and
/// only if the active scenario has a scripted send at all
/// (`ChatModelScenarioDriver.facts(for:).hasSendStep`), it calls
/// `send(body:)` once on the OTHER (narrator) client after a short delay.
/// This never happens when attached to A (the default): A is always the
/// scripted sender already, driven by the user's own composer.
@MainActor
@Observable
final class ChatAppSession {
    let model: ChatModel
    let options: ChatLaunchArgOptions

    /// `nonisolated(unsafe)`: see `ChatModel.transport`'s doc comment
    /// (`CyrinxChatApp` package) for the identical rationale --
    /// `VirtualClock`/`SimulatedChatTransportClient` are plain,
    /// non-`Sendable`, single-sequential-caller types by design, and this
    /// session (always driven on `@MainActor`, per this class's own
    /// isolation) is that single caller.
    private nonisolated(unsafe) let clock: VirtualClock
    private nonisolated(unsafe) let clientA: SimulatedChatTransportClient
    private nonisolated(unsafe) let clientB: SimulatedChatTransportClient
    /// The client this session's `model` is NOT attached to -- see the
    /// "Narrator send" section of this type's own doc comment.
    private nonisolated(unsafe) let narratorClient: SimulatedChatTransportClient

    private var tickTask: Task<Void, Never>?
    private var narratorSendTask: Task<Void, Never>?
    private var hasScheduledNarratorSend = false

    /// Real-time tick granularity for the virtual-clock-pacing loop: 50
    /// ticks/second (20 ms) is smooth enough for UI state changes
    /// (connection banner, link-budget badge, message status) to read as
    /// animated rather than jumpy, while cheap enough to run for an entire
    /// app session. Not derived from any CONTRACT.md-pinned value -- purely
    /// a UI-smoothness heuristic for this sample, safe to retune.
    private static let tickInterval = Duration.milliseconds(20)

    /// Real-time delay, after the attached side's connection first reaches
    /// `.connected`, before the narrator client sends -- approximates
    /// CONTRACT.md §3's own `sendAtMs (300) - connectAtMs (100) -
    /// connectHandshakeDelayMs (50) = 150` gap between `connected` and the
    /// scripted send in every one of its four send-having scenario tables.
    /// Not itself CONTRACT.md-pinned (the narrator mechanism is this app's
    /// own addition, not a scenario script), but the delay is chosen to
    /// echo the shape of the real pinned timelines rather than being
    /// arbitrary.
    private static let narratorSendDelay = Duration.milliseconds(150)

    init(
        options: ChatLaunchArgOptions = ChatLaunchArgParsing.parse(),
        attachToB: Bool = ChatAppOnlyLaunchArgs.parseAttachToB()
    ) {
        self.options = options
        let clock = VirtualClock()
        self.clock = clock
        let pair = SimulatedChatTransportClient.makePair(
            scenario: options.scenario, seed: options.seed, clock: clock
        )
        self.clientA = pair.a
        self.clientB = pair.b
        // See `ChatAppOnlyLaunchArgs.attachTo`'s doc comment: attaching to
        // B (rather than the default A) is this sample's own affordance
        // for exercising the "receive an incoming message" flow, since no
        // CONTRACT.md §3 scenario script ever has A receive one.
        self.model = ChatModel(transport: attachToB ? pair.b : pair.a)
        self.narratorClient = attachToB ? pair.a : pair.b
    }

    /// Starts model event consumption, starts both simulated clients, and
    /// begins the real-time clock-pacing loop. Idempotent -- a second call
    /// is a no-op. Call once, e.g. from the root view's `.task`.
    func start() {
        guard tickTask == nil else { return }
        model.start()
        let referenceInstant = Date()

        tickTask = Task { [weak self] in
            guard let self else { return }
            do {
                try await self.clientA.start()
                try await self.clientB.start()
            } catch {
                // `start()` only throws for an already-terminal client
                // (CONTRACT.md §2), which a freshly constructed pair never
                // is.
                return
            }
            while !Task.isCancelled {
                try? await Task.sleep(for: Self.tickInterval)
                guard !Task.isCancelled else { break }
                let elapsedMs = Int64(Date().timeIntervalSince(referenceInstant) * 1_000)
                self.clock.advance(toMs: max(elapsedMs, self.clock.nowMs))
                self.scheduleNarratorSendIfNewlyConnected()
            }
        }
    }

    /// See this type's own "Narrator send" doc comment. Checked once per
    /// tick; only ever schedules the narrator's send the FIRST time
    /// `model.connection` is observed `.connected` (`hasScheduledNarratorSend`
    /// latches so a later disconnect/reconnect never re-fires it).
    private func scheduleNarratorSendIfNewlyConnected() {
        guard !hasScheduledNarratorSend, case .connected = model.connection else { return }
        hasScheduledNarratorSend = true
        let facts = ChatModelScenarioDriver.facts(for: options.scenario)
        guard facts.hasSendStep else { return }
        narratorSendTask = Task { [weak self] in
            guard let self else { return }
            try? await Task.sleep(for: Self.narratorSendDelay)
            guard !Task.isCancelled else { return }
            _ = try? await self.narratorClient.send(body: facts.sendBody)
        }
    }

    /// Cancels the pacing loop and tears down the model/transport.
    /// Idempotent.
    func stop() async {
        tickTask?.cancel()
        tickTask = nil
        narratorSendTask?.cancel()
        narratorSendTask = nil
        await model.teardown()
    }
}
