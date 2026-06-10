# Cyrinx Publication — Work Journal

Fresh as of 2026-06-10. A running, dated log of the work to publish Cyrinx
(portable-C bulk PHY → public Apache-2.0 library + cyrinx.org + arXiv paper).
This is **raw material for the Phase 3 paper update** — decisions, measurements,
surprises, dead ends, and the reasoning behind them, captured while fresh.

Cited from [PUBLICATION.md](PUBLICATION.md). Durable negative results graduate
to [NEGATIVE_FINDINGS.md](NEGATIVE_FINDINGS.md); positive measured results to the
subsystem docs. This journal is the narrative connective tissue between them.

Convention: newest entries at the top within each day; each entry tagged with
the plan stage (e.g. `[1.1]`, `[0.2]`) it belongs to.

---

## 2026-06-10

### `[plan]` Architecture & scope decisions locked
- Library scope: port the wideband bulk PHY into a **portable C core** so the
  library itself delivers the 36.6 / 27.3 kbps headline (not just the Python
  harness). Rationale: "broadly-useful library" — C is the single source of
  truth; Swift/vDSP is an Apple acceleration backend; Android binds via JNI.
- License **Apache-2.0**; website **cyrinx.org** (Cloudflare Pages, registered
  today); paper to **arXiv** (cs.NI / eess.SP) + PDF on site.
- Crypto (X25519/CTR/HMAC) is an **opt-in layer, OFF by default**, with a
  documented cost/security tradeoff table (overhead as % of goodput per MCS
  tier — near-free at 36 kbps, dominant at the 267 bps MFSK floor). Apps choose
  bandwidth-vs-security per observed channel quality.
- Added a consumer-facing **repositioning-guidance API** (plan 1.4b): turn
  sounder metrics into actionable user hints ("move closer", "soft surface",
  "point the bottom edge at the speaker", "too loud").
- De-risking fact: the bulk PHY already exists working in three reference
  implementations (Python `modem.py`, Swift `BulkDemod.swift`,
  Kotlin `BulkDemod.kt`) — the C core is a *validated port*, not a new design.

### `[0]` Phase 0 hygiene (branch `publication/groundwork`, PR pending)
- Added `LICENSE` (canonical Apache-2.0, fetched from apache.org) + `NOTICE`
  (Primatech Paper Co LLC). README gains a License section.
- Issue board mapped: label `publication-v1`; existing #4–#8 cross-referenced to
  plan stages; new #9–#22 created for Phase 0, PRs 1.1–1.10, paper, site, release.
- Junk scrub: removed compiled `TestFFT.class`; `.gitignore` now excludes
  `*.class/*.dex/*.o/*.a`.
- Kept (did **not** delete) the FFT/OFDM parity spikes — they're material for
  the PR 1.1 FFT-parity work. Organized the four FFT spikes under `scratch/fft/`
  with a README (vDSP vs Cooley-Tukey vs Java bit-reversal references). These
  pin the sign/scaling/bit-reversal conventions the portable-C FFT must match.
- Added `CONTRIBUTING.md`, `SECURITY.md` (documents the opt-in/OFF-by-default
  crypto stance + acoustic-broadcast threat note), and `.github/` issue + PR
  templates (PR template enforces measured-result + golden-vector checkboxes).
- Deferred to follow-ups (kept #9 open): per-file SPDX header sweep (scripted,
  low-value churn now); stale branch/worktree pruning; full-history secret/PII
  sweep happens at the Phase 5 public-flip gate.

### `[1.1]` Golden-vector contract — next
- (entries to follow as work lands)
