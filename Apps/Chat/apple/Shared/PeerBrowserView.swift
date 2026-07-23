import CyrinxChatApp
import CyrinxChatKit
import SwiftUI

/// The peer browser list (design brief: "peer browser list"). Shown
/// whenever there is no active/attempted connection; `ChatRootView`
/// switches to `ConversationView` once the user taps Connect.
struct PeerBrowserView: View {
    let model: ChatModel

    var body: some View {
        List(model.peers) { peer in
            PeerRow(peer: peer, model: model)
        }
        .listStyle(.plain)
        .accessibilityIdentifier(ChatAccessibilityID.peerList)
        .overlay {
            if model.peers.isEmpty {
                ContentUnavailableView(
                    "Searching for peers…", systemImage: "antenna.radiowaves.left.and.right",
                    description: Text("Nearby devices running Cyrinx Chat will appear here.")
                )
            }
        }
    }
}

private struct PeerRow: View {
    let peer: ChatPeer
    let model: ChatModel

    var body: some View {
        HStack {
            // `.accessibilityElement(children: .combine)` is scoped to just
            // the name/timestamp text here, NOT the whole row -- combining
            // at the row level would fold the Connect button into the same
            // merged element, making it unreachable as its own VoiceOver
            // stop and (since `.accessibilityIdentifier` on a combined
            // container's children stops being independently queryable)
            // unreachable by XCUITest's `app.buttons[ChatAccessibilityID
            // .connectButton]` lookups.
            VStack(alignment: .leading, spacing: 2) {
                Text(peer.displayName)
                    .font(.headline)
                Text("Discovered at \(peer.discoveredAtMs) ms")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            .accessibilityElement(children: .combine)
            .accessibilityLabel("\(peer.displayName). Not connected.")
            Spacer()
            Button("Connect") {
                Task { await model.connect(toPeerIdHex: peer.id.hexString) }
            }
            .buttonStyle(.borderedProminent)
            .accessibilityIdentifier(ChatAccessibilityID.connectButton)
        }
        .padding(.vertical, 4)
        .accessibilityIdentifier(ChatAccessibilityID.peerRow)
    }
}

#Preview {
    let model = ChatModel(transport: PreviewTransport())
    return PeerBrowserView(model: model)
}
