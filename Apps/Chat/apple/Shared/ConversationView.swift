import CyrinxChatApp
import CyrinxChatKit
import SwiftUI

/// The conversation screen (design brief: "conversation screen"): message
/// list, link-budget badge, eventSeq-gap caption, transient composer error,
/// and the composer.
///
/// The message list itself renders `model.conversationRows`, not
/// `model.messages` directly -- the C3-29 sequence amendment interleaves
/// `messageGap` captions (`MessageGapNoticeRowView`) among the message
/// bubbles at the point in the timeline where the missing messages would
/// have appeared. This is unrelated to the `eventSeqGapDetected` caption
/// below: that one is this section's OWN `eventSeq` bounded-buffer-drop
/// contract (CONTRACT.md §1.7), not a `messageGap` `ChatEvent`
/// (CONTRACT.md §1.7/§2's "Gap surfacing (pinned)") -- see `ChatEvent
/// .Kind`'s own doc comment for the distinction between the two.
struct ConversationView: View {
    let model: ChatModel
    @Binding var composerText: String

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                LinkBudgetBadgeView(budget: model.budget)
                Spacer()
            }
            .padding(.horizontal)
            .padding(.top, 6)

            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 8) {
                        ForEach(model.conversationRows) { row in
                            switch row {
                            case .message(let message):
                                MessageRowView(message: message)
                                    .id(row.id)
                            case .messageGapNotice(let notice):
                                MessageGapNoticeRowView(notice: notice)
                                    .id(row.id)
                            }
                        }
                    }
                    .padding()
                }
                .accessibilityIdentifier(ChatAccessibilityID.messageList)
                .onChange(of: model.conversationRows.count) {
                    guard let last = model.conversationRows.last else { return }
                    withAnimation {
                        proxy.scrollTo(last.id, anchor: .bottom)
                    }
                }
            }

            if model.eventSeqGapDetected {
                Text("Some events were dropped.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal)
            }

            if let composerError = model.composerError {
                Text(composerError)
                    .font(.caption)
                    .foregroundStyle(.red)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal)
                    .accessibilityIdentifier(ChatAccessibilityID.errorBanner)
            }

            Divider()
            ComposerView(model: model, text: $composerText)
        }
    }
}

#Preview {
    ConversationView(model: ChatModel(transport: PreviewTransport()), composerText: .constant(""))
}
