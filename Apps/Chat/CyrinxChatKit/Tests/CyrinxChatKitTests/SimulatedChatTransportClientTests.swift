import Foundation
import Testing

@testable import CyrinxChatKit

/// Direct, ad hoc (not tied to one of the six canonical scenario scripts)
/// tests against `SimulatedChatTransportClient`'s public protocol surface:
/// a real paired exchange with caller-supplied content, and cancellation.
@Suite("Simulated transport client: paired exchange and cancellation")
struct SimulatedChatTransportClientTests {
    /// Drives a pair through start -> connect, matching the driver timing
    /// every one of CONTRACT.md §3's six tables share (connect at t=100,
    /// connected at t=150), leaving the clock at t=150. Callers advance
    /// further as needed for their own scenario.
    private static func connectedPair(
        scenario: ChatScenario = .happyPair,
        seed: UInt64
    ) async throws -> (
        a: SimulatedChatTransportClient, b: SimulatedChatTransportClient, clock: VirtualClock
    ) {
        let clock = VirtualClock()
        let (clientA, clientB) = SimulatedChatTransportClient.makePair(
            scenario: scenario, seed: seed, clock: clock
        )
        try await clientA.start()
        try await clientB.start()
        clock.advance(toMs: 100)
        try await clientA.connect(toPeer: clientB.localPeerId.hexString)
        clock.advance(toMs: 150)
        return (clientA, clientB, clock)
    }

    @Test("B receives exactly what A sent, through real encode/decode, for caller-supplied content")
    func pairedExchangeRoundTripsCallerSuppliedBody() async throws {
        let (clientA, clientB, clock) = try await Self.connectedPair(seed: 7)

        clock.advance(toMs: 300)
        let customBody = "Custom paired-exchange body: café 日本語 😀 \"quoted\" and\ttabbed"
        let sentMessageIdHex = try await clientA.send(body: customBody)

        // happyPair's send outcome: transmitting@+20, B receives@+80, A
        // delivered@+100 relative to the send call (ChatScenario.swift's
        // .deliverNormally(60,80) applied on top of the 20ms transmitting
        // delay) -- advance comfortably past all of it.
        clock.advance(toMs: 500)
        await clientA.stop()
        await clientB.stop()

        var receivedMessages: [ChatMessage] = []
        for await event in clientB.events {
            if case .messageReceived(let message) = event.kind {
                receivedMessages.append(message)
            }
        }

        #expect(receivedMessages.count == 1)
        #expect(receivedMessages.first?.body == customBody)
        #expect(receivedMessages.first?.id.hexString == sentMessageIdHex)
        #expect(receivedMessages.first?.senderPeerIdHex == clientA.localPeerId.hexString)
        #expect(receivedMessages.first?.direction == .incoming)
    }

    @Test("cancelSend stops further scripted transitions, emits failed(cancelled); B never receives it")
    func cancelSendStopsFurtherProgress() async throws {
        let (clientA, clientB, clock) = try await Self.connectedPair(seed: 55)

        clock.advance(toMs: 300)
        let messageIdHex = try await clientA.send(body: "cancel-me")
        // Cancel immediately, before the scripted transmitting transition
        // (which would otherwise fire 20ms later per ChatSimTiming).
        await clientA.cancelSend(messageIdHex: messageIdHex)

        clock.advance(toMs: 2000)
        await clientA.stop()
        await clientB.stop()

        var statusesForMessage: [ChatMessageDisplayStatus] = []
        for await event in clientA.events {
            if case .messageStatusChanged(let idHex, let status) = event.kind, idHex == messageIdHex {
                statusesForMessage.append(status)
            }
        }
        // CONTRACT.md §2's "Behavior outside the six scenario tables
        // (pinned)": cancelSend emits messageStatusChanged(failed,
        // failureReason: "cancelled") -- the initial `queued` (emitted
        // synchronously inside send(), before cancellation) is followed by
        // exactly that, and nothing further (no scripted `transmitting`/
        // `delivered` reaches it, since cancelSend cancels those scheduled
        // transitions).
        #expect(statusesForMessage == [.queued, .failed(reason: "cancelled")])

        var receivedOnB = false
        for await event in clientB.events {
            if case .messageReceived = event.kind { receivedOnB = true }
        }
        #expect(!receivedOnB)
    }

    @Test("cancelSend on an unknown messageIdHex is a harmless no-op")
    func cancelSendOnUnknownIdIsNoOp() async throws {
        let (clientA, _, _) = try await Self.connectedPair(seed: 8)
        await clientA.cancelSend(messageIdHex: "deadbeef")  // never sent; must not crash or throw
    }

    @Test("cancelSend on an already-delivered messageIdHex is a no-op: no further status change")
    func cancelSendOnAlreadyDeliveredIsNoOp() async throws {
        let (clientA, clientB, clock) = try await Self.connectedPair(seed: 21)

        clock.advance(toMs: 300)
        let messageIdHex = try await clientA.send(body: "already-delivered")
        // happyPair's send outcome: transmitting@+20, B receives@+80, A
        // delivered@+100 relative to the send call -- advance well past
        // `delivered` before cancelling.
        clock.advance(toMs: 500)
        await clientA.cancelSend(messageIdHex: messageIdHex)

        clock.advance(toMs: 1000)
        await clientA.stop()
        await clientB.stop()

        var statusesForMessage: [ChatMessageDisplayStatus] = []
        for await event in clientA.events {
            if case .messageStatusChanged(let idHex, let status) = event.kind, idHex == messageIdHex {
                statusesForMessage.append(status)
            }
        }
        // Terminal (`.delivered`) status must not be overwritten by a
        // trailing `failed(cancelled)` -- CONTRACT.md's "unless the message
        // ID is unknown or already terminal" no-op case.
        #expect(statusesForMessage == [.queued, .transmitting, .delivered])
    }

    @Test("cancelSend on an already-failed messageIdHex is a no-op: no duplicate failed emission")
    func cancelSendOnAlreadyFailedIsNoOp() async throws {
        let (clientA, clientB, clock) = try await Self.connectedPair(scenario: .sendFailure, seed: 34)

        clock.advance(toMs: 300)
        let messageIdHex = try await clientA.send(body: "will-fail")
        // sendFailure's outcome: transmitting@+20, failed(noAcknowledgment)@+130.
        clock.advance(toMs: 500)
        await clientA.cancelSend(messageIdHex: messageIdHex)

        clock.advance(toMs: 1000)
        await clientA.stop()
        await clientB.stop()

        var statusesForMessage: [ChatMessageDisplayStatus] = []
        for await event in clientA.events {
            if case .messageStatusChanged(let idHex, let status) = event.kind, idHex == messageIdHex {
                statusesForMessage.append(status)
            }
        }
        // The scripted `failed(noAcknowledgment)` must not be replaced or
        // duplicated by a second `failed(cancelled)`.
        #expect(statusesForMessage == [.queued, .transmitting, .failed(reason: "noAcknowledgment")])
    }

    @Test("disconnect() emits connectionChanged(disconnected, reason: \"userInitiated\")")
    func disconnectEmitsUserInitiatedReason() async throws {
        let (clientA, _, _) = try await Self.connectedPair(seed: 13)

        await clientA.disconnect()
        #expect(clientA.connectionState == .disconnected(reason: "userInitiated"))

        await clientA.stop()
        var connectionChanges: [ChatConnectionState] = []
        for await event in clientA.events {
            if case .connectionChanged(let state) = event.kind {
                connectionChanges.append(state)
            }
        }
        // connecting, connected (from connectedPair's setup), then this
        // disconnect's disconnected(userInitiated) -- CONTRACT.md §2's
        // pinned reason string for the not-scripted-by-any-of-the-six-
        // scenarios disconnect() path.
        #expect(connectionChanges.last == .disconnected(reason: "userInitiated"))
    }

    @Test("send while not connected throws notConnected")
    func sendWhileNotConnectedThrows() async throws {
        let clock = VirtualClock()
        let (clientA, clientB) = SimulatedChatTransportClient.makePair(
            scenario: .happyPair, seed: 3, clock: clock
        )
        try await clientA.start()
        try await clientB.start()
        // Deliberately skip connect().
        await #expect(throws: ChatSimulatedTransportError.notConnected) {
            _ = try await clientA.send(body: "should not send")
        }
    }

    @Test("connect to an unrecognized peer idHex throws unknownPeer")
    func connectToUnknownPeerThrows() async throws {
        let clock = VirtualClock()
        let (clientA, clientB) = SimulatedChatTransportClient.makePair(
            scenario: .happyPair, seed: 4, clock: clock
        )
        try await clientA.start()
        try await clientB.start()
        clock.advance(toMs: 100)
        await #expect(throws: ChatSimulatedTransportError.unknownPeer) {
            try await clientA.connect(toPeer: "ffffffff")
        }
    }

    @Test("send is accepted while degraded (CONTRACT.md §2's Send precondition)")
    func sendAcceptedWhileDegraded() async throws {
        let (clientA, _, clock) = try await Self.connectedPair(scenario: .degradedThenRecovered, seed: 65)

        // degradedThenRecovered's postConnectSteps (ChatScenario.swift):
        // linkBudget@+50, degraded@+250 relative to `connected` (t=150) --
        // advance to t=410 so `connectionState == .degraded` before sending.
        clock.advance(toMs: 410)
        #expect(clientA.connectionState == .degraded)
        let messageIdHex = try await clientA.send(body: "sent-while-degraded")
        #expect(!messageIdHex.isEmpty)

        clock.advance(toMs: 2000)
        await clientA.stop()

        var statusesForMessage: [ChatMessageDisplayStatus] = []
        for await event in clientA.events {
            if case .messageStatusChanged(let idHex, let status) = event.kind, idHex == messageIdHex {
                statusesForMessage.append(status)
            }
        }
        #expect(statusesForMessage.first == .queued)
    }

    @Test("connect-then-disconnect: no connected event after disconnect; scheduled handshake is cancelled")
    func connectThenDisconnectCancelsScheduledHandshake() async throws {
        let clock = VirtualClock()
        let (clientA, clientB) = SimulatedChatTransportClient.makePair(
            scenario: .happyPair, seed: 71, clock: clock
        )
        try await clientA.start()
        try await clientB.start()
        clock.advance(toMs: 100)
        try await clientA.connect(toPeer: clientB.localPeerId.hexString)
        // Disconnect immediately -- before the scripted `connected`
        // transition (otherwise due 50ms later per
        // ChatSimTiming.connectHandshakeDelayMs) has any chance to fire.
        await clientA.disconnect()

        // Advance well past every one of happyPair's pinned times; if the
        // handshake step were NOT cancelled, `connected` (and the
        // scenario's post-connect linkBudgetChanged) would appear here.
        clock.advance(toMs: 2000)
        await clientA.stop()
        await clientB.stop()

        var connectionChanges: [ChatConnectionState] = []
        var sawLinkBudgetChanged = false
        for await event in clientA.events {
            switch event.kind {
            case .connectionChanged(let state):
                connectionChanges.append(state)
            case .linkBudgetChanged:
                sawLinkBudgetChanged = true
            default:
                break
            }
        }
        // connecting (from connect()), then disconnected(userInitiated)
        // (from disconnect()) -- CONTRACT.md §2's Lifecycle cancellation:
        // disconnect() cancels the scheduled `becomeConnected` handshake
        // step, so `.connected` must never appear, and stop() afterward
        // sees state already disconnected so emits nothing further.
        #expect(connectionChanges == [.connecting, .disconnected(reason: "userInitiated")])
        #expect(!sawLinkBudgetChanged)
    }

    @Test("send-then-stop: nonterminal send terminalizes as failed(\"stopped\") before the stream finishes")
    func sendThenStopTerminalizesBeforeStreamFinishes() async throws {
        let (clientA, clientB, clock) = try await Self.connectedPair(seed: 72)

        clock.advance(toMs: 300)
        let messageIdHex = try await clientA.send(body: "stop-me")
        // Stop immediately -- before the scripted transmitting/receive/
        // delivered transitions (happyPair's sendOutcome, otherwise due at
        // +20/+80/+100ms per ChatSimTiming/ChatScenario) have any chance to
        // fire.
        await clientA.stop()

        // Advance well past every one of happyPair's pinned send-outcome
        // times; if the scheduled transitions were NOT cancelled, this
        // would surface `.transmitting`/`.delivered` below.
        clock.advance(toMs: 2000)
        await clientB.stop()

        var statusesForMessage: [ChatMessageDisplayStatus] = []
        var connectionChanges: [ChatConnectionState] = []
        // Draining this stream to completion (the for-await loop ends only
        // once `stop()`'s `emitter.finish()` runs) is itself part of what
        // this test verifies: if `stop()` never finished the stream, this
        // loop would hang instead of returning.
        for await event in clientA.events {
            switch event.kind {
            case .messageStatusChanged(let idHex, let status) where idHex == messageIdHex:
                statusesForMessage.append(status)
            case .connectionChanged(let state):
                connectionChanges.append(state)
            default:
                break
            }
        }
        #expect(statusesForMessage == [.queued, .failed(reason: "stopped")])
        #expect(connectionChanges.last == .disconnected(reason: "stopped"))

        var receivedOnB = false
        for await event in clientB.events {
            if case .messageReceived = event.kind { receivedOnB = true }
        }
        #expect(!receivedOnB)
    }

    @Test("repeat stop() and disconnect() calls are no-ops")
    func repeatStopAndDisconnectAreNoOps() async throws {
        let (clientA, clientB, _) = try await Self.connectedPair(seed: 73)

        await clientA.disconnect()
        await clientA.disconnect()  // repeat -- must not emit a second event
        await clientA.stop()
        await clientA.stop()  // repeat -- must not finish the stream twice or crash
        await clientB.stop()

        var connectionChanges: [ChatConnectionState] = []
        for await event in clientA.events {
            if case .connectionChanged(let state) = event.kind {
                connectionChanges.append(state)
            }
        }
        // connecting, connected (connectedPair's setup), disconnected
        // (userInitiated) from the FIRST disconnect() only -- the repeat
        // disconnect() and both stop() calls contribute nothing further
        // (stop() sees state already disconnected).
        #expect(connectionChanges == [.connecting, .connected, .disconnected(reason: "userInitiated")])
    }

    @Test("stop() suppresses every previously scheduled scenario action -- none fire after stop")
    func stopSuppressesPreviouslyScheduledActions() async throws {
        let clock = VirtualClock()
        let (clientA, clientB) = SimulatedChatTransportClient.makePair(
            scenario: .degradedThenRecovered, seed: 74, clock: clock
        )
        try await clientA.start()
        try await clientB.start()
        clock.advance(toMs: 100)
        try await clientA.connect(toPeer: clientB.localPeerId.hexString)
        // `connected` fires at t=150; degradedThenRecovered's postConnect
        // steps (linkBudget@+50, degraded@+250, ...) are all still
        // scheduled ahead of that.
        clock.advance(toMs: 150)
        await clientA.stop()

        // Comfortably past every one of degradedThenRecovered's pinned
        // times (last pinned event at t=710, §3.3).
        clock.advance(toMs: 5000)
        await clientB.stop()

        var kindsAfterStop: [ChatEvent.Kind] = []
        for await event in clientA.events {
            kindsAfterStop.append(event.kind)
        }
        let hasAnyPostConnectStep = kindsAfterStop.contains { kind in
            if case .linkBudgetChanged = kind { return true }
            if case .connectionChanged(.degraded) = kind { return true }
            return false
        }
        #expect(!hasAnyPostConnectStep)
    }

    @Test("peerLoss's scripted disconnect terminalizes a nonterminal send as failed(\"peerLost\")")
    func peerLossScriptedDisconnectTerminalizesNonterminalSend() async throws {
        let clock = VirtualClock()
        let (clientA, clientB) = SimulatedChatTransportClient.makePair(
            scenario: .peerLoss, seed: 75, clock: clock
        )
        try await clientA.start()
        try await clientB.start()
        clock.advance(toMs: 100)
        try await clientA.connect(toPeer: clientB.localPeerId.hexString)
        clock.advance(toMs: 150)

        // peerLoss's scripted disconnect fires at connected(150)+350=500
        // (§3.2); happyPair-shaped sendOutcome would otherwise deliver this
        // message at transmitting+80 -- send late enough (t=450) that
        // `delivered` (450+20+80=550) would land AFTER the scripted
        // disconnect at 500, so the message is still nonterminal
        // (`.transmitting`) when the scripted disconnect fires.
        clock.advance(toMs: 450)
        let messageIdHex = try await clientA.send(body: "still-in-flight")

        // Past peerLoss's full pinned timeline (last pinned event at
        // t=510, §3.2) and well past where an uncancelled `delivered`
        // would otherwise have fired (t=550).
        clock.advance(toMs: 2000)
        await clientA.stop()
        await clientB.stop()

        var statusesForMessage: [ChatMessageDisplayStatus] = []
        for await event in clientA.events {
            if case .messageStatusChanged(let idHex, let status) = event.kind, idHex == messageIdHex {
                statusesForMessage.append(status)
            }
        }
        // CONTRACT.md §2's third Lifecycle-cancellation bullet: exactly
        // queued -> transmitting -> failed(peerLost), nothing further --
        // never delivered, and not overwritten by stop()'s own
        // failed(stopped) (the message is already terminal by the time
        // stop() runs).
        #expect(statusesForMessage == [.queued, .transmitting, .failed(reason: "peerLost")])

        var receivedOnB = false
        for await event in clientB.events {
            if case .messageReceived = event.kind { receivedOnB = true }
        }
        #expect(!receivedOnB)
    }
}

/// Direct tests of `ChatEventEmitter`'s bounded-buffer policy
/// (CONTRACT.md §1.7), independent of any scenario -- none of the six
/// pinned scenarios come close to 512 events, so this exercises the
/// overflow path synthetically.
@Suite("Event emitter: bounded buffer, drop-oldest policy")
struct ChatEventEmitterTests {
    @Test("drop-oldest at 512 capacity preserves eventSeq gap detectability")
    func dropOldestPreservesGapDetectability() async {
        let emitter = ChatEventEmitter()
        let totalEmitted = 600
        for index in 0..<totalEmitted {
            emitter.emit(.clientFailed(reason: "synthetic-\(index)"))
        }
        emitter.finish()

        var received: [ChatEvent] = []
        for await event in emitter.stream {
            received.append(event)
        }

        #expect(received.count == ChatEventEmitter.bufferCapacity)
        let expectedFirstSurvivorSeq = UInt64(totalEmitted - ChatEventEmitter.bufferCapacity)
        #expect(received.first?.eventSeq == expectedFirstSurvivorSeq)
        #expect(received.last?.eventSeq == UInt64(totalEmitted - 1))

        // Gap-detection contract (CONTRACT.md §1.7): a consumer that had
        // seen nothing yet (previousEventSeq treated as -1) computes
        // gap = currentEventSeq - previousEventSeq - 1 for the first
        // surviving event, which must equal exactly how many were dropped.
        let gap = Int64(received.first!.eventSeq) - (-1) - 1
        #expect(gap == Int64(totalEmitted - ChatEventEmitter.bufferCapacity))

        // Survivors are contiguous and in original relative order --
        // dropping never renumbers or reorders what does get through.
        for (previous, current) in zip(received, received.dropFirst()) {
            #expect(current.eventSeq == previous.eventSeq + 1)
        }
    }

    @Test("under-capacity emission drops nothing")
    func underCapacityDropsNothing() async {
        let emitter = ChatEventEmitter()
        let totalEmitted = 10
        for index in 0..<totalEmitted {
            emitter.emit(.clientFailed(reason: "synthetic-\(index)"))
        }
        emitter.finish()

        var received: [ChatEvent] = []
        for await event in emitter.stream {
            received.append(event)
        }
        #expect(received.count == totalEmitted)
        #expect(received.map(\.eventSeq) == Array(0..<UInt64(totalEmitted)))
    }
}
