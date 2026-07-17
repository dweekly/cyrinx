import CCyrinx
import Foundation

/// Swift binding for the portable-C wideband bulk PHY (`cyrinx_bulk`).
///
/// The default configuration preserves the Cyrinx 1.x control geometry. Use a
/// route-specific Cyrinx 2.0 factory only after staging and validating that
/// physical route. The DSP lives in C and is validated against the golden
/// vectors; no DSP lives in this wrapper. Android's Kotlin demodulator is a
/// separate legacy implementation.
///
/// See docs/PUBLICATION.md (Phase 1) and docs/ACOUSTIC_BULK_PHY.md.
public struct BulkPHY: Sendable {
    /// Uniform bit-loading configuration (every data subcarrier carries
    /// `bitsPerBin` bits). Mixed per-bin loading arrives with the adaptive
    /// sounder. Defaults to the stable Cyrinx 1.x 16-QAM r3/4 control profile.
    public struct Configuration: Sendable {
        public var lowFrequencyHz: Double
        public var highFrequencyHz: Double
        public var pilotEvery: Int
        public var bitsPerBin: Int
        public var rate: String
        public var symbolCount: Int
        public var fftSize: Int
        public var cyclicPrefix: Int
        public var sampleRate: Int
        public var amplitude: Double
        public var clipSigma: Double
        public var chirpF0: Double
        public var chirpF1: Double

        public init(
            lowFrequencyHz: Double = 1100.0,
            highFrequencyHz: Double = 23000.0,
            pilotEvery: Int = 8,
            bitsPerBin: Int = 4,
            rate: String = "3/4",
            symbolCount: Int = 64,
            fftSize: Int = 2048,
            cyclicPrefix: Int = 768,
            sampleRate: Int = 48000,
            amplitude: Double = 0.5,
            clipSigma: Double = 3.3,
            chirpF0: Double = 2000.0,
            chirpF1: Double = 16000.0
        ) {
            self.lowFrequencyHz = lowFrequencyHz
            self.highFrequencyHz = highFrequencyHz
            self.pilotEvery = pilotEvery
            self.bitsPerBin = bitsPerBin
            self.rate = rate
            self.symbolCount = symbolCount
            self.fftSize = fftSize
            self.cyclicPrefix = cyclicPrefix
            self.sampleRate = sampleRate
            self.amplitude = amplitude
            self.clipSigma = clipSigma
            self.chirpF0 = chirpF0
            self.chirpF1 = chirpF1
        }

        /// Creates the Moto G 2026 near-field high-goodput profile.
        ///
        /// The preset uses CP 240, pilots every 8 bins, 16-QAM, rate-3/4
        /// convolutional coding, and 64 data symbols over 1.1–23 kHz at 48 kHz.
        /// Its measured 46.915 kbit/s one-frame payload rate applies only to the
        /// documented clean-contact Mac-to-Moto cell. The retained result was
        /// 225/225 blocks over three independent frames; it was not a prospective
        /// reliability campaign. This factory does not establish that a new route
        /// can sustain the profile or that the supplied level is acoustically safe.
        ///
        /// - Parameters:
        ///   - amplitude: The route-specific digital amplitude selected after
        ///     clipping and distortion checks.
        ///   - clipSigma: The post-waveform peak-clipping threshold.
        /// - Returns: The measured Moto G 2026 near-field profile configuration.
        public static func makeMotoG2026NearFieldHighGoodputProfile(
            amplitude: Double,
            clipSigma: Double = 3.3
        ) -> Self {
            Self(
                lowFrequencyHz: 1100.0,
                highFrequencyHz: 23000.0,
                pilotEvery: 8,
                bitsPerBin: 4,
                rate: "3/4",
                symbolCount: 64,
                fftSize: 2048,
                cyclicPrefix: 240,
                sampleRate: 48000,
                amplitude: amplitude,
                clipSigma: clipSigma,
                chirpF0: 2000.0,
                chirpF1: 16000.0
            )
        }

        /// Creates the schedule-comparable Pixel 7a near-field flagship profile.
        ///
        /// The preset uses CP 96, pilots every 16 bins, 64-QAM, rate-2/3
        /// convolutional coding, and 64 data symbols over 1.1–23 kHz at 48 kHz.
        /// Its measured 65.875 kbit/s eight-pair mean applies only to the
        /// documented Mac-to-Pixel bench cell with four 12,000-sample gaps
        /// between five frames. That campaign recovered 4,215/4,280 blocks
        /// (98.481%) and failed its baseline-equivalent reliability gate.
        /// Reproducing the receiver policy requires two sample-aligned microphone
        /// channels and `decode(_:automaticallyCombining:)`; this configuration
        /// alone does not enable diversity. Pass Pixel capture channel 0 as the
        /// primary `samples` argument and channel 1 as `second`; the primary
        /// channel drives synchronization and the mono fallback. Inter-frame
        /// scheduling is outside the PHY configuration, so callers must separately
        /// reproduce the measured gap schedule. This factory does not establish
        /// that a new route can sustain the profile or that the supplied level is
        /// acoustically safe.
        ///
        /// - Parameters:
        ///   - amplitude: The route-specific digital amplitude selected after
        ///     clipping and distortion checks.
        ///   - clipSigma: The post-waveform peak-clipping threshold.
        /// - Returns: The measured Pixel 7a schedule-comparable configuration.
        public static func makePixel7aNearFieldFlagshipProfile(
            amplitude: Double,
            clipSigma: Double = 3.3
        ) -> Self {
            Self(
                lowFrequencyHz: 1100.0,
                highFrequencyHz: 23000.0,
                pilotEvery: 16,
                bitsPerBin: 6,
                rate: "2/3",
                symbolCount: 64,
                fftSize: 2048,
                cyclicPrefix: 96,
                sampleRate: 48000,
                amplitude: amplitude,
                clipSigma: clipSigma,
                chirpF0: 2000.0,
                chirpF1: 16000.0
            )
        }

        /// Creates the peak-goodput Pixel 7a near-field research profile.
        ///
        /// The preset uses CP 96, pilots every 64 bins, 64-QAM, rate-2/3
        /// convolutional coding, and 96 data symbols over 1.1–23 kHz at 48 kHz.
        /// Its measured 69.652 kbit/s eight-pair confirmatory mean applies only
        /// to the documented Mac-to-Pixel zero-gap bench cell. That campaign
        /// recovered 6,129/6,760 blocks (90.666%), failed its baseline-equivalent
        /// reliability gate, and selected two-microphone MRC for 26/40 candidate
        /// frames. Reproducing the receiver policy therefore requires two
        /// sample-aligned microphone channels and
        /// `decode(_:automaticallyCombining:)`; this configuration alone does
        /// not enable diversity. Pass Pixel capture channel 0 as the primary
        /// `samples` argument and channel 1 as `second`; the primary channel
        /// drives synchronization and the mono fallback. Inter-frame scheduling
        /// is also outside this PHY configuration, so callers must separately
        /// select the zero-gap burst schedule used by that measurement. This
        /// factory does not establish that a new route can sustain the profile or
        /// that the supplied level is acoustically safe.
        ///
        /// - Parameters:
        ///   - amplitude: The route-specific digital amplitude selected after
        ///     clipping and distortion checks.
        ///   - clipSigma: The post-waveform peak-clipping threshold.
        /// - Returns: The measured Pixel 7a peak-goodput research configuration.
        public static func makePixel7aNearFieldPeakGoodputProfile(
            amplitude: Double,
            clipSigma: Double = 3.3
        ) -> Self {
            Self(
                lowFrequencyHz: 1100.0,
                highFrequencyHz: 23000.0,
                pilotEvery: 64,
                bitsPerBin: 6,
                rate: "2/3",
                symbolCount: 96,
                fftSize: 2048,
                cyclicPrefix: 96,
                sampleRate: 48000,
                amplitude: amplitude,
                clipSigma: clipSigma,
                chirpF0: 2000.0,
                chirpF1: 16000.0
            )
        }

        /// Creates an earlier experimental Cyrinx 2.0 candidate.
        ///
        /// A recovered, unversioned bench narrative reports 83.708 kbit/s for
        /// this CP96/p16/64-QAM/r5/6 geometry, but its ignored manifest and raw
        /// bundle are missing. The result also failed its baseline-equivalent
        /// block-success gate. This constructor remains available only to avoid
        /// a silent source-compatible behavior change. Do not treat it as a
        /// durable measured device profile.
        ///
        /// - Parameters:
        ///   - amplitude: The route-specific digital amplitude selected after
        ///     clipping and distortion checks.
        ///   - clipSigma: The post-waveform peak-clipping threshold.
        /// - Returns: The unsupported experimental profile configuration.
        @available(
            *, deprecated,
            message: "Evidence-limited historical candidate; use a retained route-specific profile."
        )
        public static func makeCyrinx2Fast(
            amplitude: Double,
            clipSigma: Double = 3.3
        ) -> Self {
            Self(
                lowFrequencyHz: 1100.0,
                highFrequencyHz: 23000.0,
                pilotEvery: 16,
                bitsPerBin: 6,
                rate: "5/6",
                symbolCount: 64,
                fftSize: 2048,
                cyclicPrefix: 96,
                sampleRate: 48000,
                amplitude: amplitude,
                clipSigma: clipSigma,
                chirpF0: 2000.0,
                chirpF1: 16000.0
            )
        }
    }

    /// Derived frame geometry: how many payload bytes a frame carries and how
    /// many samples it spans.
    public struct Geometry: Sendable {
        public let payloadBytes: Int
        public let blockCount: Int
        public let frameSamples: Int
        public let bitsPerSymbol: Int
        public let usedBins: Int
    }

    /// A receiver selected by automatic two-microphone diversity.
    public enum DiversityReceiver: Sendable, Equatable {
        /// The first, acquisition-driving microphone by itself.
        case primary
        /// Both microphones combined per subcarrier by maximal-ratio combining.
        case maximalRatioCombined
    }

    /// The reason automatic diversity selected its receiver.
    public enum AutomaticDiversityReason: Sendable, Equatable {
        /// The selector did not run to a scoring decision.
        case notEvaluated
        /// Held-out pilots showed the required MRC improvement.
        case mrcImproved
        /// MRC did not clear the frozen improvement margin.
        case primaryMarginNotMet
        /// The configuration did not provide enough train/holdout pilots.
        case insufficientPilots
        /// At least one held-out score was not finite.
        case nonfiniteScore
        /// Temporary scoring storage could not be allocated.
        case resourceFailure
        /// No secondary channel was supplied, so the primary receiver was used.
        case secondUnavailable
    }

    /// Payload-independent evidence from automatic diversity policy v1.
    public struct AutomaticDiversityDiagnostics: Sendable {
        /// The C diagnostics structure size written by the codec.
        public let structureSize: Int
        /// The diagnostics layout version written by the codec.
        public let abiVersion: Int
        /// The immutable policy version used for selection.
        public let policyVersion: Int
        /// The receiver selected before payload demapping and FEC decoding.
        public let selectedReceiver: DiversityReceiver
        /// Whether both held-out pilot scores were finite and nonnegative.
        public let hasValidScores: Bool
        /// The reason the policy selected its receiver.
        public let reason: AutomaticDiversityReason
        /// The number of held-out known-pilot observations in each score.
        public let validationObservations: Int
        /// The primary receiver's held-out pilot root-mean-square error.
        public let primaryHoldoutPilotRMS: Double
        /// The MRC receiver's held-out pilot root-mean-square error.
        public let mrcHoldoutPilotRMS: Double
        /// The measured MRC-to-primary held-out pilot RMS ratio.
        public let observedMRCToPrimaryPilotRMSRatio: Double
        /// The strict upper bound MRC must beat to be selected.
        public let maximumMRCToPrimaryPilotRMSRatio: Double
    }

    /// Result of decoding a captured frame.
    public struct Decoded: Sendable {
        public let payload: Data
        public let blocksOK: Int
        public let blockCount: Int
        public let evmRMS: Double
        /// Automatic-diversity evidence, or nil for mono and unconditional MRC decoding.
        public let automaticDiversity: AutomaticDiversityDiagnostics?
        /// True when every CRC block validated.
        public var isComplete: Bool { blocksOK == blockCount && blockCount > 0 }
    }

    public let configuration: Configuration

    public init(configuration: Configuration = Configuration()) {
        self.configuration = configuration
    }

    /// Run `body` with a filled C config whose `rate` C-string stays valid for
    /// the call duration.
    private func withCConfig<R>(_ body: (inout cyrinx_bulk_config) -> R?) -> R? {
        let c = configuration
        guard let pilotEvery = Int32(exactly: c.pilotEvery),
            let bitsPerBin = Int32(exactly: c.bitsPerBin),
            let symbolCount = Int32(exactly: c.symbolCount),
            let fftSize = Int32(exactly: c.fftSize),
            let cyclicPrefix = Int32(exactly: c.cyclicPrefix),
            let sampleRate = Int32(exactly: c.sampleRate)
        else { return nil }
        return c.rate.withCString { rptr in
            var bc = cyrinx_bulk_config()
            bc.f_lo = c.lowFrequencyHz
            bc.f_hi = c.highFrequencyHz
            bc.pilot_every = pilotEvery
            bc.bits_per_bin = bitsPerBin
            bc.rate = rptr
            bc.n_sym = symbolCount
            bc.nfft = fftSize
            bc.cp = cyclicPrefix
            bc.sr = sampleRate
            bc.amp = c.amplitude
            bc.clip_sigma = c.clipSigma
            bc.chirp_f0 = c.chirpF0
            bc.chirp_f1 = c.chirpF1
            return body(&bc)
        }
    }

    /// The frame geometry for this configuration, or nil if the config is invalid.
    public func geometry() -> Geometry? {
        withCConfig { bc in
            var g = cyrinx_bulk_geometry()
            guard cyrinx_bulk_compute_geometry(&bc, &g) == 0 else { return nil }
            return Geometry(
                payloadBytes: Int(g.payload_bytes), blockCount: Int(g.n_blocks),
                frameSamples: Int(g.frame_samples), bitsPerSymbol: Int(g.bits_per_sym),
                usedBins: Int(g.n_used))
        }
    }

    /// Modulate exactly `geometry().payloadBytes` of payload into a mono float
    /// waveform (chirp preamble + sync + data, trailing-silence not included).
    /// Returns nil if the payload length doesn't match the frame capacity.
    public func encode(_ payload: Data) -> [Float]? {
        withCConfig { bc in
            var g = cyrinx_bulk_geometry()
            guard cyrinx_bulk_compute_geometry(&bc, &g) == 0,
                payload.count == Int(g.payload_bytes)
            else { return nil }
            var wave = [Float](repeating: 0, count: Int(g.frame_samples))
            let written = payload.withUnsafeBytes { (raw: UnsafeRawBufferPointer) -> Int in
                let p = raw.bindMemory(to: UInt8.self).baseAddress
                return cyrinx_bulk_modulate(&bc, p, payload.count, &wave, wave.count, nil)
            }
            return written == Int(g.frame_samples) ? wave : nil
        }
    }

    /// Demodulate with two sample-aligned microphone channels combined per
    /// subcarrier by maximal-ratio combining (MRC) — the diversity path
    /// measured to rescue placements where NEITHER mic decodes alone. Sync
    /// runs on `samples`; `second` is the other channel of the same capture.
    public func decode(_ samples: [Float], combining second: [Float]) -> Decoded? {
        withCConfig { bc in
            var g = cyrinx_bulk_geometry()
            guard cyrinx_bulk_compute_geometry(&bc, &g) == 0 else { return nil }
            var payload = [UInt8](repeating: 0, count: Int(g.payload_bytes))
            var ok: Int32 = 0
            var total: Int32 = 0
            var evm = 0.0
            let n = samples.withUnsafeBufferPointer { p0 in
                second.withUnsafeBufferPointer { p1 in
                    cyrinx_bulk_demodulate2(
                        &bc, p0.baseAddress, p0.count, p1.baseAddress, p1.count,
                        &payload, payload.count, &ok, &total, &evm)
                }
            }
            guard n == Int(g.payload_bytes) else { return nil }
            return Decoded(
                payload: Data(payload), blocksOK: Int(ok), blockCount: Int(total), evmRMS: evm,
                automaticDiversity: nil)
        }
    }
}

extension BulkPHY {
    /// Demodulates with automatic diversity policy v1 over two sample-aligned channels.
    ///
    /// The selector fits phase on even-ordinal known pilots and scores odd-ordinal
    /// known pilots across the full frame. It selects MRC only when its held-out
    /// RMS error is strictly below 95% of the primary receiver's error. Payload
    /// bytes, data bins, FEC output, and CRCs do not participate in selection.
    ///
    /// - Parameters:
    ///   - samples: The primary channel used for chirp and fine synchronization.
    ///   - second: The sample-aligned secondary microphone channel.
    /// - Returns: The selected receiver's decode and selection evidence, or nil
    ///   when the configuration or capture is invalid.
    public func decode(
        _ samples: [Float],
        automaticallyCombining second: [Float]
    ) -> Decoded? {
        withCConfig { bc in
            var g = cyrinx_bulk_geometry()
            guard cyrinx_bulk_compute_geometry(&bc, &g) == 0 else { return nil }
            var payload = [UInt8](repeating: 0, count: Int(g.payload_bytes))
            var ok: Int32 = 0
            var total: Int32 = 0
            var evm = 0.0
            var rawDiagnostics = cyrinx_bulk_diversity_diagnostics()
            let n = samples.withUnsafeBufferPointer { primary in
                second.withUnsafeBufferPointer { secondary in
                    let secondaryAddress = secondary.isEmpty ? nil : secondary.baseAddress
                    return cyrinx_bulk_demodulate2_auto_v1(
                        &bc, primary.baseAddress, primary.count, secondaryAddress,
                        secondary.count, &payload, payload.count, &ok, &total, &evm,
                        &rawDiagnostics)
                }
            }
            guard n == Int(g.payload_bytes),
                Int(rawDiagnostics.struct_size)
                    == MemoryLayout<cyrinx_bulk_diversity_diagnostics>.size,
                Int(rawDiagnostics.abi_version)
                    == Int(CYRINX_BULK_DIVERSITY_DIAGNOSTICS_ABI_VERSION),
                Int(rawDiagnostics.policy_version) == Int(CYRINX_BULK_AUTO_V1_POLICY_VERSION),
                let selectedReceiver = Self.diversityReceiver(
                    from: rawDiagnostics.selected_receiver),
                let reason = Self.automaticDiversityReason(
                    from: rawDiagnostics.selection_reason)
            else { return nil }
            let diagnostics = AutomaticDiversityDiagnostics(
                structureSize: Int(rawDiagnostics.struct_size),
                abiVersion: Int(rawDiagnostics.abi_version),
                policyVersion: Int(rawDiagnostics.policy_version),
                selectedReceiver: selectedReceiver,
                hasValidScores: rawDiagnostics.scores_valid == 1,
                reason: reason,
                validationObservations: Int(rawDiagnostics.validation_observations),
                primaryHoldoutPilotRMS: rawDiagnostics.primary_holdout_pilot_rms,
                mrcHoldoutPilotRMS: rawDiagnostics.mrc_holdout_pilot_rms,
                observedMRCToPrimaryPilotRMSRatio:
                    rawDiagnostics.observed_mrc_to_primary_pilot_rms_ratio,
                maximumMRCToPrimaryPilotRMSRatio:
                    rawDiagnostics.maximum_mrc_to_primary_pilot_rms_ratio)
            return Decoded(
                payload: Data(payload), blocksOK: Int(ok), blockCount: Int(total), evmRMS: evm,
                automaticDiversity: diagnostics)
        }
    }

    /// Demodulate a captured mono frame. Returns the decoded payload (whatever
    /// blocks were recovered) plus per-block CRC results and pilot EVM.
    public func decode(_ samples: [Float]) -> Decoded? {
        withCConfig { bc in
            var g = cyrinx_bulk_geometry()
            guard cyrinx_bulk_compute_geometry(&bc, &g) == 0 else { return nil }
            var payload = [UInt8](repeating: 0, count: Int(g.payload_bytes))
            var ok: Int32 = 0
            var total: Int32 = 0
            var evm = 0.0
            let n = samples.withUnsafeBufferPointer { p in
                cyrinx_bulk_demodulate(
                    &bc, p.baseAddress, p.count, &payload, payload.count, &ok, &total, &evm)
            }
            guard n == Int(g.payload_bytes) else { return nil }
            return Decoded(
                payload: Data(payload), blocksOK: Int(ok), blockCount: Int(total), evmRMS: evm,
                automaticDiversity: nil)
        }
    }

    private static func diversityReceiver(from value: Int32) -> DiversityReceiver? {
        switch value {
        case CYRINX_BULK_DIVERSITY_PRIMARY:
            return .primary
        case CYRINX_BULK_DIVERSITY_MRC:
            return .maximalRatioCombined
        default:
            return nil
        }
    }

    private static func automaticDiversityReason(from value: Int32) -> AutomaticDiversityReason? {
        switch value {
        case CYRINX_BULK_AUTO_REASON_NOT_EVALUATED:
            return .notEvaluated
        case CYRINX_BULK_AUTO_REASON_MRC_IMPROVED:
            return .mrcImproved
        case CYRINX_BULK_AUTO_REASON_PRIMARY_MARGIN_NOT_MET:
            return .primaryMarginNotMet
        case CYRINX_BULK_AUTO_REASON_INSUFFICIENT_PILOTS:
            return .insufficientPilots
        case CYRINX_BULK_AUTO_REASON_NONFINITE_SCORE:
            return .nonfiniteScore
        case CYRINX_BULK_AUTO_REASON_RESOURCE_FAILURE:
            return .resourceFailure
        case CYRINX_BULK_AUTO_REASON_SECOND_UNAVAILABLE:
            return .secondUnavailable
        default:
            return nil
        }
    }
}
