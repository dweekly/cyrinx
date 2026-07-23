import CyrinxChatKit
import XCTest

/// Page object for `PeerBrowserView` (Shared/PeerBrowserView.swift):
/// discovery + connect flows. Every lookup goes through
/// `ChatAccessibilityID` (CONTRACT.md §5's pinned registry), never a
/// hardcoded label string that could silently drift from the view.
/// `@MainActor`: recent XCTest SDKs isolate `XCUIElement`/`XCUIApplication`
/// querying members to the main actor.
@MainActor
struct PeerBrowserPageObject {
    let app: XCUIApplication

    var peerList: XCUIElement {
        app.collectionViews[ChatAccessibilityID.peerList].exists
            ? app.collectionViews[ChatAccessibilityID.peerList]
            : app.tables[ChatAccessibilityID.peerList]
    }

    /// The first discovered peer row, waiting up to `timeout` for peer
    /// discovery (CONTRACT.md §3: `peerFound` fires 50 virtual ms after
    /// both simulated clients start, paced in real time by
    /// `ChatAppSession`).
    func firstPeerRow(timeout: TimeInterval = ChatUITestSupport.defaultTimeout) -> XCUIElement {
        app.descendants(matching: .any)[ChatAccessibilityID.peerRow].assertExists(
            timeout: timeout, "expected at least one discovered peer row"
        )
    }

    func connectButton(in row: XCUIElement) -> XCUIElement {
        row.buttons[ChatAccessibilityID.connectButton]
    }

    /// Waits for the first peer to appear, then taps its Connect button.
    func connectToFirstPeer() {
        let row = firstPeerRow()
        connectButton(in: row).assertExists("expected a Connect button on the discovered peer row").click()
    }
}
