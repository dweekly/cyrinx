# Changelog

Validated, backward-looking milestones — what was built, measured, and proven,
by date. Forward-looking work lives in [ROADMAP.md](ROADMAP.md). The repository
has public v1.0.0 and v2.0.0 releases; intervening research milestones remain
date-labelled.
Detailed evidence for every claim is in the cited document and its reproduction
commands or content hashes.

## Unreleased — C3-03..05 reconstruction: versioned ABI, profile registry, batch contract

- Reconstructed the rejected `c3-02-05-batch-demod` branch work fresh against
  the reviewed contracts ([docs/reviews/C3_03_05_RECONSTRUCTION.md](docs/reviews/C3_03_05_RECONSTRUCTION.md)):
  a fixed-width versioned ABI base (`cyrinx_base.h`), a profile registry with
  SHA-256 wire identity over a domain-separated canonical serialization
  proved against a checked-in JSON source (`cyrinx_profiles.h`), and a batch
  capture contract with explicit strided channel views and caller-provided
  block validity (`cyrinx_batch.h`). Digital loopback and boundary coverage
  only; no OTA, streaming, or on-device claims. The new C surface is
  classified `experimental` in the API inventory until the C3-05 review
  freezes the v1 layouts.

## Unreleased — documentation and claim-boundary corrections

- Replaced the unqualified C-ABI stability claim with the actual boundary: a
  public 2.x API, a versioned receiver contract, and an active ABI migration.
- Quarantined the optional Apple/Android HIL crypto envelope as an unaudited
  interoperability prototype. The implementation record now identifies the
  custom SHA-256 XOR stream, eight-byte sequence, eight-byte truncated HMAC,
  unauthenticated key exchange, and missing receive-side replay protection.
- Classified the headline Cyrinx 2 measurements as integrity-record evidence,
  not public replay, because the full raw captures, manifests, and frozen
  binaries are absent from the checkout. New comparative headline claims now
  require their replay bundle and command in the same change.
- Updated the Cyrinx 3 status ledger and research roadmap so branch-only work,
  simulator-only chat, stopped Rank 8 spikes, and the non-identifiable Rank 11
  audit are not presented as merged or active capabilities.
- Marked `walkthrough.md` as a historical legacy-HIL narrative and removed its
  current-product wording.
- Added a mechanical claims gate to prevent recurrence of the corrected
  compatibility, security, and legacy-HIL phrases.

## v2.0.0 — 2026-07-17 — Cyrinx 2.0 Pixel receiver and goodput campaign

- **Swift Format advanced to 603.0.0.** The strict `Sources`/`Tests` formatter
  pass remains clean under the current release. The repository keeps an exact
  formatter version because formatter output is not stable across releases; the
  pin is advanced deliberately rather than retaining 602.0.0 as a compatibility
  dependency.
- **The separate Cyrinx 2.0 follow-on whitepaper is published in-tree.** The
  11-page source/PDF records the 65.875 kbps same-schedule flagship, failed
  reliability gate, +347-block same-capture receiver replay, separately classed
  69.652 kbps zero-gap result, rejected branches, calibration limitations,
  ultrasonic asymmetry, implementation drift, and evidence-availability gap.
  The 28-page Cyrinx 1.0 paper remains a separate historical record.
- **Cyrinx 2.0 reached 65.875 kbps in the accepted Pixel schedule class.**
  The prospective Mac-to-Pixel campaign used five 64-symbol frames with four
  250 ms gaps, CP 96, pilot spacing 16, 64-QAM rate 2/3, 48 kHz stereo
  `UNPROCESSED` capture, Mac volume 50%, and waveform peak 0.18. All 16 runs
  completed; the candidate won 8/8 pairs (`p = 1/256`) and recovered
  4,215/4,280 ordered byte-identical blocks (98.481%). The paired 16-QAM
  control recovered 2,999/3,000 (99.967%), so the predeclared
  baseline-equivalent resilience gate failed. The result is 1.801× the
  accepted 36.571 kbps number, not almost tripled.
- **A separate zero-gap profile measured 69.652 kbps mean.** CP 96, pilot
  spacing 64, 64-QAM rate 2/3, and 96-symbol frames recovered 6,129/6,760
  blocks (90.666%), range 65.731–72.641 kbps, gross mean 68.636 kbps. The
  paired baseline recovered 4,509/4,520 (99.757%); all 16 runs completed and
  the candidate again won 8/8 (`p = 1/256`), but the resilience gate failed.
  This `five-frame-ota-sr48000-gap0-samples` class is not directly equivalent
  to the accepted 250 ms-gap schedule. Its mean is 1.9045×, or 90.45% above,
  36.571 kbps. Evidence: plan SHA-256 `8e5f1062dcccc42bbfac866a8659bf8d1884dd54bb054a9f42d944a1919e966b`,
  completed manifest SHA-256 `9846a33717c18e457169578217dbbdabe740eaa26c09965cb24ce128c9d1e462`,
  frozen decoder SHA-256 `c6e4e5adc0865a379dc036c549cef4cd91528152ffffa66ee2fdf9be47580956`.
- **The C receiver gained frequency-local known-pilot LLR reliability.** After
  affine pilot phase correction, an endpoint-replicated 11-pilot boxcar is
  linearly interpolated onto data bins and blended 25% global / 75% local with
  sync noise. Nonfinite observations become low-confidence erasures. A fixed
  72-byte receiver-contract ABI and deterministic edge/nonfinite tests pin the
  semantics for mono and MRC.
- **Fresh same-capture replay isolated the receiver contribution.** The local
  receiver recovered 4,215/4,280 candidate blocks versus 3,868/4,280 for the
  frozen global-only decoder: +347 blocks, all eight runs improved, none
  regressed. Payload bytes, CRC results, decoded bits, and Viterbi metrics are
  forbidden reliability inputs.
- **Pilot-only automatic diversity now ships in C.** Even pilot ordinals train
  the phase model; odd ordinals score primary versus MRC before demapping. The
  Pixel zero-gap confirmatory campaign selected MRC in 26/40 candidate frames
  and mic0 in 14/40, without payload or CRC leakage. Swift remains a thin C binding;
  Kotlin remains a legacy control decoder; Python is the research oracle and
  independent referee.
- **The campaign path was hardened before playback.** Dry and execute plans
  bind the target/APK/route/calibration, five Python implementation hashes,
  canonical C/KISS inputs, runtime receiver contract, behavioral challenges,
  integral-sample schedule, balanced order, and every planned failure slot.
  The decoder is built from retained source snapshots, content-addressed,
  read-only, and rehashed before/after playback. A separate comparator verifies
  capture/timing/payload identity for legacy/current replay.
- **Longer frames exposed a resilience limit rather than a free speedup.** A
  128-symbol zero-gap campaign reached 66.102 kbps but only 7,662/8,600 blocks
  (89.093%). Across separate sessions and configurations, that observed mean
  was only 0.34% above the fresh 64-symbol/250 ms-gap result; this is not a
  causal estimate of frame length or gap. Pilot residuals and CRC recovery
  degraded with session time while timing, capture level, and sync-derived SNR
  remained healthy. Naive fixed-comb pilot interpolation did not generalize to
  data bins, so a tracker was not shipped.
- **Additional full campaigns mapped the throughput/resilience frontier.**
  Pilot-16/64-symbol zero-gap measured 69.110 kbps at 97.009% block success;
  pilot-32 measured 68.960 kbps at 93.311%. A 600 Hz lower-band extension was
  rejected after its first predeclared screen recovered only 60/110 candidate
  blocks (39.948 kbps) versus a 77/77 baseline. Earlier CP 48, Pixel 64-QAM
  rate 3/4, and rate 5/6 screens also remained rejected.
- **Route-specific Swift factories replace a universal-fast-profile fiction.**
  `makeMotoG2026NearFieldHighGoodputProfile` pins the retained 46.915 kbps
  Moto CP240/p8/16-QAM/r3/4 geometry;
  `makePixel7aNearFieldFlagshipProfile` pins the schedule-comparable Pixel
  pilot-16/rate-2/3/64-symbol geometry, while
  `makePixel7aNearFieldPeakGoodputProfile` pins the separate zero-gap
  pilot-64/rate-2/3/96-symbol geometry. The old `makeCyrinx2Fast` spelling
  remains only as a deprecated evidence-limited CP96/rate-5/6 candidate. A
  recovered unversioned bench narrative reports 83.708 kbps, 5,356/5,360
  blocks, and a failed baseline-equivalent reliability gate, but the named
  ignored manifest and raw bundle are missing. It is recorded in the historical
  correction ledger, not promoted as a durable public benchmark. Inter-frame
  scheduling, automatic diversity input, and acoustic level qualification stay
  outside the PHY configuration.

## 2026-07-08

- **OTA re-validation of the diversity stack (A1 step 4) — the paper's
  robustness claims now demonstrated end-to-end in the live loop** (PR #52;
  docs/EXPERIMENTS.md 2026-07-06→08 table). Clean cell reproduced exactly
  (46.915 kbps post-sounding ordered PHY payload rate, 225/225, MRC never
  invoked — identical pre/post a Pixel OS
  update). Three defects invisible to digital calibration were found and
  fixed OTA: RS-floor false-erasure budget burn (first OTA contact decoded
  0/6 frames; errors-only decode now runs first → 9/9 at 138 bps, ×2.03 the
  June repetition floor); the floor gained a stereo tone-energy combining
  rung (non-coherent MRC analog, never worse than the best mic on all saved
  captures); and the sounder's single-mic EVM probe couldn't see MRC
  potential — it now probes library-MRC EVM (`via_mrc` tiers) with one 2×-CP
  retry before conceding to the floor. That retry was available but not
  invoked in the reverberant-overhang result:
  **11.366 kbps post-sounding QPSK PHY payload rate with 0/75 blocks decodable
  on either mic alone and 75/75
  MRC-rescued** (82× the floor it previously accepted). New diagnostic
  spike: `scratch/hw20k/mfsk_diag.py`.
- **swift-format gate green** (issue #25, PR #51): pinned 602.0.0, 890 → 0
  violations, 83/83 tests, formatting-only reformat.
- **Pre-public secret/PII sweep: CLEAR TO PUBLISH** (gitleaks + independent
  greps + per-blob strings over full history, all branches; only benign
  path-leak notes).
- **Whitepaper 25 → 26 pp:** §Robustness gains the OTA re-validation
  paragraph (the measured RS-floor number replaces "pending"; the live-loop
  MRC demonstration; the three digital-blind defects as a methods lesson).
  Site ladder/copy updated to match (138 bps floor, 75/75 MRC, 26 pp).
- **Site deployed to Cloudflare Pages** (`cyrinx.pages.dev`; custom-domain
  binding to cyrinx.org pending a dashboard action).

## 2026-07-02

- **Whitepaper revised (22 → 25 pp)** for flow, approachability, and the
  benchmark framing: restructured three-paragraph abstract; de-nested
  introduction; new frame-anatomy spectrogram figure generated from the
  reference modem (`scripts/gen-paper-figures.py`); "Building on this work"
  extension-points subsection; MRC-in-C-library and RS-floor results
  integrated with explicit measured-vs-digital labeling; and a new section
  "AI-Agent Development, and the Project as a Capabilities Benchmark" — a
  commit-trailer-documented era table (GPT-5.5 Feb–May: <0.3 kbps measured
  vs 20+ kbps claimed; Fable 5 Jun 9–10: 36.6/27.3 kbps; Opus 4.8 Jun
  10–12: 38.4 kbps payload-normalized historical console aggregate
  (historically reported as a 39.3 kbps coded-information rate) + robustness;
  Fable 5 Jul: library
  diversity + site), the verification-discipline analysis, five properties
  that make the task a hard-to-game agent benchmark, a scoring protocol
  (goodput delta + claims-integrity audit), and the honest limits of the
  n=1 comparison. Builds clean on BasicTeX via `lmodern`.
- **cyrinx.org website built** (Track C; deploy pending user auth on
  Cloudflare). Static, dependency-free single page in `site/`: the hero
  synthesizes a deterministic control-profile geometry illustration in-browser
  (chirp, guard, random-QPSK OFDM at 1.1–23 kHz and NFFT 2048/CP 768), renders
  its spectrogram on canvas, and plays it via WebAudio on click; it does not
  implement sync, pilots, payload mapping, FEC, or CRC. The interactive
  16-QAM/EVM constellation demonstrating the measured 64-QAM ceiling;
  graceful-degradation ladder; measured-results table with the honest goodput
  definition; prior-art section; whitepaper PDF. OpenGraph card generated
  from a real frame spectrogram (`scripts/gen-site-assets.py`); JSON-LD,
  sitemap, robots, cache/security headers. Rendering validated desktop +
  mobile via Chrome DevTools (clean console).
- **A2: two-mic MRC ported into the shipped C codec** (digital validation
  complete; OTA pending a bench). `cyrinx_bulk_demodulate2` implements
  per-subcarrier maximal-ratio combining exactly as the validated Python
  reference (`modem.demodulate_frame(rx2=...)`): identical payloads/block
  counts and EVM equal to 4 decimals on the same captures across the MCS/CP
  grid, including the null-fill rescue where mic0 alone decodes 0/N and MRC
  decodes N/N. Pinned by a new committed golden case (`qam16_r34_mrc`:
  complementary per-mic dead bands over distinct multipath, mic0-alone failure
  recorded in the manifest) on both KISS and vDSP FFT backends. Swift surface:
  `BulkPHY.decode(_:combining:)`. The adaptive loop's MRC escalation is now
  library-native end-to-end, and its per-rep accounting was fixed to
  per-block ordered verification (the old all-or-nothing payload check gave
  partial decodes zero credit). The single-mic path is bit-identical to
  before. 83/83 Swift tests + 23/23 Accelerate-backend tests green.

## 2026-07-01

- **A3 MFSK-floor FEC upgrade, digital complete** (OTA re-measurement pending
  a bench): Reed–Solomon RS(15,11) over GF(16) with detector-confidence
  erasures (`scratch/hw20k/rs16.py`) replaces the 3× repetition + majority
  vote in `mfsk.py`. One RS code symbol = one nibble = one 16-FSK tone
  decision; codewords block-interleaved across OTA symbols. **×2.06 net rate
  at the same symbol duration** (32 B frames: 68 → 138 bps by the airtime
  formula) and **more robust at the low-SNR edge**, not less (40 ms reverb,
  20 trials: at −3 dB SNR repetition decoded 5/20, RS 18/20 — the erasure
  flags exploit confidence the majority vote discarded). The whitepaper's
  measured 68 bps floor figure describes the repetition implementation.
- **A1 auto-MRC, digital steps complete** (steps 1–3 + 5 of
  [docs/A1_AUTO_MRC.md](docs/A1_AUTO_MRC.md)):
  - `scratch/hw20k/xcompat_validate.py` **proved the cross-compat hypothesis**:
    frames encoded by the library C codec decode through the Python reference
    RX (`modem.demodulate_frame`), mono and two-mic MRC, across the full
    MCS/CP grid the adaptive loop emits — including the null-fill case
    (complementary per-mic spectral notches: mic0 alone 0/N blocks, MRC N/N).
  - `clib.modem_cfg_from_clib()` bridges clib Cfg → `modem.Config` with a
    geometry-parity assertion between the two implementations.
  - `adaptive.py` coherent decode now escalates **clib-first → two-mic MRC**
    when the library single-mic decode is imperfect, with per-rep provenance
    (`clib_verified`, `mrc_rescued_blocks`, `decode_path`) in the JSONL;
    goodput accounting unchanged. Covered by a new offline
    `adaptive.py selftest`.
  - Remaining for A1: step 4, OTA re-validation across the orientation set
    (needs a bench).
- **PR #43 merged:** A1 auto-MRC execution plan recorded as
  [docs/A1_AUTO_MRC.md](docs/A1_AUTO_MRC.md) (docs only; implementation pending).
- **Environment reproduced on a second Mac (M1 Max MacBook Pro):** full Swift
  suite (80/80) and all `scratch/hw20k` offline selftests + the clib digital
  loopback pass. All prior *measured OTA numbers* remain M4-MacBook-Pro-specific;
  bench gain-staging constants do not transfer across Macs.
- Docs consolidated: `CHANGELOG.md` added; `ROADMAP.md` rewritten as the single
  forward-looking plan; `docs/NEXT_SESSION.md` folded into the two and removed.

## 2026-06-12 — Robustness/diversity layer, validated over the air

The link now degrades gracefully across placements — measured
**46.915 kbps post-sounding PHY payload rate (clean) → 11.366 kbps
post-sounding PHY payload rate (reverberant, rescued) → 68 bps (shadowed) →
never zero** where a naive receiver collapsed. A separate 5.154 kbps MRC probe
had CRC-only validation and no retained byte oracle; it was not a closed-loop
rung. Whitepaper folded it all in (22 pp). PRs #38–#42.

- **EVM-probe sounder** replaces repeated-pilot SNR estimation for MCS selection
  (fixes the systematic under-call; see EXPERIMENTS.md finding 3).
- **Distance sweep finding:** mic-position-over-keyboard, *not* distance, is the
  dominant axis on this bench; the best cell contributed to the historical
  38.4 kbps payload-normalized console aggregate.
- **Non-coherent MFSK floor modem** (`mfsk.py`): 68 bps floor that decodes at
  delay spreads where coherent CP-OFDM fails outright.
- **Decode-based mic selection + adaptive cyclic prefix** rescue reverberant
  high-SNR cells (RMS-loudness mic selection was picking the wrong mic).
- **Two-mic maximal-ratio combining validated OTA** (ROADMAP #15 at the time):
  combining beats selection; at `edge_below_laptop` MRC decoded 8/11 blocks
  where *each mic alone decoded 0/11* (`scratch/hw20k/mrc_validate.py`).
- Known gap at close: the robustness layer lives in the Python reference /
  bench loop, not yet in the shipped C codec (tracked as Track A in ROADMAP.md).

## 2026-06-11 — Library-native OTA exceeds the harness headline

First live bench session with the experiment harness (PRs #36–#37,
docs/EXPERIMENTS.md).

- **38.400 kbps payload-normalized library-native OTA aggregate** (16-QAM r¾;
  contemporaneous console output reported 375/375 across five independent
  one-frame trials):
  the shipped C codec (`libcyrinxbulk`) decoding over the air, exceeding the
  36.6 kbps Python-harness headline and retiring the paper's earlier
  "library only hit 12.8 kbps OTA" caveat.
  Earlier project material labeled this 39.3 kbps by counting CRC and fill in
  the numerator. Conditional on the console aggregate, five times 19,200
  payload bytes over five times 4 s is 38.400 kbps under the current metric.
  No matching machine-readable run record or raw capture was retained; the
  tracked 150/150 env-sweep row is a different run.
- **64-QAM r3/4 ceiling in this June cell:** 64-QAM r3/4 decoded 0/339 at
  EVM 0.173 despite 52 dB raw channel SNR — that profile was bounded by the
  effective-SINR/EVM floor (~15 dB), not raw channel SNR. This was not a
  universal constellation limit: the later Pixel Cyrinx 2.0 rate-2/3 profile
  closed at high goodput, with a measured resilience tradeoff.
- Adaptive-sounder repeated-pilot SNR estimation shown unreliable for MCS
  selection (pessimistic); led to the EVM-probe sounder (2026-06-12).
- Measured channel-response figure grid + whitepaper revision (19 → 21 pp).

## 2026-06-10 — Portable-C port, iOS parity, publication groundwork

The publication effort's Phase 0/1 (PRs #23–#34) plus the iOS HIL merge.

- **Portable C bulk-PHY codec** (`cyrinx_bulk`: DetRng, CRC-32, K=7
  convolutional FEC + puncturing, Gray QAM, OFDM over vendored KISS FFT behind
  the `cyrinx_fft` plan interface, soft Viterbi) — TX *and* RX — validated
  bit-exact / float-tolerant against committed golden vectors; Swift `BulkPHY`
  binding round-trips in pure Swift. vDSP/Accelerate FFT backend behind the same
  interface, validated against the same vectors.
- **First library-native OTA result:** 12.8 kbps QPSK r½, byte-verified, via
  `libcyrinxbulk` + `clib.py` (coupling-limited, not codec-limited — superseded
  2026-06-11).
- **iPhone 17 Pro Max parity** (docs/IOS_HIL.md): **36.57 kbps Mac→iPhone**
  (decoded on-device by `BulkDemod.swift`, bit-compatible with the Python
  reference) and **16.87 kbps iPhone→Mac** (iPhone speaker is band-limited to
  ≈11 kHz usable coherent band). `devicectl`-based device control (the iOS
  analog of `adb am`).
- **Ultrasonic-band investigation** (docs/ULTRASONIC_BAND.md): inaudible 96 kHz
  variant; iPhone speaker found phase-incoherent in the ultrasonic band.
- Repositioning-guidance API; crypto cost/security tradeoff analysis
  (docs/CRYPTO_TRADEOFF.md); adaptive sounder + MCS ladder (first iteration).
- **docs/NEGATIVE_FINDINGS.md** established: durable record of dead ends and
  disproved hypotheses.
- First whitepaper draft merged (17 pp).

## 2026-06-09 — Measured wideband bulk PHY (the headline result)

Merge of `acoustic-20kbps` (docs/ACOUSTIC_BULK_PHY.md).

- **36.6 kbps Mac→Pixel 7a** (decoded on-device by `BulkDemod.kt`) and
  **27.3 kbps Pixel→Mac**, historical expected-set byte-verified over-the-air
  metrics on a MacBook Pro M4, audible band, palm-rest geometry. The verifier
  did not retain decoded-record uniqueness, strict stream order, or scheduled
  slot attribution.
- Four physical-layer defects diagnosed and cataloged; diagnostic methodology
  and platform gotchas written up.
- README reframed as a research prototype with explicit originality and
  limitations sections; PRD-vs-as-built analysis (docs/PRD_VS_AS_BUILT.md).
- External project review recorded; its priorities drove the June work (see the
  review-feedback appendix in ROADMAP.md for per-item status).

## 2026-05-22 → 2026-05-24 — Handshake & sensing experiments

- 2×2 MIMO acoustic handshake experiment, channel-capacity broadcast, THD
  characterization.
- 10-byte capabilities handshake; transducer-calibration EQ curves; programmatic
  acoustic gain staging.
- Dynamic background room-tone noise notcher; closed-loop handshake.
- Curve25519 ECDH key exchange + CTR/HMAC envelope (experimental, unaudited —
  see SECURITY.md).
  - Correction (2026-08-27): the implementation uses a custom SHA-256-derived
    XOR stream and truncated HMAC, not a standard CTR construction.

## 2026-02-12 → 2026-05-08 — Ultrasonic transport stack (original strand)

- C core (`CCyrinx`) with stable ABI + Swift wrapper; bit-packed frame codec
  (CRC16/CRC32C), fragmentation/reassembly; half-duplex ping-pong MAC with ACK +
  selective retransmission; ARC gear state machine; stream-multiplexed transport
  API; in-memory linked transport for deterministic tests.
  - Correction (2026-08-27): this historical bullet overstated the boundary.
    The repository has a public 2.x API and versioned receiver contract, but the
    general ABI was not frozen.
- Apple audio scaffolds (RemoteIO iOS / AVAudioEngine macOS); Android HIL app
  with ADB automation; raw tone codecs (OOK/nibble/DTMF/Morse); vDSP OFDM-QPSK
  and D-CSS modulators; acoustic PHY bridge with dual-ZC sync.
- Net result: functional but slow as measured — **<0.3 kbps OTA** in the
  18.5–23.5 kHz band. This ceiling motivated the audible-band bulk PHY.
