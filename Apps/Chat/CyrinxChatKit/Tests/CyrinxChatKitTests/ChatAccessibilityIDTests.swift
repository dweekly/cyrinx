import Testing

@testable import CyrinxChatKit

/// Verbatim conformance of `ChatAccessibilityID` / `ChatLaunchArgs` to
/// CONTRACT.md §5's registry table -- both platforms must share these exact
/// string values, since they cross into UI-test/instrumentation tooling
/// that matches on the literal string, not the Swift/Kotlin identifier.
@Suite("Accessibility identifiers and launch arguments: verbatim registry match")
struct ChatAccessibilityIDTests {
    /// CONTRACT.md §5's accessibility-identifier table, transcribed
    /// verbatim as (constant, expected string value) pairs.
    static let expectedAccessibilityIDs: [(name: String, value: String, expected: String)] = [
        ("peerList", ChatAccessibilityID.peerList, "chat.peerList"),
        ("peerRow", ChatAccessibilityID.peerRow, "chat.peerRow"),
        ("connectButton", ChatAccessibilityID.connectButton, "chat.connectButton"),
        ("connectionBanner", ChatAccessibilityID.connectionBanner, "chat.connectionBanner"),
        ("linkBudgetBadge", ChatAccessibilityID.linkBudgetBadge, "chat.linkBudgetBadge"),
        ("messageList", ChatAccessibilityID.messageList, "chat.messageList"),
        ("messageRow", ChatAccessibilityID.messageRow, "chat.messageRow"),
        ("messageStatus", ChatAccessibilityID.messageStatus, "chat.messageStatus"),
        ("composerField", ChatAccessibilityID.composerField, "chat.composerField"),
        ("sendButton", ChatAccessibilityID.sendButton, "chat.sendButton"),
        ("diagnosticsButton", ChatAccessibilityID.diagnosticsButton, "chat.diagnosticsButton"),
        ("unauthenticatedNotice", ChatAccessibilityID.unauthenticatedNotice, "chat.unauthenticatedNotice"),
        ("errorBanner", ChatAccessibilityID.errorBanner, "chat.errorBanner"),
        ("messageGapNotice", ChatAccessibilityID.messageGapNotice, "chat.messageGapNotice"),
    ]

    @Test(
        "each ChatAccessibilityID constant matches its CONTRACT.md §5 string value verbatim",
        arguments: expectedAccessibilityIDs
    )
    func accessibilityIdMatchesContractTable(_ entry: (name: String, value: String, expected: String)) {
        #expect(entry.value == entry.expected, "ChatAccessibilityID.\(entry.name)")
    }

    @Test("ChatAccessibilityID registry has exactly the 14 constants CONTRACT.md §5 lists")
    func accessibilityIdCountMatchesContractTable() {
        #expect(Self.expectedAccessibilityIDs.count == 14)
    }

    /// CONTRACT.md §5's launch/instrumentation-argument table, transcribed
    /// verbatim.
    static let expectedLaunchArgs: [(name: String, value: String, expected: String)] = [
        ("scenario", ChatLaunchArgs.scenario, "chat.scenario"),
        ("seed", ChatLaunchArgs.seed, "chat.seed"),
        ("simulated", ChatLaunchArgs.simulated, "chat.simulated"),
    ]

    @Test(
        "each ChatLaunchArgs key matches its CONTRACT.md §5 string value verbatim",
        arguments: expectedLaunchArgs
    )
    func launchArgMatchesContractTable(_ entry: (name: String, value: String, expected: String)) {
        #expect(entry.value == entry.expected, "ChatLaunchArgs.\(entry.name)")
    }
}
