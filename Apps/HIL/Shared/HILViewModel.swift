import Cyrinx
import Foundation
import SwiftUI

#if os(iOS)
    import AVFoundation
#elseif os(macOS)
    import CoreAudio
#endif

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

enum HILSampleRateChoice: Int, CaseIterable, Identifiable {
    case rate48k = 48_000
    case rate96k = 96_000

    var id: Int { rawValue }

    var label: String {
        switch self {
        case .rate48k:
            return "48 kHz"
        case .rate96k:
            return "96 kHz"
        }
    }

    var hz: UInt32 {
        UInt32(rawValue)
    }
}

extension CyrinxSession: @retroactive @unchecked Sendable {}

@MainActor
final class HILViewModel: ObservableObject {
    @Published var roleChoice: HILRoleChoice = .master
    @Published var sampleRateChoice: HILSampleRateChoice = .rate48k
    @Published var statusText: String = "Idle"
    @Published var diagnosticsText: String = "Audio backend is not started"
    @Published var logs: [String] = []

    private var session: CyrinxSession?
    private var eventTask: Task<Void, Never>?
    private let sessionOperationQueue = DispatchQueue(label: "com.dweekly.cyrinx.hil.sessionOps")
    private var lastObservedCoreRxFrames: UInt32 = 0

    func start() {
        stop()

        let compatibilityBandEndHz = effectiveBandEndHz(for: sampleRateChoice.hz)
        let config = Config(
            role: roleChoice.role,
            transportBackend: .appleAudioScaffold,
            sampleRateHz: sampleRateChoice.hz,
            bandStartHz: 18_500,
            bandEndHz: compatibilityBandEndHz
        )

        do {
            let opened = try CyrinxSession(config: config)
            try opened.start()
            session = opened
            lastObservedCoreRxFrames = 0
            statusText = "Running (\(roleChoice.label), \(sampleRateChoice.label))"
            appendLog("session started with preferred sampleRate=\(sampleRateChoice.hz)Hz")
            appendLog("using compatibility band=18500...\(compatibilityBandEndHz)Hz")
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
        lastObservedCoreRxFrames = 0
        statusText = "Stopped"
        diagnosticsText = "Audio backend is not started"
    }

    func sendProbe() {
        guard let session else {
            appendLog("send skipped: session is not started")
            return
        }

        let payload = Data("probe:\(Date().timeIntervalSince1970)".utf8)
        sessionOperationQueue.async { [weak self] in
            do {
                try session.send(payload, streamID: 7, qos: .bestEffort, priority: .high, flags: [.fin])
                self?.postOperationResult("sent \(payload.count) bytes on stream 7 (best-effort)")
            } catch {
                self?.postOperationResult("send (best-effort) failed: \(error)")
            }
        }
    }

    func sendReliableProbe() {
        guard let session else {
            appendLog("send skipped: session is not started")
            return
        }

        let payload = Data("probe-reliable:\(Date().timeIntervalSince1970)".utf8)
        sessionOperationQueue.async { [weak self] in
            do {
                try session.send(payload, streamID: 9, qos: .reliable, priority: .high, flags: [.fin])
                self?.postOperationResult("sent \(payload.count) bytes on stream 9 (reliable)")
            } catch {
                self?.postOperationResult("send (reliable) failed: \(error)")
            }
        }
    }

    func receiveOnce() {
        guard let session else {
            appendLog("receive skipped: session is not started")
            return
        }

        sessionOperationQueue.async { [weak self] in
            do {
                let message = try session.receive(timeoutMS: 50)
                if let message {
                    let preview = String(decoding: message.data.prefix(32), as: UTF8.self)
                    self?.postOperationResult(
                        "rx stream=\(message.streamID) bytes=\(message.data.count) preview=\(preview)")
                } else {
                    self?.postOperationResult("rx timeout")
                }
            } catch {
                self?.postOperationResult("receive failed: \(error)")
            }
        }
    }

    func injectNominalChannelReport() {
        guard let session else {
            appendLog("inject skipped: session is not started")
            return
        }

        sessionOperationQueue.async { [weak self] in
            do {
                try session.injectChannelReport(
                    snrDB: 26,
                    evmPct: 4.5,
                    cfoHz: 0,
                    per2s: 0.01,
                    crcFail: false
                )
                self?.postOperationResult("injected nominal channel report")
            } catch {
                self?.postOperationResult("inject failed: \(error)")
            }
        }
    }

    func refreshDiagnostics() {
        guard let diagnostics = session?.audioDiagnostics else {
            diagnosticsText = "Audio backend is not enabled"
            return
        }

        let metrics = session?.metrics
        let gear = metrics.map { "\($0.gear)" } ?? "n/a"
        let coreRx = metrics.map { "\($0.rxFrames)" } ?? "n/a"
        let coreTx = metrics.map { "\($0.txFrames)" } ?? "n/a"
        let corePer = metrics.map { String(format: "%.3f", $0.per2s) } ?? "n/a"
        if let coreRxFrames = metrics?.rxFrames {
            if lastObservedCoreRxFrames == 0, coreRxFrames > 0 {
                appendLog("link evidence: decoded inbound frame(s) detected")
            }
            lastObservedCoreRxFrames = coreRxFrames
        }

        diagnosticsText =
            "backend=\(diagnostics.backend) state=\(diagnostics.state.rawValue) configuredHz=\(diagnostics.configuredSampleRateHz) inHz=\(diagnostics.observedInputSampleRateHz) outHz=\(diagnostics.observedOutputSampleRateHz) txFrames=\(diagnostics.txFrameCount) txBytes=\(diagnostics.txByteCount) rxCallbacks=\(diagnostics.rxCallbackCount) coreGear=\(gear) coreTx=\(coreTx) coreRx=\(coreRx) per2s=\(corePer)"
    }

    func probeLocalAudioRoute() {
        #if os(iOS)
            let session = AVAudioSession.sharedInstance()
            let routeSummary = session.currentRoute.outputs.map { $0.portName }.joined(separator: ",")
            appendLog(
                "ios route preferredHz=\(Int(session.preferredSampleRate)) actualHz=\(Int(session.sampleRate)) outputs=\(routeSummary)"
            )
        #elseif os(macOS)
            if let output = defaultDeviceSummary(selector: kAudioHardwarePropertyDefaultOutputDevice) {
                appendLog(output)
            } else {
                appendLog("mac output probe unavailable")
            }
            if let input = defaultDeviceSummary(selector: kAudioHardwarePropertyDefaultInputDevice) {
                appendLog(input)
            } else {
                appendLog("mac input probe unavailable")
            }
        #else
            appendLog("audio route probe unavailable on this platform")
        #endif
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
                self?.recordEvent(event)
            }
        }
    }

    private func recordEvent(_ event: Event) {
        appendLog("event=\(event)")
        refreshDiagnostics()
    }

    nonisolated private func postOperationResult(_ line: String) {
        Task { @MainActor [weak self] in
            self?.appendLog(line)
            self?.refreshDiagnostics()
        }
    }

    private func effectiveBandEndHz(for requestedSampleRateHz: UInt32) -> UInt32 {
        let nyquistLimited = requestedSampleRateHz > 4_000 ? (requestedSampleRateHz / 2) - 1_000 : 20_000
        return min(21_000, max(19_500, nyquistLimited))
    }

    #if os(macOS)
        private func defaultDeviceSummary(selector: AudioObjectPropertySelector) -> String? {
            var address = AudioObjectPropertyAddress(
                mSelector: selector,
                mScope: kAudioObjectPropertyScopeGlobal,
                mElement: kAudioObjectPropertyElementMain
            )
            var deviceID = AudioDeviceID(0)
            var size = UInt32(MemoryLayout<AudioDeviceID>.size)
            let rc = AudioObjectGetPropertyData(
                AudioObjectID(kAudioObjectSystemObject),
                &address,
                0,
                nil,
                &size,
                &deviceID
            )
            if rc != noErr {
                return nil
            }

            let name = deviceName(deviceID) ?? "<unknown>"
            let current = currentSampleRate(deviceID) ?? 0
            let supports96k = availableRates(deviceID).contains { range in
                range.mMinimum <= 96_000 && 96_000 <= range.mMaximum
            }
            let role = selector == kAudioHardwarePropertyDefaultOutputDevice ? "mac output" : "mac input"
            return "\(role) \(name) currentHz=\(Int(current)) supports96k=\(supports96k)"
        }

        private func deviceName(_ deviceID: AudioDeviceID) -> String? {
            var address = AudioObjectPropertyAddress(
                mSelector: kAudioObjectPropertyName,
                mScope: kAudioObjectPropertyScopeGlobal,
                mElement: kAudioObjectPropertyElementMain
            )
            var unmanaged: Unmanaged<CFString>?
            var size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
            let rc = AudioObjectGetPropertyData(deviceID, &address, 0, nil, &size, &unmanaged)
            if rc != noErr {
                return nil
            }
            return unmanaged?.takeRetainedValue() as String?
        }

        private func currentSampleRate(_ deviceID: AudioDeviceID) -> Double? {
            var address = AudioObjectPropertyAddress(
                mSelector: kAudioDevicePropertyNominalSampleRate,
                mScope: kAudioObjectPropertyScopeGlobal,
                mElement: kAudioObjectPropertyElementMain
            )
            var value: Double = 0
            var size = UInt32(MemoryLayout<Double>.size)
            let rc = AudioObjectGetPropertyData(deviceID, &address, 0, nil, &size, &value)
            return rc == noErr ? value : nil
        }

        private func availableRates(_ deviceID: AudioDeviceID) -> [AudioValueRange] {
            var address = AudioObjectPropertyAddress(
                mSelector: kAudioDevicePropertyAvailableNominalSampleRates,
                mScope: kAudioObjectPropertyScopeGlobal,
                mElement: kAudioObjectPropertyElementMain
            )
            var size: UInt32 = 0
            let sizeRC = AudioObjectGetPropertyDataSize(deviceID, &address, 0, nil, &size)
            if sizeRC != noErr || size == 0 {
                return []
            }

            let count = Int(size) / MemoryLayout<AudioValueRange>.stride
            var values = Array(repeating: AudioValueRange(mMinimum: 0, mMaximum: 0), count: count)
            let dataRC = AudioObjectGetPropertyData(deviceID, &address, 0, nil, &size, &values)
            return dataRC == noErr ? values : []
        }
    #endif
}
