# Ultrasonic-Band (Inaudible) Bulk PHY — Measured Results

Fresh as of 2026-06-09. Status: complete. Branch `ultrasonic-band`, PR #1.

Question: can the bulk-PHY methodology of
[ACOUSTIC_BULK_PHY.md](ACOUSTIC_BULK_PHY.md) run entirely in the inaudible
band (>= ~18.5 kHz), and how much goodput does each direction sustain?
96 kHz sampling allowed.

## Headline result (asymmetric, transducer-bound)

| Direction | Inaudible band | Result |
|---|---|---|
| **Mac → Pixel 7a** | 18.5–23.9 kHz, 96 kHz | **~9 kbps verified** (16-QAM r3/4, 39/39 blocks, EVM ~0.11) |
| **Pixel 7a → Mac** | 18.5–21 kHz | **does not close** — Pixel micro-speaker is phase-incoherent above ~18 kHz |

The inaudible-band link is strongly asymmetric, exactly as the physical
review predicted. Mac→Pixel works because the MacBook tweeter is
phase-coherent to 24 kHz. Pixel→Mac fails because the Pixel 7a speaker,
while it radiates ultrasonic *power*, cannot reproduce it with the phase
coherence that OFDM/QAM (or any phase modulation) requires.

## The decisive measurement: Pixel speaker phase coherence vs frequency

Single-tone, 2 s, Pixel speaker → Mac mic, STFT phase of the fundamental
detrended and its standard deviation reported (low = coherent):

| Tone | Drive amp | Phase-jitter std | Interpretation |
|---|---|---|---|
| 8 kHz   | 0.9  | 0.055 rad | clean, coherent (audible band works) |
| 19.5 kHz | 0.9 | 0.01–2.16 rad | **intermittent** — coherent some runs, not others |
| 19.5 kHz | 0.4 | 0.02 rad | coherent when driven |
| 19.5 kHz | 0.15 | 2.25 rad | incoherent (near noise floor) |
| 22 kHz  | 0.9 / 0.4 / 0.15 | 4.06 / 3.51 / 2.57 rad | reliably **incoherent** at all levels |

THD is low at 19.5 kHz (harmonics fall above Nyquist), so this is not
classic distortion — it is phase/timing instability, plausibly the smart-amp
excursion-protection DSP or mechanical behavior of the micro-speaker near its
upper limit. OFDM's high peak-to-average ratio repeatedly drops the
instantaneous amplitude into the incoherent regime, so even the marginally
coherent 18.5–20 kHz sub-band will not carry coherent QAM reliably.

This also resolves an apparent contradiction: a stationary multitone PSD
measures **27.8 dB "SNR"** for Pixel→Mac at 18.5–21 kHz (power is genuinely
there), yet coherent OFDM demodulation yields **EVM ~1.0 (SINR ~0 dB)**.
Power-per-bin and phase coherence are different things; PSD-based channel
surveys overstate capacity for phase-modulated waveforms on this transducer.

## Mac → Pixel inaudible: what works

- 96 kHz, NFFT 2048, CP 768 (8 ms), chirp preamble moved into 18.6–23.4 kHz,
  band 18.5–23.9 kHz (~100 data bins), 16-QAM rate 3/4.
- ~9 kbps verified goodput, EVM ~0.11, all blocks recovered across frames.
- Channel SNR (sync-symbol estimate): median ~22 dB, falling to ~16 dB above
  23 kHz. Firmly 16-QAM territory; uniform 64-QAM fails (EVM ~0.12 → SINR
  ~19 dB, below 64-QAM's need). Per-bin adaptive loading does not raise it
  because almost every bin sits in the same ~22 dB band.
- Ceiling is bandwidth, not modulation: a ~5.4 kHz coherent band at ~22 dB
  caps near 9–10 kbps. Reaching 20 kbps inaudible would require either more
  coherent ultrasonic bandwidth than this Pixel offers, or dropping below
  18.5 kHz (audible).

## Techniques added in this effort (kept in modem.py / harness)

- 96 kHz capture/playback verified on both devices (Pixel `AudioRecord`/
  `AudioTrack` UNPROCESSED @96k; Mac sounddevice @96k).
- Sample-rate- and band-parameterized `Config` (chirp frequencies, CP, NFFT,
  sample rate all per-config) so one modem serves audible and ultrasonic.
- **Receive Butterworth band-pass** before coarse sync: with the data band
  isolated, the chirp matched filter is not swamped by audible room noise
  (chirp correlation quality rose 0.17 → 0.54). The OFDM demod is already
  bin-selective; only coarse sync needed this.
- **Decision-directed per-bin channel tracking** (`track_alpha`): blends the
  pilot+data residual into the channel estimate each symbol, since the
  acoustic channel ages within ~10 symbols (measured: residual EVM degrades
  from 38 dB at 1-symbol lag to 23 dB at 16-symbol lag). Improved m2a
  ultrasonic EVM 0.20 → 0.11.
- Single-tone phase-coherence and Schroeder energy-decay diagnostics
  (`ultra_characterize.py` and inline scripts).

## Environment validation (room is quiet, per operator)

Room-tone capture confirmed the ultrasonic band is near-silent: in-band
(18.5–21 kHz) RMS ~5e-8 on the Pixel mic and ~4e-8 on the Mac mic. a2m's
failure is therefore not ambient noise; it is the transmit transducer.

## Conclusion

Honest answer to the inaudible-band question: **feasible one way, not the
other, on this hardware.** Mac→Pixel sustains ~9 kbps inaudibly. Pixel→Mac
in the inaudible band would need a fundamentally different, non-coherent
modulation (power/energy-based MFSK or OOK, as ggwave uses) to tolerate the
micro-speaker's phase incoherence — at much lower rates than the audible-band
27 kbps. For a symmetric inaudible link, the binding constraint is the mobile
speaker, not the protocol.

## Reproduce

```
.venv/bin/python3 scratch/hw20k/ultra_characterize.py      # 96k channel survey
.venv/bin/python3 scratch/hw20k/ultra_test.py m2a qam16-34 0.95 768 96
```
