import CyrinxChatApp
import CyrinxChatKit
import SwiftUI

/// A "messages missing" caption row, rendered inline in the conversation's
/// message list wherever a `ChatEvent.messageGap` was consumed
/// (Apps/Chat/CONTRACT.md's `messageGap(fromSequence, toSequence)`, §1.7,
/// and this C3-29 sequence amendment's `ChatModel.messageGapNotices`).
///
/// Not an error and not a `banner` -- ORCHESTRATOR PINS #3 for this
/// amendment: "banner priority rules unchanged; the caption is a
/// message-list row with the new accessibility tag." Styled as a plain,
/// centered informational caption, distinct from both a `MessageRowView`
/// bubble (it carries no direction or delivery status -- it isn't a
/// message) and `ConnectionBannerView` (it never claims the persistent
/// top-of-screen banner slot).
struct MessageGapNoticeRowView: View {
    let notice: ChatMessageGapNotice

    var body: some View {
        Label(notice.text, systemImage: "exclamationmark.bubble")
            .font(.caption)
            .foregroundStyle(.secondary)
            .frame(maxWidth: .infinity, alignment: .center)
            .padding(.vertical, 4)
            // Status meaning is never color/icon-alone anywhere else in
            // this sample (see `MessageRowView`'s identical note) -- here
            // there is no status to encode, just plain-language text, so
            // the label's own text already carries the full meaning.
            .accessibilityIdentifier(ChatAccessibilityID.messageGapNotice)
            .accessibilityLabel(notice.text)
    }
}

#Preview {
    VStack(spacing: 8) {
        MessageGapNoticeRowView(
            notice: ChatMessageGapNotice(fromSequence: 5, toSequence: 5, precedingMessageCount: 1)
        )
        MessageGapNoticeRowView(
            notice: ChatMessageGapNotice(fromSequence: 12, toSequence: 19, precedingMessageCount: 3)
        )
    }
    .padding()
}
