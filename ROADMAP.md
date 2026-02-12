# Cyrinx Roadmap

## Scope
This roadmap sequences `cyrinx` from its current simulation-oriented transport core to a production-grade ultrasonic desktop↔phone link optimized for Apple Silicon MacBook Pro and iPhone Pro Max in the 1-2 ft range.

Current baseline already implemented:
- C ABI + Swift bindings
- Framing, CRC, fragmentation/reassembly, ping-pong ACK transport
- ARC state machine and policy hooks
- In-memory link simulation path
- PHY utility primitives (ZC generation, CFO estimate, dynamic CP heuristic)
- Lint/format/test gates (`./scripts/check.sh`)

Not yet implemented (and planned here):
- Real-time audio I/O (`RemoteIO`, `AVAudioSessionModeMeasurement`)
- vDSP-backed modulation/demodulation for D-CSS and OFDM
- Real hardware PHY pipeline and calibration
- In-library security envelope plumbing
- Multi-user/frequency-hopping profiles

## Sequencing Principles
1. Reliability before throughput: mode downshift and delivery guarantees gate every performance upgrade.
2. Deterministic simulation before hardware: every PHY/MAC change gets simulation vectors before live testing.
3. Hardware truth over paper thresholds: ARC thresholds are finalized from measured data, not static constants.
4. One stable API surface: keep C ABI and Swift wrapper stable while internals evolve.
5. Release by evidence: every milestone has explicit exit criteria and benchmark artifacts.

## Phase Plan

### Phase 0: Repo Hardening And Test Infrastructure (Week 1)
Objective:
- Make the repo production-ready for iterative modem development.

Work:
- Add channel simulation framework (`Desktop Canyon` direct path + desk reflection + noise + Doppler).
- Add benchmark harness for setup latency, goodput, PER, retries, mode transitions.
- Add golden-vector fixtures for frame codec, CRC, ZC, CFO, ARC decision logic.
- Add CI workflow to run `./scripts/check.sh` on every push/PR.

Exit criteria:
- Deterministic simulation run with reproducible seed.
- Bench command outputs JSON metrics to `artifacts/bench/*.json`.
- CI green on lint/format/tests.

Dependencies:
- None.

### Phase 1: Implement G1 Discovery PHY (Weeks 2-3)
Objective:
- Replace synthetic discovery behavior with real preamble detection and timing/CFO estimation primitives.

Work:
- Implement dual-ZC preamble insertion and matched-correlation detector.
- Implement sample-accurate timing lock and CFO correction from repeated ZC blocks.
- Add false-positive controls (correlation thresholding, hysteresis, lock timeout).
- Integrate discovery results into current session state machine and events.

Exit criteria:
- Detection probability >=99.9% at SNR >= -6 dB in simulation.
- CFO estimate error <= +/-5 Hz for true CFO in +/-80 Hz.
- Discovery->linked state change driven by decoder output (not injected reports).

Dependencies:
- Phase 0 simulation harness.

### Phase 2: Implement G2 Robust D-CSS Path (Weeks 4-5)
Objective:
- Provide resilient control/header and fallback payload mode for noisy/interference conditions.

Work:
- Implement D-CSS modulator/demodulator over full 18.5-23.5 kHz band.
- Implement symbol timing and differential decoding with burst-noise tolerance.
- Wire G2 into existing frame codec and reassembly path.
- Integrate interleaver hooks and LDPC 1/2 interface stubs.

Exit criteria:
- 300-600 bps net throughput in simulation profiles with intermittent burst noise.
- Header decode success >=99% under target noisy scenarios.
- Automatic ARC fallback from turbo modes to G2 on threshold violations.

Dependencies:
- Phase 1 discovery lock.

### Phase 3: Implement G3 OFDM Turbo Modes (Weeks 6-8)
Objective:
- Deliver adaptive high-throughput payload transport (QPSK/16QAM, 64QAM experimental).

Work:
- Implement OFDM modem chain:
  - 1024 FFT
  - 106 active carriers
  - pilot/null map
  - CP insertion/removal (default 96 samples)
- Implement channel equalization and EVM estimation per frame.
- Add adaptive constellation mapping (QPSK, 16QAM, optional 64QAM flag-gated).
- Add HARQ parity round plumbing (up to 3 rounds).

Exit criteria:
- QPSK goodput >=4 kbps effective in clean simulation.
- 16QAM goodput >=8 kbps effective in clean simulation.
- No uncontrolled mode flapping in stationary and mild-motion simulations.

Dependencies:
- Phase 2 robust control path for fallback.

### Phase 4: ARC Production Tuning And Stability (Weeks 9-10)
Objective:
- Convert static thresholds into validated, stable rate-control behavior.

Work:
- Tune ARC thresholds and dwell windows using large simulation sweeps.
- Add oscillation suppression checks (minimum dwell, upshift hold, retransmit guard).
- Add explicit timeout recovery and reacquisition flow instrumentation.
- Add KPI dashboards for transition latency and dwell distribution.

Exit criteria:
- Downshift within <=2 frames on abrupt SNR/EVM degradation.
- Upshift only after configured hold windows with <1 oscillation/minute under mixed traffic.
- Link recovery to discovery state within timeout budget and successful reacquisition.

Dependencies:
- Phase 3 modem metrics.

### Phase 5: Apple Real-Time Audio Backend (Weeks 11-13)
Objective:
- Move from in-memory transport to real ultrasonic over-air exchange on target hardware.

Work:
- Implement iOS audio backend:
  - `AVAudioSessionCategoryPlayAndRecord`
  - `AVAudioSessionModeMeasurement`
  - `RemoteIO` path (no voice-processing chain)
- Implement macOS CoreAudio backend with matching 48 kHz path.
- Integrate vDSP kernels for FFT/correlation hot loops.
- Add runtime calibration burst for per-device notch/frequency response estimation.

Exit criteria:
- End-to-end message exchange MacBook Pro↔iPhone Pro Max at 1-2 ft in quiet room.
- Transport remains stable with expected guard interval timing.
- Measured degradation when forcing voice-processing path is documented.

Dependencies:
- Phase 1-4 completed modem path.
- Access to target Apple hardware.

### Phase 6: Security Envelope Integration (Weeks 14-15)
Objective:
- Provide first-class secure session option while preserving transparent transport mode.

Work:
- Add secure session mode with:
  - X25519 key agreement
  - ChaCha20-Poly1305 payload protection
  - nonce + replay window enforcement
- Add key/session lifecycle APIs in C and Swift layers.
- Add compatibility mode: external security remains supported.

Exit criteria:
- Replay attacks rejected in tests.
- Tampered ciphertext rejected with deterministic error reporting.
- Secure-mode throughput and latency overhead quantified.

Dependencies:
- Phase 5 real transport.

### Phase 7: Hardware Validation Matrix And UX Hooks (Weeks 16-17)
Objective:
- Validate real-world robustness and integrate UX-driven physical optimizations.

Work:
- Test matrix:
  - distances 1 ft / 2 ft
  - on-axis and off-axis
  - quiet and office noise
  - desk material variants
- Validate dynamic CP behavior with cloth/mousepad damping scenarios.
- Validate static-device optimization path (sensor-assisted policy controls).
- Add structured benchmark report generation.

Exit criteria:
- 256-byte delivery success >=99% in target 1-2 ft conditions.
- Throughput targets met in clean and moderate-noise profiles.
- KPI report published with reproducible methodology.

Dependencies:
- Phase 5 hardware backend.

### Phase 8: Release Candidate And API Freeze (Weeks 18-19)
Objective:
- Ship an RC suitable for external integration.

Work:
- Freeze C ABI and Swift wrapper surface.
- Write integration guides and operational tuning docs.
- Add semver versioning, changelog, and migration notes.
- Package benchmark artifacts and known limitations.

Exit criteria:
- `v0.2.0-rc1` tagged with complete docs and passing checks.
- No P0/P1 open defects in tracker.

Dependencies:
- Phase 0-7.

### Phase 9: Advanced Profiles (Post-RC)
Objective:
- Add optional advanced operating modes for dense/interfering environments.

Work:
- Frequency-hopping profile family.
- Multi-user scheduling strategies and collision management.
- Optional ANE wake detector integration for low-power standby on iPhone.

Exit criteria:
- Experimental profile flags with documented constraints.
- Measured benefit over baseline under interference-heavy scenarios.

Dependencies:
- Stable RC baseline.

## Deliverable Checklist By Milestone

### M1 (End Phase 1)
- Discovery modem path merged.
- Golden vectors for ZC detection/CFO.
- Simulation benchmarks for detection probability.

### M2 (End Phase 3)
- G2 + G3 modem paths merged.
- ARC integrated with real metrics (not synthetic-only).
- Throughput targets met in simulation.

### M3 (End Phase 5)
- Real Mac↔iPhone over-air transport demo.
- Apple audio backend docs and caveats.
- Hardware smoke-test report.

### M4 (End Phase 8)
- RC tag + frozen API.
- Security mode available.
- Release documentation complete.

## Risks And Mitigations
1. iOS processing pipeline unexpectedly suppresses ultrasound:
   - Mitigation: strict measurement mode configuration validation and runtime diagnostics.
2. Desk multipath nulls collapse narrow-band symbols:
   - Mitigation: keep robust wideband fallback always available; dynamic CP and calibration.
3. ARC oscillation in borderline SNR:
   - Mitigation: hysteresis, dwell timers, retransmit-aware hold rules.
4. Thermal/CPU constraints on mobile:
   - Mitigation: vDSP-first kernels, profiling gates, optional lower-duty-cycle modes.
5. Security overhead reduces goodput:
   - Mitigation: benchmark secure vs external mode and tune framing overhead.

## Immediate Next 2 Sprints
Sprint A:
- Phase 0 complete and CI artifacts in place.
- Start Phase 1 ZC detector implementation.

Sprint B:
- Finish Phase 1 and begin Phase 2 D-CSS modem path.
- Publish first simulation benchmark package.
