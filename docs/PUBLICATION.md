# Cyrinx Publication Plan

Fresh as of 2026-06-10. The sequenced, multi-PR plan to take Cyrinx from a
private research prototype to a **public, Apache-2.0 library that itself
delivers the measured headline result**, with a companion website
(**cyrinx.org**, Cloudflare Pages) and an **arXiv** paper.

Decisions locked (2026-06-10):

- **Library scope:** port the wideband bulk PHY into a **portable C core**
  (`CCyrinx`) so the *library* (not just the Python harness) delivers the
  36.6 / 27.3 kbps headline, on any platform.
- **PHY home:** the canonical DSP lives in **portable C**. Swift/vDSP is an
  **Apple acceleration backend**, not the source of truth; Android binds the
  same C via JNI. (Resolves the PR 1.1 architecture question below.)
- **License:** Apache-2.0 (permissive + patent grant). Any vendored FFT must be
  license-compatible (KISS FFT / pocketfft are BSD-3 → OK).
- **Website:** Cloudflare Pages on **cyrinx.org** (registered 2026-06-10).
- **Paper:** arXiv (cs.NI / eess.SP) + PDF on the site.

Convention: one entry per shippable stage; ~~strikethrough~~ as each merges.
Each stage is a tracking PR opened with its first commit, in a worktree under
`~/dev/cyrinx-<stage>/`.

## Status (2026-06-10)

**Merged to `main` (73 tests green):** Phase 0 hygiene (#23); 1.1 golden-vector
contract (#24); **1.2 + 1.3 the full portable-C bulk PHY codec — TX *and* RX**
(#26, validated bit-exact / float-tol against the golden vectors, RX decodes the
multipath `rx_wave` to the exact payload); 1.7-core Swift `BulkPHY` binding (#26,
pure-Swift round trips); 1.4b repositioning-guidance API (#28). The core
technical risk — porting the validated 36 kbps modem into a portable C library —
is **done**.

**Automatable, remaining:** 1.4 adaptive sounder + MCS ladder + MFSK floor
(feeds 1.4b real metrics); 1.6 vDSP FFT backend; 1.7-rest (retire the HIL
`BulkDemod` forks); 1.8 transport integration; 1.9 optional crypto + tradeoff
doc; Phase 2 (#4 BPSK, #6 spread-spectrum, #5 research); Phase 3 paper draft;
Phase 4 website build.

**1.10 library-native OTA — DEMONSTRATED (2026-06-10):** with the Pixel 7a on
the bench, the actual library C codec (via `libcyrinxbulk.dylib` + `clib.py`)
encoded on the Mac, played over the speaker, the Pixel recorded, and the C codec
decoded the capture — **12.8 kbps QPSK r1/2, byte-verified, 25/25 blocks, 3/3
frames**. Higher rates are SNR-limited at the current coupling (16-QAM decoded
partially), not codec-limited. The library itself now has a measured OTA result;
matching the historical 36 kbps needs better physical coupling + the adaptive
sounder (1.4) to pick the MCS. (`scratch/hw20k/ota_clib.py`.)

**User-gated (cannot be automated):** Phase 3 arXiv *submission* (needs the
arXiv account + endorsement); Phase 4 Cloudflare *deploy* (needs wrangler auth on
cyrinx.org); Phase 5 make-repo-public + tag + announce; a clean-coupling OTA pass
to reproduce the full 36 kbps headline through the library.

Day-to-day decisions, measurements, dead ends, and rationale are logged in
[publication-journal.md](publication-journal.md) as we go — raw material for the
final paper update (Phase 3). Durable "don't do that" results still graduate to
[NEGATIVE_FINDINGS.md](NEGATIVE_FINDINGS.md).

---

## Critical-path insight

The bulk PHY already exists, working, in **three independent implementations**:
Swift (`Apps/HIL/iOS/App/BulkDemod.swift`, 700 lines — decodes Mac→iPhone,
encodes iPhone→Mac), Kotlin (`BulkDemod.kt`, 558 lines), and the Python
reference (`scratch/hw20k/modem.py`). `DetRng` (splitmix64) is already bit-exact
across all three. **The C core is therefore a port against three living
reference oracles, not a green-field DSP design** — every stage can be diffed
against a known-good output. That is the single biggest de-risking fact here.

The end-state: **one C DSP core** in `CCyrinx`; the existing Swift/Kotlin
`BulkDemod` files become validation oracles and are then retired in favor of an
Apple Swift wrapper and an Android JNI binding to the same core.

### Architecture (resolved 2026-06-10): portable C is canonical

The DSP lives in `CCyrinx`. Swift/vDSP is an Apple **acceleration backend**
(selectable FFT), Android binds via **JNI**, Python validates via **ctypes**.
Consequences baked into Phase 1:

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

- [ ] **1.1 Golden-vector harness + FFT choice.** Python emits canonical
      fixtures for fixed (config, seed, payload): encoded bits, interleaved
      bits, QAM symbols, OFDM time samples, full TX waveform, and a captured RX
      → decoded payload. Land as fixtures + a C test rig (and Swift test target)
      that loads them at tiered tolerance. Vendor the FFT (KISS FFT default).
      **This is the contract; it lands before any C DSP.**
- [ ] **1.2 C TX path** in `CCyrinx`: `DetRng` (verify parity), conv-encode +
      puncture (1/2, 3/4), frame interleaver, Gray QAM map (BPSK/QPSK/16/64),
      OFDM mod (NFFT 2048, CP 768), chirp+GUARD+sync prefix. Bit-exact on the
      deterministic stages; float-tolerant on the OFDM samples vs golden TX.
- [ ] **1.3 C RX path** in `CCyrinx`, ported against `BulkDemod.swift`/`.kt`:
      chirp sync, LS channel est, iterative pilot phase-slope + CPE tracking,
      per-bin noise var, max-log LLR, per-symbol EVM² weighting, deinterleave,
      soft Viterbi, CRC32 ordered-stream verify. Byte-exact decode of golden
      captures + the three reference oracles agree.
- [ ] **1.4 C adaptive sounder + MCS ladder:** Schroeder EDC delay spread,
      per-bin SNR, clock-offset est; MCS ladder; per-bin bit-loading
      (calibrated thresholds, QPSK floor); non-coherent MFSK floor tier.
      (Subsumes issue #13; lays groundwork for #6.)
- [ ] **1.4b Actionable repositioning-guidance API.** A consumer-facing surface
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
- [ ] **1.5 Two-mic MRC** in C + iOS stereo capture; **validate OTA**
      (closes issue #7). Needs stereo receiver on iOS (data source `.stereo`).
- [ ] **1.6 Apple acceleration backend.** Optional `vDSP`/Accelerate FFT behind
      a compile-time switch in the C core (or a Swift-provided FFT callback),
      validated to decode the same golden vectors as the KISS path. This is the
      *Apple optimization* — correctness comes from the portable path.
- [ ] **1.7 Bindings + retire forks.** Thin Swift wrapper over the C bulk PHY in
      `Sources/Cyrinx`; Android **JNI** binding to the same core. Re-point the
      HIL apps and `Examples/*` at the library; retire the `BulkDemod`
      Swift/Kotlin forks (kept only as golden-vector oracles in tests).
- [ ] **1.8 Transport integration** — wire the wideband PHY into the Cyrinx
      transport API as a bulk profile/gear (G3 turbo-bulk): real-time streaming
      buffers and closed-loop rate adaptation driven by the sounder. Closes the
      "not-yet-integrated / not-real-time / not-rate-adaptive" gaps the README
      discloses.
- [ ] **1.9 Optional crypto envelope (decoupled layer).** The X25519/CTR/HMAC
      envelope is an **opt-in layer above the PHY, OFF by default**, never a
      hard dependency of bulk transport. Deliverable includes a documented
      **cost/security tradeoff table**: fixed handshake bytes + per-frame
      nonce/tag overhead expressed as a *percentage of goodput at each MCS
      tier*, so an implementing app can choose plaintext-bulk vs
      authenticated-trickle per channel quality and its own threat model. The
      overhead is near-free at 36 kbps and can dominate at the 267 bps MFSK
      floor — make that explicit. (Renumbers later stages.)
- [ ] **1.10 OTA re-validation, library-native.** End-to-end through the
      *library C core* (not Python): Mac↔iPhone (HIL, vDSP backend) and
      Mac↔Pixel (JNI). Demonstrate the library itself hits the headline; update
      README/docs measured numbers and add a library-native repro recipe.
      Validate both plaintext-bulk and crypto-on paths so the 1.9 tradeoff
      table carries *measured* (not just analytic) overhead.

## Phase 2 — Open bugs & research (parallel; not all gate v1.0)

- [ ] **2.1** Fix BPSK 1-bit per-bin loading bug (issue #4): instrument
      fine-sync window placement on BPSK vs QPSK frames. Non-blocking for v1.0
      (worked around by QPSK floor); fixing it unlocks the bottom loading rung.
- [ ] **2.2** Spread-spectrum / D-CSS floor tier below OFDM-BPSK (issue #6).
- [ ] **2.3** Challenged-environment goodput research (issue #5) — ongoing;
      feeds the paper, does not gate the release.

## Phase 3 — Whitepaper update + arXiv (issue #8)

- [ ] **3.1** Fold in post-3df0d1b findings: geometry sweep (contact 36.6 vs
      reverberant-desk 0 kbps + the 35.8 ms delay-spread mechanism),
      contact-vs-cloth (chassis contact worse: 10.5 vs 22 dB SNR), adaptive
      sounder/ladder/MFSK-floor graceful degradation (36.5 → 14.7–16.7 → 0.27
      kbps), per-bin loading +10% (16.15 vs 14.74 kbps), two-mic MRC, and a
      reference to `NEGATIVE_FINDINGS.md`.
- [ ] **3.2** Add the **library-parity result** from PR 1.7 (the headline is
      now delivered by the published library, not only the Python harness).
- [ ] **3.3** Threats-to-validity + reproducibility blocks final; soften any
      remaining over-broad claims (contact-range prototype framing).
- [ ] **3.4** arXiv prep: abstract, cs.NI/eess.SP category, author/affiliation
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
- [ ] **5.3** Tag `v1.0.0`; GitHub Release with paper PDF + key artifacts;
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
