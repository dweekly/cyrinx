# Re-plan: a delay-spread readout the stage 1G gate can rely on

Date: 2026-09-07. Status: proposed, replacing the readout on branch
`research/garage-g0`.

## In plain English

The garage plan has to decide, per position, whether a room echoes for longer
than our signal can tolerate. If it does, no amount of better coding helps and
we need a different kind of signal entirely. That decision needs one number:
how long the room keeps echoing.

Measuring it is harder than it looks, because a recording always contains some
background noise, and integrating that noise across a few hundred milliseconds
looks exactly like a room that never stops ringing. Two attempts at telling
those apart both failed, in opposite directions. This plan adopts the method
room acousticians already use for it instead of inventing a third.

## Why

Stage 1G of the [garage plan](garage-throughput-plan.md) is a fork: a position
whose late energy sits beyond the declared 16 ms guard budget goes to stage 8
waveform-class screening rather than through stages 2-4. That fork is only as
good as the number it reads. A readout that over-reports sends good positions
away from the work that would have fixed them; one that under-reports spends
weeks tuning a position that was never going to close.

The readout on the branch does both, depending on input, and cannot report at
all on the acquisition primitive it was written for:

```
make_ess() -> deconvolve_ir(), no added noise, no reflections
  -10 dB -> noise-limited     (freqresp.delay_spread: 0.021 ms)
  -15 dB -> noise-limited
  -20 dB -> noise-limited
```

## What the previous attempt got wrong

Two assumptions, neither checked, which together produce failures in both
directions:

- **That the pre-peak region is noise.** For a band-limited deconvolution it
  holds the main tap's own sidelobes, measured here at 8.4e-03 rms against a
  unit peak: an apparent 41.5 dB noise floor on a noiseless capture.
- **That residual energy at a crossing separates a real crossing from a
  noise-driven one.** It does not. For sparse multipath the residual after the
  last tap *is* noise, which is what a clean channel looks like.

Those are the two horns of one misconception, which is why the fix for the first
review's finding produced the second review's. A better-tuned version of the
same test is not available.

Both were reachable by one test through `make_ess` -> `deconvolve_ir`. Every
test used a synthetic impulse response with a zeroed pre-peak region, so the
suite exercised the function rather than the path the function sits in.

## Approach

Adopt **Lundeby truncation**, the established method for exactly this problem:
iteratively smooth the decay, fit a regression line to it, estimate the noise
floor, and truncate the backward integration at the crosspoint between the two,
leaving a safety margin above the floor. The Schroeder curve is then integrated
only over the interval where it is decay rather than noise, which removes the
artefact both previous attempts were trying to detect after the fact.

Two properties make it the right choice here rather than a third invention: it
is the method the surrounding literature and ISO 3382 practice already assume,
so a figure produced this way is comparable with published ones; and its failure
mode is documented — it becomes unreliable at low SNR — which maps onto an
`invalid` result rather than a wrong number.

**The noise estimate becomes an input, not an inference.** ADR 0006 makes
environmental sampling a required local phase, captured per position and per
direction, so a noise-only recording at the same gain is available by contract.
The readout takes it as an argument. No code path may infer noise from pre-peak
samples.

**One source of truth for the numbers.** `freqresp.delay_spread` keeps producing
the figures in the existing tables. The garage readout adds truncation and
validity around the same Schroeder convention, and a test pins the two to
identical values on inputs where truncation is a no-op, so the two cannot drift.

## What must be true of the implementation

Each of these is a test, and every one runs through `make_ess` ->
`deconvolve_ir` rather than a hand-built impulse response:

1. **The sweep against itself**, no noise and no reflections, reports about
   0.02 ms at -10 dB and agrees with `freqresp.delay_spread` to within a sample.
2. **A synthetic 30 ms reflection at gain 0.5**, high SNR, reports a figure
   consistent with that tap structure and does not abstain.
3. **One impulse in stationary noise at ~46 dB peak-to-noise** reports either
   the short true answer or `invalid`. It must never report tens of
   milliseconds. This is the first review's P1 and it must stay dead.
4. **A clean two-tap channel at 120 dB SNR** reports a figure. This is the
   second review's P1 and it must stay dead.
5. **A capture truncated before the decay completes** reports `window-limited`.
6. **As SNR falls**, the deeper threshold becomes unavailable before the
   shallower one, and the aggregate status names which limitation applied.
7. **A noise capture is required.** Constructing the readout without one is an
   error, not a fallback.
8. **`exceeds_guard_budget` abstains** — returns `None` — for every non-`ok`
   reading, so a missing measurement can never read as a short delay spread.

Acceptance for the branch as a whole: those eight pass, the geometries tests
continue to pass unchanged, and a Codex review at the branch tip returns no P1.

## Where the work happens

Branch `research/garage-g0`, worktree `~/dev/cyrinx-WORKTREE/garage-g0`, tracked
by PR #87. `scratch/garage/delay_spread.py` is rewritten; `geometries.py` is
untouched by this plan.

## Out of scope

- The G1 capture runner, and any hardware capture. This plan produces an
  analysis module and its offline tests.
- Changing `freqresp.delay_spread`. Existing tables depend on its current
  behaviour, and a second implementation of the crossing arithmetic is not
  proposed — only truncation and validity around it.
- Refinements beyond Lundeby, such as nonlinear decay-model truncation. If
  Lundeby proves insufficient on real garage captures, that is a new
  preregistration with its own evidence, not an extension of this one.
- Opportunistic probing of unoccupied subcarriers, which is Rank 8e in the
  roadmap and unrelated to measuring a decay.

## Alternatives considered

- **Peak-to-noise ratio gating.** Rejected: the first review's P1. A peak ratio
  says nothing about a curve that integrates noise across a window.
- **Residual-energy-at-crossing gating.** Rejected: the second review's P1. It
  rejects precisely the cleanest sparse channels.
- **A fixed truncation at a fraction of the window.** Cheap and predictable, but
  it does not adapt to the capture's own SNR, so it would be tuned to one bench
  and wrong at the next position.
- **Reporting `freqresp.delay_spread` unmodified.** It is correct on clean
  input, which is most of what the existing tables contain, and it is what the
  gate would fall back to. Rejected because stage 1G will read exactly the noisy,
  reverberant, marginal captures where it silently returns the window length.

## Sources

- Lundeby truncation and the crosspoint method, including the recommended
  5-10 dB safety margin above the noise floor and its documented unreliability
  at low SNR: [Automated estimation of the truncation of room impulse response
  by applying a nonlinear decay model, JASA
  139(3)](https://pubs.aip.org/asa/jasa/article-abstract/139/3/1047/910126/Automated-estimation-of-the-truncation-of-room)
  and [Correction of room impulse response truncation based on a nonlinear decay
  model](https://www.sciencedirect.com/science/article/abs/pii/S0003682X17308654),
  both of which state the Lundeby baseline before proposing refinements.
- [The ISO 3382 parameters: can we simulate them? Can we measure
  them?](https://www.odeon.dk/pdf/ISRA2013_Paper_The%20ISO%203382%20parameters_Can%20we%20simulate%20them_Can%20we%20measure%20them_24July2013.pdf)
  for the measurement-practice context these figures are compared against.
