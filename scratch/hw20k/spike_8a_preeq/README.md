# Rank 8a retained-capture pre-equalization spike

This directory contains an offline-only screen of regularized per-speaker
pre-equalization. It does not use ADB or open an audio device, and it does not
change the shipping modem under `Sources/`.

The preregistration was frozen at SHA-256
`6b8f5bd0c47b081f35062343d22c93ee7f2297f86827714d6123bb4f084934e5`
before candidate outcomes were inspected. It uses whole-pair, order-balanced
splits from the July 17 Pixel 7a campaign:

- profile fitting and selection: pairs 0, 3, 4, and 7;
- held-out screen: pairs 1 and 6;
- held-out confirmation: pairs 2 and 5; and
- external stability: all eight pairs from the independent July 16 campaign.

Both retained Pixel microphones are included. All captures use the same
nominal face-up near-field pose with thermostat-cycling A/C, so the corpus does
not supply the three placement offsets required for full ROADMAP Spike 8a.

## Result

The fit-only selection chose the smooth minimum-phase profile with Tikhonov
factor 0.3 and the 3 dB cap. Its realized maximum inversion gain is only
1.487 dB. It did not pass the frozen continuation gate.

| Whole-run subset | Aggregate p10 SINR change | Mic 0 p10 change | Mic 1 p10 change |
|---|---:|---:|---:|
| Held-out screen | +0.164 dB | -0.219 dB | +0.490 dB |
| Held-out confirmation | +0.255 dB | -0.528 dB | +0.544 dB |
| External campaign | -0.956 dB | -0.609 dB | -0.972 dB |

The preregistered screen required at least +1 dB aggregate p10 gain and
nonnegative p10 GMI on each microphone. The candidate missed the gain floor,
regressed the primary bottom microphone, and reversed on the external
campaign. Equal-peak normalization held the waveform at 0.18. Median
out-of-band energy changed by -0.573 dB, -0.493 dB, and -0.170 dB in the three
subsets, respectively, so spectral regrowth was not the failed gate. Matched
RMS was effectively identical and required a worst observed peak of about
0.18003; it did not change the conclusion.

The linear frequency replay was then passed through the exact retained C
decoder. Synthetic flat replay reproduced all 80 training frame/policy block
masks exactly (20 frames across mic 0, mic 1, MRC, and the automatic policy).
Across all eight development pairs, the automatic-policy candidate projection
was 3,752/4,280 ordered blocks, 87.664% and 58.639 kbps, versus the observed
flat result of 4,215/4,280, 98.481% and 65.875 kbps. The candidate numbers are
counterfactual predictions, not OTA measurements.

The complex fixed-route inverse was not evaluated. After bulk-delay and
common-phase alignment, mic 0 passed the phase gate on 99.893% of bins. Mic 1
passed on only 47.701%; its p10 coherence was 0.891 and its p90 circular phase
standard deviation was 27.53 degrees. A single stable two-microphone phase
reference was therefore not established.

One analysis audit materially affected the implementation. Treating all
post-sync channel evolution as additive residual makes ordinary CPE/timing
drift depend spuriously on the flat QAM phase when a complex profile is tested.
The final model instead estimates per-symbol amplitude, CPE, and linear phase
only from the known comb pilots, then retains everything else as the residual.
Flat synthesis remains algebraically exact, and the exact C block-mask check
guards the replay construction.

## Reproduce

Use the repository virtual environment:

```sh
.venv/bin/python scratch/hw20k/spike_8a_preeq/pre_eq_replay.py \
  --output-dir scratch/hw20k/spike_8a_preeq/results
```

The retained corpus is intentionally not duplicated into git. Override
`--development-root` and `--external-root` if the local artifact directories
move; the program still verifies the frozen dry-run manifest hashes and exact
eight-pair structure.

The machine-readable OTA acquisition manifest includes an offline waveform
builder. It produces PCM and metadata but never plays audio:

```sh
.venv/bin/python scratch/hw20k/spike_8a_preeq/emit_pre_eq_burst.py \
  --profile scratch/hw20k/spike_8a_preeq/results/profile.json \
  --payload FRAME0.bin --payload FRAME1.bin --payload FRAME2.bin \
  --payload FRAME3.bin --payload FRAME4.bin \
  --output candidate-stereo-f32le.pcm
```

## Claim boundary and next gate

This result supports stopping this exact smooth profile. It does not establish
that every pre-equalizer is harmful. The selected fit objective traded a small
weak-microphone improvement against a primary-microphone regression, which is
not useful to the current MRC/automatic receiver. A future profile family
should be separately preregistered around the causal MRC or automatic-policy
objective rather than reopening this result.

No retained capture measures pre-EQ OTA goodput, three-offset stability, THD,
intermodulation, speaker/OS protection, calibrated SPL, LAeq, LCpeak, or
exposure. If a redesigned offline profile clears its gate, the next OTA screen
is two balanced flat/EQ pairs at each of centered, -20 mm, and +20 mm lateral
offsets, followed only on success by at most eight confirmatory pairs. The
exact acquisition contract is in
`results/ota-acquisition.manifest.json`.
