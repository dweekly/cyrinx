# Cyrinx Roadmap

## Scope
This roadmap advances `cyrinx` from the current end-to-end acoustic PHY baseline to a production-grade ultrasonic link that is robust to noise, echoes, and reverb for MacBook Pro <-> iPhone Pro Max at 1-2 ft.

## Current Baseline (Implemented)
- C ABI + Swift wrapper with multiplexed streams.
- Framing, CRC16/CRC32C, fragmentation/reassembly, ACK/retry transport.
- ARC policy engine with reliability-first downshift behavior.
- Apple audio backends (`RemoteIO` on iOS, `AVAudioEngine` on macOS).
- End-to-end acoustic PHY bridge:
  - dual-ZC preamble detector
  - D-CSS robust/header path
  - OFDM-QPSK turbo path
  - RX callback -> demod -> `cyrinx_ingest_frame`.
- vDSP-backed OFDM and D-CSS modem implementations.
- Deterministic simulation and comprehensive test/lint/format gates.

## Critical Gaps To Production Robustness
- Multipath/reverb lock hardening beyond a single-threshold preamble detector.
- Live CFO/Doppler estimation and correction in the acoustic RX path.
- OFDM pilots + channel estimation/equalization for non-ideal channels.
- True PHY FEC/interleaving and incremental-redundancy HARQ combining.
- Impulsive-noise and clipping resilience in realistic office environments.
- Hardware qualification matrix and threshold retuning from measured data.

## Sequencing Principles
1. Reliability before throughput: any performance upgrade must preserve delivery guarantees.
2. Measured truth over static constants: thresholds finalize from HIL data, not paper-only values.
3. Deterministic regression first: every DSP change requires golden vectors and seeded simulation.
4. Keep public API stable: evolve internals without breaking C ABI / Swift surface.
5. Ship by evidence: each phase exits with explicit KPI artifacts.

## Phase Plan

### Phase R0: Robustness Bench Harness (Week 1)
Objective:
- Make robustness regressions measurable before algorithm changes.

Work:
- Extend simulation harness with:
  - desktop-canyon multipath profiles (direct + reflected paths, comb null sweeps)
  - reverberation tails (RT60 buckets)
  - Doppler/CFO trajectories (+/-80 Hz instantaneous events)
  - burst-noise injectors (keyboard clicks, cough-like impulses).
- Add per-run artifacts: lock rate, false-lock rate, PER, retries, dwell transitions, decode latency.
- Add scenario presets used by CI and HIL scripts.

Exit criteria:
- `artifacts/bench/*.json` contains reproducible robustness metrics by scenario.
- CI gates fail on statistically significant regressions against baseline.

Dependencies:
- None.

### Phase R1: Sync And Reverb Hardening (Weeks 2-3)
Objective:
- Achieve stable frame start detection under multipath and moderate reverb.

Work:
- Replace simple threshold lock with scored lock candidates:
  - peak-to-sidelobe ratio
  - dual-block consistency checks
  - lock hysteresis and timeout windows.
- Add multipath-aware preamble alignment strategy to avoid echo-peak mislocks.
- Add false-positive suppression and reacquisition flow for burst interference.

Exit criteria:
- Preamble detect >=99.9% at SNR >= -6 dB in non-reverb profiles.
- False-lock rate <=1e-4/frame in reverb/noise stress profiles.
- Reacquisition after forced unlock <=2 frames median.

Dependencies:
- Phase R0 harness.

### Phase R2: CFO And Doppler Compensation (Weeks 4-5)
Objective:
- Maintain decode stability during device motion and oscillator offsets.

Work:
- Implement coarse CFO from repeated ZC blocks.
- Add fine CFO/phase tracking loop over payload symbols.
- Apply per-symbol phase rotation correction before demod.
- Feed CFO confidence and residual error into channel reports used by ARC.

Exit criteria:
- CFO estimation error <= +/-5 Hz for true CFO in +/-80 Hz.
- No catastrophic demod collapse during scripted motion events.
- PER improvement vs baseline in Doppler scenarios is statistically significant.

Dependencies:
- Phase R1 lock quality.

### Phase R3: OFDM Pilot Tracking And Equalization (Weeks 6-8)
Objective:
- Make turbo mode resilient to frequency-selective fading from desk reflections.

Work:
- Define pilot/null map consistent with active-carrier plan.
- Implement per-subcarrier channel estimation and interpolation.
- Add one-tap equalization and EVM computation on equalized symbols.
- Add pilot-aided phase tracking and optional per-carrier quality masking.

Exit criteria:
- QPSK goodput >=4 kbps and 16QAM >=8 kbps in clean 1-2 ft profiles.
- PER reduced vs pre-equalization baseline in desktop-canyon sweeps.
- EVM metrics correlate with decode outcomes for ARC decisions.

Dependencies:
- Phase R2 CFO correction.

### Phase R4: PHY FEC, Interleaver, And HARQ IR (Weeks 9-11)
Objective:
- Move reliability from transport-only retries to real PHY error correction.

Work:
- Implement LDPC encode/decode profiles:
  - robust mode: rate 1/2
  - turbo modes: rate 2/3 (and optional 3/4 experiment).
- Add block interleaver/deinterleaver targeting ~50 ms depth.
- Add soft-decision demodulation (LLRs) into LDPC decoder.
- Implement incremental-redundancy HARQ rounds with soft combining.

Exit criteria:
- Robust mode sustains 300-600 bps under office-burst scenarios.
- 256 B delivery success >=99% at 1-2 ft in moderate noise.
- Retry count and timeout rate materially reduced vs pre-FEC baseline.

Dependencies:
- Phase R3 equalized demod metrics.

### Phase R5: Impulsive Noise And Clipping Resilience (Weeks 12-13)
Objective:
- Prevent transient acoustic events from causing link collapse.

Work:
- Add impulsive-noise mitigation (blanking/clipping-aware preprocessing).
- Add saturation detection and TX/RX gain safety adaptation hooks.
- Add optional narrowband interference suppression in ultrasonic band.
- Add guard/recovery policy tuning for short high-energy events.

Exit criteria:
- Noise burst downshift occurs within <=2 frames.
- Link recovers to prior gear after hysteresis window without oscillation.
- No sustained frame-loss cascades under scripted transient events.

Dependencies:
- Phase R4 FEC/HARQ.

### Phase R6: ARC Retune With Real PHY Metrics (Weeks 14-15)
Objective:
- Calibrate mode transitions using real post-equalization and post-FEC data.

Work:
- Retune SNR/EVM/PER thresholds and dwell windows from measured distributions.
- Add explicit safeguards:
  - no upshift during retransmission bursts
  - minimum dwell and upshift hold windows
  - timeout-driven hard reset behavior verification.
- Validate mode stability under mixed traffic and channel variability.

Exit criteria:
- <1 oscillation/minute in mixed scenarios.
- Throughput and reliability targets met simultaneously in target profiles.
- Transition behavior matches documented policy envelopes.

Dependencies:
- Phase R5 stable metrics.

### Phase R7: Hardware Qualification Matrix (Weeks 16-17)
Objective:
- Validate real-world performance on target Apple hardware.

Work:
- Run matrix across:
  - 1 ft / 2 ft
  - face-on / off-axis
  - quiet / office-noise
  - desk surface variants / cloth damping.
- Validate 48 kHz and optional 96 kHz profiles where route supports it.
- Publish reproducible benchmark report and known limitations.

Exit criteria:
- 99% delivery for 256 B payload in target environments.
- Throughput targets met for clean and noisy profiles with documented confidence.
- Calibration guidance published for reproducible setup.

Dependencies:
- Phase R6.

### Phase R8: Security Envelope And RC Freeze (Weeks 18-19)
Objective:
- Deliver release candidate with security and frozen API.

Work:
- Add secure session option (X25519 + ChaCha20-Poly1305 + replay window).
- Freeze ABI/API and complete operational docs.
- Publish migration and tuning guidance.

Exit criteria:
- `v0.2.0-rc1` with passing gates and documented security behavior.
- No P0/P1 defects open.

Dependencies:
- Phase R7.

### Phase R9: Advanced Profiles (Post-RC)
Objective:
- Add optional profiles for dense/interfering deployments.

Work:
- Frequency-hopping profile family.
- Multi-user scheduling and collision management.
- Optional low-power wake detection path.

Exit criteria:
- Experimental flags with measured benefit and documented tradeoffs.

Dependencies:
- Stable RC baseline.

## Robustness Acceptance Matrix
1. Noise:
   - Office-like background + burst injectors.
   - Target: 256 B message delivery >=99%, no runaway timeout cascades.
2. Echo / Multipath:
   - Desktop-canyon delay spread sweeps with deep comb nulls.
   - Target: stable lock and no false-lock amplification.
3. Reverb:
   - Reverberation tail scenarios with controlled RT60 buckets.
   - Target: false-lock <=1e-4/frame and bounded reacquisition latency.
4. Motion / Doppler:
   - CFO trajectories up to +/-80 Hz.
   - Target: decode continuity with bounded PER impact.
5. End-to-end UX:
   - Verify downshift/upshift behavior is stable and explainable from metrics.

## Checkpoint Milestones
### M1 (End R2)
- Hardened sync + CFO compensation merged.
- Lock/CFO KPI artifacts published.

### M2 (End R4)
- Equalization + FEC/HARQ merged.
- Robust/noisy scenario targets met in simulation bench.

### M3 (End R7)
- Full hardware validation report published.
- Default deployment profile finalized.

### M4 (End R8)
- RC tag and API freeze complete.

## Immediate Next 2 Sprints
Sprint A:
- Complete Phase R0 harness updates.
- Start Phase R1 sync/reverb hardening.

Sprint B:
- Finish Phase R1.
- Implement Phase R2 coarse+fine CFO tracking and publish first Doppler benchmark set.
