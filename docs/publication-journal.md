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
golden + the C loader).
