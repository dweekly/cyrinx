# Cyrinx Publication Plan

Fresh as of 2026-07-17. This document retains the sequenced, multi-PR plan that
took Cyrinx from a private prototype to a public Apache-2.0 v1.0.0 release. The
remaining publication work is to archive the raw Cyrinx 2.0 evidence and
prepare an arXiv submission. The v2.0.0 package and companion site are public.

Decisions locked (2026-06-10):

- **Library scope:** port the wideband bulk PHY into a **portable C core**
  (`CCyrinx`) so the *library* (not just the Python harness) delivers the
  36.6 / 27.3 kbps headline, on any platform.
- **PHY home:** the canonical DSP lives in **portable C**. Swift/vDSP is an
  **Apple acceleration backend**, not the source of truth; the target Android
  architecture binds the same C via JNI, but that binding is not implemented.
  (Resolves the PR 1.1 architecture question below.)
- **License:** Apache-2.0 (permissive + patent grant). Any vendored FFT must be
  license-compatible (KISS FFT / pocketfft are BSD-3 → OK).
- **Website:** Cloudflare Pages on **cyrinx.org** (registered 2026-06-10).
- **Paper:** arXiv (cs.NI / eess.SP) + PDF on the site.

The detailed phase checklist below preserves historical sequencing and is not
the authoritative status tracker; use this section, README.md, and ROADMAP.md
for current state.

## Status (v2.0.0, 2026-07-17)

**Public baseline:** v2.0.0, the portable-C TX/RX bulk PHY, the thin Swift
`BulkPHY` binding, golden-vector validation, the repositioning-guidance API,
the site, and the public repository are complete. `main` includes the Moto G
findings through PR #65. The core technical risk—porting the measured bulk PHY
into a portable C library—is closed.

**Cyrinx 2.0 receiver work completed on the current branch:** canonical-C
two-microphone MRC, automatic receiver selection, and payload-independent
pilot-local LLR weighting, including deterministic contract tests and a
prospective Pixel 7a OTA campaign. Swift is the thin library binding. The
Kotlin receiver is still a legacy DSP fork rather than the target architecture;
Python is retained as a research oracle and independent measurement referee.

**Automatable, remaining:** 1.4 adaptive sounder + MCS ladder + MFSK floor
(feeds 1.4b real metrics); full Android JNI migration and retirement of the
Kotlin HIL `BulkDemod` fork; 1.8 transport integration; 1.9 optional crypto +
tradeoff doc; Phase 2 (#4 BPSK, #6 spread-spectrum, #5 research); a stable raw
evidence archive. A separate
11-page Cyrinx 2.0 follow-on source/PDF now documents the 2026-07-17 results;
the 28-page Cyrinx 1.0 paper remains the historical system report.

**1.10 library-native OTA — DEMONSTRATED and extended:** the 2026-06-10
12.8 kbps QPSK r1/2 demonstration first established the C codec OTA path
(25/25 blocks, 3/3 frames). The 2026-07-17 prospective campaign then exceeded
the 36.571 kbps benchmark with the current C receiver. The remaining 1.10 work
is cross-direction and cross-device breadth, not proof that the library can
carry a high-rate OTA result.

**User-gated:** the arXiv *submission* needs the account and endorsement. The
repository, v2.0.0 release, and updated companion site are public.

Day-to-day decisions, measurements, dead ends, and rationale are logged in
[publication-journal.md](publication-journal.md) as we go — raw material for the
final paper update (Phase 3). Durable "don't do that" results still graduate to
[NEGATIVE_FINDINGS.md](NEGATIVE_FINDINGS.md).

---

## Cyrinx 2.0 evidence incorporated in the follow-on paper

The publication-facing flagship remains the schedule-comparable class. With a
12,000-sample inter-frame gap, the prospective 2026-07-17 Pixel 7a campaign
measured **65.875457875 kbps mean goodput**, with 4,215/4,280 blocks decoded
(98.4813%). The candidate won all eight order-balanced pairs (exact sign-test
p = 1/256). It did **not** pass the strict reliability gate: the conservative
baseline decoded 99.9667% of its blocks. The result is therefore a measured
goodput improvement with a reliability tradeoff, not a claim that every link
should select this profile.

An isolated replay of those fresh OTA captures through the frozen pilot-local
and global-only reliability decoders decoded 4,215 versus 3,868 blocks. All
eight candidate runs improved and none regressed. The two decoder roles retain
the same pilot-only receiver-selection policy, so this comparison supports the
pilot-local reliability change; it does not separate the local estimator's
smoothing, interpolation, and blend components.

The highest eight-pair confirmatory **zero-gap** campaign used pilot spacing 64
and 96 OFDM symbols. It measured **69.651849660 kbps mean goodput** (range
65.731–72.641 kbps; harness-reported gross rate 68.636220472 kbps), decoding
6,129/6,760 blocks (90.6657%). The baseline decoded 4,509/4,520 blocks
(99.7566%). All 16 planned runs completed; the candidate won all eight pairs
(p = 1/256), but again failed the strict reliability gate. This is 1.9045x, or
90.45% above, 36.571 kbps — **not almost triple** — and it must not be compared
as though its schedule matched the 12,000-sample-gap flagship.

The accompanying zero-gap campaigns show an observed design frontier rather
than a monotonic amortization win: pilot spacing 16 measured 69.110 kbps at
97.009% block success; spacing 32 measured 68.960 kbps at 93.311%; and the
separate 128-symbol campaign measured 66.102 kbps at 89.093%. These were not a
randomized pilot-spacing/frame-length factorial, so the intermediate and
negative outcomes belong in the paper without a causal length claim.

Reproducibility bindings for the zero-gap confirmatory result:

- plan SHA-256:
  `8e5f1062dcccc42bbfac866a8659bf8d1884dd54bb054a9f42d944a1919e966b`
- execution-manifest SHA-256:
  `9846a33717c18e457169578217dbbdabe740eaa26c09965cb24ce128c9d1e462`
- frozen decoder SHA-256:
  `c6e4e5adc0865a379dc036c549cef4cd91528152ffffa66ee2fdf9be47580956`

Both measurements are near-field, route-specific Mac-to-Pixel results. A fixed
3 ft campaign remains future work and must re-characterize levels, routes,
delay spread, and room noise rather than inheriting the near-field profile.
These results are incorporated in
`whitepaper/cyrinx-2-goodput.tex` and its generated 11-page PDF. The remaining
reproducibility gap is archival: compact ledgers and hashes are tracked, while
full manifests, raw captures, frozen binaries, and replay reports remain
ignored local artifacts.

## Critical-path insight

The portable C core is now the **canonical implementation**, including the
current MRC, receiver-selection, and pilot-local reliability behavior. Swift is
a thin binding over that core. The Kotlin HIL receiver is a legacy DSP fork,
and retaining it as production logic creates an observed drift risk rather than
useful implementation diversity. Python remains valuable as a frozen research
oracle and as an independently implemented campaign referee; it should not
become a second shipping PHY.

The remaining consolidation target is therefore narrower and explicit: bind
Android to `CCyrinx` through JNI, retain Swift/Kotlin/Python vectors only where
they provide independent cross-checks, and run the same receiver-contract and
captured-waveform fixtures through every binding before deleting legacy mobile
DSP paths.

### Architecture (resolved 2026-06-10): portable C is canonical

The DSP lives in `CCyrinx`; Swift provides the thin Apple binding and vDSP is
an optional acceleration backend. Android JNI is the target still to complete,
not the current Kotlin architecture. Python validates via `ctypes` and referees
campaigns independently. Consequences baked into Phase 1:

- **FFT:** vendor a portable C FFT (default **KISS FFT**, BSD-3; or pocketfft).
  On Apple, an optional `vDSP`/Accelerate backend behind a compile-time switch.
  Confirm choice in PR 1.1.
- **Golden-vector tolerance is tiered** (FFT backends differ by ~ULPs, so
  sample-exactness across implementations is not achievable):
  - **bit-exact:** `DetRng`, conv-encode + puncture, interleaver, QAM
    bit-mapping (all integer/deterministic);
  - **float-tolerant:** FFT outputs / OFDM time samples (assert EVM or
    max-abs-error below a documented threshold);
  - **byte-exact:** the final CRC-verified decoded payload.
  The Python reference (numpy FFT) is the *semantic* oracle; the C core is the
  *canonical* implementation; the two agree on decoded bytes, not on raw floats.

---

## Phase 0 — Public-readiness hygiene (small, lands on `main`)

- [ ] **0.1** Add `LICENSE` (Apache-2.0) + per-file SPDX headers; `NOTICE`.
- [ ] **0.2** Scrub committed junk: remove the tracked compiled artifact
      `TestFFT.class`; extend `.gitignore` (`*.class`, `*.dex`, `*.o`, `*.a`;
      build dirs already ignored). **Keep** the source FFT/OFDM parity spikes
      (`scratch_fft.swift`, `TestFFT.java`, `scratch/Fft*.swift`) per "ship the
      spike" — *move/organize* them under `scratch/fft/` with a README rather
      than delete (they pin the FFT conventions the C port must match; see PR
      1.1). Confirm no secrets/PII in history (private repo → public; check
      `git log -p` for keys, the X25519 work).
- [ ] **0.3** Prune stale branches/worktrees (`wp-update`, `whitepaper`,
      three `worktree-agent-*`) after confirming `git log --oneline main..<b>`
      is empty for each.
- [ ] **0.4** Top-level `CONTRIBUTING.md`, `SECURITY.md`, issue/PR templates.
- [ ] **0.5** Curation pass: every `.md` cited from nearest README with a
      "fresh as of" date (CLAUDE.md rule); `docs/INDEX.md` if needed.

## Phase 1 — Bulk PHY into the portable C core (the long pole)

Each PR keeps `swift test` + the C unit tests green and adds golden-vector
assertions at the tiered tolerance above.

- [x] **1.1 Golden-vector harness + FFT choice.** Python emits canonical
      fixtures for fixed (config, seed, payload): encoded bits, interleaved
      bits, QAM symbols, OFDM time samples, full TX waveform, and a captured RX
      → decoded payload. Land as fixtures + a C test rig (and Swift test target)
      that loads them at tiered tolerance. Vendor the FFT (KISS FFT default).
      **This is the contract; it lands before any C DSP.**
- [x] **1.2 C TX path** in `CCyrinx`: `DetRng` (verify parity), conv-encode +
      puncture (1/2, 2/3, 3/4, 5/6), frame interleaver, Gray QAM map
      (BPSK/QPSK/16/64),
      OFDM mod (NFFT 2048, CP 768), chirp+GUARD+sync prefix. Bit-exact on the
      deterministic stages; float-tolerant on the OFDM samples vs golden TX.
- [x] **1.3 C RX path** in `CCyrinx`, ported against the retained reference
      implementations:
      chirp sync, LS channel est, iterative pilot phase-slope + CPE tracking,
      per-bin noise var, max-log LLR, and payload-independent known-pilot EVM²
      weighting both per symbol and across local frequency, then deinterleave,
      soft Viterbi, CRC32 ordered-stream verify. The current contract includes
      edge-replicated pilot-local smoothing, interpolation through the final
      partial comb, a fixed SNR floor, and defined non-finite handling.
- [ ] **1.4 C adaptive sounder + MCS ladder:** Schroeder EDC delay spread,
      per-bin SNR, clock-offset est; MCS ladder; per-bin bit-loading
      (calibrated thresholds, QPSK floor); non-coherent MFSK floor tier.
      (Subsumes issue #13; lays groundwork for #6.)
- [x] **1.4b Actionable repositioning-guidance API.** A consumer-facing surface
      that turns sounder metrics into a small, stable enum of human-actionable
      hints (with the measured evidence behind each), so an implementing app can
      coach its user to a better link. Maps measured pathology → advice, e.g.:
      - low broadband SNR + low RX peak → **move the devices closer**;
      - strong late taps / delay spread ≫ CP → **hard/reflective surface nearby:
        place the phone on something soft, or away from walls**;
      - high-frequency roll-off / directional loss → **point the phone's bottom
        edge (mic/speaker ports) toward the other device**;
      - RX peak clipping → **too loud / too close: lower volume or back off**;
      - ultrasonic uplink phase-incoherence → **switch to audible mode**.
      Output: `{hint enum, severity, human string, evidence (the metric + value)}`,
      localizable, rendered however the app chooses. Pure function of a sounding
      result — no audio I/O — so it's unit-testable against recorded captures.
- [x] **1.5a Two-mic MRC, automatic receiver selection, and pilot-local LLR
      weighting in C; Pixel stereo OTA validation.** Deterministic mono and MRC
      challenge fixtures, public receiver-contract tests, and the prospective
      2026-07-17 Pixel campaign cover the canonical implementation.
- [ ] **1.5b iOS stereo capture and cross-device OTA breadth.** Validate the
      same canonical C receiver through the iOS data source and retain route
      provenance; the Pixel result does not close this platform-specific work.
- [x] **1.6 Apple acceleration backend.** Optional `vDSP`/Accelerate FFT behind
      a compile-time switch in the C core (or a Swift-provided FFT callback),
      validated to decode the same golden vectors as the KISS path. This is the
      *Apple optimization* — correctness comes from the portable path.
- [ ] **1.7 Bindings + retire forks — PARTIAL.** The thin Swift wrapper over the
      C bulk PHY exists in `Sources/Cyrinx`. Android still needs a **JNI**
      binding to the same core; re-point the HIL app and retire Kotlin
      `BulkDemod` as shipping DSP, retaining only intentional validation
      fixtures.
- [ ] **1.8 Transport integration** — wire the wideband PHY into the Cyrinx
      transport API as a bulk profile/gear (G3 turbo-bulk): real-time streaming
      buffers and closed-loop rate adaptation driven by the sounder. Closes the
      "not-yet-integrated / not-real-time / not-rate-adaptive" gaps the README
      discloses.
- [x] **1.9 Quarantine the optional crypto prototype.** Source inspection found
      that the legacy HIL envelope uses X25519 plus a custom SHA-256-derived
      XOR/HMAC construction, not a standard authenticated-encryption mode. It
      is off by default, unauthenticated, lacks receive-side replay protection,
      and is not integrated with bulk transport. The maintained deliverable is
      an implementation/cost record and explicit non-security boundary.
      Standard construction selection, identity binding, replay semantics,
      cross-language vectors, and review are separate security work.
- [ ] **1.10 OTA re-validation, library-native — PARTIAL.** The prospective
      Mac-to-Pixel campaign through the C core exceeds the old headline; the
      reverse direction, iPhone path, Android JNI path, and crypto-on
      measurements remain. Update all publication surfaces only with explicitly
      classed and reproducibly bound results.

## Phase 2 — Open bugs & research (parallel; not all gate v1.0)

- [ ] **2.1** Fix BPSK 1-bit per-bin loading bug (issue #4): instrument
      fine-sync window placement on BPSK vs QPSK frames. Non-blocking for v1.0
      (worked around by QPSK floor); fixing it unlocks the bottom loading rung.
- [ ] **2.2** Spread-spectrum / D-CSS floor tier below OFDM-BPSK (issue #6).
- [ ] **2.3** Challenged-environment goodput research (issue #5) — ongoing;
      feeds the paper, does not gate the release.
- [ ] **2.4** Fixed 3 ft throughput campaign. Re-sound and re-calibrate at the
      measured distance, then run a prospective order-balanced campaign. Do not
      extrapolate the near-field Cyrinx 2.0 profile or level settings.
- [ ] **2.5** Integrated ultrasonic asymmetric mode. Existing measurements are
      partial feasibility evidence (including transducer-limited asymmetry),
      not an end-to-end inaudible mode; add negotiation, fallback, and held-out
      OTA robustness before making a product claim.
- [ ] **2.6** Pleasant audible low-rate mode. Explore benign or musical symbol
      maps and psychoacoustic masking with measured annoyance and goodput; this
      remains a future mode, not a property of the wideband maximum-rate PHY.

## Phase 3 — Whitepaper update + arXiv (issue #8)

- [x] **3.1** Fold in post-3df0d1b findings: geometry sweep (contact 36.6 vs
      reverberant-desk 0 kbps + the 35.8 ms delay-spread mechanism),
      contact-vs-cloth (chassis contact worse: 10.5 vs 22 dB SNR), adaptive
      sounder/ladder/MFSK-floor graceful degradation (36.5 → 14.7–16.7 → 0.27
      kbps), per-bin loading +10% (16.15 vs 14.74 kbps), two-mic MRC, and a
      reference to `NEGATIVE_FINDINGS.md`.
- [x] **3.2** Add the Cyrinx 2.0 comparable flagship, zero-gap campaign, receiver
      replay ablation, negative frame-length result, exact denominators, strict
      reliability-gate failures, and artifact hashes recorded above. Regenerate
      and inspect the paper. Completed in the separate 11-page follow-on so the
      1.0 paper remains an intact historical record.
- [x] **3.3** Add the **library-parity result** from PR 1.7 (the headline is
      now delivered by the published library, not only the Python harness).
- [x] **3.4** Threats-to-validity + reproducibility blocks final; soften any
      remaining over-broad claims (contact-range prototype framing).
- [ ] **3.5** arXiv prep: abstract, cs.NI/eess.SP category, author/affiliation
      (Primatech Paper Co LLC), endorsement check, license (CC BY 4.0 on
      arXiv). Post; link DOI/handle from site + README.

## Phase 4 — Companion website (cyrinx.org, Cloudflare Pages)

- [ ] **4.1** Scaffold a static site (astro/plain) in `~/dev/cyrinx/site` or a
      dedicated repo; Cloudflare Pages project bound to **cyrinx.org**
      (DNS + Pages; verify wrangler token scope first per CLAUDE.md).
- [ ] **4.2** Content: landing (what it is + headline result), the
      defect/diagnostic catalog as the narrative hook, paper PDF + arXiv link,
      API/usage docs, the reproducible harness, negative findings.
- [ ] **4.3** Full OpenGraph + favicon metas + SEO + JSON-LD (CLAUDE.md);
      validate rendering/layout/a11y/perf with Chrome DevTools MCP.
- [ ] **4.4** Wire `homepageUrl` + repo "About" to cyrinx.org.

## Phase 5 — Flip public + release

- [ ] **5.1** Final secret/PII sweep of full history; squash-free.
- [ ] **5.2** `gh repo edit --visibility public`; set description + homepage.
- [x] **5.3** Tag `v1.0.0`; GitHub Release with paper PDF + key artifacts;
      confirm SwiftPM-consumable (`.package(url:…)` smoke test from a scratch
      project).
- [ ] **5.4** Announce (links from david.weekly.org; optional social).

---

## Sequencing & parallelism

- Phase 0 lands on `main` immediately (no dependencies).
- Phase 1 is the long pole: 1.1 (contract) → 1.2/1.3 (TX/RX, overlap once 1.1
  lands) → 1.4/1.5 (sounder/MRC, overlap) → 1.6 (Apple accel) → 1.7 (bindings,
  retire forks) → 1.8 (transport) → 1.9 (optional crypto) → 1.10 (OTA parity).
- Phase 2 runs in parallel with Phase 1 (separate worktrees), gates nothing
  except where noted.
- Phase 3 (paper) trails **1.10** because it claims library parity.
- Phase 4 (site) can scaffold during Phase 1 but its headline copy trails
  **1.10**; publish after 3.4 so it can link arXiv.
- Phase 5 is the final gate.

## Downstream consumers (contracts to honor)

- **iOS/Android HIL apps** — kept in parity via shared golden vectors; the
  promotion in 1.2/1.3 should let the apps depend on the library instead of a
  forked `BulkDemod`.
- **`Examples/*`** — must keep building; add a bulk-PHY example in 1.6.
- **C ABI (`cyrinx.h`)** — stable status codes; any new bulk entry points are
  additive.
