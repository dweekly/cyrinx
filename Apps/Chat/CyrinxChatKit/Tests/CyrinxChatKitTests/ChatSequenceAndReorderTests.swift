import Foundation
import Testing

@testable import CyrinxChatKit

/// C3-28 sequence amendment: direct tests of CONTRACT.md §2's "Outgoing
/// sequence assignment (pinned)," "Receiver reorder window (pinned; 32
/// sequences)," "Gap surfacing (pinned; `messageGap` event)," and
/// "Test-only injection seam (pinned)" bullets, plus ENVELOPE.md §1.2/§1.3's
/// wire `sequence` field as it flows through `ChatMessage`/trace JSON.
///
/// Most tests here construct envelope bytes directly via
/// `ChatEnvelopeCodec.encode` and feed them to a receiving client's
/// (internal, `@testable`-visible) `receiveEnvelope(_:)` -- the same entry
/// point `scheduleDelivery` uses in ordinary paired-exchange playback --
/// rather than only driving through `send()`, so out-of-order/duplicate-
/// sequence/window-bypass arrivals can be crafted precisely without needing
/// 32+ real `send()` calls. `traceSink` (also internal, `@testable`-visible,
/// the same hook `ChatScenarioRunner` uses) gives synchronous access to
/// emitted events between successive `receiveEnvelope` calls, which the
/// public `events` `AsyncStream` cannot provide without finishing the
/// stream first.
@Suite("Sequence assignment, reorder window, gap surfacing, and injection seam")
struct ChatSequenceAndReorderTests {
    /// Builds valid envelope bytes for a crafted inbound arrival: a
    /// 16-byte messageId derived from `tag` (repeated to fill the field, so
    /// distinct `tag` values give distinct, easily-recognized IDs), a fixed
    /// `senderId`, and the given `sequence`/`body`.
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

    /// `true` iff `kinds` contains at least one `messageGap` event.
    private static func containsMessageGap(_ kinds: [ChatEvent.Kind]) -> Bool {
        kinds.contains { kind in
            if case .messageGap = kind { return true }
            return false
        }
    }

    // MARK: - Outgoing sequence assignment

    @Test("outgoing sequence starts at 1 and increments by 1 per accepted send()")
    func outgoingSequenceStartsAtOneAndIncrements() async throws {
        let (clientA, clientB, clock) = try await connectedPair(seed: 601)

        clock.advance(toMs: 300)
        _ = try await clientA.send(body: "first")
        _ = try await clientA.send(body: "second")
        _ = try await clientA.send(body: "third")

        // happyPair's outcome delivers 60ms after transmitting (+20ms after
        // send) -- advance comfortably past all three round trips.
        clock.advance(toMs: 1000)
        await clientA.stop()
        await clientB.stop()

        var received: [(sequence: UInt64, body: String)] = []
        for await event in clientB.events {
            if case .messageReceived(let message) = event.kind {
                received.append((message.sequence, message.body))
            }
        }
        #expect(received.map { $0.sequence } == [1, 2, 3])
        #expect(received.map { $0.body } == ["first", "second", "third"])
    }

    // MARK: - Test-only injection seam

    @Test("a retried delivery via the injection seam is discarded as a duplicate messageId, sequence intact")
    func retryThroughSeamIsDuplicateDiscardedWithSequenceIntact() async throws {
        let (clientA, clientB, clock) = try await connectedPair(seed: 602)
        // CONTRACT.md §2's test-only injection seam: re-deliver the SAME
        // outgoing bytes 5ms after their original scheduled delivery --
        // exactly the "retried delivery via the injection seam re-delivers
        // the SAME bytes (same ID, same sequence)" case the pin describes.
        clientA.testOnlyInjectionSeam = { dueAtMs, bytes in
            [(dueAtMs: dueAtMs, bytes: bytes), (dueAtMs: dueAtMs + 5, bytes: bytes)]
        }

        clock.advance(toMs: 300)
        _ = try await clientA.send(body: "seam-retry")

        clock.advance(toMs: 1000)
        await clientA.stop()
        await clientB.stop()

        var received: [ChatMessage] = []
        for await event in clientB.events {
            if case .messageReceived(let message) = event.kind { received.append(message) }
        }
        #expect(received.count == 1)
        #expect(received.first?.sequence == 1)
        #expect(received.first?.body == "seam-retry")
    }

    @Test("same seed + same deterministic injection-seam script produce byte-identical traces")
    func injectionSeamIsDeterministic() async throws {
        let seed: UInt64 = 603

        func runOnce() async throws -> [ChatTraceRecord] {
            let clock = VirtualClock()
            let (clientA, clientB) = SimulatedChatTransportClient.makePair(
                scenario: .happyPair, seed: seed, clock: clock
            )
            var records: [ChatTraceRecord] = []
            let sink: (ChatClientRole, Int64, ChatEvent) -> Void = { role, virtualTimeMs, event in
                records.append(
                    ChatTraceRecord(
                        eventSeq: event.eventSeq,
                        virtualTimeMs: virtualTimeMs,
                        client: role,
                        event: event.kind
                    )
                )
            }
            clientA.traceSink = sink
            clientB.traceSink = sink
            // A pure, deterministic function of (dueAtMs, bytes) -- no
            // wall-clock/randomness involved -- so two independent runs of
            // the same seed with this same script produce identical
            // schedules.
            clientA.testOnlyInjectionSeam = { dueAtMs, bytes in
                [(dueAtMs: dueAtMs, bytes: bytes), (dueAtMs: dueAtMs + 3, bytes: bytes)]
            }

            try await clientA.start()
            try await clientB.start()
            clock.advance(toMs: 100)
            try await clientA.connect(toPeer: clientB.localPeerId.hexString)
            clock.advance(toMs: 300)
            _ = try await clientA.send(body: "seam-deterministic")
            clock.advance(toMs: 1000)
            clientA.traceSink = nil
            clientB.traceSink = nil
            await clientA.stop()
            await clientB.stop()
            return records
        }

        let first = try await runOnce()
        let second = try await runOnce()

        let firstLines = first.map { $0.canonicalJSONLine() }
        let secondLines = second.map { $0.canonicalJSONLine() }
        #expect(!firstLines.isEmpty)
        #expect(firstLines == secondLines)

        // The seam's duplicate delivery is still suppressed by messageId
        // dedup -- exactly one messageReceived survives.
        let receivedOnB = first.filter { record in
            guard record.client == .b else { return false }
            if case .messageReceived = record.event { return true }
            return false
        }
        #expect(receivedOnB.count == 1)
    }

    // MARK: - Receiver reorder window

    @Test("in-window out-of-order arrivals are delivered in ascending sequence order")
    func inWindowOutOfOrderDeliveredAscending() async throws {
        let clock = VirtualClock()
        let (_, clientB) = SimulatedChatTransportClient.makePair(
            scenario: .happyPair, seed: 604, clock: clock
        )
        let senderId = Data(repeating: 0xAA, count: 4)

        clientB.receiveEnvelope(
            try Self.craftedEnvelopeBytes(senderId: senderId, sequence: 3, tag: 0x03, body: "three")
        )
        clientB.receiveEnvelope(
            try Self.craftedEnvelopeBytes(senderId: senderId, sequence: 1, tag: 0x01, body: "one")
        )
        clientB.receiveEnvelope(
            try Self.craftedEnvelopeBytes(senderId: senderId, sequence: 2, tag: 0x02, body: "two")
        )

        await clientB.stop()

        var received: [(sequence: UInt64, body: String)] = []
        for await event in clientB.events {
            if case .messageReceived(let message) = event.kind {
                received.append((message.sequence, message.body))
            }
        }
        // Arrived 3, 1, 2 -- delivered 1, 2, 3: sequence 3 was buffered
        // until sequence 1 (then 2) filled the hole in front of it.
        #expect(received.map { $0.sequence } == [1, 2, 3])
        #expect(received.map { $0.body } == ["one", "two", "three"])
    }

    @Test("an arrival whose sequence was already resolved but whose messageId is new is dropped and counted")
    func duplicateSequenceUnknownIdIsDroppedAndCounted() async throws {
        let clock = VirtualClock()
        let (_, clientB) = SimulatedChatTransportClient.makePair(
            scenario: .happyPair, seed: 605, clock: clock
        )
        let senderId = Data(repeating: 0xBB, count: 4)

        clientB.receiveEnvelope(
            try Self.craftedEnvelopeBytes(senderId: senderId, sequence: 1, tag: 0x01, body: "first")
        )
        #expect(clientB.droppedStaleOrDuplicateSequenceCount == 0)

        // A DIFFERENT messageId reusing the already-resolved sequence 1 --
        // not the `duplicateIncoming` messageId-dedup case (this is a new
        // ID), CONTRACT.md §2's "already delivered ... but messageId
        // unseen" drop.
        clientB.receiveEnvelope(
            try Self.craftedEnvelopeBytes(senderId: senderId, sequence: 1, tag: 0x02, body: "impostor")
        )
        #expect(clientB.droppedStaleOrDuplicateSequenceCount == 1)

        await clientB.stop()

        var receivedBodies: [String] = []
        for await event in clientB.events {
            if case .messageReceived(let message) = event.kind { receivedBodies.append(message.body) }
        }
        // Only the legitimate first delivery ever reaches the event stream
        // -- the impostor is dropped, never delivered.
        #expect(receivedBodies == ["first"])
    }

    // MARK: - Gap surfacing

    @Test("an arrival 32+ sequences beyond the current base surfaces messageGap at the exact bypass boundary")
    func gapSurfacedAtWindowBypass() throws {
        let senderId = Data(repeating: 0xCC, count: 4)

        func gapEvents(fromArrivalAtSequence sequence: UInt64) throws -> [(from: UInt64, to: UInt64)] {
            let clock = VirtualClock()
            let (_, client) = SimulatedChatTransportClient.makePair(
                scenario: .happyPair, seed: 606, clock: clock
            )
            var captured: [ChatEvent.Kind] = []
            client.traceSink = { _, _, event in captured.append(event.kind) }
            client.receiveEnvelope(
                try Self.craftedEnvelopeBytes(senderId: senderId, sequence: sequence, tag: 0x30)
            )
            return captured.compactMap {
                if case .messageGap(let from, let to) = $0 { return (from, to) }
                return nil
            }
        }

        // base=1: sequence 32 is 31 beyond base (32 - 1 = 31 < 32) --
        // still exactly in-window, buffered normally, no gap.
        #expect(try gapEvents(fromArrivalAtSequence: 32).isEmpty)

        // base=1: sequence 33 is 32 beyond base (33 - 1 = 32 >= 32) --
        // bypasses the window. The missing run is exactly [1, 33-32] = [1,1].
        let bypassGaps = try gapEvents(fromArrivalAtSequence: 33)
        #expect(bypassGaps.count == 1)
        #expect(bypassGaps.first?.from == 1)
        #expect(bypassGaps.first?.to == 1)
    }

    @Test("a window bypass prunes already-buffered entries the jump leaves behind, counted as stale")
    func windowBypassPrunesAlreadyBufferedEntriesLeftBehind() async throws {
        let clock = VirtualClock()
        let (_, client) = SimulatedChatTransportClient.makePair(
            scenario: .happyPair, seed: 609, clock: clock
        )
        let senderId = Data(repeating: 0xEE, count: 4)
        var captured: [ChatEvent.Kind] = []
        client.traceSink = { _, _, event in captured.append(event.kind) }

        // base=1: sequences 5 and 6 arrive out of order first and buffer
        // (in-window: 5-1=4 < 32, 6-1=5 < 32) -- base itself (1) is still
        // missing.
        client.receiveEnvelope(try Self.craftedEnvelopeBytes(senderId: senderId, sequence: 5, tag: 0x60))
        client.receiveEnvelope(try Self.craftedEnvelopeBytes(senderId: senderId, sequence: 6, tag: 0x61))
        #expect(client.droppedStaleOrDuplicateSequenceCount == 0)
        #expect(!Self.containsMessageGap(captured))

        // sequence 40 is 39 beyond base=1 (>= 32) -- bypasses the window.
        // The missing run is [1, 40-32] = [1, 8], which swallows both
        // already-buffered entries (5 and 6, both <= 8): they are pruned
        // and counted as stale, never delivered.
        client.receiveEnvelope(try Self.craftedEnvelopeBytes(senderId: senderId, sequence: 40, tag: 0x62))

        let gaps = captured.compactMap { kind -> (from: UInt64, to: UInt64)? in
            if case .messageGap(let from, let to) = kind { return (from, to) }
            return nil
        }
        #expect(gaps.count == 1)
        #expect(gaps.first?.from == 1)
        #expect(gaps.first?.to == 8)
        // Both pruned buffered entries (5, 6) are counted as stale drops.
        #expect(client.droppedStaleOrDuplicateSequenceCount == 2)

        await client.stop()

        var receivedBodies: [String] = []
        for await event in client.events {
            if case .messageReceived(let message) = event.kind { receivedBodies.append(message.body) }
        }
        // Nothing at all was ever delivered: 5 and 6 were pruned as stale,
        // and 40 itself is buffered (40 - 9 = 31 < 32 against the new
        // base), waiting for a base that never arrives.
        #expect(receivedBodies.isEmpty)
    }

    @Test("a later arrival inside an already-surfaced gap range is dropped as stale, never re-surfaced")
    func staleArrivalInsideSurfacedGapIsDroppedNeverResurfaced() throws {
        let clock = VirtualClock()
        let (_, client) = SimulatedChatTransportClient.makePair(
            scenario: .happyPair, seed: 607, clock: clock
        )
        let senderId = Data(repeating: 0xDD, count: 4)
        var captured: [ChatEvent.Kind] = []
        client.traceSink = { _, _, event in captured.append(event.kind) }

        // Bypass the window (base=1, arrival=33) -- surfaces messageGap(1,1).
        client.receiveEnvelope(try Self.craftedEnvelopeBytes(senderId: senderId, sequence: 33, tag: 0x40))
        let gapCountAfterBypass = captured.filter {
            if case .messageGap = $0 { return true }
            return false
        }.count
        #expect(gapCountAfterBypass == 1)
        #expect(client.droppedStaleOrDuplicateSequenceCount == 0)

        // A later, distinct-messageId arrival for sequence 1 -- squarely
        // inside the just-surfaced [1,1] gap -- must be dropped as stale,
        // counted, and must NOT surface a second messageGap.
        client.receiveEnvelope(try Self.craftedEnvelopeBytes(senderId: senderId, sequence: 1, tag: 0x41))
        #expect(client.droppedStaleOrDuplicateSequenceCount == 1)
        let gapCountAfterStaleArrival = captured.filter {
            if case .messageGap = $0 { return true }
            return false
        }.count
        #expect(gapCountAfterStaleArrival == 1)
    }

    @Test("a gap still unfilled when the connection scope ends is surfaced via messageGap at stop()")
    func gapSurfacedAtScopeEnd() async throws {
        let (clientA, clientB, _) = try await connectedPair(seed: 608)

        var captured: [ChatEvent.Kind] = []
        clientB.traceSink = { _, _, event in captured.append(event.kind) }

        // Sequence 1 (the base) never arrives -- only sequence 2, which
        // buffers, waiting for a base that will never show up.
        let bufferedBytes = try Self.craftedEnvelopeBytes(
            senderId: clientA.localPeerId, sequence: 2, tag: 0x50, body: "buffered"
        )
        clientB.receiveEnvelope(bufferedBytes)
        #expect(!Self.containsMessageGap(captured))

        await clientB.stop()

        let gapsAtScopeEnd = captured.compactMap { kind -> (from: UInt64, to: UInt64)? in
            if case .messageGap(let from, let to) = kind { return (from, to) }
            return nil
        }
        #expect(gapsAtScopeEnd.count == 1)
        #expect(gapsAtScopeEnd.first?.from == 1)
        #expect(gapsAtScopeEnd.first?.to == 1)

        await clientA.stop()
    }

    // MARK: - Sequence values in traces

    @Test(
        "messageReceived trace JSON carries sequence at the pinned position (idHex, sequence, direction, ...)"
    )
    func messageReceivedTraceIncludesSequenceAtPinnedPosition() async throws {
        let records = try await ChatScenarioRunner.runToCompletion(scenario: .happyPair, seed: 1)
        let received = records.first { record in
            guard record.client == .b else { return false }
            if case .messageReceived = record.event { return true }
            return false
        }
        guard case .messageReceived(let message) = received?.event else {
            Issue.record("expected a messageReceived record for the happyPair/seed-1 golden scenario")
            return
        }
        // Matches fixtures/traces/happyPair.jsonl's own pinned value.
        #expect(message.sequence == 1)

        let json = try #require(received).canonicalJSONLine()
        let idHexPos = try #require(json.range(of: "\"idHex\""))
        let sequencePos = try #require(json.range(of: "\"sequence\""))
        let directionPos = try #require(json.range(of: "\"direction\""))
        #expect(idHexPos.lowerBound < sequencePos.lowerBound)
        #expect(sequencePos.lowerBound < directionPos.lowerBound)
    }
}
