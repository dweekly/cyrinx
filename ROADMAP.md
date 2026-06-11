# Cyrinx Engineering Roadmap

This document outlines the strategic engineering roadmap for Cyrinx, stack-ranked by **Return on Investment (ROI)** in terms of likely operational impact relative to technical implementation difficulty. 

Our development priorities are organized around three key architectural pillars:
* **Pillar A: Ambient Environmental Sensing & Room Tone** (capturing room acoustics, noise profiles, and echo tails).
* **Pillar B: Capabilities & Security Handshake** (negotiating protocol versions, mic/speaker capacities, device calibration signatures, and cryptographic handshakes).
* **Pillar C: Closed-Loop Channel Sounding** (dynamic sounding, THD extraction, pilot tracking, and spatial SVD decomposition to maintain the highest quality bidirectional link).

---

## 📈 Stack-Ranked Priorities (By Return on Investment)

### Transducer Calibration Database
* **Pillar:** Pillar B (Capabilities & Handshake)
* **Description:** Maintains a local and crowdsourced database of hardware-specific frequency response profiles for popular consumer laptops and mobile devices (e.g., MacBook Pro 16", Pixel 7a, iPhone 15). The moment the capability handshake identifies the peer hardware model, the transmitter applies a pre-calculated inverse equalization filter to pre-compensate for the receiver's high-frequency roll-off.
* **Likely Impact:** Extremely High. Instantly corrects sharp transducer attenuation at the ultrasonic band edges, stabilizing QAM phase constellations.
* **Technical Difficulty:** Low. Simple static database integration and a digital filter multiplication prior to transmission.
* **ROI:** **Exceptional.**

### Multi-Variable Capability & Protocol Handshake
* **Pillar:** Pillar B (Capabilities & Handshake)
* **Description:** Expands the control-plane Stream `0` capability frame (`0xE1`) payload to negotiate protocol version alignment, specific device hardware signatures (matching our calibration database), and maximum buffer/memory capacities before high-speed transmission.
* **Likely Impact:** High. Prevents frame crashes, enforces backward compatibility with mono devices, and dynamically scales resource allocations.
* **Technical Difficulty:** Low. Simple extension of the existing Stream `0` serialization and parsing logic in the core C transport session.
* **ROI:** **Very High.**

### Dynamic "Room Tone" Noise Floor Sensing
* **Pillar:** Pillar A (Ambient Sensing)
* **Description:** Prior to and between active transmissions, the receiver listens to the room's noise signature (spectral density of HVAC units, power supplies, computer fans). If a continuous ambient noise peak is detected at specific carrier frequencies, the transmitter dynamically disables (notches out) those subcarrier bins in the OFDM modulator.
* **Likely Impact:** High. Drastically reduces frame errors by dynamically steering transmission energy away from active ambient noise sources.
* **Technical Difficulty:** Low to Moderate. Requires a short background FFT capture to construct a static subcarrier mask.
* **ROI:** **High.**

### Forward Error Correction (LDPC & Soft-Decision FEC)
* **Pillar:** Pillar C (Channel Sounding)
* **Description:** Replaces simple CRC-and-retry transport mechanisms with high-performance digital error correction. Demodulated carrier phase angles are mapped to soft-decision Likelihood Ratios (LLRs) and processed through Low-Density Parity-Check (LDPC) or Polar Code decoders.
* **Likely Impact:** Extremely High. Repairs data frames corrupted by ambient clicks, keyboard taps, and transient noise bursts without incurring the latency penalty of full retransmissions.
* **Difficulty:** Moderate. Standard, highly optimized error-correction libraries can be integrated directly on the digital side.
* **ROI:** **High.**

### Reverb-Decay PDP Guard (CP) Adaptation
* **Pillar:** Pillar A (Ambient Sensing)
* **Description:** Calculates the channel's Power Delay Profile (PDP) from the Zadoff-Chu correlation peaks during the sounding phase to estimate the room's RT60 reverberation tail. If high reflections are detected, the Cyclic Prefix (CP) guard interval is dynamically lengthened; in quiet, carpeted rooms, the CP shrinks to minimize overhead.
* **Likely Impact:** High. Stabilizes OFDM demodulation in highly reflective concrete or tiled spaces while maximizing goodput in damp/quiet environments.
* **Technical Difficulty:** Moderate. Reuses existing preamble cross-correlation data to estimate decay spread.
* **ROI:** **High.**

### 96 kHz Extended Ultrasonic Bandwidth Qualification
* **Pillar:** Pillar C (Channel Sounding)
* **Description:** Programmatically tests and qualifies standard consumer microphones and speakers to operate at $96\text{ kHz}$ sampling rates. This pushes the Nyquist limit to $48\text{ kHz}$, opening up an entirely silent, wide ultrasonic band ($20\text{ kHz} - 40\text{ kHz}$) and multiplying single-channel speeds past $50\text{ kbps}$.
* **Likely Impact:** Extremely High. Multiplies throughput and ensures complete inaudibility to human ears by shifting further out of the audible spectrum.
* **Technical Difficulty:** Moderate to High. Requires physical profiling and hardware verification of high-frequency transducer roll-off on popular target devices.
* **ROI:** **Moderate-High.**

### Ultrasonic ECDH Cryptographic Handshake
* **Pillar:** Pillar B (Capabilities & Handshake)
* **Description:** Implements an out-of-band Elliptic-Curve Diffie-Hellman (ECDH) key exchange (X25519) directly inside silent ultrasonic control frames during link startup. All subsequent high-speed OFDM payloads are encrypted using ChaCha20-Poly1305.
* **Likely Impact:** High. Provides robust, air-gapped security and anti-replay windowing against active acoustic eavesdroppers.
* **Technical Difficulty:** Moderate. standard integration of cryptographic libraries on the transport layer.
* **ROI:** **Moderate.** (Essential for security, but does not directly improve signal throughput).

### Subcarrier SVD MMSE/ZF MIMO Demultiplexer
* **Pillar:** Pillar C (Channel Sounding)
* **Description:** Implements a full, subcarrier-level Minimum Mean Square Error (MMSE) or Zero-Forcing (ZF) spatial demultiplexer at the receiver to un-mix the signals $y_1(t)$ and $y_2(t)$ in real-time, enabling active dual-stream co-channel spatial multiplexing.
* **Likely Impact:** Extremely High. Dynamically doubles transmission throughput up to a theoretical peak of $24\text{ kbps}$ on stereo setups.
* **Technical Difficulty:** Very High. Demands deep mathematical and DSP restructuring of the OFDM receiver pipeline.
* **ROI:** **Moderate.**

### IMU-Assisted Doppler Tracking Loops
* **Pillar:** Pillar C (Channel Sounding)
* **Description:** Integrates the device's native IMU sensors (accelerometers/gyroscopes) to feed phase-tracking loops. If the phone is picked up or moved during a session, the feed-forward motion vector instantly rotates the subcarrier phase angles at the receiver.
* **Likely Impact:** Moderate. Prevents phase constellation collapse and frame drops under continuous high-motion events.
* **Technical Difficulty:** Very High. Requires low-latency, multi-threaded synchronization between background sensor callbacks and real-time audio loops.
* **ROI:** **Low-Moderate.**

### Seismic Haptics-to-Accelerometer Tapping Modem
* **Pillar:** Pillar A (Ambient Sensing)
* **Description:** Designs an alternative physical transport layer utilizing the phone's haptic engine (linear resonant actuator) to emit micro-vibrations that conduct through solid desk surfaces, using the receiving device's accelerometer as a seismic sensor.
* **Likely Impact:** Low to Moderate. Highly secure, completely silent, and bypasses acoustic air path loss completely.
* **Technical Difficulty:** Very High. Requires designing an entire vibrational physical layer, mapping haptic resonances, and managing seismic frequency responses.
* **ROI:** **Low.** (Possesses immense whimsical and educational value, but is limited by the requirement of solid physical contact).

### "Acoustic LAN Party" OFDMA Multi-Device Orchestration
* **Pillar:** Pillar B (Capabilities & Handshake)
* **Description:** Creates an acoustic star network topology where multiple nearby laptops and mobile devices share data over sound.
* **Likely Impact:** Low. Enables local multi-user whiteboard sessions and broadcast scenarios.
* **Technical Difficulty:** Extremely High. Requires a custom, timing-synchronized acoustic MAC layer and collision avoidance protocols.
* **ROI:** **Low.** (A fun, whimsical proof-of-concept, but highly niche compared to standard point-to-point links).

---

## 2026-06-09 Review Feedback (verbatim priorities, recorded for execution)

Source: external project review after the measured bulk-PHY result. Items
marked (in progress) are being exercised by the ultrasonic-band effort (PR #1).

### Project hardening
1. Separate claims by subsystem in README: Cyrinx transport (C/Swift framing,
   ARC, streams, ACKs, simulation) vs legacy acoustic PHY (D-CSS/vDSP/Android
   HIL) vs measured bulk PHY (scratch/hw20k + BulkDemod.kt). (partially done)
2. Promote the bulk PHY out of scratch/ into a first-class module with shared
   test vectors for Swift, Kotlin, and Python.
3. Capture-replay tests: small OTA captures + expected decode summaries so CI
   exercises chirp detection, sync, pilot tracking, Viterbi, CRC accounting
   offline.
4. Formal HIL acceptance suite: repeatable profiles (both directions,
   palm-rest and desk-distance geometry, typing transients, low volume, wrong
   mic, processed audio, clipping); pass/fail on verified bytes.
5. Unified per-run metrics JSON: SNR/band, EVM/symbol, verified+failed blocks,
   chirp starts, clock offset, CP margin, goodput, airtime, decode CPU time.
6. Integrate bulk PHY with transport as two planes: robust low-rate control
   (discovery, capabilities, feedback, security) + high-rate bulk data plane.
7. Document hardware limits plainly: throughput came from abandoning
   inaudibility, wide audible bandwidth, fixed geometry, asymmetric bands.
   (done in docs/ACOUSTIC_BULK_PHY.md SS7)

### Algorithmic directions (highest ROI = better channel-state use)
1. Fast-acquisition superframe: chirp + known symbols + robust control payload
   + pilot structure -> lock, channel estimate, first MCS < 1 s.
2. Per-bin adaptive bit loading off/1/2/4/6/8 bits with margin. (in progress)
3. Adaptive coding rate paired with loading; keep conv code until it is the
   measured bottleneck before considering LDPC/Polar.
4. Generalize pilot-driven reliability weighting: per-symbol + per-bin +
   burst-erasure flags + transient classifier from pilot residuals.
5. Dynamic CP / window bias from measured PDP per session. (in progress)
6. Block-level ARQ / incremental parity on CRC blocks, not frame retries.
7. Two-mic maximal-ratio combining (easier than MIMO, immediate robustness).
8. True 2x2 MIMO later with per-subcarrier channel estimates.
9. Continuous environment sensing: ambient PSD notch masks between bursts +
   pilot-EVM transient inference during frames.
10. Asymmetric link profiles negotiated by the control plane. (in progress)

### Distinctive direction
Environment-adaptive acoustic OFDM scheduler: receiver continuously estimates
per-bin SNR, per-symbol reliability, clock drift, delay spread, transients;
transmitter chooses bins, QAM order, FEC rate, CP, interleaver depth per
burst. Standard ingredients; the closed-loop adaptation to consumer-device
acoustics with measured pilot health and block-level verification is the
novel contribution to aim at.

### Exploration: pleasant-sounding audible modes
Investigate data-over-audio waveforms that occupy the **audible** band but are
*designed not to sound unpleasant* — trading bitrate for a benign or even
musical timbre. Ideas: map symbols onto consonant chord tones / a pentatonic
scale instead of dense OFDM hiss; shape the spectrum to pink/ambient noise that
reads as "background"; hide payload under a melodic carrier (à la dial-up-as-
music, or ggwave's "audible" presets but tuned for aesthetics); psychoacoustic
masking so the data energy sits under a pleasant cover sound. Goal: a mode a
user would tolerate (or enjoy) playing aloud in a shared room — even at much
lower goodput than the wideband bulk PHY. Cross-reference the crypto tradeoff
(docs/CRYPTO_TRADEOFF.md): low-rate modes pay proportionally more envelope
overhead, so "pleasant + authenticated" is its own design point.
