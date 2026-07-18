# Spike 8c localized DFT-spread OFDM result

This was an offline, preregistered screen. It did not use a speaker,
microphone, Android device, ADB, or live audio.

## Decision

OTA is **NOT PERMITTED** under the frozen gate. Library integration remains
unjustified regardless of this screen because no OTA or on-device protection
behavior was measured.

## Held-out result

- Frames: 8000 held out (10000 total).
- Pre-limiter payload crest-factor q99.9: 15.1532 dB conventional OFDM versus 12.3757 dB localized DFT-spread OFDM; reduction 2.7775 dB (gate >= 3.0 dB: False).
- Coded-RIR paired bootstrap 95% interval, candidate minus incumbent: [0.005938, 0.022031]; worst RIR-cell point difference -0.005000 (gate: True).
- Spectral out-of-band delta: -0.1664 dB; guard-peak delta -0.2042 dB (gate: True).
- CRC-probe recovery: 0.583500 conventional versus 0.590125 candidate.
- Median EVM RMS: 0.153392 conventional versus 0.150947 candidate.
- Equal-peak RMS: 0.05449671 conventional versus 0.05455235 candidate; mean-square 0.0029698918 versus 0.0029759648.
- Accumulated Python TX time: 5.262 s conventional versus 7.143 s candidate. Accumulated RX time: 12.888 s versus 14.663 s. These are prototype timings, not mobile real-time estimates.

## Interpretation and limits

The candidate used one frozen unitary 935-point localized DFT and logical comb
pilots. No pilot or mapping change was made after preregistration. The CRC
metric is one rate-2/3 convolutionally coded, CRC-protected 256-byte probe per
full 64-symbol frame; it is not the production 107-block frame decoder or a
goodput measurement.

There was no measured room impulse response in the tracked checkout. The two
"golden" responses are deterministic modem fixtures and the other responses
are declared synthetic models. Symbol timing was known, and CFO, SRO,
acquisition, clock drift, speaker/microphone distortion, OS protection DSP,
THD, intermodulation, clipping, and acoustic exposure were not measured.
Consequently, even a passing offline result would only justify designing a
bounded OTA screen, not integration or a throughput claim.

## Reproduction

```bash
.venv/bin/python scratch/spikes/dfts_ofdm_8c/test_spike.py
.venv/bin/python scratch/spikes/dfts_ofdm_8c/spike.py run
```

Preregistration SHA-256: `4bc76ad6297d5719dc58c83365a8382aaff6e269fb1ec6be58e5d6fbc7a9c221`

Implementation SHA-256: `9a1760c4c851a7670956b120e584231ce5a6101a2577ca9e036dcbddf363c18d`

Campaign wall time: 105.228 s.
