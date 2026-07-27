import Foundation
import Testing

@testable import CyrinxChatKit

/// Compares this package's in-memory generated `happyPair` and `peerLoss`
/// traces byte-for-byte against the committed goldens in
/// `../fixtures/traces/*.jsonl` -- CONTRACT.md §4's "Golden trace fixtures
/// (pinned)": "the committed goldens are `fixtures/traces/happyPair.jsonl`
/// and `fixtures/traces/peerLoss.jsonl`, generated with seed 1 by
/// `CyrinxChatKit`'s `chat-trace-gen` executable. BOTH platforms assert
/// their own generated traces byte-identical to these files ... and both
/// comparisons FAIL -- never skip -- when a fixture file is missing,
/// because they are merge-gate evidence."
///
/// Mirrors the *intent* of Kotlin's `ChatTraceGoldenComparisonTest`
/// (`Apps/Chat/android/chatkit`) -- same seed, same two scenarios, same
/// byte-for-byte comparison -- but deliberately NOT that class's own
/// `org.junit.Assume.assumeTrue`-based skip when a fixture is absent.
/// CONTRACT.md's amended "Golden trace fixtures (pinned)" text supersedes
/// that skip behavior; this suite fails via `Issue.record` instead, which
/// Swift Testing reports as a genuine test failure, not a skip.
@Suite("Golden trace fixtures: happyPair and peerLoss byte-identity")
struct ChatTraceGoldenComparisonTests {
    /// CONTRACT.md §4's "Golden trace fixtures (pinned)": "generated with
    /// seed 1."
    static let goldenTraceSeed: UInt64 = 1

    /// Locates `Apps/Chat/fixtures/traces/<filename>` relative to this
    /// source file: `Tests/CyrinxChatKitTests/<this file>` -> (package
    /// root) `CyrinxChatKit` -> `../fixtures/traces/<filename>` -- the same
    /// relative-path derivation `GoldenFixture.fixtureURL(sourceFile:)`
    /// (`TestSupport.swift`) uses for the envelope golden vectors.
    static func tracesFixtureURL(_ filename: String, sourceFile: String = #filePath) -> URL {
        let packageRoot = URL(fileURLWithPath: sourceFile)
            .deletingLastPathComponent()  // Tests/CyrinxChatKitTests
            .deletingLastPathComponent()  // Tests
            .deletingLastPathComponent()  // CyrinxChatKit (package root)
        return
            packageRoot
            .appendingPathComponent("..")
            .appendingPathComponent("fixtures")
            .appendingPathComponent("traces")
            .appendingPathComponent(filename)
            .standardizedFileURL
    }

    /// Renders `scenario` at `goldenTraceSeed` to the exact JSON-lines text
    /// `chat-trace-gen` writes to disk: one `canonicalJSONLine()` per
    /// record, newline-joined, with a single trailing newline
    /// (`Sources/ChatTraceGen/main.swift`), generated here purely in-memory
    /// via the same `ChatScenarioRunner` the executable itself calls.
    static func renderTrace(scenario: ChatScenario) async throws -> String {
        let records = try await ChatScenarioRunner.runToCompletion(scenario: scenario, seed: goldenTraceSeed)
        return records.map { $0.canonicalJSONLine() }.joined(separator: "\n") + "\n"
    }

    /// Reads `url`'s contents, or fails (via `Issue.record`, never a skip)
    /// with a message identifying CONTRACT.md's pinned reason a missing
    /// fixture is a hard failure, not a soft skip.
    static func requireGoldenContents(at url: URL) throws -> String? {
        guard let contents = try? String(contentsOf: url, encoding: .utf8) else {
            Issue.record(
                """
                \(url.path) not found. CONTRACT.md §4's "Golden trace fixtures (pinned)" \
                requires this comparison to FAIL, never skip, when a fixture file is missing \
                -- these are merge-gate evidence, not optional artifacts.
                """
            )
            return nil
        }
        return contents
    }

    @Test("happyPair trace matches fixtures/traces/happyPair.jsonl byte-for-byte")
    func happyPairMatchesGoldenFile() async throws {
        let url = Self.tracesFixtureURL("happyPair.jsonl")
        guard let goldenContents = try Self.requireGoldenContents(at: url) else { return }
        let actual = try await Self.renderTrace(scenario: .happyPair)
        #expect(actual == goldenContents)
    }

    @Test("peerLoss trace matches fixtures/traces/peerLoss.jsonl byte-for-byte")
    func peerLossMatchesGoldenFile() async throws {
        let url = Self.tracesFixtureURL("peerLoss.jsonl")
        guard let goldenContents = try Self.requireGoldenContents(at: url) else { return }
        let actual = try await Self.renderTrace(scenario: .peerLoss)
        #expect(actual == goldenContents)
    }
}
