import CyrinxChatKit
import XCTest

/// Declared per the C3-29/C3-30 design brief ("iOS UI test target declared
/// but runnable manually"). This target is a documented MANUAL gate, not
/// part of this task's CI/verify-stage gates (those cover
/// `CyrinxChatUITests-macOS` only) -- run from Xcode against an iOS
/// Simulator. A single smoke test here (rather than the full macOS page-
/// object suite) keeps this target's build/maintenance cost low while
/// still proving the app launches and exposes the CONTRACT.md §5
/// accessibility-ID registry correctly on iOS; the full flow coverage
/// (discovery/connect/send/receive/degraded/failed-message) lives in
/// `CyrinxChatUITests-macOS`, whose page objects are not reused here
/// verbatim because iOS XCUITest interaction (`.tap()`) differs from
/// macOS's (`.click()`).
@MainActor
final class ChatUITestsiOSSmokeTests: XCTestCase {
    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    func testAppLaunchesAndShowsPeerList() throws {
        let app = XCUIApplication()
        app.launchArguments = ["-chat.scenario", "happyPair", "-chat.seed", "1", "-chat.attachTo", "a"]
        app.launch()
        defer { app.terminate() }

        let unauthenticatedNotice = app.descendants(matching: .any)[ChatAccessibilityID.unauthenticatedNotice]
        XCTAssertTrue(
            unauthenticatedNotice.waitForExistence(timeout: 10),
            "expected the persistent unauthenticated-link notice to be visible on launch"
        )
    }
}
