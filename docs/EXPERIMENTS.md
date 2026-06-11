# Cyrinx physical-measurement campaign (to round out the whitepaper)

Fresh as of 2026-06-11.

## Measured findings — 2026-06-11 bench session (Mac ↔ Pixel 7a)

First live session with the new harness, one cell: `palmrest_stand_fan` (phone
face-up on cloth on the left palm rest, charge-port/bottom-edge toward the hinge
≈ toward the Mac speakers, MacBook on its stand as always, house fan running).
Data: `scratch/hw20k/data/freqresp/mac2pixel_palmrest_stand_fan.json`,
`data/env_sweep.jsonl`, figure `data/freqresp/freqresp_by_path.png`.

1. **E4 met and exceeded — library-native 39.3 kbps.** The library C codec
   (`libcyrinxbulk`) decoded over the air, byte-verified: QPSK r½ 13.1 kbps,
   16-QAM r½ 26.2 kbps, **16-QAM r¾ 39.3 kbps (375/375 blocks, EVM 0.157)**.
   This *exceeds* the 36.6 kbps Python-harness headline and **removes the paper's
   "library only hit 12.8 kbps OTA" caveat** (abstract + §Library-OTA).

2. **64-QAM ceiling — the "64-QAM doubles throughput" headroom estimate is NOT
   realized here.** 64-QAM r¾ decoded 0/339 (0%) at EVM 0.173 (~15 dB effective
   SINR). The cap is the residual-EVM/phase-noise floor (~15 dB), *not* channel
   SNR (the swept-sine measures the raw channel at 52 dB). The paper's headroom
   bullet must be softened: 64/256-QAM is bounded by the effective-SINR/EVM floor
   on this transducer, not the raw SNR.

3. **Adaptive-sounder SNR estimation is unreliable for MCS selection — a real
   finding, no clean one-line fix.** The sounder predicted QPSK (median "SNR"
   10.5 dB) for a channel that carried 16-QAM r¾. Two failure modes bracket the
   truth (effective SINR ≈ 15 dB):
   - *variance across repeated pilots* (current): **pessimistic** — the −10 to
     −25 ppm clock drift rotates the channel between symbols and is counted as
     noise; under-calls.
   - *detrend the per-symbol phase first* (tried, reverted): **optimistic** —
     jumps to ~30 dB and would load 64-QAM (which fails). Refuted the
     short-burst hypothesis: even a 64-symbol sounding stays ~30 dB. The cause is
     that the sounding uses **identical low-PAPR pilots**, so it never excites the
     **channel-estimation error + PAPR-driven speaker nonlinearity** that random
     full-PAPR data incurs.
   **Conclusion:** repeated-pilot statistics cannot predict random-data SINR. The
   reliable sounder is a **data-representative EVM probe** (send a known random-QAM
   frame, read its EVM through the real demod) with **empirically calibrated
   EVM→MCS thresholds**. The sweep below *generates* that calibration curve
   (EVM/effective-SINR vs achievable MCS), so it is the prerequisite for a proper
   sounder redesign (roadmap item). Note the bug is confined to the SNR-estimation
   half (`sounder.analyze`, Python reference); the C/Swift `recommend()` only
   consumes a given SNR.

4. **`rx_peak` is a poor coupling indicator.** This cell read peak 0.087
   ("weak") yet delivered 39.3 kbps. Use SNR/EVM, not peak.

Paper edits these imply: remove the library-OTA caveat (now 39.3 kbps native);
soften the 64-QAM headroom bullet to an EVM-floor bound; add the
sounder-SNR-estimation finding; add the first measured Mac→Pixel H(f) figure.

This is the sequenced plan for the over-the-air measurements that close the
honest gaps in [the whitepaper](whitepaper/cyrinx-acoustic-link.tex) — chiefly
the single-geometry and single-ambient-condition limitations in its
threats-to-validity section. It is the experiment-side companion to
[PUBLICATION.md](PUBLICATION.md).

## The through-line

**Every cell logs three things: the measured channel (frequency response + per-bin
SNR + delay spread), the sounder's _predicted_ MCS tier, and the _achieved_
ceiling tier (the most aggressive library-native MCS that decoded clean).** That
turns the whole campaign into a validation of the adaptive sounder against
ground truth — the paper's most distinctive contribution and currently its
least-evidenced one — not merely a "does it still work farther away" table.

Each `env_sweep.py` cell emits a `prediction_verdict`:
`exact` (predicted == achieved), `conservative` (sounder under-called; the link
could have gone faster), `optimistic` (recommended tier did not decode clean), or
`floor` (sounder dropped to the non-coherent MT-FSK tier). A good sounder is
`exact`-or-`conservative` across the envelope and never `optimistic`.

## Shared methodology (applies to every test)

- **Fixed controls** unless that factor is the independent variable: Mac output
  100 % / Mac input ~22 of 100 / phone media max; quiet room; reference modulation
  16-QAM r3/4; same `DetRng` seed; ordered byte-verification; **span goodput**
  (active message span, first chirp to last data sample, _excludes_ the fixed
  trailing silence — the gross figure is reported alongside where it matters).
- **Reps:** ≥ 8 runs per cell per direction → Wilson 95 % CIs on block-failure
  rate, matching the existing iPhone campaign (`campaign.py`).
- **Metric stack per cell:** goodput; block-failure rate + CI; median EVM; median
  per-bin SNR; −15 dB delay spread; rx peak; **sounder-predicted vs achieved tier**.
- **Calibration:** RELATIVE — drive amplitude and mic gain pinned, magnitude
  reported in dB re the in-band median. Valid for within-device cross-environment
  overlays and band-edge/shape claims; not absolute SPL, and cross-_device_
  magnitude is not directly comparable (stated on every figure).
- **Devices on hand:** MacBook Pro M4 ↔ Pixel 7a, MacBook Pro M4 ↔ iPhone 17 PM.
  A third device class is out of scope (noted as future work).

---

## Tier 1 — essential (convert the paper's three biggest caveats into data)

### E1. Distance sweep — *kills the "single geometry" threat; headline goodput-vs-distance curve*
- **IV:** air-gap separation ∈ {contact, 5, 10, 20, 40, 80 cm}, phone face-up on
  cloth, both directions.
- **Output:** goodput & block-failure vs distance, per direction, with the
  sounder's predicted tier overlaid; the `freqresp` magnitude curves at a few
  distances show *why* the curve bends.
- **Falsifiable claim:** "contact-range" — if the link is brittle past contact,
  that is the finding, stated with data.
- **Runs:** 6 distances × 2 directions × 8 ≈ 96.

| distance | dir | goodput (kbps) | blk-fail % (CI) | med SNR dB | ds15 ms | predicted | achieved | verdict |
|---|---|---|---|---|---|---|---|---|
| contact | down | | | | | | | |
| … | | | | | | | | |

### E2. Orientation × surface sweep — *validates or falsifies the repositioning-guidance heuristics*
- **IV (one factor at a time from nominal):**
  orientation ∈ {face-up, face-down, on-edge, bottom-edge-toward-speaker,
  bottom-edge-away, ±90° yaw}; surface ∈ {hard desk, soft cloth, foam, glass,
  in-hand}.
- **Directly tests:** the "aim the phone's bottom edge at the speaker" and "rest
  on a soft surface" hints in §Repositioning guidance. This is the test most
  likely to *falsify* a guidance heuristic — which is exactly why it is worth
  running; a refuted hint gets softened in the paper.
- **Runs:** ~10 cells × 8 ≈ 80 (downlink; spot-check uplink).

| cell | goodput (kbps) | med SNR dB | ds15 ms | predicted | achieved | verdict | hint fired |
|---|---|---|---|---|---|---|---|

### E3. Ambient-noise robustness — *kills the "single ambient condition" threat*
- **IV:** in-band interferer level (relative, from a fixed external speaker):
  {quiet, office speech, music, HVAC hum, near-ultrasonic jammer}. The
  near-ultrasonic case connects to the `nusguard`/`ultrajam` citations.
- **Output:** goodput vs interferer level, and the SNR at which the sounder steps
  down the ladder.
- **Runs:** ~5 levels × 8 ≈ 40 (downlink).

| ambient | goodput (kbps) | med SNR dB | predicted | achieved | verdict |
|---|---|---|---|---|---|

---

## Tier 1+ — the cheap integrity win

### E4. Library-native full-rate reproduction — *removes the abstract/§Library-OTA caveat*
- Reproduce ~36 kbps through `libcyrinxbulk` at the good palm-rest coupling (the
  path `ota_clib.py` already drives). The paper currently must caveat that the
  library only hit 12.8 kbps OTA; a clean library-native ~36 kbps run makes the
  "measurement made reusable" claim fully earned.
- **Runs:** 8 × 2 directions at the nominal geometry. Lowest effort, high payoff.

---

## Tier 2 — substantiate claims currently stated as *estimates*

### E5. Closed-loop sounder demonstration — *upgrades §Adaptive from "decision logic" to "demonstrated loop"*
- `env_sweep.py` already runs sound → recommend → link in sequence. Run it across
  3 environments (clean / reverberant-contact / noisy) and report that the link
  ran at the recommended tier with the predicted success — a genuine closed loop.

### E6. Per-bin bit-loading at 64-QAM — *tests the "mid-band 35–45 dB SNR roughly doubles throughput" estimate*
- The `hi` tier (6 bits/bin, 64-QAM r3/4) is already in the `env_sweep.py` ladder.
  At the clean palm-rest cell, measure achieved goodput at `hi` vs `fast`. Could
  falsify the "double" estimate if a residual EVM/phase-noise floor caps it —
  also a useful, honest result.

---

## Tier 3 — defer (note as future work)

- **E7. Two-mic MRC:** stereo Pixel capture, MRC vs single-mic Δgoodput, in the
  high band where the mics decorrelate (the corrected MRC framing in the paper).
  Needs a stereo RX path.
- **E8. Wider device matrix / real-time streaming RX:** a third device class, or a
  ring-buffer streaming receiver to retire the "batch only" limitation. Both are
  larger than "round out the paper."

---

## Frequency-response figure grid (rides along inside Tier 1)

At each cell, one swept-sine sounding (`freqresp.py`) produces, from a single
capture: **|H(f)|** (magnitude transfer function), the **impulse response →
delay spread**, and **per-bin SNR** (with a silent capture). `plot_freqresp.py`
renders two paper figures:

- **`freqresp_by_path.png`** — one panel per (device, direction); within a panel,
  one |H(f)| curve per environment. Shows how placement moves the channel.
- **`freqresp_by_direction.png`** — downlink vs uplink per device. Visualizes the
  asymmetry (downlink flat-and-wide; uplink speaker-limited) the paper asserts in
  text.

These convert the channel-characterization section from tables of numbers into
curves a reader believes at a glance: band edges, the >17 kHz uplink collapse,
the delay-spread ripple deepening on a hard surface, the >18 kHz ultrasonic
roll-off.

---

## Harness

All scripts live in `scratch/hw20k/` and run from the repo root with the project
venv (`.venv`); they build on the existing `harness.py` (Mac/Android audio + ADB),
`ios_harness.py` (devicectl), `sounder.py` (recommendation), and `clib.py`
(library-native codec via `libcyrinxbulk.dylib`).

```bash
# offline self-tests (no hardware) — the analysis-math regression net
.venv/bin/python3 scratch/hw20k/freqresp.py selftest
.venv/bin/python3 scratch/hw20k/env_sweep.py selftest

# at the bench (needs the device + audio)
.venv/bin/python3 scratch/hw20k/freqresp.py mac2pixel  dist_20cm     # one F-resp capture
.venv/bin/python3 scratch/hw20k/env_sweep.py pixel down dist_20cm 8  # one full cell
.venv/bin/python3 scratch/hw20k/plot_freqresp.py                     # render the figure grid
```

Per-cell records append to `data/env_sweep.jsonl`; frequency-response captures to
`data/freqresp/<path>_<env>.json`. The minimum set that removes every caveat now
in the threats-to-validity list is **Tier 1 + E4**.
