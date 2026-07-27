import CyrinxChatKit
import XCTest

/// Page object for `ConversationView`/`ComposerView`/`ConnectionBannerView`/
/// `LinkBudgetBadgeView` (Shared/): connect confirmation, send, receive,
/// degraded, and failed-message flows. Every lookup goes through
/// `ChatAccessibilityID` (CONTRACT.md §5's pinned registry). `@MainActor`:
/// recent XCTest SDKs isolate `XCUIElement`/`XCUIApplication` querying
/// members to the main actor.
@MainActor
struct ConversationPageObject {
    let app: XCUIApplication

    var messageList: XCUIElement {
        app.scrollViews[ChatAccessibilityID.messageList]
    }

    var composerField: XCUIElement {
        app.textFields[ChatAccessibilityID.composerField]
    }

    var sendButton: XCUIElement {
        app.buttons[ChatAccessibilityID.sendButton]
    }

    var connectionBanner: XCUIElement {
        app.descendants(matching: .any)[ChatAccessibilityID.connectionBanner]
    }

    var linkBudgetBadge: XCUIElement {
        app.descendants(matching: .any)[ChatAccessibilityID.linkBudgetBadge]
    }

    var errorBanner: XCUIElement {
        app.staticTexts[ChatAccessibilityID.errorBanner]
    }

    func messageRows() -> [XCUIElement] {
        app.descendants(matching: .any).matching(identifier: ChatAccessibilityID.messageRow)
            .allElementsBoundByIndex
    }

    func messageStatusLabels() -> [XCUIElement] {
        app.descendants(matching: .any).matching(identifier: ChatAccessibilityID.messageStatus)
            .allElementsBoundByIndex
    }

    /// Types `text` into the composer and taps Send.
    func send(_ text: String) {
        let field = composerField.assertExists("expected the composer field to be visible")
        field.click()
        field.typeText(text)
        sendButton.assertExists("expected the Send button to be visible").click()
    }

    /// Waits for at least `count` message rows to be present.
    @discardableResult
    func waitForMessageRowCount(
        atLeast count: Int, timeout: TimeInterval = ChatUITestSupport.defaultTimeout
    ) -> [XCUIElement] {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            let rows = messageRows()
            if rows.count >= count { return rows }
            // XCTest has no async/await bridge for element polling; a short,
            // bounded `RunLoop` spin (not a `sleep`) is the standard XCUITest
            // idiom for "wait, then re-query the accessibility tree,"
            // asserted fail-closed by the `XCTFail` below if the deadline
            // passes first.
            RunLoop.current.run(until: Date().addingTimeInterval(0.1))
        }
        XCTFail("expected at least \(count) message row(s) within \(timeout)s, found \(messageRows().count)")
        return messageRows()
    }
}
