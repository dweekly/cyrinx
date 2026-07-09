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

5 frames per direction, 16QAM r3/4, goodput = CRC32-valid AND byte-verified
payload / span from first chirp to last data sample (all overhead included):

- **Mac -> Android: 36,571 bps** — 375/375 blocks (96,000 bytes), demodulated
  ON THE PIXEL by BulkDemod.kt (Kotlin port, 170 ms per 4 s frame), payload
  verified on-device against the transmitter's splitmix64 PRBS.
- **Android -> Mac: 27,307 bps** — 280/280 blocks (71,680 bytes), demodulated
  on the Mac (modem.py) from its own mic capture.

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

## MRC-aware sounding + CP escalation (2026-07-08)

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

Live result at the cell: **138 bps floor → 11.6 kbps QPSK r1/2, 0/75 blocks
single-mic, 75/75 MRC-rescued (84×)** — the paper's "MRC rescues cells where
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

## A5: third device pair — Moto G 2026 (2026-07-09)

Budget-hardware generality run (Wi-Fi adb: the phone's USB data path was
dead — charged fine, never enumerated on the bus, on a cable+port the Pixel
passed; wireless debugging pair/connect worked immediately). Clean cell
(`facedown_port_fnkey`), Mac M4:

- **Downlink Mac→Moto: 48.0 kbps, 225/225 blocks, 16-QAM r3/4, probe EVM
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
