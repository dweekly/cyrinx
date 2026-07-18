# Rank 5a capacity/predictor replay — 2026-07-18

## Outcome

This offline-only spike found that same-probe bitwise GMI was a materially
better *descriptive discriminator* of strict block recovery than either
passive-noise Shannon arithmetic or the earlier repeated-pilot arithmetic.
It did not produce an adaptation-qualified predictor and nothing was
integrated into the library.

Across ten unique mono path observations, Spearman correlation with strict
block-recovery fraction was:

| Predictor | Spearman rho |
|---|---:|
| Actual-LLR bitwise GMI | 0.8842 |
| AWGN-equivalent effective SINR derived from that GMI | 0.8842 |
| Passive stationary-noise Shannon upper bound | 0.5278 |
| Repeated-pilot `log2(1+SNR)` arithmetic | 0.4729 |

The GMI result is promising but not sufficient. The preregistered rule that
declared the 16-QAM rate-1/2 probe feasible whenever optimized GMI exceeded
the exact 0.499971 configured information-bit rate still made two
catastrophically optimistic selections in the calibration/descriptive data:
reverse R1 output 0 recovered 0/50 blocks at GMI 0.5133, and output 1
recovered 43/50 at GMI 0.6958. This is evidence that a frame-average
information statistic and an asymptotic code-rate threshold do not by
themselves model finite-frame convolutional-code recovery, bursts, or the 98%
recovery objective.

Only R2 was held out from implementation and threshold choices. Its two
speaker paths are one correlated physical run, not two independent cells. In
that limited repeat, GMI rejected the 0/50 output-0 path and selected the 45/50
output-1 path. Mean absolute fixed-profile goodput error was 1.280 kbps for
the GMI rule versus 14.080 kbps for each Shannon-arithmetic rule. The latter
result cannot be treated as held-out qualification because the sample is one
device, one geometry, one route, and one run.

Machine-readable evidence is in
[`results.json`](../../artifacts/spikes/rank5a-capacity-predictor-20260718/results.json)
and
[`summary.json`](../../artifacts/spikes/rank5a-capacity-predictor-20260718/summary.json).

## Frozen analysis contract

The predictor preregistration was frozen before any candidate outcome was
calculated. Its SHA-256 is
`ece043f5c94e96643ac3e9235525a594c7d7450942438b3f74781c860d357fbd`.
It fixed:

- all forward, reverse-repeat, Mac-self, and scalar-input SIMO observations;
- the passive-noise FFT and covariance regularization;
- the portable-C receiver reliability semantics to reconstruct;
- bitwise GMI and effective-SINR definitions;
- the exact fixed-profile feasibility rules;
- strict-recovery comparison and catastrophic-miss definitions; and
- a stop condition requiring at least five independent held-out physical
  cells across at least two devices before adaptation could be promoted.

No recovery mapping was fitted to the physical observations. Optimizing the
single nonnegative LLR scale is part of the declared mismatched-decoder GMI
definition; it is not a fitted block-recovery model. R2 did not select a
threshold or alter the implementation.

The frozen document is
[`preregistration.json`](../../artifacts/spikes/rank5a-capacity-predictor-20260718/preregistration.json).

## Exact probe schedule

Every observation used the already-retained randomized 16-QAM, convolutional
rate-1/2 probe:

| Property | Value |
|---|---:|
| Sample rate | 48,000 Hz |
| FFT / CP | 2,048 / 768 samples |
| Occupied band | 1.1–23 kHz |
| Occupied / pilot / data bins | 935 / 117 / 818 |
| Data symbols | 64 |
| Coded bits per symbol | 3,272 |
| Coded-bit slots per frame | 209,408 |
| Information bits | 104,698 |
| Exact configured information-bit rate | 0.499971 |
| Preamble and guard | 4,096 + 2,048 samples |
| Frame duration | 192,000 samples = 4.000 s |
| Strict payload | 50 × 256 = 12,800 bytes |
| Scheduled payload ceiling | 25.600 kbps |
| Inter-frame gap inside the probe | 0 s |

The probe rate is not application goodput. It excludes discovery, sounding,
negotiation, ARQ, and retransmission. The schedule ceiling is the maximum for
this exact probe, not a claim about the acoustic channel's capacity.

## Measurement inventory

All 12 observations contained the actual randomized known-QAM probe, retained
an ordered portable-C block-validity mask, and had a passive-noise estimate.
The offline Python analysis reconstructed the receiver-semantics-v1 sync,
equalization, pilot phase fit, 25:75 global/local known-pilot reliability
blend, and max-log LLR stream. Its decoded ordered block mask matched the
retained portable-C block mask in every observation. Because the public C API
does not expose raw LLRs, this mask parity is required before the reconstructed
LLRs are called receiver-matched actual LLRs.

| Observation class | Paths | Random QAM | Receiver-matched LLRs | Passive noise | Active `H[k]` | Qualified stable `H[k]` | Strict masks |
|---|---:|---|---|---|---|---|---|
| F0 Mac→Pixel mono | 4 | yes | yes, C-mask parity | Pixel per-bin 2×2 covariance; diagonal used for mono | yes | no held-out repeat | yes |
| F0 Mac→Pixel MRC diagnostics | 2 | yes | yes, C-mask parity | full Pixel per-bin 2×2 covariance | yes | no held-out repeat | yes |
| R1/R2 Pixel→Mac mono | 4 | yes | yes, C-mask parity | Mac scalar per-bin covariance | yes | no: only 33.7%/44.2% of bins passed the repeat screen and route provenance is incomplete | yes |
| Mac self mono | 2 | yes | yes, C-mask parity | Mac scalar per-bin covariance | yes | no held-out repeat | yes |

The P0 passive capture is from the same campaign, not simultaneous with each
data probe. It is a digital receiver-level noise observation, not calibrated
SPL. None of the path matrices establishes generally stable `H[k]`.

## Per-path result

The passive column is a scalar or scalar-input SIMO Shannon upper bound under
the frozen measured-drive, stationary Gaussian-noise, independent-symbol, and
per-bin assumptions. The repeated-pilot column is merely the previously
reported `log2(1+SNR)` arithmetic; it is not labeled Shannon capacity. GMI is
the optimized empirical bitwise statistic from actual known transmitted bits
and receiver-matched LLRs. Effective SINR is the 16-QAM AWGN GMI equivalent,
not independently measured SNR.

| Path | Strict blocks | Probe kbps | Passive bound kbps | Repeated arithmetic kbps | GMI/bit | Effective SINR dB | GMI rule selects |
|---|---:|---:|---:|---:|---:|---:|---|
| F0-S0-R0 | 50/50 | 25.600 | 85.82 | 32.24 | 0.8718 | 11.51 | yes |
| F0-S0-R1 | 0/50 | 0 | 23.26 | 5.96 | 0.0399 | −8.05 | no |
| F0-S0-MRC01 | 50/50 | 25.600 | 89.03 | — | 0.8743 | 11.57 | yes |
| F0-S1-R0 | 0/50 | 0 | 25.53 | 27.27 | 0.1753 | −1.11 | no |
| F0-S1-R1 | 0/50 | 0 | 8.48 | 16.50 | 0.0465 | −7.37 | no |
| F0-S1-MRC01 | 0/50 | 0 | 29.38 | — | 0.3190 | 2.22 | no |
| R1-S0-R0 | 0/50 | 0 | 82.82 | 64.64 | 0.5133 | 5.53 | yes |
| R1-S1-R0 | 43/50 | 22.016 | 161.20 | 67.12 | 0.6958 | 8.42 | yes |
| R2-S0-R0 | 0/50 | 0 | 85.85 | 69.49 | 0.4475 | 4.47 | no |
| R2-S1-R0 | 45/50 | 23.040 | 170.04 | 68.97 | 0.7043 | 8.56 | yes |
| SM-S0-R0 | 50/50 | 25.600 | 62.61 | 135.45 | 0.8490 | 11.06 | yes |
| SM-S1-R0 | 0/50 | 0 | 9.05 | 53.65 | 0.2447 | 0.68 | no |

The reverse output-0 result is the clearest failure of stationary arithmetic.
It recovered 0/50 blocks twice while the passive bound was 82.82–85.85 kbps
and repeated-pilot arithmetic was 64.64–69.49 kbps. Those calculations omit
or average away impairments that dominate the actual randomized-QAM frame.

## Predictor errors and setbacks

Using each preregistered predictor as a binary selector for the 25.600 kbps
profile across the ten mono observations produced:

| Rule | Mean absolute error | Median absolute error | Selections below 98% | Catastrophic selections below 90% |
|---|---:|---:|---:|---:|
| Actual-LLR GMI ≥ 0.499971 | 3.174 kbps | 0 | 3 | 2 |
| Passive Shannon bound ≥ 25.600 kbps | 5.734 kbps | 0 | 4 | 3 |
| Repeated-pilot arithmetic ≥ 25.600 kbps | 10.854 kbps | 3.072 kbps | 6 | 5 |

No rule met the 98% reliability objective. The GMI rule improved ranking and
reduced average error, but it did not eliminate catastrophic optimism. In
particular:

- R1 output 0 sat only 0.0133 above the asymptotic rate threshold yet decoded
  no strict blocks. A safety margin cannot be selected from this same path and
  then presented as held-out evidence.
- R1/R2 output 1 had high average GMI but only 86%/90% block recovery. A good
  average information statistic did not guarantee the lower-tail reliability
  required by the transport.
- Optimized LLR scaling was large on reverse paths (approximately 3–7), which
  means the current max-log reliability magnitude is substantially
  mismatched for information accounting. This does not identify a decoder
  fix: multiplying every LLR by one positive constant leaves Viterbi path
  ordering unchanged. Frequency- or time-selective reliability errors require
  separate evidence.
- Passive-noise bounds use a stationary capture and therefore miss active
  transducer DSP, clipping/compression, time variation, and non-Gaussian
  interference. They must not be used as an achievable-rate selector.
- Repeated identical QPSK symbols do not reproduce the PAPR, constellation,
  tracking, and finite-code behavior of randomized QAM. Their variance can be
  severely optimistic.

The two MRC rows were retained as scalar-input SIMO diagnostics only. No
log-determinant MIMO calculation was made from the sequential matrix.

## Qualification gap and next calibration gate

The result remains `BENCH-SPIKE_NOT_PROMOTED` with a formal
`QUALIFICATION_GAP`. Before an adaptation policy is integrated, a new
preregistered acquisition should:

1. close Android realized-output-route and exact media-volume provenance;
2. collect whole independent physical cells spanning at least two receiver
   devices, both directions, multiple geometry/noise conditions, and at least
   five held-out cells not used for implementation or calibration;
3. probe a frozen MCS ladder rather than one profile—for example robust QPSK,
   16-QAM rate-1/2, 16-QAM rate-3/4, and one higher-order candidate—with order
   balanced independently of results;
4. place passive noise, active `H[k]`, randomized-QAM LLR evidence, and strict
   block masks in each same-session record;
5. use calibration cells only to choose any GMI safety margin or lower-tail
   statistic, including a candidate based on symbol/block-local GMI or LLR
   mismatch; and
6. evaluate the frozen policy on whole held-out physical cells against the
   Roadmap gate: median goodput error at most 15%, profile choice within 10%
   of the measured oracle in at least 80%, no choice below the 98% objective
   when a compliant profile exists, zero selections below 90%, and held-out
   Spearman correlation at least 0.5.

If those conditions are met, the promoted C measurement surface should expose
the aggregate known-probe bit metric, confidence, age, profile ceiling, and
lower-tail evidence directly. Raw LLR export is not necessary for the public
API. Until then, GMI and both Shannon calculations remain diagnostics and must
not drive automatic profile activation.

## Reproducibility and raw-input integrity

The analysis read 27 retained files totaling 60,893,841 bytes. SHA-256, size,
mtime, and mode inventories were identical before and after analysis. No ADB,
recording, playback, or live-device operation was used.

The lightweight raw-input inventory is
[`raw-input-inventory.json`](../../artifacts/spikes/rank5a-capacity-predictor-20260718/raw-input-inventory.json).
The analysis implementation and deterministic tests are
[`rank5a_capacity_predictor.py`](../../scratch/hw20k/rank5a_capacity_predictor.py)
and
[`test_rank5a_capacity_predictor.py`](../../scratch/hw20k/test_rank5a_capacity_predictor.py).

Run from the repository root with the project virtual environment:

```sh
.venv/bin/python scratch/hw20k/rank5a_capacity_predictor.py \
  --raw-root /path/to/rank5-pixel-mac-20260718 \
  --preregistration artifacts/spikes/rank5a-capacity-predictor-20260718/preregistration.json \
  --out artifacts/spikes/rank5a-capacity-predictor-20260718
```

The retained input root is external to this worktree because raw PCM is not a
shipping-library fixture. The committed output records its exact hashes rather
than copying approximately 61 MB of acquisition data.
