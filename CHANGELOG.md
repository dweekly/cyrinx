# Changelog

Validated, backward-looking milestones — what was built, measured, and proven,
by date. Forward-looking work lives in [ROADMAP.md](ROADMAP.md). The project has
no version numbers yet (pre-public research prototype); entries are dated
milestones. Detailed evidence for every claim: the cited doc + the reproduction
commands it contains.

## 2026-07-02

- **cyrinx.org website built** (Track C; deploy pending user auth on
  Cloudflare). Static, dependency-free single page in `site/`: the hero
  synthesizes a real bulk-PHY frame in-browser (correct geometry: chirp,
  guard, 2 sync symbols, OFDM 1.1–23 kHz at NFFT 2048/CP 768), renders its
  spectrogram on canvas, and plays it via WebAudio on click; interactive
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
**48 kbps (clean) → ~11 kbps (reverberant, rescued) → 5 kbps–68 bps (shadowed)
→ never zero** where a naive receiver collapsed. Whitepaper folded it all in
(22 pp). PRs #38–#42.

- **EVM-probe sounder** replaces repeated-pilot SNR estimation for MCS selection
  (fixes the systematic under-call; see EXPERIMENTS.md finding 3).
- **Distance sweep finding:** mic-position-over-keyboard, *not* distance, is the
  dominant axis on this bench; best cell `port_fnkey` carried 39.3 kbps.
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

- **39.3 kbps library-native OTA** (16-QAM r¾, 375/375 blocks byte-verified):
  the shipped C codec (`libcyrinxbulk`) decoding over the air, exceeding the
  36.6 kbps Python-harness headline and retiring the paper's earlier
  "library only hit 12.8 kbps OTA" caveat.
- **64-QAM ceiling finding:** 64-QAM r¾ decoded 0/339 at EVM 0.173 despite
  52 dB raw channel SNR — higher-order QAM is bounded by the effective-SINR/EVM
  floor (~15 dB) of the transducer chain, not channel SNR.
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
  **27.3 kbps Pixel→Mac**, ordered byte-verified over-the-air goodput on a
  MacBook Pro M4, audible band, palm-rest geometry.
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

## 2026-02-12 → 2026-05-08 — Ultrasonic transport stack (original strand)

- C core (`CCyrinx`) with stable ABI + Swift wrapper; bit-packed frame codec
  (CRC16/CRC32C), fragmentation/reassembly; half-duplex ping-pong MAC with ACK +
  selective retransmission; ARC gear state machine; stream-multiplexed transport
  API; in-memory linked transport for deterministic tests.
- Apple audio scaffolds (RemoteIO iOS / AVAudioEngine macOS); Android HIL app
  with ADB automation; raw tone codecs (OOK/nibble/DTMF/Morse); vDSP OFDM-QPSK
  and D-CSS modulators; acoustic PHY bridge with dual-ZC sync.
- Net result: functional but slow as measured — **<0.3 kbps OTA** in the
  18.5–23.5 kHz band. This ceiling motivated the audible-band bulk PHY.
