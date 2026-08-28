import Foundation
import Testing

@testable import CyrinxChatApp
@testable import CyrinxChatKit

/// C3-29 sequence amendment: `ChatModel`'s projection of a REAL `messageGap`
/// `ChatEvent`, produced end-to-end by `CyrinxChatKit`'s own reorder-window
/// machinery -- not a hand-built `ChatEvent.messageGap(...)` (that shape of
/// test already lives in `ChatModelProjectionTests`). `@testable import
/// CyrinxChatKit` is required here (unlike every other file in this test
/// target) to reach `SimulatedChatTransportClient.receiveEnvelope(_:)`,
/// which CONTRACT.md §2 documents as the "test-only injection seam" --
/// package-internal, not part of the public `ChatTransportClient` surface,
/// exactly like `CyrinxChatKit`'s own `ChatSequenceAndReorderTests` (Apps/
/// Chat/CyrinxChatKit/Tests/CyrinxChatKitTests/ChatSequenceAndReorderTests
/// .swift) uses it. This mirrors that file's `craftedEnvelopeBytes` helper
/// rather than importing it (that helper is `private` to its own suite).
///
/// No scenario in CONTRACT.md §3 ever surfaces a `messageGap` (§4's golden
/// trace note: "the injection-seam gap path is covered by unit tests, not
/// scenario goldens") -- this file, not `ChatModelScenarioTests`, is where
/// that coverage lives on the model-projection side.
@Suite("ChatModel: messageGap consumption via CyrinxChatKit's real reorder-window/injection seam")
struct ChatModelMessageGapTests {
    /// Builds valid envelope bytes for a crafted inbound arrival -- same
    /// shape as `ChatSequenceAndReorderTests.craftedEnvelopeBytes`, kept
    /// this test file's own small copy rather than trying to share a
    /// `private` helper across suites/packages.
    private static func craftedEnvelopeBytes(
        senderId: Data, sequence: UInt64, tag: UInt8, body: String = "x"
    ) throws -> Data {
        let envelope = ChatEnvelope(
            messageId: Data(repeating: tag, count: ChatEnvelope.messageIdLength),
            senderId: senderId,
            sequence: sequence,
            body: body
        )
        return try ChatEnvelopeCodec.encode(envelope)
    }

    @Test("a window-bypass arrival surfaces messageGap; ChatModel counts it and produces the pinned caption")
    @MainActor
    func windowBypassMessageGapIsProjectedWithPinnedCaption() async throws {
        nonisolated(unsafe) let clock = VirtualClock()
        nonisolated(unsafe) let (_, clientB) = SimulatedChatTransportClient.makePair(
            scenario: .happyPair, seed: 701, clock: clock
        )
        let model = ChatModel(transport: clientB, now: { 0 })
        let senderId = Data(repeating: 0xCC, count: 4)

        // `nextExpectedIncomingSequence` starts at 1 (CONTRACT.md §2); a
        // reorder window of 32 means sequence 33 is exactly the smallest
        // arrival that bypasses it: `gap = [1, 33 - 32] = [1, 1]`, which
        // advances `nextExpectedIncomingSequence` to `toSequence + 1 == 2`
        // (`SimulatedChatTransportClient.surfaceWindowBypassGap`'s own doc
        // comment: "`arrivingSequence` itself is handled by the caller
        // ... against the now-updated `nextExpectedIncomingSequence`").
        // Verified directly against that method's source: since `33 != 2`,
        // the bypassing arrival itself is buffered (not delivered) by this
        // same `receiveEnvelope` call -- it only becomes a `messageReceived`
        // once sequences 2-32 eventually drain the window, which this test
        // never does. A single `receiveEnvelope` call here therefore
        // produces exactly ONE event: `messageGap`, never a paired
        // `messageReceived`.
        clientB.receiveEnvelope(
            try Self.craftedEnvelopeBytes(senderId: senderId, sequence: 33, tag: 0x21, body: "bypass")
        )

        await model.consumeExactly(1)

        #expect(model.messageGaps == 1)
        let notice = try #require(model.messageGapNotices.first)
        #expect(notice.fromSequence == 1)
        #expect(notice.toSequence == 1)
        #expect(notice.text == "Messages missing: sequences 1-1")
        // The bypassing arrival (sequence 33) is buffered behind the new
        // floor (2), not delivered -- see the comment above -- so no
        // message row exists yet, and the caption is the only
        // `conversationRows` entry.
        #expect(model.messages.isEmpty)
        #expect(
            model.conversationRows == [
                .messageGapNotice(
                    ChatMessageGapNotice(fromSequence: 1, toSequence: 1, precedingMessageCount: 0))
            ])
    }

    @Test("a wider window-bypass renders a multi-sequence 'X-Y' caption, not 'X-X'")
    @MainActor
    func widerWindowBypassRendersRangeCaption() async throws {
        nonisolated(unsafe) let clock = VirtualClock()
        nonisolated(unsafe) let (_, clientB) = SimulatedChatTransportClient.makePair(
            scenario: .happyPair, seed: 702, clock: clock
        )
        let model = ChatModel(transport: clientB, now: { 0 })
        let senderId = Data(repeating: 0xDD, count: 4)

        // Arrival at sequence 40: gap = [1, 40 - 32] = [1, 8]; new floor is
        // 9, so (same reasoning as `windowBypassMessageGapIsProjectedWithPinnedCaption`
        // above) the arrival itself is buffered, not delivered -- exactly
        // one event fires.
        clientB.receiveEnvelope(
            try Self.craftedEnvelopeBytes(senderId: senderId, sequence: 40, tag: 0x22, body: "far-bypass")
        )
        await model.consumeExactly(1)  // messageGap only

        #expect(model.messageGaps == 1)
        #expect(model.messageGapNotices.first?.text == "Messages missing: sequences 1-8")
    }

    /// Cooperatively yields until `model.processedEventCount >= target` or
    /// `maxYields` is exhausted -- CONTRACT.md §2 point 3's "no wall-clock
    /// sleeps; bounded waits asserted fail-closed" rule, same helper shape
    /// as `ChatModelLifecycleTests.waitForProcessedEvents`. Exhausting the
    /// bound is NOT itself success: the caller's own `#expect` after this
    /// returns is what actually fails the test if the bound was hit first.
    @MainActor
    private func waitForProcessedEvents(_ model: ChatModel, atLeast target: Int, maxYields: Int = 10_000)
        async
    {
        var yields = 0
        while model.processedEventCount < target, yields < maxYields {
            await Task.yield()
            yields += 1
        }
    }

    @Test("scope-end (stop()) with an unfilled hole surfaces messageGap too -- CONTRACT.md §2 case (b)")
    @MainActor
    func scopeEndWithUnfilledHoleSurfacesMessageGap() async throws {
        nonisolated(unsafe) let clock = VirtualClock()
        nonisolated(unsafe) let (_, clientB) = SimulatedChatTransportClient.makePair(
            scenario: .happyPair, seed: 703, clock: clock
        )
        let model = ChatModel(transport: clientB, now: { 0 })
        let senderId = Data(repeating: 0xEE, count: 4)

        // Sequence 2 arrives while 1 is still missing -- buffered in the
        // reorder window, not yet delivered, not yet a gap.
        clientB.receiveEnvelope(
            try Self.craftedEnvelopeBytes(senderId: senderId, sequence: 2, tag: 0x23, body: "buffered")
        )
        #expect(model.messageGaps == 0)

        // Ending the scope with sequence 1 still missing surfaces the gap
        // (CONTRACT.md §2's case (b)) instead of leaving it silently
        // unresolved. `stop()` finishes the event stream immediately after
        // emitting `messageGap`, already buffered by the time `model
        // .start()` begins consuming -- `AsyncStream` buffers survive a
        // finish, so the background consumer still drains it before
        // observing completion. Verified against `stop()`'s own source:
        // this client was never `connect()`ed, so `connectionState` is
        // still its initial `.disconnected(reason: nil)`, and `stop()`'s
        // "unless already disconnected" clause means its own
        // `connectionChanged` does NOT fire here -- exactly one event
        // (`messageGap`) is on the stream, not two.
        await clientB.stop()
        model.start()
        await waitForProcessedEvents(model, atLeast: 1)  // messageGap

        #expect(model.messageGaps == 1)
        #expect(model.messageGapNotices.first?.text == "Messages missing: sequences 1-1")
        await model.teardown()
    }
}
