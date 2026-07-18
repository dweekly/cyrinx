# Spike 8b preregistration: bracketing channel-estimate oracle

Frozen on 2026-07-18 before implementing or inspecting any bracketing-estimator
outcome. Existing campaign-level results in the tracked evidence ledger were
known when this document was written. This is an offline, non-claimable oracle
screen. It does not authorize playback, recording, ADB access, or a receiver
promotion claim.

## Falsifiable hypothesis and incumbent

On retained Pixel 7a captures with CP 96, pilot spacing 16, 64-QAM, rate 2/3,
128 data symbols, and zero inter-frame gap, a terminal channel estimate derived
from known transmitted symbols and aligned to the incumbent timing/common-phase
reference will show that channel aging is large enough to justify an OTA
terminal-training experiment.

The incumbent is a frame-start estimate from the two full-band synchronization
symbols, followed by the existing per-symbol known-pilot timing-slope and
common-phase estimator. Decision-directed tracking is disabled. The timing
origin and the per-symbol timing/common-phase corrections computed against the
start estimate are frozen and reused by every candidate.

## Dataset and immutable eligibility checks

The only eligible source is campaign `pixel7a-p16-sym128-gap0`, whose tracked
ledger binds the completed manifest SHA-256 to
`0f81dc4dce591c47e7fb1aeefeb635ac49de2ce01fbf0fd7805326446668af9e`.
The analyzer will accept candidate-profile runs only after verifying:

- executed and complete campaign/run manifests and their recorded SHA-256s;
- CP 96, pilots/16, 6 bits/bin, rate 2/3, 128 data symbols, 48 kHz, FFT 2048,
  five frames, and zero inter-frame gaps;
- raw two-channel PCM length and SHA-256;
- each expected-payload file against the payload hash frozen in the run plan;
- recorded frame detections without searching expected bytes; and
- a deterministic regeneration of every known frequency-domain symbol from
  the verified payload and frame seed.

Files failing any check are excluded with an explicit reason; no substitute
capture is admitted. Sorted run ID and frame index define processing order.
This diagnostic uses all eligible frames. It has no held-out promotion set
because expected payload symbols are intrinsic to the oracle and contaminate
every candidate. Any subsequent transmitted terminal-trainer comparison must
be newly preregistered and use separate development and held-out OTA runs.

## Frozen estimators

For each microphone independently:

1. Estimate `H_start[k]` by complex averaging of the two synchronization-symbol
   observations after division by their known symbols.
2. Use only received comb pilots and weights fixed from the synchronization
   estimate to compute the incumbent timing slope and common phase for every
   data symbol. Apply those exact corrections to all estimators.
3. Form `H_end[k]` from the final eight data symbols after fixed phase/timing
   correction and division by their known transmitted QAM/pilot values. Use a
   component-wise median across the eight complex observations. No decoded
   decision, CRC result, or candidate outcome may alter this estimate.
4. Align `H_end` to `H_start` only by removing one residual affine phase fit on
   the pilot-bin ratios. Preserve the fitted gain and frequency-selective
   residual; record the removed intercept and slope.

Compare the following frozen equalizers:

- `start-only`: `H_start` for all symbols;
- `complex-linear`: direct complex interpolation between the endpoints;
- `logmag-unwrapped-phase`: linear interpolation of log magnitude and of the
  frequency-unwrapped phase of `H_end / H_start`, with a -30 dB relative floor;
- `significant-tap`: transform both endpoints to a 2048-tap CIR, retain the
  union of taps within CP 96 that are within 20 dB of either endpoint peak,
  linearly interpolate only those complex taps, and transform back.

For each candidate, run both full-frame interpolation and a causal-attribution
variant that leaves symbols 0--63 on `H_start` and interpolates only symbols
64--127. MRC, if evaluated, interpolates each microphone's channel separately
and uses the incumbent synchronization-only noise weights. Receiver selection
is frozen from the source start-only policy and cannot inspect candidate CRC,
payload, EVM, or expected-byte results.

## Primary diagnostic and non-regression checks

The frame-wide interleaver spreads every CRC block across the frame, so block
index is not a time coordinate. The primary block diagnostic is therefore the
fraction of start-only-invalid ordered blocks that become valid when only the
late-half symbols receive the candidate equalizer. Report its denominator,
candidate-only recoveries, candidate regressions, and net recovered blocks.

The second co-primary diagnostic is payload-bin late-quartile EVM over symbols
96--127, computed against known transmitted QAM values:

`gain_dB = 20 log10(EVM_start-only / EVM_candidate)`.

Report median and aggregate energy-weighted gain. Also report first-half EVM,
whole-frame ordered CRC-and-same-position-byte recovery, and per-frame results.
A candidate clears the oracle proceed threshold if either:

- it recovers at least 50% of start-only-invalid blocks in the late-half-only
  intervention; or
- it improves aggregate late-quartile EVM by at least 3 dB.

Stop without an OTA candidate if neither threshold is met, if the first-half
EVM regresses by more than 0.5 dB under full interpolation, or if the endpoint
interpolant does not improve the middle-quarter EVM. An oracle pass authorizes
only a separately preregistered waveform acquisition with real known terminal
training; it cannot promote an expected-payload-aided receiver.

## Leakage controls

Expected payload bytes may be used only to regenerate oracle symbols, compute
oracle EVM, and verify decoded bytes after an estimator is frozen. They are
forbidden from timing search, microphone/estimator selection, hyperparameter
selection, LLR weighting, stopping within a frame, or any claimable receiver
result. Output labels must contain `non-claimable-terminal-oracle`. The report
must distinguish CRC-only from ordered CRC-and-byte identity. A test must show
that changing expected bytes after symbol regeneration cannot change timing,
phase tracking, receiver selection, or estimator choice.

## Accounting

Report exact payload bits and these distinct denominators:

- scheduled: first preamble sample through the final data/trainer sample across
  all five zero-gap frames;
- gross: the complete retained capture duration, including pre/post roll; and
- session: discovery, sounding, buffering, and decode wall time. Retained burst
  captures cannot establish a session denominator, so report it as unavailable
  rather than equating it with scheduled or gross time.

For a future terminal trainer, report counterfactual scheduled ceilings for
zero, one, and two appended 2,144-sample OFDM training symbols per frame, with
payload held constant. Also report the alternative replacement cost if one or
two of the 128 data symbols are replaced, reducing payload geometry; never
describe appended training as free or shared without a separately defined
superframe schedule.

## Retained outputs

Retain this preregistration, inventory with file hashes and exclusion reasons,
analyzer source and deterministic tests, machine-readable per-frame results,
aggregate report, exact denominator calculation, repository revision, Python
and dependency versions, and an OTA acquisition manifest or explicit stop
reason. Raw PCM remains referenced by verified hash rather than copied into
Git.
