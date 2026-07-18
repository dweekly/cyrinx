# Spike 8d effective-SINR loading — 2026-07-18

## Outcome

The retained Rank 5 corpus cannot support a causal offline comparison of
alternate per-bin allocation policies. This spike therefore stopped before
computing a p10 goodput delta, recovery comparison, oracle regret, or
catastrophic-selection count. OTA is **not permitted** by this result, and no
code was integrated into `Sources/`.

The stop is high-confidence for identifiability, not for policy performance:

- four whole retained acquisitions contain one unique physically transmitted
  data profile, not the four required policies;
- every acquisition uses the same uniform 16-QAM, rate-1/2 waveform with SHA-256
  `9adf5ec0a296682e6e1382b755025ac6b9c84e42cb4d97bfc28e26e780dcda6b`;
- every probe reuses payload SHA-256
  `3d33a8692fdab11b792184efbd86a322fa0d2145336c68a381e6db0093db9384`;
- no P1/P2/P3 waveform, allocation descriptor, profile-ID displacement, or
  alternate-policy outcome was physically observed; and
- the R2 aggregate block result was already disclosed by the required Rank 5
  report. R2 is also an order-balanced repeat of the same reverse physical
  cell, not an independent device or geometry.

Masking bins from the recorded uniform codeword would not recreate the
different coded bits, constellation mapping, transmit power distribution, or
signalling airtime of a lower-loaded transmission. Treating that operation as
a counterfactual decode would manufacture performance evidence.

## Frozen evaluation contract

The [preregistration](../../artifacts/spikes/spike8d-effective-sinr-20260718/preregistration.json)
was written before raw-PCM, per-bin data, or alternate-policy inspection. The
committed Rank 5 report had necessarily exposed aggregate outcomes before this
freeze; that limitation is recorded rather than presenting R2 as blind.

The four frozen policies are:

1. uniform 16-QAM incumbent;
2. raw-PSD thresholds at 6 and 13 dB;
3. scalar pilot/GMI thresholds of 1.20 bits/symbol for QPSK and
   2.40 bits/symbol for 16-QAM; and
4. a per-bin noise-covariance-aware maximum-SINR variant, falling back to the
   scalar GMI policy when covariance is unavailable.

All retain the current constraint-length-7, rate-1/2 convolutional code.
Allocation signalling is frozen as a two-bit assignment for each of 935 bins
plus 128 metadata/CRC bits: three QPSK rate-1/2 OFDM symbols, or 0.176 seconds,
per 64-data-symbol activation. A 32-bit profile ID also displaces application
payload in every frame. The primary metric is p10 whole-cell net goodput with
setup, sounder, descriptors, gaps, padding, and failures included. Recovery
must be at least 98%.

The offline screen would require at least 2% p10 gain before OTA. Final
promotion remains at least 5%, at least 98% recovery, no hash-invalid transfer,
and seven wins in eight confirmatory pairs after signalling.

## Payload-independent estimator

[`effective_sinr_loading.py`](../../scratch/hw20k/effective_sinr_loading.py)
implements research-only estimators for:

- Gray QPSK and 16-QAM AWGN bitwise GMI using deterministic Gauss-Hermite
  quadrature;
- empirical per-bin bitwise GMI from held-out randomized known QAM symbols,
  with channel/noise fitting required on earlier symbols;
- the frozen raw-PSD and conservative GMI allocation thresholds; and
- regularized per-bin `hᴴR⁻¹h` maximum-SINR combining for independently
  verified microphones.

The module rejects same-frame outcome keys, has no ADB, hardware, audio, or
subprocess import, and consumes only causally prior sounder features. Nine
deterministic tests cover GMI bounds and monotonicity, known-symbol GMI,
threshold boundaries, covariance arithmetic, leakage rejection, import
isolation, corpus stopping, and acquisition-plan balance.

The retained repeated-QPSK sounder can exercise only a descriptive scalar
approximation. It cannot validate GMI against alternate delivered profiles.
For example:

| Path | Descriptive policy | Off | QPSK | 16-QAM | Active |
|---|---|---:|---:|---:|---:|
| Mac speaker 0 → Pixel mic 0, F0 | scalar GMI surrogate | 434 | 406 | 95 | 53.58% |
| Pixel output 1 → Mac mic, R1 | scalar GMI surrogate | 22 | 252 | 661 | 97.65% |

These are allocation counts, not rates. The raw-PSD policy is unavailable
because the retained passive analysis has only coarse band powers rather than
an aligned per-bin PSD; repeated-pilot SNR is not relabeled as raw PSD. The
covariance-aware policy likewise falls back to scalar GMI because the retained
capture has only broadband covariance. The large directional difference also
illustrates why one global stationary-PSD rate claim would be unsafe.

## Minimum new acquisition

The generated
[acquisition plan](../../artifacts/spikes/spike8d-effective-sinr-20260718/minimum-ota-acquisition-plan.json)
contains 32 deterministic jobs: four position-balanced rounds of all four
policies in one calibration cell and one outcome-blind 20 mm lateral holdout
cell. Every job has a unique committed payload seed. Each round requires:

1. route verification and passive noise;
2. eight known QPSK sounder symbols;
3. 32 randomized known 16-QAM probe symbols, with 16 used to fit the
   channel/noise model and 16 held out for bitwise GMI;
4. a hashed policy-selection record written before payload; and
5. all four physical candidate captures behind one decode barrier, so no
   candidate outcome can affect another candidate in that round.

This is an identifiability screen, not the final seven-of-eight qualification.
The plan is machine-validated but deliberately has `ready=false` and
`ota_permitted=false`. Before physical execution, it still needs a
deterministic mixed 0/2/4-bit oracle/decoder, validated descriptor accounting,
and a runner that enforces the pre-payload commit and decode barrier. The Pixel
was not contacted and no sound was emitted during this spike.

## Reproduction

From the repository root, using the repository virtual environment:

```sh
.venv/bin/python scratch/hw20k/test_effective_sinr_loading.py
.venv/bin/python scratch/hw20k/effective_sinr_loading.py validate-acquisition \
  artifacts/spikes/spike8d-effective-sinr-20260718/minimum-ota-acquisition-plan.json
```

The [offline audit](../../artifacts/spikes/spike8d-effective-sinr-20260718/offline-audit.json)
contains hashes for all 21 retained inputs plus the estimator source. Raw PCM
was hashed but not semantically inspected. The audit records one unique physical profile, one
unique payload hash, zero alternate-policy outcomes, and null performance
metrics rather than extrapolated values.
