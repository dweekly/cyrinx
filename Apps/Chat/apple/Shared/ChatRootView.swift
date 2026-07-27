import CyrinxChatApp
import CyrinxChatKit
import SwiftUI

/// The chat sample's single root screen. Shows `PeerBrowserView` while
/// there is no active/attempted connection, `ConversationView` once the
/// user has asked to connect -- both wrapped in the persistent connection
/// banner (top) and unauthenticated-link notice (bottom), per the design
/// brief and Apps/Chat/README.md's security notice.
///
/// macOS keyboard: Return sends (handled inside `ComposerView`'s
/// `.onSubmit`); Cmd-N reconnects (disconnects the current session, if
/// any, returning to the peer browser so the user can pick again) --
/// bound here via a hidden button so it works regardless of which subview
/// currently has focus.
struct ChatRootView: View {
    @State private var session: ChatAppSession
    @State private var showDiagnostics = false
    @State private var composerText = ""

    init(session: ChatAppSession = ChatAppSession()) {
        _session = State(initialValue: session)
    }

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                if let banner = session.model.banner {
                    ConnectionBannerView(text: banner)
                }
                Group {
                    if isConversationActive {
                        ConversationView(model: session.model, composerText: $composerText)
                    } else {
                        PeerBrowserView(model: session.model)
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                UnauthenticatedNoticeView()
            }
            .navigationTitle("Cyrinx Chat")
            .toolbar {
                ToolbarItem {
                    Button {
                        showDiagnostics = true
                    } label: {
                        Label("Diagnostics", systemImage: "wrench.and.screwdriver")
                    }
                    .accessibilityIdentifier(ChatAccessibilityID.diagnosticsButton)
                }
            }
            .sheet(isPresented: $showDiagnostics) {
                DiagnosticsSheetView()
            }
            .background(reconnectShortcut)
        }
        .task { session.start() }
    }

    private var isConversationActive: Bool {
        switch session.model.connection {
        case .disconnected: return false
        case .connecting, .connected, .degraded: return true
        }
    }

    /// An invisible button carrying the Cmd-N keyboard shortcut so it
    /// fires regardless of which control currently has focus -- SwiftUI
    /// only routes a `.keyboardShortcut` to a button that is part of the
    /// active view hierarchy, so this cannot simply live as a dangling
    /// modifier with no view.
    private var reconnectShortcut: some View {
        Button("Reconnect") {
            Task { await session.model.disconnect() }
        }
        .keyboardShortcut("n", modifiers: .command)
        .accessibilityHidden(true)
        .opacity(0)
        .frame(width: 0, height: 0)
    }
}

#Preview {
    ChatRootView(session: ChatAppSession(options: .init(scenario: .happyPair, seed: 1, simulated: true)))
}
