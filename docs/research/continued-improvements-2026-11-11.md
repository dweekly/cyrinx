# Continued improvements from `main@cc50068`

> **Provenance.** Written by an AI coding/research agent (Antigravity) on 2026-11-11 against `main@cc50068` (after PRs `#92`–`#102`). The processor timings in section 1 were measured locally on this Mac during this review using the reproduction steps shown; nothing else here plays or captures new audio. Stack rank, scope, and thresholds are proposed by an agent and await owner confirmation.

## In plain English

Between `6956552` and `cc50068`, the project solved its worst stationary off-contact failure and several concrete software defects. A repaired research receiver (coherent reference-tone timing fit plus blind clock-offset resampling) and two-microphone combining carried **39.0 kbps** from laptop to phone and **12.6 kbps** from phone to laptop across about 2.5 ft of desk air on one stationary pose, where the shipped receiver decoded zero bytes. Python's BPSK mapping bug, the slow C chirp search, Android's broken `X25519` math and `playrec_pcm` audio check, capture retention in linked git worktrees, and the local Swift 6.4 gates were also fixed.

The next improvements fall into five groups, ordered by leverage and risk:

1. **Three fast checks on recordings already on disk** before spending more bench time: separate space-heater air currents from phone orientation on this morning's rotated capture (`867/870` down to `169/870`), find why the Python reference and C decoder disagreed on two retained uplink captures, and test whether the coherent timing fit closes the close-range `98.48%` vs `99.97%` block-delivery gap if those captures are still stored on backup.
2. **Move the proven research receiver fixes into the C library cleanly**, while fixing a remaining channel-0 bias where synchronization ignores the second microphone and can lock onto a late reflection.
3. **Build the measured-channel replay simulator and stop sending the exact same signal out of both laptop speakers**, pairing that with orientation logging on the phone.
4. **Turn on automated C, Swift, Python, and Android tests in GitHub Actions**, and fix a small set of remaining integer-overflow, audio-thread, framing, and chat-state bugs.
5. **Unify the mobile apps onto the C library over JNI** (retiring the separate Swift and Kotlin modem copies), and only then test per-block LDPC coding or motion-tolerant tracking where hardware measurements show they are needed.

---

## 1. Baseline measurements added in this review (`main@cc50068`)

Both [pilot-fit-and-blind-clock-correction-2026-11-10.md](pilot-fit-and-blind-clock-correction-2026-11-10.md) and [live-two-microphone-link-and-ranging-2026-11-10.md](live-two-microphone-link-and-ranging-2026-11-10.md) noted that the processor cost of the research receiver in `scratch/garage/clock_offset.c` had not been measured. Below are single-core median wall times on this Apple M4 Mac (`clang -std=c11 -O2 -Dkiss_fft_scalar=double`, vendored KISS FFT, digital waveforms at `48 kHz`):

| Workload (`48 kHz`) | `6956552` | `cc50068` | Real-time multiple (`cc50068`) | Notes |
|---|---:|---:|---:|---|
| Cloth `96`-sym `64-QAM r2/3`, `NFFT 2048, CP 96` (`4.51 s`, no pad) | `~363 ms` | `100.2 ms` | `45.0× RT` | Shipped C decoder (`169/169` blocks) |
| Same frame + `10.0 s` leading silence (`14.51 s` total) | `~1,365 ms` | `109.3 ms` | `132.7× RT` | Marginal chirp-search cost fell from `100.1 ms/s` to **`0.91 ms/s`** (`110×`) |
| Cloth `64`-sym `p16` (`59` pilots): `FIT_SHIPPED` vs `FIT_COHERENT` | — | `64.1 ms` vs `66.0 ms` | `48.0×` vs `46.6× RT` | Coherent fit adds `+1.9 ms` per frame (`30 µs/symbol`) |
| Desk `NFFT 16384, CP 8192`, `6` sym (`969` pilots, `5.22 s`): `FIT_SHIPPED` vs `FIT_COHERENT` | — | `44.6 ms` vs `46.7 ms` | `117.0×` vs `111.8× RT` | Coherent fit adds `+2.1 ms` per frame (`345 µs/symbol`) |
| Desk `5.22 s` capture (`-28 ppm`): `estimate_clock_offset` (1 pass) | — | `10.5 ms` | — | Re-runs chirp + fine sync via `cyrinx_clock_locate` |
| Desk `5.22 s` capture: `128`-tap Kaiser `resample` (1 call) | — | `23.9 ms` | `218.7× RT` | Includes rebuilding the `1.05 MB` polyphase sinc table each call |
| Desk `5.22 s` capture (`-28 ppm`): **Blind 1-mic decode (`2` passes + coherent fit)** | — | `126.0 ms` | `41.4× RT` | `87/87` blocks (`0/87` on shipped decoder) |
| Desk `5.22 s` capture (`-28 ppm`): **Blind 2-mic MRC decode (`2` passes + coherent fit)** | — | `152.9 ms` | `34.2× RT` | `87/87` blocks; well above the `4× RT` mobile gate (`ROADMAP.md` Rank 2) |

Reproduce without touching tracked files by building `scratch/garage/clock_offset.c` to `/tmp/libcyrinxclock.dylib` with the flags in `scratch/garage/build_clock_dylib.sh` and timing `clib.decode`, `clock_offset.decode`, and `clock_offset.decode2` under `.venv/bin/python`. All `112` tests in `scratch/garage/` (`89` pure-Python + `23` C-dylib tests in `test_clock_offset.py` and `test_bpsk_parity.py`) pass.

---

## 2. Priority 1 — Fast offline diagnostics on retained captures (no new hardware run)

### 1A. Separate heater air convection from phone pose on the rotated morning capture
- **Evidence:** In [live-two-microphone-link-and-ranging-2026-11-10.md](live-two-microphone-link-and-ranging-2026-11-10.md) §7, rotating the phone 180° with a space heater running dropped `64-QAM r3/4` from `867/870` to `169/870` (`256-QAM r1/2` from `778/780` to `197/780`). Steady noise did not explain it (`31–37 dB` SNR; the quietest run scored `0/156` while a noisier run scored `166/174`), and the laptop fan came on ten minutes after the failures began.
- **Hypothesis to test offline:** Speed of sound in air changes by about **`+1,710 ppm/°C`**. Warm air plumes from a space heater crossing a `0.95 m` path produce time-varying time-of-flight fluctuations within a `3.5 s` frame (consistent with the morning frame-start clock estimates shifting and widening to `28.5–30.6 ppm` vs `27.1–28.4 ppm` the evening before, and isolated runs decoding `166/174` between `0`-block runs). On `NFFT 16384` (`341 ms` symbols, `2.93 Hz` bin spacing), intra-frame path-delay wander looks like non-constant clock stretch after the two initial training symbols. Alternatively, both microphones saw `-47.3 / -47.6 dB` direct sound (`7 dB` weaker than evening `mic1`'s `-40.4 dB`), pointing to lower static direct-to-reverberant ratio (DRR).
- **Action:** Write a short script in `scratch/garage/` to dump per-symbol `pilot_evm[s]`, `slope_tot[s]`, and `cpe[s]` (`s = 0..5`) on `~/dev/cyrinx-artifacts/garage/ladder-desk-rotated-repeat6-down-20261111T102846`:
  - If `pilot_evm[0]` starts near `0.10–0.14` and climbs across `s = 1..5` (or `slope_tot[s]` curves away from zero after resampling), **moving warm air warped the path mid-frame**.
  - If `pilot_evm[s]` is `0.30–0.40` from `s = 0` on every symbol, **static pose multipath/DRR** caused the loss.
  - Follow with the two-cell hardware check noted in §7 (rotated pose with heater off; original pose with heater on).

### 1B. Resolve the Python-vs-C uplink decode disagreement and bring `modem.py` to parity
- **Evidence:** [range-ladder-2026-11-10.md](range-ladder-2026-11-10.md) §5 records two retained uplink captures where unmodified `cyrinx_bulk.c` decoded `14/22` blocks while `scratch/hw20k/modem.py` decoded `0`. In addition, `modem.py:fit_pilot_phase` still uses only the fragile adjacent-pilot step average.
- **Action:** Diff intermediate arrays (`start`, `base`, `H`, `nv`, pilot weights, `slope_tot`, `llr`) between `modem.py` and `cyrinx_bulk.c` on those two retained uplink captures until the divergence is isolated and fixed; then add an opt-in `coherent` pilot-fit mode to `modem.py` so Python stays an independent referee when Receiver `v2` is promoted.

### 1C. Replay Cyrinx 2.0 near-field captures (if retained) through `FIT_COHERENT`
- **Evidence:** White-noise simulation in [pilot-fit-and-blind-clock-correction-2026-11-10.md](pilot-fit-and-blind-clock-correction-2026-11-10.md) §6 showed `FIT_COHERENT` gaining `~1 dB` on the near-field `64-QAM r2/3 p16` shape (`97.39%` → `99.77%` at `20 dB`) and `~2 dB` on the `16-QAM` control shape.
- **Action:** Check `trogdor` and local archives for raw captures from the `65.875 kbps` (`4,215/4,280`) or `128`-symbol (`7,662/8,600`, `scratch/hw20k/spike8b/inventory.json`) campaigns. Replaying them with `FIT_COHERENT` + blind clock correction is a zero-hardware test of whether the `98.481%` vs `99.967%` close-range resilience gap shrinks or closes.

---

## 3. Priority 2 — Promote Receiver Semantics `v2` into `Sources/CCyrinx`

Right now the shipped C library (`Sources/CCyrinx/cyrinx_bulk.c`) still fails every `2.5 ft` capture (`0` blocks), while `scratch/garage/clock_offset.c` textually `#include`s `cyrinx_bulk.c` and switches behavior via a process-global `_Atomic int`. Promoting the proven pieces into `Sources/CCyrinx` as a versioned receiver contract (`semantics_version = 2`) should bundle four changes:

### 2A. Coherent pilot-slope fit (`cyrinx_research_fit_coherent`) per config, not process-global
- Move `cyrinx_research_fit_coherent` (`scratch/garage/clock_offset.c:64-100`) into `Sources/CCyrinx/cyrinx_bulk.c`, selectable via `cyrinx_bulk_config` / `cyrinx_profile_t` (preserving `v1` bit-exactness for legacy golden vectors while making `v2` the default for new profiles).
- Measured CPU overhead is `30–345 µs/symbol` (`<3%` of decode time), so no algorithmic shortcut is needed.

### 2B. Symmetric multi-microphone synchronization, first-arrival lookback, and 3-way `{CH0, CH1, MRC}` selection
- **Defect in current C architecture:** In `Sources/CCyrinx/cyrinx_bulk.c:1360-1397` (and `clock_offset.c:203-216, 421`), coarse chirp correlation, fine `sync0` correlation, and clock-offset estimation inspect **only `rx` (channel 0)** and apply that `base` index to channel 1. On the face-up desk pose, `mic0`'s direct sound was `-16 dB` below `mic1`'s and its strongest correlation peak was a reflection **`9.5 ms` (`456` samples) late**. Synchronizing on `mic0` placed `base` `~430` samples *after* `mic1`'s direct arrival (outside the `24`-sample early bias), turning `mic1`'s direct path into pre-cursor inter-symbol interference (`385/870` blocks synced on `mic0` vs `867/870` synced on `mic1`, forcing `ladder.py` to run four decodes per capture). Furthermore, `cyrinx_select_auto_v1_receiver` (`cyrinx_bulk.c:1254-1260`) only compares `PRIMARY` (`mic0`) against `MRC`, never `SECONDARY` (`mic1`) alone.
- **Change:**
  1. Sum non-coherent matched-filter energy across all supplied channels (`|MF_0[n]|^2 + |MF_1[n]|^2`) for coarse chirp sync and fine `sync0` sync, and apply a **first-arrival lookback window** (earliest local peak `>= 0.35..0.50 × max_peak` within `30 ms` before `argmax`, matching the logic already proven in `scratch/garage/rangefind.py:122-128` and `speaker_matrix.py:84-87`) so `base` anchors on the earliest direct path across microphones.
  2. Extend `auto_v2` holdout-pilot scoring to evaluate `{CH0, CH1, MRC}` symmetrically with no primary-channel assumption (`ROADMAP.md` Rank 9 constraint).

### 2C. Joint dual-mic clock-offset estimation, session caching, and a static Kaiser polyphase table
- **Dual-mic ramp score:** In `cyrinx_research_estimate_clock_offset` (`clock_offset.c:287-302`), accumulate `cross[k] = sum_m H1_m[k] * conj(H0_m[k])` across all channels before searching the phase ramp. Weighting automatically follows `|H_m[k]|^2` per bin and reduces single-frame `±8–10 ppm` estimation noise when one microphone is shadowed.
- **Cross-frame clock state:** Because crystal clock offset is a physical property of the endpoint pair (`~27.7 ppm` here, drifting slowly with temperature), expose an optional prior/running estimate (`ppm_hint` + coherence-weighted update across frames) so a single noisy frame preamble cannot jump `10 ppm` away from the established link clock.
- **Hoist the `1.05 MB` resampler table & reuse `base`:** `cyrinx_research_resample` (`clock_offset.c:135-155`) currently `malloc`s and computes `1,025 × 128` Kaiser-Bessel sinc taps (`65,600` Bessel series loops + `131,200` `sin` calls) on every call, and `cyrinx_clock_locate` re-runs full chirp correlation on every pass. Precomputing the `1,025 × 128` table once (`static const` or `pthread_once`) and narrowing pass-2 sync around pass-1's scaled `base` will nearly halve blind 2-mic decode time (`~153 ms` → `<85 ms` on M4).

### 2D. Multi-frame capture enumeration, silence early-exit, and `±Inf` lag guard
- **Multi-frame & silence gate:** `cyrinx_bulk_demod_impl` currently takes a single global `argmax` over `rx_len` with no peak-to-floor check: pure-zero captures still run 98 FFTs and full Viterbi (`841 ms` in `silentCaptureReturnsOrderedAllInvalidMask`), and multi-frame recordings silently drop all frames except the loudest chirp. Add a peak-to-median acquisition gate (returning `0` blocks immediately on noise-only input) and a frame-locator helper that masks `±frame_samples` around found chirps.
- **`±Inf` sample guard:** Commit `b32c9c4` made `cyrinx_matched_filter` survive `NaN` padding samples, but as noted in `Tests/CyrinxTests/CyrinxBulkPHYTests.swift:438-439`, an `±Inf` sample in `cyrinx_matched_filter_direct` (`cyrinx_bulk.c:948-952`) sets `best = +Inf` and hides valid frames elsewhere in the capture. Changing `if (a > best)` to `if (isfinite(a) && a > best)` fixes `±Inf` identically to `NaN`.

---

## 4. Priority 3 — Replay simulator, pose instrumentation, TX speaker policy, and profile registry

### 3A. Build `scratch/sim/` against the six frozen desk gates + log phone orientation
- Implement the linear measured-channel replay tool specified in [replay-simulator-plan-2026-11-11.md](replay-simulator-plan-2026-11-11.md) (`scratch/sim/replay.py`), enforcing `evidence_class: simulated:measured-channel` on every emitted row and extending `scripts/check-claims.sh` so simulated run IDs cannot be cited without the word `simulated`.
- Before running the 4-pose hardware sweep (`face up`, `face down`, `upright`, `handheld`), add a gravity/accelerometer snapshot (`Sensor.TYPE_GRAVITY` / `TYPE_ACCELEROMETER`) to the Android HIL recording log alongside `MicrophoneInfo` (`Apps/HIL/android/.../MainActivity.kt`) so orientation is recorded mechanically with every capture.

### 3B. Replace dual-speaker identical-mono TX with single-speaker or cyclic-delay diversity
- **Evidence:** [live-two-microphone-link-and-ranging-2026-11-10.md](live-two-microphone-link-and-ranging-2026-11-10.md) §§5–6 showed that macOS mono playback drives both speakers with the same waveform, causing `>=6 dB` destructive interference across `~10–11%` of bins; right-speaker-only beat both-speakers-same in `12/12` paired runs, and delaying one speaker by `8` samples (`0.17 ms` cyclic/delay diversity, well inside any guard) won `6/6` paired runs without needing channel feedback.
- **Change:** Make stereo output routing explicit in the macOS/iOS/Android audio engines and `cyrinx_batch` / turn coordinator:
  - Open-loop default on stereo transmitters: single speaker (selected from reverse/probe pilot EVM) or small fixed inter-speaker delay (`8` samples) when probe feedback is not yet available — never identical unshifted mono on two separated speakers.
  - Closed-loop upgrade once bidirectional turns run: quantized sub-band phase alignment (`+3 dB` mean gain, cutting `>10 dB` fades from `~7.8%` to `~2.5%` of bins).

### 3C. Register bootstrap & long-guard profiles (`cyrinx_profiles.c`) and reconcile `ADR 0006` vs `CYRINX_3_PLAN.md`
- `Sources/CCyrinx/cyrinx_profiles.c:205-220` currently registers only five near-field `16-QAM` and `64-QAM` (`NFFT 2048`) profiles — zero `QPSK`/`BPSK` profiles and zero long-guard (`NFFT 8192 / 16384`, `CP 4096 / 8192`) off-contact profiles.
- `docs/adr/0006-session-initiation.md` specifies lazy Tier-0 startup (sending immediate payload at a cautious profile and sounding only on demand), whereas `docs/CYRINX_3_PLAN.md` (`C3-07`, lines 860–886) still describes a mandatory `~30 s` upfront mutual sounding ceremony.
- Reconcile in favor of `ADR 0006`: register cautious near-field and off-contact `QPSK r1/2` bootstrap profiles, optionally precede session setup with a `<0.5 s` BeepBeep ranging exchange (`rangefind.py` primitive) to pick near-field (`CP 96–240`) vs room-air (`CP 4096–8192`) guard families immediately, and piggyback SNR/bit-loading feedback onto data burst preambles.

---

## 5. Priority 4 — Automated CI (`C3-02`) and remaining correctness, audio-thread, and security fixes

### 4A. Add core build, test, sanitizer, and Android unit-test workflows (`C3-02`)
- `.github/workflows/` currently contains only `repository-claims.yml` and path-filtered `chat-c3-28.yml`. Changes to `Sources/CCyrinx`, `Sources/Cyrinx`, `scratch/garage`, and `Apps/HIL/android` do not run in CI.
- Add a PR workflow covering:
  1. **Linux fast C + Python gate (<2 min):** compile `CCyrinx` and `clock_offset.c` under `gcc` and `clang` with `-Wall -Wextra -Werror -fsanitize=address,undefined` (plus a 32-bit `-m32` layout/overflow test on `cyrinx_batch.c`), and run `.venv/bin/python -m pytest scratch/garage -q`.
  2. **macOS Swift gate:** `./scripts/format-check.sh`, `./scripts/lint.sh`, `./scripts/check-api-inventory.sh`, and `swift test`.
  3. **Android HIL unit gate:** `cd Apps/HIL/android && ./gradlew testDebugUnitTest assembleDebug` (exercising `X25519Test.kt` and JVM unit tests).

### 4B. Concrete defects still open at `cc50068`

| File & location | Defect | Recommended fix |
|---|---|---|
| `Sources/CCyrinx/cyrinx_batch.c:24-25, 56-58` | `reachable + view->stride - 1` overflows `uint64_t` near `UINT64_MAX` when `stride > 1`; `view->first_sample + i * view->stride` lacks a 32-bit `SIZE_MAX` check | Compute `(reachable - 1) / view->stride + 1`; reject inputs where `first_sample + (count - 1) * stride > SIZE_MAX / sizeof(float)` |
| `Sources/Cyrinx/AudioScaffold.swift:1846-2066`, `Apps/HIL/iOS/App/IOSAudio.swift:125` | Real-time CoreAudio input/render callbacks allocate `[Float]`, lock `NSLock`, call `Array.removeFirst`, log, and perform synchronous `FileHandle.write` disk I/O; Swift 6 `#SendableClosureCaptures` warning at `L1852` | Replace callback queues with pre-allocated lock-free single-producer/single-consumer ring buffers; drain disk/session work on a background thread |
| `Sources/CCyrinx/cyrinx.c:75-119`, `Sources/Cyrinx/AudioScaffold.swift:1708`, `Apps/HIL/macOS/HILViewModel.swift:56,224` | Unsynchronized concurrent mutation of lockless `cyrinx_session` from `rxQueue` and `@MainActor`/TX tasks | Confine all `cyrinx_session` calls to one serial queue or Swift actor |
| `Sources/Cyrinx/AudioScaffold.swift:133-220, 1340-1370`, `Sources/Cyrinx/AcousticPHY.swift:330-345` | iOS `channels == 2` writes interleaved samples across frames at `0.5×` sample rate; `LinearStreamResampler` interpolates `L[i]` with `R[i]` on stereo macOS input; unconditional `print("[prof]...")` on every TX frame | Deinterleave/resample per channel, fix iOS frame stride for `channels == 2`, and gate `[prof]` logging behind debug configuration |
| `Sources/Cyrinx/VDSPPHY.swift:103-115`, `Sources/Cyrinx/AcousticPHY.swift:608-639`, `AcousticPhyLink.kt:1542-1558` | Legacy OFDM RX applies `peerNotchMask` instead of local `notchMask`, and `expectedOFDMSamples` divides by total subcarriers without subtracting notched bins | Filter RX bins with local `notchMask` and compute symbol count from active (unnotched) data carriers |
| `Sources/Cyrinx/AcousticPHY.swift:122-128, 522-533`, `AcousticPhyLink.kt:212-218, 574-586`, `X25519.kt:66-92` | Even when the optional prototype crypto envelope is enabled, any incoming byte-0 `0xE1` frame bypasses `secureEnvelopeUnpack`, an all-zero `0xE1` key wipes established session keys mid-session, and `X25519.kt` branches on secret bits with `BigInteger` | Reject plaintext `0xE1` and key-wipe frames once keys are active, and use Android platform `KeyAgreement.getInstance("X25519")` (or retire the legacy envelope when C3 lands) |
| `Apps/HIL/android/.../MainActivity.kt:1299-1308` | `runOnUiThread { startSession(role) }` races against immediate `val current = session` read on `ioExecutor` | Await UI-thread latch completion before reading `session` |
| `Apps/Chat/Apple/.../ChatModel.swift:134, 337-343` vs `Apps/Chat/Android/.../ChatProjection.kt:51-59` | Apple initializes `expectedEventSeq = nil` (missing pre-attach overflow when first event has `eventSeq > 0`), differs on `.degraded` vs `.peerLost` banner priority, ignores `-chat.simulated false` in `ChatAppSession.swift`, and Android lacks `onNewIntent` under `singleTask` | Initialize `expectedEventSeq = 0` on Apple, align banner/upsert rules via shared fixture vectors, honor `-chat.simulated false`, and forward `onNewIntent` on Android |

---

## 6. Priority 5 — Single-engine runtime consolidation & gated PHY research

### 5A. Converge macOS, iOS, and Android onto `CCyrinx` (`C3-06..C3-27`, `C3-31..C3-35`)
1. **Android NDK/CMake JNI bridge (`C3-06`):** Bind `cyrinx_profiles`, `cyrinx_batch`, and Receiver `v2` directly into Android so Kotlin calls the exact same C implementation as Swift and Python.
2. **Delete duplicate modem forks & isolate bench codecs (`ADR 0004`):** Retire `Apps/HIL/android/.../BulkDemod.kt` (`643` lines) and `Apps/HIL/iOS/App/BulkDemod.swift` (`700` lines — which also uses seed-oracle block attribution, computes goodput span over `okFrames` only, and calls `bindMemory(to: Int16.self)` on unaligned WAV slices). Move the six experimental `Raw*` tone codecs out of the public `Cyrinx` library target into HIL/bench-only targets.
3. **SIMD real-FFT backend & reusable decoder scratch arena:** Add a single-precision real-packed FFT backend (`vDSP_DFT_zrop` on Apple; `PFFFT` or NEON radix-4 on Android/Linux) and replace the `~35` per-frame `malloc`/`free` calls in `cyrinx_bulk_demod_impl` + `cyrinx_viterbi` with a pre-allocated decoder context.
4. **Live half-duplex turn engine for `Apps/Chat`:** Replace `SimulatedChatTransportClient` in `Apps/Chat/Apple` and `Apps/Chat/Android` with real acoustic turns driven by `cyrinx_batch` + selective-repeat block bitmaps, sizing inter-turn guard silence to the measured room ring-down (`~90–180 ms` off-contact, `~20 ms` near-field).

### 5B. Gated physical-layer candidates (start only after Priorities 1–3 clarify the bottleneck)
1. **Per-block rate-compatible `QC-LDPC` (`N = 2048` or `4096` coded bits) + selective-repeat / IR-HARQ:**
   - *Trigger:* Amending the `ROADMAP.md` LDPC deferral once Priority 3's simulator and hardware runs confirm code margin or whole-frame (`3.5–5 s`) latency is the active limiter.
   - *Why:* `cyrinx_bulk.c` currently encodes all `~100–200 kb` of a frame as one `K=7` convolutional trellis, preventing sub-frame streaming delivery, losing `>2 dB` when punctured to `r=3/4` or `r=5/6` (`NF-22`), and forcing full-block retransmissions. Per-block `QC-LDPC` (1–2 OFDM symbols per codeword, `int8_t` SIMD layered min-sum with syndrome early exit) provides `+1.5–3 dB` BICM gain at `r=2/3..5/6` and native circular-buffer incremental parity for failed blocks.
2. **Time-varying drift tracking for handheld / moving-air (`NFFT 8192 / 16384`) links:**
   - *Trigger:* Priority 1A or the Priority 3A handheld pose sweep showing intra-frame phase/timing drift across `170–341 ms` symbols.
   - *Candidates to compare on retained captures:* (a) continuous symbol-by-symbol fractional time-warping driven by coherent `slope_tot[s]` trajectories + DVB-T-style staggered pilots (`k_0 + step * (s mod 4)`) smoothed per bin across time (never smoothed raw across bins, per `NF-2`); or (b) shorter-symbol `NFFT 4096` with strong low-rate coding (`Li et al. 2008` underwater regime) or pulse-shaped single-carrier adaptive DFE (`Tabak et al. 2021`) in `12–23 kHz` where echo tails are `30–52 ms` and symbol periods stay short enough to track hand motion.
3. **Frequency-hopped soft-decision MFSK floor in C (`0.4–1.0 kbps`):**
   - *Trigger:* Non-stationary or heavily shadowed placements where even Receiver `v2` `BPSK/QPSK` (`r=1/2` or `r=1/4`) fails acquisition or equalization.
   - *Why:* `scratch/hw20k/mfsk.py` (`138 bps`) is Python-only, hardcodes a `45 ms` guard skip, wastes `87.5%` of orthogonal tone spacing (`102.4 Hz` vs `13.3 Hz`), and suffers `-18 dB` 8-tone peak scaling. Hopping across 2–3 orthogonal tone sets so echoes decay on resting bins while feeding soft tone energies into binary FEC raises the non-coherent floor `3–7×` without coherent phase tracking.
