# cyrinx

`cyrinx` is an adaptive ultrasonic transport prototype optimized for close-range desktop-to-phone links (1-2 ft) in the 18.5-23.5 kHz band.

This repository currently provides:

- A C core (`CCyrinx`) with a stable C ABI
- A native Swift wrapper (`Cyrinx`)
- Frame codec with bit-packed headers, CRC16/CRC32C, fragmentation/reassembly
- Half-duplex ping-pong MAC with ACK and selective retransmission policy hooks
- Adaptive Rate Control (ARC) gear state machine
- Stream-multiplexed transport API (`stream_id`, priority, `FIN`/`RST` flags)
- In-memory linked transport for deterministic tests without audio hardware
- PHY utility module with Zadoff-Chu generation, CFO estimation, and dynamic CP selection
- Apple audio backend scaffold (`RemoteIO` on iOS, `AVAudioEngine` on macOS)
- End-to-end acoustic PHY bridge (`AcousticPHYLink`) with:
  - dual-ZC preamble-based synchronization
  - robust D-CSS control header
  - adaptive D-CSS/OFDM-QPSK payload modulation based on frame gear/control type
  - RX demodulation path into `cyrinx_ingest_frame` from live audio callbacks
- Shared fallback deterministic ultrasonic waveform synthesizer used by both Apple scaffolds
- Deterministic OFDM/D-CSS PHY stubs for C ABI compatibility
- vDSP-backed OFDM QPSK and D-CSS modulators/demodulators in Swift (`VDSPPHY`)
- Golden-vector, chunked-sequence, and acoustic end-to-end PHY tests for deterministic behavior

## Measured bulk-PHY results (hw20k)

A wideband bulk-transfer PHY achieving measured, byte-verified over-the-air
goodput of **36.6 kbps Mac->Pixel 7a** (decoded on-device by `BulkDemod.kt`)
and **27.3 kbps Pixel 7a->Mac** is documented in
[scratch/hw20k/NOTES.md](scratch/hw20k/NOTES.md) (fresh as of 2026-06-09),
with the channel measurements, modem design, debugging log, and the
reproducible harness (`scratch/hw20k/*.py`).

## Implemented Protocol Model

- `G1 Discovery`: ZC preamble + sync/CFO control path
- `G2 Robust`: D-CSS fallback mode
- `G3 Turbo`: OFDM mode (`QPSK`, `16QAM`, `64QAM experimental`)
- OFDM constants:
  - FFT size: `1024`
  - Subcarrier spacing: `46.875 Hz`
  - Active carriers: `106`
  - Default CP: `96 samples` (~2 ms @ 48 kHz)

## Stream Model

- `stream_id = 0` is reserved for control frames.
- Applications send on stream IDs `1...4095`.
- Each received payload includes stream metadata:
  - `stream_id`
  - `priority` (`0...3`)
  - `flags` (`FIN`, `RST`)

## Error Codes

`cyrinx` status codes are stable across the C and Swift APIs.

- Swift:
  - `CyrinxError` now includes `statusCode`, `statusName`, and `statusDescription`.
  - `Cyrinx.explainStatus(code)` returns a log-friendly single-line summary.
- C:
  - `cyrinx_status_name(int status)` returns symbolic names like `CYRINX_ERR_TIMEOUT`.
  - `cyrinx_status_description(int status)` returns a human-readable explanation.

Common status values:

- `0` (`CYRINX_OK`): success.
- `-1` (`CYRINX_ERR_INVALID_ARGUMENT`): invalid input parameter.
- `-2` (`CYRINX_ERR_NOT_RUNNING`): session not started.
- `-3` (`CYRINX_ERR_BUFFER_TOO_SMALL`): caller buffer too small.
- `-4` (`CYRINX_ERR_TIMEOUT`): timed out waiting for link progress/ACK.
- `-5` (`CYRINX_ERR_CRC`): frame integrity check failed.
- `-6` (`CYRINX_ERR_BUSY`): operation blocked by current activity.
- `-7` (`CYRINX_ERR_UNSUPPORTED`): feature not available in this build/backend.
- `-8` (`CYRINX_ERR_STATE`): wrong state for this API call.
- `-9` (`CYRINX_ERR_INTERNAL`): internal failure.

For HIL diagnostics, `CyrinxSession.playLocalAudibleBeacon()` emits a role-distinct audible pattern so you can verify local speaker output independent of link decode success.

## Apple-Targeted Configuration Hooks

`cyrinx_config_t` includes policy/config controls to support the Apple-specific deployment profile:

- `sample_rate_hz` (`48k` default)
- `tx_gain_cap` (`0.70` default)
- `spectral_leakage_limit_dbfs`
- `ofdm_cp_samples_default` / `ofdm_cp_samples_min`
- `enable_sensor_assisted_arc`
- `enable_dynamic_cp`
- `enable_sfbc_static_mode`

## Build and Test

```bash
swift test
```

## Sample Programs

Sample runnable programs live in `/Users/dew/dev/cyrinx/Examples/README.md`.

```bash
swift run cyrinx-example-loopback
swift run cyrinx-example-multiplex
swift run cyrinx-example-large-payload
swift run cyrinx-sim-bench --profile quiet --out artifacts/bench/sim-quiet.json
./scripts/hil-generate.sh --open
./scripts/probe-macos-audio-rates.sh
./scripts/hil-96k-smoke.sh --open
```

## Linting and Formatting

```bash
./scripts/format.sh
./scripts/format-check.sh
./scripts/lint.sh
./scripts/check.sh
```

- `format.sh`: applies Swift and C formatting in place.
- `format-check.sh`: fails if any Swift/C file is not formatted.
- `lint.sh`: runs `swiftlint` (strict mode) and `shellcheck` for scripts.
- `check.sh`: full gate (`format-check` + `lint` + `swift test`).
- `bench-sim.sh`: deterministic simulation benchmark JSON output under `artifacts/bench/`.
- `hil-generate.sh`: generates paired macOS+iOS hardware-in-the-loop app project under `Apps/HIL/`.
- `probe-macos-audio-rates.sh`: probes default macOS input/output sample-rate capability (including 96 kHz support) and writes JSON.
- `hil-96k-smoke.sh`: runs macOS capability probe, generates HIL project, builds macOS+iOS HIL apps, and prints 96 kHz validation steps.

## Current Scope & Future Roadmap

This implementation includes a real-time TX/RX acoustic physical layer in Swift for Apple audio backends, paired with a robust Kotlin companion target for physical Android devices, plus a comprehensive local-first simulation and benchmark suite.

For our long-term, multi-antenna architectural vision and stack-ranked priorities organized around environmental sensing, capabilities handshaking, and SVD spatial sounding, please see the complete [ROADMAP.md](file:///Users/dew/dev/cyrinx/ROADMAP.md).

