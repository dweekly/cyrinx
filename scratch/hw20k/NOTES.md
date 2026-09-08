# 20 kbps Bidirectional Acoustic Link — Working Notes

> Audio captures (.pcm/.npy/.wav) are NOT committed: TX waveforms are pure
> functions of the code, and RX captures are reproduced in minutes by
> re-running the harness with the hardware attached (`ota_test.py`,
> `characterize.py`, `final_measurement.py`). Only small derived artifacts
> (channel.json, channel_snr.png, snr_*.json) are kept.
>
> Lab notebook. The curated research writeup (results, methodology,
> platform gotchas, future directions) is
> [docs/ACOUSTIC_BULK_PHY.md](../../docs/ACOUSTIC_BULK_PHY.md).

Fresh as of 2026-07-01 (A1 section at end). Effort: measured ≥20 kbps acoustic goodput Mac↔Pixel 7a.
Setup: Pixel 7a face-up on MacBook Pro palm rest on a soft cloth; both volumes max.

## Measured channel (2026-06-09, characterize.py, real OTA)

Probe: random-phase multitone 0.3–23.5 kHz, amp 0.6, vs 6 s ambient floor.
Welch PSD, NFFT=1024 (46.875 Hz bins).

| Band (kHz) | Mac→Android mic0 SNR | Android→Mac SNR |
|---|---|---|
| 0.3–2   | 26.4 dB | 28.1 dB |
| 2–6     | 41.2 dB | 44.3 dB |
| 6–10    | 37.1 dB | 47.3 dB |
| 10–14   | 36.0 dB | 45.5 dB |
| 14–18   | 38.1 dB | 33.9 dB |
| 18–21   | 36.1 dB | 10.0 dB |
| 21–24   | 40.7 dB | 4.6 dB  |

- Shannon (8-bit cap): M→A ≈ 184 kbps, A→M ≈ 155 kbps. Target 20 kbps ≈ 13 % of capacity.
- Pixel mic ch0 (bottom mic, nearest Mac) beats ch1 by 6–20 dB below 10 kHz.
- A→M usable band ≈ 0.3–17 kHz (Pixel speaker/Mac mic rolls off above 18 kHz).
- M→A usable band ≈ 0.3–23 kHz (flat!). Prior agents' "severe roll-off at 16 kHz"
  claim does not reproduce in this near-field geometry.
- Delay spread (ESS, −30 dB threshold): M→A ~21.7 ms, A→M ~10.8 ms. Long tail is
  low-level; equalization + modest CP handles it (verify EVM in practice).
- Sample clock offset Mac vs Pixel: **−24.6 ppm** (10 kHz tone, 8 s). Over 2 s
  frame ≈ 2.4 samples of drift → needs per-symbol pilot timing/phase tracking.
- Ambient floors: Pixel rms ≈ 7e-4 (16-bit FS), Mac rms ≈ 8e-3.

## Hardware gotchas (hard-won)

- Android mic capture is silently zeroed if the activity isn't top-visible:
  keyguard bouncer (AlternateBouncerView) or expanded notification shade both
  trigger it. Fix: `setShowWhenLocked(true)+setTurnScreenOn(true)` in the
  activity + `cmd statusbar collapse` + wake before every capture.
- Battery saver at low charge re-dozes the screen despite `svc power stayon`.
- App `play_pcm` sets STREAM_MUSIC to max programmatically (AudioHardening
  partially ignores setStreamVolume on Android 16 — verify level in logs).
- Pull captures via `adb exec-out run-as com.dweekly.cyrinxhil cat files/x.pcm`
  (app files dir; /sdcard is scoped-storage-restricted, /data/local/tmp is
  readable but not writable by the app).

## Modem design v1 (modem.py)

- 48 kHz, FFT 1024, CP 256 (5.33 ms guard) → 37.5 sym/s.
- M→A data band 1.1–23.0 kHz; A→M 0.6–17.0 kHz. Comb pilots every 8th bin.
- Preamble per frame: 4096-sample chirp (coarse sync) + 2 known OFDM symbols
  (fine sync + LS channel estimate).
- Per-symbol pilot processing: LS fit of pilot phase vs bin → timing slope +
  CPE, correct data bins; 1-tap MMSE equalizer from preamble estimate.
- Adaptive per-bin QAM (BPSK/QPSK/16/64/256) from measured SNR with margin.
- FEC: K=7 (171,133) convolutional, punctured to 3/4 (configurable), random
  interleaver, zero-terminated per frame; CRC32 per 512-byte block for honest
  goodput accounting.
- Goodput = CRC-valid unique payload bits / airtime (preamble through last
  symbol), measured on real OTA captures.

## Iteration log

- v0 primitives + characterization: done (above).
- v1 modem debugging (all on real OTA captures, 2026-06-09):
  - Mac mic input clips near-field at full input volume -> input vol 22-40.
  - Channel-estimate smoothing across bins destroys H under multipath: removed.
  - Adjacent-sync-symbol decorrelation chased to FOUR stacked causes:
    (1) ISI: CP 256 (5.3 ms) << delay spread; geometry now NFFT 2048 / CP 768.
    (2) Dual-speaker TX: right speaker arrives 3x weaker at the phone with
        garbage phase; L+R sum wrecks composite EVM. m2a now LEFT ONLY.
    (3) Stream-end fade kills the final OFDM symbol -> trailing 0.33 s pad.
    (4) A single corrupted symbol poisons Viterbi through the frame-wide
        interleaver (overconfident LLRs). Per-symbol pilot-EVM noise weighting
        turns such symbols into soft erasures. Digital regression added.
  - find_chirp returns the global max; multi-frame runs now detect all chirp
    peaks (threshold + suppression) and decode each.

## Measured OTA goodput (2026-06-09, offline decode of real captures)

Honest accounting: CRC32-valid 256-byte blocks, byte-compared against the TX
PRBS payload, divided by airtime including preambles and 0.25 s inter-frame
gaps. NFFT 2048, CP 768, 64 symbols/frame, comb pilots /8.

| Direction | Profile | Blocks | EVM | Goodput |
|---|---|---|---|---|
| Mac->Android (1.1-23 kHz) | QPSK r1/2  | 75/75   | 0.06 | 12.29 kbps |
| Mac->Android | 16QAM r1/2 | 150/150 | 0.06 | 24.58 kbps |
| Mac->Android | 16QAM r3/4 | 225/225 | 0.07 | **36.86 kbps** |
| Android->Mac (0.6-17 kHz) | QPSK r1/2 | 54/54 | 0.12 | 8.85 kbps |
| Android->Mac | 16QAM r1/2 | 111/111 | 0.12 | 18.19 kbps |
| Android->Mac | 16QAM r3/4 | 168/168 | 0.11 | **27.53 kbps** |

Both directions exceed the 20 kbps target.

## FINAL verified result (2026-06-09, final_measurement.py)

5 frames per direction, 16QAM r3/4. The historical verifier counted decoded
CRC32-valid records whose bytes belonged to the expected deterministic set,
divided by the first-chirp-to-last-data span (all PHY overhead included):

- **Mac -> Android: 36,571 bps** — 375/375 blocks (96,000 bytes), demodulated
  ON THE PIXEL by BulkDemod.kt (Kotlin port, 170 ms per 4 s frame), payload
  verified on-device against the transmitter's splitmix64 PRBS.
- **Android -> Mac: 27,307 bps** — 280/280 blocks (71,680 bytes), demodulated
  on the Mac (modem.py) from its own mic capture.

The verifier retained neither decoded-record uniqueness nor strict scheduled
slot attribution. These are accepted historical expected-set metrics, not the
strict ordered contract used by the later Cyrinx 2.0 referee.

The Kotlin decoder was first validated bit-exact against the Python decoder
on an identical capture file (225/225 blocks, matching EVM).

## A1 auto-MRC: cross-compat spike + escalation in the live loop (2026-07-01)

Executed A1 steps 1–3 of [docs/A1_AUTO_MRC.md](../../docs/A1_AUTO_MRC.md),
all digital (no hardware). Bench machine note: this session ran on an M1 Max
MacBook Pro, not the M4 the OTA numbers were measured on — irrelevant for
these digital steps, but step 4 (OTA re-validation) must re-derive gain
staging if run on this machine.

- `xcompat_validate.py` — **the unverified hypothesis is now proven:** frames
  produced by the library C codec (`clib.encode`) decode through the Python
  reference RX (`modem.demodulate_frame`), mono and two-mic MRC, across
  {16-QAM r¾, 16-QAM r½, QPSK r½} × {nfft 2048/cp 768, nfft 4096/cp 3072}
  (the loop-default and adaptive long-CP shapes). Per cell: (a) clean mono
  decodes all blocks ordered-verified; (b) MRC under independent AWGN decodes;
  (c) null-fill — complementary deep spectral notches per mic, mic0 alone 0/N,
  MRC N/N. Also `clib.modem_cfg_from_clib(cfg)` (new, in clib.py) asserts
  geometry parity (bins, bits/sym, info bits, blocks, frame samples) between
  the two implementations on every construction.
- `adaptive.py` — coherent branch now does **clib-first, MRC escalation**:
  library single-mic decode on the sounder-selected mic stays primary; if it
  is imperfect the rep escalates to `modem.demodulate_frame(rx2=...)` (both
  mics, per-subcarrier MRC) and keeps the better result. JSONL rows gain
  `clib_verified`, `mrc_rescued_blocks`, `decode_path`; the console line shows
  `clib + MRC-rescued` per cell. Goodput definition unchanged.
- `adaptive.py selftest` — offline stereo synth from `clib.encode`: clean
  stereo → clib path (no escalation); notched mic0 + complementary mic1 →
  clib 0/18, MRC rescues 18/18; mono capture → clib-only, no crash.

Still open for A1: step 4, OTA re-validation across the orientation set
(needs a bench: Pixel per the original cells, or iPhone TX + M1 Max
re-calibration as the cross-hardware variant — ROADMAP A5).

## A3: RS(15,11)/GF(16) + erasures replaces 3× repetition in the MFSK floor (2026-07-01)

Digital-only (no hardware). New `rs16.py`: errors-and-erasures Reed–Solomon
over GF(16) (syndromes → Forney syndromes → Berlekamp–Massey → Chien →
Forney), exhaustive selftest over every in-budget (e, f) errata combination
(3600 trials) + overload sanity. `mfsk.py` FEC swapped: one RS code symbol =
one nibble = one 16-FSK tone decision; codewords block-interleaved
position-major across OTA symbols; detector confidence (best/second tone
energy < 1.3) flags erasures, capped at the 4-erasure budget.

- **Rate: ×2.06 at the same symbol duration.** 16 B frame: 55 → 113 bps;
  32 B: 68 → 138 bps; 256 B: 187 bps (measured-airtime formula, digital).
- **Robustness: strictly better, not traded.** Failure-edge sweep (40 ms
  reverb, 20 seeded trials/point, 16 B): at 0 dB SNR rep3 16/20 vs RS 20/20;
  at −3 dB rep3 5/20 vs **RS 18/20**. Majority voting needs 2-of-3 correct
  picks and throws confidence away; RS spends its budget exactly where the
  detector knows it guessed. The dead-band case (5–6 kHz killed + reverb)
  also decodes via erasures — repetition could not use that information.
- OTA re-measurement of the floor number pending a bench (the whitepaper's
  68 bps figure describes the repetition implementation it measured).

## A3 OTA fix: erasure budget burn + stereo energy combining (2026-07-07)

First OTA contact for the RS floor (Pixel 7a, `edge_below_laptop`,
probe EVM 1.1–1.6, ds15 ~42 ms) FAILED 0/6 frames across two placements —
digital-only calibration had two gaps, found via `mfsk_diag.py` (new spike;
saves the stereo capture, instruments chirp lock / raw SER / timing sweep /
confidence distribution / per-codeword RS budget, `replay` mode re-analyzes
saved captures):

- **Erasure-first burns the RS budget over the air.** Reverb yields low
  best/runner-up energy ratios on many *correct* decisions, so ERASE_CONF
  flags false erasures; 2 real errors + 1 false erasure = 5 > 4 overloads a
  codeword that plain error correction decodes (measured: raw SER 6.2%,
  ≤2 err/cw, frame still failed). Fix: errors-only RS decode first,
  erasure-assisted only as fallback — the digital low-SNR erasure wins are
  preserved (errors-only overloads → None → erasure path).
- **The floor now has a combining rung** (was selection-only): if no single
  mic CRC-verifies, sum the mics' tone-energy matrices scaled to unit total
  frame energy and re-decide — the non-coherent analog of the coherent MRC
  escalation. Tone-decision errors are position-independent across mics
  (measured: same codeword 3-err on mic0, 1-err on mic1). Total-energy
  normalization chosen over median/noise-floor normalization empirically:
  SER 5.4/2.7/4.5/3.6% on the four saved captures vs best-single-mic
  6.2/6.2/4.5/6.2% (never worse); median-norm was sometimes worse than the
  best mic alone. Selftest gained a complementary-dead-band stereo case.
- **OTA result: 0/6 → 6/6 frames, 138 bps** at the same cell/placement —
  the graceful-degradation floor is again never-zero AND ×2.03 the old
  measured 68 bps repetition floor. (One harness timeout mid-session was the
  Pixel rebooting for an OS update — benign; relaunch the HIL app and rerun.)

## MRC-aware sounding + CP-retry fallback (2026-07-08)

At `overhang_kbwell` (phone face-down, port edge over the key well) the live
loop bailed to the 138 bps floor while `mrc_validate.py` at the same placement
ran 16-QAM r1/2 **22/22 via MRC where BOTH single mics decoded 0 blocks**
(EVMs 0.76/1.33 vs MRC 0.25). Two sounder gaps, both fixed in `sounder.py`:

- **The EVM probe was single-mic** — it cannot see MRC potential, so exactly
  the cells the paper's MRC story targets never entered the coherent branch
  (where the A1 escalation lives). `evm_probe` now also decodes the probe
  through library MRC (`clib.decode2`) and returns `evm_mrc`;
  `recommend_evm(evm_mrc=...)` selects on the better of the two and marks
  `via_mrc` tiers. Selftest anchored on the measured cell.
- **CP sized from ds15 under-covers heavy-tailed reverb** (energy past the
  −15 dB spread): ds15 15.8 ms sized a 23.6 ms CP → MRC EVM 0.49 (floor),
  while 2× the CP measured MRC EVM 0.25–0.32 (QPSK/16-QAM clean).
  `sound_channel_evm` now retries the probe once at 2× CP (capped at
  CP_LONG_CAP_MS) before conceding to the floor.

Live result at the cell: **138 bps floor → 11.366 kbps post-sounding QPSK r1/2
PHY payload rate, 0/75 blocks
single-mic, 75/75 MRC-rescued (82×)** — the paper's "MRC rescues cells where
neither mic decodes alone" claim, demonstrated end-to-end in the live
adaptive loop, library-native. JSONL rows gain `probe_evm_mrc` / `via_mrc`.

## A2: two-mic MRC ported into the C codec (2026-07-02)

Digital-only. `cyrinx_bulk_demodulate2` added to the library
(Sources/CCyrinx/cyrinx_bulk.c): the single-mic demodulator generalized to a
shared impl — per-mic H/noise estimation from the sync symbols, per-bin
effective SNR summed across mics, data symbols combined per subcarrier
(Z = Σ conj(Hₘ)Yₘ / (Σ|Hₘ|² + 1e-12)), pilot tracking on the combined Z.
Mono path arithmetic unchanged (bit-identical). Ports
`modem.demodulate_frame(rx2=...)` exactly.

- **C ≡ Python:** `xcompat_validate.py` now decodes the same captures through
  both — payloads identical, block counts identical, EVM equal to 4 decimals,
  across the MCS/CP grid incl. the null-fill rescue (mic0 0/N → MRC N/N).
- **Golden-pinned:** new committed case `qam16_r34_mrc` (mic0 notched
  [3–8, 12–18 kHz] over one multipath, mic1 complementary [1.1–3, 8–12,
  18–23 kHz] over a different multipath, no noise): mic0 alone decodes 0
  blocks (recorded in the manifest and asserted), MRC decodes 4/4 byte-exact.
  Passes on both KISS and vDSP FFT backends (83/83 + 23/23 accelerate).
- Swift surface: `BulkPHY.decode(_:combining:)` + binding tests (identity
  channels; deterministic zeroed-data-mic rescue at QPSK).
- `adaptive.py` escalation switched from Python MRC to `clib.decode2` — the
  loop's whole coherent path is now library-native. Per-rep accounting also
  fixed to per-block ordered verification (`_ordered_verified`): the old
  `payload == pl` shortcut gave partial decodes 0 credit, under-counting vs
  the documented goodput definition.

OTA validation of the C MRC path pending a bench (A1 step 4 doubles as it).

## minimodem honor-defense run (2026-07-09)

Prompted by K. Mostafa's reply to the launch outreach (600+ baud works
air-gapped on some pairs). `minimodem_bench.py` (new spike): minimodem TX
WAV -> Mac speakers -> Pixel stereo capture -> minimodem RX per mic, byte
accuracy vs sent text. At `facedown_port_fnkey` (clean cell): 300 bd 100%
(240 bps), **1200 bd 99.8% (~958 bps effective)**, 600/2400 bd <=3%.
Forcing 600 bd onto Bell202 tones (1200/2200) or higher (2400/4400) did NOT
rescue it (1.1%/30%) -- the 600 failure is NOT simple tone placement and is
recorded as unexplained. 2400's failure is consistent with ISI (0.42 ms
symbols, no equalizer) but was not isolated. Paper baselines discussion
updated: the FAIL rows are pair/placement-specific, per Mostafa's report +
this measurement.

## A5: third device pair — Moto G 2026 (2026-07-09)

Budget-hardware generality run (Wi-Fi adb: the phone's USB data path was
dead — charged fine, never enumerated on the bus, on a cable+port the Pixel
passed; wireless debugging pair/connect worked immediately, and it then
autoconnects as serial `adb-ZT4226T9HB-5V6xZE._adb-tls-connect._tcp`, which is
what `ANDROID_SERIAL` wants for this phone — the Pixel can stay on USB at the
same time). Clean cell
(`facedown_port_fnkey`), Mac M4:

- **Downlink Mac→Moto: 46.915 kbps post-sounding ordered PHY payload rate,
  225/225 blocks, 16-QAM
  r3/4, probe EVM
  0.096 — identical to the Pixel at the same cell**, first try, zero MRC.
  The headline rate is not premium-phone-specific.
- **Uplink Moto→Mac: 8,777 bps, 90/90 blocks at QPSK r1/2** (Pixel: 27.3
  kbps; iPhone: 16.9) — third confirmation that the phone speaker is the
  uplink bottleneck. Three Moto-specific mechanisms found (all in
  `uplink_qpsk.py`, the new spike):
  1. **Dolby DAX** effect chain on the media stream adds an EVM floor
     (0.42 at the Pixel profile). `pm disable-user --user 0
     com.dolby.daxservice` (reversible) improved it.
  2. **Speaker-protection DSP settle**: first frame EVM 2.5-2.8 while later
     frames read ~0.35; a 3 s low-level noise preroll fixes it (all 5
     frames verify with it).
  3. **Hot speaker**: Mac input 22 clipped (peak 1.03) → use 15 for this
     pair.
- Ambient and smoke: rms floors comparable to the Pixel bench; Moto speaker
  ~6× hotter into the Mac mic at the 1 kHz smoke tone.
- Shadowed desk cell (`motog_desk_below_stand`, probe EVM 1.51-1.56,
  ds15 ~40 ms): RS floor LINKED at 46-92 bps, 3/6 frames across two runs —
  never zero, but lossier than the Pixel's 6/6 at an equivalent channel
  (honest budget-microphone difference). Paper §Third-pair + site table +
  threats item updated.
- **Band-fitting recovered the uplink** (freqresp sweep `motog_palmrest`):
  the Moto speaker cliffs at 14 kHz (−40 dB by 14–17 kHz), so the Pixel's
  0.6–17 kHz profile wasted 3 kHz. Fitted 0.6–14 kHz: **14.6 kbps 16-QAM
  r1/2 (150/150)** and **19.212 kbps steady-state r3/4** (197/230 ordered
  blocks; frame 1 is partial at the thinner margin). The required 3.0 s emitted
  speaker-settle signal plus 0.25 s silence lowers r3/4 cold-start goodput to
  **16.637 kbps** (16.412 kbps including the tail), below the selected iPhone
  result. The old 22.5 kbps label discarded the
  partial frame's 13 valid blocks and omitted that frame from its denominator.
  Ladder: 0 (stock) → 8.8k (Dolby off + settle + gain) → 14.6k (band-fit
  r1/2) → 19.212k steady-state / 16.637k cold-start (r3/4). The correction
  ledger pins the old decoder and capture hashes. Captures:
  `data/motog_a2m_16qam{,_r34}_bandfit.npy`. Downlink freqresp capture
  clipped (rx_peak 1.0) — magnitudes above 8 kHz suspect, SNR fine;
  re-sweep at lower amp if the downlink curve is ever needed precisely.

## G0: MacBook self-calibration and the delay-spread horizon (2026-09-07)

First measurements from the garage readout (`scratch/garage/`), on the MacBook
Pro's own speaker-to-microphone path — ADR 0006's self-calibration and
environmental-sampling phases for a single endpoint. Five swept-sine captures,
one fixed geometry, ~4 minutes, `selfcal.py`; raw captures under
`artifacts/garage/`.

**The analysis horizon changes the answer, and the default crop is too short.**
The energy decay curve normalizes to the energy inside the analysed window, so a
short window inflates every remaining fraction and pulls crossings earlier.
Measured on one capture:

| horizon | 60 ms | 90 | 120 | 160 | 200 | 250 | 400 | 700 | 1000 |
|---|---|---|---|---|---|---|---|---|---|
| −10 dB | 0.917 | 0.917 | 0.917 | 0.917 | 0.917 | 0.917 | 0.917 | 0.917 | 0.917 |
| −20 dB | 12.00 | 14.58 | 15.29 | 15.75 | 16.33 | 16.75 | 17.04 | 17.06 | 17.06 |

At `freqresp.deconvolve_ir`'s fixed 120 ms crop this capture reads 15.29 ms at
−20 dB, below the 16 ms guard budget; converged it reads 17.06 ms, above it. The
crop alone flips a gate decision. Cells compared against one another must share a
horizon, and the garage adapter now defaults to 500 ms.

**−20 dB is not repeatable enough to decide a 16 ms budget; −10 dB is.** All five
captures recomputed at one horizon, so the effect above cannot confound them:

| threshold | min | max | range | range / median |
|---|---|---|---|---|
| −10 dB | 0.708 | 0.917 | 0.208 ms | 26% |
| −15 dB | 1.792 | 2.271 | 0.479 ms | 26% |
| −20 dB | 11.812 | 17.229 | 5.417 ms | 42% |

The −20 dB figure moves 5.4 ms on an unchanged bench and straddles the budget in
both directions. The −10 dB figure moves 0.2 ms against the same budget. The
spread at −20 dB looked bimodal (two runs near 17.1, three near 12.0) rather than
continuous, and did not track room-tone rms, which varied 5× across the same
runs; the cause is not identified. n=5 in one window at one geometry — this wants
a fresh-day repeat before being treated as settled.

Consistent with entry 1 of NEGATIVE_FINDINGS, which already says to size the
guard to the strong-tap spread rather than a deep point, and with entry 13, whose
decisive 35.8 ms figure was quoted at −10 dB.

**Bench numbers for this path:** capture rms 0.15 at sweep amplitude 0.5, peak
0.62 (no clipping), room tone rms 0.0007–0.0034, capture peak-to-tone 58 dB.
Playback-plus-capture latency is 7,387 samples, **154 ms**, which is subtracted
from the observable window: a recording must retain the horizon *plus* the
latency after the sweep ends, so the 1.0 s tail used here supports about 846 ms.
Farina deconvolution against a 6 s sweep buys ~40 dB of processing gain, so
integrated noise sits at 6.5e-08 of window energy and truncation never engages at
this SNR.

## G0: first cross-device characterization, Pixel 7a at ~1 ft (2026-09-08)

Mutual channel characterization (ADR 0006 phase 3) with `scratch/garage/crosscal.py`.
Pixel 7a over Wi-Fi adb, face up on the desk about a foot to the right of the
MacBook, both devices stationary. Sweep amplitude 0.5, phone media volume 25/25,
500 ms horizon, room tone captured on each receiver at the same gain.

| directed link | −10 dB | −15 dB | −20 dB | beyond the 16 ms budget |
|---|---|---|---|---|
| Mac speaker → Pixel mic | 48.271 ms | 87.292 ms | 136.521 ms | yes, at every threshold |
| Pixel speaker → Mac mic | 82.333 ms | 141.896 ms | 200.354 ms | yes, at every threshold |

Both readings are well clear of noise — raw capture 22.6 dB and 19.9 dB above
room tone, impulse-response peak-to-noise 75.3 dB and 64.2 dB, integrated-noise
fraction an order of magnitude below the threshold at which truncation would
engage — so these are measurements of the channel, not of the noise floor. No
clipping: raw peaks 0.018 and 0.148.

Two things worth carrying forward.

**This is NF-13's regime, reached at one foot.** Entry 13 recorded coherent
CP-OFDM decoding zero blocks at every MCS in a geometry whose strong-tap spread
was 35.8 ms. These directed links measure 48 ms and 82 ms at the same threshold,
so no guard the profile format can express covers them. The delay-spread evidence
alone does not prove a link attempt fails here — that needs an actual decode —
but it is the evidence stage 1G reads, and it points at stage 8 rather than at
guard and MCS tuning.

**The two directions differ by 34 ms at −10 dB**, which is a reminder that a
directed link is its own channel: different speaker, different microphone,
different radiation pattern. Averaging them would describe neither.

Repeatability at −10 dB was good on this path: two Mac→Pixel runs read 47.208 and
48.271 ms, about 2%, against the 26% seen on the laptop's own chassis path. A
longer, more diffuse decay is better conditioned than a short one dominated by a
few taps.

### Harness gotcha: reused capture request ids read the previous run's result

`harness.android_record_finish` waits on a logcat line naming the request id, and
`logcat -d` returns the whole buffer. A request id reused from an earlier run
matches *that* run's completion line immediately, so the file is pulled while the
new recording is still being written — the second run of this session pulled an
empty capture and only failed because the deconvolution refused an empty array.
`crosscal.py` gives every capture a per-run id and rejects an empty or all-zero
capture rather than analysing one. Any new Android capture code should do the
same, or clear the buffer first.

### Pixel 7a shell has no `media` command

`harness.android_prepare(media_volume=...)` shells out to `media volume`, which
this device answers with "media: inaccessible or not found".
`cmd media_session volume --stream 3 --get/--set` works. `crosscal.py` handles the
level locally rather than changing shared harness code for one device, and
defaults to reading and recording the current level rather than setting one.

### Conservative coherent link at ~1 ft: zero blocks, with a passing control

`scratch/garage/linkprobe.py`, same geometry and session as the characterization
above. Two frames per direction with a declared 250 ms gap and 0.4 s of trailing
silence, payloads independently seeded and verified at their scheduled block
positions, decoded host-side by the C codec.

| link | band | peak/mean | best EVM | ordered blocks | verified payload |
|---|---|---|---|---|---|
| Mac → Pixel, QPSK r1/2, CP 768 | 300–23000 Hz | 43.2 | 1.102 | **0 / 52** | 0 bps |
| Pixel → Mac, QPSK r1/2, CP 768 | 300–17000 Hz | 30.8 | 1.944 | **0 / 38** | 0 bps |
| Mac → Mac, same profile (control) | 300–23000 Hz | 130.7 | 0.136 | **52 / 52** | 12,312 bps |

The control is the point. The same code, codec, scoring and payload verification
carry 52 of 52 blocks on the laptop's own path, whose strong-tap spread is 0.9 ms
against the 16 ms budget. So an all-zero cross-device result is a property of
those channels, not of the apparatus.

Both failing directions acquire: matched-filter peak over mean of 43.2 and 30.8
against a floor of 1, no capture empty, silent, or clipping, and the same
configuration decodes 26/26 in digital loopback at several frame offsets. What
fails is the coherent demodulation itself, at EVM 1.1 and 1.9 against the
control's 0.136.

That is NEGATIVE_FINDINGS entry 13's signature reached at one foot — entry 13 had
sync locking at peak/mean 104 while QPSK r1/2 decoded zero blocks at EVM ~2 — and
the delay-spread readout said so first, measuring 48 ms and 82 ms of strong-tap
spread against a guard no expressible cyclic prefix can extend far enough to
cover.

**This is the first position where stage 1G's two conditions both hold**: a
conservative link that fails, and valid evidence of substantial energy beyond the
declared budget. The plan directs such a position to stage 8 waveform-class
screening rather than to further guard and MCS tuning.

Scope: one position, one device pair, one run per direction. It says nothing yet
about closer spacings, other rooms, or other devices. What it establishes is that
the gate's prediction was tested against a real decode, with a passing control in
the same run, and held.

#### Scoring gotcha: the receiver locks the strongest chirp in the buffer

The first version of this probe scored the control at 26/52 while both frames
were in fact perfect. The C receiver acquires the *strongest* chirp inside the
buffer it is handed, so a search window spanning two frames decodes the stronger
one and the other scores zero however good it was. A window must therefore hold
at most one frame, and the search step must be smaller than the window's slack so
that some window starts just before each frame's chirp — otherwise a present
frame falls between two windows and reads as a failure.

Both mistakes understate. Any multi-frame capture scored by sliding a window past
a self-acquiring receiver needs the same care, and needs a positive control in
the same run to notice when it does not have it.
