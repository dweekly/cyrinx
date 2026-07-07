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
    /// Maps payload bytes to OFDM frames with arbitrary mode and returns real-valued waveform samples.
    public static func modulateOFDM(
        payload: [UInt8],
        mode: UInt8,
        config: VDSPOFDMConfig = VDSPOFDMConfig()
    ) throws -> [Float] {
        #if canImport(Accelerate)
            return try OFDMModem(config: config, mode: mode).modulate(payload: payload)
        #else
            throw VDSPPHYError.unavailable("Accelerate/vDSP unavailable on this platform")
        #endif
    }

    /// Demodulates real-valued OFDM waveform samples of arbitrary mode back into payload bytes.
    public static func demodulateOFDM(
        samples: [Float],
        mode: UInt8,
        config: VDSPOFDMConfig = VDSPOFDMConfig(),
        expectedLength: Int? = nil
    ) throws -> [UInt8] {
        #if canImport(Accelerate)
            return try OFDMModem(config: config, mode: mode).demodulate(
                samples: samples, expectedLength: expectedLength)
        #else
            throw VDSPPHYError.unavailable("Accelerate/vDSP unavailable on this platform")
        #endif
    }

    /// Maps payload bytes to OFDM QPSK frames and returns real-valued waveform samples.
    public static func modulateOFDMQPSK(
        payload: [UInt8],
        config: VDSPOFDMConfig = VDSPOFDMConfig()
    ) throws -> [Float] {
        return try modulateOFDM(payload: payload, mode: 1, config: config)
    }

    /// Demodulates real-valued OFDM-QPSK waveform samples back into payload bytes.
    public static func demodulateOFDMQPSK(
        samples: [Float],
        config: VDSPOFDMConfig = VDSPOFDMConfig()
    ) throws -> [UInt8] {
        return try demodulateOFDM(samples: samples, mode: 1, config: config)
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
    private struct OFDMModem {
        private let config: VDSPOFDMConfig
        private let mode: UInt8
        private let dftForward: vDSP.DiscreteFourierTransform<Float>
        private let dftInverse: vDSP.DiscreteFourierTransform<Float>
        private let activeBins: [Int]
        private let amplitude: Float
        private let bitsPerSymbol: Int

        init(config: VDSPOFDMConfig, mode: UInt8) throws {
            try OFDMModem.validate(config: config)
            self.config = config
            self.mode = mode
            switch mode {
            case 2:
                self.bitsPerSymbol = 4
            case 3:
                self.bitsPerSymbol = 6
            default:
                self.bitsPerSymbol = 2
            }
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
            activeBins = try OFDMModem.makeActiveBins(config: config)
            amplitude = min(max(config.txGainCap, 0), 0.95)
        }

        func modulate(payload: [UInt8]) throws -> [Float] {
            let bits = BitPacking.encodeLengthPrefixed(payload)
            let symbols = mapBitsToSymbols(bits: bits, bitsPerSymbol: bitsPerSymbol)
            let frameCount = Int(ceil(Double(symbols.count) / Double(activeBins.count)))
            var output: [Float] = []
            output.reserveCapacity(frameCount * (config.fftSize + config.cpSamples))

            for frameIndex in 0..<frameCount {
                let start = frameIndex * activeBins.count
                let end = min(start + activeBins.count, symbols.count)
                var frameSymbols = Array(symbols[start..<end])
                if frameSymbols.count < activeBins.count {
                    let needed = activeBins.count - frameSymbols.count
                    for i in 0..<needed {
                        frameSymbols.append(UInt8(i % (1 << bitsPerSymbol)))
                    }
                }
                let waveform = try modulateFrame(symbols: frameSymbols)
                output.append(contentsOf: waveform)
            }
            return output
        }

        func demodulate(samples: [Float], expectedLength: Int? = nil) throws -> [UInt8] {
            let symbolLength = config.fftSize + config.cpSamples
            guard samples.count >= symbolLength else {
                throw VDSPPHYError.decodeFailure("not enough samples for OFDM demod")
            }

            let frameCount = samples.count / symbolLength
            var equalizedFrames: [([Float], [Float])] = []
            equalizedFrames.reserveCapacity(frameCount)

            var sharedTau: Float? = nil
            var sharedConj: Bool? = nil

            for frameIndex in 0..<frameCount {
                let start = (frameIndex * symbolLength) + config.cpSamples
                let end = start + config.fftSize
                let frame = Array(samples[start..<end])

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
                let (eqRe, eqIm, tau, conj) = equalizeFrame(
                    re: reOut,
                    im: imOut,
                    sharedTau: sharedTau,
                    sharedConj: sharedConj
                )
                sharedTau = tau
                sharedConj = conj
                equalizedFrames.append((eqRe, eqIm))
            }

            for conj in [false, true] {
                for q in 0..<4 {
                    var bits: [UInt8] = []
                    bits.reserveCapacity(frameCount * activeBins.count * bitsPerSymbol)

                    for (eqRe, eqIm) in equalizedFrames {
                        bits.append(
                            contentsOf: demapBits(
                                reOut: eqRe,
                                imOut: eqIm,
                                rotationQuadrant: q,
                                conjugate: conj
                            ))
                    }

                    if let payload = try? BitPacking.decodeLengthPrefixed(bits: bits) {
                        if let expected = expectedLength {
                            if payload.count == expected {
                                return payload
                            }
                        } else {
                            return payload
                        }
                    }
                }
            }

            var bits: [UInt8] = []
            bits.reserveCapacity(frameCount * activeBins.count * bitsPerSymbol)
            for (eqRe, eqIm) in equalizedFrames {
                bits.append(
                    contentsOf: demapBits(
                        reOut: eqRe,
                        imOut: eqIm,
                        rotationQuadrant: 0,
                        conjugate: false
                    ))
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

        private func fillSpectrum(symbols: [UInt8], real: inout [Float], imag: inout [Float]) {
            let binWidth = Float(config.sampleRateHz) / Float(config.fftSize)
            for (idx, bin) in activeBins.enumerated() {
                let symbol = idx < symbols.count ? symbols[idx] : 0
                let point: (re: Float, im: Float)
                switch bitsPerSymbol {
                case 4:
                    point = map16QAM(symbol)
                case 6:
                    point = map64QAM(symbol)
                default:
                    point = mapQPSK(symbol)
                }

                var eqFactor = Float(1.0)
                let freq = Float(bin) * binWidth
                if config.peerDeviceSignature == 0x01 {  // CYRINX_DEVICE_MACBOOK_PRO
                    let x = max(
                        0.0,
                        min(
                            1.0, (freq - config.bandStartHz) / max(1.0, config.bandEndHz - config.bandStartHz)
                        ))
                    let dbBoost = 3.0 + 9.0 * x
                    eqFactor = pow(10.0, dbBoost / 20.0)
                } else if config.peerDeviceSignature == 0x02 {  // CYRINX_DEVICE_PIXEL_7A
                    let x = max(
                        0.0,
                        min(
                            1.0, (freq - config.bandStartHz) / max(1.0, config.bandEndHz - config.bandStartHz)
                        ))
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
            var maxPeak: Float = 0.0
            for v in values {
                let absV = abs(v)
                if absV > maxPeak {
                    maxPeak = absV
                }
            }

            var finalScale: Float = 0.0
            if maxPeak > 1e-7 {
                finalScale = amplitude / maxPeak
            }

            return values.map { $0 * finalScale }
        }

        private func demapBits(
            reOut: [Float],
            imOut: [Float],
            rotationQuadrant: Int,
            conjugate: Bool = false
        ) -> [UInt8] {
            var bits: [UInt8] = []
            bits.reserveCapacity(activeBins.count * bitsPerSymbol)

            var totalEnergy: Float = 0.0
            for bin in activeBins {
                let re = reOut[bin]
                let im = imOut[bin]
                totalEnergy += re * re + im * im
            }
            let avgEnergy = totalEnergy / Float(max(1, activeBins.count))
            let normFactor = sqrt(max(avgEnergy, 1e-7))

            for bin in activeBins {
                var reNorm = reOut[bin] / normFactor
                var imNorm = imOut[bin] / normFactor

                if conjugate {
                    imNorm = -imNorm
                }

                switch rotationQuadrant {
                case 1:  // 90 degrees CCW: (x, y) -> (-y, x)
                    let tmp = reNorm
                    reNorm = -imNorm
                    imNorm = tmp
                case 2:  // 180 degrees: (x, y) -> (-x, -y)
                    reNorm = -reNorm
                    imNorm = -imNorm
                case 3:  // 270 degrees CCW: (x, y) -> (y, -x)
                    let tmp = reNorm
                    reNorm = imNorm
                    imNorm = -tmp
                default:
                    break
                }

                let symbol: UInt8
                switch bitsPerSymbol {
                case 4:
                    symbol = demap16QAM(re: reNorm, im: imNorm)
                case 6:
                    symbol = demap64QAM(re: reNorm, im: imNorm)
                default:
                    symbol = demapQPSK(re: reNorm, im: imNorm)
                }
                for shift in (0..<bitsPerSymbol).reversed() {
                    bits.append((symbol >> UInt8(shift)) & 1)
                }
            }
            return bits
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

        private func map16QAM(_ symbol: UInt8) -> (re: Float, im: Float) {
            let d: Float = 0.31622777
            let b = symbol & 0xF
            let i_bits = b & 0x3
            let q_bits = (b >> 2) & 0x3

            let re: Float
            switch i_bits {
            case 0: re = -3.0 * d
            case 1: re = -1.0 * d
            case 3: re = 1.0 * d
            default: re = 3.0 * d
            }

            let im: Float
            switch q_bits {
            case 0: im = -3.0 * d
            case 1: im = -1.0 * d
            case 3: im = 1.0 * d
            default: im = 3.0 * d
            }

            return (re, im)
        }

        private func demap16QAM(re: Float, im: Float) -> UInt8 {
            let d: Float = 0.31622777
            let i_val: UInt8
            if re < -2.0 * d {
                i_val = 0
            } else if re < 0.0 {
                i_val = 1
            } else if re < 2.0 * d {
                i_val = 3
            } else {
                i_val = 2
            }

            let q_val: UInt8
            if im < -2.0 * d {
                q_val = 0
            } else if im < 0.0 {
                q_val = 1
            } else if im < 2.0 * d {
                q_val = 3
            } else {
                q_val = 2
            }

            return i_val | (q_val << 2)
        }

        private func map64QAM(_ symbol: UInt8) -> (re: Float, im: Float) {
            let d: Float = 0.15430335
            let b = symbol & 0x3F
            let i_bits = b & 0x7
            let q_bits = (b >> 3) & 0x7

            let re: Float
            switch i_bits {
            case 0: re = -7.0 * d
            case 1: re = -5.0 * d
            case 2: re = -3.0 * d
            case 3: re = -1.0 * d
            case 4: re = 1.0 * d
            case 5: re = 3.0 * d
            case 6: re = 5.0 * d
            default: re = 7.0 * d
            }

            let im: Float
            switch q_bits {
            case 0: im = -7.0 * d
            case 1: im = -5.0 * d
            case 2: im = -3.0 * d
            case 3: im = -1.0 * d
            case 4: im = 1.0 * d
            case 5: im = 3.0 * d
            case 6: im = 5.0 * d
            default: im = 7.0 * d
            }

            return (re, im)
        }

        private func demap64QAM(re: Float, im: Float) -> UInt8 {
            let d: Float = 0.15430335
            let i_val: UInt8
            if re < -6.0 * d {
                i_val = 0
            } else if re < -4.0 * d {
                i_val = 1
            } else if re < -2.0 * d {
                i_val = 2
            } else if re < 0.0 {
                i_val = 3
            } else if re < 2.0 * d {
                i_val = 4
            } else if re < 4.0 * d {
                i_val = 5
            } else if re < 6.0 * d {
                i_val = 6
            } else {
                i_val = 7
            }

            let q_val: UInt8
            if im < -6.0 * d {
                q_val = 0
            } else if im < -4.0 * d {
                q_val = 1
            } else if im < -2.0 * d {
                q_val = 2
            } else if im < 0.0 {
                q_val = 3
            } else if im < 2.0 * d {
                q_val = 4
            } else if im < 4.0 * d {
                q_val = 5
            } else if im < 6.0 * d {
                q_val = 6
            } else {
                q_val = 7
            }

            return i_val | (q_val << 3)
        }

        private func mapBitsToSymbols(bits: [UInt8], bitsPerSymbol: Int) -> [UInt8] {
            if bits.isEmpty {
                return []
            }
            var symbols = [UInt8]()
            symbols.reserveCapacity((bits.count + bitsPerSymbol - 1) / bitsPerSymbol)
            var index = 0
            while index < bits.count {
                var symbol: UInt8 = 0
                for _ in 0..<bitsPerSymbol {
                    let bit = (index < bits.count) ? (bits[index] & 1) : 0
                    symbol = (symbol << 1) | bit
                    index += 1
                }
                symbols.append(symbol)
            }
            return symbols
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

        private func equalizeFrame(
            re: [Float],
            im: [Float],
            sharedTau: Float?,
            sharedConj: Bool?
        ) -> (eqRe: [Float], eqIm: [Float], tau: Float, conj: Bool) {
            var totalEnergy: Float = 0.0
            for bin in activeBins {
                let r = re[bin]
                let i = im[bin]
                totalEnergy += r * r + i * i
            }
            let avgEnergy = totalEnergy / Float(max(1, activeBins.count))
            let normFactor = sqrt(max(avgEnergy, 1e-7))

            // Pre-divide active bins by normFactor once.
            var normRe = [Float](repeating: 0.0, count: config.fftSize)
            var normIm = [Float](repeating: 0.0, count: config.fftSize)
            for bin in activeBins {
                normRe[bin] = re[bin] / normFactor
                normIm[bin] = im[bin] / normFactor
            }

            // Pre-calculate bin factors: 2 * pi * bin / fftSize
            var binFactors = [Float](repeating: 0.0, count: config.fftSize)
            let multiplier = 2.0 * Float.pi / Float(config.fftSize)
            for bin in activeBins {
                binFactors[bin] = Float(bin) * multiplier
            }

            var bestTau: Float = 0.0
            var bestTheta: Float = 0.0
            var bestConj = false
            var minMSE: Float = Float.greatestFiniteMagnitude

            if let sTau = sharedTau, let sConj = sharedConj {
                // Subsequent frame: use sharedTau and sharedConj, only search theta
                bestTau = sTau
                bestConj = sConj
                for thetaIdx in 0...16 {
                    let theta = -Float.pi / 4.0 + (Float(thetaIdx) * Float.pi / 32.0)
                    var sumError: Float = 0.0

                    for bin in activeBins {
                        let r = normRe[bin]
                        let i = bestConj ? -normIm[bin] : normIm[bin]

                        let phi = theta + binFactors[bin] * bestTau
                        let cosPhi = cos(phi)
                        let sinPhi = sin(phi)

                        let rRot = r * cosPhi + i * sinPhi
                        let iRot = i * cosPhi - r * sinPhi

                        let err: Float
                        switch bitsPerSymbol {
                        case 4:
                            let d: Float = 0.31622777
                            let absR = abs(rRot)
                            let errR = absR < 2.0 * d ? abs(absR - d) : abs(absR - 3.0 * d)
                            let absI = abs(iRot)
                            let errI = absI < 2.0 * d ? abs(absI - d) : abs(absI - 3.0 * d)
                            err = errR * errR + errI * errI
                        case 6:
                            let d: Float = 0.15430335
                            let absR = abs(rRot)
                            let errR: Float
                            if absR < 2.0 * d {
                                errR = abs(absR - d)
                            } else if absR < 4.0 * d {
                                errR = abs(absR - 3.0 * d)
                            } else if absR < 6.0 * d {
                                errR = abs(absR - 5.0 * d)
                            } else {
                                errR = abs(absR - 7.0 * d)
                            }

                            let absI = abs(iRot)
                            let errI: Float
                            if absI < 2.0 * d {
                                errI = abs(absI - d)
                            } else if absI < 4.0 * d {
                                errI = abs(absI - 3.0 * d)
                            } else if absI < 6.0 * d {
                                errI = abs(absI - 5.0 * d)
                            } else {
                                errI = abs(absI - 7.0 * d)
                            }
                            err = errR * errR + errI * errI
                        default:
                            let norm: Float = 0.70710677
                            let errR = abs(rRot) - norm
                            let errI = abs(iRot) - norm
                            err = errR * errR + errI * errI
                        }
                        sumError += err
                    }

                    let penalty = 0.0001 * (bestTau * bestTau) + 0.00005 * (theta * theta)
                    let score = sumError + penalty
                    if score < minMSE {
                        minMSE = score
                        bestTheta = theta
                    }
                }
            } else {
                // First frame: Hierarchical coarse-to-fine search
                let tauCoarse: [Float] = [-8.0, -6.0, -4.0, -2.0, 0.0, 2.0, 4.0, 6.0, 8.0]
                let thetaCoarseIdx: [Int] = [0, 4, 8, 12, 16]

                var bestCoarseTau: Float = 0.0
                var bestCoarseThetaIdx: Int = 8
                var bestCoarseConj = false
                var minCoarseMSE: Float = Float.greatestFiniteMagnitude

                for conj in [false, true] {
                    for tau in tauCoarse {
                        for thetaIdx in thetaCoarseIdx {
                            let theta = -Float.pi / 4.0 + (Float(thetaIdx) * Float.pi / 32.0)
                            var sumError: Float = 0.0

                            for bin in activeBins {
                                let r = normRe[bin]
                                let i = conj ? -normIm[bin] : normIm[bin]

                                let phi = theta + binFactors[bin] * tau
                                let cosPhi = cos(phi)
                                let sinPhi = sin(phi)

                                let rRot = r * cosPhi + i * sinPhi
                                let iRot = i * cosPhi - r * sinPhi

                                let err: Float
                                switch bitsPerSymbol {
                                case 4:
                                    let d: Float = 0.31622777
                                    let absR = abs(rRot)
                                    let errR = absR < 2.0 * d ? abs(absR - d) : abs(absR - 3.0 * d)
                                    let absI = abs(iRot)
                                    let errI = absI < 2.0 * d ? abs(absI - d) : abs(absI - 3.0 * d)
                                    err = errR * errR + errI * errI
                                case 6:
                                    let d: Float = 0.15430335
                                    let absR = abs(rRot)
                                    let errR: Float
                                    if absR < 2.0 * d {
                                        errR = abs(absR - d)
                                    } else if absR < 4.0 * d {
                                        errR = abs(absR - 3.0 * d)
                                    } else if absR < 6.0 * d {
                                        errR = abs(absR - 5.0 * d)
                                    } else {
                                        errR = abs(absR - 7.0 * d)
                                    }

                                    let absI = abs(iRot)
                                    let errI: Float
                                    if absI < 2.0 * d {
                                        errI = abs(absI - d)
                                    } else if absI < 4.0 * d {
                                        errI = abs(absI - 3.0 * d)
                                    } else if absI < 6.0 * d {
                                        errI = abs(absI - 5.0 * d)
                                    } else {
                                        errI = abs(absI - 7.0 * d)
                                    }
                                    err = errR * errR + errI * errI
                                default:
                                    let norm: Float = 0.70710677
                                    let errR = abs(rRot) - norm
                                    let errI = abs(iRot) - norm
                                    err = errR * errR + errI * errI
                                }
                                sumError += err
                            }

                            let penalty = 0.0001 * (tau * tau) + 0.00005 * (theta * theta)
                            let score = sumError + penalty
                            if score < minCoarseMSE {
                                minCoarseMSE = score
                                bestCoarseTau = tau
                                bestCoarseThetaIdx = thetaIdx
                                bestCoarseConj = conj
                            }
                        }
                    }
                }

                // Fine local search around the best coarse point
                let tauFine: [Float] = [
                    bestCoarseTau - 1.0, bestCoarseTau - 0.75, bestCoarseTau - 0.5, bestCoarseTau - 0.25,
                    bestCoarseTau,
                    bestCoarseTau + 0.25, bestCoarseTau + 0.5, bestCoarseTau + 0.75, bestCoarseTau + 1.0,
                ].filter { $0 >= -8.0 && $0 <= 8.0 }

                let thetaFineIdx: [Int] = [
                    bestCoarseThetaIdx - 2, bestCoarseThetaIdx - 1,
                    bestCoarseThetaIdx,
                    bestCoarseThetaIdx + 1, bestCoarseThetaIdx + 2,
                ].filter { $0 >= 0 && $0 <= 16 }

                bestConj = bestCoarseConj
                for tau in tauFine {
                    for thetaIdx in thetaFineIdx {
                        let theta = -Float.pi / 4.0 + (Float(thetaIdx) * Float.pi / 32.0)
                        var sumError: Float = 0.0

                        for bin in activeBins {
                            let r = normRe[bin]
                            let i = bestConj ? -normIm[bin] : normIm[bin]

                            let phi = theta + binFactors[bin] * tau
                            let cosPhi = cos(phi)
                            let sinPhi = sin(phi)

                            let rRot = r * cosPhi + i * sinPhi
                            let iRot = i * cosPhi - r * sinPhi

                            let err: Float
                            switch bitsPerSymbol {
                            case 4:
                                let d: Float = 0.31622777
                                let absR = abs(rRot)
                                let errR = absR < 2.0 * d ? abs(absR - d) : abs(absR - 3.0 * d)
                                let absI = abs(iRot)
                                let errI = absI < 2.0 * d ? abs(absI - d) : abs(absI - 3.0 * d)
                                err = errR * errR + errI * errI
                            case 6:
                                let d: Float = 0.15430335
                                let absR = abs(rRot)
                                let errR: Float
                                if absR < 2.0 * d {
                                    errR = abs(absR - d)
                                } else if absR < 4.0 * d {
                                    errR = abs(absR - 3.0 * d)
                                } else if absR < 6.0 * d {
                                    errR = abs(absR - 5.0 * d)
                                } else {
                                    errR = abs(absR - 7.0 * d)
                                }

                                let absI = abs(iRot)
                                let errI: Float
                                if absI < 2.0 * d {
                                    errI = abs(absI - d)
                                } else if absI < 4.0 * d {
                                    errI = abs(absI - 3.0 * d)
                                } else if absI < 6.0 * d {
                                    errI = abs(absI - 5.0 * d)
                                } else {
                                    errI = abs(absI - 7.0 * d)
                                }
                                err = errR * errR + errI * errI
                            default:
                                let norm: Float = 0.70710677
                                let errR = abs(rRot) - norm
                                let errI = abs(iRot) - norm
                                err = errR * errR + errI * errI
                            }
                            sumError += err
                        }

                        let penalty = 0.0001 * (tau * tau) + 0.00005 * (theta * theta)
                        let score = sumError + penalty
                        if score < minMSE {
                            minMSE = score
                            bestTau = tau
                            bestTheta = theta
                        }
                    }
                }
            }

            var eqRe = re
            var eqIm = im
            for bin in activeBins {
                let phi = bestTheta + binFactors[bin] * bestTau
                let cosPhi = cos(phi)
                let sinPhi = sin(phi)
                let r = re[bin]
                let i = bestConj ? -im[bin] : im[bin]
                eqRe[bin] = r * cosPhi + i * sinPhi
                eqIm[bin] = i * cosPhi - r * sinPhi
            }

            return (eqRe, eqIm, bestTau, bestConj)
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
