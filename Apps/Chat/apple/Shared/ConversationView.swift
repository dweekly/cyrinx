import CyrinxChatApp
import CyrinxChatKit
import SwiftUI

/// The conversation screen (design brief: "conversation screen"): message
/// list, link-budget badge, eventSeq-gap caption, transient composer error,
/// and the composer.
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
                        ForEach(model.messages) { message in
                            MessageRowView(message: message)
                                .id(message.id)
                        }
                    }
                    .padding()
                }
                .accessibilityIdentifier(ChatAccessibilityID.messageList)
                .onChange(of: model.messages.count) {
                    guard let last = model.messages.last else { return }
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
