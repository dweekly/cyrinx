import CyrinxChatApp
import CyrinxChatKit
import SwiftUI

/// The message composer. macOS keyboard: Return sends (via `.onSubmit`,
/// which SwiftUI wires to the physical Return key on macOS and the
/// software keyboard's Send/Return key on iOS identically -- no
/// platform-conditional code needed for this one).
struct ComposerView: View {
    let model: ChatModel
    @Binding var text: String
    @FocusState private var isFocused: Bool

    private var trimmedText: String {
        text.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    var body: some View {
        HStack(alignment: .bottom, spacing: 8) {
            TextField("Message", text: $text, axis: .vertical)
                .textFieldStyle(.roundedBorder)
                .lineLimit(1...5)
                .focused($isFocused)
                .onSubmit(send)
                .submitLabel(.send)
                .accessibilityIdentifier(ChatAccessibilityID.composerField)
                .accessibilityLabel("Message")
            Button(action: send) {
                Image(systemName: "paperplane.fill")
                    .accessibilityHidden(true)
            }
            .disabled(trimmedText.isEmpty)
            .accessibilityIdentifier(ChatAccessibilityID.sendButton)
            .accessibilityLabel("Send")
        }
        .padding(12)
    }

    private func send() {
        let body = trimmedText
        guard !body.isEmpty else { return }
        text = ""
        Task { await model.send(body) }
    }
}

#Preview {
    ComposerView(model: ChatModel(transport: PreviewTransport()), text: .constant(""))
}
