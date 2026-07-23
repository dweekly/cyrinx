import CyrinxChatKit
import XCTest

/// Discovery, connect, send, and receive flows -- driven entirely by
/// CONTRACT.md §5's launch arguments against the simulated transport, per
/// the C3-29 task brief's macOS XCUITest requirement. No audio, no two
/// physical devices.
///
/// CI note: NOT wired into `.github/workflows/chat-c3-28.yml` in this
/// round -- the C3-29/C3-30 design brief's own "CI (orchestrator edits
/// only)" section is explicit that "UI-test jobs are NOT added to CI in
/// this round ... keep runners fast and reliable." This target is meant to
/// be the one cheap enough to add later; until then it is a documented
/// manual gate, run locally via `xcodebuild test -scheme CyrinxChat-macOS
/// -only-testing:CyrinxChatUITests-macOS` (or from Xcode) on a machine with
/// an interactive login session (XCUITest needs to actually draw windows).
@MainActor
final class DiscoveryConnectSendReceiveTests: XCTestCase {
    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    func testDiscoverConnectAndSendShowsDelivered() throws {
        let app = ChatUITestSupport.launch(scenario: "happyPair", seed: 1)
        defer { app.terminate() }

        let peerBrowser = PeerBrowserPageObject(app: app)
        peerBrowser.connectToFirstPeer()

        let conversation = ConversationPageObject(app: app)
        conversation.messageList.assertExists("expected the conversation screen after connecting")
        conversation.linkBudgetBadge.assertExists("expected a link-budget badge once connected")

        conversation.send("hello from the UI test")
        let rows = conversation.waitForMessageRowCount(atLeast: 1)
        XCTAssertEqual(rows.count, 1, "expected exactly one message row after one send")

        // CONTRACT.md §3.1's happyPair timeline ends `delivered` -- the
        // status label (an independent accessibility element per
        // `MessageRowView`'s own design note) should read exactly that,
        // never color alone.
        let statusLabels = conversation.messageStatusLabels()
        XCTAssertFalse(statusLabels.isEmpty, "expected a status label on the sent message")
        let deadline = Date().addingTimeInterval(ChatUITestSupport.defaultTimeout)
        var sawDelivered = false
        while Date() < deadline {
            if let label = conversation.messageStatusLabels().first, label.label.contains("Delivered") {
                sawDelivered = true
                break
            }
            RunLoop.current.run(until: Date().addingTimeInterval(0.1))
        }
        XCTAssertTrue(sawDelivered, "expected the message status to reach Delivered")
    }

    func testReceiveFlowShowsIncomingMessageWithoutUserSending() throws {
        // Attached to B (this sample's own `chat.attachTo` extension -- see
        // Shared/ChatAppOnlyLaunchArgs.swift): CONTRACT.md §3.5's
        // duplicateIncoming script has A send "dup-test" automatically once
        // B connects to it, and B must display exactly one incoming
        // message despite the underlying transport-layer duplicate
        // delivery (CONTRACT.md §2's dedup-by-messageId guarantee).
        let app = ChatUITestSupport.launch(scenario: "duplicateIncoming", seed: 5, attachToB: true)
        defer { app.terminate() }

        let peerBrowser = PeerBrowserPageObject(app: app)
        peerBrowser.connectToFirstPeer()

        let conversation = ConversationPageObject(app: app)
        let rows = conversation.waitForMessageRowCount(atLeast: 1, timeout: ChatUITestSupport.defaultTimeout)
        XCTAssertEqual(rows.count, 1, "expected exactly one incoming message row, not a duplicate")
    }
}
