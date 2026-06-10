import CCyrinx
import Foundation

/// Swift binding for the portable-C wideband bulk PHY (`cyrinx_bulk`), the modem
/// that achieved the measured 36.6 / 27.3 kbps over-the-air result. The DSP is
/// the single C implementation (validated bit-exact / float-tolerant against the
/// golden vectors); this is a thin, ergonomic wrapper — no DSP lives here.
///
/// See docs/PUBLICATION.md (Phase 1) and docs/ACOUSTIC_BULK_PHY.md.
public struct BulkPHY: Sendable {
    /// Uniform bit-loading configuration (every data subcarrier carries
    /// `bitsPerBin` bits). Mixed per-bin loading arrives with the adaptive
    /// sounder. Defaults to the measured near-field 16-QAM r3/4 profile.
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

    /// Result of decoding a captured frame.
    public struct Decoded: Sendable {
        public let payload: Data
        public let blocksOK: Int
        public let blockCount: Int
        public let evmRMS: Double
        /// True when every CRC block validated.
        public var isComplete: Bool { blocksOK == blockCount && blockCount > 0 }
    }

    public let configuration: Configuration

    public init(configuration: Configuration = Configuration()) {
        self.configuration = configuration
    }

    /// Run `body` with a filled C config whose `rate` C-string stays valid for
    /// the call duration.
    private func withCConfig<R>(_ body: (inout cyrinx_bulk_config) throws -> R) rethrows -> R {
        let c = configuration
        return try c.rate.withCString { rptr in
            var bc = cyrinx_bulk_config()
            bc.f_lo = c.lowFrequencyHz
            bc.f_hi = c.highFrequencyHz
            bc.pilot_every = Int32(c.pilotEvery)
            bc.bits_per_bin = Int32(c.bitsPerBin)
            bc.rate = rptr
            bc.n_sym = Int32(c.symbolCount)
            bc.nfft = Int32(c.fftSize)
            bc.cp = Int32(c.cyclicPrefix)
            bc.sr = Int32(c.sampleRate)
            bc.amp = c.amplitude
            bc.clip_sigma = c.clipSigma
            bc.chirp_f0 = c.chirpF0
            bc.chirp_f1 = c.chirpF1
            return try body(&bc)
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
                payload: Data(payload), blocksOK: Int(ok), blockCount: Int(total), evmRMS: evm)
        }
    }
}
