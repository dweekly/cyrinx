import Foundation
import Testing

@testable import CyrinxChatKit

/// C3-28 round-5 fix pass: direct tests of CONTRACT.md §2's "Post-connect
/// script admission (pinned)" bullet -- a scenario's post-connect timeline
/// is admitted only by a successful joint `connected` emission; a dropped
/// handshake cancels the remainder of the scenario script on BOTH sides,
/// no matter which side (or neither) ends up terminal.
@Suite("Simulated transport client: post-connect script admission (dropped handshake)")
struct PostConnectScriptAdmissionTests {
    @Test(
        """
        R3 (round-5 reviewer probe): degradedThenRecovered, A.connect() at t=100, B.disconnect() \
        at t=120 -- strictly before the t=150 joint handshake step has any chance to fire on \
        either side -- drops the ENTIRE post-connect script on BOTH sides. Every one of \
        degradedThenRecovered's post-connect steps (linkBudget@200, degraded@400, \
        linkBudget@410, connectionRecoveredConnected@700, linkBudget@710) targets A alone, and A \
        itself never disconnects or goes terminal here -- so this specifically proves \
        `isEstablished` (not per-client terminality alone) is what gates admission: neither A nor \
        B ever emits `degraded` or `connected`; A shows only its own `connecting` plus its own \
        later lifecycle events.
        """
    )
    func degradedThenRecoveredDroppedHandshakeCancelsEntireScriptOnBothSides() async throws {
        let clock = VirtualClock()
        let (clientA, clientB) = SimulatedChatTransportClient.makePair(
            scenario: .degradedThenRecovered, seed: 201, clock: clock
        )
        try await clientA.start()
        try await clientB.start()
        clock.advance(toMs: 100)
        try await clientA.connect(toPeer: clientB.localPeerId.hexString)
        // Strictly before the t=150 joint `connected` handshake step
        // (ChatSimTiming.connectHandshakeDelayMs) has any chance to fire
        // on either side.
        clock.advance(toMs: 120)
        await clientB.disconnect()

        // Advance through degradedThenRecovered's ENTIRE pinned timeline
        // and past it (§3.3's last pinned event is at t=710 relative to a
        // successful connect; `scriptEndMs` is 800) -- if the post-connect
        // admission gap this pin closes were still open, A's own
        // post-connect steps would incorrectly fire here despite the
        // handshake never having jointly completed: `becomeConnected()`'s
        // own "Both endpoints live" check already stops A from ever
        // emitting `connected` itself (B is terminal at A's own copy's
        // fire time, t=150), but -- before this round-5 fix -- nothing
        // stopped `applyPostConnect(_:)` from still firing A's later
        // `degraded`/`connectionRecoveredConnected`/`linkBudgetChanged`
        // steps regardless, since those were scheduled unconditionally at
        // `connect()` time and only ever checked A's OWN terminality
        // (which never flips here -- A itself never disconnects).
        clock.advance(toMs: ChatScenario.degradedThenRecovered.scriptEndMs)
        await clientA.stop()
        await clientB.stop()

        var aConnectionChanges: [ChatConnectionState] = []
        var aSawLinkBudgetChanged = false
        for await event in clientA.events {
            switch event.kind {
            case .connectionChanged(let state):
                aConnectionChanges.append(state)
            case .linkBudgetChanged:
                aSawLinkBudgetChanged = true
            default:
                break
            }
        }
        // A's own copy of becomeConnected() declines (B is terminal at
        // fire time -- "Both endpoints live for connection
        // establishment"), so `isEstablished` never latches `true` on A
        // either: connecting (from connect()), then nothing else from the
        // dropped handshake or its post-connect script, then A's own
        // stop() sees it still `.connecting` (never reached `.connected`
        // or `.disconnected`) and emits its own
        // disconnected(reason: "stopped") -- matching
        // `TargetOwnershipAndStartSemanticsTests
        // .passiveSideHandshakeCancellation`'s identical shape for the
        // simpler `happyPair` case.
        #expect(aConnectionChanges == [.connecting, .disconnected(reason: "stopped")])
        #expect(!aConnectionChanges.contains(.connected))
        #expect(!aConnectionChanges.contains(.degraded))
        #expect(!aSawLinkBudgetChanged)

        var bConnectionChanges: [ChatConnectionState] = []
        for await event in clientB.events {
            if case .connectionChanged(let state) = event.kind {
                bConnectionChanges.append(state)
            }
        }
        // B's only event ever is its own disconnect(); B's own stop()
        // afterward finds it already disconnected and emits nothing
        // further -- and, since none of degradedThenRecovered's
        // post-connect steps target B at all (every one targets `.a`),
        // this also confirms B was never a delivery target of any
        // (correctly) dropped step either.
        #expect(bConnectionChanges == [.disconnected(reason: "userInitiated")])
        #expect(!bConnectionChanges.contains(.connected))
        #expect(!bConnectionChanges.contains(.degraded))
    }
}
