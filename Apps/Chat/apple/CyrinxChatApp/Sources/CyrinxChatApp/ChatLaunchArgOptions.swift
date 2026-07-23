import CyrinxChatKit
import Foundation

/// Parsed values for CONTRACT.md §5's three launch arguments
/// (`ChatLaunchArgs.scenario`/`.seed`/`.simulated`, `CyrinxChatKit`'s
/// registry). Apple: "process launch arguments, e.g. `-chat.scenario
/// happyPair`" (CONTRACT.md §5).
public struct ChatLaunchArgOptions: Equatable, Sendable {
    public let scenario: ChatScenario
    public let seed: UInt64
    /// `false` selects the live SDK adapter -- not available until C3-31;
    /// this sample always simulates through C3-28-C3-30 regardless of this
    /// value (CONTRACT.md §5), but the value is still parsed and carried
    /// so the eventual C3-31 wiring has nothing to add here.
    public let simulated: Bool

    public init(scenario: ChatScenario, seed: UInt64, simulated: Bool) {
        self.scenario = scenario
        self.seed = seed
        self.simulated = simulated
    }
}

/// Parses CONTRACT.md §5's launch arguments from `ProcessInfo`/
/// `CommandLine`-style arguments. Malformed or missing values fall back to
/// documented defaults -- a typo in a launch argument must never make the
/// app un-launchable.
public enum ChatLaunchArgParsing {
    /// **DECISION (not pinned by the brief or CONTRACT.md):** `happyPair`
    /// is the least surprising default scenario for a plain, no-launch-arg
    /// app launch (double-clicking the built app, or Xcode's default
    /// scheme) -- it is the scenario CONTRACT.md §3.1 describes as "Clean
    /// discovery -> connect -> send -> deliver, no faults," demonstrating
    /// the most `ChatEvent` kinds with none of them being an error path.
    public static let defaultScenario = ChatScenario.happyPair
    /// **DECISION (not pinned by the brief or CONTRACT.md):** matches the
    /// seed the C3-29 verify stage's `chat-model-trace-gen` gate and
    /// CONTRACT.md §4's committed golden traces both use.
    public static let defaultSeed: UInt64 = 1
    public static let defaultSimulated = true

    public static func parse(
        _ arguments: [String] = Array(CommandLine.arguments.dropFirst())
    ) -> ChatLaunchArgOptions {
        var scenario = defaultScenario
        var seed = defaultSeed
        var simulated = defaultSimulated

        // Iterator-based scan, matching `chat-trace-gen`'s own argument
        // parser (Apps/Chat/CyrinxChatKit/Sources/ChatTraceGen/main.swift):
        // only a RECOGNIZED flag consumes the following token as its
        // value, so an unrelated/unknown launch argument (Xcode/XCTest
        // inject plenty) is skipped one token at a time and can never be
        // mistaken for one of ours, nor swallow one of ours as its own
        // value.
        var iterator = arguments.makeIterator()
        while let arg = iterator.next() {
            switch arg {
            case "-\(ChatLaunchArgs.scenario)":
                if let value = iterator.next(), let parsed = ChatScenario(rawValue: value) {
                    scenario = parsed
                }
            case "-\(ChatLaunchArgs.seed)":
                if let value = iterator.next(), let parsed = UInt64(value) {
                    seed = parsed
                }
            case "-\(ChatLaunchArgs.simulated)":
                if let value = iterator.next(), let parsed = Self.parseBool(value) {
                    simulated = parsed
                }
            default:
                break
            }
        }

        return ChatLaunchArgOptions(scenario: scenario, seed: seed, simulated: simulated)
    }

    private static func parseBool(_ raw: String) -> Bool? {
        switch raw.lowercased() {
        case "true", "yes", "1": return true
        case "false", "no", "0": return false
        default: return nil
        }
    }
}
