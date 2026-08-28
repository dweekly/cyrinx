import CyrinxChatKit
import Foundation
import Testing

@testable import CyrinxChatApp

/// End-to-end tests driving `ChatModel` against the real
/// `SimulatedChatTransportClient`, through `ChatModelScenarioDriver`, for
/// every one of CONTRACT.md §3's six pinned scenarios where this model's
/// own behavior (not already covered by `CyrinxChatKit`'s own
/// `ChatScenarioRunnerTests`) needs checking: duplicate suppression
/// display, peer loss, send failure, degraded/recovered, and a
/// mid-scenario (not just final-state) slow-link check. All virtual time
/// via `VirtualClock`; no sleeps anywhere in this file.
///
/// `@MainActor`, matching `ChatModel`'s own isolation; the two tests below
/// that construct `SimulatedChatTransportClient`/`VirtualClock` directly
/// (rather than through `ChatModelScenarioDriver.run`, which already
/// handles this internally) mark those specific local bindings
/// `nonisolated(unsafe)` -- see `ChatModelScenarioDriver.run`'s doc comment
/// for the identical rationale (single-sequential-caller types by design;
/// this test function IS that caller).
@Suite("ChatModel: scenario coverage over the real simulated transport")
@MainActor
struct ChatModelScenarioTests {
    @Test("happyPair: final projection matches CONTRACT.md §3.1 exactly")
    func happyPairFinalState() async throws {
        let (model, _, clientB) = try await ChatModelScenarioDriver.run(scenario: .happyPair, seed: 7) {
            ChatModel(transport: $0, now: { 0 })
        }

        #expect(model.peers.map { $0.id.hexString } == [clientB.localPeerId.hexString])
        #expect(model.connection == .connected)
        #expect(model.budget.classification == .text)
        #expect(model.budget.confidence == 0.7)
        #expect(model.messages.count == 1)
        #expect(model.messages.first?.direction == .outgoing)
        #expect(model.messages.first?.body == "hello")
        #expect(model.messages.first?.status == .delivered)
        #expect(model.banner == nil)
        #expect(model.eventSeqGapDetected == false)
        #expect(model.droppedStatusUpdates == 0)
    }

    @Test("peerLoss: peer removed, connection disconnected, recovery-guidance banner shown")
    func peerLossFinalState() async throws {
        let (model, _, _) = try await ChatModelScenarioDriver.run(scenario: .peerLoss, seed: 11) {
            ChatModel(transport: $0, now: { 0 })
        }

        #expect(model.peers.isEmpty)
        #expect(model.connection == .disconnected(reason: "peerSilenceTimeout"))
        #expect(model.banner == "Peer stopped responding. Move the devices closer and reconnect.")
        #expect(model.messages.isEmpty)
    }

    @Test("sendFailure: outgoing message ends failed(noAcknowledgment), B never receives it")
    func sendFailureFinalState() async throws {
        let (model, _, _) = try await ChatModelScenarioDriver.run(scenario: .sendFailure, seed: 13) {
            ChatModel(transport: $0, now: { 0 })
        }

        #expect(model.messages.count == 1)
        #expect(model.messages.first?.status == .failed(reason: "noAcknowledgment"))
        // A synchronous throw is what produces `composerError`; a scripted
        // async status transition to `failed` is a normal message-row
        // update, not a composer error.
        #expect(model.composerError == nil)
    }

    @Test("duplicateIncoming, model attached to B: exactly one incoming message row, not two")
    func duplicateIncomingDisplaysExactlyOnce() async throws {
        let (model, _, _) = try await ChatModelScenarioDriver.run(
            scenario: .duplicateIncoming, seed: 17, attachTo: .b
        ) { ChatModel(transport: $0, now: { 0 }) }

        let incoming = model.messages.filter { $0.direction == .incoming }
        #expect(incoming.count == 1)
        #expect(incoming.first?.body == "dup-test")
        #expect(incoming.first?.status == .delivered)
    }

    @Test("degradedThenRecovered: banner shows degraded mid-scenario, clears back to nil once recovered")
    func degradedThenRecoveredBannerSequence() async throws {
        // Manual construction (not `ChatModelScenarioDriver.run`, whose
        // single final consumption point can't observe the mid-scenario
        // degraded banner) -- CONTRACT.md §3.3: linkBudget(text)@200,
        // degraded@400, linkBudget(controlOnly)@410, connected@700,
        // linkBudget(text)@710.
        nonisolated(unsafe) let clock = VirtualClock()
        nonisolated(unsafe) let (clientA, clientB) = SimulatedChatTransportClient.makePair(
            scenario: .degradedThenRecovered, seed: 19, clock: clock
        )
        let model = ChatModel(transport: clientA, now: { 0 })

        try await clientA.start()
        try await clientB.start()
        clock.advance(toMs: 100)
        try await clientA.connect(toPeer: clientB.localPeerId.hexString)

        clock.advance(toMs: 250)
        await model.consumeExactly(4)  // peerFound, connecting, connected, linkBudget(text)
        #expect(model.banner == nil)

        clock.advance(toMs: 450)
        await model.consumeExactly(1)  // degraded
        #expect(model.banner == "Link degraded — move devices closer")
        #expect(model.connection == .degraded)

        clock.advance(toMs: 800)
        await model.consumeExactly(3)  // linkBudget(controlOnly), connected, linkBudget(text)
        #expect(model.banner == nil)
        #expect(model.connection == .connected)
        #expect(model.budget.classification == .text)
        #expect(model.budget.confidence == 0.65)
    }

    @Test("slowLink observes transmitting (not a terminal state) mid-scenario, then delivered at the end")
    func slowLinkDwellsInTransmittingMidScenario() async throws {
        // Manual construction (not `ChatModelScenarioDriver.run`, which
        // only exposes a single final consumption point): this test needs
        // to inspect state strictly between `transmitting` and `delivered`,
        // at t=1000 -- CONTRACT.md §3.6: transmitting@320, B receives@2450,
        // A delivered@2500.
        nonisolated(unsafe) let clock = VirtualClock()
        nonisolated(unsafe) let (clientA, clientB) = SimulatedChatTransportClient.makePair(
            scenario: .slowLink, seed: 23, clock: clock
        )
        let model = ChatModel(transport: clientA, now: { 0 })

        try await clientA.start()
        try await clientB.start()
        clock.advance(toMs: 100)
        try await clientA.connect(toPeer: clientB.localPeerId.hexString)
        clock.advance(toMs: 300)
        // Exercise the model's own send path (not the raw transport), per
        // `ChatModelScenarioDriver`'s own rationale for doing so. `send(_:)`
        // itself appends the message row directly (status `.queued`) AND
        // triggers the transport's own `messageStatusChanged(queued)`
        // event, which is still sitting unconsumed in the stream -- the
        // next `consumeExactly` below accounts for it before reaching
        // `transmitting`.
        await model.consumeExactly(4)  // peerFound, connecting, connected, linkBudgetChanged
        await model.send("slow")

        clock.advance(toMs: 1_000)
        await model.consumeExactly(2)  // queued@300 (redundant re-apply), transmitting@320
        #expect(model.messages.first?.status == .transmitting)

        clock.advance(toMs: 2_600)
        await model.consumeExactly(1)  // delivered@2500
        #expect(model.messages.first?.status == .delivered)
    }
}
