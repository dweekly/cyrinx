import CyrinxChatKit
import SwiftUI

/// The persistent connection banner (design brief: "connection banner").
/// Status meaning is never color-alone: an SF Symbol plus the plain-
/// language text (`ChatModel.banner`, already mapped by the pinned
/// reason table) both carry the meaning; the yellow background is
/// reinforcement, not the only signal.
struct ConnectionBannerView: View {
    let text: String

    var body: some View {
        Label(text, systemImage: "exclamationmark.triangle.fill")
            .font(.callout)
            .padding(8)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.yellow.opacity(0.25))
            .accessibilityIdentifier(ChatAccessibilityID.connectionBanner)
            .accessibilityElement(children: .combine)
    }
}

#Preview {
    ConnectionBannerView(text: "Peer stopped responding. Move the devices closer and reconnect.")
}
