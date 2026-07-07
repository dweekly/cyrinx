import Foundation

#if canImport(Accelerate)
    import Accelerate
#endif

/// Background spectrum scanner that ambiently analyzes noise floors and notches out corrupted bins.
public final class AcousticNoiseScanner {
    /// Ambiently scans a noise sample buffer, computes the Power Spectral Density (PSD),
    /// runs a 51-point moving median noise-floor estimation, and yields a 14-byte subcarrier notch mask.
    public static func generateNotchMask(
        samples: [Float],
        sampleRateHz: UInt32,
        fftSize: Int = 1024,
        bandStartHz: Float = 18_500,
        bandEndHz: Float = 23_500,
        thresholdDB: Float = 8.0
    ) -> [UInt8] {
        var notchMask = [UInt8](repeating: 0xFF, count: 14)
        #if canImport(Accelerate)
            guard samples.count >= fftSize else { return notchMask }

            // 1. Setup Discrete Fourier Transform
            guard
                let dft = try? vDSP.DiscreteFourierTransform(
                    count: fftSize,
                    direction: .forward,
                    transformType: .complexComplex,
                    ofType: Float.self
                )
            else {
                return notchMask
            }

            // 2. Apply Hanning window to avoid spectral leakage
            var window = [Float](repeating: 0, count: fftSize)
            vDSP_hann_window(&window, vDSP_Length(fftSize), Int32(vDSP_HANN_NORM))

            var windowedSamples = [Float](repeating: 0, count: fftSize)
            vDSP.multiply(Array(samples.prefix(fftSize)), window, result: &windowedSamples)

            // 3. Transform to frequency domain
            var reOut = [Float](repeating: 0, count: fftSize)
            var imOut = [Float](repeating: 0, count: fftSize)
            dft.transform(
                inputReal: windowedSamples,
                inputImaginary: [Float](repeating: 0, count: fftSize),
                outputReal: &reOut,
                outputImaginary: &imOut
            )

            // 4. Compute PSD power values in decibels
            var psdDB = [Float](repeating: 0, count: fftSize / 2)
            for i in 0..<(fftSize / 2) {
                let power = (reOut[i] * reOut[i]) + (imOut[i] * imOut[i])
                psdDB[i] = 10.0 * log10(power + 1e-12)
            }

            // 5. Compute moving 51-point median to extract baseline ambient floor
            let kernelSize = 51
            var noiseFloorEst = [Float](repeating: 0, count: psdDB.count)
            for i in 0..<psdDB.count {
                let startIdx = max(0, i - kernelSize / 2)
                let endIdx = min(psdDB.count - 1, i + kernelSize / 2)
                var slice = Array(psdDB[startIdx...endIdx])
                slice.sort()
                let median: Float
                if slice.count % 2 == 1 {
                    median = slice[slice.count / 2]
                } else {
                    median = (slice[slice.count / 2 - 1] + slice[slice.count / 2]) / 2.0
                }
                noiseFloorEst[i] = median
            }

            // 6. Map background spikes to active subcarriers
            let binWidth = Float(sampleRateHz) / Float(fftSize)
            let startBin = max(1, Int(ceil(bandStartHz / binWidth)))
            let endBin = min((fftSize / 2) - 1, Int(floor(bandEndHz / binWidth)))
            guard startBin <= endBin else { return notchMask }

            let activeCarrierCount = endBin - startBin + 1
            guard activeCarrierCount > 0 else { return notchMask }

            // Clear notch mask to all zeros (all carriers default to notched/disabled)
            notchMask = [UInt8](repeating: 0, count: 14)

            // Evaluate each subcarrier's signal-to-ambient-noise level
            for i in 0..<activeCarrierCount {
                let bin = startBin + i
                let diff = psdDB[bin] - noiseFloorEst[bin]

                let byteIdx = i / 8
                let bitIdx = i % 8
                if byteIdx < 14 {
                    // If PSD power does not exceed the local floor by threshold, keep as active (1)
                    if diff <= thresholdDB {
                        notchMask[byteIdx] |= (1 << (7 - bitIdx))
                    }
                }
            }
        #endif
        return notchMask
    }
}
