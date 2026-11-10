# Should Cyrinx build an acoustic channel simulator?

> **Provenance.** Written by an AI research agent on 2026-11-10 and committed as received. The absence of `artifacts/garage/` on the development Mac was re-checked by hand; nothing else here has been independently re-verified.

Date: 2026-11-10. Repo state read: `main@6956552`. Every number labelled **[SIM]** below came from a simulation run in this investigation. None of them is a hardware measurement.

## In plain English

The idea is to test the modem by playing its signal through recorded or computed room echoes and noise on a computer, instead of carrying a laptop and phone into a garage each time. That would be faster and repeatable, and it could run thousands of conditions overnight.

A small experiment showed both the promise and the danger. A computed echo with the same headline "echo length" as a real desk gave the right answer for the standard signal: nothing got through. For a longer-guard signal it gave the wrong answer. It said every block would get through, where the real desk delivered zero. A simulator that is tuned to look right on one number can be confidently wrong on the next question.

The recommendation is to build a small simulator that replays the project's own measured room echoes. Each kind of question is allowed to rely on it only after the simulator has reproduced the real results that bear on that question. Simulating rooms from geometry, or borrowing other people's recordings, should not drive decisions.

## Recommendation

**Build "Replay Our Own Rooms" now. Keep it small, and make it waveform-level and gated by question type. Do not build a geometric room simulator as a decision tool.**

Why:

1. **The waveform-level simulator that would answer environmental questions does not exist yet.** `cyrinx-sim-bench` (`Sources/Cyrinx/Simulation.swift`) generates no audio. It draws SNR/EVM/CFO/PER numbers from three hand-written profiles (`quiet`, `office-burst`, `moving-phone`), feeds them to the legacy session's ARC through `injectChannelReport`, and moves packets through `cyrinx_link_in_memory`, a pointer link between two sessions. It exercises transport and ARC state on the legacy stack. It never touches the bulk OFDM modem in `cyrinx_bulk.c`, and it never touches a channel. C3-13 (deterministic faulting simulator with "PCM mode for end-to-end modem tests") is planned and not merged.
2. **The highest-value open question is offline-shaped.** The garage plan's Stage 8 asks which waveform class (non-coherent, single-carrier with equalization, long symbols) can carry a channel with 48–82 ms of strong-tap spread. Receiver-only changes can already be tested on retained capture PCM (Stage 2). Stage 8 cannot, because it changes the *transmitted* waveform. Convolving a new waveform with the *measured* impulse response of that cell is the cheapest way to screen candidates before a garage session. Throughput is about 7× real time per core [SIM timing], and results are bit-identical across macOS and Linux (tested; see Spike).
3. **The spike found a concrete false positive from synthetic channels** (spike section, check `Long-Guard Desk`). A statistically generated room echo matched to NF-13's 35.8 ms spread predicts 26/26 blocks with a 43 ms guard. Hardware measured 0. So only measured channels feed decisions, and synthetic channels are only for stress and regression testing.
4. **A geometric model cannot be pinned down with what the project knows.** pyroomacoustics on a plausible garage moved the −10 dB spread from 0.0 ms to 172.6 ms [SIM] across unmeasured parameters (wall absorption 0.03–0.25, separation 10 vs 30 cm). It can be tuned to hit 48 ms, but that is a fit and not a prediction, and the near-field laptop/desk scattering that dominates these links lies outside geometric acoustics' assumptions.

A precondition surfaced along the way. **The garage impulse responses behind the 48.3 ms and 82.3 ms figures are not on disk.** `scratch/hw20k/NOTES.md` says raw captures are under `artifacts/garage/`. That directory exists in no Cyrinx checkout on this Mac (`/Users/dew/dev/cyrinx*`), and `artifacts/` is gitignored. Only the Mac self-calibration IR is retained (`scratch/garage/fixtures/mac_selfcal_ir.npz`, 0.92 ms). The NF-13 desk capture `scratch/hw20k/data/geo_ess.pcm` is present but untracked, and its format and sweep parameters are not recorded. The first step is therefore to retain IRs, not to write simulator code.

## Options

| Option | What it is | Can represent | Cannot represent (on this channel) | Verdict |
|---|---|---|---|---|
| **Report-Injection Bench** (exists) | `cyrinx-sim-bench`: hand-set SNR/EVM/CFO numbers injected into legacy ARC; no waveform | Transport/ARC state-machine behaviour under scripted metric streams | Anything acoustic. It is not connected to the bulk modem. | Keep for ARC/state tests; never cite for PHY or range |
| **Replay Our Own Rooms** | Convolve TX waveform with *measured* per-direction, per-mic IRs from the project's own ESS sweeps; add *recorded* room tone at the recorded level; add SRO by resampling | Exact linear multipath of a measured cell, including near-field desk/chassis coupling and the speaker/mic *linear* response, because the ESS measures transducer and room together. Measured two-mic spatial correlation, if both mics' IRs come from one sweep. Real noise spectrum. Clock offset. | Speaker nonlinearity and clipping (ESS pushes harmonics to negative time and discards them). Phase incoherence above ~18 kHz on phone speakers (NF-9; an LTI IR cannot hold it). Time variation within a 3–4 s frame (hand motion, NF-24 drift). OS processing/AGC that responds to the signal. Cells nobody measured. | **Build** (staged) |
| **Geometric Room Model** | Image-source/ray tracing: [pyroomacoustics](https://github.com/LCAV/pyroomacoustics) 0.10.1 (2026-05-01, MIT; source/receiver directivity for ray tracing added in 0.10.0, SOFA measured directivity since 0.8.0); [gpuRIR](https://github.com/DavidDiazGuerra/gpuRIR) (AGPL-3.0, NVIDIA CUDA required, shoebox rooms, last push 2026-08-25); [rir-generator](https://github.com/audiolabs/rir-generator) 0.3.0 (MIT, Allen–Berkley ISM); commercial [Treble SDK](https://treble.tech) (PyPI `treble-tsdk` 0.0.1, proprietary licence, wave-based + GA hybrid; not evaluated) | Room-scale reverberation trends (RT60 vs absorption and volume). Many rooms cheaply (0.1 s per RIR on this Mac [SIM timing]). Moving-source trajectories (gpuRIR). | Near-field (≤30 cm) geometry where laptop body, lid and desk are comparable to the wavelength (1.5–110 cm across 0.3–23 kHz), so diffraction and scattering dominate and GA assumptions fail (reasoning, not tested). Device directivity and shadowing unless measured. Absorption/scattering at 16–23 kHz, which is poorly characterized. Everything Replay also cannot do. | Do not use for decisions. Optional later for synthetic stress IRs only. |
| **Borrowed Rooms** | Public measured RIR and noise sets (e.g. [DEMAND](https://zenodo.org/records/1227121) multichannel noise at 48 kHz, licence stated inconsistently as CC BY-SA 3.0 / CC BY 4.0 on the record; [MUSAN](https://www.openslr.org/17/) CC BY 4.0, sample rate not stated on the download page, believed to be 16 kHz, **unverified**; 48 kHz RIR sets such as [SuRIR](https://dael.euracoustics.org/confs/fa2026/data/articles/000751.pdf), [Vislab](https://cvssp.org/soundzone/resource/VislabRIR/index.html), [UPV_RIR_DB](https://arxiv.org/html/2603.25947v1), not read in detail) | Diversity of real rooms and real noise spectra. Narrowband/impulsive interference (noise sets). Room-scale reverberation measured with studio loudspeakers and measurement mics. | Phone/laptop transducers and the near-field desk coupling that defines these links. A limited search found no public phone-speaker→phone-mic RIR set. Speech-oriented 16 kHz corpora carry nothing above 8 kHz. | Use only the noise sets, as additive interference, and only at 48 kHz. Not for channels. |
| **Measured Core, Modelled Edges** (hybrid) | Replay Our Own Rooms, plus explicitly modelled impairments added one at a time, each admitted only after it reproduces its own hardware finding: memoryless speaker nonlinearity fitted from self-cal; phase-noise model above 18 kHz for phone TX; slow IR interpolation between two measured poses for motion; DEMAND/recorded interferers | Everything Replay can, plus each impairment that has passed its check | Whatever has not passed its check. AGC/OS processing remains route-specific and is best handled by capturing on the `UNPROCESSED` route that is already used. | **The target shape**, reached in stages |

The hard parts, one by one:

| Effect | How to get it | Confidence |
|---|---|---|
| Small-speaker / MEMS-mic response above 16 kHz (magnitude) | Inside measured IR (ESS covers 60–23,500 Hz) | Good, for the measured device pair only |
| Phone speaker phase incoherence above ~18 kHz (NF-9) | Not LTI. Needs a fitted per-tone phase-jitter model; reproduce NF-9's 10–31 rad jitter | Low until gated |
| Speaker nonlinearity / clipping / PAPR-driven distortion | Fit from self-cal harmonics (ESS negative-time IRs) or a multitone level sweep. EXPERIMENTS finding 2 puts the EVM floor at ~15 dB effective SINR while swept SNR is 52 dB, so a linear simulator will be optimistic about high-order QAM. | Low until gated |
| OS processing / AGC | Avoid it: capture on `UNPROCESSED` routes. A route that applies AGC needs a measured gain trace, which is not modelled. | Out of scope |
| Sample-rate offset | Resample. EXPERIMENTS records −10 to −25 ppm Mac↔Pixel. [SIM] 20 ppm raises digital-loopback EVM 0.046→0.251 at 30 dB SNR, so the receiver's SRO handling matters and is cheap to sweep. | Good mechanically |
| Time variation from hand motion within a frame | Interpolate between IRs measured at two poses, or use gpuRIR-style trajectories. Neither has been validated against NF-24's drift. | Low |
| Near-field coupling through a shared desk | Inside measured IR, if the sweep is taken in that exact pose | Good, for that pose only; does not generalize |
| Two-microphone spatial correlation | Measured stereo IRs from one sweep. The overhang_kbwell MRC rescue (single mics 0 blocks, MRC 75/75) depends on it. | Good if captured; absent today |
| Narrowband / impulsive interference | Add recorded or DEMAND noise; synthetic tones and clicks. No interference has been measured on hardware yet (E3 empty). | Mechanically easy; unvalidated |

## Validation gate

A simulator version is trusted only **for the question class whose checks it passes**, and only **without retuning** between checks. Free knobs (noise level, nonlinearity coefficients) are fixed from self-calibration and room-tone captures of the same session, never adjusted to match a decode result. Thresholds are proposed here (proposed by an agent, not yet confirmed) and should be frozen before the simulator is run against the anchors.

| Check | Hardware anchor (source) | Simulator input | Pass if | Unlocks | Status today |
|---|---|---|---|---|---|
| **Bit-Exact Loopback** | Digital loopback all blocks (clib `__main__`) | Identity channel | All blocks, identical bytes, results identical across macOS and Linux builds | Running anything | **Pass** [SIM]: 26/26; 76/76 spike rows identical macOS-arm64 clang vs Linux-aarch64 gcc 14.2 |
| **Short-Path Control** | Mac→Mac QPSK r1/2 CP 768: 52/52, EVM 0.136, ds10 0.9 ms (NOTES 2026-09-08) | Measured self-cal IR + that session's room tone at its level | All blocks; EVM within ×1.5 of 0.136 | Anything | Partial: [SIM] 26/26, EVM 0.145 at an *assumed* 30 dB white-noise SNR; recorded-room-tone version not runnable (room tone for that session not retained) |
| **Garage One-Foot** | Mac→Pixel 0/52, EVM 1.10, chirp peak/mean 135.6, ds10 48.3 ms; Pixel→Mac 0/38, EVM 1.94, 72.9, ds10 82.3 ms | Measured garage IRs per direction | 0 blocks both directions; acquisition succeeds (peak/mean within ×2 of measured) | Reverberation screening | **Blocked: IRs not retained** |
| **Long-Guard Desk** | NF-13: QPSK r1/2 CP 768 → 0; NFFT 4096 / CP 2048 (43 ms) → 0; ds10 35.8 ms; sync locks (peak/mean 104) | Measured desk IR | 0 blocks at both guards | Waveform-class screening (Stage 8) | **Synthetic channel fails** [SIM]: Polack ds10 35.8 ms gives 26/26 at CP 2048. Candidate desk IR passes (0/26 at both) but its provenance is unverified. |
| **Two-Mic Rescue** | overhang_kbwell: single mics 0 blocks (EVM 0.76/1.33), MRC 22/22 (16-QAM r1/2) and 75/75 live (QPSK r1/2) (NOTES 2026-07-08; EXPERIMENTS) | Measured stereo IR at that pose | Each single mic ≤10% blocks; MRC ≥90% | Diversity / MRC questions | Not runnable (no stereo IR retained). [SIM] single-IR synthetic at ds10 16 ms decodes 24–26/26, which hints the simulator is optimistic near the cliff; not like-for-like. |
| **Near-Field Ceiling** | Cloth contact 64-QAM r2/3 CP96: 4,215/4,280 (98.48%), 65.875 kbps (ACOUSTIC_BULK_PHY; NF-4); 64-QAM r3/4 87/121; r5/6 24/134 (NF-22) | Measured cloth-contact IR + nonlinearity model fitted from self-cal only | r2/3 within ±3 pp of 98.48%; r3/4 and r5/6 block success within ±15 pp of measured, and in the same order | MCS / rate / throughput questions | Not attempted. Expected to fail for a linear simulator (EXPERIMENTS finding 2). |
| **Phone Ultrasonic** | NF-9: phone TX at 19.5–22 kHz, EVM ~1.0; STFT phase jitter 10–31 rad (Pixel) | Phone-TX impairment model | Coherent EVM ≥0.8 in band; ≤0.1 at 8 kHz | Any phone-TX band question | Not attempted |
| **Pilot-vs-Data** | EXPERIMENTS finding 3: repeated-pilot SNR over-reads random-data SINR (~30 dB vs ~15 dB effective) | Full impairment stack | Gap ≥8 dB, same sign | Sounder / ARC policy | Not attempted |
| **Preregistered Ladder** | New hardware: the plan's C0 (10 cm) → C1 → C2 → C3 ladder, both directions, with IRs retained | Predictions written down *before* the session from each cell's own IR | Decode/fail agrees in ≥7 of 8 directed cells; block success within ±15 pp where both decode | Range / placement claims, only for measured device pairs | Needs hardware; this is the plan's own next step |

What the gate says now: the simulator may be used for **regression testing of the receiver and for pipeline integrity**. It may not be used for any environmental decision until Garage One-Foot and Long-Guard Desk pass on retained measured IRs.

### Labelling, so simulated results are never read as measurements

- `docs/C3_08_10_GATE_SPEC.md` rule 1 already defines evidence classes: *digital, simulated, retained replay, current OTA, route-qualified*, "never interchangeable". Reuse `simulated` and split it: `simulated:measured-channel` (with IR id and hash) and `simulated:synthetic`. ROADMAP's maturity labels already put anything implemented only in a simulator under BENCH-SPIKE, "supports no product claim".
- Every result row carries `evidence_class`, simulator version, channel id and hash, seed, config hash, and the list of gate checks that version has passed. The report generator refuses to render a row without `evidence_class` and prints "SIMULATED" in every table title.
- `scripts/check-claims.sh` today is a fixed-string denylist (`rg -F`) plus six required literals. It cannot express "a line citing a simulation run must say simulated". Proposed extension (not yet confirmed): give sim run ids a fixed prefix (`sim-`), and add a regex rule that fails any Markdown/HTML line containing `sim-[0-9a-f]{8}` without the word "simulated" on the same line. Self-test fixtures for both cases, matching the existing self-test pattern.
- NEGATIVE_FINDINGS gets an entry for every gate check that fails, starting with the Long-Guard Desk disagreement below, so the failure is recorded rather than tuned away.

## Feasibility spike: method and output

All scripts are in `scratch/spikes/sim_feasibility/`. The spike wrote nothing into the repo while it ran; the scripts and their outputs were copied in afterwards, unchanged, so some carry absolute paths from the machine they ran on. The C codec was built into the scratchpad from `main@6956552` with the command in `scratch/hw20k/clib.py`'s docstring, and loaded via `CYRINX_BULK_CODEC_PATH`. Python ran in the project `.venv` (numpy 2.4.6, scipy 1.17.1) with `-I` and `PYTHONDONTWRITEBYTECODE=1`.

**Frame.** `geometries.conservative_link(MACBOOK_PRO_M4, PIXEL_7A, 0.5)`: 300–23,000 Hz, QPSK r1/2, NFFT 2048, CP 768 (16 ms), 64 symbols, 26 blocks/frame, 192,000 samples (4.00 s). This is the same geometry the garage link probe used. One frame per condition, a seeded random payload, scoring by `clib.ordered_verified_blocks` (ordered, position-verified).

**Channels.**
- Identity.
- Measured short IR: `scratch/garage/fixtures/mac_selfcal_ir.npz` (Mac speaker→Mac mic, ds10 0.92 ms).
- Candidate desk IR, reconstructed from `scratch/hw20k/data/geo_ess.pcm`. This *assumes* int16 stereo and `freqresp.make_ess()` defaults (6 s, 60–23,500 Hz) with a 120 ms crop. That combination gave the cleanest peak (peak/median 6,565 vs ≤2,252 for other layouts) and ds10 38.3 ms against NF-13's recorded 35.8 ms. The file predates `freqresp.py` by a day, and its true sweep parameters are not recorded, so this IR is **unverified**. `save_desk_ir.py`, `probe_geo_ess2.py`.
- Synthetic Polack IRs: a direct tap plus an exponentially decaying Gaussian tail, with T60 bisected so the Schroeder −10 dB crossing (`freqresp.delay_spread`) hits targets 1–82.3 ms, at direct-to-reverberant ratios of 0 and 6 dB.

**Impairments.** White noise at 20 and 30 dB SNR relative to received signal power; SRO 0 and 20 ppm by linear-interpolation resampling.

**Commands.**
```
CYRINX_BULK_CODEC_PATH=$S/libcyrinxbulk.dylib PYTHONDONTWRITEBYTECODE=1 \
  /Users/dew/dev/cyrinx/.venv/bin/python -I $S/spike_reverb.py $S/spike_results.jsonl $S/desk_ir_candidate.npy
... spike_longcp.py $S/desk_ir_candidate.npy
$S/venv-pra/bin/python -I $S/spike_pra_garage.py      # pyroomacoustics 0.10.1 in a scratch venv
```

**Output, selected [SIM]** (full: `spike_output.txt`, `spike_results.jsonl`, `spike_longcp_output.txt`, `spike_pra_output.txt`). Blocks are out of 26. SNR 30 dB, SRO 0 unless noted.

| Channel | ds10 / ds15 ms | CP 768 | NFFT 4096 / CP 2048 | Hardware reference |
|---|---|---|---|---|
| Identity | 0 | 26, EVM 0.046 | — | — |
| Measured Mac self-cal IR | 0.92 / 2.27 | 26, EVM 0.145 (20 dB: 0.247) | — | 52/52, EVM 0.136 |
| Polack 1–8 ms | 1.2–8.0 | 26, EVM 0.07–0.34 | — | — |
| Polack 16 ms | 16.0 / 25.5–39.8 | 24–26, EVM 0.60–0.71 (20 dB + 20 ppm, DRR 6: **6**) | — | (overhang ds15 15.8: single mic 0, different pose and guard) |
| Polack 24 ms | 24.0 | 0, EVM 0.89–0.90 | **26**, EVM 0.38 | — |
| Polack 35.8 ms | 35.8 | 0, EVM 0.87–1.06 | **26**, EVM 0.61–0.64 | NF-13: **0** at both guards |
| Candidate desk IR | 38.3 | 0, EVM 3.95 | 0, EVM 2.26 | NF-13: 0 at both (EVM ~2 / ~1.0) |
| Polack 48.3 ms | 48.3 | 0, EVM 0.85–1.55 | 0–13 | garage Mac→Pixel 0/52, EVM 1.10 |
| Polack 82.3 ms | 82.3 | 0, EVM 0.92–2.14 | — | garage Pixel→Mac 0/38, EVM 1.94 |

What the spike shows:

1. **The collapse is reproduced in direction.** At CP 768 the simulated cliff lies between ds10 16 and 24 ms. Every channel at or beyond the garage spreads decodes 0 [SIM], matching hardware. Short channels (<2 ms) decode all blocks [SIM], matching the hardware control. EVM at failure depends on DRR, a parameter ds10 alone does not fix, so the simulator's EVM there is not a meaningful match or mismatch.
2. **Synthetic channels give a false positive on the next question.** With NF-13's guard (NFFT 4096 / CP 2048), Polack channels at ds10 24 and 35.8 ms decode 26/26 [SIM]. Hardware measured 0 at ds10 35.8 ms. Hypotheses, not tested: real IRs have discrete strong late reflections and deep spectral nulls that a smooth exponential tail lacks; real-channel time variation; speaker nonlinearity; or a difference between this config and NF-13's exact one (NF-13 was Mac→iPhone, and its full NFFT 4096 config is not recorded). Had this simulator been used to choose Stage 8 candidates, it would have recommended exactly the ladder NF-13 already closed.
3. **The measured desk IR candidate does agree with hardware** (0 at both guards). That supports replaying *measured* channels, with the caveat that this particular reconstruction is unverified.
4. **Geometric simulation is under-determined here.** pyroomacoustics ShoeBox ISM, max order 60, air absorption on, omnidirectional point source and receiver, 0.9 m bench: ds10 ranged **0.0–172.6 ms** [SIM] across two garage sizes, three absorption guesses and two separations. The setting nearest the measurement (6×6×2.7 m, absorption 0.10, 30 cm) gave 43.7 ms, but that is the parameter that was guessed. With omni sources, ds10 also sits near 0 whenever the direct path holds 90% of the energy, which is the opposite of the "shadowed" cells where hardware fails.
5. **Linux build works.** In Docker `python:3.12` (Debian, gcc 14.2.0, **aarch64**), `gcc -std=c11 -O2 -Wall -fPIC -shared` of the four bulk-codec C files built with no warnings printed. `clib.py`'s digital loopback passed (3/3, 9/9, 14/14), and the full 76-row spike reproduced macOS block counts and EVM to 3 decimals with zero differences.

Not verified: x86_64 (if trogdor is x86_64, its first run must repeat the cross-platform diff), and SwiftPM `swift build` on Linux. Apple imports in `Sources/Cyrinx` sit behind `#if canImport`/`#if os` guards, but the package's only Linux CI job builds ChatKit, not Cyrinx. The Python + C path needs no Swift.

Spike time: about 45 minutes, including the Docker image pull.

## Scale and reproducibility

- **Cost per simulated frame [SIM timing, single thread]:** decode of one 4.0 s QPSK frame is 0.47–0.55 s on the M4 and 0.61 s in the Linux container, which is ~7× real time. FFT convolution and noise are negligible next to that. An ISM RIR is ~0.1 s. Estimate **~0.6 core-seconds per condition-frame**; denser MCS and longer frames scale roughly with samples.
- **Sweep size (estimate):** 10 measured IRs × 2 mics × 8 SNR/noise levels × 5 SRO values × 6 waveform configs × 10 seeds = 48,000 frames ≈ 8 core-hours, or about 15 minutes on a 32-core machine. trogdor's core count is not known to me, and I did not ssh.
- **Parameterization:** one YAML/JSON sweep spec → a cartesian list of conditions. Each condition is a dict (channel id + IR hash, noise source + level, SRO ppm, config hash, codec build hash, seed), and its id is the SHA-256 of canonical JSON. Seeds derive from the condition id, so any single row re-runs alone and bit-identically (cross-platform identity shown above).
- **Execution:** a process pool. Each worker appends one JSONL row per condition (append-only, flushed), and on restart the runner skips ids already present. A progress line every N rows prints done/total, rows per second and ETA. This follows the existing `goodput_campaign.py` / `linkprobe.py` conventions of seeded payloads and ordered verification, and keeps expected bytes out of the receiver.
- **trogdor implications:** the C bulk codec builds as a `.so` with gcc (shown on aarch64). A `make`-free one-line build plus `clib.CODEC_PATH_ENV` is enough. The corpus (IRs + room tone, maybe ~100 MB) is synced from the capture machine, and results come back as JSONL. A self-hosted runner can run Bit-Exact Loopback and the gate anchors as CI on every receiver change, because they need no hardware.

## Staged build plan

1. **Retain Our Rooms (smallest useful step; no simulator code).** Make the G1 capture runner, already the plan's next task, store per directed link and per mic: the raw ESS capture, the deconvolved IR (500 ms horizon), room tone at session gain, route/level metadata, and a hash. Pin the format of the IR files. Recover what exists: confirm whether the 2026-09-07/08 `artifacts/garage/` captures survive anywhere, and record `geo_ess.pcm`'s true sweep parameters if anyone knows them. Gate: the Garage One-Foot and Long-Guard Desk anchors each have a retained, hashed IR.
2. **Replay harness, linear only** (`scratch/sim/`, about 200 lines on top of `clib.py`): IR convolution, recorded room tone at recorded level, SRO, the sweep runner above, `evidence_class` on every row. Gate: Bit-Exact Loopback, Short-Path Control, Garage One-Foot and Long-Guard Desk all pass. Then use it for Stage 8 waveform screening on measured channels only.
3. **Two-mic.** Stereo IRs; Two-Mic Rescue check.
4. **Impairments, one per PR, each behind its check:** speaker nonlinearity (Near-Field Ceiling), phone >18 kHz phase model (Phone Ultrasonic), pilot-vs-data (Pilot-vs-Data). Recorded/DEMAND interference once E3 has a hardware anchor.
5. **Preregistered Ladder** at the next garage session: predictions committed before the session, scored after.
6. Only then, if wanted: motion via IR interpolation, and synthetic or geometric IRs as *stress* inputs for receiver regression, labelled `simulated:synthetic`.

**Out of scope:** geometric room simulation as a predictor; public RIR datasets as channels; modelling OS AGC; any throughput or range claim from simulation; changes to the profile registry or C receiver as part of this work; the legacy `cyrinx-sim-bench` (unchanged).

## What simulation should not be used for, and what still needs hardware

- **Not for:** a throughput, range or "works in a room" claim; choosing MCS or code rate before Near-Field Ceiling passes (EXPERIMENTS finding 2 and NF-22 are exactly where linear models are optimistic); phone-TX band decisions above 18 kHz; sounder/ARC calibration; any cell or device pair that has not been measured; promoting anything past BENCH-SPIKE.
- **Still needs hardware:** acquiring IRs for each new cell and device; motion and handling (NF-24's drift); OS route behaviour (NF-16/17 silent capture, AGC, Dolby DAX); on-device real-time performance; every promotion decision (C3-09 gate: "a simulator-only or desktop-only run cannot qualify"); the preregistered prediction checks themselves.

## Risks

- **Fit masquerading as validation.** Tuning noise or nonlinearity until anchors match, then reading the match as evidence. Mitigation: knobs come from self-cal and room tone only, thresholds are frozen first, and the anchors are held out.
- **Measured-IR overconfidence.** An IR is one pose at one moment. The garage Mac→Pixel −10 dB repeat moved ~2%, but NOTES records −20 dB moving 42% on the laptop chassis path. Replaying a single IR understates variability, so use several sweeps per cell.
- **LTI blind spot.** The failures that matter most on these links (NF-9 phase scrambling, the EVM floor, pilot-vs-data) are non-linear or time-varying, which convolution cannot represent.
- **Lost evidence recurs.** The garage IRs are already missing. Without retention the simulator has nothing to replay.
- **Scope creep into a parallel stack.** C3-13 already plans a PCM-mode simulator. This harness should be its channel stage or be folded into it, not a second one.

## Open questions for the owner (business intent only)

1. **What decision should simulation buy first?** Recommended: Stage 8 waveform-class screening for the one-foot regime. Alternatives are MCS tuning near-field or range mapping, which need the later gates.
2. **Is one foot (or more) of air a product requirement?** If not, Stage 8 and most of this simulator's first use lose urgency.
3. **May the simulator harness live in `scratch/` as a BENCH-SPIKE,** or should it be built as C3-13's channel stage from the start? This trades speed against contract work.
4. **How many device pairs must a "measured channel" corpus cover** before simulation results inform a product decision? One pair (Mac/Pixel 7a) or the four characterized endpoints?

## Sources

- pyroomacoustics: [repo](https://github.com/LCAV/pyroomacoustics) (MIT per GitHub API), [CHANGELOG](https://github.com/LCAV/pyroomacoustics/blob/master/CHANGELOG.rst) (read), PyPI 0.10.1 (2026-05-01), installed and run.
- gpuRIR: [repo README](https://github.com/DavidDiazGuerra/gpuRIR) (read; AGPL-3.0, CUDA). Not on PyPI. Not installed.
- rir-generator: PyPI metadata only (0.3.0, MIT); [repo](https://github.com/audiolabs/rir-generator) not read.
- Treble SDK: PyPI metadata only (0.0.1, proprietary licence); [site](https://treble.tech) not read.
- pyroadacoustics 1.1.0 (GPL-3.0), audiomentations 0.43.1 (MIT), sofar 1.3.0 (MIT): PyPI metadata only.
- [DEMAND on Zenodo](https://zenodo.org/records/1227121) (read). [MUSAN on OpenSLR](https://www.openslr.org/17/) (read; sample rate not stated there).
- RIR datasets from one web search, **not read**: [SuRIR (FA2026)](https://dael.euracoustics.org/confs/fa2026/data/articles/000751.pdf), [microphone IR dataset (Zenodo 4633508)](https://zenodo.org/records/4633508), [Illinois wearable-mic IRs](https://databank.illinois.edu/datasets/IDB-1932389), [Surrey Vislab](https://cvssp.org/soundzone/resource/VislabRIR/index.html), [UPV_RIR_DB](https://arxiv.org/html/2603.25947v1), [METU SPARG](https://zenodo.org/records/2635758), [SMARD](https://www.smard.es.aau.dk/downloads/).
- Repo: `Sources/Cyrinx/Simulation.swift`, `Examples/SimulationBench/main.swift`, `Sources/CCyrinx/cyrinx.c` (`cyrinx_link_in_memory`), `scratch/hw20k/{clib.py,freqresp.py,NOTES.md}`, `scratch/garage/{README.md,geometries.py,linkprobe.py}`, `docs/{NEGATIVE_FINDINGS.md,EXPERIMENTS.md,CYRINX_3_PLAN.md,C3_08_10_GATE_SPEC.md}`, `docs/research/garage-throughput-plan.md`, `ROADMAP.md`, `scripts/check-claims.sh`, `.gitignore`.
