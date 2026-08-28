import CyrinxChatKit
import Testing

@testable import CyrinxChatApp

/// `ChatLaunchArgParsing` coverage: CONTRACT.md §5's three launch
/// arguments (`chat.scenario`/`chat.seed`/`chat.simulated`), defaults, and
/// malformed-input fallback ("a typo must never make the app
/// un-launchable").
@Suite("ChatLaunchArgParsing")
struct ChatLaunchArgOptionsTests {
    @Test("no arguments yields the documented defaults")
    func noArgumentsYieldsDefaults() {
        let options = ChatLaunchArgParsing.parse([])
        #expect(options.scenario == ChatLaunchArgParsing.defaultScenario)
        #expect(options.seed == ChatLaunchArgParsing.defaultSeed)
        #expect(options.simulated == ChatLaunchArgParsing.defaultSimulated)
    }

    @Test("all three recognized flags are parsed")
    func allThreeFlagsParsed() {
        let options = ChatLaunchArgParsing.parse([
            "-chat.scenario", "peerLoss",
            "-chat.seed", "42",
            "-chat.simulated", "false",
        ])
        #expect(options.scenario == .peerLoss)
        #expect(options.seed == 42)
        #expect(options.simulated == false)
    }

    @Test("every ChatScenario case round-trips through its rawValue", arguments: ChatScenario.allCases)
    func everyScenarioRoundTrips(_ scenario: ChatScenario) {
        let options = ChatLaunchArgParsing.parse(["-chat.scenario", scenario.rawValue])
        #expect(options.scenario == scenario)
    }

    @Test(
        "boolean spellings for chat.simulated",
        arguments: [
            ("true", true), ("TRUE", true), ("yes", true), ("1", true),
            ("false", false), ("FALSE", false), ("no", false), ("0", false),
        ]
    )
    func booleanSpellings(_ pair: (raw: String, expected: Bool)) {
        let options = ChatLaunchArgParsing.parse(["-chat.simulated", pair.raw])
        #expect(options.simulated == pair.expected)
    }

    @Test("an unknown scenario name falls back to the default rather than crashing")
    func unknownScenarioFallsBackToDefault() {
        let options = ChatLaunchArgParsing.parse(["-chat.scenario", "notARealScenario"])
        #expect(options.scenario == ChatLaunchArgParsing.defaultScenario)
    }

    @Test("a non-numeric seed falls back to the default")
    func nonNumericSeedFallsBackToDefault() {
        let options = ChatLaunchArgParsing.parse(["-chat.seed", "not-a-number"])
        #expect(options.seed == ChatLaunchArgParsing.defaultSeed)
    }

    @Test("an unrecognized boolean spelling falls back to the default")
    func unrecognizedBooleanFallsBackToDefault() {
        let options = ChatLaunchArgParsing.parse(["-chat.simulated", "maybe"])
        #expect(options.simulated == ChatLaunchArgParsing.defaultSimulated)
    }

    @Test("unrelated launch arguments (e.g. Xcode/XCTest injected flags) are skipped harmlessly")
    func unrelatedArgumentsAreSkipped() {
        let options = ChatLaunchArgParsing.parse([
            "-NSDocumentRevisionsDebugMode", "YES",
            "-chat.scenario", "sendFailure",
            "-SomeOtherFlag",
        ])
        #expect(options.scenario == .sendFailure)
    }

    @Test("a recognized flag with no following value leaves the default untouched")
    func flagWithMissingValueLeavesDefault() {
        let options = ChatLaunchArgParsing.parse(["-chat.scenario"])
        #expect(options.scenario == ChatLaunchArgParsing.defaultScenario)
    }
}
