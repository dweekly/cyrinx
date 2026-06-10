# Negative Findings — Things That Did Not Work (and Why)

Fresh as of 2026-06-10. A durable record of dead ends, disproved hypotheses,
and hard-won "don't do that" results from the acoustic-link work, so they are
never rediscovered the expensive way. Each entry: what was tried, what was
measured, and the takeaway. Positive results live in
[ACOUSTIC_BULK_PHY.md](ACOUSTIC_BULK_PHY.md), [ULTRASONIC_BAND.md](ULTRASONIC_BAND.md),
[IOS_HIL.md](IOS_HIL.md); the lab notebook is [../scratch/hw20k/NOTES.md](../scratch/hw20k/NOTES.md).

Convention: "measured" = observed on real hardware or in a controlled digital
loopback; numbers are quoted as measured.

## Physical layer / waveform

1. **Cyclic prefix sized only to "cover the delay spread" is the wrong mental
   model.** CP 768 @48k = 16 ms works on the palm-rest channel whose −30 dB
   delay spread is ~21.7 ms. The link survives because most energy arrives
   early (within the CP) and the receiver tolerates the low-level tail — NOT
   because the CP engulfs the whole tail. Conversely, when the *strong* energy
   exceeds the CP (desk, below), no CP within reason saves it. Size CP to the
   strong-tap spread, not the −30 dB point.

2. **Channel-estimate smoothing across subcarriers destroys the estimate under
   multipath.** With bulk/multipath delay, H's phase rotates multiple cycles
   across a few bins; a moving-average across bins averages out the phase →
   garbage. Only the (real, positive) noise variance may be smoothed. H itself
   must stay per-bin.

3. **Hard-clipping for PAPR control is harmful; but PAPR was NOT the BPSK
   culprit.** Replacing the brick-wall `np.clip` with a tanh soft-limiter
   slightly *regressed* the working cases (selftest EVM 0.022→0.080) without
   fixing BPSK. Measured per-symbol PAPR is ~equal across constellations
   (BPSK 11.2 dB, QPSK 11.3, 16-QAM 11.6, 64-QAM 10.9), so PAPR does not
   explain the BPSK failure. See entry 12.

4. **64-QAM uniform does not close on these channels.** Needs ~26 dB SINR;
   the measured palm-rest/ultrasonic channels sit at ~22 dB, giving EVM ~0.12
   (SINR ~19 dB) — below 64-QAM's requirement. 16-QAM r3/4 is the practical
   ceiling for the clean near-field channel.

5. **Stream-end fade kills the final OFDM symbol.** The macOS output chain
   tapers the last ~10 ms when a stream stops; with a frame-wide interleaver,
   one dead symbol contaminates every FEC block (raw BER 0.000 on symbols
   0–14, 0.264 on the final symbol). Fix: append ≥0.3 s trailing silence to
   the transmit waveform (part of the TX contract).

6. **Overconfident LLRs from one corrupted symbol poison Viterbi.** A single
   bad symbol in 16 (≈1.7% raw BER, trivially correctable in principle)
   dropped decoding to 0–1 of 6 blocks, because its wrong LLRs carried full
   confidence through the interleaver. Fix: weight each symbol's LLRs by its
   own pilot EVM² (soft erasure). This also gives burst-noise immunity.

## Spatial / transducer geometry

7. **Driving both Mac speakers with the same signal wrecks the link.** With
   the phone at the left palm rest, the right speaker arrives ~3× weaker with
   decorrelated phase; the L+R sum gives composite EVM ~0.5 (fails) vs ~0.06
   left-only. Transmit from the near speaker only (or do real per-subcarrier
   2×2 MIMO — blind summing is not MIMO).

8. **Direct chassis contact is WORSE than the soft cloth, not better.** No
   cloth (metal-to-metal palm-rest contact): median SNR 10.5 dB → QPSK,
   14.7 kbps. On ~½″ cloth: ~22 dB → 16-QAM, 36.5 kbps. Structure-borne
   coupling raises the noise floor / adds buzz; the cloth is acoustically
   beneficial, not incidental. (Quantifies the legacy walkthrough's warning.)

9. **Phone speakers cannot carry a coherent ultrasonic uplink (>18 kHz).**
   Both Pixel 7a AND iPhone 17 Pro Max speakers are phase-incoherent above
   ~18 kHz: single-tone STFT phase-jitter std 0.006–0.055 rad at 8 kHz vs
   ~11–19 rad (iPhone) / ~10–31 rad (Pixel) at 19.5–22 kHz, across 8 reps.
   They radiate ultrasonic *power* (a stationary multitone PSD reads 27.8 dB
   "SNR") but the phase is scrambled, so coherent OFDM/QAM yields EVM ~1.0
   (SINR ~0 dB). Power-per-bin ≠ phase coherence. An inaudible uplink needs
   non-coherent (MFSK/OOK) modulation. This is a general consumer-micro-speaker
   limit, not a single-device quirk.

10. **96 kHz sampling does not rescue the ultrasonic uplink — it's the
    transducer.** a2m (phone→Mac) in 18.5–21 kHz fails at 48 kHz too, so the
    96 kHz path is not the problem; entry 9 is.

## Modulation / coding choices

11. **Naive per-bin adaptive bit-loading UNDERPERFORMS uniform.** Several
    variants measured worse than uniform QPSK (14.74 kbps) on the 10.5 dB
    contact channel: per-bin at rate 3/4 → 0 blocks; flat-margin loading → 0;
    QPSK threshold set at 12 dB (too high) → 7.07 kbps (discards bins that
    uniform QPSK+FEC handles fine). Only a *calibrated* scheme won (16.15 kbps,
    +10%): QPSK floor at the FEC's actual reach (~5 dB), upgrades to 16/64-QAM
    on clearly-strong bins, **rate kept at 1/2**. Lesson: bit-loading thresholds
    must track measured FEC coding gain, not a flat SNR margin; pairing
    aggressive loading with a weaker code rate is a double hit.

12. **Mixed per-bin maps containing 1-bit (BPSK) bins are BROKEN (open bug,
    issue #4).** Fails even in clean digital loopback (uniform BPSK EVM ~125;
    failure scales with the count of 1-bit bins; mix {2,4} fine, mix {1,2}
    fails). Disproved: not PAPR, not clip shape, not peak-vs-RMS normalization
    (all reverted after measuring no fix). Sharp symptom: in a BPSK-heavy frame
    the sync symbols give |H| = 0.055 at the pilot bins while the data-symbol
    pilots arrive at |Y| = 4.27 (77× discrepancy), even noiseless — a
    receiver-side frame-alignment/windowing issue specific to BPSK, not a TX
    scaling problem. Worked around with a QPSK floor (no 1-bit bins).

13. **Coherent CP-OFDM cannot link in a reverberant desk geometry, at ANY
    MCS.** Phone off-chassis on a desk ~7″ from an elevated (5″ stand) laptop:
    16-QAM r3/4 → 0/375; QPSK r1/2 → 0 (EVM ~2); BPSK r1/2 + 32 ms CP → 0/125;
    NFFT 4096 / CP 2048 (43 ms) → 0 (EVM ~1.0). Not signal loss — chirp sync
    locks cleanly (matched-filter peak/mean 104) and broadband power is
    present. Cause: Schroeder delay spread 35.8 ms at −10 dB (82 ms at −20 dB),
    far beyond any practical CP; deep frequency-selective nulls. The only thing
    that linked here was non-coherent MT-FSK (89–267 bps). Lesson: when the
    *strong* delay spread exceeds the CP cap, stop climbing the OFDM ladder and
    switch waveform class (non-coherent MFSK / spread-spectrum), or reposition.

14. **`minimodem` above 300 baud fails over this acoustic channel.** Same-
    hardware baseline: 300 baud decodes (237 bps), but 600/1200/2400 baud
    return 1–2 garbage bytes — single-carrier FSK with no FEC/equalization dies
    in the desktop multipath. (Not a knock on minimodem; it targets a different
    channel. It illustrates why wideband + FEC is necessary here.)

15. **An adaptive sounder that punts to "reposition" is the wrong behavior.**
    First version refused to transmit when the channel looked hard. Corrected:
    always attempt the most robust feasible tier (down to a non-coherent floor);
    "reposition" is advisory text only.

## Platform / tooling gotchas

16. **Android silently records all-zero PCM when the activity is not
    top-visible.** A secure-lockscreen bouncer or an expanded notification shade
    triggers the audio policy's capture silencing (`dumpsys audio` shows
    `rec ... silenced`). Fix: `setShowWhenLocked(true)` + `setTurnScreenOn(true)`,
    `cmd statusbar collapse`, and a wake sequence before every capture. Battery
    saver at low charge re-dozes the screen despite `svc power stayon`.

17. **iOS records mono by default and devicectl denies launch on a locked
    device.** The HIL app's capture collapses to channel 0 (so two-mic MRC is
    unavailable on iOS until stereo capture is added — iOS 14+ supports it via
    the input data source `.stereo` polar pattern). `xcrun devicectl` requires
    the iPhone awake/unlocked, and iOS silences the mic when the app is not
    foreground (the iOS analog of entry 16).

18. **Signing: the project.yml default team `W4XG9926YS` is not a valid Xcode
    account on this Mac.** The working team is Primatech Paper Co LLC
    `V83C69HYSQ` (matches the installed Apple Development cert). With that +
    automatic signing + `-allowProvisioningUpdates`, device install/launch is
    non-interactive.

19. **Diagnostic tooling that doesn't share the system-under-test's exact
    config fabricates results.** An early debug script mixed module-level
    FFT/CP constants with a differently-configured modem and produced a
    fictitious "0 dB sync consistency" reading that misdirected the
    investigation for a round. Diagnostics must take the same `Config` object.

20. **Both microphones are captured but only mic 0 is decoded.** Android
    captures stereo (UNPROCESSED) but the decoder reads channel 0; iOS records
    mono. No diversity combining was in use until MRC was added
    (`demodulate_frame(rx, rx2=...)`, digitally validated, OTA pending a stereo
    receiver — issue #7). Left spatial-diversity goodput on the table.

## Measurement-integrity reminders (process, not physics)

- **Set-membership block verification overstates correctness.** Counting any
  CRC-valid block that appears in the expected set does not prove ordered
  stream reconstruction. Use ordered verification (block *j* == payload position
  *j* of the attributed frame).
- **A stationary multitone PSD overstates capacity for phase modulation.** See
  entry 9: PSD measured 27.8 dB while coherent OFDM got SINR ~0 dB.
- **Trailing silence is excluded from the goodput "span".** Honest for long
  streams (amortizes) but inflates short 5-frame runs vs a gross accounting;
  report both.
- **A "22 kbps capacity calculation" is not a measurement.** The predecessor
  stack claimed ~22 kbps from an arithmetic formula while actually measuring
  ~0.27 kbps OTA. Always quote measured, byte-verified goodput.
