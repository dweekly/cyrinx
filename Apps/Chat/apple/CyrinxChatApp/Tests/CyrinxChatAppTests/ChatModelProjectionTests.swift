import CyrinxChatKit
import Foundation
import Testing

@testable import CyrinxChatApp

/// Direct, event-level tests of `ChatModel.apply(_:)`'s projection rules --
/// the C3-29/C3-30 design brief's pinned "Shared ChatModel/ChatViewModel
/// projection" section, exercised with hand-built `ChatEvent` values (no
/// transport, no virtual clock needed) so every rule, including edge cases
/// none of CONTRACT.md §3's six scenario scripts exercise, has a direct,
/// fast, deterministic test. No sleeps anywhere in this file.
@Suite("ChatModel: event projection per pinned rules")
struct ChatModelProjectionTests {
    /// A `ChatTransportClient` that never emits anything on its own --
    /// these tests drive `ChatModel.apply(_:)` directly, so the only thing
    /// this stub needs to satisfy is the protocol's shape.
    final class NeverEmittingTransport: ChatTransportClient {
        let events: AsyncStream<ChatEvent> = AsyncStream { _ in }
        func start() async throws {}
        func stop() async {}
        func connect(toPeer idHex: String) async throws {}
        func disconnect() async {}
        func send(body: String) async throws -> String { "" }
        func cancelSend(messageIdHex: String) async {}
    }

    @MainActor
    private func makeModel() -> ChatModel {
        ChatModel(transport: NeverEmittingTransport(), now: { 0 })
    }

    private func peer(_ hex: String, discoveredAtMs: Int64) -> ChatPeer {
        ChatPeer(id: Data(hexString: hex)!, discoveredAtMs: discoveredAtMs)
    }

    // MARK: - Initial values (design brief's pinned defaults)

    @Test("initial projection matches the design brief's pinned defaults")
    @MainActor
    func initialProjection() {
        let model = makeModel()
        #expect(model.peers.isEmpty)
        #expect(model.connection == .disconnected(reason: nil))
        #expect(model.budget.classification == .controlOnly)
        #expect(model.budget.txLowerBoundBps == nil)
        #expect(model.budget.rxLowerBoundBps == nil)
        #expect(model.budget.confidence == 0.0)
        #expect(model.budget.ageMs == 0)
        #expect(model.messages.isEmpty)
        #expect(model.banner == nil)
        #expect(model.eventSeqGapDetected == false)
        #expect(model.droppedStatusUpdates == 0)
        #expect(model.composerError == nil)
    }

    // MARK: - Peers: insert, upsert, sort order

    @Test("peerFound inserts new peers sorted by (discoveredAtMs asc, idHex asc)")
    @MainActor
    func peerFoundInsertsSorted() {
        let model = makeModel()
        // Out of order on purpose: later-discovered first, then a tie on
        // discoveredAtMs broken by idHex.
        model.apply(ChatEvent(eventSeq: 0, kind: .peerFound(peer("cccccccc", discoveredAtMs: 200))))
        model.apply(ChatEvent(eventSeq: 1, kind: .peerFound(peer("aaaaaaaa", discoveredAtMs: 100))))
        model.apply(ChatEvent(eventSeq: 2, kind: .peerFound(peer("bbbbbbbb", discoveredAtMs: 100))))

        #expect(model.peers.map { $0.id.hexString } == ["aaaaaaaa", "bbbbbbbb", "cccccccc"])
    }

    @Test("peerFound on an already-known idHex replaces in place")
    @MainActor
    func peerFoundReplacesKnownPeer() {
        let model = makeModel()
        model.apply(ChatEvent(eventSeq: 0, kind: .peerFound(peer("aaaaaaaa", discoveredAtMs: 100))))
        model.apply(ChatEvent(eventSeq: 1, kind: .peerFound(peer("aaaaaaaa", discoveredAtMs: 999))))

        #expect(model.peers.count == 1)
        #expect(model.peers.first?.discoveredAtMs == 999)
    }

    @Test("peerUpdated replaces an already-known peer")
    @MainActor
    func peerUpdatedReplacesKnownPeer() {
        let model = makeModel()
        model.apply(ChatEvent(eventSeq: 0, kind: .peerFound(peer("aaaaaaaa", discoveredAtMs: 100))))
        model.apply(ChatEvent(eventSeq: 1, kind: .peerUpdated(peer("aaaaaaaa", discoveredAtMs: 500))))

        #expect(model.peers.count == 1)
        #expect(model.peers.first?.discoveredAtMs == 500)
    }

    @Test(
        """
        DECISION: peerUpdated naming an unknown peer is a no-op (the brief's \
        own wording distinguishes peerFound's "inserts/replaces" from \
        peerUpdated's plain "replaces")
        """
    )
    @MainActor
    func peerUpdatedOnUnknownPeerIsNoOp() {
        let model = makeModel()
        model.apply(ChatEvent(eventSeq: 0, kind: .peerUpdated(peer("aaaaaaaa", discoveredAtMs: 100))))
        #expect(model.peers.isEmpty)
    }

    @Test("peerLost removes the named peer")
    @MainActor
    func peerLostRemovesPeer() {
        let model = makeModel()
        model.apply(ChatEvent(eventSeq: 0, kind: .peerFound(peer("aaaaaaaa", discoveredAtMs: 100))))
        model.apply(ChatEvent(eventSeq: 1, kind: .peerFound(peer("bbbbbbbb", discoveredAtMs: 200))))
        model.apply(
            ChatEvent(eventSeq: 2, kind: .peerLost(peerIdHex: "aaaaaaaa", reason: "peerSilenceTimeout")))

        #expect(model.peers.map { $0.id.hexString } == ["bbbbbbbb"])
    }

    // MARK: - Connection and budget projection

    @Test(
        "connectionChanged updates connection verbatim",
        arguments: [
            ChatConnectionState.connecting,
            .connected,
            .degraded,
            .disconnected(reason: nil),
            .disconnected(reason: "userInitiated"),
        ])
    @MainActor
    func connectionChangedUpdatesState(_ state: ChatConnectionState) {
        let model = makeModel()
        model.apply(ChatEvent(eventSeq: 0, kind: .connectionChanged(state)))
        #expect(model.connection == state)
    }

    @Test("linkBudgetChanged replaces budget verbatim")
    @MainActor
    func linkBudgetChangedUpdatesBudget() {
        let model = makeModel()
        let newBudget = ChatLinkBudget(
            classification: .thumbnail, txLowerBoundBps: 4_000, rxLowerBoundBps: 2_000,
            confidence: 0.9, ageMs: 12
        )
        model.apply(ChatEvent(eventSeq: 0, kind: .linkBudgetChanged(newBudget)))
        #expect(model.budget == newBudget)
    }

    // MARK: - Messages: incoming append, status update-in-place, dropped updates

    @Test("messageReceived appends an incoming message")
    @MainActor
    func messageReceivedAppends() {
        let model = makeModel()
        let message = ChatMessage(
            id: Data(hexString: "0102030405060708090a0b0c0d0e0f10")!,
            direction: .incoming, body: "hi", senderPeerIdHex: "aaaaaaaa",
            sentAtWallClockMs: 42, status: .delivered
        )
        model.apply(ChatEvent(eventSeq: 0, kind: .messageReceived(message)))
        #expect(model.messages == [message])
    }

    @Test("messageStatusChanged updates a known message's status in place")
    @MainActor
    func messageStatusChangedUpdatesInPlace() {
        let model = makeModel()
        let messageId = Data(hexString: "0102030405060708090a0b0c0d0e0f10")!
        let message = ChatMessage(
            id: messageId, direction: .incoming, body: "hi", senderPeerIdHex: "aaaaaaaa",
            sentAtWallClockMs: 0, status: .delivered
        )
        model.apply(ChatEvent(eventSeq: 0, kind: .messageReceived(message)))
        model.apply(
            ChatEvent(
                eventSeq: 1,
                kind: .messageStatusChanged(
                    messageIdHex: messageId.hexString, status: .failed(reason: "cancelled"))
            )
        )
        #expect(model.messages.first?.status == .failed(reason: "cancelled"))
        #expect(model.droppedStatusUpdates == 0)
    }

    @Test("messageStatusChanged for an unknown idHex is ignored but counted")
    @MainActor
    func messageStatusChangedUnknownIdHexIsCounted() {
        let model = makeModel()
        model.apply(
            ChatEvent(eventSeq: 0, kind: .messageStatusChanged(messageIdHex: "deadbeef", status: .delivered)))
        #expect(model.messages.isEmpty)
        #expect(model.droppedStatusUpdates == 1)
    }

    // MARK: - eventSeq gap detection (sticky caption, not a banner)

    @Test("a single eventSeq gap sets eventSeqGapDetected and never clears")
    @MainActor
    func eventSeqGapIsStickyOnceObserved() {
        let model = makeModel()
        model.apply(ChatEvent(eventSeq: 0, kind: .connectionChanged(.connecting)))
        #expect(model.eventSeqGapDetected == false)

        // Skip eventSeq 1: a gap of 1.
        model.apply(ChatEvent(eventSeq: 2, kind: .connectionChanged(.connected)))
        #expect(model.eventSeqGapDetected == true)

        // A later, contiguous event must not clear the sticky flag.
        model.apply(ChatEvent(eventSeq: 3, kind: .connectionChanged(.degraded)))
        #expect(model.eventSeqGapDetected == true)
    }

    @Test("contiguous eventSeq values never set eventSeqGapDetected")
    @MainActor
    func contiguousEventSeqNeverSetsGap() {
        let model = makeModel()
        for seq in UInt64(0)...5 {
            model.apply(ChatEvent(eventSeq: seq, kind: .connectionChanged(.connecting)))
        }
        #expect(model.eventSeqGapDetected == false)
    }

    // MARK: - Banner: pinned priority order and plain-language map

    @Test(
        "connection-disconnected reasons map through the pinned plain-language table",
        arguments: [
            ("peerSilenceTimeout", "Peer stopped responding. Move the devices closer and reconnect."),
            ("userInitiated", "Disconnected."),
            ("stopped", "Session ended."),
            ("peerLost", "Peer lost."),
            ("somethingTotallyUnknown", "somethingTotallyUnknown"),
        ]
    )
    @MainActor
    func bannerPlainLanguageMap(_ pair: (reason: String, expected: String)) {
        let model = makeModel()
        model.apply(ChatEvent(eventSeq: 0, kind: .connectionChanged(.disconnected(reason: pair.reason))))
        #expect(model.banner == pair.expected)
    }

    @Test("disconnected with a nil reason produces no banner")
    @MainActor
    func disconnectedNilReasonNoBanner() {
        let model = makeModel()
        model.apply(ChatEvent(eventSeq: 0, kind: .connectionChanged(.connected)))
        model.apply(ChatEvent(eventSeq: 1, kind: .connectionChanged(.disconnected(reason: nil))))
        #expect(model.banner == nil)
    }

    @Test("degraded connection produces the pinned degraded banner string")
    @MainActor
    func degradedBanner() {
        let model = makeModel()
        model.apply(ChatEvent(eventSeq: 0, kind: .connectionChanged(.degraded)))
        #expect(model.banner == "Link degraded — move devices closer")
    }

    @Test("connected/connecting with no failure produces no banner")
    @MainActor
    func connectedNoBanner() {
        let model = makeModel()
        model.apply(ChatEvent(eventSeq: 0, kind: .connectionChanged(.connecting)))
        #expect(model.banner == nil)
        model.apply(ChatEvent(eventSeq: 1, kind: .connectionChanged(.connected)))
        #expect(model.banner == nil)
    }

    @Test("clientFailed outranks a disconnected reason and is sticky")
    @MainActor
    func clientFailedOutranksAndSticks() {
        let model = makeModel()
        model.apply(ChatEvent(eventSeq: 0, kind: .clientFailed(reason: "transportFault")))
        #expect(model.banner == "transportFault")

        // A later disconnected-with-reason event must NOT override clientFailed
        // -- pinned top priority, and documented sticky for this model.
        model.apply(ChatEvent(eventSeq: 1, kind: .connectionChanged(.disconnected(reason: "userInitiated"))))
        #expect(model.banner == "transportFault")
    }

    @Test("clientFailed outranks degraded")
    @MainActor
    func clientFailedOutranksDegraded() {
        let model = makeModel()
        model.apply(ChatEvent(eventSeq: 0, kind: .connectionChanged(.degraded)))
        #expect(model.banner == "Link degraded — move devices closer")
        model.apply(ChatEvent(eventSeq: 1, kind: .clientFailed(reason: "transportFault")))
        #expect(model.banner == "transportFault")
    }

    @Test("peerLost naming the connected peer surfaces recovery guidance when connection hasn't already")
    @MainActor
    func peerLostNamingConnectedPeerSurfacesBanner() async {
        let model = makeModel()
        await model.connect(toPeerIdHex: "aabbccdd")
        model.apply(ChatEvent(eventSeq: 0, kind: .connectionChanged(.connected)))
        #expect(model.banner == nil)

        // Defensive branch: peerLost arrives naming the connected peer
        // without a preceding connectionChanged(disconnected) -- CONTRACT.md
        // §3.2's own pinned scenario never reaches this order (its
        // disconnected event always precedes peerLost), but this model's
        // banner mapping must still be well-defined here.
        model.apply(
            ChatEvent(eventSeq: 1, kind: .peerLost(peerIdHex: "aabbccdd", reason: "peerSilenceTimeout")))
        #expect(model.banner == "Peer stopped responding. Move the devices closer and reconnect.")
    }

    @Test("peerLost naming a different (non-connected) peer does not affect the banner")
    @MainActor
    func peerLostOtherPeerDoesNotAffectBanner() async {
        let model = makeModel()
        await model.connect(toPeerIdHex: "aabbccdd")
        model.apply(ChatEvent(eventSeq: 0, kind: .connectionChanged(.connected)))
        model.apply(
            ChatEvent(eventSeq: 1, kind: .peerLost(peerIdHex: "11223344", reason: "peerSilenceTimeout")))
        #expect(model.banner == nil)
    }
}
