# Garage-scale plan for robust acoustic throughput

## In plain English

We can move data over sound between a laptop and a phone, fast, when the two are touching through a folded cloth. We do not know what happens when they are simply near each other in a room, and one earlier measurement suggests the answer may be "nothing at all" — in a reverberant desk setup the link carried zero data at every setting we tried, and the only thing that got through was a slow fallback.

This plan finds out, in a garage, with equipment we already own: a laptop, the phones on hand, masking tape, and a handful of fifteen-minute sessions. It records everything it plays and hears so that most of the thinking can happen afterwards on a laptop instead of standing in a garage.

The first thing it measures is not speed. It is how long the room keeps echoing, because if the echo outlasts the gap our signal leaves between symbols, then faster codes and better error correction cannot help and we need a different kind of signal entirely. That decision comes early, on purpose, so we do not spend weeks tuning something that was never going to work at that distance.

Date: 2026-09-04. Revised 2026-09-06 after a repository cross-check, and 2026-09-08 after the first measurements. Status: G0 landed, first baseline cell measured.

## State of play, 2026-09-08

**G0a and G0b are on `main`** (PRs #89, #87): capability-derived link geometries, a Schroeder delay-spread readout with explicit validity, the acquisition adapter, self-calibration, and cross-device characterization, under [scratch/garage/](../../scratch/garage/README.md).

**The first real baseline cell is measured, and stage 1G fired on it.** A Pixel 7a face up about a foot from the MacBook, both stationary ([notebook](../../scratch/hw20k/NOTES.md), 2026-09-08):

| link | strong-tap spread | chirp peak/mean | best EVM | ordered blocks |
|---|---|---|---|---|
| Mac → Pixel | 48.3 ms | 135.6 | 1.102 | 0 / 52 |
| Pixel → Mac | 82.3 ms | 72.9 | 1.944 | 0 / 38 |
| Mac → Mac (control) | 0.9 ms | 138.4 | 0.136 | 52 / 52 |

Both directions acquire and fail in the demodulator, with measured strong-tap spreads well beyond the declared 16 ms practical guard budget. Longer prefixes are expressible — the validator accepts any prefix up to the FFT size — so the budget is what these spreads exceed, not a physical limit; entry 13 measured NFFT 4096 with a 43 ms guard recovering zero blocks, which is why widening it is not the answer being reached for. The delay-spread readout predicted it before either link was attempted, and the same tooling carries 52 of 52 blocks on the control, so this is a property of the channels rather than of the apparatus.

**What this changes about the sequence below.** The map was written expecting a mix of easy, marginal, and failing positions, with the failing ones the exception. The first off-chassis cell at one foot is a stage 8 referral, which makes two questions more urgent than the rest of the stage list:

1. **Where is the boundary?** The working geometry is contact through cloth and the failing one is a foot of air; the plan's C0 at 10 cm is untested and is now the most informative single cell available. Run the distance ladder inwards from one foot before anything else.
2. **Does the non-coherent bearer carry this cell?** Entry 13's only surviving waveform was MFSK at 89–267 bps. If a foot of air is a product requirement, that is the path, and C3-11/C3-12's bootstrap bearer is where it lands rather than a tuned OFDM profile.

Stages 2 through 4 remain correct for positions that pass the gate. They should not be spent on positions that do not.

**Next implementation task is G1**, the capture runner, which the three ad-hoc scripts under `scratch/garage/` now describe the requirements for by example.

**Where the work happens.** Branch `docs/garage-throughput-plan`, worktree `~/dev/cyrinx-WORKTREE/garage-plan`, tracked by its own pull request. Research code lands under `scratch/garage/`. Anything that changes the profile registry, the C receiver, or a published contract leaves `scratch/` and goes through the ordinary Cyrinx 3.0 pull-request rules.

**Relationship to the existing plans.** This is an exploratory subset supporting [EXPERIMENTS.md](../EXPERIMENTS.md) E1 and E2, with observations relevant to E3 — designs that exist and whose result tables are empty. It does not supersede them and does not complete them: E1 specifies seven distances at eight repetitions with predicted-versus-achieved tiers, E3 specifies controlled interferers, and the map below uses fewer positions, two repetitions, two fixed profiles, and whatever ambient noise the garage supplies. Results extend the existing tables rather than starting a second ledger, tagged with session, profile, and repetition count, with prediction fields left unavailable and E1–E3 left open. Two profiles cannot establish an achieved ceiling tier. It is also not a second delivery path. Every endpoint capability it needs is an existing numbered item in the [3.0 delivery plan](../CYRINX_3_PLAN.md), whose status ledger records C3-06 through C3-27 as not merged; Stages 5 and 6 below therefore hand measured inputs to those items instead of building a parallel transfer stack in `scratch/`.

This plan follows the [throughput review](acoustic-throughput-review-2026-09-04.md) and assumes a laptop, the phones already available, a garage, and occasional short sessions with an operator. It requires no new measurement equipment. It specifies work to do; the new runner, reports, receivers, and profiles described below do not yet exist unless explicitly identified as existing code.

**The objective is to increase useful throughput across ordinary placements without requiring a carefully maintained acoustic setup.** Start with one laptop/phone pair, both directions, and stationary operation out to roughly one metre. Add a second phone and modest hand movement after establishing a repeatable baseline. Preserve the existing fast near-field profile as a control. A high rate at one chosen spot and a rate that survives moving the phone are separate results.

We can make credible relative comparisons in a garage. We cannot use them to claim calibrated radiated power, exact physical capacity, precise Doppler tolerance, or performance across all rooms and devices. Those limits should narrow the claims, not prevent useful experiments.

**What the repository has already measured about this geometry.** Three entries in [NEGATIVE_FINDINGS.md](../NEGATIVE_FINDINGS.md) constrain the plan before any new recording:

- **Entry 13.** With the phone off-chassis on a desk about seven inches from an elevated laptop, coherent CP-OFDM recovered zero blocks at every modulation and coding scheme attempted: 16-QAM r3/4 → 0/375, QPSK r1/2 → 0 at EVM ~2, BPSK r1/2 with a 32 ms guard → 0/125, and NFFT 4096 with a 43 ms guard → 0. Chirp sync locked cleanly (matched-filter peak/mean 104), so this was not signal loss. Schroeder delay spread was 35.8 ms at −10 dB and 82 ms at −20 dB. The only waveform that carried anything there was non-coherent MT-FSK at 89–267 bps.
- **Entry 1.** Guard length should be sized to the strong-tap spread, not the −30 dB point. A −30 dB figure understates and overstates the problem in different channels and must not be the number a decision is made on.
- **Entry 8.** The historical working condition is contact through roughly half an inch of cloth (median SNR ~22 dB, 16-QAM, 36.5 kbps), not proximity in air. Bare metal-to-metal contact measured worse (~10.5 dB, QPSK, 14.7 kbps).

Entry 13 does not predict the garage result. That geometry had an elevated laptop and a desk-surface reflection at the phone, so separation was one variable among several, and a workbench at 50 cm is a different reflection structure rather than a longer version of the same one. What entry 13 does establish is that **all-fail at range is a live outcome this plan must budget for rather than discover in Stage 4.** Stage 1 therefore measures delay spread per cell and Stage 1G turns it into an explicit fork.

The fork is a spending decision, not an impossibility proof. The 768-sample guard is the longest *registered* one, not the longest expressible: the profile validator accepts any cyclic prefix up to the FFT size, so the registry inventory is not a physical boundary. Nor does energy beyond the guard prove that timing correction, input selection, or a different receiver must fail — reception under insufficient cyclic prefix is an active research topic, not a closed question. What entry 13 supports is narrower and still decisive for planning: continuing to climb the guard and MCS ladder in a geometry with that much late energy has already been measured as unproductive, and the same spend buys more in Stage 8.

The working method is: make recording easy, collect a small useful corpus, do most iteration offline, and return to hardware only for a specific comparison. Each live batch should fit in about 15–30 minutes, with explicit limits on playback and automatic retries. Estimates below are operator-session budgets, not promises about software completion time.

**The order of work is deliberately small and conditional.** Each stage should produce an artifact or answer that makes the next decision easier.

| Order | Explore or build | Reason for this position | Required human involvement |
|---:|---|---|---|
| 0 | Minimal capture/replay runner, the conservative profile that does not yet exist, and simple placements | Prevent lost evidence and repeated setup work; the conservative half of the baseline map has no profile to run today | One 15–25 minute setup session |
| 1 | Baseline map with per-cell delay spread, plus baseline-again comparisons | Learn where the existing system actually fails, and whether that failure is one this waveform class can address | Two 20–30 minute sessions; a short repeat on another day |
| 1G | **Experiment-budget gate:** threshold-specific remaining-energy crossing times against a predeclared guard budget, after replay diagnostics pass | Decides where the next hours go per position, Stages 3–4 or Stage 8 | None; a decision on already recorded data |
| 2 | Replay diagnostics and receiver-only changes | Many hypotheses can be tested on the same recording | Normally none |
| 3 | Fresh channel tracking and time-scale correction | Address observed receiver mismatch and motion | One 15–25 minute finalist comparison |
| 4 | One modern FEC family | Measure whether coding can recover reliability or enable a faster profile | One 15–25 minute finalist comparison |
| 5 | Physical validation of C3-19 best-effort messaging, then C3-22 reliable completion | Translate PHY gains into completed messages, in dependency order | One 15–30 minute session per validation |
| 6 | Measured profile set and policy inputs for C3-20a–C3-23 | Let the actual link choose among measured winners | One 20–30 minute mixed-condition session |
| 7 | Second phone, movement, and routine repositioning | Check whether the result survives leaving the development condition | Several short sessions as needed |
| 8 | Stronger equalization or single-carrier challenger | Only when residual multipath/tracking failures justify the work | One bounded comparison after offline screening |
| 9 | Higher-rate profiles, finer loading, and possible MIMO | Spend complexity only where measured headroom remains | Optional, hypothesis-specific sessions |

Stages 3, 4, and 8 can change order when diagnostics identify a specific dominant failure. Bring one second-phone check into the first finalist comparison when a phone is available; Stage 7 expands that check into transfer and handling tests. Stage 7's movement screen moves earlier if ordinary handling breaks the link. **The gate reads the −10 dB crossing.** Five self-calibration captures of one unchanged geometry put the −20 dB figure across a 5.4 ms range straddling the budget in both directions, while −10 dB moved 0.2 ms (`scratch/hw20k/NOTES.md`, 2026-09-07). A deeper threshold is reported for context but cannot decide a 16 ms budget it cannot resolve. This agrees with entry 1, which sizes the guard to the strong-tap spread, and with entry 13, whose decisive 35.8 ms figure is a −10 dB number. Every cell in a comparison must also share one analysis horizon, since a shorter window pulls crossings earlier.

Stage 1G is a budget gate rather than advice, and it runs *after* Stage 2's capture-integrity and replay diagnostics, never instead of them — a zero-block cell caused by a broken route or a windowing bug must be excluded before any conclusion about the channel. Declare the practical guard budget explicitly before the batch, as a number chosen for overhead reasons rather than read off the current registry. A position that keeps failing while carrying substantial energy beyond that budget moves to Stage 8 or non-coherent screening; the claim recorded is "further ordinary guard and MCS tuning is not justified by the current evidence for this position," not that the position is unrecoverable. Positions inside the budget continue through the sequence normally. Do not proceed through the sequence mechanically.

**Use household objects to make placement repeatable enough.** Put masking-tape outlines on a table or workbench, use a ruler or tape measure, and use books or existing stands to hold devices in a repeatable pose. A folded cloth under the phone is useful, but record it and keep it consistent within a comparison. Avoid blocking speaker or microphone openings with the fixture. Keep the laptop lid angle approximately fixed. One photo per setup, stored locally, is sufficient; repeated photographs are unnecessary unless something moves.

Keep phone cases either on or off consistently during a batch. Mark approximate speaker-to-microphone distance rather than measuring between device centres. If the actual acoustic centre is uncertain, use a visible chassis landmark consistently and describe that convention. Centimetre-scale placement records are sufficient for the first comparisons. Exact geometry is not needed to see a large, repeatable advantage.

Record garage-door position and conspicuous noise sources. It is acceptable for a refrigerator, fan, traffic, or household activity to vary. Compare candidates close together in time, and collect a few seconds of room tone with each attempt. Do not try to reproduce a laboratory noise field or control humidity. Briefly describe unusual events such as a door slam. A third phone can play the same noise clip from a marked spot in a later experiment, if one is already available, but it is not required and its volume is only a repeatable setting.

Use existing comfortable playback levels. Do not automatically raise volume after failed decodes. Pick a fixed device-specific level during setup, record the visible setting and API readback where available, and retain it through a batch. A lower-level comparison can reveal clipping or gain compression, but lack of improvement does not isolate a physical cause. The built-in microphones provide digital amplitude and spectral observations, not calibrated SPL or a transducer-only distortion measurement. No calibrated meter is a prerequisite for the proposed comparisons.

**An association begins with three phases, and only the third needs the peer.** This shapes what the runner has to capture. (1) *Self-calibration*, purely local: each endpoint measures its own speaker against its own microphone for response, clipping, nonlinear products, and route identity — C3-20b, and the reason capability must be measured rather than declared, since a Moto G with Dolby DAX enabled has different real capability than the same phone with it off. (2) *Environmental sampling*, purely local and passive: room tone and noise floor at the session's fixed gain. (3) *Channel characterization*, turn-taking: each endpoint transmits a probe in turn while the other measures, giving one result per directed link — C3-21's bidirectional sounding, arbitrated by the same half-duplex turn-taking as data rather than by a separate mechanism. Contention only exists before association, which is where C3-18 already puts listen-before-talk and randomized slots; an associated pair has a turn to hand over, not a channel to contend for.

Phase 2 is not optional bookkeeping. A Schroeder decay measurement needs a noise-only reference at the same gain to know where its curve stops being signal, so **the runner must capture room tone per position and per direction**, not once per session, and retain it beside the sweep.

**Stage 0: make one recording dependable and reusable.** Reuse the raw Android capture/playback primitives in [harness.py](../../scratch/hw20k/harness.py) and the explicitly selected C codec in [clib.py](../../scratch/hw20k/clib.py). Keep host decoding for early research so DSP changes do not require reinstalling the phone app. Select a single attached device explicitly. The [Android HIL documentation](../../Apps/HIL/android/README.md) identifies the existing raw-PCM workflow and the legacy decoder's limits. The [Apple HIL app](../../Apps/HIL/README.md) exercises the older acoustic transport; successful operation of that path is not proof that the high-throughput bulk receiver is integrated.

The first software deliverable is a thin garage runner under `scratch/garage/`, rather than another modem implementation. It should:

- load a frozen profile and the intended phone/route;
- request one simple placement label from the operator;
- generate independently seeded payloads and retain their exact transmit waveforms;
- perform a brief route/capture check and record all available input channels;
- save PCM, request IDs, route logs, sample counts, and results automatically;
- decode with the selected C binary and verify bytes at their scheduled positions, reusing the payload-independent anchoring and scheduled-slot assignment already in [goodput_campaign.py](../../scratch/hw20k/goodput_campaign.py) rather than reinventing it — the batch contract supplies no timing, and a successful API call can contain zero valid blocks, so call success is not a detection flag;
- freeze and retain the actual output channel matrix (`[waveform, zeros]`, not a channel count), since entry 7 measured that driving both Mac speakers wrecks the link, and record Mac input gain alongside playback volume;
- retain the exact post-routing, post-quantization transmit file as well as the generated waveform, and the frozen decoder build inputs rather than only its hash;
- display one result per attempt and write a short comparison report;
- stop cleanly and resume incomplete batches without overwriting earlier attempts.

**Two instruments the baseline map needs do not exist yet.** Both belong to Stage 0, and neither is a `scratch/` hack:

- **A conservative geometry, in research code rather than the registry.** Line for line, the five rows in [the profile registry](../../Sources/CCyrinx/cyrinx_profiles.c) are 16-QAM or 64-QAM; there is no QPSK profile and no rate-1/2 profile in the registry at all, and the longest guard is 768 samples — 16 ms at 48 kHz, which is 37.5% of the useful FFT interval at NFFT 2048 and 27.3% of the complete symbol — with the remaining four at 240 and 96 samples. The conservative arm of the Stage 1 map therefore had nothing to run.

  It does not need a registry row. The C bulk codec takes a `cyrinx_bulk_config` directly, so host-side research never resolves a profile through `cyrinx_profiles.c`; a registry row is C3-04 contract surface — canonical JSON fixture, C table, identity expectations, and their tests moving together — and the Kotlin/JNI registry view is still an open C3-04 merge gate, so a row added now would carry no JVM representation. A row is what a geometry earns after it wins a comparison. **Delivered** in [scratch/garage/geometries.py](../../scratch/garage/geometries.py) with the source of every constant recorded, and a byte-exact digital round trip through the C codec covered by its tests.

  Two constraints shaped it. First, entry 13's 35.8 ms strong-tap spread corresponds to about 1,719 samples at 48 kHz, and NFFT 4096 with a 43 ms guard was already measured at zero blocks in that geometry, so guard length is not a free parameter to spend the problem away with — which is why Stage 1G is a budget gate rather than a ladder, and why `PRACTICAL_GUARD_BUDGET_MS` is declared as 16.0 ms up front.

  Second, **the band is derived per directed link from endpoint capability, and no geometry is named for a role.** The occupied band is a property of the transmitting speaker and the receiving microphone, so it is the intersection of the two, capped at 18 kHz for a phone transmitter by entry 9's phase incoherence. Calling one end "uplink" would bake in laptop-strong/phone-weak, which is wrong for a laptop pair, wrong for a phone pair, and backwards for the iPhone/Moto pair, where the cheaper phone emits the wider band (600–14000 Hz against 600–11000 Hz). C3-18's merge gate is that two *symmetric* peers converge on complementary roles elected at runtime, with capability carried in the beacon's capability hash, so this is the contract the delivery plan already set. An uncharacterized endpoint resolves to the narrowest measured band, so unknown degrades to conservative. [uplink_qpsk.py](../../scratch/hw20k/uplink_qpsk.py) is the prior art for the narrow-emitter case, and C3-20b eventually replaces the measured table with self-characterization.
- **A delay-spread readout wired into the runner.** The analysis already exists and does not need writing: `delay_spread()` in [freqresp.py](../../scratch/hw20k/freqresp.py) computes a Schroeder energy-decay curve from the main tap and reports −10/−15/−20 dB, which is the convention entry 13's figures come from. Do not extend [characterize.py](../../scratch/hw20k/characterize.py) instead — it reports the last individual sample above a peak-relative −30 dB threshold, which is a different quantity and the one entry 1 rejects. Keep −15 dB reported so existing tables stay comparable, and label any peak-relative tap figure separately.

  The readout is being rebuilt against [its own plan](delay-spread-readout-plan.md); the module on the branch cannot report on the acquisition primitive it was written for. The integration work is the missing part, and it is where the measurement can quietly go wrong. `freqresp.py` truncates the impulse response roughly 120 ms after its largest peak and decays from that peak, so it will emit a number whether or not the window contains the response and whether or not the decay is separable from the noise floor; integrated decay measurements are contaminated by background noise as a matter of course. The readout must therefore be allowed to return `invalid`, `noise-limited`, or `window-limited` instead of a figure, and a cell reporting one of those is not evidence for the gate. Both entry points also force Mac output volume to 100, so run the analysis over garage-owned acquisition at the session's fixed level rather than calling their hardware setup.

  Budget one sweep **per position and per direction**, recording all available inputs at once — the speaker and microphone paths are not the same channel, and the two receive channels need not agree. Store direction, channel, route, analysis band, time reference, and window with every result. This is a separate stimulus with its own airtime, not a by-product of the sync chirp.

Keep the expected payload and its seed in the transmitter/verifier layer. They must not be available to receiver synchronization, mode selection, channel estimation, or ordinary decoding. Known training sequences remain part of the protocol. Diagnostic oracle code that uses transmitted data must be a separately labeled execution path.

Start with the smallest usable metadata record: run ID, time, device/model, OS, available route fields, actual sample rate/channel count, volume setting, profile, source/decoder hashes, placement, direction, and noise note. Automate hashes and file naming. Do not require the operator to fill out a large questionnaire. If physical microphone identity is unknown, label logical channels honestly; mono performance can still be measured. A route that demonstrably changes during an A/B pair invalidates that controlled comparison, but its failure record remains in the operational results.

The normal research burst should contain two data frames with a declared 250 ms inter-frame gap and the existing stream-end protection pad. Prepend a short room-tone recording interval and retain post-roll. These two-frame trials are a new measurement class, not repeats of the published five-frame benchmark. Keep that five-frame profile available for a final near-field comparison when relevant. Profiles may have different airtime and payload counts; calculate rates from actual scheduled samples instead of assuming equal duration.

Store each session in an ignored directory such as `artifacts/garage/YYYY-MM-DD/session-id/`, containing the plan, transmit waveform, receive PCM, verifier payload, metadata, and ordered block results. Save raw integer PCM or lossless floating-point arrays with an explicit layout; do not use lossy audio compression. At 48 kHz, stereo PCM16 is about 11.5 MB per recorded minute, so a compact corpus is manageable. Preserve original acquisition data, and keep a second local copy when practical.

**Finish this stage when** one clean transmission can be recorded, replayed twice with identical ordered decisions, and scored correctly after a deliberately truncated or misordered fixture. Confirm that all-zero capture, wrong format, stale request output, and a missing preamble are visible failures. This is a small tool-integrity check; it does not need a full device-characterization framework.

**Stage 1: learn the existing link's failure boundary.** Use these five positions with one primary phone. Every cell records strong-tap delay spread at −10 dB and −20 dB alongside its recovery result; a cell without a delay-spread number cannot be read at the Stage 1G gate.

| Label | Placement | What it tells us |
|---|---|---|
| CC | Contact through the documented ~½″ cloth, the historical working geometry | Rig integrity: the one position we expect to pass. Without it, an all-fail map cannot be distinguished from a broken runner |
| C0 | About 10 cm, stationary, unobstructed openings, phone on the documented support | Whether a simple strong link works |
| C1 | About 50 cm, same height and orientation where possible | Whether there is useful margin beyond close placement |
| C2 | About 1 m, same orientation | Initial range/reverberation challenge |
| C3 | About 1 m, phone turned 90 degrees in the table plane | Sensitivity to orientation and different reflection paths |

CC is a control for the apparatus, not a rate claim: it needs to decode, not to reproduce 65.875 kbps in a different room, and a shortfall against the historical number is uninformative here. It is in the map because C0 through C3 are all off-chassis in air, entry 13 recorded zero blocks in an off-chassis geometry, and a map with no expected-pass cell cannot separate "this channel is hard" from "the new runner is broken" — Stage 0's fixture checks cover truncation, format, and missing preambles, but not an end-to-end acoustic path. Make it operational: CC must decode in a given direction before that direction's off-contact failures mean anything. If it does not, stop the range batch and diagnose rather than collecting more cells, and retain every attempt either way. Do not spend the first day trying to reproduce the historical headline.

Use two profiles per direction initially: a conservative QPSK rate-1/2 profile with a long guard, and the best existing higher-rate profile appropriate to that direction. Freeze the exact band, FFT, CP, pilot density, and frame length in the session plan. For the forward link, the existing 64-QAM rate-2/3 profile is a useful fast control; do not assume it suits the reverse link. An obviously unusable upper band in a phone-to-laptop path can be excluded during declared calibration, then held fixed during the deciding comparisons.

Run each profile twice in each position in both directions: **5 positions × 2 profiles × 2 directions × 2 repetitions = 40 short bursts**, plus one sine-sweep delay-spread measurement per position. At roughly 45 seconds per burst including repositioning and transmit/receive role flips, that is two sessions rather than one; split it by position and keep the day labels. Reverse the order of the two profiles in the second repetition. Limit unexpected extra probing; note any additional exploratory attempts separately. This is a discovery map, not qualification. If the operator session runs out of time, finish the remaining positions later and retain the day labels.

At one stable position, add three A/A pairs: play the same frozen baseline waveform twice without changing the setup. These six bursts reveal how much variation appears without an algorithm change. On another day, replace the phone into its tape outline and repeat a small subset. If baseline results routinely move by 5–10%, a claimed 2% acoustic gain is not an efficient next objective. Receiver-only same-recording gains can still be assessed precisely, but their practical value needs fresh recordings.

For every attempt report detection success, verified blocks/scheduled blocks, verified payload over scheduled airtime, rate including emitted pads, and capture/route faults. Before a real session exists, host orchestration and ADB time belong in a separately named bench wall-time metric, not in a claim about on-device application performance.

**Score a message-shaped endpoint from Stage 1 onward.** The [review](acoustic-throughput-review-2026-09-04.md) puts completion probability by deadline ahead of first-pass block recovery, and Stage 4's own stopping rule concedes that a near-perfect baseline may miss too few blocks to resolve a halving. The endpoint that closes part of that gap now is a predeclared **first-pass 4 KiB recovery** figure: fix a message placement against actual frame-completion boundaries and report, per attempt, whether that message's blocks all arrived on the first pass. It costs nothing beyond the ordered block results the runner already retains.

Do not report a completion-by-deadline number off these captures without doing the harder work first. A two-frame research burst contains no repair transmissions, no acoustic acknowledgments, no turnaround times, and no channel observation spanning the deadline, and the convolutional interleaver runs across a frame — so CRC blocks are not independently timed retransmission units and reassigning them to hypothetical repairs changes what the experiment means. A deterministic rearrangement of the same sparse losses also adds no statistical evidence when the baseline barely fails. If a modelled figure is wanted, make it an explicit optional artifact that states repair packing, full-frame airtime, acknowledgment assumptions, turnaround, trace resampling and correlation, and what happens when the trace runs out, and call it a **modelled completion estimate under stated assumptions**. Actual completion by deadline stays Stage 5's measurement.

**Choose three development conditions from this map:** one easy, one marginal, and one failing or much slower. Reserve the next day's reset placement and, if available, one second-phone placement for later checks. Do not tune on every recorded condition and then call those conditions held out. When there are too few untouched cases, call the result a repeatability check instead of manufacturing a validation split.

**Stage 2: diagnose recorded failures before building a new waveform.** Produce a compact replay report showing detection/timing, per-symbol known-pilot error, error by frequency region, clipping/discontinuities, and ordered block recovery. Examine each logical microphone and existing MRC on the same capture. Plot full complex-channel drift only where the known training supports it; use unavailable or unknown fields rather than inferred measurements.

The first branch decisions should be explicit:

| Observation | First experiment | Avoid concluding |
|---|---|---|
| All-zero samples, wrong route, discontinuities | Repair the audio/capture path | That the acoustic channel has insufficient capacity |
| Acquisition fails but useful signal is visible | Receiver band isolation, normalized detection, timing/window checks | That stronger FEC will solve acquisition |
| Mostly the last symbol fails | Verify render continuity and tail handling | That the whole modulation is unsuitable |
| Early symbols decode, later pilot error rises | Shorter coherent blocks, fresh channel training | That more sparse pilots will improve net throughput |
| One microphone has complementary fades | Existing MRC, then covariance-aware combining | That two logical channels imply two independent spatial streams |
| Only high-drive captures degrade | Lower-level A/B at fixed waveform | That phone mechanics rather than OS processing caused it |
| Strong variation between frequencies with stable timing | Coarse-band selection or an equalizer experiment | That raw PSD thresholds predict code recovery |
| Large residual error persists despite long CP | Adjacent-symbol/interference diagnostics | That all coherent signaling is impossible |

Test receiver-only changes on identical PCM: corrected synchronization/windowing, bounded pilot reliability changes, input selection, and regularized covariance-aware combining. Begin with the simplest candidate that explains the observation. Covariance estimates need enough independent noise/residual observations and regularization; a noisy estimate can be worse than current MRC. With only one usable input, skip multi-input research rather than blocking progress.

Receiver decisions must use information available in normal reception. Oracle decoding of all alternatives can show what information might be recoverable, but the deployed selector must be scored separately. Same-frame expected bytes or CRC outcomes cannot choose a receiving policy. Future adaptation can use acknowledgments from completed earlier attempts.

**Continue when** a candidate recovers a meaningful number of lost blocks on more than one troublesome capture without damaging the easy captures, or materially improves timing robustness. For a nearly perfect fast profile, first-pass goodput has little remaining headroom; reliability can be the meaningful endpoint. Stop a weak idea after a few controlled variants rather than accumulating a large hyperparameter sweep. Keep at most one finalist per mechanism for live confirmation.

**Stage 3: refresh the channel estimate and correct time-scale drift.** The first transmitter-changing experiment should usually test whether the channel becomes stale within the current frame. Compare the incumbent with a shorter coherent block or periodic known training, keeping modulation/code rate fixed and explicitly counting extra training. Start with one change, not a simultaneous change in FFT, pilots, code, and QAM.

For static garage captures with slow drift, start with periodic full-band training because its interpretation is straightforward. Then compare a frequency-staggered pilot pattern if the full-band refresh indicates a useful gain. Estimate the complex channel with a physically sensible delay/phase representation; do not repeat the failed raw complex-bin smoothing or terminal-interpolation experiment under a new name. A positive oracle interpolation result would only justify a realizable known-training comparison.

Implement audio-derived fractional time-scale correction before the FFT when measurements show accumulated timing drift or motion-related intercarrier error. First verify a resampler on generated waveforms with known clock offsets. Then replay real captures. Do not require IMU integration, exact hand velocity, or a motion stage. A common resampler is only one hypothesis; moving reflected paths can retain different residual shifts.

For a motion screen, put the phone on a small board or book that does not cover its openings and move it between two tape marks about 10–20 cm apart over a few seconds. Avoid dragging it directly on the workbench, which introduces contact noise. Repeat a roughly similar path rather than asserting a measured speed. Compare stationary runs using the same support/hand contact. Record stationary and moved results separately. This establishes practical sensitivity, not a calibrated Doppler curve.

Choose the best change using identical received recordings where possible; changed pilot patterns or frame structures require new transmitted waveforms. Advance only if verified recovery or net rate improves after its overhead. A visually cleaner channel estimate alone is insufficient. Preserve the current receiver if denser training costs more than it saves.

**Stage 4 needs a roadmap amendment before it runs.** [ROADMAP.md](../../ROADMAP.md) lists LDPC/polar FEC under explicit deferrals, triggered only once soft-decision FEC is a measured bottleneck after tracking and loading are fixed. This stage is a bounded diagnostic asking whether coding matters at all, which is not the same as the broad loading program that deferral gates on — but the conflict is real and gets resolved in the roadmap, not by proceeding quietly past it. Amend that entry to permit the bounded comparison while keeping its promotion restrictions.

**Stage 4: measure the benefit of modern coding with one controlled challenger.** Use one mature QC-LDPC family with soft decoding and early termination. Begin with one block size, approximately 4–8 thousand coded bits, and a small rate set around 1/2, 2/3, and 3/4 where the chosen family supports them. These are experiment-design choices, not a claim that these exact lengths or rates are optimal. Keep the convolutional code as the baseline.

Before new playback, verify byte-exact digital operation and generate error-rate curves through simple known channels using the actual encoder, demapper, and decoder. Add modest multipath, burst interference, and sample-clock perturbations incrementally. The first simulator should answer these narrow questions; it need not model the whole garage. Apply replay-derived channel parameters only with their stationarity limitations recorded. Replaying old PCM through a different code does not recreate the waveform that code would transmit.

Run two separate comparisons in order:

1. At approximately the same net code rate, modulation, band, drive, and training schedule, does the new code improve loss or tolerate a more difficult position? Match or report actual information-bit counts and rounding overhead.
2. Only if the first comparison helps, can the added margin support a faster net profile while retaining the chosen recovery objective?

Use independent codewords with a bounded time/frequency interleaver, rather than automatically copying the existing whole-frame layout. Measure CPU time, peak working memory, and decode latency as well as recovered bytes. A mobile performance smoke test should happen before a large OTA campaign; wait until Stage 5 for full streaming integration.

Do not combine the first coding comparison with probabilistic shaping, per-bin loading, new constellation mapping, and channel estimation changes. If coding adds little, use the result to prioritize residual receiver failures. Do not interpret a stronger code failing on bad synchronization as a coding-theory failure.

**Use one lightweight comparison rule for new garage experiments.** Existing frozen campaigns retain their original outcomes and stop conditions. This plan creates a new exploratory experiment class; it does not retroactively pass pre-EQ, DFT-spread, or other stopped candidates. Library/default changes still need an explicit review against the applicable repository criteria. No new calibrated-lab requirement is introduced for an exploratory relative result.

For each new candidate, write a short plan before the deciding batch: question, baseline, one candidate, conditions, order, primary endpoint, tolerated regressions, maximum attempts, and what would make us stop. Let the runner save configurations and hashes. A short Markdown note plus generated manifest is enough.

Run two nearby-time pairs in the marginal condition, one A then B and one B then A. A is the frozen baseline, B the candidate. If it clearly loses, stop. A tie is not permission for an indefinite tuning campaign. If promising, run eight fresh pairs in conditions where the primary endpoint can improve, distributed over at least two sessions with the phone removed/replaced between sessions. A concrete allocation is two marginal-condition pairs and two reserved-condition pairs on each of two days, with balanced A/B order. Add one easy-condition control pair per day to check regressions; those two control pairs are separate from the eight gain comparisons, since an already perfect baseline may only permit a tie. Freeze this allocation before the deciding batch. Report the placement/session groups; do not treat all blocks or all within-session pairs as independent laboratory trials.

Use these suggested engineering decision criteria, fixed before the deciding batch:

- For a rate change, seek at least roughly 10% gain in the declared target condition, with no substantial new outages or material loss in the easy control. An observed gain smaller than baseline variation is inconclusive.
- For a robustness change at a saturated profile, seek at least a halving of the observed missing-block fraction in the marginal condition, with near-field rate loss no worse than 5%. If the baseline misses too few blocks to resolve this, choose another predeclared endpoint or a new experiment; do not reinterpret a handful of losses.
- Prefer seven wins in eight fresh pairs for a convincing local result, but also require the practical endpoint and inspect results by day and condition. Seven wins alone does not prove generality or justify treating correlated pairs as independent.
- Record an inconclusive outcome when day-to-day effects dominate. Repeat once on a fresh session if the possible gain matters; otherwise defer the idea.

These are local engineering filters, not confidence guarantees or replacements for an existing stricter promotion contract. With a small sample, show every run, medians, observed worst cases, and zero-throughput attempts. Avoid precise p10 claims or rare-failure guarantees. A few clean transfers cannot establish 99.9% reliability.

Keep acoustic failures in the result. If recording fails before a usable waveform exists, keep a separate acquisition-failure entry and count it in the bench-operational attempt rate. Do not substitute a later success under the original run ID. Allow at most one automatic retry for a clearly identified acquisition failure, with its own ID; do not automatically replay ordinary decode failures. An A/B pair disrupted by an unrelated route change can be excluded from the controlled PHY comparison with a stated reason, while still appearing in the full attempt ledger.

**Stage 5: feed the smallest real on-device transfer, which is Phase D's to build.** After one promising receiver or code result, shift effort toward endpoint execution. Do not wait for every PHY research idea.

This stage does not author a transfer stack, and it is two validations rather than one, because the delivery plan's own dependency graph separates them. Best-effort manual-profile messaging is C3-19, which sits behind C3-14/C3-15 PCM adapters, C3-16 on-device parity, C3-17's serialized session engine, and C3-18 discovery. Reliable completion with selective repair is C3-22, which sits behind C3-21 sounding and activation, which in turn depends on the measurement and self-characterization work this plan calls Stage 6. So the deadline-bounded transfer described below arrives *after* Stage 6's inputs land, not before, and manual start does not remove that ordering. None of these items are merged today. Building a second half-duplex transfer with its own sequence numbers and repair logic in `scratch/garage/` would create a competing implementation before the first one exists, and would then have to be reconciled. What this stage owes Phase D is the measured input those items need: the profile set that survived Stages 1–4, the observed repair rates and block-loss distributions per condition, and the retained captures to replay against. What it takes back is the first real completion measurement.

The minimum milestone, once those items land, is a manually started, fixed-profile, half-duplex transfer with bounded buffers, sequence numbers, CRC-protected units, a robust acoustic acknowledgment, selective-repeat repair, and final whole-message verification. Manual start/profile selection is acceptable at this stage. It does not require the final discovery UI, crypto, automatic negotiation, or full SDK packaging.

ADB/USB can launch apps and export diagnostic records. Payload, acknowledgments, profile feedback, and missing-block information used to complete the measured transfer must traverse the acoustic link. An acknowledgment delivered over ADB is a useful explicitly host-assisted experiment, not an all-acoustic session result.

Start with 4 KiB and 64 KiB random messages. Choose a finite deadline before each test, initially perhaps 15 s for 4 KiB and 90 s for 64 KiB, and record misses. These are proposed usability targets, not measured supported limits. Do not quietly extend a deadline until the transfer succeeds. Leave 1 MiB transfers for a link that already completes 64 KiB repeatedly; lengthy playback at the fallback rate is not useful for this stage.

Measure from a locally defined sender start event to the final validated acoustic acknowledgment, so unsynchronized device clocks do not corrupt the elapsed-time result. Report attempted/delivered bytes, repair traffic, transfer completion, and total elapsed time. Define sender enqueue, acoustic start, receiver completion, and sender-confirmed completion separately. Run discovery-inclusive timing later when discovery exists.

Keep the audio stream active through a burst session where supported, while retaining explicitly accounted guard and end behavior. Break FEC codewords and training intervals independently of the application message. Compare selective repair against resending a whole message only as a controlled protocol baseline. Add incremental-redundancy HARQ after ordinary selective repeat works and only if coding support and observed repairs justify it.

**Finish this stage when** one complete transfer succeeds on the endpoints and failures terminate/report correctly, then demonstrate several repeatable transfers in more than the easy placement. Archive host replay alongside endpoint results and compare them; a mobile integration regression is not evidence that the waveform stopped working.

**Stage 6: supply the measured profile set that adaptation selects from.** The controller itself is C3-20a, C3-20b, C3-21, and C3-23; this stage produces its inputs and its physical validation, not a second selector. Begin with three or four profiles total, for example a robust coherent mode, a medium mode, the qualified fast mode, and a noncoherent control/fallback bearer. Each direction has its own choice. Do not activate the highest QAM order based on one PSD measurement or treat a trained GMI threshold as a universal code-success guarantee.

Use a short known probe, previous acknowledged results, estimate age, and loss history. Start conservatively, upgrade after repeated successful evidence, and fall back quickly when the current profile fails. Add hysteresis so marginal conditions do not cause constant switching. Freeze the initial policy on development recordings; compare it on later physical runs with both a fixed conservative profile and the best fixed profile measured in that condition. A post-hoc oracle is a diagnostic reference and must be labeled as such.

Include probe and activation costs in session time. If sophisticated sounding takes longer than a small message, start that message immediately on a conservative profile instead. During long messages, amortize measurements and reuse recent channel evidence. A single coherent codeword can fail even when mean GMI looks adequate; retain empirical margins and use lower-tail evidence where enough data exists.

Exercise a simple physical scenario: begin at C1, move to C2 during transfer, turn the phone, then return to C1. Score completion, downtime, recovery, and unnecessary profile changes. After static adaptation works, add an ordinary background noise event. Exact repetition is unnecessary for a practical recovery test; reserve claims about isolated causes for controlled comparisons.

**Stage 7: check the result on the phones and conditions actually available.** Prefer a second phone early enough that weeks of tuning do not depend on one device. If only one is ready, use a fresh day, orientation, and table position, and explicitly retain the device-coverage gap. Brand diversity is useful but not a prerequisite to progress.

Run the frozen candidate and baseline on both directions of the second phone, at a close position and roughly one metre. Keep models, routes, and directions separate in the report. If the second phone exposes only mono input, test the scalar fallback rather than rejecting it. An improvement dependent on stereo capture can remain a route-specific option.

After this, test ordinary held use, modest translation, and a different garage-door/noise state in separate small batches. Vary one property at a time for causal questions. Add mixed-condition trials only to assess overall behavior. If desired, one trip to another room offers more generalization evidence than many extra repeats in the same tape outline.

Define success as a reproducible improvement in the declared conditions with understandable failure behavior. Publish a small table such as device/direction, placement, successful transfers/attempts, median completion time, slowest observed completion, and failure causes. Keep 1 m fixed-distance, handheld, and near-field results distinct. Neither a single garage nor two phones supports a universal device claim.

**Stage 8: spend on heavier equalization only if the remaining failures call for it.** This stage moves earlier if Stage 2 shows strong residual multipath even on stable recordings. It has two bounded branches:

- First compare a regularized block or channel-shortening receiver that accounts for adjacent-symbol energy against the incumbent OFDM receiver. Use recorded signals where this is a legitimate receiver-only change. Avoid unconstrained inversion of deep notches.
- If the receiver remains expensive, fragile, or inefficient, construct one pulse-shaped single-carrier QAM challenger with adaptive DFE or block frequency-domain equalization. Compare using one code held constant across both waveforms — the incumbent convolutional code if Stage 4 has not run, since an early Stage 8 must not wait on a coding choice that may not exist — along with the same occupied band, declared drive class, and complete overhead accounting. It requires its own digital fixtures and actual transmitted captures.

Start with low-order modulation on the already collected troublesome conditions. If that cannot deliver a practical improvement, stop before optimizing high-order constellations. Promote a waveform family only after its gain survives new placements and endpoint compute limits. A lower crest factor alone is not the target; the existing DFT-spread experiment already illustrates that distinction.

Maintain a noncoherent fallback throughout the eventual session system. If the baseline map reveals that coherent operation is rare, bring a robust MFSK/CSS comparison forward before spending heavily on QAM optimization. Evaluate actual short-message delivery and acquisition; do not attempt long-file transfer indefinitely at the floor rate. If noncoherent fallback is used only briefly for control, optimizing it may have little effect on bulk throughput.

**Stage 9: pursue peak rate after establishing a robust operating envelope.** Start with the cheapest measured opportunities, in this order:

1. Use the improved receiver/code to attempt one faster profile at the same bandwidth and drive. Keep the existing reliability objective and include all scheduled overhead.
2. Compare coarse frequency groups if a persistent part of the band limits the whole profile. Freeze masks from previous known probes, count descriptors, and measure the cost of stale masks. Fine per-bin allocation comes only after coarse selection wins.
3. Reduce training or guard only when fresh measurements show surplus margin. The old CP48 failure and sparse-pilot/long-frame regressions remain relevant controls.
4. Consider shaping or a redesigned peak-power waveform only after separating coding losses, tracking error, and distortion. Do not reopen a failed pre-EQ profile by relaxing its old threshold.
5. Perform a small MIMO feasibility acquisition if the devices expose independent transmit and receive ports. Keep all channel columns phase referenced in one uninterrupted render/capture and compare under fixed total transmit power. If the second usable mode is weak or unstable, retain speaker selection and receive diversity.

A physical lab is not necessary for an exploratory MIMO feasibility measurement, but true port independence and common timing are necessary. Without them, defer MIMO rather than reporting an assumed twofold gain. OTFS/AFDM, IMU phase feed-forward, end-to-end learned modulation, full-duplex echo cancellation, and custom ultrasonic hardware remain outside the initial program. Each needs a specific measured reason to displace the simpler experiments.

**Treat capacity as a diagnostic alongside the experiments.** The initial dashboard should show actual profile ceiling, verified delivered rate, and known-probe reliability. Add a conditional stationary channel-model information estimate only where its assumptions can be checked. The old capped PSD arithmetic should not choose a profile. Do not ask the operator to produce a complete power-calibrated channel model before useful receiver tests can start.

To investigate headroom later, compare information estimates from the current receiver and a clearly labeled offline oracle, then examine which errors a realizable algorithm removes. High estimated information with poor finite-code recovery points toward coding, burst structure, or mismatched likelihoods. Poor information even with better synchronization/equalization suggests limitations of the usable path. Neither outcome identifies an exact universal Shannon limit.

**The first five operator sessions should be enough to choose a direction.** They should be separated by software analysis rather than scheduled back-to-back:

| Session | Operator actions | What the software work should produce afterward |
|---|---|---|
| 1, 15–25 min | Choose phone and volume, mark positions, make one good capture, run A/A repeats | Reliable saved evidence and a baseline-variation check |
| 2a, 20–30 min | Sweep and burst CC, C0, C1 while the runner handles both directions/profiles | Half the baseline map, with delay spread per cell |
| 2b, 20–30 min | Same for C2 and C3, then evaluate the Stage 1G gate after replay diagnostics | Small baseline map, the budget-gate decision, and three diagnostic conditions |
| 3, 15–25 min | Replace phone on another day and run the single receiver/tracking finalist | Fresh paired result, or a documented stop and next hypothesis |
| 4, 15–25 min | Repeat the useful comparison with a second phone or reserved condition | Evidence of transfer beyond the development condition |

These total approximately 85–135 minutes of operator time if basic tooling works. Operator time is the smaller cost: G0 through G8 are weeks of software, and the table's involvement column sizes only the sessions. Failed installations or route debugging can consume a session; the runner should stop and leave a useful report rather than use up the rest of the session making noise. Coding and on-device-transfer comparisons get additional short sessions only when their software is ready. There is no requirement to complete every later branch.

**Keep implementation deliverables small and reviewable.** The recommended software sequence is:

| Work item | Concrete output | Verification before hardware use |
|---|---|---|
| G0a | Conservative link geometries derived from endpoint capability — **landed**, [scratch/garage/](../../scratch/garage/README.md) | Byte-exact digital round trip through the C codec on every pairing under test, with no skips |
| G0b | Schroeder readout wired into garage acquisition with validity states — **landed**, see its [plan](delay-spread-readout-plan.md) | The eleven acceptance criteria in that plan, physical cases run through `make_ess` and the garage deconvolution adapter, plus a retained real capture |
| G1 | Capture manifest, runner, immutable attempt ledger, replay command, declared acquisition anchor with bounded non-overlapping slot windows | Existing fixtures, malformed capture, and scheduled-attribution checks, including a missing first frame with a surviving second frame |
| G2 | Baseline report and per-symbol/per-band diagnostics | Compare exact decisions against the canonical C decoder |
| G3 | One receiver-only candidate behind a research option | Generated channels and retained PCM; separate oracle from deployable policy |
| G4 | Fresh-training/time-scale candidate | Exact waveform geometry, clock-offset fixtures, fresh transmitted A/B plan |
| G5 | One QC-LDPC experiment | Independent coding fixtures, error curves, bounded CPU/memory smoke |
| G6 | Measured inputs and replay corpus handed to C3-19/C3-22, and the physical validation of what they build | C/binding parity, chunk boundaries, dropouts, duplicates, deadlines |
| G7 | Measured profile and policy inputs, plus physical validation, for C3-20a through C3-23 | Deterministic link-state transitions, stale estimates, recovery, later physical checks |
| G8 | Garage result summary and integration decision | All attempts retained, claims separated by direction/device/condition |

Use the existing [bulk C implementation](../../Sources/CCyrinx/cyrinx_bulk.c), [batch API](../../Sources/CCyrinx/include/cyrinx/cyrinx_batch.h), [profile registry](../../Sources/CCyrinx/cyrinx_profiles.c), and [goodput accounting](../../scratch/hw20k/goodput_bench.py). New research Python runs through `.venv/bin/python`. Keep early experiments in `scratch/`; port measured winners into C with meaningful deterministic tests and retained regressions. Follow repository formatting, lint, test, and PR checks when shipping source changes are proposed.

The [global roadmap](../../ROADMAP.md) and [3.0 delivery plan](../CYRINX_3_PLAN.md) remain the integration references. This proposed sequence brings lightweight distance/motion observation and a coding comparison earlier, and reduces the measurement burden for exploratory research. If an older gate relies on unavailable calibrated measurements, write a new bounded relative experiment with narrower claims rather than silently treating the old gate as satisfied.

**G0 has landed; the next implementation task is G1, then the baseline map.** The order mattered because the conservative arm of the baseline map could not run until a conservative geometry existed, and the Stage 1G gate could not be evaluated until the delay-spread readout could report a strong-tap figure — or decline to. No exotic DSP should precede the ability to retain a short failed recording, replay it reliably, and explain where information was lost. Once that loop exists, the garage becomes sufficient for making the next several engineering decisions without requiring repeated manual setup or a formal physical lab.
