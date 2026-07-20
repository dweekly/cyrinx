# ADR 0005: Message-First API and Vocabulary

## Status
Accepted

## Context
Acoustic channels are highly dynamic, characterized by severe multipath fading, frequency-selective interference, variable propagation delays, and packet loss. Attempting to emulate a reliable continuous stream (like TCP) directly over an acoustic interface leads to significant latency spikes, buffer bloat, and fragile connection state.
To provide applications with robust, deterministic controls, Cyrinx 3.0 pivots away from raw byte-stream APIs to a **Message-First** architecture.

## Decision
Cyrinx 3.0 exposes a public API optimized for discrete, bounded messages (transfers) with explicit tracking of delivery progress and link characteristics.

---

### 1. Stable API Vocabulary

The public Swift surface defines the following stable vocabulary:

- **`CyrinxTransport`**: The primary coordinator class/actor. It manages the underlying audio engine, discovery state, role election, and acts as the factory for connections.
- **`CyrinxPeer`**: A lightweight representation of a discovered remote endpoint. Contains a stable cryptographic identifier or hardware-derived address.
- **`CyrinxConnection`**: A logical association between the local endpoint and a `CyrinxPeer`. Manages the handshake status, active profiles, and transfer queues.
- **`CyrinxTransfer`**: Represents an individual message send or receive operation. Exposes unique transfer IDs, status (pending, sending, completed, failed), and progress tracking.
- **`LinkEstimate`**: A snapshot of current channel health metrics. Includes signal-to-noise ratio (SNR) in dB, pilot subcarrier confidence, estimated propagation delay, and symbol timing error.
- **`SendOptions`**: Parameters tuning how a message is transmitted. Allows configuring priority level, timeout duration, reliability expectations (acknowledged vs. unacknowledged), and profile overrides.
- **`CyrinxError`**: A structured enumeration of potential failure conditions, containing precise reason codes and error metadata.

---

### 2. Message-First Semantics

- **Discrete Boundaries**: A transfer represents a discrete payload of up to 64 KB. 
- **Explicit Acknowledgement**: The transport supports automatic repeat request (ARQ) at the frame level. Transfers can be sent as `acknowledged` (requiring a CRC-validated reply frame from the peer) or `unacknowledged` (best-effort broadcast).
- **No Implicit Streaming**: A byte-stream interface is not supported natively. If streaming is required in future 3.x iterations, it must be implemented as a layer on top of discrete messages (e.g., transmitting sequential numbered message chunks).

---

### 3. Queue Limits, Backpressure, and Overflow Behavior

At the API layer, bounds are enforced to maintain deterministic performance under poor channel conditions:

- **Send Queue Limit**: A `CyrinxConnection` holds a maximum of 16 pending transfers in its outbound queue.
- **Backpressure**:
  - When the send queue is full, attempting to call `CyrinxConnection.send()` immediately throws `CyrinxError.queueFull`.
  - Applications must handle this error by pausing transmissions or showing a "network busy" state.
- **Receive Buffer Limit**: Incoming partial payloads are stored in reassembly buffers capped at 64 KB per message. If a message assembly fails due to a timeout or missing packets that exceed the recovery window, the partial buffer is cleared, and the transfer fails.
- **Diagnostics Queue**: Retained logs and metrics are written to a sliding-window ring buffer. When the buffer reaches 1.0 MB, older logs are overwritten.
