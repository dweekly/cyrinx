import CyrinxChatKit
import SwiftUI

/// The persistent "Unauthenticated acoustic link" notice every build of
/// this sample must show (Apps/Chat/README.md's security notice; C3-29
/// design brief). Rendered as a footer on every screen, not just a
/// one-time alert, so it stays visible for the lifetime of a conversation.
struct UnauthenticatedNoticeView: View {
    var body: some View {
        Label("Unauthenticated acoustic link", systemImage: "lock.slash")
            .font(.caption)
            .foregroundStyle(.secondary)
            .padding(6)
            .frame(maxWidth: .infinity)
            .accessibilityIdentifier(ChatAccessibilityID.unauthenticatedNotice)
            .accessibilityLabel(
                "Unauthenticated acoustic link. No identity verification or encryption. "
                    + "Never send secrets or passwords."
            )
    }
}

#Preview {
    UnauthenticatedNoticeView()
}
