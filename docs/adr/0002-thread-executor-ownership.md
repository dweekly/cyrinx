# ADR 0002: Thread and Executor Ownership

## Status
Accepted

## Context
Acoustic data transmission requires deterministic timing and execution latency guarantees. In previous versions of Cyrinx, ad-hoc threading and lock-based synchronization between platform audio threads (e.g., CoreAudio HAL callbacks, Android AudioRecord loops) and the core decoding state machine caused occasional thread contention, priority inversions, and heap-allocation overhead. To support robust real-time operations, we must define strict execution context boundaries and resource constraints.

## Decision

### 1. Serial Executor Ownership
Every session mutation, state change, and packet dispatch MUST run on a single declared serial executor per session.
- **Swift Binding**: Controlled by the `CyrinxTransport` actor. All interaction with the underlying C session state is isolated within this actor's serial execution context.
- **Kotlin Binding**: Dispatched onto a dedicated single-threaded dispatcher or a serialized Coroutine scope context.
- **C Core**: The `cyrinx_session_t` object is not thread-safe. All calls to `cyrinx_session_process()`, `cyrinx_session_ingest()`, or `cyrinx_session_snapshot()` must be serialized by the calling binding.

### 2. High-Priority Audio Callback Constraints
The audio capture and render callbacks (e.g., AURemoteIO, AVAudioEngine render blocks, AudioRecord threads) run in a high-priority, real-time context. To prevent audio glitches (dropouts/underruns/overruns), these callbacks are prohibited from performing any operations that may yield, block, or allocate memory.

Within any high-priority audio callback:
- **No Heap Allocation**: Calls to `malloc()`, `free()`, `realloc()`, or C++ `new`/`delete` are strictly prohibited.
- **No String Formatting or Logging**: String interpolation, printing (`print`, `printf`), and system logging (`NSLog`, `os_log`, `__android_log_print`) are prohibited because they perform system calls and memory allocations.
- **No Synchronization Mutexes**: Heavy lock primitives (mutexes, semaphores, condition variables) are prohibited. Use only lock-free, single-producer single-consumer (SPSC) ring buffers.
- **No Complex DSP / FFT**: Heavy mathematical processing like Fast Fourier Transforms (FFT) or framing detection must be offloaded to the serial session executor or a dedicated background DSP thread. Callbacks only copy bounded PCM blocks.

### 3. Bounded Resource Limits and Overflow Behaviors
To prevent memory exhaustion and uncontrolled latency, every queue, buffer, and window has a documented static limit and explicit overflow policy:

| Resource | Maximum Bound | Overflow Policy |
| :--- | :--- | :--- |
| **PCM Ingress/Egress Queues** | 8192 samples (approx. 170ms at 48kHz) | **Reject & Drop**: Drop new incoming audio samples, increment drop counter, emit a warning event. |
| **Event Queues** | 128 elements | **Block/Discard**: Stop generating non-critical events. Critical session events trigger snapshot state invalidation. |
| **Message Tx/Rx Queues** | 16 messages | **Backpressure**: Reject additional `send()` requests from the application level with `CyrinxError.queueFull`. |
| **Reassembly Buffers** | 64 KB per message | **Abrupt Fail**: Terminate transfer, release buffer, notify sender with CRC/timeout error. |
| **Retry Window** | 8 frames | **Sliding Window**: Reject additions beyond current window limits; wait for ACK or timeout. |
| **Diagnostic Ring Buffer** | 1.0 MB | **Sliding Window**: Overwrite the oldest diagnostic entries with the newest metrics. |
