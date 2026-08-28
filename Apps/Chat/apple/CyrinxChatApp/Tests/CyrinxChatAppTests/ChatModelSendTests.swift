import CyrinxChatKit
import Foundation
import Testing

@testable import CyrinxChatApp

/// Command-wrapper tests: `ChatModel.send(_:)`/`connect(toPeerIdHex:)`/
/// `disconnect()`/`cancelSend(messageIdHex:)` against a fully programmable
/// stub transport -- the design brief's pinned "send failure/retry"
/// coverage, plus the composer-error mapping for every
/// `ChatSimulatedTransportError` case. No real transport, no virtual clock
/// needed here; `ChatModelScenarioTests` covers the same commands wired
/// through the real `SimulatedChatTransportClient`.
@Suite("ChatModel: send/connect/disconnect commands and composer errors")
struct ChatModelSendTests {
    /// A `ChatTransportClient` whose command outcomes are fully
    /// caller-controlled -- lets these tests trigger every named
    /// `ChatSimulatedTransportError` case on demand without needing to
    /// reproduce the real transport's preconditions (e.g. actually being
    /// disconnected) for each one.
    final class StubTransport: ChatTransportClient {
        let events: AsyncStream<ChatEvent> = AsyncStream { _ in }

        var connectError: Error?
        var sendError: Error?
        var nextSendMessageIdHex = "0102030405060708090a0b0c0d0e0f10"

        private(set) var connectCallCount = 0
        private(set) var sendCallCount = 0
        private(set) var disconnectCallCount = 0
        private(set) var cancelSendCallCount = 0
        private(set) var lastCancelledMessageIdHex: String?

        func start() async throws {}
        func stop() async {}

        func connect(toPeer idHex: String) async throws {
            connectCallCount += 1
            if let connectError { throw connectError }
        }

        func disconnect() async {
            disconnectCallCount += 1
        }

        func send(body: String) async throws -> String {
            sendCallCount += 1
            if let sendError { throw sendError }
            return nextSendMessageIdHex
        }

        func cancelSend(messageIdHex: String) async {
            cancelSendCallCount += 1
            lastCancelledMessageIdHex = messageIdHex
        }
    }

    // MARK: - send(_:) success

    @Test("send success appends an outgoing message at acceptance, status queued")
    @MainActor
    func sendSuccessAppendsQueuedMessage() async throws {
        let transport = StubTransport()
        let model = ChatModel(transport: transport, now: { 12_345 })

        await model.send("hello")

        #expect(model.composerError == nil)
        #expect(model.messages.count == 1)
        let message = try #require(model.messages.first)
        #expect(message.direction == .outgoing)
        #expect(message.body == "hello")
        #expect(message.status == .queued)
        #expect(message.sentAtWallClockMs == 12_345)
        #expect(message.id.hexString == transport.nextSendMessageIdHex)
        // CONTRACT.md §2's "Outgoing sequence assignment (pinned)": starts
        // at 1. `ChatModel` mirrors this locally (see `ChatModel
        // .nextOutgoingSequence`'s doc comment) since `send(body:)` never
        // returns the transport's own assigned sequence.
        #expect(message.sequence == 1)
    }

    @Test("outgoing sequence starts at 1 and increments by 1 per accepted send, mirroring CONTRACT.md §2")
    @MainActor
    func outgoingSequenceIncrementsPerAcceptedSend() async {
        let transport = StubTransport()
        let model = ChatModel(transport: transport, now: { 0 })

        transport.nextSendMessageIdHex = "01010101010101010101010101010101"
        await model.send("first")
        transport.nextSendMessageIdHex = "02020202020202020202020202020202"
        await model.send("second")
        // A synchronous send failure must NOT consume a sequence value --
        // only accepted sends increment the counter.
        transport.sendError = ChatSimulatedTransportError.notConnected
        await model.send("this one fails")
        transport.sendError = nil
        transport.nextSendMessageIdHex = "03030303030303030303030303030303"
        await model.send("third")

        #expect(model.messages.map { $0.sequence } == [1, 2, 3])
    }

    @Test("an invalid (non-hex) returned messageIdHex surfaces a composer error, no message row")
    @MainActor
    func sendWithInvalidReturnedHexSurfacesError() async {
        let transport = StubTransport()
        transport.nextSendMessageIdHex = "not-hex"
        let model = ChatModel(transport: transport, now: { 0 })

        await model.send("hello")

        #expect(model.messages.isEmpty)
        #expect(model.composerError != nil)
    }

    // MARK: - send(_:) failure and retry

    @Test(
        "synchronous send failures surface as composerError, never a message row",
        arguments: [
            (ChatSimulatedTransportError.notConnected, "Not connected. Connect to a peer before sending."),
            (.terminal, "This session has ended. Reconnect to try again."),
            (.concurrentCommand, "Still finishing the last action. Try again in a moment."),
            (.unknownPeer, "Unknown peer. Choose a peer from the list."),
        ])
    @MainActor
    func sendFailureSurfacesComposerError(_ pair: (error: ChatSimulatedTransportError, expected: String))
        async
    {
        let transport = StubTransport()
        transport.sendError = pair.error
        let model = ChatModel(transport: transport, now: { 0 })

        await model.send("will-fail")

        #expect(model.messages.isEmpty)
        #expect(model.composerError == pair.expected)
    }

    @Test("send retry: a failed send followed by a successful one clears composerError and appends")
    @MainActor
    func sendRetrySucceedsAfterFailure() async {
        let transport = StubTransport()
        transport.sendError = ChatSimulatedTransportError.notConnected
        let model = ChatModel(transport: transport, now: { 0 })

        await model.send("first attempt")
        #expect(model.composerError != nil)
        #expect(model.messages.isEmpty)

        transport.sendError = nil
        await model.send("second attempt")
        #expect(model.composerError == nil)
        #expect(model.messages.count == 1)
        #expect(model.messages.first?.body == "second attempt")
    }

    @Test("a fresh command attempt clears a prior composerError even if it does not itself fail")
    @MainActor
    func freshCommandClearsPriorComposerError() async {
        let transport = StubTransport()
        transport.sendError = ChatSimulatedTransportError.notConnected
        let model = ChatModel(transport: transport, now: { 0 })

        await model.send("will-fail")
        #expect(model.composerError != nil)

        await model.disconnect()
        #expect(model.composerError == nil)
    }

    // MARK: - connect(toPeerIdHex:)

    @Test("connect success clears composerError and calls the transport once")
    @MainActor
    func connectSuccess() async {
        let transport = StubTransport()
        let model = ChatModel(transport: transport, now: { 0 })
        await model.connect(toPeerIdHex: "aabbccdd")
        #expect(transport.connectCallCount == 1)
        #expect(model.composerError == nil)
    }

    @Test("connect failure surfaces composerError")
    @MainActor
    func connectFailureSurfacesComposerError() async {
        let transport = StubTransport()
        transport.connectError = ChatSimulatedTransportError.unknownPeer
        let model = ChatModel(transport: transport, now: { 0 })
        await model.connect(toPeerIdHex: "aabbccdd")
        #expect(model.composerError == "Unknown peer. Choose a peer from the list.")
    }

    // MARK: - disconnect() / cancelSend(messageIdHex:)

    @Test("disconnect calls the transport exactly once and cannot itself fail")
    @MainActor
    func disconnectCallsTransport() async {
        let transport = StubTransport()
        let model = ChatModel(transport: transport, now: { 0 })
        await model.disconnect()
        #expect(transport.disconnectCallCount == 1)
        #expect(model.composerError == nil)
    }

    @Test("cancelSend forwards the exact messageIdHex to the transport")
    @MainActor
    func cancelSendForwardsMessageId() async {
        let transport = StubTransport()
        let model = ChatModel(transport: transport, now: { 0 })
        await model.cancelSend(messageIdHex: "0102030405060708090a0b0c0d0e0f10")
        #expect(transport.cancelSendCallCount == 1)
        #expect(transport.lastCancelledMessageIdHex == "0102030405060708090a0b0c0d0e0f10")
    }

    // MARK: - composerErrorDescription(for:) generic fallback

    @Test("an error type this model does not specifically recognize still produces a non-empty description")
    @MainActor
    func genericErrorFallbackDescription() {
        struct SomeOtherError: Error {}
        let description = ChatModel.composerErrorDescription(for: SomeOtherError())
        #expect(!description.isEmpty)
    }

    @Test("ChatEnvelopeError produces a size/malformed-specific description")
    @MainActor
    func envelopeErrorDescription() {
        let description = ChatModel.composerErrorDescription(for: ChatEnvelopeError.oversizeBody)
        #expect(description.contains("too large") || description.contains("malformed"))
    }
}
