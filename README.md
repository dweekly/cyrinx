# cyrinx

`cyrinx` is a **research prototype** exploring data-over-sound for close-range
desktop-to-phone links (1-2 ft). It contains two largely separate strands:

1. An adaptive ultrasonic transport stack (Swift/C + Kotlin) targeting the
   18.5-23.5 kHz band — the original protocol design (gears, ARQ, crypto
   envelope). Functional but slow as measured (<0.3 kbps OTA).
2. A **measured wideband bulk PHY** (audible band) that achieved 36.6 / 27.3
   kbps verified over-the-air goodput between a MacBook Pro and a Pixel 7a — see
   below. The modem is now **ported into the portable C core** (`CCyrinx`,
   `cyrinx_bulk`) with a Swift binding (`BulkPHY`), validated bit-exact /
   float-tolerant against committed golden vectors and round-tripping in pure
   Swift (the in-progress publication effort, docs/PUBLICATION.md). Still
   pending: closed-loop rate adaptation and transport-API integration, the
   optional X25519 envelope, two-mic MRC, and **over-the-air re-validation
   through the C library itself** (the 36.6/27.3 kbps figure was measured via the
   Python reference harness; the library-native OTA result is PR 1.10). It has
   been validated in one physical geometry (phone on the palm rest). An inaudible
   ultrasonic-band variant is under investigation (docs/ULTRASONIC_BAND.md, PR #1).

On originality: the communications techniques used (OFDM, cyclic prefixes,
pilot tracking, QAM, convolutional/Viterbi FEC, CRC block verification) are
standard; data-over-sound itself has substantial prior art (minimodem, ggwave,
Quiet, Google Nearby Messages, Chirp/LISNR, BatNet). The contribution here is
the measured end-to-end result on this hardware pair, the diagnostic
methodology, and the documented platform/physical-layer defect catalog.

This repository currently provides:

- A C core (`CCyrinx`) with a stable C ABI
- A **portable-C wideband bulk PHY codec** (`cyrinx_bulk`: DetRng, CRC-32, K=7
  convolutional FEC + puncturing, Gray QAM, OFDM via a vendored KISS FFT behind
  the `cyrinx_fft` plan interface, soft Viterbi) with a Swift binding (`BulkPHY`),
  validated against committed golden vectors
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

## Measured bulk-PHY results

A wideband bulk-transfer PHY achieving measured, byte-verified over-the-air
goodput of **36.6 kbps Mac->Pixel 7a** (decoded on-device by `BulkDemod.kt`)
and **27.3 kbps Pixel 7a->Mac**. The research writeup — measured channel,
modem design, the four physical-layer defects, diagnostic methodology, and
platform gotchas — is [docs/ACOUSTIC_BULK_PHY.md](docs/ACOUSTIC_BULK_PHY.md)
(fresh as of 2026-06-09); the lab notebook and reproducible harness live in
[scratch/hw20k/](scratch/hw20k/NOTES.md). Hard-won **negative findings** (dead ends, disproved hypotheses, "don't do that" results) are consolidated in [docs/NEGATIVE_FINDINGS.md](docs/NEGATIVE_FINDINGS.md) (fresh as of 2026-06-10) so they are never rediscovered the expensive way. A contrast of the original PRD
against what was actually built (and why they diverged) is
[docs/PRD_VS_AS_BUILT.md](docs/PRD_VS_AS_BUILT.md) (fresh as of 2026-06-09).
An academic-workshop-style LaTeX whitepaper consolidating all of the above —
related work, channel measurements, modem design, the defect/diagnostic
catalog, the verified results, the portable-C library port + library-native OTA,
and the adaptive-MCS sounder / repositioning-guidance surfaces — is
[docs/whitepaper/cyrinx-acoustic-link.tex](docs/whitepaper/cyrinx-acoustic-link.tex)
(compiled PDF: [docs/whitepaper/cyrinx-acoustic-link.pdf](docs/whitepaper/cyrinx-acoustic-link.pdf),
19 pp; fresh as of 2026-06-10).

The same bulk PHY ported to an iOS HIL app and measured over the air on an
iPhone 17 Pro Max — **36.57 kbps Mac->iPhone** (decoded on-device by
`BulkDemod.swift`) and **16.87 kbps iPhone->Mac** (iPhone speaker is
band-limited) — plus the devicectl-based device-control mechanism and the
iPhone speaker's ultrasonic phase-incoherence finding, is in
[docs/IOS_HIL.md](docs/IOS_HIL.md) (fresh as of 2026-06-10).

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
swift test                    # portable KISS-FFT default (ships to Android too)
./scripts/test-accelerate.sh  # validate the Apple vDSP/Accelerate FFT backend
```

The bulk-PHY OFDM core uses a vendored KISS FFT by default (the portable
correctness reference); on Apple, building with `-DCYRINX_FFT_ACCELERATE` +
linking `Accelerate` swaps in a vDSP backend behind the same `cyrinx_fft`
interface, validated against the same golden vectors.

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

For our long-term, multi-antenna architectural vision and stack-ranked priorities organized around environmental sensing, capabilities handshaking, and SVD spatial sounding, please see the complete [ROADMAP.md](ROADMAP.md).

The active, sequenced effort to publish Cyrinx as a public Apache-2.0 library —
porting the wideband bulk PHY into the portable C core so the library itself
delivers the measured headline, plus the [cyrinx.org](https://cyrinx.org)
companion site and an arXiv paper — is tracked in
[docs/PUBLICATION.md](docs/PUBLICATION.md) (fresh as of 2026-06-10), with
day-to-day rationale in [docs/publication-journal.md](docs/publication-journal.md).

## License

Apache License 2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
Copyright 2026 Primatech Paper Co LLC. The optional crypto envelope is
experimental and unaudited; see [SECURITY.md](SECURITY.md).

