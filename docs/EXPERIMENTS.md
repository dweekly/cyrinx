# Cyrinx physical-measurement campaign (to round out the whitepaper)

Fresh as of 2026-07-17.

## Measured findings — Cyrinx 2.0 Pixel goodput campaign (2026-07-17)

This campaign was prospective and paired: each candidate run was adjacent to a
legacy-profile baseline, the candidate order was balanced, failed or unattempted
runs could not disappear from the denominator, and the plan and frozen C
decoder were bound before playback. The Pixel 7a was face-up on 0.5-inch soft
cloth above the MacBook left function key; only the MacBook's built-in left
speaker was driven. Capture was 48 kHz stereo `UNPROCESSED`; Mac volume was 50%
and waveform peak was 0.18. The A/C was enabled but thermostat-cycling and
uninstrumented, and SPL was not measured.

### Accepted schedule-class result

The accepted comparison retained the website benchmark's five-frame schedule
and four 12,000-sample (250 ms) inter-frame gaps. The CP96, pilot-spacing-16,
64-QAM r2/3, 64-symbol candidate measured **65.875457875 kbps** mean span
goodput (65.266–66.641), with 4,215/4,280 blocks (98.4813%). Its paired
CP240/pilot-spacing-8 baseline measured 44.199424 kbps and recovered
2,999/3,000 blocks (99.9667%). The candidate won 8/8 balanced-order pairs
(exact one-sided sign test `p = 1/256`), and all 16 planned runs completed.

This is a throughput success and a reliability shortfall. The predeclared gate
requiring candidate block success not below baseline failed, so the result must
not be described as a simultaneous robustness improvement. Replaying the same
fresh captures through frozen current and legacy receivers isolates one real
algorithmic contribution: local known-pilot LLR weighting recovered 4,215
blocks versus 3,868 with global-only weighting (+347), improving every one of
the eight candidate runs and regressing none.

### Separate zero-gap confirmatory result

The highest eight-pair confirmatory schedule used CP96, pilot spacing 64,
64-QAM r2/3, 96 symbols, and no inter-frame gaps. It measured
**69.651849660 kbps** mean span goodput
(65.731–72.641) and **68.636220472 kbps gross**, with 6,129/6,760 blocks
(90.6657%). The paired baseline measured 48.102681 kbps and recovered
4,509/4,520 blocks (99.7566%). Again the candidate won 8/8 (`p = 1/256`) and
all 16 runs completed, but the reliability gate failed. This mean is 1.9045×
the 36.571 kbps website result, not “almost tripled.” Zero gap is a separate
schedule/accounting class, not an algorithmic gain.

The raw zero-gap manifest and captures are locally retained but ignored by Git.
A tracked evidence ledger publishes exact denominators and hashes, not the raw
waveforms. Reproduction identity is campaign seed
`20260800`, plan SHA-256
`8e5f1062dcccc42bbfac866a8659bf8d1884dd54bb054a9f42d944a1919e966b`,
execution-manifest SHA-256
`9846a33717c18e457169578217dbbdabe740eaa26c09965cb24ce128c9d1e462`, and
frozen-decoder SHA-256
`c6e4e5adc0865a379dc036c549cef4cd91528152ffffa66ee2fdf9be47580956`.

### Mechanism attribution and follow-up campaigns

The Cyrinx 2.0 profile combines CP96, a lower pilot tax, 64-QAM r2/3,
payload-independent local-frequency LLR weighting, held-out-pilot-only choice
between mic0 and MRC, and a qualified volume-50/peak-0.18 device route. Longer
frames and zero gaps merely amortize fixed overhead. The completed campaigns
and the one-pair lower-edge screen constrain the next experiment more usefully
than a capacity extrapolation:

| Campaign / screen | Mean goodput | Block success | Interpretation |
|---|---:|---:|---|
| pilot/16, 64 symbols, gap 0 | 69.110 kbps | 97.009% | best reliability among zero-gap pilot-spacing tests |
| pilot/32, 64 symbols, gap 0 | 68.960 kbps | 93.311% | pilot tax falls, but block recovery worsens |
| pilot/64, 96 symbols, gap 0 | 69.652 kbps | 90.666% | highest eight-pair confirmatory mean, strict reliability failure |
| pilot/16, 128 symbols, gap 0 | 66.102 kbps | 89.093% | lower mean in this separate campaign; causal length effect not isolated |
| lower band edge 600 Hz, one-pair screen | 39.948 kbps, 60/110 | 54.545% | failed against baseline 77/77 |

Across separate sessions and configurations, the 128-symbol zero-gap campaign's
observed mean was only about 0.34% above the fresh 64-symbol, 250 ms-gap result;
that is descriptive, not a clean causal estimate of either frame length or gap.
The candidate's paired advantage fell from about 24.6 kbps in the first pair to
6.1–8.7 kbps in the final two while the robust baseline stayed near 48.9 kbps.
Together with the reliability loss as pilots became sparser, this is evidence
of a time-varying/high-order-modulation problem, not a reason to make frames
still longer. A randomized 2×2 length/gap experiment is still needed to
separate those effects.

A naïve fixed-comb pilot tracker did not provide enough channel observability:
the same frequencies are sampled each symbol, leaving evolution between pilot
bins inferred rather than measured. A decision-directed update at
`alpha = 0.05` is a research candidate only; it can propagate wrong 64-QAM
decisions and lacks prospective validation. The next controlled comparison is a
denser or frequency-staggered pilot lattice plus a code rate near 0.70, between
the working r2/3 and failed r3/4 profiles. CP48, r3/4, and r5/6 are recorded
failures, not current optimization candidates.

## Measured findings — 2026-06-11 bench session (Mac ↔ Pixel 7a)

First live session with the new harness, one cell: `palmrest_stand_fan` (phone
face-up on cloth on the left palm rest, charge-port/bottom-edge toward the hinge
≈ toward the Mac speakers, MacBook on its stand as always, house fan running).
Data: `scratch/hw20k/data/freqresp/mac2pixel_palmrest_stand_fan.json`,
`data/env_sweep.jsonl`, figure `data/freqresp/freqresp_by_path.png`.

1. **E4 met and exceeded in a historical console aggregate — library-native
   38.400 kbps normalized payload.** The library C codec (`libcyrinxbulk`)
   decoded over the air. The
   historical harness printed coded-information rates of 13.1 kbps for QPSK
   r½, 26.2 kbps for 16-QAM r½, and 39.3 kbps for 16-QAM r¾. Contemporaneous
   console output reported **375/375 records across five independent one-frame
   r¾ trials**. Conditional on that aggregate, five times 19,200 payload bytes
   over five times 4 s is **38.400 kbps of user payload**. No matching
   machine-readable run record or raw capture was retained; the tracked
   150/150, EVM 0.187 env-sweep row is a different run. The normalized aggregate
   still *exceeds* the 36.6 kbps Python-harness headline, with that evidence
   limitation explicit.

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

4. **`rx_peak` is a poor coupling indicator.** The separately retained 150/150
   env-sweep run read peak 0.087 ("weak") at the same profile. That supports the
   peak-level observation, but is not evidence for the 375/375 console
   aggregate. Use SNR/EVM, not peak.

Paper edits these imply: replace the library-OTA caveat with the evidence-limited
38.400 kbps payload-normalized console aggregate; soften the 64-QAM headroom bullet to an
EVM-floor bound; add the
sounder-SNR-estimation finding; add the first measured Mac→Pixel H(f) figure.

This is the sequenced plan for the over-the-air measurements that close the
honest gaps in [the whitepaper](whitepaper/cyrinx-acoustic-link.tex) — chiefly
the single-geometry and single-ambient-condition limitations in its
threats-to-validity section. It is the experiment-side companion to
[PUBLICATION.md](PUBLICATION.md).

## Measured findings — 2026-07-06→08 OTA re-validation (Mac M4 ↔ Pixel 7a)

The A1-step-4 referee session: every prior OTA number predated the new C
diversity paths (A1 escalation, A2 `cyrinx_bulk_demodulate2`, A3 RS floor).
Bench geometry note: the MacBook now sits on a ~5" four-post stand, so the
desk plane is in the up-firing speakers' shadow — the June "middle" cell
(QPSK @ 4.3 kbps, EVM 0.45) is not reproducible with this geometry; desk
cells degrade straight to the floor tier. A Pixel OS update mid-session was
A/B-cleared (clean cell identical pre/post: EVM 0.064/0.066, 46.915 kbps of
ordered payload both).

| Cell (`adaptive.py` label) | Probe (EVM single / MRC, ds15) | Tier chosen | Result |
|---|---|---|---|
| `facedown_port_fnkey` (clean) | 0.064 / — , 0.4 ms | 16-QAM r3/4, clib | **46.915 kbps post-sounding PHY payload rate**, 225/225, MRC never invoked |
| `overhang_kbwell` (reverberant well) | 0.61 / **0.32**, 15.8 ms | QPSK r1/2 **via MRC**, delay-spread-sized CP | **11.366 kbps post-sounding PHY payload rate** — 0/75 blocks single-mic, **75/75 MRC-rescued**; the 2×-CP retry was available but not invoked |
| `center_front_portaway` (desk, shadowed) | 1.60 / —, 41.5 ms | MFSK floor | **138 bps**, 3/3 |
| `edge_below_laptop` (under stand, shadowed) | 1.1–1.6 / —, ~42 ms | MFSK floor | **138 bps**, 6/6 across two runs (after the fixes below; was 0/6) |

Three product-grade defects were found and fixed only because of this OTA
pass (digital calibration alone missed all three — details in
`scratch/hw20k/NOTES.md` and PR #52):

1. **RS floor erasure budget burn** — reverb yields low-confidence *correct*
   tone decisions; false erasures overloaded codewords plain error
   correction decodes. Errors-only decode now runs first. (0/6 → 6/6 at
   `edge_below_laptop`.)
2. **The floor lacked a combining rung** — added unit-total-energy stereo
   tone-energy combining (the non-coherent MRC analog); never worse than
   the best single mic on all saved captures.
3. **Single-mic EVM probe cannot see MRC potential** — at `overhang_kbwell`
   both mics probe undecodable yet MRC probes 0.25–0.32. The sounder now
   probes library-MRC EVM too (`via_mrc` tiers) and retries once at 2× CP
   before conceding to the floor (heavy-tailed reverb carries energy past
   the −15 dB spread). Floor 138 bps → coherent 11.366 kbps at that cell
   (82×).

These coherent rates divide verified payload only by the selected link frame's
active span; the preceding sounding/probe transaction time was not included and
can only be amortized over a longer session. The graceful-degradation ladder
post-fix: 46.915 kbps → 11.366 kbps (100 %
MRC-carried) → 138 bps floor (×2.03 the June repetition floor) → never zero.
The 138 bps number excludes the floor frame's unconditional 0.1 s tail pad;
including it gives 131.282 bps gross.

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

- **Fixed controls** unless that factor is the independent variable. Pin and
  report the device-specific qualified drive/capture route; for the 2026-07-17
  Pixel campaign this was Mac volume 50%, waveform peak 0.18, 48 kHz stereo
  `UNPROCESSED`, and left speaker only. Use ordered byte verification and report
  **span goodput** (first chirp to last data sample) plus gross goodput where
  schedule or transaction overhead matters.
- **Reps:** ≥ 8 runs per cell per direction → Wilson 95 % CIs on block-failure
  rate, matching the existing iPhone campaign (`campaign.py`).
- **Metric stack per cell:** goodput; block-failure rate + CI; median EVM; median
  per-bin SNR; −15 dB delay spread; rx peak; **sounder-predicted vs achieved tier**.
- **Calibration:** RELATIVE — drive amplitude and mic gain pinned, magnitude
  reported in dB re the in-band median. Valid for within-device cross-environment
  overlays and band-edge/shape claims; not absolute SPL, and cross-_device_
  magnitude is not directly comparable (stated on every figure).
- **Devices measured:** MacBook Pro M4 ↔ Pixel 7a, iPhone 17 Pro Max, and Moto G
  2026. The Moto clean-contact downlink and band-fitted uplink are supplemental
  single-cell results, not a reliability campaign. A wider device matrix remains
  future work.

---

## Tier 1 — essential (convert the paper's three biggest caveats into data)

### E1. Distance sweep — *kills the "single geometry" threat; headline goodput-vs-distance curve*
- **IV:** air-gap separation ∈ {contact, 5, 10, 20, 40, 80, 91 cm}, phone face-up
  on cloth, both directions. The 91 cm (3 ft) cell is the explicit next target
  for maximizing throughput at longer separation, not an extrapolation from the
  near-field headline.
- **Output:** goodput & block-failure vs distance, per direction, with the
  sounder's predicted tier overlaid; the `freqresp` magnitude curves at a few
  distances show *why* the curve bends.
- **Falsifiable claim:** "contact-range" — if the link is brittle past contact,
  that is the finding, stated with data.
- **Runs:** 7 distances × 2 directions × 8 ≈ 112.

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

### E4. Library-native full-rate reproduction — *historical aggregate; durable record incomplete*
- The June C-library console aggregate normalizes to 38.400 kbps of user
  payload (the old 39.3 kbps label counted CRC and fill), but lacks a matching
  machine-readable run record or raw capture. The prospective Cyrinx 2.0 C
  campaign above reached 65.875 kbps in the accepted schedule class. Remaining
  work is cross-device and distance reproduction plus durable archival of any
  future native-library reproduction, not another unarchived nominal-geometry
  claim.

---

## Tier 2 — substantiate claims currently stated as *estimates*

### E5. Closed-loop sounder demonstration — *upgrades §Adaptive from "decision logic" to "demonstrated loop"*
- `env_sweep.py` already runs sound → recommend → link in sequence. Run it across
  3 environments (clean / reverberant-contact / noisy) and report that the link
  ran at the recommended tier with the predicted success — a genuine closed loop.

### E6. 64-QAM profile limits and reliability — *partially completed*
- Uniform 64-QAM now closes with rate 2/3 and local LLR weighting; r3/4 and r5/6
  do not. The remaining question is not whether 64-QAM can decode, but whether a
  pilot/coding design can retain its goodput while approaching the baseline's
  block success across time, distance, and devices.

---

## Tier 3 — defer (note as future work)

- **E7. Two-mic policy generalization:** the stereo Pixel route and held-out-
  pilot-only mic0/MRC selector are implemented and were exercised in the
  Cyrinx 2.0 campaign. Measure its gain and failure modes across distance,
  orientation, and devices; do not select on payload or CRC outcomes.
- **E8. Wider device matrix / real-time streaming RX:** a fourth device class
  spanning another laptop or phone family, or a ring-buffer streaming receiver
  to retire the "batch only" limitation. Both are larger than "round out the
  paper."

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
`data/freqresp/<path>_<env>.json`. **Tier 1 + E4** addresses the listed distance,
orientation, ambient, and library-path caveats; it does not establish broader
device generality or a real-time streaming receiver.
