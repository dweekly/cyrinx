import SwiftUI

/// Diagnostics sheet placeholder. Apps/Chat/README.md's "Export a redacted
/// support bundle from an advanced diagnostics sheet" is real, wired-up
/// functionality in a later stage (C3-26/C3-31 per the design brief) --
/// this sheet reserves the accessibility ID and the screen slot with
/// simulated placeholder text, per the C3-29 task brief's "diagnostics
/// sheet placeholder."
struct DiagnosticsSheetView: View {
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 16) {
                Text("Diagnostics")
                    .font(.title2.bold())
                Text(
                    "Support-bundle export is not available in this build. A future release will "
                        + "let you export a redacted diagnostic bundle -- connection history, link-budget "
                        + "samples, and event counts, with no message contents or peer identifiers -- for "
                        + "troubleshooting."
                )
                .foregroundStyle(.secondary)
                Spacer()
            }
            .padding()
            .navigationTitle("Diagnostics")
            #if os(iOS)
                .navigationBarTitleDisplayMode(.inline)
            #endif
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Close") { dismiss() }
                }
            }
        }
    }
}

#Preview {
    DiagnosticsSheetView()
}
