import Foundation
import Testing

@testable import CyrinxChatKit

/// Scenario-script conformance: every scenario matches the exact event
/// counts CONTRACT.md §3's tables pin, plus the determinism guarantee
/// CONTRACT.md §4 requires ("Same (scenarioName, seed) on both platforms
/// must produce byte-identical trace files").
@Suite("Scenario runner: determinism and shape")
struct ChatScenarioRunnerTests {
    /// Total emitted events (both clients combined) pinned by each
    /// scenario's table in CONTRACT.md §3.1-3.6.
    static func expectedTotalEvents(_ scenario: ChatScenario) -> Int {
        switch scenario {
        case .happyPair: return 10  // §3.1: 7 (A) + 3 (B)
        case .peerLoss: return 9  // §3.2: 5 (A) + 4 (B)
        case .degradedThenRecovered: return 10  // §3.3: 8 (A) + 2 (B)
        case .sendFailure: return 8  // §3.4: 6 (A) + 2 (B)
        case .duplicateIncoming: return 9  // §3.5: 6 (A) + 3 (B)
        case .slowLink: return 10  // §3.6: 7 (A) + 3 (B)
        }
    }

    @Test("scenario produces the exact pinned total event count", arguments: ChatScenario.allCases)
    func scenarioMatchesPinnedEventCount(_ scenario: ChatScenario) async throws {
        let records = try await ChatScenarioRunner.runToCompletion(scenario: scenario, seed: 0xABCD_EF01)
        #expect(records.count == Self.expectedTotalEvents(scenario), "scenario \(scenario.rawValue)")
    }

    @Test(
        "every scenario stays at or under 15 total events (design brief guidance)",
        arguments: ChatScenario.allCases
    )
    func scenarioStaysUnderFifteenEvents(_ scenario: ChatScenario) async throws {
        let records = try await ChatScenarioRunner.runToCompletion(scenario: scenario, seed: 42)
        #expect(records.count <= 15, "scenario \(scenario.rawValue)")
    }

    @Test("same scenario+seed run twice yields byte-identical traces", arguments: ChatScenario.allCases)
    func sameSeedIsFullyDeterministic(_ scenario: ChatScenario) async throws {
        let seed: UInt64 = 0xC0FFEE_1234
        let first = try await ChatScenarioRunner.runToCompletion(scenario: scenario, seed: seed)
        let second = try await ChatScenarioRunner.runToCompletion(scenario: scenario, seed: seed)

        let firstLines = first.map { $0.canonicalJSONLine() }
        let secondLines = second.map { $0.canonicalJSONLine() }
        #expect(firstLines == secondLines, "scenario \(scenario.rawValue)")
    }

    @Test("different seeds yield different discovered peer IDs")
    func differentSeedsYieldDifferentPeerIds() async throws {
        let recordsSeed1 = try await ChatScenarioRunner.runToCompletion(scenario: .happyPair, seed: 1)
        let recordsSeed2 = try await ChatScenarioRunner.runToCompletion(scenario: .happyPair, seed: 2)

        let peerIdSeed1 = try #require(Self.firstPeerFoundId(in: recordsSeed1))
        let peerIdSeed2 = try #require(Self.firstPeerFoundId(in: recordsSeed2))
        #expect(peerIdSeed1 != peerIdSeed2)
    }

    private static func firstPeerFoundId(in records: [ChatTraceRecord]) -> Data? {
        for record in records {
            if case .peerFound(let peer) = record.event {
                return peer.id
            }
        }
        return nil
    }

    @Test("duplicateIncoming yields exactly one messageReceived on B")
    func duplicateIncomingDedupsToExactlyOne() async throws {
        let records = try await ChatScenarioRunner.runToCompletion(scenario: .duplicateIncoming, seed: 99)
        let receivedOnB = records.filter { record in
            guard record.client == .b else { return false }
            if case .messageReceived = record.event { return true }
            return false
        }
        #expect(receivedOnB.count == 1)
    }

    @Test("sendFailure never delivers a messageReceived to B")
    func sendFailureNeverDeliversToB() async throws {
        let records = try await ChatScenarioRunner.runToCompletion(scenario: .sendFailure, seed: 17)
        let receivedOnB = records.filter { record in
            guard record.client == .b else { return false }
            if case .messageReceived = record.event { return true }
            return false
        }
        #expect(receivedOnB.isEmpty)
    }

    @Test("eventSeq is monotonic from 0, per client, across every scenario", arguments: ChatScenario.allCases)
    func eventSeqIsMonotonicPerClient(_ scenario: ChatScenario) async throws {
        let records = try await ChatScenarioRunner.runToCompletion(scenario: scenario, seed: 123)
        for role in [ChatClientRole.a, ChatClientRole.b] {
            let seqs = records.filter { $0.client == role }.map { $0.eventSeq }
            #expect(seqs.first == 0, "scenario \(scenario.rawValue), client \(role.rawValue)")
            for (previous, current) in zip(seqs, seqs.dropFirst()) {
                #expect(
                    current == previous + 1,
                    "scenario \(scenario.rawValue), client \(role.rawValue): non-contiguous eventSeq"
                )
            }
        }
    }

    @Test("slowLink observes transmitting (not a terminal state) at t=1000")
    func slowLinkDwellsInTransmittingMidScenario() async throws {
        // CONTRACT.md §3.6: transmitting@320, B receives@2450, A
        // delivered@2500 -- at t=1000 the message must still read
        // `transmitting`, not have skipped ahead to a terminal state.
        let records = try await ChatScenarioRunner.runToCompletion(scenario: .slowLink, seed: 321)
        let statusChangesAtOrBefore1000 = records.filter { record in
            guard record.client == .a, record.virtualTimeMs <= 1000 else { return false }
            if case .messageStatusChanged = record.event { return true }
            return false
        }
        let lastStatus = statusChangesAtOrBefore1000.last
        guard case .messageStatusChanged(_, let status) = lastStatus?.event else {
            Issue.record("expected at least one messageStatusChanged at or before t=1000")
            return
        }
        #expect(status == .transmitting)
    }
}
