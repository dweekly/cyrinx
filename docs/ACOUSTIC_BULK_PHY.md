# Wideband Acoustic Bulk PHY: Measured 36.6 / 27.3 kbps Mac ↔ Pixel 7a

Fresh as of 2026-06-09. Status: goal (≥20 kbps measured goodput each
direction) achieved and verified on hardware. Working code:
`scratch/hw20k/` (Python harness + modem), `Apps/HIL/android/.../BulkDemod.kt`
(on-device decoder). Lab notebook: [scratch/hw20k/NOTES.md](../scratch/hw20k/NOTES.md).

## 1. Result

Physical setup: Pixel 7a face-up on the MacBook Pro palm rest on a soft
cloth; both devices at maximum volume; normal office ambient.

| Direction | Decoder | Blocks verified | Goodput |
|---|---|---|---|
| Mac → Pixel 7a (1.1–23 kHz) | **on the Pixel** (BulkDemod.kt) | 375/375 (96,000 B) | **36,571 bps** |
| Pixel 7a → Mac (0.6–17 kHz) | on the Mac (modem.py) | 280/280 (71,680 B) | **27,307 bps** |

Goodput definition (deliberately conservative): payload bytes that are both
CRC32-valid and byte-identical to the transmitted PRBS, divided by the span
from the first frame's chirp to the last frame's final data sample — so
preambles, channel-estimation symbols, pilots, FEC redundancy, CRCs, and
inter-frame gaps all count against the number. 5 frames per direction
(21.0 s span each). Reproduce with:

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

- Shannon capacity (8-bit cap): ≈184 kbps M→A, ≈155 kbps A→M. The achieved
  link uses ~20–24% of capacity; substantial headroom remains.
- The Mac→Pixel channel is essentially **flat to 23 kHz** in this near-field
  geometry. An earlier walkthrough claim of "−32.5 dB roll-off at 16 kHz"
  does not reproduce; design decisions based on it were wrong.
- Pixel→Mac dies above ~17 kHz (Pixel speaker / Mac mic roll-off).
- Pixel bottom mic (ch0) beats the top mic by 6–20 dB below 10 kHz.
- Sample clock offset Mac↔Pixel: **−24.6 ppm** (8 s, 10 kHz tone, quadratic
  interpolated FFT peak). ≈0.037 samples/symbol drift at the final geometry.
- Delay spread (ESS, −30 dB): ~21.7 ms M→A, ~10.8 ms A→M. Most energy is in
  the first few ms; CP 768 (16 ms) plus per-symbol tracking suffices.

## 3. Modem design (what shipped)

48 kHz PCM16. NFFT 2048 (23.4 Hz bins), CP 768 → 17.05 symbols/s.
Per frame: 4096-sample chirp (2→16 kHz) for detection/coarse sync, 2048
samples of guard (lets the chirp's reverb tail decay before channel
estimation), 2 known QPSK sync symbols (LS channel estimate + per-bin noise
variance from their difference), then 64 data symbols.

- Comb pilots every 8th used bin, random QPSK. Per symbol: iterative
  (3-pass) fit of pilot phase ramp → timing slope + common phase error,
  applied to all bins. This absorbs the −24.6 ppm clock skew.
- Uniform 16-QAM on data bins (per-bin adaptive loading is implemented but
  wasn't needed to hit the target).
- FEC: K=7 (171,133) convolutional, punctured to rate 3/4, soft max-log
  LLRs, frame-wide random interleaver, zero-terminated, Viterbi decode.
- **Per-symbol LLR weighting: noise variance = sync-derived per-bin estimate
  + that symbol's pilot EVM²** — see §4 defect 4; this is load-bearing.
- CRC32 per 256-byte payload block for goodput accounting.
- Window placement biased 24 samples early so multipath pre-cursors stay
  inside the CP.
- All deterministic streams (pilots, sync symbols, interleaver permutation,
  PRBS payload/padding) derive from a shared splitmix64 generator
  (`DetRng`), implemented identically in Python and Kotlin, so the phone
  regenerates the expected payload locally and verifies bytes on-device.

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

This is a measured experiment, not a product. Known deficiencies: the bulk
PHY is not integrated into the public Swift/C transport API; receive is
batch-decoded (no real-time streaming RX yet, though decode runs 20x real
time); MCS selection is open-loop per session (no closed-loop rate
adaptation); the X25519/CTR/HMAC envelope from the older stack is not wired
into the bulk PHY; and results are validated in exactly one geometry
(Pixel 7a on a MacBook Pro palm rest) on one device pair.

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

## 8. Where to go next (untapped headroom)

- Per-bin adaptive bit loading (`adapt` profile in `ota_test.py`): the SNR
  is 35–45 dB mid-band; 64/256-QAM there should roughly double throughput.
- Maximal-ratio combining of the Pixel's two mics (mic0+mic1).
- True 2×2 MIMO (Mac stereo speakers × Pixel stereo mics) for spatial
  multiplexing Mac→Pixel.
- Real-time streaming RX (current Kotlin decoder is batch over a capture,
  but runs 20× real time, so a ring-buffer port is straightforward).
- Link-layer integration: rate adaptation from receiver-fed-back per-bin
  SNR, ARQ for the residual block errors at higher MCS.
