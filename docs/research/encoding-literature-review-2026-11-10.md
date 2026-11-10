# Where Cyrinx should lean in: encoding, modulation, equalization, sync and FEC, read against the literature

> **Provenance.** Written by an AI research agent on 2026-11-10 and committed as received. Its citations and estimates have not been independently re-verified; each source is marked read, abstract-only or not read by the agent that wrote it.

Research note, 2026-11-10. Repository read at `6956552` (read-only; no files in the repo were changed). No audio was played or captured. Every number below is labelled with whose it is and under what conditions; numbers marked **estimate** are mine, with the arithmetic shown. Sources marked *(not read)* or *(abstract only)* were not opened in full.

## In plain English

Cyrinx moves data fast when a phone sits on a cloth next to a laptop, and moves nothing when the phone sits a foot away in an ordinary room, because the room keeps echoing for longer than the gap the signal leaves between symbols. The published work on sound links (phones in air, and especially the sonar-modem field, which deals with long echoes all the time) points to three ways of getting a useful rate at a foot to a few metres. One is to make symbols much longer than the echo. Another is to treat the echo as noise and lean on a much stronger error-correcting code. The third is a "single-carrier" receiver that learns the echo and subtracts it. None of these has been demonstrated with this project's devices in this kind of room, and a few cheap measurements on recordings the project already has would show which of them is worth building. The second problem, the small fraction of blocks lost even at close range, has no established cause yet. The literature offers standard fixes for each candidate cause, but the first job is to find out which cause it is. Most of the earlier AI-written proposals either don't address these channels or cite work that could not be found.

**Headline finding.** Before building any new waveform, run three offline checks on the garage recordings already made. They are listed under *Measure first*, below. Each takes hours rather than weeks, and together they decide which of the three approaches can work.

> **Correction to a working assumption.** The garage plan says that if the echo outlasts the guard, "faster codes and better error correction cannot help". The measured error levels contradict that. Mac→Pixel at about 30 cm measured EVM 1.102, which is about −0.8 dB signal-to-interference-plus-noise (SINR). That is low, but in the Gaussian approximation it still supports up to about 0.87 bit per QPSK symbol. A strong low-rate code could in principle decode where the current rate-1/2 convolutional code cannot. Li et al. (2008, below) did exactly this underwater: they treated echoes beyond the guard as noise and used coding plus several receivers. This is a hypothesis to measure, not a result. The one-number test is described under *Measure first*.

> **Second correction.** Treat the full-band strong-tap spread (48 ms and 82 ms at −10 dB) as an upper bound, not as a property of every band. Air absorption alone shortens high-frequency echo tails considerably. My estimate from the ISO 9613-1 formula at 20 °C and 50% RH: an echo arriving 100 ms late has lost an extra 0.2 dB at 1 kHz, 3.6 dB at 8 kHz, 12.5 dB at 16 kHz and 18 dB at 20 kHz, compared with the direct sound. Tabak et al. (below) ran 4 kbps through conference rooms at 19 kHz with a decision-feedback equalizer whose feedback span covers only 40 ms. The guard needed above about 10 kHz may be much shorter than the full-band figure. This is untested here.

---

## 1. What the repository has already established (the constraints)

| Fact | Source in repo |
|---|---|
| Near field, cloth, Mac→Pixel: 65.875 kbps, 4,215/4,280 blocks (98.48%) vs baseline 99.967%. CP96, 64-QAM r2/3, K=7 convolutional code, frame-wide interleaver, 2 sync symbols for H, comb pilots + common phase/slope tracking | `docs/ACOUSTIC_BULK_PHY.md` §1, §3 |
| Garage, ~30 cm, stationary: strong-tap (−10 dB Schroeder) spread 48.3 ms Mac→Pixel, 82.3 ms Pixel→Mac; −15 dB: 87.3 / 141.9 ms; −20 dB: 136.5 / 200.4 ms. Chirp sync locks (peak/mean 135.6, 72.9). QPSK r1/2 CP768 decodes 0/52 and 0/38 at EVM 1.102 / 1.944; Mac→Mac control 52/52 at EVM 0.136 | `scratch/hw20k/NOTES.md` 2026-09-08 |
| Entry 13 desk geometry (35.8 ms at −10 dB): 0 blocks at every MCS, including NFFT 4096 / CP 2048 (43 ms) at EVM ~1.0. Only MT-FSK linked (89–267 bps) | `docs/NEGATIVE_FINDINGS.md` 13 |
| Non-coherent floor: 16-FSK in 8 blocks, 128 tones over 2–15 kHz (102 Hz spacing), T_sym 120 ms with 45 ms skip, RS(15,11)/GF(16) errors-then-erasures, 2-mic energy combining. 138 bps OTA; linked at ds15 ~42 ms desk cells | `scratch/hw20k/mfsk.py`, NOTES A3 |
| MRC rescued a cell where both single mics decoded 0 (11.366 kbps QPSK r1/2 at ds15 15.8 ms, 2× CP) | NOTES 2026-07-08 |
| Stopped spikes, confirmed present: `scratch/hw20k/spike_8a_preeq/README.md` (pre-EQ: primary-mic regressions), `scratch/hw20k/spike8b/REPORT.md` (terminal-estimate interpolation: net −172 blocks; significant-tap CIR projection 0/8,600), `scratch/spikes/dfts_ofdm_8c/results/REPORT.md` (2.7775 dB crest reduction, below 3 dB gate), `scratch/rank11a_mimo/README.md` (no phase-coherent 2×2 data, not identifiable), `docs/spikes/spike-8d-effective-sinr-20260718.md` (loading not identifiable) | ROADMAP Rank 8, 11 |
| Fixed 2,048-sample (42.7 ms) silence after the 85 ms chirp, independent of CP; H estimated from 2 sync symbols | `Sources/CCyrinx/include/cyrinx/cyrinx_bulk.h:303-304`, `cyrinx_bulk.c` ~L1278 |
| Higher convolutional rates failed (r3/4, r5/6); pilot spacing 32/64 and 128-symbol frames lost reliability; paired advantage decayed across a session (24.6 → 6.1–8.7 kbps) | NF 22, 24; `docs/EXPERIMENTS.md` |

On the NumPy BPSK bug, I followed the brief and treated finding 12 as void. I did not re-verify it.

The garage raw captures (`artifacts/garage/…`, per NOTES) are not in this checkout (`/Users/dew/dev/cyrinx/artifacts/` has no `garage/`). The *Measure first* checks assume they exist on the owner's machine or in the garage worktree.

---

## 2. Ranked shortlist: where to lean in

The order is by expected information per hour, not by headline rate. The first three items are offline and decide which of the next four to build.

### Rank 1: "Measure first": three offline checks on retained garage captures (cliff)

**What it addresses:** the reverberation cliff. It decides between items 2–5.

1. **Per-band strong-tap spread.** Recompute the Schroeder −10/−15 dB crossings in octave or third-octave bands (1–2, 2–4, 4–8, 8–12, 12–16, 16–23 kHz) from the retained sweeps.
   - **Basis:** frequency-dependent air absorption (ISO 9613-1 formula, my arithmetic in the callout above). Tabak et al. ran a 19 kHz DFE with a 40 ms feedback span successfully in conference rooms.
   - **Kills it:** every band ≥10 kHz still shows −10 dB spreads ≥40 ms.
   - **Cost:** about an hour, using `scratch/garage/delay_spread.py` with a band-pass stage.
2. **Information actually present: GMI.** On the failed QPSK r1/2 CP768 captures, compute bitwise GMI from the receiver's own LLRs against the known transmitted bits. The tool already exists from Rank 5a. Report it per band and per symbol.
   - **Interpretation:** GMI ≳0.3 bit/coded bit says a rate ≈0.25 code (LDPC or convolutional r1/4) could decode at this placement.
   - **Kills it:** GMI ≤0.15 bit/bit, or LLRs so miscalibrated that GMI is far below the EVM-implied value.
   - **Cost:** hours.
3. **Replay the measured room through the decoder.** Convolve the C modulator's output with the measured garage impulse response, add room tone at the measured level, and decode offline. Try NFFT 2048/CP768, NFFT 4096/CP 2048–4096, and NFFT 8192/CP 4096. Repeat with the post-chirp guard lengthened to ≥ the measured −15 dB spread and with 8–16 training symbols instead of 2.
   - **Why:** entry 13's EVM ~1.0 at a 43 ms guard against a 35.8 ms −10 dB spread is larger than delay-spread arithmetic alone predicts. Residual energy past 43 ms should be ≲−10 dB of total, implying EVM ≲0.3: **estimate**, assuming the Schroeder curve is monotone past −10 dB.
   - **Two receiver-structure hypotheses** (mine, unverified): the fixed 42.7 ms post-chirp silence lets the chirp's own echo corrupt the 2-symbol channel estimate, and 2 training symbols are too few at ~0 dB SINR.
   - **Kills the long-guard branch:** the replay fails at a guard ≥ the −15 dB spread with ample training.
   - **Cost:** a day. The C config already accepts any CP ≤ NFFT (`cyrinx_bulk.c:515`). The post-chirp guard is a compile-time constant and would need a research override.

**Expected benefit:** no throughput by itself. It turns three multi-week candidates into one.

### Rank 2: "Strong-code coherent": low-rate QPSK with a modern code, MRC, and better channel training (cliff; also gives near-field margin)

**Idea:** keep the OFDM waveform. Treat echo beyond the guard as noise. Make the channel estimate good, combine both mics, and use a code rate near the measured GMI.

**Evidence:**
- Li, Zhou, Stojanovic, Freitag, Willett, IEEE JOE 2008 ([MIT copy](https://web.mit.edu/people/millitsa/resources/pdfs/shengli-joe.pdf), read):
  - Setup: zero-padded OFDM, 12 kHz bandwidth, symbols up to 170.67 ms, 25 ms guard, QPSK r2/3 convolutional code, 4-element hydrophone array, shallow water, a moving boat at 3–10 kn.
  - Woods Hole Harbor channel: strong paths to about 80 ms against the 25 ms guard. The authors "treat all multipath returns after the guard interval as additive noise" and report "reasonable performance" with coding and multichannel reception.
  - Rates: 7.0–9.7 kb/s, in water rather than air.
- CCSDS 130.1-G-3, 2020 ([PDF](https://public.ccsds.org/Pubs/130x1g3e1.pdf), §3.3 read). Binary-input AWGN, BER 1e-5:
  - K=7 r1/2 convolutional gives about 5.5 dB coding gain over uncoded.
  - A rate-1/2 turbo code at 8,920 bits sits within 1 dB of the Shannon limit, about 1.5 dB better than convolutional+RS and so about 3.5 dB better than the convolutional code alone.
  - Finite length: 1,000-bit blocks are about 1.3 dB better than 100-bit; 10,000-bit about 0.5 dB better again.
  - Applies to AWGN with ideal interleaving, not this channel.

**Expected benefit (estimate):**
- Mac→Pixel: EVM 1.102 means SINR ≈ −0.84 dB. If the interference behaves like Gaussian noise across interleaved bins, QPSK at code rate 1/4 (0.5 bit/symbol) on ~908 data bins at 17.05 symbols/s gives 908 × 0.5 × 17.05 ≈ **7.7 kbps** before frame overhead.
- Pixel→Mac: EVM 1.944 means −5.8 dB SINR, Gaussian capacity 0.34 bit/symbol. At about 0.2 bit/symbol on ~680 bins: 680 × 0.2 × 17.05 ≈ **2.3 kbps**.
- Realistic range: **0 to ~5 kbps** Mac→Pixel and **0 to ~2 kbps** Pixel→Mac, depending on what GMI shows.
- The "0" end is real. Rank 5a recorded GMI 0.51 alongside 0/50 blocks at rate 1/2, and per-bin fading plus estimation error lower the usable value.
- Near-field side effect: a stronger code at about the current rate could recover margin lost to whatever causes the 1.5% loss (see Rank 6).

**Implementation cost:**
- Cheapest probe: no new code family. Add K=7 r1/3 or r1/4 convolutional mother codes, an 8–16-symbol training block, and a longer post-chirp guard as a research config. Days of work.
- Proper version: the QC-LDPC challenger the garage plan already schedules (Stage 4/G5), which needs the ROADMAP LDPC-deferral amendment the plan already notes. One to two weeks for encoder, layered min-sum decoder, tests and fixtures (**estimate**).

**Cheapest confirming experiment:** Rank 1 check 2 (GMI) on the existing recordings. A different code needs a newly transmitted waveform, so after that, run one short garage session at 30 cm: QPSK r1/4 convolutional with 16 training symbols vs the incumbent r1/2, 2 pairs.

**Kills it:**
- GMI below 0.15 bit/bit on the retained captures, or
- r1/4 decoding <50% of blocks where GMI predicts success (meaning the interference is not noise-like or LLRs are miscalibrated).

### Rank 3: "Long-guard differential OFDM": very long symbols with a guard sized to the measured per-band spread (cliff, stationary only)

**Idea:**
- NFFT 8192 (170.7 ms, 5.86 Hz bins) with a guard near the −15 dB spread.
- Differential (DBPSK/DQPSK) encoding across consecutive symbols on each bin, so no full channel estimate is needed. Alternatively coherent, with the Rank 2 training.
- Pre-FFT resampling is mandatory at this bin spacing (see below).

**Evidence:**
- AquaApp, Chen, Chan, Gollakota, SIGCOMM 2022 ([arXiv](https://arxiv.org/abs/2208.10569), §2.3 read):
  - Stock Android phones underwater, OFDM in 1–4 kHz, 20 ms symbols, 50 Hz spacing, 67-sample CP.
  - Time-domain MMSE equalizer, 480 taps (10 ms).
  - DBPSK across consecutive symbols "to alleviate the time-varying channel effects".
  - K=7 r2/3 convolutional code.
  - 100 bps–1.8 kbps up to 30 m in six real environments.
- Li et al. 2008 (above): 170.67 ms OFDM symbols worked over strong 80 ms multipath with coding and an array.

**Expected benefit (estimate):**
- Mac→Pixel, 1–16 kHz: about 2,560 bins, about 2,400 after 1/16 pilots. Symbol period with CP 4096 (85 ms) is 12,288 samples, so 3.9 symbols/s.
- DQPSK r1/2: 2,400 × 1 × 3.9 ≈ 9.4 kbps. DBPSK r1/2: ≈4.7 kbps. Before frame overhead and fades.
- Stated range: **2–5 kbps**, if the Rank 1 replay shows residual ISI ≤ −10 dB at that guard.
- Pixel→Mac needs about a 128 ms guard at −15 dB, so roughly 20–30% lower.

**Constraints:**
- *Clock offset:* the measured −24.6 ppm Mac↔Pixel offset is 0.39 Hz at 16 kHz = 6.7% of a 5.86 Hz bin. ICI ≈ (π·0.067)²/3 ≈ **−18 dB** (estimate, standard small-offset approximation). The receiver must therefore resample before the FFT, as Li et al. do.
- *Motion:* 0.1 m/s radial motion gives about 4.7 Hz Doppler at 16 kHz, nearly a whole bin. This tier only serves stationary placements.
- *Conflict with entry 13:* entry 13 measured 0 blocks at NFFT 4096/CP 43 ms. This branch must not be built until the Rank 1 replay explains that result. What would be different: a guard sized per band to the −15 dB spread, a lengthened post-chirp guard, more training, and pre-FFT resampling.

**Implementation cost:**
- Moderate.
- Differential mapping and demapping: small.
- Pre-FFT fractional resampler, which Stage 3 already plans: a few days.
- Frame-structure changes, because chirp guard and training count are fixed today: a few days.
- CPU is not a concern at about 4 symbols/s.

**Kills it:**
- The replay check fails at a guard ≥ the −15 dB spread, or
- the OTA DQPSK symbol error rate stays above about 20% with resampling on.

### Rank 4: "Single-carrier DFE": pulse-shaped single-carrier QAM with an adaptive phase-coherent DFE, in the band with the shortest echo (cliff; also a motion-tolerant mode)

**Evidence:**
- Tabak, Lin, Singer, "High Data Rate Near-Ultrasonic Communication with Consumer Devices" ([arXiv 2103.11261](https://arxiv.org/abs/2103.11261), PDF read):
  - Two laptops (HP EliteBook 830, Lenovo T460p) using built-in speakers and mics, in conference rooms.
  - QPSK at 2 kbaud on a 19 kHz carrier, RRC roll-off 0.3, so about 2.6 kHz occupied. The "4 kbps" is the raw signalling rate; there is no FEC.
  - Phase-coherent DFE: 8 non-causal + 20 causal feed-forward taps, 80 feedback taps (40 ms). 30% of symbols used for training, 5,120-bit packets.
  - Measured BER: <2e-4 at 0.5 m and 2 m in both directions; 3 m <2e-4 one way and 0.08 the other; 5 m 0.09–0.1 at 65–80% volume, <2e-4 and 0.01 at 100%.
  - Equalizer output SNR 28 dB at 0.5 m.
- Underwater lineage: Stojanovic, Catipovic, Proakis, IEEE JOE 1994 ("Phase-coherent digital communications for underwater acoustic channels"; *abstract only*, via [IEEE record](https://search.isc.ac/inventory/49/1097731.htm)) combine an RLS DFE with second-order PLL/DLL. A 1992 ASA abstract from that group ([link](https://auditory.org/asamtgs/asa92nwo/1pUW/1pUW2.html), not read beyond snippet) reports up to 100 feed-forward / 80 feedback taps at 1,000 symbols/s.
- Yamamoto & Kubo, IEICE ComEx 2021 ([link](https://www.jstage.jst.go.jp/article/comex/11/3/11_2021XBL0206/_article/-char/en/), *abstract only*): single-carrier block transmission with frequency-domain equalization, speaker precoding and channel interpolation, "32 kbps transmission information bit rate in hand-held conditions". Distance and hardware are not stated in the abstract.

**Transfer to Cyrinx:**
- Mac→Pixel can use 16–23 kHz. The Mac speaker was flat to 23 kHz near-field, and NF-9's incoherence finding concerns *phone* speakers above ~18 kHz.
- Pixel→Mac cannot go above about 17 kHz, where echo tails are longer (Rank 1 check 1 tells how much longer).
- Tabak's rooms and devices differ; their 3–5 m failures were direction-dependent and tied to speaker placement.

**Expected benefit (estimate):**
- Mac→Pixel: one 2–4 kbaud QPSK carrier in 17–23 kHz gives 4–8 kbps raw, roughly 2–5 kbps after r1/2 coding and training.
- Several parallel carriers in separate sub-bands (each with its own DFE) could scale with usable bandwidth. That is my extrapolation, untested in air.

**Implementation cost:**
- High relative to the others: new waveform, RRC filters, fractionally spaced DFE with RLS or LMS plus PLL, training and decision-directed modes, and new fixtures.
- Roughly 2–4 weeks in C (**estimate**). This is the garage plan's Stage 8b.

**Cheapest experiment:** offline first. Drive a Python DFE with the retained garage sweep impulse response (band-limited to 17–23 kHz) and measured noise. If the equalizer output SNR is ≥10 dB, one short OTA session with a 2 kbaud QPSK burst (the transmit side is trivial to generate).

**Kills it:**
- Simulated output SNR <6 dB in every band that the transmitting route can carry coherently, or
- the OTA DFE diverges (BER >1e-1) in 2 of 2 pairs.

### Rank 5: "Hopped MFSK floor": frequency-hopped, denser, soft-decoded MFSK to replace the 138 bps floor (cliff fallback)

**Evidence:**
- WHOI Micro-Modem: FH-FSK at 80 bps alongside coherent PSK at about 5 kbps (Freitag et al., OCEANS 2005, [PDF](https://acomms.whoi.edu/wp-content/uploads/sites/20/2014/08/Micromodem1_2005.pdf), *not read*; the figure comes from the search abstract).
- JANUS / STANAG 4748: FH-BFSK, 13 tone pairs, 62.5 ms chips, 80 bps (Potter et al., UComms 2014, *not read*; figures from search snippets). Both use frequency hopping so that a tone is not reused while its own echo is still ringing. Their exact design rationale was not read.
- Smartphone chirp/FSK links are much slower than this:
  - Lee, Kim, Choi, Choi, INFOCOM 2015: chirp BOK at 16 bps up to 25 m indoors (*not read*; [DOI 10.1109/INFOCOM.2015.7218629](https://doi.org/10.1109/INFOCOM.2015.7218629)).
  - Google Nearby: DSSS at 94.5 raw b/s, "reliable at 2 m, often works at 10 m" ([Getreuer et al. 2018](https://getreuer.info/papers/getreuer2018ultrasonic/index.html), abstract read).
  - ggwave: 6-of-96-tone MFSK, 8–16 bytes/s, RS ([README](https://github.com/ggerganov/ggwave), read).

**Expected benefit (estimate):**
- Today's floor uses 128 tones at 102 Hz spacing. With a 75 ms integration window the orthogonal spacing is 13.3 Hz, so 2–15 kHz holds about 975 tones.
- Split them into 2–3 hopping sets so each tone rests for 2–3 symbols, and use 16-FSK blocks: 975/2/16 × 4 bits / 75 ms ≈ 1.6 kbps raw (2 sets) or 1.1 kbps (3 sets).
- With soft-decision decoding at about r1/2: **0.4–1 kbps**, 3–7× today's floor.
- Leakage from decaying neighbours and per-tone Rayleigh fading are the main risks.

**Implementation cost:** low. The change is a modification of `mfsk.py`: hop sets, a denser grid, energy-based soft metrics into a convolutional or LDPC decoder instead of hard RS. A C port follows only if adopted.

**Cheapest experiment:** digital first (`mfsk.py` selftest with the measured garage impulse response convolved in), then one garage session at 30 cm comparing it with the current floor.

**Kills it:** at the 30 cm cell, frame success is below the current floor's, or the net gain is under 2×.

### Rank 6: "Find the late-frame cause before fixing it" (late-frame/near-field loss)

The literature has a standard fix for each candidate cause. The repo has not yet shown that the losses are late in the frame. The frame-wide interleaver makes block index non-chronological (spike 8b says so explicitly). Nor has it shown which cause applies.

**Diagnostic (offline, hours):** on the retained Cyrinx 2.0 captures (hash-bound inventory in `scratch/hw20k/spike8b/inventory.json`), plot known-pilot residual power by symbol index × frequency × frame-in-run × run order. Then read the signature:

| Signature | Points to | Literature fix | Expected gain |
|---|---|---|---|
| Residual constant in time, rising ~ (frequency)² | Sampling-clock ICI | Pre-FFT resampling (Li et al. 2008) | Estimate: at −24.6 ppm, ICI ≈ (π·ε·k)²/3 gives −27 dB at bin 981 (23 kHz) and −34 dB at 11 kHz, against a total error of about −24 dB (EVM 0.06). This is a floor on the top bins, not a late-frame effect. Timing creep is about 3.4 samples per 64-symbol frame (5.1 per 96), well inside CP96 minus its 24-sample early bias. |
| Residual grows with symbol index in narrow, moving frequency regions | Channel aging / micro-motion | Frequency-staggered scattered pilots. DVB-T uses `k = Kmin + 3·(l mod 4) + 12p` ([ETSI EN 300 744](https://www.etsi.org/deliver/etsi_en/300700_300799/300744/01.06.02_60/en_300744v010602p.pdf), clause 4.5.3 read), so every 3rd carrier is observed every 4 symbols. Add temporal smoothing per bin, not across bins (NF-2). | Unknown. Spike 8b showed endpoint interpolation regresses, so the drift is not smooth over 3 s and denser-in-time observation is the plausible lever. |
| Isolated broadband spikes coinciding with room-tone transients | Impulsive noise | Existing per-symbol EVM² erasure weighting, plus LLR clipping | Small; already partly in place (NF-6) |
| Differences between frames 1–5 and across runs, not within a frame | Session-level drift (thermal or speaker protection, HVAC) | Fresh training per frame; ARQ | NF-24's decaying paired advantage (24.6 → 6–9 kbps) hints at this |

**Code margin:** the Rank 2 LDPC work also applies here. At about the same rate (r≈0.67–0.70) it buys roughly 1.5–3 dB (estimate, scaled down from the CCSDS AWGN figure because 64-QAM BICM, finite length and non-AWGN errors reduce gains). That is the most likely route to both a lower loss rate and the r≈0.70 profile the roadmap wants.

**Kills the diagnosis branch:** none; it is a measurement. The resampler is killed if top-band residuals are not frequency-dependent. The pilot change is killed if the residuals do not grow with symbol index.

### Rank 7: "Covariance-aware combining" (small; mostly near-field)

The garage plan's regularized combiner `w ∝ (Rn+λI)⁻¹h` is well founded for *directional* interference, such as a fan or a single strong reflection that is stable across symbols. It is not a cure for diffuse reverberation. In a diffuse field the cross-mic correlation falls off as sin(kd)/kd (Cook et al., JASA 27:1072, 1955, *not read*; formula is textbook). For mics about 15 cm apart its first zero is at about 1.1 kHz (estimate: c/2d). Above that the two mics see nearly uncorrelated reverb, and MRC is already close to optimal against it.

**Expected gain:** 0 dB to a few dB, only in cells with a dominant directional interferer. Low priority for the cliff.

---

## 3. Verdicts on the earlier AI-written analysis (section 3 of the Antigravity note)

| Proposal | Verdict | What the literature says, and transfer |
|---|---|---|
| "HiRA-style" overlapping multi-chirp CSS, 1.5–5.5 kbps at 0.3–3 m | **Citation not found; numbers unsupported.** | No acoustic system named HiRA turned up in searches. The only overlapping-chirp result found was a patent ([US 10819386](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/10819386), not read). Published smartphone chirp links run at 16 bps to 25 m (Lee et al. 2015) and 20 bps to 70 m (2023 Android CSS, per search snippet, not read). The note's own condition, that parallel chirp shifts must be spaced wider than the multipath beat spread (B/T_c)·τ_max, gives (14 kHz/32 ms)·80 ms = 35 kHz, which exceeds the whole band (my arithmetic on their numbers), so the design cannot meet it. Non-coherent CSS is orthogonal M-ary signalling in a rotated basis, with the same rate ceiling as MFSK of the same symbol length. Fold into Rank 5 instead. |
| OCDM with frequency-domain equalization | **Exists; does not address the cliff.** | Ouyang & Zhao, IEEE TCOM 2016 ([arXiv 1601.06576](https://arxiv.org/abs/1601.06576), read): simulation only, "guard interval is inserted between adjacent blocks, just as the OFDM system does". The advantage shown is uncoded multipath diversity with single-tap equalizers. Halder & Murthy 2025 ([arXiv 2508.21373](https://arxiv.org/abs/2508.21373), simulation): with coding, OFDM/OTFS/OCDM/ODSS give "nearly the same BER" under a capable receiver. OCDM needs the same guard OFDM does, so it does nothing about echo longer than the guard. |
| Two-mic interference-rejection combining (IRC/MMSE) | **Partly supported.** | Sound for directional interference; limited against diffuse reverb (Rank 7). Keep it as the garage plan's bounded Stage 2 receiver experiment. |
| Multi-channel time-domain channel shortening (TEQ, "Bezout") to fit 50–80 ms rooms into 8–16 ms | **Unsupported for this channel.** | Channel shortening is mature in DSL over static wireline channels (Martin et al., IEEE TSP 53(10), 2005; abstract via [UT page](https://users.ece.utexas.edu/~bevans/papers/2005/equalizationI/index.html)). For room acoustics, exact multichannel inversion (MINT) is "highly sensitive to RIR estimation errors" (Kodrasi, Goetze, Doclo, IEEE TASLP 2013, [PDF](https://uol.de/fileadmin/user_upload/mediphysik/ag/sigproc/download/papers/SP2012_18.pdf), abstract read). Equalization degrades with source or mic moves of "a few tenths of the acoustic wavelength", worst at high frequency (Radlović, Williamson, Kennedy, IEEE TSAP 8(3) 2000, [abstract](https://researchportalplus.anu.edu.au/en/publications/equalization-in-an-acoustic-reverberant-environment-robustness-re/)): about 3–5 mm at 10 kHz (estimate). AquaApp's single-channel 10 ms TEQ is a much milder task in a 3 kHz band. |
| Residual ISI cancellation (RISIC) | **Partly supported; high risk here.** | Real: Kim & Stüber, IEEE JSAC 16(8) 1998, HDTV, static/slowly fading channels (*abstract only*, [record](https://search.isc.ac/inventory/49/1445945.htm)). Cancelling a 50–100 ms tail needs that tail to be stable and known; the RIR-sensitivity work above and Prawda et al. 2025 ([arXiv 2501.01206](https://arxiv.org/abs/2501.01206), read) report coherence loss growing with RIR time and frequency. At SINR near 0 dB, decision-driven reconstruction will often feed back errors. Revisit only after Rank 2 shows usable soft information. |
| Pre-FFT resampling for clock offset and Doppler | **Supported in mechanism; overstated as a late-frame fix.** | Li et al. 2008 (read): resampling plus residual CFO compensation worked at speeds where Doppler exceeded the bin spacing. For the static bench, my estimate puts clock ICI at −27 dB at the top bin, constant in time, and window creep at 3.4–5.1 samples within a 96-sample CP. Worth building for motion and for any long-symbol tier (Rank 3), where it is mandatory. The note's arithmetic (3 mm/s → 8.7 ppm) is correct. |
| Delay-subspace pilot tracking, staggered pilots, periodic midambles | **Partly supported.** | Staggered pilots are standard practice (DVB-T, above). Delay-domain projection conflicts with a recorded result: spike 8b's preregistered significant-tap CIR projection produced 0/8,600 blocks. That does not rule out every CIR estimator, but the claim needs a design that explains that failure. The claim that a 1/32 midamble "completely eliminates late-frame EVM decay" has no evidence. Spike 8b found terminal training did not predict the midpoint. |
| Per-block QC-LDPC (5G NR BG2) with incremental-redundancy HARQ | **Partly supported.** | Direction supported: CCSDS shows modern codes about 3.5 dB better than K=7 convolutional at BER 1e-5 on AWGN. The note's specific "+2.2 to +3.5 dB over punctured K=7" and ">50 Mbps on Cortex-A76" are unsourced. IR-HARQ is reasonable after selective repeat works, as the garage plan already says. |
| CA-polar for control | **Partly supported, low priority.** | Polar with CRC-aided list decoding performs well at short lengths (Liva et al. survey, [arXiv 1610.00873](https://arxiv.org/abs/1610.00873), abstract and snippets only). Control frames are not the bottleneck. |
| Sub-band bit-loading (8–16 sub-bands) | **Partly supported; conflicts with NF-11/8d unless thresholds are calibrated.** | AquaApp picks one contiguous band from preamble SNR, a coarse form that works on phones. NF-11 measured naive per-bin loading underperforming; only FEC-calibrated thresholds won (+10%). Spike 8d stopped as not identifiable. Coarse band selection (Stage 9 item 2) is the defensible version. |
| Citation hygiene in that note | **Errors.** | It cites NF-21 for pre-emphasis (NF-21 is CP48) and NF-20/NF-23 for handheld chronological EVM (they are mic capture and lower band edge). Its table gives cloth-desk spread "<2.0 ms", which the repo does not measure (repo: about 21.7 ms at −30 dB). |

---

## 4. What not to pursue now, and why

- **Climbing the CP/MCS ladder on the current receiver at range.** NF-13 and the garage cell both show zero at every MCS. The one new reason to revisit long guards is the Rank 1 replay check, and the long-guard branch (Rank 3) depends on it explaining NF-13.
- **OTFS / AFDM / OCDM.** No measurement shows Doppler-dominated loss (ROADMAP deferral trigger unmet). Coded performance is near-identical to OFDM in simulation (Halder & Murthy 2025). The one sea-trial claim found (Guishan Island 2024, OTFS BER < OFDM; [record](https://unpaywall.org/10.1109%2FICSPCC62635.2024.10770503), not read) is underwater and not about echo longer than the guard.
- **Multichannel inversion / MINT-style channel shortening of room tails** (section 3 verdict).
- **Pre-EQ, terminal-estimate interpolation, DFT-spread OFDM for crest factor.** Spikes 8a, 8b and 8c stopped. Reopen only with a new hypothesis that explains their failures.
- **Weaker convolutional rates (r3/4, r5/6)** (NF-22) and **fine per-bin loading** (NF-11, 8d).
- **Coherent ultrasonic phone uplink** (NF-9). Tabak's 19 kHz result is laptop speakers; it does not contradict NF-9's phone-speaker finding.
- **BackDoor-style nonlinearity links.** Roy, Hassanieh, Roy Choudhury, MobiSys 2017 ([PDF](https://synrg-web.web.engr.illinois.edu/papers/backdoor_mobisys17.pdf), not read; reported 4 kbps at 1 m, 2 kbps at 1.5 m per search snippets). This needs an external ultrasonic transmitter, which belongs to Rank 14b.
- **MIMO** (11a not identifiable) and **learned end-to-end modems** (no held-out corpus).
- **Sparse channel estimation from underwater work, applied to the late tail.** Underwater channels are sparse; a room's late reverberation is dense. Sparsity priors may help only with the early reflections. This is my judgement, not a measured result.

---

## 5. Questions only the owner can answer

1. **Is reliable operation at about 30 cm–1 m off-contact a product requirement? If so, what is the smallest rate that is still useful there?** For example, about 0.5 kbps (chat or control: Rank 5), 2–5 kbps (small files: Ranks 2–4), or ≥10 kbps. This sets how far down the shortlist to go.
2. **Which direction matters most off-contact: laptop→phone or phone→laptop?** Phone→laptop is the harder channel (82 ms spread, ≤17 kHz band), and Rank 4 helps it least.
3. **Must the off-contact mode work while a phone is handheld, or only resting on a surface?** Rank 3 is stationary-only. Ranks 2 and 4 tolerate more motion.
4. **Is the 99.967% first-pass block gate a product requirement, or a benchmark convention that selective-repeat ARQ could replace?** With ARQ, a 1.5% first-pass loss costs about 1.5% throughput. If the gate is a convention, Rank 6 drops in priority.
5. **Is audible operation acceptable at range, and at what loudness?** The long-symbol and hopped-tone modes sound different from the current burst, and loudness trades directly against range.

---

## Source list and read status

| Source | Status |
|---|---|
| Tabak, Lin, Singer, arXiv 2103.11261 — https://arxiv.org/abs/2103.11261 | PDF read (method §IV–V, Table II) |
| Li, Zhou, Stojanovic, Freitag, Willett, IEEE JOE 33(2) 2008 — https://web.mit.edu/people/millitsa/resources/pdfs/shengli-joe.pdf | Read (abstract, §IV experiments) |
| Chen, Chan, Gollakota, AquaApp, SIGCOMM 2022 — https://arxiv.org/abs/2208.10569 | Read (§2.2–2.3) |
| CCSDS 130.1-G-3 (2020) — https://public.ccsds.org/Pubs/130x1g3e1.pdf | Read (§3.3) |
| ETSI EN 300 744 v1.6.2 (DVB-T) — link above | Read (clause 4.5.3) |
| Ouyang & Zhao, OCDM — https://arxiv.org/abs/1601.06576 | Read (§III-C, §VI) |
| Halder & Murthy 2025 — https://arxiv.org/abs/2508.21373 | Abstract + intro read |
| Kodrasi, Goetze, Doclo, IEEE TASLP 2013 — link above | Abstract read |
| Radlović, Williamson, Kennedy, IEEE TSAP 2000 — link above | Abstract only |
| Prawda et al. 2025, RIR sensitivity — https://arxiv.org/abs/2501.01206 | Read (summary of results) |
| Yamamoto & Kubo, IEICE ComEx 2021 — link above | Abstract only |
| Getreuer et al. 2018 (Google Nearby) — link above | Abstract only |
| BatNet — https://arxiv.org/abs/2008.00136 | Abstract only (8-PSK, 20–24 kHz, >600 bps, ≤6 m) |
| Kim & Stüber 1998 (RISIC); Stojanovic et al. 1994; Martin et al. 2005; Speth et al. 1999 | Abstract or metadata only |
| Lee et al. INFOCOM 2015; Dhwani (SIGCOMM 2013, 2.4 kbps per MSR abstract); JANUS (Potter et al. 2014); WHOI Micro-Modem (Freitag et al. 2005); BackDoor (MobiSys 2017); Cook et al. 1955; ISO 9613-1 | Not read (figures from search abstracts or snippets, or a standard formula I applied myself) |
| U-Wear (MobiSys 2015), Quiet profiles, Chirp.io | Not read; no claims made from them |
