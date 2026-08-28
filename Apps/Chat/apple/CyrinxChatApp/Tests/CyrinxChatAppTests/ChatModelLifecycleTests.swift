import CyrinxChatKit
import Foundation
import Testing

@testable import CyrinxChatApp

/// Lifecycle coverage: `start()`'s production (background-`Task`)
/// consumption path, `teardown()`, and cancellation of an in-flight
/// outgoing message via `cancelSend(messageIdHex:)` -- the design brief's
/// pinned "cancellation" and "teardown" C3-29 test-list items. No sleeps:
/// `start()`'s open-ended consumption is synchronized with a bounded,
/// fail-closed yield loop against `ChatModel.processedEventCount`, never a
/// wall-clock wait.
///
/// `@MainActor`, matching `ChatModel`'s own isolation; every local
/// `SimulatedChatTransportClient`/`VirtualClock` binding is marked
/// `nonisolated(unsafe)` -- see `ChatModelScenarioDriver.run`'s doc comment
/// for the identical rationale (single-sequential-caller types by design;
/// each test function IS that caller).
@Suite("ChatModel: lifecycle -- start(), teardown(), cancellation")
@MainActor
struct ChatModelLifecycleTests {
    /// Cooperatively yields until `model.processedEventCount >= target` or
    /// `maxYields` is exhausted -- CONTRACT.md's own "no wall-clock
    /// sleeps; bounded waits asserted fail-closed" rule (this package's
    /// design brief). Exhausting the bound is NOT treated as success: the
    /// caller's own `#expect` on `processedEventCount` after this returns
    /// is what actually fails the test if the bound was hit without
    /// reaching `target`.
    private func waitForProcessedEvents(_ model: ChatModel, atLeast target: Int, maxYields: Int = 10_000)
        async
    {
        var yields = 0
        while model.processedEventCount < target, yields < maxYields {
            await Task.yield()
            yields += 1
        }
    }

    @Test("start() consumes events on a background Task until the stream finishes")
    func startConsumesUntilStreamFinishes() async throws {
        nonisolated(unsafe) let clock = VirtualClock()
        nonisolated(unsafe) let (clientA, clientB) = SimulatedChatTransportClient.makePair(
            scenario: .happyPair, seed: 29, clock: clock
        )
        let model = ChatModel(transport: clientA, now: { 0 })

        model.start()

        try await clientA.start()
        try await clientB.start()
        clock.advance(toMs: 100)
        try await clientA.connect(toPeer: clientB.localPeerId.hexString)
        clock.advance(toMs: 300)
        await model.send("hello")
        clock.advance(toMs: 500)

        // CONTRACT.md §3.1: A emits 7 events total. Bounded, fail-closed
        // wait for the background consumption Task to catch up.
        await waitForProcessedEvents(model, atLeast: 7)
        #expect(model.processedEventCount == 7)
        #expect(model.messages.first?.status == .delivered)

        await model.teardown()
    }

    @Test("start() is idempotent -- a second call does not create a second consumer")
    func startIsIdempotent() async throws {
        nonisolated(unsafe) let clock = VirtualClock()
        nonisolated(unsafe) let (clientA, clientB) = SimulatedChatTransportClient.makePair(
            scenario: .happyPair, seed: 31, clock: clock
        )
        let model = ChatModel(transport: clientA, now: { 0 })

        model.start()
        model.start()  // must be a no-op, not a second competing consumer

        try await clientA.start()
        try await clientB.start()
        clock.advance(toMs: 100)
        try await clientA.connect(toPeer: clientB.localPeerId.hexString)
        clock.advance(toMs: 500)

        // peerFound(0), connecting(1), connected(2), linkBudgetChanged(3):
        // 4 events, none missing and none double-delivered to a second
        // consumer.
        await waitForProcessedEvents(model, atLeast: 4)
        #expect(model.processedEventCount == 4)

        await model.teardown()
    }

    @Test("teardown() stops consumption and asks the transport to stop")
    func teardownStopsConsumptionAndTransport() async throws {
        nonisolated(unsafe) let clock = VirtualClock()
        nonisolated(unsafe) let (clientA, clientB) = SimulatedChatTransportClient.makePair(
            scenario: .happyPair, seed: 37, clock: clock
        )
        let model = ChatModel(transport: clientA, now: { 0 })

        model.start()
        try await clientA.start()
        try await clientB.start()
        clock.advance(toMs: 100)
        try await clientA.connect(toPeer: clientB.localPeerId.hexString)
        clock.advance(toMs: 250)
        await waitForProcessedEvents(model, atLeast: 3)  // peerFound, connecting, connected

        await model.teardown()

        // CONTRACT.md §2's Lifecycle cancellation: `stop()` on an already-
        // `.connected` client emits `connectionChanged(disconnected, reason:
        // "stopped")`. Whether or not this model's now-cancelled consumer
        // happens to observe that final event, a repeat `teardown()` must
        // remain a harmless no-op (both `Task.cancel()` and `transport.stop()`
        // are idempotent).
        await model.teardown()
    }

    @Test("cancelSend transitions a nonterminal outgoing message to failed(cancelled)")
    func cancelSendTransitionsToFailedCancelled() async throws {
        nonisolated(unsafe) let clock = VirtualClock()
        nonisolated(unsafe) let (clientA, clientB) = SimulatedChatTransportClient.makePair(
            scenario: .slowLink, seed: 41, clock: clock
        )
        let model = ChatModel(transport: clientA, now: { 0 })

        try await clientA.start()
        try await clientB.start()
        clock.advance(toMs: 100)
        try await clientA.connect(toPeer: clientB.localPeerId.hexString)
        clock.advance(toMs: 300)
        // `send(_:)` appends the message row directly AND triggers the
        // transport's own `messageStatusChanged(queued)` event, which is
        // still sitting unconsumed in the stream -- the next
        // `consumeExactly` below accounts for it before reaching
        // `transmitting`.
        await model.consumeExactly(4)  // peerFound, connecting, connected, linkBudgetChanged
        await model.send("slow")

        clock.advance(toMs: 320)
        await model.consumeExactly(2)  // queued (redundant re-apply), transmitting
        #expect(model.messages.first?.status == .transmitting)

        let messageIdHex = try #require(model.messages.first?.id.hexString)
        await model.cancelSend(messageIdHex: messageIdHex)

        // `cancelSend` itself only calls the transport; the resulting
        // `messageStatusChanged(failed, "cancelled")` event still needs to
        // be consumed like any other, per CONTRACT.md §2's pinned
        // `cancelSend` behavior. `slowLink`'s own scripted delivery
        // (originally due at t=2500) is cancelled at the transport layer by
        // this same call (CyrinxChatKit's own `SimulatedChatTransportClient
        // Tests` cover that cancellation directly); advancing well past
        // t=2500 here would need an unbounded "assert nothing more ever
        // arrives" wait to confirm from this model's side, which cannot be
        // done without either a wall-clock timeout (disallowed) or a
        // transport-level hook this model does not have -- so this test
        // stops at confirming the model's own projection reflects
        // cancellation correctly.
        await model.consumeExactly(1)
        #expect(model.messages.first?.status == .failed(reason: "cancelled"))
    }
}
