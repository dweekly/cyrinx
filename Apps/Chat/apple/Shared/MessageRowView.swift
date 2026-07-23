import CyrinxChatKit
import SwiftUI

/// One message bubble. Per-message status is shown as an SF Symbol plus a
/// plain-language text label (design brief: "status meaning never
/// color-alone") -- color is reinforcement only, never the sole carrier of
/// meaning, so this reads correctly under Increase Contrast, color-blind
/// palettes, and VoiceOver alike.
///
/// The honest-delivery caveat (CONTRACT.md §1.5: `.delivered` means
/// transport acknowledgment only, not authenticated identity) is not
/// re-stated on every bubble -- that would be noisy -- but IS covered by
/// the always-visible `UnauthenticatedNoticeView` footer.
struct MessageRowView: View {
    let message: ChatMessage

    var body: some View {
        HStack {
            if message.direction == .outgoing { Spacer(minLength: 32) }
            VStack(alignment: message.direction == .outgoing ? .trailing : .leading, spacing: 2) {
                Text(message.body)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 6)
                    .background(bubbleBackground, in: RoundedRectangle(cornerRadius: 12))
                    .accessibilityLabel(bubbleAccessibilityLabel)
                if message.direction == .outgoing {
                    statusLabel
                }
            }
            if message.direction == .incoming { Spacer(minLength: 32) }
        }
        // Deliberately NOT `.accessibilityElement(children: .combine)` at
        // this row level: combining would fold `statusLabel`'s own
        // `ChatAccessibilityID.messageStatus` identifier into the merged
        // parent element, making it unreachable as its own VoiceOver stop
        // or XCUITest query -- see `PeerBrowserView`'s identical note for
        // the Connect button. The bubble text and the status label remain
        // two separate, independently identifiable accessibility elements
        // instead.
        .accessibilityIdentifier(ChatAccessibilityID.messageRow)
    }

    private var bubbleBackground: Color {
        message.direction == .outgoing ? Color.accentColor.opacity(0.18) : Color.secondary.opacity(0.14)
    }

    @ViewBuilder
    private var statusLabel: some View {
        Label(statusText, systemImage: statusSymbol)
            .font(.caption2)
            .foregroundStyle(statusColor)
            .labelStyle(.titleAndIcon)
            .accessibilityIdentifier(ChatAccessibilityID.messageStatus)
            .accessibilityLabel(statusText)
    }

    private var bubbleAccessibilityLabel: String {
        let directionWord = message.direction == .outgoing ? "You" : "Peer"
        return "\(directionWord): \(message.body)."
    }

    private var statusText: String {
        switch message.status {
        case .queued: return "Queued"
        case .transmitting: return "Sending"
        case .delivered: return "Delivered"
        case .failed(let reason): return "Failed: \(reason)"
        }
    }

    private var statusSymbol: String {
        switch message.status {
        case .queued: return "clock"
        case .transmitting: return "arrow.up.circle"
        case .delivered: return "checkmark.circle.fill"
        case .failed: return "exclamationmark.triangle.fill"
        }
    }

    private var statusColor: Color {
        switch message.status {
        case .queued: return .secondary
        case .transmitting: return .blue
        case .delivered: return .green
        case .failed: return .red
        }
    }
}

#Preview {
    VStack(alignment: .leading, spacing: 8) {
        MessageRowView(
            message: ChatMessage(
                id: Data(repeating: 1, count: 16), direction: .incoming, body: "hello there",
                senderPeerIdHex: "aabbccdd", sentAtWallClockMs: 0, status: .delivered
            )
        )
        MessageRowView(
            message: ChatMessage(
                id: Data(repeating: 2, count: 16), direction: .outgoing, body: "hi!",
                senderPeerIdHex: "", sentAtWallClockMs: 0, status: .transmitting
            )
        )
        MessageRowView(
            message: ChatMessage(
                id: Data(repeating: 3, count: 16), direction: .outgoing, body: "did this fail?",
                senderPeerIdHex: "", sentAtWallClockMs: 0, status: .failed(reason: "noAcknowledgment")
            )
        )
    }
    .padding()
}
