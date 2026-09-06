# Garage research tooling

## In plain English

Two small pieces of the garage experiment program, described in
[docs/research/garage-throughput-plan.md](../../docs/research/garage-throughput-plan.md).
The first says exactly what radio settings the cautious baseline test uses, and
why each number is what it is. The second measures how long the room keeps
echoing, and — unlike the tool we already had — refuses to give you a number when
the recording cannot actually support one.

Nothing here talks to hardware. The capture runner (G1) comes next.

## What is here

| File | What it is |
|---|---|
| `geometries.py` | The frozen conservative configurations for the stage 1 baseline map, one per direction, with the source of every constant |
| `delay_spread.py` | Schroeder delay spread with `ok` / `noise-limited` / `window-limited` / `invalid` states |
| `test_garage_g0.py` | Offline tests for both, including a byte-exact digital round trip through the C codec |

## Running the tests

```sh
python3 -m venv .venv && .venv/bin/pip install numpy pytest
clang -std=c11 -O2 -dynamiclib -Dkiss_fft_scalar=double \
  -ISources/CCyrinx/include -ISources/CCyrinx/kissfft \
  Sources/CCyrinx/cyrinx_bulk.c Sources/CCyrinx/cyrinx_fft.c \
  Sources/CCyrinx/kissfft/kiss_fft.c Sources/CCyrinx/kissfft/kiss_fftr.c \
  -o scratch/hw20k/libcyrinxbulk.dylib -lm
.venv/bin/python3 -m pytest scratch/garage/test_garage_g0.py -q
```

The two C-codec tests skip if the dylib is absent; every other test runs without
it.

## Two decisions worth knowing about

**These are not registry profiles, and G0 does not add a registry row.** The C
bulk codec takes a `cyrinx_bulk_config` directly, so host-side research never
resolves a profile through `Sources/CCyrinx/cyrinx_profiles.c`. Adding a row
would mean touching C3-04 contract surface — the canonical JSON fixture, the C
table, identity expectations, and their tests — while the Kotlin/JNI registry
view is still an open C3-04 merge gate, which would land a row with no JVM
representation. A registry row is what a geometry earns *after* it wins a
comparison, not what research needs to start.

**There are two geometries, not one.** A single band shared by both directions
would put the uplink arm of the baseline map into a region already measured as
unusable: the Moto G speaker cliffs at 14 kHz (`scratch/hw20k/NOTES.md`, A5), the
iPhone rolls off by 10–14 kHz (`docs/IOS_HIL.md`), and both tested phone speakers
are phase-incoherent above ~18 kHz (`docs/NEGATIVE_FINDINGS.md` entry 9). The
uplink geometry is therefore band-fitted to 600–14000 Hz — the intersection of
what the phones on hand can emit — while the downlink keeps the registry's
1100–23000 Hz.

## Why the validity states are against integrated noise

A peak-to-noise ratio cannot tell you whether a Schroeder crossing is real. The
curve integrates noise across the whole remaining window while the peak is one
sample, so a single impulse with no reflections at all, sitting in stationary
noise 46 dB below it, produces a -10 dB "delay spread" of 38.8 ms where the
noiseless answer is 0.02 ms. That is past the 16 ms guard budget, so accepting it
would send a reflection-free position to stage 8.

A crossing is therefore judged against the integrated noise energy still inside
the window at that point, needing `NOISE_HEADROOM_DB` of margin over it, and
`test_garage_g0.py` pins that case. The first version of this module checked the
peak ratio instead and reported the 38.8 ms figure as `ok`.

## The guard budget

`geometries.PRACTICAL_GUARD_BUDGET_MS` is 16.0 ms, equal to the 768-sample
cyclic prefix, and it is the number the plan's stage 1G gate compares measured
late energy against. It is declared here, before the batch, so it cannot be
argued afterwards. It is a spending decision, not a physical limit: the profile
validator accepts any prefix up to the FFT size, and a position beyond the budget
is recorded as one where further guard and MCS tuning is not justified by current
evidence — not as one that cannot be recovered.
