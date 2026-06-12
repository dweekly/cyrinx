# Cyrinx — Next-Session Plan

Fresh as of 2026-06-12. Companion to [PUBLICATION.md](PUBLICATION.md) (the
multi-PR publication effort) and [EXPERIMENTS.md](EXPERIMENTS.md) (the OTA
measurement campaign). This is the "where we are / what to do next" handoff.

## Where we are (snapshot)

Everything below is **on `main`, no open PRs.**

- **Library (portable C + Swift):** `cyrinx_bulk` codec — **39.3 kbps verified
  OTA** (library-native, exceeds the harness headline); `BulkPHY` Swift binding;
  KISS + vDSP FFT behind `cyrinx_fft`; committed golden vectors.
- **Adaptive layer (Python reference, `scratch/hw20k/`):** EVM-probe sounder with
  **decode-based mic selection + adaptive cyclic prefix**; **non-coherent MFSK
  floor** (`mfsk.py`); **two-mic MRC** (in `modem.demodulate_frame(rx2=...)`);
  repositioning guidance; crypto cost/security tradeoff.
- **Paper:** `docs/whitepaper/cyrinx-acoustic-link.tex`, **22 pp**, complete —
  headline + iPhone 2nd pair + library-native + the full §Robustness (graceful
  degradation, mic diversity, MRC, adaptive CP, MFSK floor).
- **Validated** OTA on MacBook Pro M4 ↔ Pixel 7a (and iPhone 17 PM for the 2nd
  pair). Graceful degradation demonstrated across orientations: 48 kbps (clean) →
  ~11 kbps (reverberant, rescued) → 5 kbps–68 bps (shadowed) → never zero.

## The gap that matters most

**The robustness/diversity wins live in the Python reference and the bench loop,
NOT in the shippable C library / Swift.** The C codec (`cyrinx_bulk`) is still
single-mic, fixed-CP. So a consumer linking against the library does *not* yet get
the graceful-degradation behavior the paper now describes. **Closing that gap is
the #1 priority** — otherwise the paper claims capabilities the shipped library
lacks.

---

## Track A — Finish the robust library (recommended first)

- **A1. Auto-MRC + mic-selection in the live coherent path.** The adaptive loop
  (`adaptive.py`) decodes coherent OFDM single-mic via `clib`; MRC only happens in
  `modem.py`. Wire decode-based mic selection + two-mic MRC into the loop's
  coherent decode (use `modem.demodulate_frame(rx2=...)` or port MRC to `clib`).
  Re-validate across the orientation set. *Bounded; makes the library do what the
  paper says.* **Start here.**
- **A2. Port diversity into the C codec.** Add 2-mic input + per-subcarrier MRC +
  caller-selectable CP/NFFT to `cyrinx_bulk` so the *shipped* library degrades
  gracefully. Extend golden vectors for the new configs. *Larger.*
- **A3. MFSK floor: Reed–Solomon over GF(16)** replacing 3× repetition (68 →
  ~150 bps; nibble-aligned, rate-efficient). *~half session.*
- **A4. Widen the EVM→MCS calibration** with more cells, especially a sub-0.1 EVM
  cell to justify adding a 64-QAM tier (currently omitted; 64-QAM needs
  EVM ≲ 0.08, measured 33% at 0.097). *Bench.*

## Track B — 2×2 MIMO cooperative sounding (the frontier)

The capacity play, and the user's big-vision idea: distinct **per-speaker pilots**
→ full 2×2 channel matrix → spatial multiplexing (~2×) + transmit precoding.
**Reverberation becomes an asset** (rich multipath → well-conditioned H).

- B1. Pilot scheme: FDM (interleaved tone combs per speaker) or CDM (chirp-up vs
  chirp-down / distinct ZC roots) so each mic resolves both speakers.
- B2. MIMO sounder: estimate H[f] (2 TX × 2 RX) per subcarrier; report the
  condition number across geometries (the MacBook's wide-spaced mics should give
  well-conditioned H).
- B3. Spatial multiplexing: 2 streams, ZF/MMSE separation; measure the throughput
  gain (target ~2×).
- B4. Transmit precoding / null-steering: weight the 2 speakers to fill the RX
  nulls. *Multi-session research effort.*

## Track C — The website (Phase 4, still untouched)

[cyrinx.org](https://cyrinx.org) (registered; Cloudflare Pages). Static site:
encoding-waveform visualizations, a multi-audience explainer (how acoustic data
encoding works; prior work — minimodem, ggwave, Quiet, Chirp/LISNR, BatNet; how
Cyrinx implemented best practices novelly, incl. the robustness/diversity story).
Full OpenGraph/favicon/SEO/JSON-LD per the project standard; validate rendering
with the Chrome DevTools MCP. *A big separate deliverable; nothing started yet.*

## Track D — Publication finalization (user-gated)

- arXiv submission (cs.NI / eess.SP) — needs the user.
- Cloudflare Pages deploy auth — needs the user.
- Phase 5 public-repo flip (license is already Apache-2.0) — clear the
  swift-format violations (issue #25) first.

## Tech debt / hygiene

- **Issue #25:** ~900 pre-existing swift-format violations — a pre-public blocker.
- Golden vectors don't yet cover the new sounder / MFSK floor / MRC configs.
- `scratch/hw20k/` has grown large; the spikes are kept by policy — a short NOTES
  index of which script does what would help.
- Extend unit coverage: `sounder.py selftest`, `mfsk.py`, `freqresp.py selftest`,
  `env_sweep.py selftest` exist; add the auto-MRC path once built.

## How to resume (bench quick-start)

- **Hardware:** Mac M4 ↔ Pixel 7a over USB (`adb`); iPhone 17 PM via `devicectl`.
- **venv:** `/Users/dew/dev/cyrinx/.venv`. **Rebuild dylib:** see the header of
  `scratch/hw20k/clib.py`.
- **Offline self-tests (no hardware):** `python scratch/hw20k/sounder.py selftest`,
  `python scratch/hw20k/mfsk.py`, `python scratch/hw20k/freqresp.py selftest`,
  `python scratch/hw20k/env_sweep.py selftest`.
- **Smoke (audio):** `python scratch/hw20k/harness.py smoke`.
- **Adaptive loop:** `python scratch/hw20k/adaptive.py <label>`.
- **Gain staging:** Mac output 100 %, Mac input ~22/100 (it clips above that),
  phone media max.
- **Good working geometry:** phone face-up/down on a soft cloth, charge-port/bottom
  edge toward the Mac speakers, over the function-key area (39–48 kbps). *Avoid*
  overhanging the mic over the keyboard well (reverberant) or shadowing the phone
  below the laptop stand (the speakers fire up, away from it).

## Recommendation

Start with **A1 (auto-MRC in the live path)** — it closes the paper-vs-shipped-
library gap with a clean, bounded win. Then **A2/A3** to harden the shipped C
library, then pick **B (MIMO frontier)** or **C (website)** by appetite. The
paper is in good shape and can go to arXiv (Track D) largely as-is once you're
ready.
