# ADR 0003: Processing Boundaries

## Status
Accepted

## Context
Acoustic communication requires low-latency signal processing to handle timing synchronization, channel estimation, and FEC decoding. During the development of Cyrinx 2.x, some pipelines relied on capturing raw PCM on the device and sending it over a secondary link (e.g., USB, ADB, or network) to a host machine to perform the actual demodulation and decoding. This is "host-assisted processing". 
For Cyrinx 3.0, the core SDK must support true real-time, on-device operations, while preserving host-assisted processing solely as a debugging, verification, and research oracle facility.

## Decision

### 1. On-Device vs. Host-Assisted Definition

- **On-Device Processing (Production Path)**: The target hardware that captures or renders the audio MUST execute the entire Cyrinx stack locally. This includes real-time audio routing, synchronization, demodulation, error correction (FEC), state reducing, and application-level payload dispatch. 
- **Host-Assisted Processing (Research & Debugging)**: Capturing raw PCM on a mobile device and streaming/exporting it via ADB, USB, or Wi-Fi to a separate host computer for batch decoding is strictly forbidden for production applications. However, this remain a first-class facility for research, golden vector generation, system replay, and test oracles.

### 2. DSP Block Isolation Guidelines
To ensure that all physical layer and session processing blocks can run seamlessly in either real-time on-device loops or batch host environments, the following rules apply:
- **No Global System Side-Effects**: DSP math modules (e.g., FFT wrappers, filters, demodulators) must not access global clocks, system file descriptors, or platform-specific variables.
- **Pure Context Injection**: Every DSP operator must accept its configuration (profiles), state buffers, and workspace memory explicitly from the caller. This ensures deterministic behavior when run in a unit test or batch simulation.
- **Variable Frame and Chunk Sizes**: DSP processing blocks must support arbitrary chunk processing sizes to accommodate various hardware callback buffer lengths (e.g., 256, 512, 1024 samples) without internal re-buffering logic that introduces latency.

### 3. Resource Budgets

To prevent Cyrinx from starving the host application of CPU cycles or memory, we enforce strict limits:

#### Mobile On-Device Budget (per active session)
- **CPU Usage**:
  - Apple (A-series / M-series): `< 5%` of a single CPU core.
  - Android (Qualcomm Snapdragon 8 Gen 2 equivalent or higher): `< 8%` of a single CPU core.
- **Memory Footprint**:
  - Heap usage: `< 4.0 MB` static allocation.
  - No dynamic allocations during the active transfer state.
- **Latency Budget**:
  - Total processing time per audio block (512 samples at 48kHz, ~10.6ms): `< 2.5ms` thread time.

#### Host/Desktop Replay Budget (batch processing)
- **CPU Usage**: Can scale to utilize maximum available cores for batch decoding and simulation.
- **Memory Footprint**: Bounded by the size of the captured recording being replayed.
