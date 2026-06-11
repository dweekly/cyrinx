# Cyrinx Publication — Work Journal

Fresh as of 2026-06-10. A running, dated log of the work to publish Cyrinx
(portable-C bulk PHY → public Apache-2.0 library + cyrinx.org + arXiv paper).
This is **raw material for the Phase 3 paper update** — decisions, measurements,
surprises, dead ends, and the reasoning behind them, captured while fresh.

Cited from [PUBLICATION.md](PUBLICATION.md). Durable negative results graduate
to [NEGATIVE_FINDINGS.md](NEGATIVE_FINDINGS.md); positive measured results to the
subsystem docs. This journal is the narrative connective tissue between them.

Convention: newest entries at the top within each day; each entry tagged with
the plan stage (e.g. `[1.1]`, `[0.2]`) it belongs to.

---

## 2026-06-10

### `[plan]` Architecture & scope decisions locked
- Library scope: port the wideband bulk PHY into a **portable C core** so the
  library itself delivers the 36.6 / 27.3 kbps headline (not just the Python
  harness). Rationale: "broadly-useful library" — C is the single source of
  truth; Swift/vDSP is an Apple acceleration backend; Android binds via JNI.
- License **Apache-2.0**; website **cyrinx.org** (Cloudflare Pages, registered
  today); paper to **arXiv** (cs.NI / eess.SP) + PDF on site.
- Crypto (X25519/CTR/HMAC) is an **opt-in layer, OFF by default**, with a
  documented cost/security tradeoff table (overhead as % of goodput per MCS
  tier — near-free at 36 kbps, dominant at the 267 bps MFSK floor). Apps choose
  bandwidth-vs-security per observed channel quality.
- Added a consumer-facing **repositioning-guidance API** (plan 1.4b): turn
  sounder metrics into actionable user hints ("move closer", "soft surface",
  "point the bottom edge at the speaker", "too loud").
- De-risking fact: the bulk PHY already exists working in three reference
  implementations (Python `modem.py`, Swift `BulkDemod.swift`,
  Kotlin `BulkDemod.kt`) — the C core is a *validated port*, not a new design.

### `[0]` Phase 0 hygiene (branch `publication/groundwork`, PR pending)
- Added `LICENSE` (canonical Apache-2.0, fetched from apache.org) + `NOTICE`
  (Primatech Paper Co LLC). README gains a License section.
- Issue board mapped: label `publication-v1`; existing #4–#8 cross-referenced to
  plan stages; new #9–#22 created for Phase 0, PRs 1.1–1.10, paper, site, release.
- Junk scrub: removed compiled `TestFFT.class`; `.gitignore` now excludes
  `*.class/*.dex/*.o/*.a`.
- Kept (did **not** delete) the FFT/OFDM parity spikes — they're material for
  the PR 1.1 FFT-parity work. Organized the four FFT spikes under `scratch/fft/`
  with a README (vDSP vs Cooley-Tukey vs Java bit-reversal references). These
  pin the sign/scaling/bit-reversal conventions the portable-C FFT must match.
- Added `CONTRIBUTING.md`, `SECURITY.md` (documents the opt-in/OFF-by-default
  crypto stance + acoustic-broadcast threat note), and `.github/` issue + PR
  templates (PR template enforces measured-result + golden-vector checkboxes).
- Deferred to follow-ups (kept #9 open): per-file SPDX header sweep (scripted,
  low-value churn now); stale branch/worktree pruning; full-history secret/PII
  sweep happens at the Phase 5 public-flip gate.

### `[1.1]` Golden-vector contract — landed (branch `publication/golden-vectors`)
- **FFT strategy decided** (answer to "KISS first, then optimize?"): yes —
  KISS FFT as the portable correctness reference behind a thin internal FFT-plan
  interface (`create/forward/inverse/destroy`); per-arch optimized backends
  (vDSP/Accelerate on Apple, PFFFT/NEON on ARM, AVX on x86) added later, each
  gated by these golden vectors. What makes the fast version cheap to reach,
  baked into the C structure now: fixed compile-time size (2048) + precomputed
  twiddles in a plan struct (no per-call alloc); **real-input transform** (rfft —
  half the work, biggest single win); structure-of-arrays + 32-byte alignment +
  `restrict` for auto-vectorization; **no global `-ffast-math`** (it reorders FP
  ops → breaks cross-platform determinism AND the golden float tolerances; scope
  it only to an explicitly-tolerant fast backend); `-O2` + LTO.
- **Instrument, don't reimplement.** Added an optional `taps` dict to
  `modem.py:modulate_frame` — populated only when passed, zero behavior change
  (modem selftest still 4/4 + 14/14). Records every stage + DetRng-derived
  artifacts (interleave perm, pilots, sync, PRBS pads).
- **Emitter** `scratch/hw20k/golden_vectors.py` (`emit`/`verify`) writes small
  canonical fixtures to `Tests/Fixtures/golden/` with a manifest carrying
  per-artifact dtype/shape/SHA-256/tolerance. Two cases (qpsk_r12, qam16_r34) at
  real params (NFFT 2048, CP 768), smallest n_sym that still carries one CRC
  block — every code path, tiny frame.
- **Tiered tolerance**: exact (byte-for-byte) for the deterministic integer
  pipeline + decoded payload; float (1e-5 abs, float32 reference) for FFT-derived
  values. Reference stored as **float32** — matches the real TX dtype and halved
  the fixtures from 1.2 MB → **664 KB**. Dropped redundant `ofdm_time_norm`
  (= `wave` tail) and debug-only `ofdm_time_raw` (kept in taps).
- **Library-side lock**: `Tests/CyrinxTests/GoldenVectors.swift` (loader; locates
  fixtures via `#filePath` so the C rig shares the path) + 4 passing tests
  (manifest loads, SHA-256 integrity, round-trip target self-consistent, exact-
  stage shapes). Full suite green (48 tests).
- **Next (1.2)**: C TX path against these vectors — DetRng, conv-encode+puncture,
  interleaver, Gray QAM map, OFDM mod — bit-exact on integer stages, float-tol on
  the wave. The Swift `GoldenVectors` typed accessors are ready for it.

### `[1.1]` Review response (PR #24 feedback)
Five precise findings, all addressed:
1. **RX contract was missing.** Added an `rx_wave` artifact per case = the TX
   `wave` through a fixed deterministic multipath channel (`RX_CHANNEL_TAPS`,
   echoes within the CP, no noise), decoded to prove it reproduces the payload.
   So 1.3 has a stable receive fixture exercising chirp/fine sync, LS channel
   est, pilot tracking, LLR, Viterbi — and a *non-unity* channel so a broken
   equalizer can't pass. Decode metadata (`decode_blocks_ok/_total`) recorded.
2. **Fill branch wasn't exercised** (both cases had `pad_fill=0`). Added a rate
   2/3 case (`qpsk_r23`) → `pad_fill=1`. Probed all rate×n_sym combos: pad_fill
   is **structurally ≤1 bit** (the modem sizes payload to fill capacity), so 1
   bit is the max; `emit` now *asserts* at least one case has non-empty fill.
3. **No C-side rig.** Added `Tests/CGoldenVectors` (test-support C target):
   `cyrinx_golden.{h,c}` (blob reader + size-verify) consuming a generated
   `golden_manifest.h` (emitted by `golden_vectors.py`), exercised by a Swift
   test that diffs C-read vs Swift-read bytes. Minimal, no JSON-in-C.
4. **Fragile typed loaders.** Rewrote the Swift Int64/Float accessors to use
   unaligned little-endian loads (`loadUnaligned` + `littleEndian:`), never
   `bindMemory` (which assumes alignment + host endianness).
5. **PUBLICATION.md 0.2 wording** (on #23) corrected to "move/organize parity
   spikes," matching the kept-not-deleted decision.

Now 3 cases, 16 artifacts each, **~1.3 MB**, full suite green (52 tests incl. 8
golden + the C loader). #23 + #24 merged to main.

### `[1.2]` C TX path — deterministic integer core (branch `publication/c-tx-path`)
First increment: the FFT-independent deterministic TX primitives in portable C
(`Sources/CCyrinx/cyrinx_bulk.{c,h}`), each validated **bit-exact** against the
golden vectors via `CyrinxBulkTXTests` (uses a golden intermediate as the input
to the next stage, so a failure pinpoints the diverging stage):
- `DetRng` splitmix64 (+ `permutation`/`bits`/`bytes`), `prbs_bits`;
- IEEE **CRC-32** (zlib, poly 0xEDB88320) — explicitly NOT the core's CRC-32C;
- conv-encode K=7 (171,133) + 6 tail bits; puncture (1/2, 2/3, 3/4, 5/6);
- the interleaver permutation, validated against `interleave_perm`.
- **Bug the vectors caught:** wrote the generator polynomial as C `0121` (octal
  121 = 81) instead of `0171` (octal 171 = 121) — a classic Python-`0o171`→C
  octal slip. conv_encode produced plausible-but-wrong codes; `coded_bits.bin`
  flagged it immediately. Exactly why 1.1 lands before any DSP.
- `testDetRngStream` reimplements splitmix64 independently in Swift so the C
  isn't merely compared against itself.
58 tests green. **Next:** Gray QAM map → `data_freq` (float-tol), then vendor
KISS FFT behind the FFT-plan interface for OFDM mod → `wave`.

### `[1.2]` C TX path — COMPLETE (QAM + KISS FFT + full frame)
The portable-C transmitter is now end-to-end validated against the golden
vectors (all 3 cases: QPSK r1/2, 16-QAM r3/4, QPSK r2/3):
- **Gray QAM map** (BPSK/QPSK/16/64) via the inverse-Gray PAM levels.
- **KISS FFT vendored** (BSD-3, `Sources/CCyrinx/kissfft/`, NOTICE updated),
  compiled `-Dkiss_fft_scalar=double` so the C reference matches the numpy
  oracle to ~1e-9. Wrapped behind `cyrinx_fft.h` (`cyrinx_irfft_*`) — the
  FFT-plan interface a vDSP/NEON backend can later replace (PR 1.6). numpy.irfft
  is 1/N-normalized; KISS is unnormalized, so the wrapper divides by nfft.
- **Full orchestration** `cyrinx_bulk_modulate` mirrors `modulate_frame`:
  geometry → blocks+CRC → info bits (+seed-7 pad) → conv+puncture (+seed-8 fill)
  → interleave → pilots/sync/QAM per symbol → IFFT+CP → std-clip + peak-normalize
  → chirp+GUARD+data. `data_freq` (pre-IFFT) and `wave` (final) both within the
  1e-5 float tolerance; integer stages bit-exact.
- Config recorded in the manifest (`f_lo/f_hi/pilot_every/bits_per_bin_uniform/
  chirp_*/amp/clip_sigma`) so the test reconstructs the exact generating config.
- Known wart: KISS's `kiss_fft_log.h` `#define DEBUG 4` warns against the debug
  build's `-DDEBUG=1` (harmless; vendored file, left unmodified).
59 tests green. **Next (PR 1.3):** the C RX path — chirp sync, LS channel est,
pilot tracking, LLR, Viterbi — decoding `rx_wave` to `decoded_payload`.

### `[1.3]` C RX path — COMPLETE, decodes on the first run
The portable-C **receiver** decodes each case's `rx_wave` (TX through the fixed
multipath channel) to the exact `decoded_payload`, all blocks CRC-valid, across
all 3 cases — **passed first try**, a strong signal the golden-vector contract
pinned the TX correctly. Implemented (single-mic, track_alpha=0 path):
- forward real FFT added to `cyrinx_fft` (`cyrinx_rfft_*`, numpy.rfft semantics);
- chirp matched-filter coarse sync + sync-symbol fine sync (−24-sample early bias);
- LS channel estimation from the 2 sync symbols, 9-tap box noise variance,
  per-bin SNR; **no cross-bin H smoothing** (negative finding #2);
- per-symbol 3-pass pilot phase-slope + CPE tracking; pilot-EVM² LLR weighting;
- max-log QAM LLR (inverse-Gray min-distance); deinterleave; depuncture into the
  rate-1/2 stream; soft Viterbi (trellis traceback); CRC-32 per block.
- Decision: **bundle TX+RX into PR #26** — a codec's TX without RX is a
  half-feature (can't validate a round trip); they share the module and the
  golden harness. Closes #11 (1.2) and #12 (1.3).
60 tests green. The portable-C bulk PHY is functionally complete (uniform
bit-loading). **Next:** Apple vDSP FFT backend (1.6) and/or the adaptive sounder
+ repositioning-guidance API (1.4/1.4b); MRC for stereo (1.5).

### `[1.7]` Swift binding — `BulkPHY` (library surface over the C codec)
`Sources/Cyrinx/BulkPHY.swift`: an ergonomic Swift API over the C codec — no DSP
here, just config marshalling. `BulkPHY.Configuration` (defaults to the measured
16-QAM r3/4 near-field profile), `geometry()`, `encode(Data) -> [Float]`,
`decode([Float]) -> Decoded` (payload + per-block CRC + EVM, `isComplete`).
Pure-Swift encode→loopback→decode round trips for QPSK/16-QAM/64-QAM all recover
the payload with every block valid; wrong-length payloads are rejected. The
**Swift library now delivers the bulk codec end-to-end** (digitally); OTA parity
(1.10) needs hardware. 64 tests green. (Retiring the HIL `BulkDemod` forks in
favor of this binding is the remaining part of 1.7.)

### `[1.4b]` Repositioning-guidance API — built and tested
`cyrinx_guidance.{c,h}` + Swift `RepositioningGuidance.swift`: a pure function
mapping measured channel metrics → one actionable hint with severity + evidence.
Rules (priority order, thresholds documented from the measured findings):
clipping (peak≥0.98) → lower volume; ultrasonic + coherence<0.3 → use audible
(NEGATIVE_FINDINGS #9); delay spread > 2×CP → soft surface (#13); low SNR + weak
peak → move closer; marginal SNR → move closer (advisory); HF roll-off < −10 dB →
aim the phone's bottom edge. Swift surface: `repositioningAdvice(for: ChannelMetrics)
-> RepositioningAdvice` with a localizable `.text`. 9 unit tests over each
pathology + priority order. The sounder (1.4) will populate the metrics from a
real sounding; the function is independently testable now. 73 tests green.

### `[1.10]` Library-native OTA — DEMONSTRATED (Mac→Pixel, C codec both ends)
The Pixel 7a was reattached, so I ran the **actual library C code** over the air
(not the Python reference): `scratch/hw20k/clib.py` ctypes-loads
`libcyrinxbulk.dylib` (compiled from `Sources/CCyrinx/cyrinx_bulk.c` +
`cyrinx_fft.c` + kissfft); `ota_clib.py` C-encodes a frame on the Mac, plays it
over the speaker, the Pixel records, and the **C codec decodes the capture**.
- **Fully byte-verified: 12.8 kbps QPSK r1/2, 25/25 blocks, 3/3 frames** — a
  genuine library-native OTA result (the library itself, not the harness, hits
  measured goodput).
- Ladder at the current coupling (peak ~0.08, EVM ~0.30 ≈ 10–11 dB SINR):
  16-QAM r1/2 → 40–49/50 blocks (~20–25 kbps, a few CRC failures);
  16-QAM r3/4 → 25/75 (too aggressive). Higher rates are **SNR-limited, not
  codec-limited** — the C codec decoded real OTA 16-QAM partially, proving the
  demap/Viterbi work on live captures; the cap is the physical link (this setup
  is weaker than the historical 36 kbps palm-rest geometry). This is exactly the
  case the adaptive sounder (1.4) picks the MCS for, with ARQ for the stragglers.
- Reproducible: `clib.py` (digital loopback self-test) + `ota_clib.py [n] [bpb]
  [rate]`. The dylib is a build artifact (gitignored); rebuild line in clib.py.

### `[1.9-doc]` Crypto cost/security tradeoff documented
`docs/CRYPTO_TRADEOFF.md`: the per-MCS overhead table, computed from the real
codec geometry (`clib.py`). Per-frame AEAD tag (28 B) is **0.15 %** at 16-QAM
r3/4 (38 kbps) but **87.5 %** at the MT-FSK floor with tiny frames; the 80-B
handshake amortizes in 17 ms at the top tier vs 2.4 s at the floor. Guidance:
turn it on for free on the fast tiers; at the floor, batch larger frames /
authenticate-don't-encrypt / or skip it. The wiring of the envelope onto the
bulk path (the code half of 1.9) remains; the *decision surface* — the thing the
user emphasized (apps choose bandwidth-vs-security by channel) — is now
documented. Cited from SECURITY.md.

### `[roadmap]` Pleasant-sounding audible modes
Added a ROADMAP exploration item (user idea): data-over-audio waveforms in the
audible band designed to sound benign/musical (chord-tone/pentatonic symbol
maps, pink-noise spectral shaping, melodic carriers, psychoacoustic masking),
trading bitrate for a sound a user would tolerate playing aloud in a shared room.

### `[1.4]` Adaptive-sounder decision engine — built and tested (C)
`cyrinx_sounder.{c,h}` + Swift `AdaptiveSounder.swift`: the decision half of the
environment-adaptive scheduler (the distinctive innovation). `recommendMCS(
medianSNRdB:delaySpreadMs15:)` walks the MCS ladder (16-QAM r3/4 → r1/2 → QPSK →
BPSK), picking the most aggressive tier whose SNR + delay-spread gates both
clear, falling to a non-coherent MT-FSK floor when the −15 dB delay spread
exceeds the 32 ms CP cap (the reverberant-desk regime), and it **never refuses to
link** ("reposition" is advisory). Sizes the CP to cover the delay spread + 25 %
(clamped to the cap) and switches NFFT 2048→4096 past 1024-sample CP.
`bitLoading(perBinSNRdB:)` does the calibrated per-bin loading (0/2/4/6 by SNR).
7 unit tests across the SNR/delay-spread space + the floor + CP clamp + loading;
pure logic, no audio. The capture→metrics half reuses the RX channel estimation
(needs a sounding burst = audio). 80 tests green.

### `[1.6]` Apple vDSP/Accelerate FFT backend — built and validated
`cyrinx_fft.c` now has two backends behind one interface, selected at compile
time: the vendored **KISS FFT** (portable default, the CI-tested reference that
also ships to Android via JNI) and, under `-DCYRINX_FFT_ACCELERATE` on Apple, a
**vDSP/Accelerate** double-precision DFT. The vDSP path uses the full complex DFT
(`vDSP_DFT_zop_*D`) rather than the packed real FFT — simpler and unambiguous (no
zrip pack/scale/sign pitfalls), still Accelerate-vectorized; the real input gets
a zero imaginary part and irfft mirrors the Hermitian half. Validated against the
**same golden vectors**: the entire codec (TX `data_freq`+`wave`, RX decode of
`rx_wave`) passes all 20 codec/golden tests through Accelerate within the 1e-5
tolerance. KISS default stays green (80 tests). Repeatable via
`scripts/test-accelerate.sh`. This is the "Apple optimization" — correctness
comes from the portable path; the backend is a drop-in the golden vectors gate.

### `[examples]` Adaptive-bulk usage example
`Examples/AdaptiveBulk` (`cyrinx-example-adaptive-bulk`): one runnable program
tying the new library surfaces together — `BulkPHY.encode/decode` round trip,
`recommendMCS` across clean/contact/reverberant channels (→ 16-QAM / QPSK /
MT-FSK floor), and `repositioningAdvice` (→ move closer / lower volume / soft
surface). Compiles in CI; doubles as usage docs (Examples/README.md). Output
confirms the whole stack: 9/9 blocks, payload match, evm 0.013.
