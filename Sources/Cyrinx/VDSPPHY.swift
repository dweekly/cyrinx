import CCyrinx
import Foundation

#if canImport(Accelerate)
    import Accelerate
#endif

/// Errors returned by vDSP-backed ultrasonic modems.
public enum VDSPPHYError: Error, CustomStringConvertible {
    case unavailable(String)
    case invalidConfiguration(String)
    case decodeFailure(String)

    public var description: String {
        switch self {
        case .unavailable(let text):
            return text
        case .invalidConfiguration(let text):
            return text
        case .decodeFailure(let text):
            return text
        }
    }
}

/// OFDM (Turbo gear) modem configuration.
public struct VDSPOFDMConfig: Sendable {
    public var sampleRateHz: UInt32
    public var fftSize: Int
    public var cpSamples: Int
    public var bandStartHz: Float
    public var bandEndHz: Float
    public var txGainCap: Float
    public var peerDeviceSignature: UInt8
    public var peerNotchMask: [UInt8]

    public init(
        sampleRateHz: UInt32 = 48_000,
        fftSize: Int = Int(CYRINX_OFDM_FFT_SIZE),
        cpSamples: Int = 96,
        bandStartHz: Float = 18_500,
        bandEndHz: Float = 23_500,
        txGainCap: Float = 0.12,
        peerDeviceSignature: UInt8 = 0,
        peerNotchMask: [UInt8] = [UInt8](repeating: 0xFF, count: 14)
    ) {
        self.sampleRateHz = sampleRateHz
        self.fftSize = fftSize
        self.cpSamples = cpSamples
        self.bandStartHz = bandStartHz
        self.bandEndHz = bandEndHz
        self.txGainCap = txGainCap
        self.peerDeviceSignature = peerDeviceSignature
        self.peerNotchMask = peerNotchMask
    }
}

/// Differential-CSS (Robust gear) modem configuration.
public struct VDSPDCSSConfig: Sendable {
    public var sampleRateHz: UInt32
    public var symbolSamples: Int
    public var symbolBins: Int
    public var startHz: Float
    public var endHz: Float
    public var txGainCap: Float
    public var peerDeviceSignature: UInt8

    public init(
        sampleRateHz: UInt32 = 48_000,
        symbolSamples: Int = 256,
        symbolBins: Int = 256,
        startHz: Float = 18_500,
        endHz: Float = 23_500,
        txGainCap: Float = 0.12,
        peerDeviceSignature: UInt8 = 0
    ) {
        self.sampleRateHz = sampleRateHz
        self.symbolSamples = symbolSamples
        self.symbolBins = symbolBins
        self.startHz = startHz
        self.endHz = endHz
        self.txGainCap = txGainCap
        self.peerDeviceSignature = peerDeviceSignature
    }
}

/// vDSP-backed modulators and demodulators for ultrasonic waveforms.
public enum VDSPPHY {
    /// Maps payload bytes to OFDM QPSK frames and returns real-valued waveform samples.
    public static func modulateOFDMQPSK(
        payload: [UInt8],
        config: VDSPOFDMConfig = VDSPOFDMConfig()
    ) throws -> [Float] {
        #if canImport(Accelerate)
            return try OFDMQPSKModem(config: config).modulate(payload: payload)
        #else
            throw VDSPPHYError.unavailable("Accelerate/vDSP unavailable on this platform")
        #endif
    }

    /// Demodulates real-valued OFDM-QPSK waveform samples back into payload bytes.
    public static func demodulateOFDMQPSK(
        samples: [Float],
        config: VDSPOFDMConfig = VDSPOFDMConfig()
    ) throws -> [UInt8] {
        #if canImport(Accelerate)
            return try OFDMQPSKModem(config: config).demodulate(samples: samples)
        #else
            throw VDSPPHYError.unavailable("Accelerate/vDSP unavailable on this platform")
        #endif
    }

    /// Maps payload bytes to differential chirp symbols across the configured ultrasonic sweep.
    public static func modulateDCSS(
        payload: [UInt8],
        config: VDSPDCSSConfig = VDSPDCSSConfig()
    ) throws -> [Float] {
        #if canImport(Accelerate)
            return try DCSSModem(config: config).modulate(payload: payload)
        #else
            throw VDSPPHYError.unavailable("Accelerate/vDSP unavailable on this platform")
        #endif
    }

    /// Demodulates differential chirp waveform samples back into payload bytes.
    public static func demodulateDCSS(
        samples: [Float],
        config: VDSPDCSSConfig = VDSPDCSSConfig()
    ) throws -> [UInt8] {
        #if canImport(Accelerate)
            return try DCSSModem(config: config).demodulate(samples: samples)
        #else
            throw VDSPPHYError.unavailable("Accelerate/vDSP unavailable on this platform")
        #endif
    }
}

#if canImport(Accelerate)
    private struct OFDMQPSKModem {
        private let config: VDSPOFDMConfig
        private let dftForward: vDSP.DiscreteFourierTransform<Float>
        private let dftInverse: vDSP.DiscreteFourierTransform<Float>
        private let activeBins: [Int]
        private let amplitude: Float

        init(config: VDSPOFDMConfig) throws {
            try OFDMQPSKModem.validate(config: config)
            self.config = config
            dftForward = try vDSP.DiscreteFourierTransform(
                count: config.fftSize,
                direction: .forward,
                transformType: .complexComplex,
                ofType: Float.self
            )
            dftInverse = try vDSP.DiscreteFourierTransform(
                count: config.fftSize,
                direction: .inverse,
                transformType: .complexComplex,
                ofType: Float.self
            )
            activeBins = try OFDMQPSKModem.makeActiveBins(config: config)
            amplitude = min(max(config.txGainCap, 0), 0.95)
        }

        func modulate(payload: [UInt8]) throws -> [Float] {
            let bits = BitPacking.encodeLengthPrefixed(payload)
            let qpskSymbols = mapBitsToQPSK(bits)
            // Pack QPSK symbols into OFDM symbols; each frame consumes activeBins.count points.
            let frameCount = Int(ceil(Double(qpskSymbols.count) / Double(activeBins.count)))
            var output: [Float] = []
            output.reserveCapacity(frameCount * (config.fftSize + config.cpSamples))

            for frameIndex in 0..<frameCount {
                let start = frameIndex * activeBins.count
                let end = min(start + activeBins.count, qpskSymbols.count)
                let frameSymbols = Array(qpskSymbols[start..<end])
                let waveform = try modulateFrame(symbols: frameSymbols)
                output.append(contentsOf: waveform)
            }
            return output
        }

        func demodulate(samples: [Float]) throws -> [UInt8] {
            let symbolLength = config.fftSize + config.cpSamples
            guard samples.count >= symbolLength else {
                throw VDSPPHYError.decodeFailure("not enough samples for OFDM demod")
            }

            let frameCount = samples.count / symbolLength
            var bits: [UInt8] = []
            bits.reserveCapacity(frameCount * activeBins.count * 2)

            for frameIndex in 0..<frameCount {
                let start = (frameIndex * symbolLength) + config.cpSamples
                let end = start + config.fftSize
                let frame = Array(samples[start..<end])
                bits.append(contentsOf: try demodulateFrame(frame))
            }

            return try BitPacking.decodeLengthPrefixed(bits: bits)
        }

        private func modulateFrame(symbols: [UInt8]) throws -> [Float] {
            let n = config.fftSize
            var re = [Float](repeating: 0, count: n)
            var im = [Float](repeating: 0, count: n)
            fillSpectrum(symbols: symbols, real: &re, imag: &im)

            var timeRe = [Float](repeating: 0, count: n)
            var timeIm = [Float](repeating: 0, count: n)
            dftInverse.transform(
                inputReal: re,
                inputImaginary: im,
                outputReal: &timeRe,
                outputImaginary: &timeIm
            )

            let scaled = scaleIFFTOutput(timeRe)
            let cpStart = n - config.cpSamples
            return Array(scaled[cpStart..<n]) + scaled
        }

        private func demodulateFrame(_ frame: [Float]) throws -> [UInt8] {
            guard frame.count == config.fftSize else {
                throw VDSPPHYError.decodeFailure("invalid OFDM frame length")
            }

            var reOut = [Float](repeating: 0, count: config.fftSize)
            var imOut = [Float](repeating: 0, count: config.fftSize)
            dftForward.transform(
                inputReal: frame,
                inputImaginary: [Float](repeating: 0, count: frame.count),
                outputReal: &reOut,
                outputImaginary: &imOut
            )
            return demapQPSKBits(reOut: reOut, imOut: imOut)
        }

        private func fillSpectrum(symbols: [UInt8], real: inout [Float], imag: inout [Float]) {
            let binWidth = Float(config.sampleRateHz) / Float(config.fftSize)
            for (idx, bin) in activeBins.enumerated() {
                let symbol = idx < symbols.count ? symbols[idx] : 0
                let point = mapQPSK(symbol)

                var eqFactor = Float(1.0)
                let freq = Float(bin) * binWidth
                if config.peerDeviceSignature == 0x01 { // CYRINX_DEVICE_MACBOOK_PRO
                    let x = max(0.0, min(1.0, (freq - config.bandStartHz) / max(1.0, config.bandEndHz - config.bandStartHz)))
                    let dbBoost = 3.0 + 9.0 * x
                    eqFactor = pow(10.0, dbBoost / 20.0)
                } else if config.peerDeviceSignature == 0x02 { // CYRINX_DEVICE_PIXEL_7A
                    let x = max(0.0, min(1.0, (freq - config.bandStartHz) / max(1.0, config.bandEndHz - config.bandStartHz)))
                    let dbBoost = 3.0 + 12.0 * x
                    eqFactor = pow(10.0, dbBoost / 20.0)
                }

                real[bin] = point.re * eqFactor
                imag[bin] = point.im * eqFactor

                // Mirror conjugate bins to keep time-domain output real-valued.
                let mirror = (config.fftSize - bin) % config.fftSize
                if mirror != bin {
                    real[mirror] = point.re * eqFactor
                    imag[mirror] = -point.im * eqFactor
                }
            }
        }

        private func scaleIFFTOutput(_ values: [Float]) -> [Float] {
            // vDSP inverse DFT is unnormalized; apply FFT-size normalization and TX amplitude cap.
            let fftScale = 1.0 / Float(config.fftSize)

            var maxPeak: Float = 0.0
            for v in values {
                let absV = abs(v)
                if absV > maxPeak {
                    maxPeak = absV
                }
            }

            let scale = amplitude * fftScale
            let peakIfScaled = maxPeak * scale
            var finalScale = scale
            if peakIfScaled > 0.95 {
                finalScale = 0.95 / max(1e-7, maxPeak)
            }

            return values.map { $0 * finalScale }
        }

        private func demapQPSKBits(reOut: [Float], imOut: [Float]) -> [UInt8] {
            var bits: [UInt8] = []
            bits.reserveCapacity(activeBins.count * 2)
            for bin in activeBins {
                let symbol = demapQPSK(re: reOut[bin], im: imOut[bin])
                bits.append((symbol >> 1) & 1)
                bits.append(symbol & 1)
            }
            return bits
        }

        private static func makeActiveBins(config: VDSPOFDMConfig) throws -> [Int] {
            let binWidth = Float(config.sampleRateHz) / Float(config.fftSize)
            let start = max(1, Int(ceil(config.bandStartHz / binWidth)))
            let end = min((config.fftSize / 2) - 1, Int(floor(config.bandEndHz / binWidth)))
            guard start <= end else {
                throw VDSPPHYError.invalidConfiguration("invalid OFDM active bin range")
            }
            let fullBins = Array(start...end)
            var filteredBins: [Int] = []
            for (idx, bin) in fullBins.enumerated() {
                let byteIdx = idx / 8
                let bitIdx = idx % 8
                if byteIdx < config.peerNotchMask.count {
                    let bit = (config.peerNotchMask[byteIdx] >> (7 - bitIdx)) & 1
                    if bit == 1 {
                        filteredBins.append(bin)
                    }
                } else {
                    filteredBins.append(bin)
                }
            }
            return filteredBins
        }

        private static func validate(config: VDSPOFDMConfig) throws {
            if config.fftSize <= 0 || config.cpSamples <= 0 || config.cpSamples >= config.fftSize {
                throw VDSPPHYError.invalidConfiguration("invalid OFDM FFT/CP configuration")
            }
            if config.sampleRateHz == 0 || config.bandStartHz <= 0 || config.bandEndHz <= config.bandStartHz {
                throw VDSPPHYError.invalidConfiguration("invalid OFDM sample rate or band")
            }
        }
    }

    private struct DCSSModem {
        private let config: VDSPDCSSConfig
        private let lookup: [[Float]]

        init(config: VDSPDCSSConfig) throws {
            try DCSSModem.validate(config: config)
            self.config = config
            lookup = DCSSModem.makeShiftedLookup(config: config)
        }

        func modulate(payload: [UInt8]) throws -> [Float] {
            let framed = BitPacking.prefixLengthBytes(payload)
            var output = [Float]()
            output.reserveCapacity(framed.count * config.symbolSamples)

            for symbol in framed {
                let shifted = lookup[Int(symbol) % config.symbolBins]
                output.append(contentsOf: shifted)
            }
            return output
        }

        func demodulate(samples: [Float]) throws -> [UInt8] {
            if samples.count < config.symbolSamples {
                throw VDSPPHYError.decodeFailure("not enough samples for D-CSS demod")
            }

            let symbolCount = samples.count / config.symbolSamples
            var decoded = [UInt8]()
            decoded.reserveCapacity(symbolCount)
            for index in 0..<symbolCount {
                let start = index * config.symbolSamples
                let end = start + config.symbolSamples
                let window = Array(samples[start..<end])
                decoded.append(UInt8(bestShift(for: window)))
            }
            return try BitPacking.unprefixLengthBytes(decoded)
        }

        private func bestShift(for window: [Float]) -> Int {
            var bestShift = 0
            var bestScore: Float = -.greatestFiniteMagnitude
            for shift in 0..<config.symbolBins {
                let score = abs(vDSP.dot(window, lookup[shift]))
                if score > bestScore {
                    bestScore = score
                    bestShift = shift
                }
            }
            return bestShift
        }

        private static func makeShiftedLookup(config: VDSPDCSSConfig) -> [[Float]] {
            let base = makeBaseChirp(config: config)
            return (0..<config.symbolBins).map { shift in
                // Differential symboling is modeled by circularly shifting the base chirp.
                let rotation = shift % config.symbolSamples
                var rotated = [Float](repeating: 0, count: config.symbolSamples)
                for idx in 0..<config.symbolSamples {
                    rotated[idx] = base[(idx + rotation) % config.symbolSamples]
                }
                return rotated
            }
        }

        private static func makeBaseChirp(config: VDSPDCSSConfig) -> [Float] {
            let fs = Float(config.sampleRateHz)
            let duration = Float(config.symbolSamples) / fs
            let sweep = config.endHz - config.startHz
            let chirpRate = sweep / duration
            let gain = min(max(config.txGainCap, 0), 0.95)

            return (0..<config.symbolSamples).map { n in
                let t = Float(n) / fs
                let phase = 2.0 * Float.pi * ((config.startHz * t) + (0.5 * chirpRate * t * t))
                return gain * sin(phase)
            }
        }

        private static func validate(config: VDSPDCSSConfig) throws {
            if config.sampleRateHz == 0 || config.symbolSamples <= 0 {
                throw VDSPPHYError.invalidConfiguration("invalid D-CSS sampling configuration")
            }
            if config.symbolBins <= 0 || config.symbolBins > 256 {
                throw VDSPPHYError.invalidConfiguration("D-CSS symbol bins must be 1...256")
            }
            if config.startHz <= 0 || config.endHz <= config.startHz {
                throw VDSPPHYError.invalidConfiguration("invalid D-CSS chirp band")
            }
        }
    }

    private enum BitPacking {
        static func encodeLengthPrefixed(_ payload: [UInt8]) -> [UInt8] {
            // A 16-bit prefix allows strict frame-bound recovery during demod.
            let prefix = prefixLengthBytes(payload)
            var bits: [UInt8] = []
            bits.reserveCapacity(prefix.count * 8)
            for byte in prefix {
                for shift in (0..<8).reversed() {
                    bits.append((byte >> UInt8(shift)) & 1)
                }
            }
            return bits
        }

        static func decodeLengthPrefixed(bits: [UInt8]) throws -> [UInt8] {
            guard bits.count >= 16 else {
                throw VDSPPHYError.decodeFailure("missing length prefix")
            }

            let length = Int(bitsToUInt16(bits[0..<16]))
            let requiredBits = 16 + (length * 8)
            if bits.count < requiredBits {
                throw VDSPPHYError.decodeFailure("incomplete payload bits")
            }
            return bitsToBytes(Array(bits[16..<requiredBits]))
        }

        static func prefixLengthBytes(_ payload: [UInt8]) -> [UInt8] {
            let clamped = min(payload.count, Int(UInt16.max))
            let length = UInt16(clamped)
            let prefix = [UInt8(length >> 8), UInt8(length & 0x00FF)]
            return prefix + Array(payload.prefix(clamped))
        }

        static func unprefixLengthBytes(_ bytes: [UInt8]) throws -> [UInt8] {
            if bytes.count < 2 {
                throw VDSPPHYError.decodeFailure("missing D-CSS length prefix")
            }
            let length = (Int(bytes[0]) << 8) | Int(bytes[1])
            let required = 2 + length
            if bytes.count < required {
                throw VDSPPHYError.decodeFailure("incomplete D-CSS payload bytes")
            }
            return Array(bytes[2..<required])
        }

        private static func bitsToUInt16<S: Collection>(_ bits: S) -> UInt16 where S.Element == UInt8 {
            var value: UInt16 = 0
            for bit in bits {
                value = (value << 1) | UInt16(bit & 1)
            }
            return value
        }

        private static func bitsToBytes(_ bits: [UInt8]) -> [UInt8] {
            if bits.isEmpty {
                return []
            }

            let byteCount = bits.count / 8
            var out = [UInt8](repeating: 0, count: byteCount)
            for idx in 0..<byteCount {
                var value: UInt8 = 0
                for bitIdx in 0..<8 {
                    value = (value << 1) | (bits[(idx * 8) + bitIdx] & 1)
                }
                out[idx] = value
            }
            return out
        }
    }

    private func mapQPSK(_ symbol: UInt8) -> (re: Float, im: Float) {
        let norm: Float = 0.70710677
        switch symbol & 0x3 {
        case 0: return (norm, norm)
        case 1: return (-norm, norm)
        case 2: return (norm, -norm)
        default: return (-norm, -norm)
        }
    }

    private func demapQPSK(re: Float, im: Float) -> UInt8 {
        if re >= 0, im >= 0 { return 0 }
        if re < 0, im >= 0 { return 1 }
        if re >= 0, im < 0 { return 2 }
        return 3
    }

    private func mapBitsToQPSK(_ bits: [UInt8]) -> [UInt8] {
        if bits.isEmpty {
            return []
        }

        var symbols = [UInt8]()
        symbols.reserveCapacity((bits.count + 1) / 2)
        var index = 0
        while index < bits.count {
            let high = bits[index] & 1
            let low = (index + 1) < bits.count ? (bits[index + 1] & 1) : 0
            symbols.append((high << 1) | low)
            index += 2
        }
        return symbols
    }
#endif
