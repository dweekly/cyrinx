# Re-plan: a delay-spread readout the stage 1G gate can rely on

Date: 2026-09-07. Status: proposed, replacing the readout on branch
`research/garage-g0`. Reviewed once by Codex at high reasoning; findings folded
in below, with declines noted.

## In plain English

The garage plan has to decide, per position, whether a room echoes for longer
than our signal's guard interval can absorb. That decision needs one number: how
long the reflected energy keeps arriving.

Measuring it is harder than it looks. A recording always contains background
noise, and integrating that noise across a few hundred milliseconds looks exactly
like a room that never stops ringing. Two attempts at telling those apart both
failed, in opposite directions. This plan adopts the method room acousticians
already use, and — more importantly — pins down the four surrounding details that
turn out to matter more than the method itself: what domain the noise reference
lives in, how much the measurement is biased by cutting the tail off, how far
into the future we can see at all, and what the number means when we cannot
measure it.

## Why

Stage 1G of the [garage plan](garage-throughput-plan.md) is a spending decision.
After capture-integrity and replay diagnostics have passed, a position that keeps
failing *and* shows valid evidence of substantial energy beyond the declared
16 ms guard budget is directed to stage 8 waveform-class screening rather than to
further ordinary guard and MCS tuning. It is not a claim that the position is
unrecoverable, and it does not bypass stage 2.

The readout's output is therefore not "how long the room rings" but
**threshold-specific times at which the remaining backward-integrated energy
crosses −10, −15 and −20 dB**, each with its own validity.

That decision is only as good as the number it reads, and the module on the
branch cannot produce one on the acquisition primitive it was written for:

```
make_ess() -> deconvolve_ir(), no added noise, no reflections
  -10 dB -> noise-limited     (freqresp.delay_spread: 0.020833 ms)
```

## What the previous attempt got wrong

Two unchecked assumptions, producing failures in opposite directions:

- **That the pre-peak region is noise.** For a band-limited deconvolution it
  holds the main tap's sidelobes — 8.4e-03 rms against a unit peak, an apparent
  41.5 dB floor on a noiseless capture.
- **That residual energy at a crossing separates a real crossing from a
  noise-driven one.** For sparse multipath the residual after the last tap *is*
  noise, which is what a clean channel looks like.

Both were reachable by one test through `make_ess` → `deconvolve_ir`. Every test
used a synthetic impulse response with a zeroed pre-peak, so the suite exercised
the function rather than the path it sits in.

## Approach

Adopt **Lundeby truncation**: iteratively smooth the decay, fit a regression to
it, estimate the noise floor, and truncate the backward integration at the
crosspoint, so the Schroeder curve is integrated only where it is decay. Its
documented failure at low SNR maps onto an explicit non-`ok` status rather than a
wrong number.

Four surrounding decisions matter as much as the method, and each is fixed here.

### 1. The noise reference is raw PCM, processed identically

A room-tone recording is raw capture PCM; `measure` sees an inverse-filtered
response. The two powers are not interchangeable: the default inverse filter has
energy 8.3359e-05, so stationary white noise moves by **−40.79 dB** under full
overlap. Comparing raw noise variance against IR energy would overstate the floor
by roughly 40 dB.

The readout therefore takes **raw noise PCM plus the same inverse filter**, and
processes it through the identical preprocessing, normalization and analysis
band as the sweep capture. The noise deconvolution is never independently
peak-aligned — it has no peak to align to. Room tone shorter than the inverse
filter (ADR 0006 suggests ~2 s against a 6.0 s filter) must be handled by a
declared convolution-boundary rule and only the fully-overlapped interior may be
used for the power estimate.

### 2. Plain truncation, with the bias reported as an interval

Truncation biases the estimate *short*, and the bias is not negligible at this
gate. For an exponential decay whose true −10 dB crossing is 16.500 ms,
discarding the last 1% of energy and renormalizing moves the crossing to
15.882 ms — across the 16 ms budget. Compensating for omitted energy requires a
decay model this plan is not prepared to validate on garage captures.

So: **plain truncation, with each reading carrying a bounded error interval**
derived from the discarded energy fraction. The gate compares the *interval*
against the budget and abstains when it straddles. A number whose uncertainty
spans the decision is not evidence for the decision.

### 3. Sparse channels take an explicit no-truncation path

Lundeby regresses a diffuse decay. A single band-limited tap, or two taps
separated by silence, offers no such decay, and a noiseless capture has no finite
crosspoint at all — so a failed regression does not imply low SNR, and treating
it as such would fail the clean-input acceptance cases.

The readout declares an explicit path for this: when the processed noise floor is
negligible relative to the observed energy, no truncation is applied and the
crossing is reported directly. Regression parameters — smoothing window, minimum
fit support, required negative slope, convergence tolerance, iteration cap — are
named constants with a documented basis, and a fit that cannot be supported
returns a distinct status from a fit that succeeded and found the floor. Fixtures
include taps arriving after a quiet gap, so a gap cannot be mistaken for the
truncation point.

### 4. Validity is relative to a declared observation horizon

`deconvolve_ir` returns exactly 120 ms after the largest peak and nothing else.
That crop is invisible downstream, and it is not conservative:

| Channel through the real acquisition path | `freqresp` −10 dB |
|---|---:|
| single tap | 0.020833 ms |
| 30 ms reflection, gain 0.5 | 30.020833 ms |
| **150 ms reflection, gain 0.5** | **0.020833 ms** |

A reflection past the crop reads exactly like no reflection at all. Since the
gate exists to find late energy, that is the most dangerous failure available to
it.

This plan adds a **garage-facing deconvolution adapter** that retains a declared
observation horizon and capture-support metadata, leaving `freqresp.deconvolve_ir`
and `freqresp.delay_spread` untouched. Every reading states the horizon it was
measured over, and the readout distinguishes three cases the previous design
collapsed into one: *the recording ended early*, *the analysis crop removed
data*, and *the decay is still unresolved at the boundary*. The contract is
explicitly bounded — a reading is valid with respect to its horizon and promises
nothing about echoes beyond it.

### One source of truth for the crossing arithmetic

`freqresp.delay_spread` is called on the accepted integration input rather than
having its crossing loop copied, so the two cannot drift. The readout's threshold
set is fixed at the three `freqresp` supports.

## What must be true of the implementation

Physical-behaviour cases run through `make_ess` → the garage adapter. Argument
and status handling keep direct unit tests, which need no acquisition path.

1. **Sweep against itself**, no noise, no reflections: −10 dB **0.020833 ms**,
   −15 dB **0.020833 ms**, −20 dB **0.166667 ms**, matching `freqresp` exactly.
2. **30 ms reflection at gain 0.5**, high SNR: **30.020833 ms** at all three
   thresholds. Not merely "reports a figure" — a wrong short answer must fail.
3. **150 ms reflection at gain 0.5** is reported against a horizon that contains
   it, or the reading names the horizon as the limit. It may not silently read as
   a single tap.
4. **A noisy diffuse decay** with a known decay rate returns accurate, usable
   numbers within the declared error interval — the case the whole method exists
   for — across several deterministic noise seeds and observation lengths.
5. **Exponential decays either side of the 16 ms budget**, with noise, either
   stay within their declared error bound or abstain. Neither may land on the
   wrong side of the gate silently.
6. **One impulse in stationary noise**, SNR stated in the deconvolved domain with
   noise added to raw capture PCM and an *independent* noise realization for the
   room-tone reference, reports the short true answer or a non-`ok` status. It
   must never report tens of milliseconds. First review's P1.
7. **A clean two-tap channel at high SNR** reports its true crossing. Second
   review's P1.
8. **Noise-domain calibration**: white and coloured noise references produce the
   floor the analytic conversion predicts, and scaling sweep and noise together
   leaves every reading unchanged.
9. **A noise capture is required.** Constructing the readout without one is an
   error, not a fallback.
10. **The gate consumes a reading, not a float.** `exceeds_guard_budget` takes a
    `Reading` (or a `DelaySpread` plus threshold), abstains for every non-`ok`
    status including one carrying a diagnostic numeric value, and rejects
    non-finite input rather than comparing it. Aggregation keeps unavailable
    thresholds and their reasons visible instead of reporting `ok` because one
    threshold succeeded.
11. **Every reading carries its diagnostics**: processed noise power, observation
    horizon, truncation time, fit interval and slope, convergence outcome, and a
    per-threshold reason. A later reviewer must be able to explain an abstention
    without rerunning the analysis.

Acceptance for the branch: these pass, the 24 geometry tests continue to pass
**without skips** (so the codec dylib is built), and a Codex review at the branch
tip returns no P1. The acceptance command is explicit:

```sh
.venv/bin/python3 -m pytest scratch/garage/ -q
```

`scripts/check.sh` does not run these; the command above is the gate until a
dedicated hook exists.

## Where the work happens

Branch `research/garage-g0`, worktree `~/dev/cyrinx-WORKTREE/garage-g0`, PR #87.
`scratch/garage/delay_spread.py` is rewritten and a deconvolution adapter is
added. `geometries.py` is untouched.

## Out of scope

- The G1 capture runner and any hardware capture.
- Changing `freqresp.delay_spread` or `freqresp.deconvolve_ir`. Existing tables
  depend on their behaviour; the adapter sits beside them.
- Nonlinear decay-model truncation and omitted-energy compensation. If the
  reported error interval proves too wide on real captures, that is a new
  preregistration with its own evidence.
- Reconciling the analysis band and the measurement time reference with the
  receiver's own timing. Recorded as a roadmap item; this effort states its
  assumptions rather than redesigning them.
- Verifying that a room-tone reference really matches its sweep's route,
  processing and gain in the field. That is G1 acquisition work; recorded on the
  roadmap.
- Opportunistic probing of unoccupied subcarriers (Rank 8e).

## Alternatives considered

- **Peak-to-noise ratio gating.** Rejected: first review's P1.
- **Residual-energy-at-crossing gating.** Rejected: second review's P1 — it
  rejects precisely the cleanest sparse channels.
- **Truncation with omitted-energy compensation.** Better bias behaviour, and the
  literature supports it, but it requires committing to a decay model before we
  have garage captures to validate one against. Declined for now in favour of a
  reported error interval, which is honest about the same uncertainty without
  modelling it away. Revisit if intervals routinely straddle the budget.
- **A fixed truncation at a fraction of the window.** Does not adapt to the
  capture's own SNR.
- **Reporting `freqresp.delay_spread` unmodified.** Correct on clean input, which
  is most of the existing tables, but it silently returns the window length on
  exactly the noisy marginal captures stage 1G will read — and, as the table
  above shows, cannot see past the 120 ms crop either.

## Sources

- Lundeby truncation, the crosspoint method, and the 5–10 dB margin above the
  noise floor: [Automated estimation of the truncation of room impulse response
  by applying a nonlinear decay model, JASA
  139(3)](https://pubs.aip.org/asa/jasa/article-abstract/139/3/1047/910126/Automated-estimation-of-the-truncation-of-room);
  [Correction of room impulse response truncation based on a nonlinear decay
  model](https://www.sciencedirect.com/science/article/abs/pii/S0003682X17308654).
- Plain versus compensated truncation, and uncorrected truncation
  underestimating decay time: [The ISO 3382 parameters: can we simulate them? Can
  we measure
  them?](https://www.odeon.dk/pdf/ISRA2013_Paper_The%20ISO%203382%20parameters_Can%20we%20simulate%20them_Can%20we%20measure%20them_24July2013.pdf).
- Method definitions, smoothing and regression-support handling in a published
  implementation: [pyrato EDC
  documentation](https://pyrato.readthedocs.io/en/develop/modules/edc.html) and
  [source](https://pyrato.readthedocs.io/en/develop/_modules/pyrato/edc.html).
