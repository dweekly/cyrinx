# 20 kbps Bidirectional Acoustic Link — Working Notes

Fresh as of 2026-06-09. Effort: measured ≥20 kbps acoustic goodput Mac↔Pixel 7a.
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
