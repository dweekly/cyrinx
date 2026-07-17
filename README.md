# cyrinx

**Data over sound, measured** — [cyrinx.org](https://cyrinx.org) ·
[Cyrinx 2.0 follow-on (PDF, 11 pp)](docs/whitepaper/cyrinx-2-goodput.pdf) ·
[Cyrinx 1.0 paper (PDF, 28 pp)](docs/whitepaper/cyrinx-acoustic-link.pdf) ·
[v2.0.0 release](https://github.com/dweekly/cyrinx/releases/tag/v2.0.0)

`cyrinx` is a **research prototype** exploring data-over-sound for close-range
desktop-to-phone links. It contains two largely separate strands:

1. An adaptive ultrasonic transport stack (Swift/C + Kotlin) targeting the
   18.5-23.5 kHz band — the original protocol design (gears, ARQ, crypto
   envelope). Functional but slow as measured (<0.3 kbps OTA).
2. A **measured wideband bulk PHY** (audible band): Cyrinx 2.0 reaches
   **65.875 kbps** from a MacBook Pro to a Pixel 7a in the accepted five-frame,
   250 ms-gap measurement class, versus the 36.571 kbps result it was designed
   to beat. A separate zero-gap research class reaches **69.652 kbps mean**.
   The canonical receiver is portable C (`CCyrinx`, `cyrinx_bulk`) with a thin
   Swift binding (`BulkPHY`); committed tests pin two-mic maximal-ratio
   combining, held-out-pilot automatic mic selection, and frequency-local
   known-pilot LLR weighting. The new fast profiles trade resilience for
   throughput and do **not** match the conservative baseline's block-success
   rate. The EVM-probe sounder and RS-coded MFSK floor remain research tools in
   the Python bench layer ([ROADMAP.md](ROADMAP.md)). Transport-API integration,
   Android JNI, and the optional X25519 envelope remain pending. An ultrasonic
   downlink was investigated, but an integrated ultrasonic mode has not shipped
   ([docs/ULTRASONIC_BAND.md](docs/ULTRASONIC_BAND.md)).

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
  where mic0 alone fails and MRC decodes byte-exact, a versioned receiver
  contract, pilot-local reliability weighting, and payload-independent
  automatic diversity selection
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

The Cyrinx 2.0 rows use **strict ordered byte-verified goodput**: a counted block
is CRC-valid *and* byte-identical to the transmitted payload at the same
scheduled position. Preambles, pilots, FEC, CRCs, and declared inter-frame gaps
are in the denominator. Historical rows retain their original byte-verification
method. In the accepted Pixel run, 375/375 decoded CRC-valid records passed
expected-set membership; the verifier retained neither record uniqueness nor
strict stream position. Every row uses the same M4 MacBook Pro.

| Peer / profile | Direction | Schedule class | Scheduled goodput | Verified blocks | Decoded by |
|---|---|---|---:|---:|---|
| Pixel 7a, accepted 1.x result | Mac → Pixel | 48 kHz, five frames, four 250 ms gaps | **36.571 kbps** | 375/375 expected-set checks | on-device `BulkDemod.kt` |
| Pixel 7a, Cyrinx 2.0 p16/sym64 | Mac → Pixel | same 48 kHz / five-frame / 250 ms-gap class | **65.875 kbps** (65.266–66.641) | 4,215/4,280 (98.481%) | frozen C host decoder, Pixel stereo capture |
| Pixel 7a, Cyrinx 2.0 p64/sym96 | Mac → Pixel | separate 48 kHz / five-frame / zero-gap class | **69.652 kbps** (65.731–72.641) | 6,129/6,760 (90.666%) | frozen C host decoder, Pixel stereo capture |
| Pixel 7a, library-native control | Mac → Pixel | five independent one-frame trials | **38.400 kbps** | 375/375 console aggregate | shipped C codec, 16-QAM r3/4 |
| Pixel 7a | Pixel → Mac | historical | **27.3 kbps** | 280/280 expected-set checks | `modem.py` reference |
| iPhone 17 Pro Max | Mac → iPhone | historical | **36.57 kbps** | — | on-device `BulkDemod.swift` |
| iPhone 17 Pro Max | iPhone → Mac | historical | **16.87 kbps** | — | `modem.py`; speaker usable to about 11 kHz |

Both prospective Cyrinx 2.0 campaigns completed all 16 planned runs, won all
eight paired comparisons (exact one-sided sign test, `p = 1/256`), and retained
every failure. Both also failed the predeclared resilience gate: the comparable
p16 candidate's 98.481% block success was below its paired baseline's 99.967%,
and the zero-gap p64 confirmatory candidate's 90.666% was below 99.757%. The 69.652
kbps result is **1.9045×** the accepted 36.571 kbps number—not “almost
tripled”—and its zero-gap schedule must not be presented as the same measurement
class. Its gross mean including the stream-end pad was 68.636 kbps.

The historical library-native result was previously labeled 39.3 kbps by
dividing 157,050 coded information bits (including CRC and fill) by a 4 s
frame. Contemporaneous console output reported 375/375 across five independent
one-frame trials; no matching machine-readable run record or raw capture was
retained. Conditional on that aggregate, five times 19,200 payload bytes over
five times 4 s is 38.400 kbps. The accounting and evidence limitations are
recorded in the
[historical correction ledger](scratch/hw20k/evidence/historical-metric-corrections-2026-07-17/results-ledger.json).

### What changed in Cyrinx 2.0

The throughput increase is a bundle of PHY, receiver, and scheduling changes;
only the receiver weighting has a same-capture isolated comparison.

| Change | Purpose and evidence |
|---|---|
| CP 96 instead of the conservative CP 240 paired control (CP 768 historically) | Reduces guard overhead; CP 48 was screened and rejected as unstable. |
| 64-QAM with rate-2/3 FEC | Carries more bits while retaining more redundancy than the failed Pixel rate-3/4 and rate-5/6 profiles. |
| Pilot spacing 16 for the comparable result; 64 for the zero-gap confirmatory result | Trades pilot observations for data carriers. Pilot-32 measured 68.960 kbps, below the observed pilot-16 and pilot-64 means; success also fell as pilots became sparser. |
| Frequency-local known-pilot LLR weighting in C | On the same fresh captures, improved 3,868/4,280 legacy blocks to 4,215/4,280; all eight runs improved and none regressed. |
| Held-out-pilot mic0/MRC selection | Uses both Pixel microphones without payload, decoded-bit, or CRC leakage. The zero-gap confirmatory campaign selected MRC in 26/40 candidate frames and mic0 in 14/40. |
| Qualified Pixel route at Mac volume 50%, waveform peak 0.18 | An enabling SNR condition, not an algorithmic throughput contribution or an SPL safety rating. |
| 96-symbol frames and zero inter-frame gap | Amortize framing and scheduler idle time, but define a different measurement class. A separate 128-symbol test reached only 66.102 kbps at 89.093% block success, with chronological degradation consistent with channel drift; it was not a randomized length/gap ablation. |

The exact Pixel cell was face-up on 0.5-inch soft cloth above the MacBook left
function-key area, with the bottom microphone near the built-in left speaker,
48 kHz stereo `UNPROCESSED` capture, and the A/C enabled but cycling and
uninstrumented. SPL was not instrumented. These are route-specific bench
results, not general device guarantees. Exact denominators and content hashes
are retained in the tracked
[Cyrinx 2.0 Pixel evidence ledger](scratch/hw20k/evidence/pixel7a-cyrinx2-2026-07-17/results-ledger.json).

With the robustness/diversity layer engaged, the link degrades gracefully
across placements — OTA re-validated 2026-07-08: **46.915 kbps** post-sounding
PHY payload rate (clean) → **11.366 kbps** post-sounding PHY payload rate
(reverberant — 0/75 blocks decodable on either mic alone,
75/75 recovered by two-mic MRC through the shipped C library) → **138 bps**
(shadowed; the RS-coded MFSK active-frame floor, 131.282 bps including its
unconditional 0.1 s tail pad, ×2 the earlier repetition floor) — never zero.
The coherent figures exclude the preceding sounding/probe transaction; its
cost must be amortized over a session and was not included in those rates.
Normalizing by emitted samples for the recorded geometry gives approximately
32.852 kbps clean and 8.95 kbps reverberant, still excluding host/ADB wall time.
Milestone history: [CHANGELOG.md](CHANGELOG.md).

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

The publication record is split deliberately. The
[Cyrinx 1.0 source](docs/whitepaper/cyrinx-acoustic-link.tex) and
[28-page PDF](docs/whitepaper/cyrinx-acoustic-link.pdf) remain the
pre-Cyrinx-2.0 system study, with the Moto supplement and accounting errata
through 2026-07-17. The separate
[Cyrinx 2.0 source](docs/whitepaper/cyrinx-2-goodput.tex) and
[11-page follow-on PDF](docs/whitepaper/cyrinx-2-goodput.pdf) document the
65.875 kbps schedule-comparable Pixel result, the isolated receiver replay,
the zero-gap frontier, route qualification, negative experiments, ultrasonic
and pleasant-audible status, evidence availability, and claim boundaries.

## Using the library (SwiftPM)

```swift
// Package.swift
.package(url: "https://github.com/dweekly/cyrinx.git", from: "2.0.0")
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
bench quick-start below.) Python drives the C core via `ctypes`
(`scratch/hw20k/clib.py`). The Android HIL receiver is currently a separate,
older Kotlin DSP implementation rather than JNI; it supports neither the Moto
CP240/p8/16-QAM/r3/4 nor the Pixel CP96/64-QAM/r2/3 Cyrinx 2.0 profiles. New
Pixel measurements capture stereo on Android and decode with the frozen C
library on the host. Converging
Android and the iOS HIL receiver on the C core is tracked as technical debt in
[ROADMAP.md](ROADMAP.md).

## Implemented Protocol Model

- `G1 Discovery`: ZC preamble + sync/CFO control path
- `G2 Robust`: D-CSS fallback mode
- `G3 Turbo`: legacy transport OFDM mode (`QPSK`, `16QAM`, `64QAM experimental`)
- Legacy transport-PHY constants (distinct from the NFFT-2048 bulk PHY above):
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
swift test                    # portable KISS-FFT default (Android-capable; JNI pending)
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
- **Historical 1.x M4 settings:** Mac output 100%, Mac input ~22/100 (clips
  above), phone media volume max. A historical good geometry placed the phone
  face-down on soft cloth over the function-key area, charge port toward the
  Mac speakers (historical 38.4 kbps console aggregate to 46.915 kbps
  post-sounding ordered PHY payload rate, depending on the profile and evidence
  contract).
- **Pixel Cyrinx 2.0 evidence envelope:** MacBook built-in left speaker only,
  output 50%, waveform peak 0.18, Pixel face-up on 0.5-inch soft cloth above
  the left function-key area, bottom mic near the speaker, and 48 kHz stereo
  `UNPROCESSED` capture. These are retained route qualifications, not universal
  defaults or an acoustic-exposure rating. Re-derive them after any device,
  pose, route, room, or level change.

  Overhanging the mic into the keyboard well is reverberant (the MRC-carried
  11.366 kbps cell); the desk plane below a laptop stand is shadowed — the
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
