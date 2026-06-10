import SwiftUI

@main
struct CyrinxHILiOSApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate

    var body: some Scene {
        WindowGroup {
            if HILAutomation.automationActive {
                AutomationStatusView()
            } else {
                HILContentView()
            }
        }
    }
}

final class AppDelegate: NSObject, UIApplicationDelegate {
    func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil
    ) -> Bool {
        // If launched with a CYRINX_CMD env var (devicectl --environment-variables),
        // run the HIL automation primitive instead of the interactive UI.
        HILAutomation.automationActive = HILAutomation.runIfRequested()
        return true
    }
}

/// Minimal foreground view shown during automation. Keeping a visible view in
/// the foreground is required: iOS silences/throttles mic capture and audio
/// rendering when the app is backgrounded (the analog of Android's
/// top-visibility mic-silencing gotcha).
struct AutomationStatusView: View {
    @State private var lines: [String] = []
    private let timer = Timer.publish(every: 0.5, on: .main, in: .common).autoconnect()

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Cyrinx HIL — automation running")
                .font(.headline)
            ScrollView {
                ForEach(Array(lines.enumerated()), id: \.offset) { _, line in
                    Text(line)
                        .font(.system(size: 11, design: .monospaced))
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
        }
        .padding()
        .onReceive(timer) { _ in lines = HILAutomation.logLines.suffix(60).map { $0 } }
    }
}
