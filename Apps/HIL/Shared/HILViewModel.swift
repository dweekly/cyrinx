import Cyrinx
import Foundation
import SwiftUI

enum HILRoleChoice: String, CaseIterable, Identifiable {
    case master
    case slave

    var id: String { rawValue }

    var label: String {
        switch self {
        case .master:
            return "Master"
        case .slave:
            return "Slave"
        }
    }

    var role: Role {
        switch self {
        case .master:
            return .master
        case .slave:
            return .slave
        }
    }
}

@MainActor
final class HILViewModel: ObservableObject {
    @Published var roleChoice: HILRoleChoice = .master
    @Published var statusText: String = "Idle"
    @Published var diagnosticsText: String = "Audio backend is not started"
    @Published var logs: [String] = []

    private var session: CyrinxSession?
    private var eventTask: Task<Void, Never>?

    func start() {
        stop()

        let config = Config(
            role: roleChoice.role,
            transportBackend: .appleAudioScaffold,
            sampleRateHz: 48_000
        )

        do {
            let opened = try CyrinxSession(config: config)
            try opened.start()
            session = opened
            statusText = "Running (\(roleChoice.label))"
            appendLog("session started")
            refreshDiagnostics()
            startEventLoop(for: opened)
        } catch {
            statusText = "Failed"
            appendLog("start failed: \(error)")
        }
    }

    func stop() {
        eventTask?.cancel()
        eventTask = nil
        session = nil
        statusText = "Stopped"
        diagnosticsText = "Audio backend is not started"
    }

    func sendProbe() {
        guard let session else {
            appendLog("send skipped: session is not started")
            return
        }

        let payload = Data("probe:\(Date().timeIntervalSince1970)".utf8)
        do {
            try session.send(payload, streamID: 7, qos: .reliable, priority: .high, flags: [.fin])
            appendLog("sent \(payload.count) bytes on stream 7")
        } catch {
            appendLog("send failed: \(error)")
        }
        refreshDiagnostics()
    }

    func receiveOnce() {
        guard let session else {
            appendLog("receive skipped: session is not started")
            return
        }

        do {
            let message = try session.receive(timeoutMS: 50)
            if let message {
                let preview = String(decoding: message.data.prefix(32), as: UTF8.self)
                appendLog("rx stream=\(message.streamID) bytes=\(message.data.count) preview=\(preview)")
            } else {
                appendLog("rx timeout")
            }
        } catch {
            appendLog("receive failed: \(error)")
        }
        refreshDiagnostics()
    }

    func injectNominalChannelReport() {
        guard let session else {
            appendLog("inject skipped: session is not started")
            return
        }

        do {
            try session.injectChannelReport(snrDB: 26, evmPct: 4.5, cfoHz: 0, per2s: 0.01, crcFail: false)
            appendLog("injected nominal channel report")
        } catch {
            appendLog("inject failed: \(error)")
        }
    }

    func refreshDiagnostics() {
        guard let diagnostics = session?.audioDiagnostics else {
            diagnosticsText = "Audio backend is not enabled"
            return
        }

        diagnosticsText = "backend=\(diagnostics.backend) state=\(diagnostics.state.rawValue) txFrames=\(diagnostics.txFrameCount) txBytes=\(diagnostics.txByteCount) rxCallbacks=\(diagnostics.rxCallbackCount)"
    }

    private func appendLog(_ line: String) {
        logs.append(line)
        if logs.count > 200 {
            logs.removeFirst(logs.count - 200)
        }
    }

    private func startEventLoop(for session: CyrinxSession) {
        eventTask = Task { [weak self] in
            for await event in session.events {
                guard !Task.isCancelled else {
                    return
                }
                await self?.recordEvent(event)
            }
        }
    }

    private func recordEvent(_ event: Event) {
        appendLog("event=\(event)")
    }
}
