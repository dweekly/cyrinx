# Cyrinx Roadmap

Fresh as of 2026-07-01. The single forward-looking plan, stack-ranked. What has
already been built and measured is in [CHANGELOG.md](CHANGELOG.md); the current
validated state is summarized in [README.md](README.md). Deeper context per
track: [docs/PUBLICATION.md](docs/PUBLICATION.md) (publication effort),
[docs/EXPERIMENTS.md](docs/EXPERIMENTS.md) (measurement campaign),
[docs/A1_AUTO_MRC.md](docs/A1_AUTO_MRC.md) (next task's execution plan).

## The gap that matters most

**The robustness/diversity wins (mic selection, two-mic MRC, adaptive CP, MFSK
floor) live in the Python reference and bench loop, NOT in the shippable C
library.** `cyrinx_bulk` is still single-mic, fixed-CP, so a consumer of the
library does not get the graceful-degradation behavior the whitepaper describes.
Closing that gap is priority #1 — otherwise the paper claims capabilities the
shipped library lacks.

## Track A — Finish the robust library (first)

- **A1. Auto-MRC + mic selection in the live adaptive loop.** Wire decode-based
  mic selection + two-mic MRC into `adaptive.py`'s coherent decode (clib-first,
  MRC escalation). Full plan: [docs/A1_AUTO_MRC.md](docs/A1_AUTO_MRC.md).
  Steps 1–3 and 5 (cross-compat spike, escalation wiring, offline selftest,
  docs) are **digital-only — no audio hardware, no sound emitted**. Step 4 (OTA
  re-validation across the orientation set) needs a bench: the Pixel 7a bench
  reproduces the published cells; alternatively the iPhone can transmit (MRC is
  Mac-side) — see A5.
- **A2. Port diversity into the C codec.** 2-mic input + per-subcarrier MRC +
  caller-selectable CP/NFFT in `cyrinx_bulk`, so the *shipped* library degrades
  gracefully. Extend golden vectors for the new configs. *Larger.*
- **A3. MFSK floor: Reed–Solomon over GF(16)** replacing 3× repetition
  (68 → ~150 bps; nibble-aligned, rate-efficient). Digital-first; OTA-verifiable
  with either phone. *~half session.*
- **A4. Widen the EVM→MCS calibration** with more cells, especially a sub-0.1
  EVM cell to justify a 64-QAM tier (needs EVM ≲ 0.08; best measured 33% success
  at 0.097). *Bench.*
- **A5. Cross-hardware generality sweep (new).** Every published number was
  measured on one Mac (M4 MacBook Pro). Re-run gain staging + sounder + adaptive
  loop + MRC rescue on the M1 Max (different speaker layout and mic array) with
  the iPhone as the peer. Either replicates the graceful-degradation story on a
  second hardware pair (a real generality claim for the paper) or surfaces
  hardware-specific assumptions for NEGATIVE_FINDINGS.md. Requires an
  audio-tolerant environment and the iPhone.
- **A6. Raise the iPhone→Mac direction (16.87 kbps, speaker-limited).**
  Per-bin bit loading shaped to the measured iPhone speaker roll-off (usable
  coherent band ≈11 kHz). The weakest headline number and the clearest
  measurable win on iPhone-only hardware.

## Track B — 2×2 MIMO cooperative sounding (the frontier)

The capacity play: distinct per-speaker pilots → full 2×2 channel matrix →
spatial multiplexing (~2×) + transmit precoding. Reverberation becomes an asset
(rich multipath → well-conditioned H). *Multi-session research effort.*

- B1. Pilot scheme: FDM (interleaved tone combs per speaker) or CDM (chirp-up
  vs chirp-down / distinct ZC roots) so each mic resolves both speakers.
- B2. MIMO sounder: estimate H[f] (2 TX × 2 RX) per subcarrier; report condition
  number across geometries (the MacBook's wide-spaced mics should help).
- B3. Spatial multiplexing: 2 streams, ZF/MMSE separation; measure the gain.
- B4. Transmit precoding / null-steering: weight the speakers to fill RX nulls.

## Track C — The website (untouched)

[cyrinx.org](https://cyrinx.org) (registered; Cloudflare Pages). Static site:
encoding-waveform visualizations, multi-audience explainer (how acoustic data
encoding works; prior art — minimodem, ggwave, Quiet, Chirp/LISNR, BatNet; what
Cyrinx did differently incl. the robustness story). Full
OpenGraph/favicon/SEO/JSON-LD; validate rendering via the Chrome DevTools MCP.
*Big separate deliverable; nothing started.*

## Track D — Publication finalization (user-gated)

- arXiv submission (cs.NI / eess.SP) — needs the user (account + endorsement).
  The 22 pp whitepaper is complete and can go largely as-is.
- Cloudflare Pages deploy auth — needs the user (wrangler on cyrinx.org).
- Phase 5 public-repo flip (already Apache-2.0) — clear the swift-format
  violations (issue #25) first.

## Tech debt / hygiene

- **Issue #25:** ~900 pre-existing swift-format violations — pre-public blocker.
- Golden vectors don't yet cover the sounder / MFSK floor / MRC configs.
- `scratch/hw20k/NOTES.md`: keep the per-script index current as spikes land.
- Unit coverage: add an `adaptive.py selftest` for the auto-MRC path once built
  (A1 step 3); keep `sounder.py` / `mfsk.py` / `freqresp.py` / `env_sweep.py`
  selftests green.
- Keep CHANGELOG.md current as milestones land.

## Long-horizon / exploratory backlog

Unranked ideas retained from the original ROI-ranked roadmap, pruned of items
since shipped (dynamic CP, room-tone sensing, two-mic MRC — see CHANGELOG.md)
and updated against measured findings:

- **Transducer calibration database** — per-model inverse-EQ profiles applied on
  capability handshake. Still attractive; the measured iPhone-speaker roll-off
  (docs/IOS_HIL.md §4) is the first real profile candidate. Overlaps A6.
- **Multi-variable capability & protocol handshake** — extend the Stream 0
  capability frame to negotiate version, hardware signature, buffer limits.
- **LDPC / polar soft-decision FEC** — only when the K=7 convolutional code is
  the *measured* bottleneck (per the 2026-06-09 review: it is not yet).
- **96 kHz extended ultrasonic band** — partially explored
  (docs/ULTRASONIC_BAND.md): iPhone speaker is phase-incoherent ultrasonically;
  path remains open for other transducer pairs.
- **Ultrasonic ECDH handshake + encrypted payloads** — the X25519/CTR/HMAC
  envelope exists (experimental, unaudited); porting it onto the bulk PHY is
  costed in docs/CRYPTO_TRADEOFF.md.
- **Pleasant-sounding audible modes** — trade bitrate for benign/musical timbre
  (chord-tone symbol maps, psychoacoustic masking under a cover sound). A mode a
  user would tolerate aloud; cross-reference the crypto-overhead tradeoff at low
  rates.
- **IMU-assisted Doppler tracking** — feed-forward motion vectors into phase
  tracking for pick-the-phone-up robustness. High difficulty, modest ROI.
- **Haptics-to-accelerometer tapping modem** — silent through-desk seismic
  transport. Whimsical, educational, contact-limited.
- **"Acoustic LAN party" OFDMA** — multi-device star topology. Fun, niche,
  needs an acoustic MAC.

## Appendix — 2026-06-09 external review priorities (status)

Recorded verbatim in git history (`ROADMAP.md` prior to 2026-07-01). Status:

| Item | Status |
|---|---|
| Separate claims by subsystem in README | done |
| Promote bulk PHY out of scratch/ into first-class module | **done** (portable C port, PRs #24–#33) |
| Capture-replay tests in CI | open (partial: committed golden vectors incl. multipath `rx_wave`) |
| Formal HIL acceptance suite | open |
| Unified per-run metrics JSON | partial (`adaptive.py` JSONL rows) |
| Two-plane transport integration (control + bulk data) | open (PUBLICATION 1.8) |
| Document hardware limits plainly | done (ACOUSTIC_BULK_PHY §7) |
| Fast-acquisition superframe | open |
| Per-bin adaptive bit loading | partial (bench harness; not in C codec) |
| Adaptive coding rate; defer LDPC | ongoing policy |
| Pilot-driven reliability weighting | partial (EVM-probe sounder) |
| Dynamic CP from measured PDP | **done** (adaptive layer, 2026-06-12) |
| Block-level ARQ / incremental parity | open |
| Two-mic MRC | **done** (validated OTA 2026-06-12); C port open (A2) |
| True 2×2 MIMO | open (Track B) |
| Continuous environment sensing | partial (room-tone check in sounder) |
| Asymmetric link profiles | partial (measured asymmetric bands; negotiation open) |
