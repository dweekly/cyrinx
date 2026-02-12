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

            Picker("Sample Rate", selection: $viewModel.sampleRateChoice) {
                ForEach(HILSampleRateChoice.allCases) { choice in
                    Text(choice.label).tag(choice)
                }
            }
            .pickerStyle(.segmented)

            LazyVGrid(columns: [GridItem(.adaptive(minimum: 150), spacing: 8)], spacing: 8) {
                Button("Start") {
                    viewModel.start()
                }
                Button("Stop") {
                    viewModel.stop()
                }
                Button("Send Probe (BE)") {
                    viewModel.sendProbe()
                }
                Button("Send Probe (Reliable)") {
                    viewModel.sendReliableProbe()
                }
                Button("Receive Once") {
                    viewModel.receiveOnce()
                }
                Button("Inject Nominal Channel") {
                    viewModel.injectNominalChannelReport()
                }
                Button("Refresh Diagnostics") {
                    viewModel.refreshDiagnostics()
                }
                Button("Probe Local Audio") {
                    viewModel.probeLocalAudioRoute()
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
        #if os(macOS)
            .frame(minWidth: 560, minHeight: 420)
        #else
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        #endif
    }
}
