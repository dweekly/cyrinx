import CyrinxChatKit
import XCTest

/// Degraded-link and failed-message flows -- driven entirely by
/// CONTRACT.md §5's launch arguments against the simulated transport, per
/// the C3-29 task brief's macOS XCUITest requirement. See
/// `DiscoveryConnectSendReceiveTests`'s header comment for the CI-manual-
/// gate note; identical here.
@MainActor
final class DegradedAndFailedFlowTests: XCTestCase {
    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    func testDegradedThenRecoveredShowsAndClearsBanner() throws {
        let app = ChatUITestSupport.launch(scenario: "degradedThenRecovered", seed: 3)
        defer { app.terminate() }

        let peerBrowser = PeerBrowserPageObject(app: app)
        peerBrowser.connectToFirstPeer()

        let conversation = ConversationPageObject(app: app)
        conversation.messageList.assertExists("expected the conversation screen after connecting")

        // CONTRACT.md §3.3: connected@150, linkBudget(text)@200,
        // degraded@400 -- the banner (design brief's pinned "Link degraded
        // — move devices closer") must appear.
        conversation.connectionBanner.assertExists(
            timeout: ChatUITestSupport.defaultTimeout, "expected the degraded banner to appear"
        )
        XCTAssertTrue(
            conversation.connectionBanner.label.contains("degraded")
                || conversation.connectionBanner.label.contains("Degraded"),
            "expected the banner text to mention the degraded link, got: \(conversation.connectionBanner.label)"
        )

        // CONTRACT.md §3.3: connected@700 (recovered) -- the banner must
        // clear again. `connectionBanner` only exists in the view hierarchy
        // while `ChatModel.banner` is non-nil (`ChatRootView`'s `if let
        // banner = ...`), so waiting for it to stop existing is the correct
        // "cleared" signal, not a fixed sleep.
        let deadline = Date().addingTimeInterval(ChatUITestSupport.defaultTimeout)
        var bannerCleared = false
        while Date() < deadline {
            if !conversation.connectionBanner.exists {
                bannerCleared = true
                break
            }
            RunLoop.current.run(until: Date().addingTimeInterval(0.1))
        }
        XCTAssertTrue(bannerCleared, "expected the degraded banner to clear once the link recovered")
    }

    func testSendFailureShowsFailedMessageStatus() throws {
        let app = ChatUITestSupport.launch(scenario: "sendFailure", seed: 9)
        defer { app.terminate() }

        let peerBrowser = PeerBrowserPageObject(app: app)
        peerBrowser.connectToFirstPeer()

        let conversation = ConversationPageObject(app: app)
        conversation.send("this will fail")
        conversation.waitForMessageRowCount(atLeast: 1)

        // CONTRACT.md §3.4: `messageStatusChanged(msg1, failed, reason:
        // "noAcknowledgment")` at t=450 -- shown as text, never color
        // alone, per `MessageRowView`'s design.
        let deadline = Date().addingTimeInterval(ChatUITestSupport.defaultTimeout)
        var sawFailed = false
        while Date() < deadline {
            if let label = conversation.messageStatusLabels().first, label.label.contains("Failed") {
                sawFailed = true
                break
            }
            RunLoop.current.run(until: Date().addingTimeInterval(0.1))
        }
        XCTAssertTrue(sawFailed, "expected the message status to reach Failed")
    }
}
