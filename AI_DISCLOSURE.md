---
disclosure-default: ai-assisted
models-used:
  - gpt-5.5
  - claude-fable-5
  - claude-opus-4.8
  - gemini (version unrecorded; Antigravity 2.0 sessions)
providers:
  - OpenAI
  - Anthropic
  - Google
scope: |
  Fundamental research, DSP C core implementation, Swift library/test stubs, companion website, LaTeX whitepapers, and peer review validation were performed by AI agents under human direction. 
  The human operator provided the physical lab bench, device parameters, and final pull request review/approvals.
last-updated: 2026-09-06
---

# Cyrinx AI Agent Attribution & Disclosure

This document discloses the governance, authorship, and peer-review process for `cyrinx` as of August 28, 2026. 

This repository utilizes a project-specific machine-readable YAML frontmatter metadata format (detailed in the header above) to declare AI agent involvement, modeled on the emerging [ai-content-disclosure](https://github.com/dweekly/ai-content-disclosure) convention.

Cyrinx is a research prototype developed through a **human-directed multi-agent collaboration**. The fundamental research, DSP C core, Swift bindings, tests, companion website, and whitepapers were designed and written by a sequence of artificial intelligence agents under human direction. 

The goal of this disclosure is to ensure scientific transparency, avoid misrepresentation of the human operator (David Weekly) as a signals expert, and document how multi-agent engineering can achieve high-rate acoustic goodput through rigorous peer review and empirical validation.

---

## 1. Collaboration Model & Author Roles

The division of labor between the human operator and the AI agents is defined as follows:

### The Human Operator (David Weekly)
- **Architectural Constraints**: Specified the operational bands (ultrasonic vs. audible), target platforms (macOS, iOS, Android), goodput metric definitions, and safety limits.
- **Physical Test Bench**: Provided the physical room and environment, performed initial device placements, configured system gains, authorized audio emissions, and accepted final results.
- **Decision Authority**: Reviewed and merged all Pull Requests and gave final approvals on design tradeoffs.

### The AI Coding & Peer Review Agents
- **Design & Coding**: Formulated the mathematical specifications (OFDM, Gray QAM, convolutional FEC, Viterbi decoders, MRC combining, known-pilot EVM² weighting) and implemented them in Swift, C, and Kotlin.
- **Automated Execution & Verification**: Orchestrated test execution on hardware and emulators via automated `adb` control, `devicectl` commands, and CoreAudio callbacks, generating golden vectors, unit tests, and validation scripts to enforce mathematical correctness across platforms.
- **Critique & Peer Review**: Checked code structure, audited formatting limits, diagnosed timing/sync issues, and analyzed failures (e.g. delay spread vs. cyclic prefix length).

---

## 2. Component-Level Agent Attribution

Table 1 logs the contributions of each model era to the repository, verified through operators' session records and Git commit histories. Attribution evidence (session records, `Co-Authored-By` trailers) verifies *who wrote what*; the performance figures follow the separate evidence classification recorded in the repository's main documentation ([docs/PUBLICATION.md](docs/PUBLICATION.md)) — aggregate ledgers and hashes are tracked in-repo, but not every figure is independently replayable from the repository alone.

### Table 1: Development Eras, Model attributions, and Verification Status

| Era / Date | Primary Agent | Primary Role / Component | Stated Performance / Stated Goal | Verification Status & Reality |
| :--- | :--- | :--- | :--- | :--- |
| **Feb–May 2026** | **GPT-5.5** (OpenAI) | Initial transport stack: gears, ARQ, room-tone notcher, DH crypto, Kotlin/Swift scaffolds. | Stated as "symmetrical 20+ kbps", "100% working bidirectional link". | **$<$0.3 kbps measured OTA**. Failed due to lack of physical loopback tests and optimistic self-assessment (session-record attribution). |
| **June 9–10, 2026** | **Claude Fable 5** (Anthropic) | Channel characterization, C wideband bulk PHY, diagnostic scripts, four-defect analysis. | Re-characterized constraints based on actual OTA captures. | **36.6 / 27.3 kbps goodput** verified on Pixel 7a over the air (verifiable via `Co-Authored-By` commit trailers). |
| **June 10–12, 2026** | **Claude Opus 4.8** (Anthropic) | Portable C port, golden-vector test rig, adaptive CP, mic selection, MFSK floor, two-mic MRC validation. | Decoupled DSP from hardware platform dependencies. | **38.400 kbps** payload-normalized console aggregate; 68 bps floor (verifiable via `Co-Authored-By` commit trailers). |
| **July 1–2, 2026** | **Claude Fable 5** (Anthropic) | C library port of MRC, RS-coded floor, adaptive loop, project website scaffolding. | Digital loopback validation. | Completed digital loopback validation; OTA deferred to next step (verifiable via `Co-Authored-By` commit trailers). |
| **July 6–8, 2026** | **Claude Fable 5** (Anthropic) | Diversity stack OTA re-validation; floor erasures, floor combining, MRC-aware sounding. | Addressed defects invisible to digital loopbacks. | **138 bps** RS floor; **11.366 kbps** MRC-carried over the air (verifiable via `Co-Authored-By` commit trailers). |
| **July 20, 2026** | **Antigravity 2.0** / **Gemini** (Google) | Cyrinx 3.0 versioned C ABI base, profile registry, and batch capture contract experiments (PR #71, commit tagged `archive/c3-02-05-batch-demod-rejected`). | Define prefix-compatible ABI boundaries and batch decode/encode layouts. | **Branch-only; never merged.** Digital/in-memory loopback only (session-record attribution). The 2026-08-27 branch-tip review ([docs/reviews/C3_02_05_TIP_REVIEW_2026-08-27.md](docs/reviews/C3_02_05_TIP_REVIEW_2026-08-27.md)) found the work not promotable. The branch was deleted on 2026-09-06 and the tag is now the only reference that resolves; the work was reconstructed against the reviewed contracts and merged in PR #82. |

---

## 3. Agent-to-Agent Peer Review and Critique Process

To prevent sycophancy and "AI psychosis" (where an agent generates plausible-sounding but physically non-functional code), Cyrinx utilizes a multi-layered peer-review loop:

1. **The Semantic Reference Oracle**: The original Python harness (`modem.py`) acts as the mathematical baseline. The C library must match the Python output at a tiered tolerance.
2. **Deterministic Golden Vectors**: Pinned under `Tests/Fixtures/golden/` (including [golden_manifest.h](Tests/CGoldenVectors/golden_manifest.h)), these verify that any change to the C DSP core matches the expected bitstream (convolutional code, puncturing, QAM symbol maps) exactly.
3. **Automated Gates**: `.github/workflows/repository-claims.yml` runs the documentation claims gate (`./scripts/check-claims.sh`) on every pull request, rejecting prohibited over-claims outside explicitly historical contexts; `.github/workflows/chat-c3-28.yml` runs the chat-lane test suites. Repository-wide format (`./scripts/format-check.sh`, including vendored-source integrity), lint (`./scripts/lint.sh`, SwiftLint-baselined), and `swift test` run via `./scripts/check.sh`.
4. **Agent Self-Critique**: Agents are instructed to proactively log all negative findings and structural failures in [NEGATIVE_FINDINGS.md](docs/NEGATIVE_FINDINGS.md) and [docs/releases/](docs/releases/).
