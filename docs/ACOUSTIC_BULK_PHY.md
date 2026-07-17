<!-- Negative results / dead ends: docs/NEGATIVE_FINDINGS.md -->
# Cyrinx 2.0 Wideband Acoustic Bulk PHY: Measured 65.9 kbps Mac → Pixel 7a

Fresh as of 2026-07-17. Status: the original ≥20 kbps goal remains verified in
both directions, and Cyrinx 2.0 raises the measured Mac→Pixel result in the
accepted five-frame/250 ms-gap schedule class from 36.571 to 65.875 kbps. The
bulk PHY research harness is under `scratch/hw20k/`; the library modem source
of truth is now the C implementation under `Sources/CCyrinx`.

## 1. Result

### Cyrinx 2.0 downlink result (2026-07-17)

The prospective test used a Pixel 7a face-up on 0.5-inch soft cloth above the
MacBook left function key, with its bottom microphone near the MacBook's
built-in left speaker. The A/C was enabled but thermostat-cycling and
uninstrumented. The qualified drive envelope was Mac output volume 50%, digital
waveform peak 0.18, 48 kHz stereo `UNPROCESSED` capture, and left-speaker-only
transmit. SPL was not instrumented, so these settings are device-specific, not
an acoustic-exposure rating.

| Schedule class and profile | Blocks verified | Mean span goodput | Run range | Paired baseline |
|---|---:|---:|---:|---:|
| Accepted class: 5 frames, four 12,000-sample gaps; CP96, pilots/16, 64-QAM r2/3, 64 symbols | 4,215/4,280 (98.4813%) | **65.875457875 kbps** | 65.266–66.641 kbps | 44.199424 kbps |
| Separate zero-gap class: 5 frames; CP96, pilots/64, 64-QAM r2/3, 96 symbols | 6,129/6,760 (90.6657%) | **69.651849660 kbps** | 65.731–72.641 kbps | 48.102681 kbps |

The accepted-class candidate beat its paired baseline in all eight balanced-
order pairs (exact one-sided sign test, `p = 1/256`). All 16 planned runs
completed. It did **not** pass the predeclared reliability gate: its 98.4813%
block success was below the baseline's 2,999/3,000 (99.9667%). The 65.875 kbps
number is therefore the accepted same-class throughput result, not a claim of
equal or better resilience.

The zero-gap campaign also won 8/8 pairs (`p = 1/256`) and completed all 16
planned runs. Its mean gross goodput, whose denominator adds the declared pads
(zero leading and 16,000-sample trailing) to the scheduled span, was
68.636220472 kbps.
It is a different schedule/accounting class and also failed the reliability
gate: the baseline recovered 4,509/4,520 blocks (99.7566%). This eight-pair
confirmatory mean is 1.9045× the 36.571 kbps website benchmark, not “almost
tripled”; individual runs and earlier development screens reached higher means.

An isolated receiver comparison replayed the same fresh accepted-class captures
through two frozen decoders. Pilot-local LLR weighting recovered 4,215 blocks,
versus 3,868 for the legacy global-only weighting: +347 blocks, with every one
of the eight candidate runs improved and none regressed. This isolates a
receiver-algorithm gain from acoustic-run variation; the remaining profile
gain also includes the waveform and scheduling changes below.

The main throughput mechanisms are:

- CP reduced to 96 samples after route-specific characterization;
- comb-pilot spacing widened from 8 to 16 for the accepted result;
- 64-QAM with rate-2/3 convolutional coding, selected because rate 3/4 and
  rate 5/6 failed the Pixel screens;
- known-pilot-only, local-frequency LLR reliability weighting;
- held-out-pilot-only selection between mic0 and two-microphone MRC, without
  consulting payload, decoded bits, CRC, or ordinary data-bin EVM; and
- the qualified volume-50/peak-0.18 route described above.

The 96-symbol frame and removal of the inter-frame gap are amortization and
accounting changes, not receiver improvements. They are reported separately
from the accepted schedule class.

For reproducibility, the accepted-class campaign seed was `20260770`; its plan
SHA-256 was `fe295a7835061ccb55f9ffe277c455c97b3487decb6e9da38b01af61c32e05ef`,
execution-manifest SHA-256 was
`3d46bf7eccf669a6d29ca6593662c6c423710c24062aebe6c9ec16ddf7646ec6`, and
frozen decoder SHA-256 was
`bd7e52acaf1d9c7a1994d5a350af8baf2ea2fef03f47ed3a0f59fd8513c0327c`.
The same-capture decoder-comparison report SHA-256 was
`57522f6b3cafb5e925a2bfbb1ea70e5448999a17052aa3201a6f4f91367009bc`.
For the zero-gap campaign (seed `20260800`), the corresponding plan, local
execution manifest, and decoder hashes were
`8e5f1062dcccc42bbfac866a8659bf8d1884dd54bb054a9f42d944a1919e966b`,
`9846a33717c18e457169578217dbbdabe740eaa26c09965cb24ce128c9d1e462`, and
`c6e4e5adc0865a379dc036c549cef4cd91528152ffffa66ee2fdf9be47580956`.
The manifests and captures are retained local benchmark artifacts ignored by
Git, not published repository files; the hashes bind the exact local evidence
when it is transferred or archived.

### Original bidirectional benchmark (2026-06-09)

Physical setup: Pixel 7a face-up on the MacBook Pro palm rest on a soft
cloth; both devices at maximum volume; normal office ambient.

| Direction | Decoder | Decoded records passing historical check | Goodput |
|---|---|---|---|
| Mac → Pixel 7a (1.1–23 kHz) | **on the Pixel** (BulkDemod.kt) | 375/375 (96,000 B) | **36,571 bps** |
| Pixel 7a → Mac (0.6–17 kHz) | on the Mac (modem.py) | 280/280 (71,680 B) | **27,307 bps** |

Historical metric definition: decoded payload records that are CRC32-valid and
members of the deterministic transmitted-PRBS set, divided by the span
from the first frame's chirp to the last frame's final data sample — so
preambles, channel-estimation symbols, pilots, FEC redundancy, CRCs, and
inter-frame gaps all count against the number. 5 frames per direction
(21.0 s span each). The verifier did not retain decoded-record uniqueness,
strict stream order, or scheduled slot attribution; 375/375 and 280/280 must
therefore not be read as proof that every unique scheduled block was recovered.
The later Cyrinx 2.0 referee enforces that stronger contract. Re-run with:

```
.venv/bin/python3 scratch/hw20k/final_measurement.py
```

The Kotlin decoder runs ~170 ms per 4 s frame on the Pixel (≈20× real time)
and was validated bit-compatible against the Python decoder on an identical
capture before the live runs.

## 2. Measured channel (the foundation everything rests on)

`characterize.py` measured probe-on vs probe-off Welch PSD (no sync needed)
plus an exponential sine sweep and a long pure tone. Artifacts:
`scratch/hw20k/data/channel.json`, `channel_snr.png`.

| Band (kHz) | Mac→Pixel mic0 SNR | Pixel→Mac SNR |
|---|---|---|
| 0.3–2 | 26.4 dB | 28.1 dB |
| 2–6 | 41.2 dB | 44.3 dB |
| 6–10 | 37.1 dB | 47.3 dB |
| 10–14 | 36.0 dB | 45.5 dB |
| 14–18 | 38.1 dB | 33.9 dB |
| 18–21 | 36.1 dB | 10.0 dB |
| 21–24 | 40.7 dB | 4.6 dB |

- Shannon capacity (8-bit cap): ≈184 kbps M→A, ≈155 kbps A→M. The original
  June links used ~20–24% of those estimates; the 65.875 kbps downlink is about
  36% of the historical downlink estimate. These are diagnostic upper bounds,
  not achievable-goodput predictions.
- The Mac→Pixel channel is essentially **flat to 23 kHz** in this near-field
  geometry. An earlier walkthrough claim of "−32.5 dB roll-off at 16 kHz"
  does not reproduce; design decisions based on it were wrong.
- Pixel→Mac dies above ~17 kHz (Pixel speaker / Mac mic roll-off).
- Pixel bottom mic (ch0) beats the top mic by 6–20 dB below 10 kHz.
- Sample clock offset Mac↔Pixel: **−24.6 ppm** (8 s, 10 kHz tone, quadratic
  interpolated FFT peak). ≈0.037 samples/symbol drift at the final geometry.
- Delay spread (ESS, −30 dB): ~21.7 ms M→A, ~10.8 ms A→M. Most energy is in
  the first few ms; CP 768 (16 ms) plus per-symbol tracking suffices.

## 3. Original modem design and Cyrinx 2.0 receiver update

The original 2026-06 profile used 48 kHz PCM16, NFFT 2048 (23.4 Hz bins),
and CP768 → 17.05 symbols/s. Per frame: 4096-sample chirp (2→16 kHz) for
detection/coarse sync, 2048
samples of guard (lets the chirp's reverb tail decay before channel
estimation), 2 known QPSK sync symbols (LS channel estimate + per-bin noise
variance from their difference), then 64 data symbols.

- Comb pilots every 8th used bin, random QPSK. Per symbol: iterative
  (3-pass) fit of pilot phase ramp → timing slope + common phase error,
  applied to all bins. This absorbs the −24.6 ppm clock skew.
- The original profile used uniform 16-QAM and rate 3/4. Cyrinx 2.0's measured
  Pixel downlink uses uniform 64-QAM and rate 2/3; the stronger code was
  necessary at this constellation density.
- FEC remains K=7 (171,133) convolutional coding with soft max-log LLRs,
  frame-wide random interleaving, zero termination, and Viterbi decode.
- The 2026-06 receiver used **one global pilot-EVM² term per symbol** in every
  data-bin LLR. The current Cyrinx 2.0 C receiver retains that burst-erasure
  signal but adds payload-independent local-frequency weighting: corrected
  known-pilot residual powers are endpoint-replicated over an 11-pilot boxcar
  and linearly interpolated to data bins. Its demapper uses `sync_noise + 0.25 ×
  global_pilot_EVM² + 0.75 × local_pilot_EVM²`. Payload-bearing data symbols,
  decoded values, and CRC results do not enter this estimate.
- CRC32 per 256-byte payload block for goodput accounting.
- Window placement biased 24 samples early so multipath pre-cursors stay
  inside the CP.
- All deterministic streams (pilots, sync symbols, interleaver permutation,
  PRBS payload/padding) derive from a shared splitmix64 generator
  (`DetRng`). The retained Python oracle and legacy Kotlin decoder implement
  the same streams for cross-checking; C is the canonical modem implementation.

Direction-specific transmit profiles, both required by measurement (§4):
Mac→Pixel uses the **left speaker only**; both directions append ≥0.3 s of
in-stream trailing silence.

## 4. The four physical-layer defects (in discovery order)

Each cost the link entirely until found; all were diagnosed on real
captures, not simulation.

1. **ISI: delay spread ≫ cyclic prefix.** Adjacent identical symbols showed
   24–27 dB consistency while different-content adjacent symbols showed
   ~11–13 dB — the signature of inter-symbol interference, not noise.
   Geometry moved from NFFT 1024/CP 256 to NFFT 2048/CP 768.
2. **Dual-speaker transmit corrupts the composite.** With the phone on the
   left palm rest, the right speaker arrives ~3× weaker with decorrelated
   phase (EVM 1.33 alone); summed with the left it drags composite EVM to
   ~0.5. Left-only transmit gives EVM ~0.06. (Proper 2×2 MIMO with
   per-stream equalization could exploit the second speaker; not needed.)
3. **Stream-end fade kills the final symbol.** The macOS speaker chain
   tapers the last ~10 ms when a stream stops. With a frame-wide
   interleaver, one dead symbol contaminates every FEC block. Raw per-symbol
   BER was 0.000 for symbols 0–14 and 0.264 for the final symbol — fixed by
   in-stream trailing silence.
4. **Overconfident LLRs from a corrupted symbol poison Viterbi.** Even 1
   corrupted symbol in 16 (≈1.7% average raw BER — trivially correctable in
   principle) yielded 0–1 of 6 blocks, because its wrong LLRs carried full
   confidence. Weighting each symbol's LLRs by its own pilot EVM² turns such
   symbols into soft erasures; the same digital regression then decodes 6/6.
   This also provides burst-noise immunity (typing, key clicks) for free.

## 5. Diagnostic methodology (including the negative results)

These discriminating experiments are reusable; several "obvious" hypotheses
were exonerated by them, which is worth as much as the positives.

- **Repeated-symbol probe** (`N` identical OFDM symbols): separates ISI
  (varying-content penalty) from channel instability, sample drops, and
  clock drift. Phase trace across repeats measured drift cleanly: linear in
  time, proportional to frequency → pure clock skew, channel stable.
- **Amplitude ladder** (0.07→0.7 digital amplitude): EVM flat across 20 dB →
  **nonlinearity/speaker-protection DSP exonerated** as the dominant
  impairment.
- **afplay vs sounddevice A/B** on the identical frame: identical EVM →
  **playback software path exonerated**.
- **Genie TX/RX ratio test** (FFT of saved TX and captured RX at matching
  symbol positions): adjacent-symbol consistency 15 dB while sync-to-late-
  symbol agreement degraded smoothly → channel fine, receiver tracking at
  fault.
- **Piecewise TX↔RX alignment** (correlate successive TX chunks against the
  capture): detects dropped samples (none found — smooth +24 ppm drift) and
  revealed correlation tap-hopping between two multipath arrivals ~19
  samples apart (motivating the early-bias window placement).
- **Per-symbol raw BER against the known coded stream**: localized failure
  to exactly the final symbol (defect 3) when aggregate metrics (EVM, BER)
  looked merely "mediocre".
- **Cautionary tale:** an early debug script mixed module-level FFT/CP
  constants with a differently-configured modem and fabricated a "0 dB sync
  consistency" reading that misdirected the investigation for a round.
  Diagnostic tooling must share the exact config object with the system
  under test (`debug_capture.py` is fixed and carries a warning comment).

## 6. Android/macOS HIL platform gotchas

- **Android silently records zeros** when the activity isn't top-visible:
  a secure-lockscreen bouncer (`AlternateBouncerView`) or an expanded
  notification shade both trigger the audio policy's capture silencing
  (`dumpsys audio` shows `rec ... silenced`). Fix: `setShowWhenLocked(true)`
  + `setTurnScreenOn(true)` in the activity, `cmd statusbar collapse`, and a
  wake sequence before every capture. Battery saver at low charge re-dozes
  the screen despite `svc power stayon`.
- Pull app-internal captures with `adb exec-out run-as <pkg> cat files/x`;
  the app can read (not write) `/data/local/tmp` for pushed waveforms.
- Mac mic clips near-field at full input volume (captures hit ±1.0);
  input volume ≈22/100 is right for a phone speaker at max on the palm rest.
- `AudioRecord` with `UNPROCESSED` at 48 kHz stereo works on the Pixel 7a
  and is mandatory — processed sources apply AGC/NS that would wreck QAM.
- Use the MacBook's native 48 kHz devices explicitly (external displays/BT
  headsets silently become default and hijack playback).

## 7. Status, originality, and limitations

This is a measured experiment, not a product. The C PHY is the canonical
implementation; Swift should remain a thin public binding, Kotlin is a legacy
HIL implementation that can drift, and Python is the research/oracle layer,
not another shipping modem. Known deficiencies remain: receive is batch-
decoded; closed-loop rate adaptation is not integrated; the older security
envelope is not wired into this bulk PHY; and the Cyrinx 2.0 headline is one
near-field geometry on one device pair. Robust maximum throughput at a 3 ft
(0.91 m) separation remains explicit roadmap work.

On originality: OFDM, cyclic prefixes, comb pilots, QAM, punctured
convolutional codes with Viterbi decoding, CRC block verification, and
acoustic speaker-mic links are all established techniques, and data-over-
sound has substantial prior art — minimodem (general-purpose audio FSK),
ggwave (multi-tone FSK data-over-sound), Quiet/quiet-js (OFDM-based with
ultrasonic profiles and FEC), Google Nearby Messages (DSSS, ~94.5 bps),
Chirp.io/LISNR (commercial CSS), and BatNet (smartphone ultrasonic PSK over
20-24 kHz; arXiv). The contribution of this work is the verified end-to-end
goodput on commodity hardware with all overhead counted, the discriminating-
experiment methodology of §5, and the platform/physical defect catalog of
§4/§6.

## 8. Where to go next

- Recover reliability before raising the headline: test a code rate near 0.70
  and a denser or frequency-staggered pilot lattice against channel drift.
  A decision-directed tracker with `alpha = 0.05` is research-only until it
  survives prospective tests without error propagation.
- Maximize robust throughput at 3 ft, with calibration and MCS selection bound
  to each device's speaker/microphone route.
- Evaluate true 2×2 MIMO (Mac stereo speakers × Pixel stereo mics) for spatial
  multiplexing; blind duplicate-speaker drive remains invalid.
- Real-time streaming RX (the legacy Kotlin HIL decoder is batch over a capture,
  but runs 20× real time, so measured CPU headroom exists; streaming sync and
  buffer integration remain engineering work).
- Link-layer integration: rate adaptation from receiver-fed-back per-bin
  SNR, ARQ for the residual block errors at higher MCS.
- The ultrasonic channel investigation is complete, but an integrated
  ultrasonic mode is not. A deliberately pleasant, low-rate audible mode has
  not yet been measured.
