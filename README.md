# cyrinx

**Data over sound, measured** — [cyrinx.org](https://cyrinx.org) · [whitepaper (PDF, 27 pp)](docs/whitepaper/cyrinx-acoustic-link.pdf) · [v1.0.0 release](https://github.com/dweekly/cyrinx/releases/tag/v1.0.0)

`cyrinx` is a **research prototype** exploring data-over-sound for close-range
desktop-to-phone links (1-2 ft). It contains two largely separate strands:

1. An adaptive ultrasonic transport stack (Swift/C + Kotlin) targeting the
   18.5-23.5 kHz band — the original protocol design (gears, ARQ, crypto
   envelope). Functional but slow as measured (<0.3 kbps OTA).
2. A **measured wideband bulk PHY** (audible band): verified over-the-air
   goodput of 36.6 / 27.3 kbps between a MacBook Pro (M4) and a Pixel 7a,
   since **ported into the portable C core** (`CCyrinx`, `cyrinx_bulk`) with a
   Swift binding (`BulkPHY`), validated bit-exact / float-tolerant against
   committed golden vectors, and re-measured **library-native at 39.3 kbps OTA**
   (the shipped C codec, not the Python reference — see the results table
   below). A robustness/diversity layer gives graceful degradation across
   placements — measured 48 kbps down to a bps-scale floor, never zero.
   **Two-mic maximal-ratio combining now ships in the C codec**
   (`cyrinx_bulk_demodulate2`, golden-vector-pinned, Swift
   `BulkPHY.decode(_:combining:)`), and CP/NFFT are caller-selectable; the
   EVM-probe sounder, mic-selection policy, and the RS-coded MFSK floor remain
   in the Python bench layer ([ROADMAP.md](ROADMAP.md)). Still pending:
   transport-API integration and the optional X25519 envelope. An inaudible
   ultrasonic-band variant was investigated (docs/ULTRASONIC_BAND.md).

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
  the `cyrinx_fft` plan interface, soft Viterbi, **two-mic MRC demodulation**
  `cyrinx_bulk_demodulate2`) with a Swift binding (`BulkPHY`), validated
  against committed golden vectors — including a two-channel rescue fixture
  where mic0 alone fails and MRC decodes byte-exact
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

## Measured results (validated over the air)

All numbers are **ordered byte-verified goodput**: each counted block is
CRC-valid *and* byte-identical to the transmitted payload at the same ordered
position, divided by total airtime (preambles, pilots, FEC, CRCs, and
inter-frame gaps all count against it). Transmitting Mac in every row: MacBook
Pro M4, palm-rest-class geometry.

| Peer | Direction | Goodput | Decoded by |
|---|---|---|---|
| Pixel 7a | Mac → Pixel | **36.6 kbps** | on-device, `BulkDemod.kt` |
| Pixel 7a | Pixel → Mac | **27.3 kbps** | `modem.py` reference |
| Pixel 7a | Mac → Pixel, **library-native** | **39.3 kbps** | the shipped C codec (`libcyrinxbulk`), 16-QAM r¾, 375/375 blocks |
| iPhone 17 Pro Max | Mac → iPhone | **36.57 kbps** | on-device, `BulkDemod.swift` |
| iPhone 17 Pro Max | iPhone → Mac | **16.87 kbps** | `modem.py` (iPhone speaker is band-limited to ≈11 kHz usable) |

With the robustness/diversity layer engaged, the link degrades gracefully
across placements — OTA re-validated 2026-07-08: **48 kbps** (clean) →
**11.6 kbps** (reverberant — 0/75 blocks decodable on either mic alone,
75/75 recovered by two-mic MRC through the shipped C library) → **138 bps**
(shadowed; the RS-coded MFSK floor, ×2 the earlier repetition floor) — never
zero. Milestone history: [CHANGELOG.md](CHANGELOG.md).

Writeups: [docs/ACOUSTIC_BULK_PHY.md](docs/ACOUSTIC_BULK_PHY.md) (channel
measurements, modem design, the four physical-layer defects, diagnostic
methodology); [docs/IOS_HIL.md](docs/IOS_HIL.md) (iPhone parity, `devicectl`
device control, iPhone speaker findings);
[docs/EXPERIMENTS.md](docs/EXPERIMENTS.md) (measurement campaign + live-bench
findings); [docs/NEGATIVE_FINDINGS.md](docs/NEGATIVE_FINDINGS.md) (dead ends
and disproved hypotheses, kept so they are never rediscovered the expensive
way); [docs/PRD_VS_AS_BUILT.md](docs/PRD_VS_AS_BUILT.md) (original PRD vs what
was built and why they diverged). The lab notebook and reproducible harness
live in [scratch/hw20k/](scratch/hw20k/NOTES.md).

Everything above is consolidated in an academic-workshop-style whitepaper —
[docs/whitepaper/cyrinx-acoustic-link.tex](docs/whitepaper/cyrinx-acoustic-link.tex)
(compiled PDF:
[docs/whitepaper/cyrinx-acoustic-link.pdf](docs/whitepaper/cyrinx-acoustic-link.pdf),
27 pp; fresh as of 2026-07-08) — including related work, the effective-SINR/EVM
ceiling on higher-order QAM, the measured channel-response and frame-anatomy
figures, the graceful-degradation section, and a development-provenance
section documenting which AI agent built each era (from commit trailers) and
framing the project as a hard-to-game agent capabilities benchmark.

## Using the library (SwiftPM)

```swift
// Package.swift
.package(url: "https://github.com/dweekly/cyrinx.git", from: "1.0.0")
// target dependency: .product(name: "Cyrinx", package: "cyrinx")
```

```swift
import Cyrinx
import Foundation

let phy = BulkPHY()                        // the measured wideband bulk PHY
let g = phy.geometry()!                    // frame geometry for the config
let payload = Data(repeating: 0xA5, count: g.payloadBytes)
let wave = phy.encode(payload)!            // Float samples at 48 kHz
let decoded = phy.decode(wave)!            // or decode(_:combining:) for two-mic MRC
assert(decoded.isComplete && decoded.payload == payload)
```

(That snippet is the release smoke test — it round-trips 75/75 CRC blocks
digitally. Real links need audio I/O and the level/geometry guidance in the
bench quick-start below.) Android binds the same C core via JNI; Python
drives it via `ctypes` (`scratch/hw20k/clib.py`).

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

Sample runnable programs live in [Examples/README.md](Examples/README.md).

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

## Bench quick-start (OTA measurements)

- **Hardware:** MacBook Pro ↔ Pixel 7a over USB (`adb`), or iPhone via
  `xcrun devicectl` (see docs/IOS_HIL.md). All published numbers were measured
  on an **M4 MacBook Pro**; gain-staging constants and geometry cells are
  machine-specific and must be re-derived on any other Mac.
- **Python env** (all Python work happens in the venv, never system-wide):
  `python3 -m venv .venv && .venv/bin/pip install numpy scipy sounddevice`.
- **Rebuild the library dylib:** clang one-liner in the header of
  `scratch/hw20k/clib.py`.
- **Offline self-tests (no hardware, no sound emitted):**
  `.venv/bin/python3 scratch/hw20k/sounder.py selftest`, `mfsk.py`,
  `freqresp.py selftest`, `env_sweep.py selftest`, and `clib.py` (digital
  loopback of the shipped C codec).
- **Smoke test (emits audio):** `.venv/bin/python3 scratch/hw20k/harness.py smoke`.
- **Adaptive loop (emits audio):** `.venv/bin/python3 scratch/hw20k/adaptive.py <label>`.
- **M4 bench settings:** Mac output 100 %, Mac input ~22/100 (clips above),
  phone media volume max. Good geometry: phone face-down on a soft cloth over
  the function-key area, charge port toward the Mac speakers (39–48 kbps).
  Overhanging the mic into the keyboard well is reverberant (the MRC-carried
  ~11.6 kbps cell); the desk plane below a laptop stand is shadowed — the
  speakers fire upward — and degrades to the 138 bps floor.

## Roadmap & documentation map

- [ROADMAP.md](ROADMAP.md) — the single forward-looking, stack-ranked plan
  (robust-library track, 2×2-MIMO frontier, website, publication finalization,
  exploratory backlog).
- [CHANGELOG.md](CHANGELOG.md) — validated milestone history.
- [docs/PUBLICATION.md](docs/PUBLICATION.md) — the sequenced publication effort
  (public Apache-2.0 library, [cyrinx.org](https://cyrinx.org), arXiv paper),
  with day-to-day rationale in
  [docs/publication-journal.md](docs/publication-journal.md).
- [docs/EXPERIMENTS.md](docs/EXPERIMENTS.md) — the OTA measurement campaign and
  its live-bench findings.
- [docs/A1_AUTO_MRC.md](docs/A1_AUTO_MRC.md) — the executed plan for auto-MRC
  in the live adaptive loop (completed 2026-07-08, PR #52; kept as the
  worked example of a plan → bench → referee cycle).

## License

Apache License 2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
Copyright 2026 Primatech Paper Co LLC. The optional crypto envelope is
experimental and unaudited; see [SECURITY.md](SECURITY.md).

