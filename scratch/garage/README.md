# Garage research tooling

## In plain English

Two things. Working out what radio settings a cautious link between two
particular devices should use, derived from what each device can actually emit
and hear rather than from what kind of device it is. And measuring how long a
room keeps echoing — refusing to give a number when the recording cannot support
one, which turns out to matter more than the measurement itself.

Only `selfcal.py` talks to hardware; everything else is analysis.

## What is here

| File | What it is |
|---|---|
| `geometries.py` | Conservative link geometries, one per directed link, with the source of every constant |
| `acquire.py` | Swept-sine acquisition with a declared observation horizon, and hardware capture helpers |
| `delay_spread.py` | Schroeder delay spread by Lundeby truncation, with explicit validity states |
| `selfcal.py` | Runs ADR 0006's two local phases: self-calibration and environmental sampling |
| `ladder.py` | Over-the-air ladder of coherent settings (band, guard, modulation), scored like `linkprobe.py`, optionally after clock-offset correction chosen by pilot EVM |
| `ladder_summary.py` | Aggregates ladder results by cell across repeats |
| `band_spread.py` | Delay spread and guard-window signal-to-echo ratio per frequency band, from retained `crosscal.py` captures |
| `genie_sinr.py` | Per-bin signal-to-interference of a retained ladder capture against its regenerated transmit waveform, after fitting the clock offset |
| `resample_replay.py` | Replays retained ladder captures through the C decoder after resampling by a clock offset |
| `slide_experiment.py` | Separates the between-symbol and within-symbol effects of a clock offset, in a patched copy of the Python reference receiver |
| `retain.py` | Decides where captures are kept: `~/dev/cyrinx-artifacts/garage/` unless `--outdir` or `$CYRINX_GARAGE_ARTIFACTS` says otherwise, and never inside a linked git worktree, which is deleted with its ignored files when removed |
| `test_geometries.py`, `test_delay_spread.py` | Offline tests, including a byte-exact round trip through the C codec and a retained real capture |

## Where captures go

Raw captures cannot be regenerated, so they are kept outside every checkout, in
`~/dev/cyrinx-artifacts/garage/`. The captures from the first two sessions were
lost because they were written inside worktrees that were later removed. That
directory is not backed up by anything in this repo; copy it somewhere durable
after a session.

## Running the tests

```sh
python3 -m venv .venv && .venv/bin/pip install numpy pytest
clang -std=c11 -O2 -dynamiclib -Dkiss_fft_scalar=double \
  -ISources/CCyrinx/include -ISources/CCyrinx/kissfft \
  Sources/CCyrinx/cyrinx_bulk.c Sources/CCyrinx/cyrinx_fft.c \
  Sources/CCyrinx/kissfft/kiss_fft.c Sources/CCyrinx/kissfft/kiss_fftr.c \
  -o scratch/hw20k/libcyrinxbulk.dylib -lm
.venv/bin/python3 -m pytest scratch/garage/ -q
```

The two C-codec tests skip if the dylib is absent; every other test runs without
it.

## Two decisions worth knowing about

**These are not registry profiles, and nothing here adds a registry row.** The C
bulk codec takes a `cyrinx_bulk_config` directly, so host-side research never
resolves a profile through `Sources/CCyrinx/cyrinx_profiles.c`. Adding a row
would mean touching C3-04 contract surface — the canonical JSON fixture, the C
table, identity expectations, and their tests — while the Kotlin/JNI registry
view is still an open C3-04 merge gate, which would land a row with no JVM
representation. A registry row is what a geometry earns *after* it wins a
comparison.

**Bands come from endpoint capability, not from a role.** There is no "uplink" or
"downlink" geometry. The occupied band is a property of the transmitting device's
speaker and the receiving device's microphone, so it is computed as the
intersection of the two, capped at 18 kHz when a phone is transmitting because
both tested phone speakers are phase-incoherent above that
(`docs/NEGATIVE_FINDINGS.md` entry 9).

Naming the two ends "uplink" and "downlink" would bake in a topology — laptop
strong, phone weak — that is wrong for a laptop pair, wrong for a phone pair, and
wrong in an unpredictable direction for a high-end phone talking to a low-end
one. The iPhone emits 600–11000 Hz and the Moto G emits 600–14000 Hz, so on that
pair the cheaper phone carries the *wider* band; a role would get it backwards.
C3-18's merge gate is that two symmetric peers converge on complementary roles
elected at runtime, and capability travels in the beacon's capability hash, so
this follows the contract the delivery plan already set.

An endpoint that has not been characterized resolves to `UNKNOWN_ENDPOINT`, the
narrowest band measured on any device here, so unknown degrades to conservative
rather than optimistic. C3-20b replaces the measured table with per-endpoint
self-characterization at association time.

## The guard budget

`geometries.PRACTICAL_GUARD_BUDGET_MS` is 16.0 ms, equal to the 768-sample
cyclic prefix, and it is the number the garage plan's stage 1G gate compares
measured late energy against. It is declared here, before any batch, so it cannot
be argued afterwards. It is a spending decision, not a physical limit: the
profile validator accepts any prefix up to the FFT size, and a position beyond
the budget is recorded as one where further guard and MCS tuning is not justified
by current evidence.
