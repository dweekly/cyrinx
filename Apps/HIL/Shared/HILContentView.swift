import SwiftUI

struct HILContentView: View {
    @StateObject private var viewModel = HILViewModel()

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Picker("Role", selection: $viewModel.roleChoice) {
                ForEach(HILRoleChoice.allCases) { choice in
                    Text(choice.label).tag(choice)
                }
            }
            .pickerStyle(.segmented)

            HStack {
                Button("Start") {
                    viewModel.start()
                }
                Button("Stop") {
                    viewModel.stop()
                }
                Button("Send Probe") {
                    viewModel.sendProbe()
                }
                Button("Receive Once") {
                    viewModel.receiveOnce()
                }
            }

            HStack {
                Button("Inject Nominal Channel") {
                    viewModel.injectNominalChannelReport()
                }
                Button("Refresh Diagnostics") {
                    viewModel.refreshDiagnostics()
                }
            }

            Text("Status: \(viewModel.statusText)")
            Text(viewModel.diagnosticsText)
                .font(.footnote)
                .foregroundStyle(.secondary)

            Divider()

            ScrollView {
                LazyVStack(alignment: .leading, spacing: 6) {
                    ForEach(Array(viewModel.logs.enumerated()), id: \.offset) { index, line in
                        Text("\(index + 1). \(line)")
                            .font(.system(size: 12, weight: .regular, design: .monospaced))
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
            }
        }
        .padding(16)
        .frame(minWidth: 560, minHeight: 420)
    }
}
