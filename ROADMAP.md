# Cyrinx Roadmap

Fresh as of 2026-07-17. The single forward-looking plan, stack-ranked. What has
already been built and measured is in [CHANGELOG.md](CHANGELOG.md); the current
validated state is summarized in [README.md](README.md). Deeper context per
track: [docs/PUBLICATION.md](docs/PUBLICATION.md) (publication effort),
[docs/EXPERIMENTS.md](docs/EXPERIMENTS.md) (measurement campaign),
[docs/A1_AUTO_MRC.md](docs/A1_AUTO_MRC.md) (diversity execution record).

## Cyrinx 2.0 measured state

The portable C receiver now contains the two-microphone MRC path, the automatic
receiver selector, and payload-independent pilot-local LLR reliability
weighting. The 2026-07-17 Pixel 7a campaign exercised that receiver over the
air rather than only through synthetic or retained-capture tests.

The **comparable flagship class** retains the 12,000-sample inter-frame gap used
by the accepted benchmark. It measured **65.875457875 kbps mean goodput** on the
Mac-to-Pixel bench: 4,215/4,280 decoded blocks (98.4813%), all eight paired runs
won, and the exact sign-test result was p = 1/256. This is about 1.80x the
36.571 kbps result, not an almost-threefold claim. It also failed the strict
reliability gate against the 99.9667% baseline, so the result is a throughput
advance with a measured resilience cost, not a blanket replacement for the
baseline. Replaying those fresh captures through the frozen current and legacy
receivers isolates the receiver-side contribution: 4,215 versus 3,868 decoded
blocks, all eight runs improved, and no run regressed.

A separately labeled **zero-gap measurement class** reached a higher mean:
pilot spacing 64 and 96 OFDM symbols measured **69.651849660 kbps** (range
65.731–72.641; reported gross rate 68.636220472 kbps), with 6,129/6,760 blocks
(90.6657%). It won all eight pairs (p = 1/256), all 16 planned runs completed,
and it failed the strict reliability gate against the 4,509/4,520-block
(99.7566%) baseline. The value is 1.9045x, or 90.45% above, 36.571 kbps; it is
not directly comparable to the gap-preserving flagship. Separate zero-gap
campaigns also bounded the observed trade space: spacing 16 gave 69.110 kbps at
97.009%, spacing 32 gave 68.960 kbps at 93.311%, and the 128-symbol campaign
gave 66.102 kbps at 89.093%. These sessions do not isolate pilot spacing or
frame length causally; a randomized factorial remains future work.

## The gap that matters most

The old C-diversity gap is closed for this Pixel 7a near-field route: two-mic
MRC, automatic receiver selection, and pilot-local reliability weighting are
implemented in the canonical C codec and have prospective OTA evidence. The
remaining gap is **robust generalization**: the faster profiles lose blocks
relative to the conservative baseline, and one device, geometry, distance, and
room-noise condition do not establish a general operating policy. Python
remains the research oracle and independent measurement referee; Swift is a
thin C binding, while the Android Kotlin DSP remains a legacy implementation
to retire rather than a co-equal source of truth.

## Track A — Finish the robust library (first)

- **A1. Auto-MRC + mic selection in the live adaptive loop — DONE for the
  Pixel near-field route (2026-07-08); cross-device OTA breadth remains.** The
  cross-compat spike (`scratch/hw20k/xcompat_validate.py`) proved clib frames
  decode through the Python reference RX (mono + MRC, full MCS/CP grid);
  `adaptive.py` now escalates clib-first → two-mic MRC with per-rep provenance,
  covered by `adaptive.py selftest`. Full plan: [docs/A1_AUTO_MRC.md](docs/A1_AUTO_MRC.md).
  The 2026-07-08 live-loop campaign completed the Pixel OTA step (46.915 kbps clean
  and 75/75 blocks MRC-rescued in the reverberant cell). The 2026-07-17
  campaign separately validated the new pilot-only selector and local LLR
  receiver. An iPhone and additional-Mac replication still belong in A5.
- **A2. Port diversity and reliability into the C codec — DONE, including
  Pixel OTA evidence (2026-07-17).**
  `cyrinx_bulk_demodulate2` (per-subcarrier two-mic MRC, ports the validated
  modem.py math; EVM parity to 4 decimals on identical captures across the
  MCS/CP grid), Swift `BulkPHY.decode(_:combining:)`, and a committed golden
  MRC rescue fixture (`qam16_r34_mrc`: notched mic0 fails alone, MRC decodes
  byte-exact; pinned on both KISS and vDSP FFT backends). The C receiver also
  owns automatic selection and pilot-local LLR weighting, with edge and
  non-finite behavior contract-tested. CP/NFFT were already caller-selectable
  (verified at 4096/3072). The adaptive loop's escalation uses the library MRC
  end-to-end.
- **A3. MFSK floor: Reed–Solomon over GF(16) — DONE, including OTA
  re-validation (2026-07-08).** RS(15,11) + detector-confidence erasures
  replaced 3× repetition (`rs16.py`, `mfsk.py`): ×2.06 rate at the same symbol
  duration (32 B frames: 68 → 138 bps) and measurably *more* robust at the
  low-SNR edge (rep3 5/20 vs RS 18/20 at −3 dB, 40 ms reverb). After trying
  errors-only decoding before consuming the erasure budget, the live shadowed
  cells recovered 9/9 frames at 138 bps across the retained runs.
- **A3b. Floor rate scaling (new — the floor is still slow by design, but not
  this slow).** Two bench-validatable levers on top of A3: (i) *adaptive
  symbol duration* — T_SYM is fixed at 120 ms, sized for the worst measured
  spread (~110 ms), but the sounder already measures ds15 per cell; scaling
  T_SYM to the actual spread roughly doubles rate at 40 ms spreads. (ii)
  *denser tone grid* — the 75 ms integration window resolves ~15 Hz; current
  tone spacing is ~102 Hz, so 2× the blocks (−3 dB per tone, covered by the
  measured edge margin) is plausible. Combined: a ~0.5 kbps floor without
  giving up the non-coherent/ISI-immune property. *Bench required to validate
  the margins.*
- **A4. Convert the 64-QAM throughput win into a reliability-qualified policy.**
  The 2026-07-17 campaign establishes that 64-QAM can raise measured near-field
  goodput, but both headline campaigns failed the strict block-success gate.
  Expand the calibration across noise cycles, levels, offsets, and held-
  out devices; choose pilot spacing and frame length against a declared
  goodput-versus-resilience objective rather than EVM alone. *Bench.*
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
- **A7. Maximize throughput at a fixed 3 ft separation (later campaign).**
  Keep this separate from the near-field Cyrinx 2.0 claim. Re-characterize
  output level, both speaker→mic paths, delay spread, route/source behavior,
  and room tone at a measured 3 ft; then run a held-out, order-balanced MCS/CP
  campaign with the same all-slots goodput denominator. Report both the best
  route-specific result and robustness across face-up/face-down and modest
  lateral offsets. Near-field settings and levels do not transfer by default.

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

## Track C — The website (built; publishing this update is user-gated)

[cyrinx.org](https://cyrinx.org) (registered; Cloudflare Pages). **Built** in
`site/`: static single page (no framework, no build step, no third-party
requests; self-hosted IBM Plex). The hero synthesizes a deterministic
control-profile geometry illustration (chirp → guard → random-QPSK OFDM),
renders its spectrogram live, and plays it via WebAudio on click; it is not an
encoded payload frame. The page also provides an interactive EVM
constellation illustrating the historical failed 64-QAM profile;
graceful-degradation ladder;
measured-results table; prior-art section; whitepaper PDF. Full
OpenGraph/JSON-LD/favicon/sitemap/robots/_headers; desktop + mobile rendering
and console were validated for the original site. The repository and v1.0.0
release are public. The Cyrinx 2.0 source update still needs visual QA and an
authenticated deployment; editing `site/` does not change the live site. See
`site/README.md`.

## Track D — Publication finalization (user-gated)

- Fold the 2026-07-17 Cyrinx 2.0 implementation and measurements into the
  whitepaper, preserving the gap-class and reliability caveats. The revised
  website is staged locally but not deployed. **The paper does not yet include
  these Pixel results.**
- arXiv submission (cs.NI / eess.SP) — needs the user (account + endorsement).
  The existing 28 pp whitepaper predates the Cyrinx 2.0 Pixel evidence update.
- Deploy the Cyrinx 2.0 site update after visual review and authenticated
  Cloudflare access.

## Tech debt / hygiene
- **Converge the bulk PHY on the C core.** The Android and iOS HIL receivers
  still duplicate modem DSP and have already drifted in pilot weighting,
  edge-bin noise smoothing, Viterbi tie handling, supported MCS profiles, and
  headline attribution. Add a coarse-grained capture/decode C API, use it from
  Swift and Android JNI, keep Python as a frozen research oracle plus an
  independent measurement referee, and run the same Cyrinx 1.x/2.0/guarded-MRC
  fixtures through every binding before retiring the mobile DSP forks.
- Golden vectors do not yet cover the sounder or MFSK floor.
- `scratch/hw20k/NOTES.md`: keep the per-script index current as spikes land.
- Unit coverage: keep `adaptive.py` / `sounder.py` / `mfsk.py` / `freqresp.py`
  / `env_sweep.py` selftests and `xcompat_validate.py` green.
- Keep CHANGELOG.md current as milestones land.

## Further research directions (2026-07-08 review, stack-ranked with critique)

Candidate directions assessed against the measured record, deduped against
the tracks above, ranked by value-to-effort. The paper's "Building on this
work" subsection carries the reader-facing version; this is the working
assessment of which are worth *our* bench time and why.

**Top tier — do these; the evidence already argues for them:**

- **Real-time streaming receiver.** The single biggest gap between "PHY
  demonstration" and "transport substrate," and honestly bounded: the buffer
  plumbing is easy, the real work is replacing per-capture matched filtering
  with a continuous sliding-window sync correlator that holds timing lock
  across stream boundaries. The on-device decoder already runs ~20× real
  time, so the CPU budget exists. Prerequisite for almost everything below
  (pairs with PUBLICATION 1.8).
- **Device/geometry matrix + open channel corpus** (one campaign, two
  outputs). The paper's own threats-to-validity says breadth is the largest
  remaining gap — this is boring, decisive, and mostly automated already
  (`env_sweep.py`, `freqresp.py`). Publishing the raw captures + block maps
  + negative findings as a corpus costs little extra and is the cheapest way
  for the project to matter to people without an audio bench. Do them
  together; a matrix without the corpus wastes the labor.
- **Generalized multi-mic diversity.** The measured 82× rescue came from
  exactly this class of work, on the *first* mic pair we tried; iPhone
  stereo capture is already scoped (PUBLICATION 1.5) and laptop arrays are
  unexplored. Highest measured-ROI-per-effort on the list. Per-band
  (frequency-selective) mic weighting is the natural refinement — the MRC
  math already computes the per-bin weights.
- **Withheld physical benchmark.** Conceptually the most distinctive
  extension, and cheap to stand up given §Benchmark already defines the
  scoring. Honest caveats: it is one task (n=1 generalization), someone must
  maintain the withheld set, and "claims-integrity audit" needs a rubric
  before it is a score rather than an anecdote. Worth doing *because* those
  objections are addressable in a one-page protocol.

**Middle tier — real but sequenced or scoped:**

- **Closed adaptive link layer / session MAC.** The right north-star metric
  (reliable transfer across *changing* placements, not peak goodput) and the
  natural consolidation of sounder + MCS/CP + mic/MRC + floor + block ARQ.
  Sequenced behind the streaming receiver — a session layer over a batch
  decoder would be rework. Note: the proposed "hybrid coherent/non-coherent
  modem with smooth transitions" is this same item wearing a different hat —
  the adaptive loop already *is* the hybrid prototype; what's missing is the
  session machinery, so it's folded in here rather than tracked separately.
- **Effective-SINR bit loading.** The insight is right — the measured
  PSD-vs-EVM divergence proves raw SNR mispredicts which bins survive
  transducer phase noise — but the measured upside of per-bin loading was
  +10%, so this is a refinement, not a headline. Rides along with A4's
  calibration sweep rather than deserving its own campaign.
- **2×2 acoustic MIMO** (Track B above). The glamour item, kept honest: the
  theoretical ceiling is ~2×, the conditioning of H is unproven, and the
  effort is multi-session research. The measured speaker/mic decorrelation
  says it's *plausible*, not that it's *cheap*. Stays on the roadmap as the
  frontier, not the next step.

**Demand-driven — wait for a forcing use case:**

- **Ultrasonic asymmetric protocol.** Physics already measured: fast
  inaudible downlink is real (~9 kbps), while the return path must be
  non-coherent and slow. This is partial feasibility evidence, not an
  integrated Cyrinx mode; protocol integration, capability negotiation, and
  end-to-end robustness remain future work.
- **Motion-robust mode.** Before reaching for OTFS-style delay–Doppler
  machinery, measure whether motion actually matters at contact range — the
  use case is a phone *resting* on a laptop, and casual motion mostly breaks
  placement (a repositioning-guidance problem, already shipped) rather than
  Doppler budget. First deliverable: a cheap motion-sensitivity
  characterization, not a new modem.
- **Secure pairing product layer.** Product engineering, not research: the
  envelope exists, the cost table exists (~4.6 s handshake at the 138 bps
  floor is the honest pain point). Build it when an application exists;
  research-wise there is nothing left to learn here except UX.

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
| Pilot-driven reliability weighting | **done in C RX** (local LLR; Pixel OTA); sounder policy separate |
| Dynamic CP from measured PDP | **done** (adaptive layer, 2026-06-12) |
| Block-level ARQ / incremental parity | open |
| Two-mic MRC | **done in canonical C RX and Pixel OTA exercised** (A2, 2026-07-17) |
| True 2×2 MIMO | open (Track B) |
| Continuous environment sensing | partial (room-tone check in sounder) |
| Asymmetric link profiles | partial (measured asymmetric bands; negotiation open) |
