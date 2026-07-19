# Rank 5 Pixel/Mac sounding matrix — 2026-07-18

## Outcome

This bounded Rank 5a/5b/5c spike produced useful route, passive-noise,
phase-continuous forward-matrix, Mac self-path, and repeated reverse-path
evidence. It did **not** pass its promotion gate and nothing was integrated into
the library.

The most important findings were:

- The Pixel exposed a stable 48 kHz, two-channel `UNPROCESSED` route throughout
  passive and forward acquisition. Logical channel 0 mapped directly to the
  bottom microphone (ID 23); channel 1 mapped directly to the back microphone
  (ID 24).
- The two Pixel inputs were not exact or delayed/scaled duplicates in this
  cell. Passive correlation was 0.488; the active delayed-copy correlations
  were 0.397 and 0.641 for the two Mac speaker columns. This supports retaining
  both rows, but does not by itself prove broadly usable diversity.
- Mac logical speaker 0 was the only useful forward bulk path at the
  preregistered volume-30 screen. It delivered 50/50 strict blocks to Pixel
  microphone 0. Pixel microphone 1 delivered 0/50, and both logical-speaker-1
  paths delivered 0/50. Two-microphone MRC preserved 50/50 for speaker 0 and
  did not rescue speaker 1.
- The volume-30 forward linearity gate failed. The volume-50 calibration and
  held-out forward repeat were therefore not run. The primary forward
  repeatability metric remains unevaluated.
- In the reverse direction, Pixel logical output 1 delivered 43/50 and 45/50
  strict blocks in order-balanced repeats. Logical output 0 delivered 0/50 in
  both. Physical speaker labels remain unknown because the Android playback
  primitive lacks strict realized-output-route provenance.
- Repeated-pilot capacity arithmetic was not a reliable rate predictor. It
  described roughly 65–69 kbps on reverse paths where the randomized data
  probe delivered either 0 or 22.016–23.040 kbps. These values are explicitly
  not application goodput and are not a Shannon-limit measurement.

Machine-readable values and artifact hashes are in
[`results-ledger.json`](../../scratch/hw20k/evidence/rank5-pixel-mac-20260718/results-ledger.json).

## Preregistered cell

The Pixel 7a remained face-up in the user-established near-field position over
the MacBook function-key area. HVAC state was unknown, so it is not asserted in
the results. Mac playback and recording were restricted to the built-in
speakers and microphone; Elgato devices were never selected.

The preregistration was frozen before playback with SHA-256
`582d1a16b0e60590de9defeeee95d0c0e3e0ef08300cdc86456e16a049bb0521`.
Each matrix used one continuous 19.481-second stereo render. Each speaker was
excited alone by:

1. the existing repeated-marker tone, THD, and IMD program;
2. an eight-symbol known-QPSK OFDM sounder; and
3. a four-second randomized 16-QAM, rate-1/2 portable-C data probe.

The waveform peak was 0.18 and RMS was 0.03844 per active output. Left-to-right
and right-to-left renders had fixed hashes. The inactive output channel was
exact digital zero during every sequential column.

## Passive measurement

The Pixel room-tone RMS values were 0.000695 and 0.000632. The covariance
matrix was:

```text
[[4.833e-7, 2.141e-7],
 [2.141e-7, 3.989e-7]]
```

No near-ultrasonic interferer was detected by the existing heuristic. This is
a digital receiver-level observation, not an SPL measurement or proof that the
room was quiet.

## Forward matrix and stopped escalation

The F0 matrix used Mac volume 30 and a single phase-continuous left-to-right
render. Before and after playback the endpoint was verified as `MacBook Pro
Speakers` / `BuiltInSpeakerDevice`; the prior Stage V2 default output and
volume were restored. Pixel capture peaks were 0.00894 and 0.00754 with zero
full-scale samples. The route remained exact and stable.

| Mac logical speaker | Pixel receiver | Strict probe blocks | Probe rate | Repeated-pilot SNR | −15 dB spread |
|---:|---:|---:|---:|---:|---:|
| 0 | bottom mic / ch0 | 50/50 | 25.600 kbps | 5.07 dB | 24.54 ms |
| 0 | back mic / ch1 | 0/50 | 0 | −7.34 dB | 40.60 ms |
| 1 | bottom mic / ch0 | 0/50 | 0 | 3.50 dB | 42.29 ms |
| 1 | back mic / ch1 | 0/50 | 0 | −0.45 dB | 39.77 ms |

“Probe rate” is strict recovered bytes divided by the fixed four-second data
probe. It excludes discovery, sounding, setup, ARQ, and retransmission and is
therefore not application goodput.

The conservative distortion screen failed. Some low-frequency IMD bounds were
noise-limited—for example speaker 0 to receiver 0 had raw IMD of −28.3 dBc but
a conservative −10.3 dBc bound because the product frequencies overlapped
strong room noise. The weaker back-microphone paths also had non-noise-limited
failures, including raw IMD of −14.9 dBc and −2.53 dBc, and the latter path
changed gain by 5.77 dB within a stage. The frozen rules prohibited treating a
noise-limited bound as a pass. F1/F2 at volume 50 were skipped.

This exposed an experimental-design issue: a fail-closed distortion gate is
appropriate, but low-frequency IMD products need a quieter or instrumented
reference method if they are to gate a wideband near-field modem without
frequent inconclusive stops.

## Local self-characterization

The Pixel local cell stopped before playback. Android AudioService logged the
request to change media volume from 25 to 15, but the realized speaker-route
index remained 25. The preregistration required a confirmed and restorable
volume transaction, so `playrec_pcm` was not launched.

Mac same-device full duplex did run at volume 30. Logical speaker 0 to the
built-in microphone delivered 50/50 strict blocks with a 1.04 ms −15 dB delay
spread. Logical speaker 1 delivered 0/50 with a reported 42.25 ms spread. This
large asymmetry should be rechecked before using the Mac self-path as a device
profile.

## Bounded reverse matrix

The Pixel-to-Mac branch ran at the already-realized media volume 25. The Mac
built-in microphone capture peaks were 0.700 and 0.693. Speaker order was
reversed in the held-out repeat.

| Output | Blocks A | Blocks B | Probe kbps A/B | Magnitude Δ median/p90 | Phase σ | Spread ms A/B |
|---:|---:|---:|---:|---:|---:|---:|
| 0 | 0/50 | 0/50 | 0 / 0 kbps | 0.94 / 2.21 dB | 12.56° | 16.90 / 14.67 ms |
| 1 | 43/50 | 45/50 | 22.016 / 23.040 kbps | 0.26 / 0.52 dB | 2.25° | 1.06 / 1.08 ms |

Only 315/935 and 413/935 bins, respectively, cleared the frozen 12 dB SNR rule
in both repeats. This is below the 80% promotion threshold. The logical-output-1
response was nevertheless quite repeatable and is worth preserving as a
candidate reverse control/data bearer after output routing is made explicit.

## Relationship to the Rank 11a MIMO acquisition

F0 supplies part of the integrity evidence requested by the separate Rank 11a
plan: one continuous stereo render, one continuous interleaved Pixel stereo
capture, both direct microphone rows, sequential speaker columns, passive noise
covariance, exact drive metadata, and retained raw PCM.

It does not satisfy the Rank 11a experiment. It used CP768 rather than CP240,
sequential rather than orthogonally coded columns, no simultaneous randomized
two-stream probe, peak-matched per-speaker drive rather than fixed-sum power,
and only one forward repeat. No MIMO rank, beamforming gain, or two-stream
goodput claim follows from this spike.

## Tooling findings and next gate

Two tooling gaps blocked stronger conclusions:

- the tone checker referenced a missing shared-interleaved-clock helper; the
  helper and a deterministic regression test were added; and
- Android needs an app-owned arbitrary media-volume transaction and a strict
  realized playback-route signature for both `play_pcm` and `playrec_pcm`.

The next Rank 5 acquisition should be separately preregistered after those
Android controls exist. It should use a distortion screen whose low-frequency
noise floor can distinguish “inconclusive” from nonlinear, then collect at
least one calibration and one held-out forward matrix. Only after that gate
passes should the Rank 11a orthogonal, fixed-sum-power campaign run.

Raw PCM and detailed per-bin analyses remain under
`artifacts/spikes/rank5-pixel-mac-20260718/` in the acquisition workspace. They
are hashed in the results ledger and intentionally are not shipping-library
fixtures.
