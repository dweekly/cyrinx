import Foundation
import Testing

@testable import CyrinxChatKit

/// Drives a pair through start -> connect, matching the driver timing every
/// one of CONTRACT.md §3's six tables share (connect at t=100, connected at
/// t=150), leaving the clock at t=150. Callers advance further as needed
/// for their own scenario. File-scope (not a member of either `@Suite`
/// struct below) so both can share it without duplication.
private func connectedPair(
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

/// Direct, ad hoc (not tied to one of the six canonical scenario scripts)
/// tests against `SimulatedChatTransportClient`'s public protocol surface:
/// a real paired exchange with caller-supplied content, and cancellation.
@Suite("Simulated transport client: paired exchange and cancellation")
struct SimulatedChatTransportClientTests {
    @Test("B receives exactly what A sent, through real encode/decode, for caller-supplied content")
    func pairedExchangeRoundTripsCallerSuppliedBody() async throws {
        let (clientA, clientB, clock) = try await connectedPair(seed: 7)

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
        let (clientA, clientB, clock) = try await connectedPair(seed: 55)

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
        let (clientA, _, _) = try await connectedPair(seed: 8)
        await clientA.cancelSend(messageIdHex: "deadbeef")  // never sent; must not crash or throw
    }

    @Test("cancelSend on an already-delivered messageIdHex is a no-op: no further status change")
    func cancelSendOnAlreadyDeliveredIsNoOp() async throws {
        let (clientA, clientB, clock) = try await connectedPair(seed: 21)

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
        let (clientA, clientB, clock) = try await connectedPair(scenario: .sendFailure, seed: 34)

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
        let (clientA, _, _) = try await connectedPair(seed: 13)

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
        let (clientA, _, clock) = try await connectedPair(scenario: .degradedThenRecovered, seed: 65)

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
        let (clientA, clientB, clock) = try await connectedPair(seed: 72)

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
        let (clientA, clientB, _) = try await connectedPair(seed: 73)

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

/// C3-28 lifecycle-ownership review fix: direct tests of CONTRACT.md §2's
/// "Target ownership" bullet (a scheduled effect that mutates a client is
/// owned by THAT client's own lifecycle, regardless of which client's call
/// scheduled it) and "`start()` semantics (pinned)" block. Split out from
/// `SimulatedChatTransportClientTests` above purely to keep each `@Suite`
/// struct's body under swiftlint's `type_body_length` limit -- these tests
/// are still ad hoc/out-of-table in exactly the same sense as that suite's
/// (none of the six §3 scenario scripts exercise a receiver disconnecting
/// mid-delivery or a passive-side handshake cancellation).
@Suite("Simulated transport client: target ownership and start() semantics")
struct TargetOwnershipAndStartSemanticsTests {
    @Test(
        """
        receiver-disconnect-with-inbound-send: B.disconnect() between A.send() and scheduled delivery \
        -- B emits nothing after its own disconnect; A's own transfer statuses are unaffected
        """
    )
    func receiverDisconnectWithInboundSend() async throws {
        // CONTRACT.md §2's "Target ownership" bullet, and the reviewer's
        // concrete failure it names directly: B.disconnect() then A's
        // already-scheduled delivery must NOT still emit messageReceived
        // on B, even though it was A's send() that scheduled it.
        let (clientA, clientB, clock) = try await connectedPair(seed: 91)

        clock.advance(toMs: 300)
        let messageIdHex = try await clientA.send(body: "will-be-orphaned")
        // happyPair's send outcome: transmitting@+20 (=320), B receives@+80
        // relative to transmitting (=400), A delivered@+100 relative to
        // transmitting (=420). Disconnect B at 350 -- after `transmitting`
        // has already fired on A, but well before B's scheduled delivery
        // (400) or A's scheduled `delivered` (420) -- so both of those
        // closures are still pending in the shared VirtualClock when B
        // disconnects.
        clock.advance(toMs: 350)
        await clientB.disconnect()

        // Advance well past both of those pending times; if the delivery
        // token were still (incorrectly) owned only by A's own
        // bookkeeping, B would observe messageReceived here despite its
        // own disconnect.
        clock.advance(toMs: 2000)
        await clientA.stop()
        await clientB.stop()

        var receivedOnBAfterDisconnect = false
        for await event in clientB.events {
            if case .messageReceived = event.kind { receivedOnBAfterDisconnect = true }
        }
        #expect(!receivedOnBAfterDisconnect)

        // CONTRACT.md's "The sender's own transfer statuses are unaffected
        // by the receiver's disconnect" -- A's own queued/transmitting
        // sequence must be exactly what happyPair's sendOutcome scripts,
        // not truncated or altered by B's unrelated disconnect. (A never
        // reaches `delivered` here because the simulator models no
        // delivery-failure backchannel and this test's B disconnects
        // before A's own scripted `delivered` fires -- but nothing about
        // that firing is B's concern: A's `delivered` closure lives in A's
        // own bookkeeping, untouched by B's disconnect.)
        var statusesForMessage: [ChatMessageDisplayStatus] = []
        for await event in clientA.events {
            if case .messageStatusChanged(let idHex, let status) = event.kind, idHex == messageIdHex {
                statusesForMessage.append(status)
            }
        }
        #expect(statusesForMessage == [.queued, .transmitting, .delivered])
    }

    @Test(
        """
        passive-side handshake cancellation: B disconnects mid-handshake (scheduled by A.connect()) \
        -- B never emits connectionChanged(connected); A never connects either (both endpoints must \
        be live)
        """
    )
    func passiveSideHandshakeCancellation() async throws {
        // CONTRACT.md §2's "Target ownership" bullet: "a client never
        // observes connectionChanged(connected) after its own
        // disconnect()/stop(), even when the peer's connect() scheduled
        // that transition (passive-side handshake cancellation)." Plus the
        // new pinned "Both endpoints live for connection establishment"
        // bullet (C3-28 terminality review): B going terminal before the
        // scripted `connected` transition fires drops that transition on
        // BOTH sides, not just B's -- so A's own copy must also decline to
        // fire here, even though A itself never disconnected.
        let clock = VirtualClock()
        let (clientA, clientB) = SimulatedChatTransportClient.makePair(
            scenario: .happyPair, seed: 92, clock: clock
        )
        try await clientA.start()
        try await clientB.start()
        clock.advance(toMs: 100)
        try await clientA.connect(toPeer: clientB.localPeerId.hexString)
        // `connect()` schedules `becomeConnected()` on BOTH sides for t=150
        // (ChatSimTiming.connectHandshakeDelayMs); disconnect B here, at
        // t=120, strictly before that handshake step has any chance to
        // fire on either side.
        clock.advance(toMs: 120)
        await clientB.disconnect()

        // Advance well past t=150; if B's own copy of `becomeConnected`
        // were not cancelled by B's own disconnect (this is the passive
        // side -- B never called connect() or disconnect() itself in the
        // buggy scheduling), B would incorrectly transition to `connected`
        // here -- and if A's own copy didn't also check B's terminality at
        // fire time, A would incorrectly connect to a peer that is already
        // gone.
        clock.advance(toMs: 2000)
        await clientA.stop()
        await clientB.stop()

        var bConnectionChanges: [ChatConnectionState] = []
        for await event in clientB.events {
            if case .connectionChanged(let state) = event.kind {
                bConnectionChanges.append(state)
            }
        }
        #expect(!bConnectionChanges.contains(.connected))
        // B's only connectionChanged is its own disconnect(); `stop()`
        // afterward sees B already disconnected and emits nothing further.
        #expect(bConnectionChanges == [.disconnected(reason: "userInitiated")])

        // A never disconnected or stopped itself before B did, but "both
        // endpoints live" means A's own `connecting` -> `connected` step
        // is dropped too, since B (the far end) was already terminal at
        // the moment that step would have fired: A never emits `connected`
        // here. A's `stop()` afterward finds A still `.connecting` (not
        // yet `.disconnected`), so it DOES emit its own
        // `disconnected(reason: "stopped")` -- unlike B, which was already
        // disconnected by the time its `stop()` ran.
        var aConnectionChanges: [ChatConnectionState] = []
        for await event in clientA.events {
            if case .connectionChanged(let state) = event.kind {
                aConnectionChanges.append(state)
            }
        }
        #expect(!aConnectionChanges.contains(.connected))
        #expect(aConnectionChanges == [.connecting, .disconnected(reason: "stopped")])
    }

    @Test(
        """
        active-side handshake cancellation: A.connect() then A.disconnect() before the scripted \
        connected transition -- A never emits connectionChanged(connected); B never connects either \
        (both endpoints must be live)
        """
    )
    func activeSideHandshakeCancellation() async throws {
        // Companion to `passiveSideHandshakeCancellation()` above --
        // exercises the OTHER side of the same handshake-cancellation
        // guarantee, with a name a Kotlin port can mirror directly:
        // disconnecting the side that itself called `connect()`. Reviewer
        // probe P4 (C3-28 terminality review): this flips the prior
        // expectation here -- CONTRACT.md §2's new pinned "Both endpoints
        // live for connection establishment" bullet means B's own copy of
        // `becomeConnected`, though scheduled by A's connect() call and
        // owned by B's own token bookkeeping (so untouched by A's
        // disconnect() as far as cancellation goes), must ALSO check A's
        // terminality at fire time and decline to fire -- B does NOT
        // connect either, even though B itself never disconnected.
        let clock = VirtualClock()
        let (clientA, clientB) = SimulatedChatTransportClient.makePair(
            scenario: .happyPair, seed: 93, clock: clock
        )
        try await clientA.start()
        try await clientB.start()
        clock.advance(toMs: 100)
        try await clientA.connect(toPeer: clientB.localPeerId.hexString)
        clock.advance(toMs: 120)
        await clientA.disconnect()

        clock.advance(toMs: 2000)
        await clientA.stop()
        await clientB.stop()

        var aConnectionChanges: [ChatConnectionState] = []
        for await event in clientA.events {
            if case .connectionChanged(let state) = event.kind {
                aConnectionChanges.append(state)
            }
        }
        #expect(!aConnectionChanges.contains(.connected))
        #expect(aConnectionChanges == [.connecting, .disconnected(reason: "userInitiated")])

        // B never connects: A (the far end) was already terminal at the
        // moment B's own copy of `becomeConnected` would have fired, so
        // "both endpoints live" drops it on B's side too. B's own
        // `connectionState` therefore never leaves its initial
        // `.disconnected(reason: nil)`, so B's own `stop()` sees it
        // already disconnected and emits nothing further -- B's
        // connectionChanged stream is empty.
        var bConnectionChanges: [ChatConnectionState] = []
        for await event in clientB.events {
            if case .connectionChanged(let state) = event.kind {
                bConnectionChanges.append(state)
            }
        }
        #expect(!bConnectionChanges.contains(.connected))
        #expect(bConnectionChanges.isEmpty)
    }

    @Test("start() idempotency and both-started discovery gating (CONTRACT.md §2's start() semantics)")
    func startIdempotencyAndBothStartedGating() async throws {
        // Parity test named so a Kotlin port can mirror it directly.
        // CONTRACT.md §2's "start() semantics (pinned)": start() is
        // idempotent (repeated calls change nothing, schedule nothing);
        // "Discovery is armed only once BOTH clients of a pair have
        // started; the moment the second client starts, each client's
        // peerFound is scheduled at its §3 scenario offset relative to
        // THAT moment" -- not relative to whichever client happened to
        // start first.
        let clock = VirtualClock()
        let (clientA, clientB) = SimulatedChatTransportClient.makePair(
            scenario: .happyPair, seed: 94, clock: clock
        )

        // Repeated, idempotent start() on A alone, well before B starts.
        // If these wrongly scheduled discovery (instead of only the
        // both-started guard doing so), peerFound would later report
        // `discoveredAtMs` relative to t=0, not to B's start() below.
        try await clientA.start()
        try await clientA.start()
        try await clientA.start()

        clock.advance(toMs: 1000)
        // B starts here, at t=1000 -- this is the moment discovery arms.
        // A's three earlier start() calls must not have moved that
        // moment earlier.
        try await clientB.start()
        try await clientB.start()  // also idempotent, after both have started

        clock.advance(toMs: 1100)
        await clientA.stop()
        await clientB.stop()

        var aDiscoveredAtMs: [Int64] = []
        for await event in clientA.events {
            if case .peerFound(let peer) = event.kind { aDiscoveredAtMs.append(peer.discoveredAtMs) }
        }
        var bDiscoveredAtMs: [Int64] = []
        for await event in clientB.events {
            if case .peerFound(let peer) = event.kind { bDiscoveredAtMs.append(peer.discoveredAtMs) }
        }
        // Exactly one peerFound per side (A's three redundant start()
        // calls didn't duplicate it), each timestamped 50ms
        // (ChatSimTiming.peerDiscoveryDelayMs) after B's start() at
        // t=1000 -- not 50ms after A's very first start() at t=0, which
        // is what a bug that armed discovery on the FIRST start() (rather
        // than gating on both) would have produced instead.
        #expect(aDiscoveredAtMs == [1050])
        #expect(bDiscoveredAtMs == [1050])
    }

    @Test("stop() before start() leaves a client permanently unstartable: start() afterward is a no-op")
    func stopBeforeStartPreventsLaterStart() async throws {
        // CONTRACT.md §2's "start() semantics (pinned)": "A stopped client
        // cannot be restarted." Exercises the edge case where stop() is
        // called before start() ever ran (an unusual but legal call
        // order): a subsequent start() must remain a no-op, never arming
        // discovery.
        let clock = VirtualClock()
        let (clientA, clientB) = SimulatedChatTransportClient.makePair(
            scenario: .happyPair, seed: 95, clock: clock
        )
        await clientA.stop()
        try await clientA.start()  // must be a no-op: A was never started, but IS finished
        try await clientB.start()

        clock.advance(toMs: 2000)
        await clientB.stop()

        var aPeerFoundCount = 0
        for await event in clientA.events {
            if case .peerFound = event.kind { aPeerFoundCount += 1 }
        }
        // A's stream was already finished by the first stop() before any
        // of this ran, so nothing -- not even a belated peerFound -- can
        // appear on it.
        #expect(aPeerFoundCount == 0)

        var bPeerFoundCount = 0
        for await event in clientB.events {
            if case .peerFound = event.kind { bPeerFoundCount += 1 }
        }
        // B alone never satisfies "both clients started" (A never
        // successfully started), so B never sees peerFound either.
        #expect(bPeerFoundCount == 0)
    }
}

/// C3-28 terminality review: direct tests of CONTRACT.md §2's three new
/// pinned bullets -- "`disconnect()` is terminal for the client instance,"
/// "Both endpoints live for connection establishment" (see
/// `activeSideHandshakeCancellation`/`passiveSideHandshakeCancellation`
/// above, in `TargetOwnershipAndStartSemanticsTests`, for that bullet's
/// coverage), and "Atomic validation" (satisfied structurally by this
/// platform's single-sequential-caller scope -- see
/// `SimulatedChatTransportClient`'s own "Atomic validation" doc comment;
/// no test here exercises real concurrency, since none is possible in this
/// scope). Split into its own `@Suite` for the same reason noted on
/// `TargetOwnershipAndStartSemanticsTests` above -- keeping each struct's
/// body under swiftlint's `type_body_length` limit.
@Suite("Simulated transport client: terminality (disconnect() is terminal for the client instance)")
struct TerminalityTests {
    @Test(
        """
        disconnect-then-peer-connect: B.disconnect() before A ever connects -- A.connect(toPeer: B) \
        throws terminal, schedules nothing on either side; B emits nothing after its own \
        disconnected(userInitiated) -- reviewer probe P1
        """
    )
    func disconnectThenPeerConnectRejected() async throws {
        // CONTRACT.md §2's new pinned bullet: "a peer's connect() targeting
        // a terminal client is rejected (thrown to the caller) and
        // schedules nothing on either side ... Neither the client's own
        // connect() nor its peer's connect() can reopen a terminal
        // client."
        let clock = VirtualClock()
        let (clientA, clientB) = SimulatedChatTransportClient.makePair(
            scenario: .happyPair, seed: 101, clock: clock
        )
        try await clientA.start()
        try await clientB.start()
        clock.advance(toMs: 50)  // peerFound fires on both sides
        await clientB.disconnect()

        await #expect(throws: ChatSimulatedTransportError.terminal) {
            try await clientA.connect(toPeer: clientB.localPeerId.hexString)
        }

        // Advance well past where a successful handshake would otherwise
        // have completed; if the rejected connect() had scheduled anything
        // anyway, it would surface here.
        clock.advance(toMs: 2000)
        await clientA.stop()
        await clientB.stop()

        var bConnectionChanges: [ChatConnectionState] = []
        for await event in clientB.events {
            if case .connectionChanged(let state) = event.kind {
                bConnectionChanges.append(state)
            }
        }
        // B's only connectionChanged is its own disconnect(); A's rejected
        // connect() attempt contributed nothing further, and B's own
        // stop() afterward finds it already disconnected.
        #expect(bConnectionChanges == [.disconnected(reason: "userInitiated")])

        var aConnectionChanges: [ChatConnectionState] = []
        for await event in clientA.events {
            if case .connectionChanged(let state) = event.kind {
                aConnectionChanges.append(state)
            }
        }
        // A's connect() call threw before mutating any state -- A never
        // even transitions through `connecting`, and A's own `stop()`
        // afterward finds it still at its initial `.disconnected(reason:
        // nil)`, so it too emits nothing.
        #expect(aConnectionChanges.isEmpty)
    }

    @Test(
        """
        send scheduled after receiver's disconnect: connected pair, B.disconnect(), then A.send() -- B \
        must not emit messageReceived even though the effect's captured generation matches at fire \
        time (terminality, not generation equality alone, is the gate) -- reviewer probe P2
        """
    )
    func sendScheduledAfterReceiverDisconnectNeverDelivers() async throws {
        // CONTRACT.md §2's new pinned bullet: "No peer-driven effect may
        // target a terminal client REGARDLESS of when the effect was
        // scheduled ... a terminal target drops the effect even if the
        // effect captured the target's post-disconnect generation." Here
        // the delivery effect is scheduled by `send()` AFTER B is already
        // terminal, so it captures B's already-bumped, post-disconnect
        // `lifecycleGeneration` at schedule time -- that value never
        // changes again (a client's generation only bumps on its own
        // disconnect()/stop(), and B already used its one bump) -- so a
        // generation-equality check alone would incorrectly still match at
        // fire time. Only the explicit terminality check catches this.
        let (clientA, clientB, clock) = try await connectedPair(seed: 106)

        clock.advance(toMs: 200)
        await clientB.disconnect()

        // A's own connectionState is unaffected by B's disconnect
        // (CONTRACT.md's "the sender's own transfer statuses are
        // unaffected by the receiver's disconnect"), so A is still free to
        // send() here.
        clock.advance(toMs: 300)
        let messageIdHex = try await clientA.send(body: "should-never-arrive")

        clock.advance(toMs: 2000)
        await clientA.stop()
        await clientB.stop()

        var receivedOnB = false
        for await event in clientB.events {
            if case .messageReceived = event.kind { receivedOnB = true }
        }
        #expect(!receivedOnB)

        // A's own send outcome still plays out per happyPair's scripted
        // sendOutcome, unaffected by its receiver being gone -- the
        // simulator models no delivery-failure backchannel (CONTRACT.md's
        // schema-limitation note).
        var statusesForMessage: [ChatMessageDisplayStatus] = []
        for await event in clientA.events {
            if case .messageStatusChanged(let idHex, let status) = event.kind, idHex == messageIdHex {
                statusesForMessage.append(status)
            }
        }
        #expect(statusesForMessage == [.queued, .transmitting, .delivered])
    }

    @Test("connect() after this client's own disconnect() throws terminal and schedules nothing")
    func connectAfterOwnDisconnectRejected() async throws {
        // CONTRACT.md §2's new pinned bullet: "`disconnect()` is terminal
        // for the client instance. After it, connect() and send() on that
        // client are rejected as transport misuse; the only permitted
        // subsequent call is stop()."
        let clock = VirtualClock()
        let (clientA, clientB) = SimulatedChatTransportClient.makePair(
            scenario: .happyPair, seed: 107, clock: clock
        )
        try await clientA.start()
        try await clientB.start()
        clock.advance(toMs: 50)
        await clientA.disconnect()

        await #expect(throws: ChatSimulatedTransportError.terminal) {
            try await clientA.connect(toPeer: clientB.localPeerId.hexString)
        }

        clock.advance(toMs: 2000)
        await clientA.stop()
        await clientB.stop()

        var aConnectionChanges: [ChatConnectionState] = []
        for await event in clientA.events {
            if case .connectionChanged(let state) = event.kind {
                aConnectionChanges.append(state)
            }
        }
        // Only the original disconnect(); the rejected connect() attempt
        // mutated nothing, and A's own stop() afterward finds it already
        // disconnected.
        #expect(aConnectionChanges == [.disconnected(reason: "userInitiated")])
    }

    @Test("send() after this client's own disconnect() throws terminal and emits no event")
    func sendAfterOwnDisconnectRejected() async throws {
        // Companion to `connectAfterOwnDisconnectRejected()` above, for
        // send() rather than connect() -- CONTRACT.md §2's same "terminal
        // for the client instance" bullet.
        let (clientA, clientB, _) = try await connectedPair(seed: 108)
        await clientA.disconnect()

        await #expect(throws: ChatSimulatedTransportError.terminal) {
            _ = try await clientA.send(body: "should not send")
        }

        await clientA.stop()
        await clientB.stop()

        var statusChangeCount = 0
        for await event in clientA.events {
            if case .messageStatusChanged = event.kind { statusChangeCount += 1 }
        }
        // The rejected send() never reached the point of assigning a
        // messageId or emitting `queued` -- no messageStatusChanged event
        // of any kind appears.
        #expect(statusChangeCount == 0)
    }

    @Test(
        """
        stop() after disconnect() still finishes the event stream cleanly, with no duplicate \
        connectionChanged
        """
    )
    func stopAfterDisconnectFinishesStreamCleanly() async throws {
        // CONTRACT.md §2's "the only permitted subsequent call is stop()"
        // clause -- stop() after disconnect() must remain well-behaved:
        // no crash, no re-run of cancellation/emission logic disconnect()
        // already performed, and the stream still finishes.
        let (clientA, clientB, _) = try await connectedPair(seed: 109)
        await clientA.disconnect()
        await clientA.stop()

        var connectionChanges: [ChatConnectionState] = []
        // Draining this stream to completion is itself part of what this
        // test verifies: if stop() never finished the stream, this loop
        // would hang instead of returning.
        for await event in clientA.events {
            if case .connectionChanged(let state) = event.kind {
                connectionChanges.append(state)
            }
        }
        // connecting, connected (connectedPair's setup), then exactly ONE
        // disconnected(userInitiated) from disconnect() -- stop()
        // afterward finds A already disconnected (CONTRACT.md's "unless
        // the state is already disconnected" clause) and emits nothing
        // further, so no second disconnectedreason appears.
        #expect(connectionChanges == [.connecting, .connected, .disconnected(reason: "userInitiated")])

        await clientB.stop()
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
