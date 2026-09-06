# Acoustic throughput: capacity, receiver limits, and research direction

Research review dated 2026-09-04. Repository inspected at `2370fe2`. This is an analysis and proposal, not a hardware qualification or an amendment to existing experiment gates. No audio was emitted, no new OTA measurement was made, and no shipping source was modified. The historical capacity arithmetic was independently reproduced from the committed JSON using the repository virtual environment. Literature searches included recent 2024–2026 work; the most directly applicable evidence also includes older terrestrial acoustic experiments and foundational coding theory.

The follow-up [garage-scale execution plan](garage-throughput-plan.md) turns these recommendations into ordered experiments using the available phones, household placement fixtures, and short operator sessions.

**Recommendation:** retain the portable-C wideband OFDM modem as the incumbent, and develop a continuously tracked, diversity-aware, soft-decoded adaptive link. Prioritize receiver estimation/equalization, realistic channel evidence, modern FEC experiments, and selective recovery. Give single-carrier equalization a serious controlled comparison for difficult or moving links. Make true spatial multiplexing conditional on measured independent channel modes. There is no evidence for choosing one new waveform as universally superior across these devices and environments.

The assumed main product constraint is communication through built-in speakers and microphones on unmodified devices, with audible operation permitted. Fully inaudible operation and custom transducers change the feasible problem substantially. Define the allowed band, peak and average drive, acoustic output, latency, outage probability, and traffic direction before asking for a maximum. Without such constraints there is no single finite engineering answer.

**What the current evidence establishes.** The accepted Mac-to-Pixel result is 65.875 kbps of strict ordered, byte-verified scheduled payload, with 4,215/4,280 blocks recovered. Its paired conservative control recovered 2,999/3,000. The 69.652 kbps experiment changes the schedule to zero gaps and recovers 90.666% of blocks. Both are valuable throughput results; neither demonstrates noninferior robustness, and neither includes a complete deployed application's discovery, sounding, feedback, retransmission, and completion latency. The 2.0 Pixel captures are decoded by a frozen C receiver on the host. See the [measurement record](../ACOUSTIC_BULK_PHY.md).

These are close-range, route-specific observations with the phone placed on cloth near the laptop's left speaker. They do not establish a rate at one metre, while handheld, on another phone, or in another room. Historical reverse-direction results are substantially lower. The [3.0 plan](../CYRINX_3_PLAN.md) correctly makes integrated on-device transport a separate deliverable.

For the accepted 64-symbol profile, the occupied/data/pilot counts are 935/876/59. Each frame occupies 147,648 samples, or 3.076 s. Five frames plus four 250 ms gaps occupy 16.380 s. The maximum payload is 5 × 107 × 256 bytes, giving **66.891331 kbps**. Recovering every remaining block adds only **1.542%** relative to 65.875 kbps. Receiver improvements can improve resilience substantially, but a large rate gain requires those improvements to support a different coding, modulation, training, band, or scheduling choice.

During data symbols, the configured information-bit rate is about 78.448 kbps before block CRC/fill and frame overhead. The error-free one-frame payload rate is 71.241 kbps. This decomposition prevents confusing removal of protocol overhead with increased channel information capacity.

**What the Shannon limit means here.** For a scalar, linear, stationary channel with additive white Gaussian noise and an average-power constraint,

\[
C=B\log_2(1+S/N).
\]

Here B is usable acoustic bandwidth, not PCM sample rate; S/N is a linear power ratio over that band. A 48 kHz real-valued audio stream does not provide 48 kHz of independent positive-frequency acoustic bandwidth. A higher sample rate is helpful only when the complete transmit/receive path provides additional useful bandwidth. This is the model in [Shannon's original paper](https://people.math.harvard.edu/~ctm/home/text/others/shannon/entropy/entropy.pdf), not a universal description of an audio device with automatic processing.

The following are calculated model examples, not measured Cyrinx capacities. Each column assumes the listed SNR over its own bandwidth; widening the band does not automatically preserve SNR at fixed power.

| SNR | 21.9 kHz wideband capacity | 5 kHz near-ultrasonic capacity |
|---:|---:|---:|
| 0 dB | 21.9 kbps | 5.0 kbps |
| 10 dB | 75.8 kbps | 17.3 kbps |
| 20 dB | 145.8 kbps | 33.3 kbps |
| 30 dB | 218.3 kbps | 49.8 kbps |
| 40 dB | 291.0 kbps | 66.4 kbps |

An ideal 100 kbps scalar link in 21.9 kHz needs at least 13.56 dB; 200 kbps needs at least 27.48 dB. Real links need additional margin and incur acquisition, coding-block, tracking, and protocol costs. These numbers justify investigating room for improvement; they do not forecast a 100 or 200 kbps implementation.

The more useful stationary linear model is frequency dependent:

\[
C=\max_{S_x}\int_{\mathcal B}\log_2\left(1+
\frac{|H(f)|^2S_x(f)}{S_n(f)}\right)df,
\qquad \int_{\mathcal B}S_x(f)df\le P.
\]

With a fixed input spectrum this expression gives the corresponding Gaussian-input information rate, rather than the maximization over allowed spectra. Spectral masks and average-power limits constrain water-filling; real time-domain peak limits add constraints not captured by a simple per-bin allocation. For multiple independently accessible inputs and outputs, the integrand becomes `log2 det(I + Rn^(-1/2) H Q Hᴴ Rn^(-1/2))`, with transmit covariance Q constrained by total power. This exposes the value of noise whitening and independent spatial modes. See [Tse and Viswanath](https://stanford.edu/~dntse/wireless_book.html).

For a moving or intermittently occluded link, a single stationary number is insufficient. Specify whether the objective is long-term average throughput or a rate sustainable with a chosen outage probability and latency. For a frozen-rate, quasi-static model, one useful description is the largest R for which `Pr(C(state) < R) <= epsilon`. A transmitter that can wait for better conditions solves a different problem from one with an immediate delivery deadline.

**The historical capacity numbers need tighter labels.** The [old characterization script](../../scratch/hw20k/characterize.py) computes:

`min(log2(1 + 10**(max(SNR_dB, 0)/10)), 8)`

over approximately 0.2–24 kHz. Three issues matter:

- The eight-bit cap is an imposed signaling ceiling; it is not Shannon's unconstrained Gaussian-input result and is not the actual finite-SNR mutual information of 256-QAM.
- Flooring negative SNR to 0 dB credits weak bins with at least one bit/s/Hz. The correct treatment retains negative dB values; zero received signal should approach zero capacity contribution.
- Probe-on minus probe-off PSD attributes all additional energy to useful signal. It can include nonlinear products and time-varying processing that do not preserve the information in randomized QAM. A passive recording also does not capture all active-link interference.

The document quotes approximately 184/155 kbps, whereas the currently committed `channel.json` contains 187.051/165.726 kbps for the primary forward/reverse paths. Reproducing that JSON's arithmetic gives the latter values. Removing only the dB floor changes them to 186.913/164.986 kbps: the floor is a real error but not the main explanation for the discrepancy from delivered throughput. Removing both floor and cap gives stationary arithmetic of 403.234/421.551 kbps. **Those much larger numbers are not corrected capacity measurements.** They demonstrate sensitivity to arbitrary assumptions; this snapshot is not a substitute for same-session channel identification.

The [ultrasonic writeup](../ULTRASONIC_BAND.md) also overstates a limit: 5.4 kHz at 22 dB gives 39.514 kbps in the ideal AWGN formula, so the reported 9–10 kbps cannot be called a fundamental bandwidth ceiling. It is a measured/profile-specific result. Exceeding 20 kbps may remain difficult on that route, but more bandwidth is not mathematically necessary if sufficiently efficient reliable signaling can be achieved. Its reported phase instability also does not isolate whether the speaker mechanics, smart amplifier, audio processing, or measurement conditions cause the failure.

**Approaching capacity requires closing different gaps.** Measure a hierarchy: a stated channel-model bound; a constellation- and receiver-specific achievable information estimate; actual finite-code recovery; and complete application goodput. Do not equate an EVM-derived SINR with independently measured Gaussian channel noise. A modern receiver may remove part of that EVM, while some residual may be signal dependent or correlated.

Bitwise generalized mutual information (GMI), calculated from known randomized probe bits and the receiver's actual log-likelihood ratios, is useful for the second layer. It measures information usable by the chosen bit-metric decoder under its modeling assumptions. It is neither an upper bound on all receivers nor a finite-frame delivery guarantee. This distinction follows the [mismatched-decoding analysis of Martinez et al.](https://arxiv.org/abs/0805.1327).

Cyrinx's [Rank 5a replay](../spikes/rank-5a-capacity-predictor-20260718.md) supports using GMI diagnostically: its rank correlation with recovery was 0.8842, versus 0.5278 for passive-noise arithmetic. Yet GMI 0.5133 for a rate-1/2 probe coincided with 0/50 recovered blocks. Only one correlated physical repeat was held out. Average GMI, optimized after observing known probes, still needs independent calibration, temporal uncertainty, and code-specific lower-tail evidence before activation decisions.

Finite block length imposes an additional cost: in an appropriate memoryless model, `R*(n,epsilon) ≈ C - sqrt(V/n) Q^-1(epsilon)`. The units here are bits per channel use; n is the coding block length in channel uses. This is a reference for the rate/reliability/latency tradeoff, not a formula to insert a room's average SNR into. See [Polyanskiy, Poor, and Verdu](https://people.lids.mit.edu/yp/homepage/data/finite_block.pdf).

One cannot reliably exceed the capacity of the correctly specified channel with the same constraints. Apparent exceptions change the model or metric: additional bandwidth, power, independently observable spatial modes, improved geometry, cancellation of predictable interference, source compression, previously shared information, or accepted errors/loss. More original bytes after decompression are not more newly transmitted information. Feedback and HARQ improve practical efficiency and reliability; they do not defeat the fixed memoryless AWGN limit. A deterministic benchmark payload must be unknown to the decoder except through its received signal and legitimately shared protocol state.

**Advantages of the existing approach.** Wideband OFDM is a defensible starting point: it exploits substantial audible bandwidth and turns sufficiently static multipath with adequate guard into per-bin estimation. Portable C, cross-language fixtures, explicit receiver contracts, and strict ordered byte verification support reproducible research. Soft decisions and local pilot reliability already produced an isolated same-capture gain of 347 blocks. Two-microphone combining has rescued captures where neither input alone decoded. A robust low-rate bearer is appropriate for discovery, feedback, and recovery. These mechanisms should remain controls for further experiments.

**Where the implementation leaves information unused.** In [cyrinx_bulk.c](../../Sources/CCyrinx/cyrinx_bulk.c), two initial training symbols establish the complex channel response. Data symbols use that response and update common phase and linear frequency-phase slope. The receiver updates uncertainty from pilots, but does not generally update the full complex channel at every data frequency. It has no general multi-symbol ISI/ICI cancellation or continuous resampling stage in this batch demodulator. Its two-channel combining uses inverse per-channel noise variance rather than a full interference covariance.

The fixed pilot comb and eleven-pilot reliability smoother can miss narrow, changing features. The large frame-wide interleaver spreads bursts but also delays recovery and distributes a short severe impairment across many CRC blocks. The convolutional stream is encoded across the frame; the 256-byte CRC records are accounting/recovery units, not independent modern FEC codewords. Uniform constellation and punctured K=7 convolutional coding are simple, but neither represents the current practical frontier for adaptable coded modulation.

The C sounder currently offers a scalar median-SNR/delay-spread ladder, not the comprehensive adaptive estimator proposed in the roadmap. Likewise, [Simulation.swift](../../Sources/Cyrinx/Simulation.swift) injects channel metrics into an in-memory transport; its moving-phone profile does not propagate the actual waveform through a moving multipath acoustic channel. It is useful for protocol behavior, but cannot qualify PHY motion robustness.

**The first research priority should be realistic channel evidence and receiver tracking.** Establish repeated, simultaneous transmit-reference and multi-input receive records across distances, devices, room types, orientations, and motion. Measure the active channel, passive and active residual covariance, clipping, gain dynamics, sample-clock drift, delay/Doppler structure, and discontinuities. Preserve failed acquisitions and route identities. Use randomized QAM probes with representative waveform statistics, not just repeated tones.

For motion, implement and compare audio-derived time-scale estimation and fractional resampling before the FFT, followed by residual phase/timing tracking. At 20 kHz and sound speed 343 m/s, 0.1 m/s radial motion produces about 5.83 Hz Doppler. Cyrinx's 2048/48k FFT spacing is 23.4375 Hz, so this is about a quarter bin; a useful symbol lasts 42.67 ms. This calculation shows why correcting phase after the FFT alone may leave substantial intercarrier interference. Different moving paths may need more than a common resampler. [Li et al.](https://www.mit.edu/~millitsa/resources/pdfs/shengli-joe.pdf) demonstrate the resampling-plus-residual-correction principle in underwater acoustics; its mechanism transfers, not their hardware results.

Compare a frequency-staggered known-pilot lattice and periodic full training against the incumbent at equal accounted overhead. Estimate a time-varying channel with a physically justified basis or regularized state-space model. Do not average raw complex H across nearby bins: delay can make its phase rotate rapidly. Delay-aligned or validated channel-basis estimation is different from that previously failed smoothing operation. If considering decision-directed refinement, use only information available to the actual decoder, bound error propagation, and retain a known-pilot fallback. Decoder extrinsic information can support a future iterative equalizer, but should not select an experiment's winning profile retrospectively.

**The next priority should be receiver diversity and equalization before spatial multiplexing.** For one transmitted stream, compare best input, existing MRC, and regularized covariance-aware combining `w proportional to (Rn + lambda I)^-1 h`. Fit covariance from appropriate independent noise/residual samples, with shrinkage and held-out validation. This can suppress correlated interference and use complementary spectral nulls. Independently verify that the OS inputs are distinct and sample aligned.

For large delay spreads, test multi-input channel shortening or a regularized block equalizer that explicitly accounts for adjacent symbols. A time-reversal matched filter is a useful baseline, but is not an inverse and may leave sidelobes. An MMSE solution must trade residual interference against noise enhancement. These are research proposals; one-tap scalar equalization cannot remove all such interference merely by increasing a guard interval.

The broad old conclusion that coherent OFDM cannot work in a reverberant desk geometry should be scoped to the tested receiver and inputs. Later MRC rescue already demonstrates that receiver structure matters. Long reverberation is not proof of zero coherent information. Conversely, a noncoherent floor cannot guarantee positive throughput under arbitrary occlusion or interference; the README's “never zero” should refer only to the tested cells.

**Modern FEC deserves an early, bounded experiment.** Keep the current Viterbi decoder as the control. Compare one established QC-LDPC family using soft decoding and early termination at several rates and useful codeword lengths. Use time/frequency interleaving bounded by the desired latency, CRC-protected delivery units, and a rate-compatible layout that can later send incremental parity. Evaluate decoder computation, memory, energy, synchronization failures, and whole-transfer completion, not just AWGN BER.

The conceptual reason is stronger than a desire for a higher headline: weakened convolutional coding already fails, while reliability calibration demonstrably changes recovery. That does not prove LDPC will help; it makes coding-gap measurement worthwhile before exhaustively retuning modulation. First validate candidate codes in known channels, then emit actual alternate encoded waveforms in a frozen experiment. Different FEC or bit loading cannot be evaluated causally by reinterpreting an old codeword's recorded symbols.

For the longer-term capacity program, probabilistic amplitude shaping plus modern FEC can reduce the gap to Gaussian-input performance and provide finer rate selection. [Bocherer, Steiner, and Schulte](https://mediatum.ub.tum.de/doc/1280039/1280039.pdf) report approximately 1.1 dB or less gap in their AWGN setting. That is an existence result for a different model, not an acoustic gain forecast. Shaping changes waveform moments, peak behavior, and matching overhead; defer it until distortion and code gaps are separately measured. Polar codes for short control messages and spatially coupled LDPC for long streams are options, not simultaneous implementation requirements. [Spatial coupling results](https://arxiv.org/abs/1301.6111) concern specified channel classes and asymptotics.

**Make adaptation maximize verified session throughput.** Select the band, code rate, constellation, CP, symbol duration, training density, and burst length jointly from a small calibrated profile set. Learn from prior probes and acknowledgments, record confidence and estimate age, and back off quickly on unexpected loss. Start with uniform profiles or coarse subbands; fine per-bin maps need enough gain to pay their descriptor and estimation costs. The [8d audit](../spikes/spike-8d-effective-sinr-20260718.md) correctly declined to fabricate alternate-waveform results from the current uniform-profile corpus.

Optimize delivered, ordered, verified information divided by all elapsed session time. Include robust-control airtime, acknowledgments, device turnaround, startup/tail behavior, sounder costs, repair attempts, and final verification. Implement selective-repeat ARQ and then compare incremental-redundancy HARQ if supported by the coding layout. Bundle acknowledgments when the reverse bearer is slow. Decouple the continuous audio session, physical training interval, FEC codeword, and application message: a longer bulk transfer need not use one increasingly stale channel estimate.

The physical objectives vary:

| Situation | Appropriate optimization focus |
|---|---|
| Static, close, good direct path | Dense reliable signaling, coding efficiency, modest training overhead |
| Moderate distance or spectral nulls | Receiver diversity, reliable band/profile selection, regularized equalization |
| Long reverberation | Channel shortening, adjacent-symbol equalization, measured guard/FFT tradeoff |
| Handheld motion | Time-scale tracking, fresh training, shorter coherent blocks, fast recovery |
| Phase-unstable or heavily shadowed path | Soft noncoherent MFSK/CSS/DSSS with an explicit outage envelope |
| Slow reverse direction | Asymmetric scheduling and compact reliable feedback |

Distance alone cannot determine a profile. Free-field spreading reduces direct-path power by approximately 6 dB for each doubling of distance, but indoor reflections, directionality, absorption, contact paths, and background noise change both the power and its usefulness. A near-field cloth placement cannot be extrapolated using only inverse-square loss.

**The strongest alternative waveform candidate is single-carrier signaling with capable equalization.** Compare pulse-shaped single-carrier QAM with adaptive phase-coherent DFE or block frequency-domain equalization against improved OFDM. It can offer a different peak-power and tracking tradeoff. Its disadvantages include equalizer convergence, residual noise enhancement, error propagation in DFE, and less convenient independent frequency loading. SC-FDE and a particular localized DFT-spread OFDM construction are related but are not interchangeable experiments.

Relevant primary work includes [Tabak, Lin, and Singer](https://arxiv.org/pdf/2103.11261), who use phase-coherent DFE with commodity laptops. Their 4 kbps is a signaling rate; their table reports BER as high as 8–10% in some 3–5 m settings, so it should not be described as universally verified 4 kbps payload delivery. [Yamamoto and Kubo](https://www.jstage.jst.go.jp/article/comex/11/3/11_2021XBL0206/_article/-char/en/) report 32 kbps information-bit transmission using single-carrier block equalization, precoding, and tracking under handheld conditions. This supports a comparison, not transfer of their rate or setup to Cyrinx.

For a robust control/fallback comparison, [Google Nearby's published design](https://getreuer.info/papers/getreuer2018ultrasonic/index.html) uses DSSS at 94.5 raw bps, with reliable operation at 2 m and operation often possible at 10 m. [BatNet](https://arxiv.org/abs/2008.00136) reports over 600 bps at up to 6 m using ultrasonic PSK. These results also caution against generalizing the tested Pixel/iPhone ultrasonic failure to every phone route. They do not show that a low-rate waveform can replace wideband OFDM at its current throughput.

**True MIMO offers additional physical dimensions only when measured.** Sound all speaker/microphone paths within one phase-continuous acquisition, measure noise-whitened singular values and their time stability, and compare at fixed total transmit power. Two microphones receiving one stream provide diversity/array gain, not automatically two information streams. Blind duplicate-speaker drive is also not spatial multiplexing. The [Rank 11a audit](../../scratch/rank11a_mimo/README.md) found no retained acquisition sufficient to establish the needed matrix.

Start with best-speaker selection and single-stream combining. Add transmit beamforming or diversity where channel knowledge remains fresh. Add a second stream only on frequencies where its measured achievable rate survives the power split and training cost. Indoor multipath can create useful rank, but may also make the estimates expensive to track. Reciprocity of the air path does not imply calibrated reciprocity of different speaker/microphone electronics. A small feasibility sounder may be worthwhile early; a full MIMO modem should remain conditional.

**Recent techniques do not remove the measurement requirement.** OTFS/AFDM and delay-Doppler processing merit consideration if measured residual delay/Doppler coupling remains the dominant impairment after a capable baseline. Their transforms do not create extra bandwidth or remove training, equalization, and finite-frame costs. For example, a [2025 OTFS acoustic study](https://robot.sia.cn/en/article/doi/10.13973/j.cnki.robot.250151?viewType=HTML) is a simulated underwater comparison, not commodity in-air validation.

Neural channel estimators may be useful as constrained estimator components once a diverse corpus exists. [A 2025 deep-image-prior study](https://ieeexplore.ieee.org/document/11104655/) evaluates an underwater dataset; lower channel-estimation MSE there does not demonstrate higher byte-verified phone throughput. Preserve a classical control and test whole devices/rooms held out. An end-to-end learned modem trained on one bench risks learning that bench.

Custom hardware can change the capacity envelope. [UltraComm](https://eudl.eu/pdf/10.1007/978-3-030-38819-5_12) uses an ultrasonic transmitter array and microphone nonlinearity; its reported rate/BER results are not evidence for the same result with a built-in phone speaker. [Velocity multiplexing research from 2025](https://arxiv.org/abs/2508.03010) uses vector sensing, which changes the observable channel. Such techniques may exceed a scalar pressure-only estimate, but remain subject to the capacity of the enlarged channel. Purpose-built bandwidth and transducers are a separate hardware program.

**Several appealing proposals have already fallen short here.** Preserve these outcomes rather than re-running them as new ideas:

| Candidate | Retained result | Consequence |
|---|---|---|
| Smooth pre-EQ | Primary-mic regressions and -0.956 dB aggregate p10 SINR on the external campaign | Redesign only with a new hypothesis and receiver-level objective; no blanket pre-EQ recommendation |
| Terminal channel interpolation | Best late-half interpolation lost a net 172 blocks; full-frame interpolation also regressed | A terminal estimate did not predict the intervening channel sufficiently |
| Localized DFT-spread waveform | 2.7775 dB crest-factor reduction, below the frozen 3 dB gate | No hardware gain established; threshold miss is not proof against the entire waveform family |
| Higher convolutional code rates | 64-QAM r3/4 and r5/6 lost useful goodput | Simply removing redundancy is not a reliable path upward |
| Fine loading and MIMO | Missing physically observed alternatives/common-phase matrices | Evidence gap, not an observed failure of the underlying methods |

Sources: [pre-EQ report](../../scratch/hw20k/spike_8a_preeq/README.md), [bracketing report](../../scratch/hw20k/spike8b/REPORT.md), [DFT-spread report](../../scratch/spikes/dfts_ofdm_8c/results/REPORT.md), and [negative findings](../NEGATIVE_FINDINGS.md).

**Recommended execution order.** Preserve the accepted near-field schedule as an anchor, but make new primary endpoints p10 complete-session goodput, completion probability by deadline, and reacquisition time. Include small messages and long transfers; report both directions and zero-throughput cells separately. An application target such as 99.9% successful verified transfers is distinct from 99% first-pass block recovery and needs its own validation budget.

1. Build a compact retained waveform corpus and real waveform simulator: time-varying multipath, clock scale, discontinuities, colored/correlated and impulsive noise, clipping, route gain dynamics, and actual encoder/decoder execution. Keep injected-metric transport tests separately labeled.
2. Run same-capture receiver ablations: synchronization/time-scale correction, fresh complex-channel tracking, covariance-aware diversity, and residual-interference handling. Use oracle diagnostics only to locate losses; evaluate deployable receivers separately.
3. Emit controlled candidate waveforms for modern FEC, staggered training, and the strongest single-carrier alternative. Hold power, band, reliability objective, and complete accounting fixed; never claim causal alternate-waveform results from unchanged recordings.
4. Integrate the winning receiver/coding choices into streaming endpoint execution, closed-loop profile activation, selective repeat, and bounded recovery. Calibrate adaptation on whole physical cells and freeze it before testing other devices and rooms.
5. Pursue finer loading, shaping, and MIMO only when their measured remaining gap and predicted net benefit justify the complexity. Reopen failed experiment families only through explicit new hypotheses, not post-hoc threshold changes.

This changes the research emphasis relative to the current roadmap: move distance/motion characterization and a bounded modern-FEC comparison earlier; prioritize ongoing channel tracking over retrying the failed pre-EQ and terminal-interpolation candidates; give single-carrier equalization an explicit comparison. Keep the 3.0 delivery architecture, but do not make completion of every SDK feature a prerequisite for independent PHY experiments.

The immediate defensible objective is to retain much more of the existing throughput as the channel changes. A universal rate guarantee is not supported. A future 100+ kbps near-field result is physically plausible under favorable measured information rates and an upgraded profile, but it remains a hypothesis. Closing the measured receiver/coding gap and documenting an outage envelope would constitute a stronger result than raising a peak number while increasing failures.
