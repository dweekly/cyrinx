import CCyrinx
import Foundation

private let CYRINX_SWIFT_OK: Int32 = 0
private let CYRINX_SWIFT_ERR_INVALID_ARGUMENT: Int32 = -1
private let CYRINX_SWIFT_ERR_BUFFER_TOO_SMALL: Int32 = -3
private let CYRINX_SWIFT_ERR_TIMEOUT: Int32 = -4
private let CYRINX_SWIFT_ERR_INTERNAL: Int32 = -9

/// Library error type that wraps C core status codes.
public enum CyrinxError: Error, CustomStringConvertible, LocalizedError, Sendable {
    /// Raw C status code returned by the transport core.
    case status(Int32)

    /// Numeric status value returned by the C API.
    public var statusCode: Int32 {
        switch self {
        case .status(let code):
            return code
        }
    }

    /// Stable symbolic name for this status code (for example `CYRINX_ERR_TIMEOUT`).
    public var statusName: String {
        Cyrinx.statusName(for: statusCode)
    }

    /// Human-readable explanation for this status code.
    public var statusDescription: String {
        Cyrinx.statusDescription(for: statusCode)
    }

    public var description: String {
        "cyrinx error \(statusCode) (\(statusName)): \(statusDescription)"
    }

    public var errorDescription: String? {
        description
    }

    public var recoverySuggestion: String? {
        switch statusCode {
        case CYRINX_SWIFT_ERR_TIMEOUT:
            return "Verify both peers are started and use best-effort probing before reliable send."
        case CYRINX_SWIFT_ERR_INVALID_ARGUMENT:
            return "Validate stream IDs, payload size, and configuration values."
        case CYRINX_SWIFT_ERR_BUFFER_TOO_SMALL:
            return "Increase receive buffer capacity and retry."
        default:
            return nil
        }
    }
}

/// Session role for the half-duplex ping-pong transport.
public enum Role {
    /// Initiates discovery and starts the first transmission slot.
    case master
    /// Responds to discovery and alternates receive/transmit slots.
    case slave

    fileprivate var cValue: cyrinx_role_t {
        switch self {
        case .master: return CYRINX_ROLE_MASTER
        case .slave: return CYRINX_ROLE_SLAVE
        }
    }
}

/// Delivery quality of service for a payload.
public enum QoS {
    /// Sends once with no retransmission/ACK requirement.
    case bestEffort
    /// Requires ACK and retransmits up to policy limits.
    case reliable

    fileprivate var cValue: cyrinx_qos_t {
        switch self {
        case .bestEffort: return CYRINX_QOS_BEST_EFFORT
        case .reliable: return CYRINX_QOS_RELIABLE
        }
    }
}

/// Priority hint used by stream-aware frame scheduling.
public enum StreamPriority: UInt8, Sendable {
    case low = 0
    case normal = 1
    case high = 2
    case critical = 3
}

/// Optional stream semantics that annotate logical message boundaries.
public struct StreamFlags: OptionSet, Sendable {
    public let rawValue: UInt8

    /// Creates a custom stream flag bitmask.
    public init(rawValue: UInt8) {
        self.rawValue = rawValue
    }

    /// Marks logical end-of-stream payload.
    public static let fin = StreamFlags(rawValue: UInt8(CYRINX_STREAM_FLAG_FIN))
    /// Requests stream reset semantics.
    public static let reset = StreamFlags(rawValue: UInt8(CYRINX_STREAM_FLAG_RST))
}

/// Payload and metadata returned by `receive(...)`.
public struct ReceivedMessage {
    public let data: Data
    public let streamID: UInt16
    public let priority: StreamPriority
    public let flags: StreamFlags
}

/// Current adaptive PHY gear selected by ARC.
public enum Gear: UInt8 {
    case discovery = 0
    case robust = 1
    case turboQPSK = 2
    case turbo16QAM = 3
    case turbo64QAM = 4

    fileprivate init(cValue: cyrinx_gear_t) {
        self = Gear(rawValue: UInt8(cValue.rawValue)) ?? .discovery
    }
}

/// Link-level state transitions emitted via `CyrinxSession.events`.
public enum Event: UInt8 {
    case idle = 0
    case discovery = 1
    case linked = 2
    case degraded = 3
    case recovering = 4
    case failed = 5

    fileprivate init(cValue: cyrinx_event_t) {
        self = Event(rawValue: UInt8(cValue.rawValue)) ?? .idle
    }
}

/// Session configuration for transport policy and backend behavior.
public struct Config {
    public var role: Role
    public var transportBackend: TransportBackend
    public var sampleRateHz: UInt32
    public var bandStartHz: UInt32
    public var bandEndHz: UInt32
    public var txGainCap: Float
    public var spectralLeakageLimitDbfs: Float
    public var ofdmCPSamplesDefault: UInt16
    public var ofdmCPSamplesMin: UInt16
    public var sensorAssistedARC: Bool
    public var dynamicCP: Bool
    public var sfbcStaticMode: Bool

    /// Creates a PRD-aligned session configuration.
    ///
    /// - Important: These settings only configure policy and transport behavior.
    ///   Real ultrasonic audio IO is added by platform backends.
    public init(
        role: Role = .master,
        transportBackend: TransportBackend = .inMemory,
        sampleRateHz: UInt32 = 48_000,
        bandStartHz: UInt32 = 18_500,
        bandEndHz: UInt32 = 23_500,
        txGainCap: Float = 0.70,
        spectralLeakageLimitDbfs: Float = -45.0,
        ofdmCPSamplesDefault: UInt16 = 96,
        ofdmCPSamplesMin: UInt16 = 10,
        sensorAssistedARC: Bool = true,
        dynamicCP: Bool = true,
        sfbcStaticMode: Bool = true
    ) {
        self.role = role
        self.transportBackend = transportBackend
        self.sampleRateHz = sampleRateHz
        self.bandStartHz = bandStartHz
        self.bandEndHz = bandEndHz
        self.txGainCap = txGainCap
        self.spectralLeakageLimitDbfs = spectralLeakageLimitDbfs
        self.ofdmCPSamplesDefault = ofdmCPSamplesDefault
        self.ofdmCPSamplesMin = ofdmCPSamplesMin
        self.sensorAssistedARC = sensorAssistedARC
        self.dynamicCP = dynamicCP
        self.sfbcStaticMode = sfbcStaticMode
    }

    fileprivate func toC(
        eventCallback: cyrinx_event_callback_t?,
        txCallback: cyrinx_tx_callback_t?,
        userData: UnsafeMutableRawPointer?
    )
        -> cyrinx_config_t
    {
        var c = cyrinx_config_t()
        c.role = role.cValue
        c.sample_rate_hz = sampleRateHz
        c.band_start_hz = bandStartHz
        c.band_end_hz = bandEndHz
        c.tx_gain_cap = txGainCap
        c.spectral_leakage_limit_dbfs = spectralLeakageLimitDbfs
        c.ofdm_cp_samples_default = ofdmCPSamplesDefault
        c.ofdm_cp_samples_min = ofdmCPSamplesMin
        c.enable_sensor_assisted_arc = sensorAssistedARC ? 1 : 0
        c.enable_dynamic_cp = dynamicCP ? 1 : 0
        c.enable_sfbc_static_mode = sfbcStaticMode ? 1 : 0
        c.security_mode = CYRINX_SECURITY_EXTERNAL
        c.tx_callback = txCallback
        c.event_callback = eventCallback
        c.user_data = userData
        return c
    }
}

/// Adaptive Rate Control thresholds and retry policy.
public struct ARCPolicy {
    public var g2ToQPSKSNR: Float
    public var qpskTo16QAMSNR: Float
    public var qpskTo16QAMMaxEVM: Float
    public var qpskToG2SNR: Float
    public var minDwellMS: UInt32
    public var maxRetransmissions: UInt8

    /// ARC threshold overrides for simulation and tuning.
    public init(
        g2ToQPSKSNR: Float = 14,
        qpskTo16QAMSNR: Float = 25,
        qpskTo16QAMMaxEVM: Float = 5,
        qpskToG2SNR: Float = 15,
        minDwellMS: UInt32 = 1000,
        maxRetransmissions: UInt8 = 4
    ) {
        self.g2ToQPSKSNR = g2ToQPSKSNR
        self.qpskTo16QAMSNR = qpskTo16QAMSNR
        self.qpskTo16QAMMaxEVM = qpskTo16QAMMaxEVM
        self.qpskToG2SNR = qpskToG2SNR
        self.minDwellMS = minDwellMS
        self.maxRetransmissions = maxRetransmissions
    }

    fileprivate func toC() -> cyrinx_arc_policy_t {
        var c = cyrinx_arc_policy_t()
        cyrinx_default_arc_policy(&c)
        c.up_g2_to_qpsk_snr_db = g2ToQPSKSNR
        c.up_qpsk_to_16qam_snr_db = qpskTo16QAMSNR
        c.up_qpsk_to_16qam_max_evm_pct = qpskTo16QAMMaxEVM
        c.down_qpsk_to_g2_snr_db = qpskToG2SNR
        c.min_dwell_ms = minDwellMS
        c.max_retransmissions = maxRetransmissions
        return c
    }
}

/// Runtime metrics sampled from the transport core.
public struct Metrics {
    public let gear: Gear
    public let snrDB: Float
    public let evmPct: Float
    public let cfoHz: Float
    public let per2s: Float
    public let goodputBps: Float
    public let txRetries: UInt32
    public let txFrames: UInt32
    public let rxFrames: UInt32
    public let crcFailures: UInt32
    public let linkResets: UInt32

    fileprivate init(c: cyrinx_metrics_t) {
        gear = Gear(cValue: c.current_gear)
        snrDB = c.snr_db
        evmPct = c.evm_pct
        cfoHz = c.cfo_hz
        per2s = c.per_2s
        goodputBps = c.goodput_bps
        txRetries = c.tx_retries
        txFrames = c.tx_frames
        rxFrames = c.rx_frames
        crcFailures = c.crc_failures
        linkResets = c.link_resets
    }
}

private final class SessionCallbackRelay {
    var continuation: AsyncStream<Event>.Continuation?
    weak var audioBackend: (any SessionFrameIOBackend)?
}

@_cdecl("cyrinx_swift_event_callback")
private func cyrinx_swift_event_callback(_ event: cyrinx_event_t, _ userData: UnsafeMutableRawPointer?) {
    guard let userData else { return }
    let relay = Unmanaged<SessionCallbackRelay>.fromOpaque(userData).takeUnretainedValue()
    relay.continuation?.yield(Event(cValue: event))
}

@_cdecl("cyrinx_swift_tx_callback")
private func cyrinx_swift_tx_callback(
    _ frame: UnsafePointer<UInt8>?,
    _ len: Int,
    _ userData: UnsafeMutableRawPointer?
) -> Int32 {
    guard let frame else {
        return CYRINX_SWIFT_ERR_INVALID_ARGUMENT
    }
    guard let userData else {
        return CYRINX_SWIFT_ERR_INTERNAL
    }

    let relay = Unmanaged<SessionCallbackRelay>.fromOpaque(userData).takeUnretainedValue()
    guard let backend = relay.audioBackend else {
        return CYRINX_SWIFT_OK
    }
    let bytes = UnsafeBufferPointer(start: frame, count: len)
    return backend.handleOutboundFrame(bytes)
}

/// Stateful transport session.
public final class CyrinxSession {
    private let handle: OpaquePointer
    private let relay: SessionCallbackRelay
    private let audioBackend: (any SessionFrameIOBackend)?
    private let relayToken: UnsafeMutableRawPointer
    public let events: AsyncStream<Event>

    /// Opens a new session handle and prepares event streaming.
    public init(config: Config = Config()) throws {
        relay = SessionCallbackRelay()
        relayToken = UnsafeMutableRawPointer(Unmanaged.passRetained(relay).toOpaque())

        var streamContinuation: AsyncStream<Event>.Continuation?
        events = AsyncStream<Event> { continuation in
            streamContinuation = continuation
        }
        relay.continuation = streamContinuation

        let callback: cyrinx_event_callback_t = cyrinx_swift_event_callback
        let backend: (any SessionFrameIOBackend)?
        let txCallback: cyrinx_tx_callback_t?
        if config.transportBackend == .appleAudioScaffold {
            backend = try AudioBackendFactory.make(config: config)
            relay.audioBackend = backend
            txCallback = cyrinx_swift_tx_callback
        } else {
            backend = nil
            txCallback = nil
        }
        audioBackend = backend

        var cConfig = config.toC(
            eventCallback: callback,
            txCallback: txCallback,
            userData: relayToken
        )

        guard let opened = cyrinx_open(&cConfig) else {
            Unmanaged<SessionCallbackRelay>.fromOpaque(relayToken).release()
            throw CyrinxError.status(CYRINX_SWIFT_ERR_INTERNAL)
        }

        handle = opened
        audioBackend?.attachSessionHandle(opened)
    }

    deinit {
        relay.continuation?.finish()
        audioBackend?.stop()
        cyrinx_close(handle)
        Unmanaged<SessionCallbackRelay>.fromOpaque(relayToken).release()
    }

    /// Moves the session from idle to discovery mode.
    public func start() throws {
        try audioBackend?.start()
        let rc = cyrinx_start(handle)
        do {
            try Self.checkStatus(rc)
        } catch {
            audioBackend?.stop()
            throw error
        }
    }

    /// Sends a payload on a specific logical stream.
    ///
    /// - Parameters:
    ///   - data: User payload bytes.
    ///   - streamID: Application stream ID in range `1...4095` (`0` is reserved).
    ///   - qos: Best-effort or reliable delivery mode.
    ///   - priority: Stream scheduling priority hint.
    ///   - flags: Stream semantic flags (`.fin`, `.reset`) applied to message end.
    public func send(
        _ data: Data,
        streamID: UInt16,
        qos: QoS = .reliable,
        priority: StreamPriority = .normal,
        flags: StreamFlags = []
    ) throws {
        if streamID == UInt16(CYRINX_STREAM_CONTROL) {
            throw CyrinxError.status(CYRINX_SWIFT_ERR_INVALID_ARGUMENT)
        }

        let rc = data.withUnsafeBytes { rawBuffer -> Int32 in
            guard let base = rawBuffer.baseAddress?.assumingMemoryBound(to: UInt8.self) else {
                return CYRINX_SWIFT_ERR_INVALID_ARGUMENT
            }
            return cyrinx_send_stream(
                handle,
                base,
                data.count,
                qos.cValue,
                streamID,
                priority.rawValue,
                flags.rawValue
            )
        }
        try Self.checkStatus(rc)
    }

    /// Receives the next payload and its stream metadata if available before timeout.
    ///
    /// - Parameters:
    ///   - timeoutMS: Poll timeout in milliseconds. `0` performs a non-blocking poll.
    ///   - maxBytes: Initial receive buffer size. The call auto-retries once if resized.
    /// - Returns: A decoded message, or `nil` when timeout expires before data arrives.
    public func receive(timeoutMS: UInt32 = 0, maxBytes: Int = 4096) throws -> ReceivedMessage? {
        var buffer = [UInt8](repeating: 0, count: maxBytes)
        var len = buffer.count
        var meta = cyrinx_message_meta_t(stream_id: 0, priority: 0, flags: 0, payload_len: 0)
        let rc: Int32 = buffer.withUnsafeMutableBufferPointer { buf in
            cyrinx_recv_stream(handle, buf.baseAddress, &len, timeoutMS, &meta)
        }
        if rc == CYRINX_SWIFT_ERR_TIMEOUT {
            return nil
        }
        if rc == CYRINX_SWIFT_ERR_BUFFER_TOO_SMALL {
            buffer = [UInt8](repeating: 0, count: len)
            var retryLen = len
            var retryMeta = cyrinx_message_meta_t(stream_id: 0, priority: 0, flags: 0, payload_len: 0)
            let retry: Int32 = buffer.withUnsafeMutableBufferPointer { buf in
                cyrinx_recv_stream(handle, buf.baseAddress, &retryLen, timeoutMS, &retryMeta)
            }
            try Self.checkStatus(retry)
            return ReceivedMessage(
                data: Data(buffer.prefix(retryLen)),
                streamID: retryMeta.stream_id,
                priority: StreamPriority(rawValue: retryMeta.priority) ?? .normal,
                flags: StreamFlags(rawValue: retryMeta.flags)
            )
        }

        try Self.checkStatus(rc)
        return ReceivedMessage(
            data: Data(buffer.prefix(len)),
            streamID: meta.stream_id,
            priority: StreamPriority(rawValue: meta.priority) ?? .normal,
            flags: StreamFlags(rawValue: meta.flags)
        )
    }

    /// Returns instantaneous transport and ARC metrics.
    public var metrics: Metrics {
        var c = cyrinx_metrics_t()
        _ = cyrinx_get_metrics(handle, &c)
        return Metrics(c: c)
    }

    /// Returns platform audio backend diagnostics when `transportBackend` is `.appleAudioScaffold`.
    public var audioDiagnostics: AudioBackendDiagnostics? {
        audioBackend?.diagnostics
    }

    /// Replaces the ARC threshold policy for this session.
    public func setARCPolicy(_ policy: ARCPolicy) throws {
        var c = policy.toC()
        let rc = cyrinx_set_arc_policy(handle, &c)
        try Self.checkStatus(rc)
    }

    /// Injects channel-state feedback for simulation and deterministic testing.
    public func injectChannelReport(
        snrDB: Float,
        evmPct: Float,
        cfoHz: Float,
        per2s: Float,
        crcFail: Bool = false
    ) throws {
        var r = cyrinx_channel_report_t(
            snr_db: snrDB,
            evm_pct: evmPct,
            cfo_hz: cfoHz,
            per_2s: per2s,
            crc_fail: crcFail ? 1 : 0
        )
        let rc = cyrinx_update_channel_report(handle, &r)
        try Self.checkStatus(rc)
    }

    /// Connects two sessions in memory, bypassing any audio modem backend.
    public static func linkInMemory(_ a: CyrinxSession, _ b: CyrinxSession) throws {
        let rc = cyrinx_link_in_memory(a.handle, b.handle)
        try Self.checkStatus(rc)
    }

    private static func checkStatus(_ status: Int32) throws {
        if status != CYRINX_SWIFT_OK {
            throw CyrinxError.status(status)
        }
    }
}

public enum Cyrinx {
    /// Current C core version string.
    public static var version: String {
        String(cString: cyrinx_version())
    }

    /// Returns the symbolic token for a C status code.
    public static func statusName(for code: Int32) -> String {
        String(cString: cyrinx_status_name(code))
    }

    /// Returns the human-readable description for a C status code.
    public static func statusDescription(for code: Int32) -> String {
        String(cString: cyrinx_status_description(code))
    }

    /// Convenience helper that joins code, name, and description for logs.
    public static func explainStatus(_ code: Int32) -> String {
        "\(code) (\(statusName(for: code))): \(statusDescription(for: code))"
    }
}
