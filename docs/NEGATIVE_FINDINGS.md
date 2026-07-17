# Negative Findings — Things That Did Not Work (and Why)

Fresh as of 2026-07-17. A durable record of dead ends, disproved hypotheses,
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

4. **The original conclusion “uniform 64-QAM does not close” was too broad;
   high-rate 64-QAM is what failed.** In the June profile, 64-QAM r3/4 decoded
   0/339 blocks at EVM 0.173. Cyrinx 2.0 later closed uniform 64-QAM by using
   CP96, fewer pilots, rate 2/3, and pilot-local LLR weighting. That does not
   exonerate aggressive coding: the prospective accepted-class result recovered
   4,215/4,280 blocks (98.4813%), below its 99.9667% baseline, while r3/4 and
   r5/6 screens were substantially worse. The takeaway is to quote the complete
   modulation/coding/pilot/CP profile, not a constellation-only “ceiling.”

5. **Stream-end fade kills the final OFDM symbol.** The macOS output chain
   tapers the last ~10 ms when a stream stops; with a frame-wide interleaver,
   one dead symbol contaminates every FEC block (raw BER 0.000 on symbols
   0–14, 0.264 on the final symbol). Fix: append ≥0.3 s trailing silence to
   the transmit waveform (part of the TX contract).

6. **Overconfident LLRs from one corrupted symbol poison Viterbi.** A single
   bad symbol in 16 (≈1.7% raw BER, trivially correctable in principle)
   dropped decoding to 0–1 of 6 blocks, because its wrong LLRs carried full
   confidence through the interleaver. The 2026-06 fix used one global pilot
   EVM² value per symbol. Cyrinx 2.0 retains that burst-erasure signal and adds
   known-pilot-only local-frequency residual weighting. On the same fresh
   captures, local weighting recovered 4,215 blocks versus 3,868 for the frozen
   global-only decoder (+347; all eight candidate runs improved, none
   regressed). Payload, decoded bits, and CRC outcomes must remain outside this
   reliability estimate.

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
   (SINR ~0 dB). Power-per-bin ≠ phase coherence. An inaudible uplink on these
   routes needs non-coherent (MFSK/OOK) modulation. The failure reproduced on
   both tested phone models; two models do not establish a universal
   consumer-micro-speaker limit.

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

20. **“Both microphones are captured but only mic0 is decoded” is now a
    historical limitation, not current Pixel behavior.** Android captures the
    two direct Pixel microphones with 48 kHz stereo `UNPROCESSED`, and the C
    receiver can select mic0 or MRC using held-out known pilots. Selection on
    payload, decoded data, ordinary data-bin EVM, or CRC would leak outcome
    information and invalidate the benchmark. iOS stereo capture and broader
    device-policy validation remain incomplete.

21. **CP48 is below the retained reliability-qualified CP floor for this Pixel
    route, even though it can occasionally look fast.** Two opposite-order
    one-pair screens produced 93/107 blocks
    (63.277 kbps) and 64/107 (43.546 kbps), while both paired CP240 baselines
    recovered 75/75. That order sensitivity is precisely why the faster single
    observation was not promoted. CP96 is the retained Cyrinx 2.0 choice.

22. **Weakening FEC above rate 2/3 did not buy usable goodput in the tested
    Pixel screens.** A 64-QAM r3/4 screen recovered 87/121 blocks (57.925 kbps)
    against a 75/75
    baseline. The 64-QAM r5/6 screens recovered only 24/134 at volume 50 and
    21/134 at volume 60 (15.979 and 13.982 kbps). More drive did not rescue the
    weaker code. A rate near 0.70, between 2/3 and 3/4, is a future hypothesis,
    not a measured result.

23. **Extending the lower band edge to 600 Hz failed its predeclared screen and
    provided no evidence of added capacity.** The candidate recovered 60/110
    blocks and delivered 39.948 kbps, versus 77/77 for its paired conservative
    baseline. There was no simultaneous same-MCS 1.1 kHz control or reverse-
    order run because the branch stopped at its declared gate, so this does not
    isolate the lower edge causally. It is sufficient evidence not to promote
    the 600 Hz branch.

24. **The tested longer-frame and sparser-pilot campaigns did not produce a
    monotonic amortization win.** In separate zero-gap campaigns, pilot spacing
    16 at 64 symbols measured 69.110 kbps and 97.009% block success; spacing 32
    measured 68.960 kbps and 93.311%; spacing 64 with 96 symbols measured
    69.652 kbps and 90.666%. A 128-symbol, spacing-16 campaign measured
    66.102 kbps and 89.093%.
    Across separate sessions/configurations, that observed mean was only
    ~0.34% above the fresh 64-symbol, 250 ms-gap result; it is not a causal
    estimate of either length or gap. The candidate's paired advantage decayed
    from ~24.6 kbps in the first pair to 6.1–8.7 kbps in the final two. The
    baseline remained near 48.9 kbps. The failure is consistent with
    time-varying channel error that high-order QAM exposes, not fixed
    transaction overhead. A randomized length/gap factorial remains required.

25. **A naïve fixed-comb pilot tracker is not a complete channel-drift fix.**
    Re-observing the same pilot frequencies each symbol leaves the channel
    between them interpolated, so frequency-local changes can remain invisible.
    Making hard 64-QAM decisions part of the update can also propagate an early
    error. Decision-directed tracking at `alpha = 0.05` is therefore research
    only; it has not earned a prospective headline. The controlled next test is
    a denser or frequency-staggered known-pilot lattice, optionally paired with
    a code rate near 0.70.

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
- **Inter-frame gaps define a measurement class.** The accepted 36.571 kbps and
  Cyrinx 2.0 65.875 kbps results both use four 12,000-sample gaps. A zero-gap
  result is useful but must be labeled separately; it is a scheduling change,
  not an algorithmic throughput gain.
- **A "22 kbps capacity calculation" is not a measurement.** The predecessor
  stack claimed ~22 kbps from an arithmetic formula while actually measuring
  ~0.27 kbps OTA. Always quote measured, byte-verified goodput.
