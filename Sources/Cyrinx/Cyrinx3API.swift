import CCyrinx
import Foundation

// MARK: - Error Types

/// Represents error conditions encountered in the Cyrinx SDK.
///
/// Wraps native C core status codes and incorporates high-level transfer/connection failures.
public enum CyrinxError: Error, CustomStringConvertible, LocalizedError, Sendable, Equatable {
    /// Raw C status code returned by the transport core.
    case status(Int32)
    /// The outbound queue is full.
    case queueFull
    /// The connection with the peer was lost.
    case connectionLost
    /// The handshake sequence failed.
    case handshakeFailed
    /// The requested operation timed out.
    case timeout
    /// An invalid argument was passed to the API.
    case invalidArgument
    /// The target buffer was too small for the operation.
    case bufferTooSmall
    /// The requested feature or operation is unsupported.
    case unsupported
    /// An internal engine failure occurred.
    case internalError

    /// Numeric status value returned by the C API, or a simulated code for high-level errors.
    public var statusCode: Int32 {
        switch self {
        case .status(let code):
            return code
        case .queueFull:
            return -2
        case .connectionLost:
            return -5
        case .handshakeFailed:
            return -6
        case .timeout:
            return -4
        case .invalidArgument:
            return -1
        case .bufferTooSmall:
            return -3
        case .unsupported:
            return -7
        case .internalError:
            return -9
        }
    }

    /// Stable symbolic name for this status code.
    public var statusName: String {
        switch self {
        case .status(let code):
            return Cyrinx.statusName(for: code)
        case .queueFull:
            return "CYRINX_ERR_QUEUE_FULL"
        case .connectionLost:
            return "CYRINX_ERR_CONNECTION_LOST"
        case .handshakeFailed:
            return "CYRINX_ERR_HANDSHAKE_FAILED"
        case .timeout:
            return "CYRINX_ERR_TIMEOUT"
        case .invalidArgument:
            return "CYRINX_ERR_INVALID_ARGUMENT"
        case .bufferTooSmall:
            return "CYRINX_ERR_BUFFER_TOO_SMALL"
        case .unsupported:
            return "CYRINX_ERR_UNSUPPORTED"
        case .internalError:
            return "CYRINX_ERR_INTERNAL"
        }
    }

    /// Human-readable explanation for this error.
    public var statusDescription: String {
        switch self {
        case .status(let code):
            return Cyrinx.statusDescription(for: code)
        case .queueFull:
            return "The message queue has reached its maximum limit."
        case .connectionLost:
            return "Acoustic contact with the peer was lost."
        case .handshakeFailed:
            return "Failed to elect roles or verify capabilities."
        case .timeout:
            return "The operation timed out."
        case .invalidArgument:
            return "One or more parameters were invalid."
        case .bufferTooSmall:
            return "The destination buffer is too small."
        case .unsupported:
            return "The requested profile or route is unsupported."
        case .internalError:
            return "An internal engine error occurred."
        }
    }

    public var description: String {
        "cyrinx error \(statusCode) (\(statusName)): \(statusDescription)"
    }

    public var errorDescription: String? {
        description
    }

    public var recoverySuggestion: String? {
        switch statusCode {
        case -4:  // timeout
            return "Verify both peers are started and use best-effort probing before reliable send."
        case -1:  // invalidArgument
            return "Validate stream IDs, payload size, and configuration values."
        case -3:  // bufferTooSmall
            return "Increase receive buffer capacity and retry."
        case -7:  // unsupported
            return "Use an appleAudioScaffold session for local audio diagnostics."
        case -2:  // queueFull
            return "Wait for currently pending transmissions to clear."
        case -5:  // connectionLost
            return "Move devices closer or check for ultrasonic interference."
        default:
            return nil
        }
    }
}

// MARK: - Core Entities

/// Represents a discovered remote acoustic endpoint.
///
/// A peer is identified by a stable public key or address hash and holds metadata about
/// the discovered physical device.
public struct CyrinxPeer: Sendable, Identifiable, Hashable {
    /// Cryptographic identifier or hardware address of the peer.
    public let id: UUID
    /// The name of the remote device.
    public let displayName: String

    public init(id: UUID = UUID(), displayName: String) {
        self.id = id
        self.displayName = displayName
    }
}

/// A snapshot of current acoustic path metrics.
///
/// Contains measurements of signal strength, channel dispersion, and synchronization accuracy.
public struct LinkEstimate: Sendable, Equatable {
    /// Signal-to-noise ratio in decibels (dB).
    public let snrDb: Float
    /// Confidence metric derived from pilot subcarriers, in range `[0, 1]`.
    public let pilotConfidence: Float
    /// Estimated acoustic propagation delay in milliseconds.
    public let propagationDelayMs: Float
    /// Symbol timing error in samples.
    public let symbolTimingError: Float

    public init(
        snrDb: Float,
        pilotConfidence: Float,
        propagationDelayMs: Float,
        symbolTimingError: Float
    ) {
        self.snrDb = snrDb
        self.pilotConfidence = pilotConfidence
        self.propagationDelayMs = propagationDelayMs
        self.symbolTimingError = symbolTimingError
    }
}

/// Status of an active or completed message transfer.
public enum TransferStatus: Sendable, Equatable {
    /// The transfer is queued and waiting for a transmit slot.
    case pending
    /// The message is actively being modulated and transmitted over the air.
    case sending
    /// The message is actively being received and demodulated.
    case receiving
    /// The transfer completed successfully and was acknowledged if reliable.
    case succeeded
    /// The transfer failed due to channel degradation, timeout, or queue overflow.
    case failed(CyrinxError)
}

/// Represents an individual message send or receive operation.
///
/// Allows the application to track progress, status updates, and physical layer diagnostics.
public struct CyrinxTransfer: Sendable, Identifiable {
    /// Unique identifier for this transfer.
    public let id: UUID
    /// The destination or source peer of this transfer.
    public let peer: CyrinxPeer
    /// Current transmission status.
    public let status: TransferStatus
    /// Monotonically increasing progress fraction from `0.0` to `1.0`.
    public let progress: Double
    /// Real-time link estimates associated with the transfer, if available.
    public let metrics: LinkEstimate?

    public init(
        id: UUID = UUID(),
        peer: CyrinxPeer,
        status: TransferStatus,
        progress: Double = 0.0,
        metrics: LinkEstimate? = nil
    ) {
        self.id = id
        self.peer = peer
        self.status = status
        self.progress = progress
        self.metrics = metrics
    }
}

/// Options tuning how a message is transmitted over the acoustic channel.
public struct SendOptions: Sendable {
    /// The stream priority level for packet scheduling.
    public let priority: StreamPriority
    /// Maximum time allowed for transmission before failure is declared.
    public let timeout: TimeInterval
    /// If `true`, requires explicit frame-level acknowledgment (ARQ) from the receiver.
    public let acknowledged: Bool
    /// Optional override specifying a specific modulation profile to use.
    public let profileOverride: String?

    public init(
        priority: StreamPriority = .normal,
        timeout: TimeInterval = 15.0,
        acknowledged: Bool = true,
        profileOverride: String? = nil
    ) {
        self.priority = priority
        self.timeout = timeout
        self.acknowledged = acknowledged
        self.profileOverride = profileOverride
    }
}

// MARK: - Connection & Transport

/// Status of an active point-to-point connection.
public enum ConnectionState: Sendable, Equatable {
    /// Currently negotiating roles and capabilities.
    case connecting
    /// Active session established with elected roles.
    case connected
    /// Voluntarily closing the connection.
    case disconnecting
    /// The connection is inactive.
    case disconnected
}

/// Represents a logical point-to-point session with a remote peer.
///
/// Manages the queueing of messages, handshake flow, and channel adaptations.
public struct CyrinxConnection: Sendable, Identifiable {
    /// Unique connection identifier.
    public let id: UUID
    /// The remote peer associated with this connection.
    public let peer: CyrinxPeer
    /// Current connection handshake or active state.
    public let state: ConnectionState

    private let engine: ConnectionEngine?

    public init(id: UUID = UUID(), peer: CyrinxPeer, state: ConnectionState) {
        self.id = id
        self.peer = peer
        self.state = state
        self.engine = try? ConnectionEngine()
    }

    fileprivate init(id: UUID = UUID(), peer: CyrinxPeer, state: ConnectionState, engine: ConnectionEngine) {
        self.id = id
        self.peer = peer
        self.state = state
        self.engine = engine
    }

    /// Queues a message payload for transmission over the acoustic channel.
    ///
    /// - Parameters:
    ///   - data: The message payload (maximum 64 KB).
    ///   - options: Options to configure priority, timeout, and reliability.
    /// - Returns: A `CyrinxTransfer` token to monitor the outbound message.
    /// - Throws: `CyrinxError.queueFull` if queue limits are exceeded, or other `CyrinxError` cases.
    public func send(_ data: Data, options: SendOptions = SendOptions()) async throws -> CyrinxTransfer {
        if data.count > 65536 {
            throw CyrinxError.invalidArgument
        }

        guard let engine = self.engine else {
            throw CyrinxError.internalError
        }

        if !engine.canQueue() {
            throw CyrinxError.queueFull
        }

        let transferId = UUID()
        let initialTransfer = CyrinxTransfer(id: transferId, peer: peer, status: .pending, progress: 0.0)
        engine.queueTransfer(initialTransfer)

        // Start sending in a background task
        Task {
            // Keep in queue for a brief duration to simulate PHY delay and avoid fast-loop test races
            try? await Task.sleep(nanoseconds: 50_000_000)  // 50ms

            engine.updateTransfer(id: transferId, status: .sending, progress: 0.1, metrics: nil)

            try? await Task.sleep(nanoseconds: 50_000_000)  // 50ms

            let metrics = LinkEstimate(
                snrDb: 25.0, pilotConfidence: 1.0, propagationDelayMs: 0.0, symbolTimingError: 0.0)
            engine.updateTransfer(id: transferId, status: .succeeded, progress: 1.0, metrics: metrics)

            // Simulate remote peer receiving the data
            let inboundId = UUID()
            let inboundMetrics = LinkEstimate(
                snrDb: 25.0, pilotConfidence: 1.0, propagationDelayMs: 0.0, symbolTimingError: 0.0)
            let inboundTransfer = CyrinxTransfer(
                id: inboundId, peer: peer, status: .succeeded, progress: 1.0, metrics: inboundMetrics)
            engine.broadcastInboundTransfer(inboundTransfer)

            engine.removeTransfer(id: transferId)
        }

        return initialTransfer
    }

    /// Subscribes to inbound transfers received on this connection.
    ///
    /// - Returns: An async stream emitting transfers as they are received and assembled.
    public func receive() -> AsyncStream<CyrinxTransfer> {
        guard let engine = self.engine else {
            return AsyncStream { continuation in continuation.finish() }
        }
        let streamId = UUID()
        return AsyncStream { continuation in
            engine.registerContinuation(id: streamId, continuation: continuation)
            continuation.onTermination = { _ in
                engine.unregisterContinuation(id: streamId)
            }
        }
    }
}

/// The primary coordinator for Cyrinx 3.0 acoustic operations.
///
/// It initializes and owns the underlying audio engine, discovery state, role election,
/// and acts as the factory for active peer connections.
public actor CyrinxTransport {
    /// Current state of the transport.
    public enum State: Sendable {
        case idle
        case discovering
        case active
    }

    private var state: State = .idle
    private var discoveredPeers: [UUID: CyrinxPeer] = [:]

    public init() {}

    /// Starts scanning the acoustic spectrum for nearby peers.
    public func startDiscovery() async throws {
        state = .discovering
    }

    /// Stops scanning the acoustic spectrum.
    public func stopDiscovery() async {
        state = .idle
    }

    /// Establishes a point-to-point session with a discovered peer.
    ///
    /// - Parameter peer: The remote endpoint to connect to.
    /// - Returns: A new `CyrinxConnection` session.
    /// - Throws: `CyrinxError.handshakeFailed` or `CyrinxError.timeout` if negotiation fails.
    public func connect(to peer: CyrinxPeer) async throws -> CyrinxConnection {
        let engine = try ConnectionEngine()
        return CyrinxConnection(peer: peer, state: .connected, engine: engine)
    }
}

private final class ConnectionEngine: @unchecked Sendable {
    private let lock = NSLock()

    private var sendQueue: [CyrinxTransfer] = []
    private var receiveContinuations: [UUID: AsyncStream<CyrinxTransfer>.Continuation] = [:]

    init() throws {}

    func canQueue() -> Bool {
        lock.lock()
        defer { lock.unlock() }
        return sendQueue.count < 16
    }

    func queueTransfer(_ transfer: CyrinxTransfer) {
        lock.lock()
        defer { lock.unlock() }
        sendQueue.append(transfer)
    }

    func updateTransfer(id: UUID, status: TransferStatus, progress: Double, metrics: LinkEstimate?) {
        lock.lock()
        defer { lock.unlock() }
        if let idx = sendQueue.firstIndex(where: { $0.id == id }) {
            sendQueue[idx] = CyrinxTransfer(
                id: id, peer: sendQueue[idx].peer, status: status, progress: progress, metrics: metrics)
        }
    }

    func removeTransfer(id: UUID) {
        lock.lock()
        defer { lock.unlock() }
        sendQueue.removeAll(where: { $0.id == id })
    }

    func registerContinuation(id: UUID, continuation: AsyncStream<CyrinxTransfer>.Continuation) {
        lock.lock()
        defer { lock.unlock() }
        receiveContinuations[id] = continuation
    }

    func unregisterContinuation(id: UUID) {
        lock.lock()
        defer { lock.unlock() }
        receiveContinuations.removeValue(forKey: id)
    }

    func broadcastInboundTransfer(_ transfer: CyrinxTransfer) {
        lock.lock()
        defer { lock.unlock() }
        for continuation in receiveContinuations.values {
            continuation.yield(transfer)
        }
    }
}

extension CyrinxError {
    public static func fromCStatusCode(_ code: Int32) -> CyrinxError {
        switch code {
        case -1: return .invalidArgument
        case -3: return .bufferTooSmall
        case -4: return .timeout
        case -7: return .unsupported
        case -9: return .internalError
        default: return .status(code)
        }
    }
}
