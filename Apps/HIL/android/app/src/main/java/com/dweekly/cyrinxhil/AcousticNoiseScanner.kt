package com.dweekly.cyrinxhil

import kotlin.math.ceil
import kotlin.math.floor
import kotlin.math.log10
import kotlin.math.max
import kotlin.math.min
import kotlin.math.cos
import kotlin.math.PI

/**
 * Background spectrum scanner for Android. Computes local background noise PSD floors,
 * identifies persistent peaks, and produces the matching 14-byte subcarrier notch mask.
 */
class AcousticNoiseScanner {
    companion object {
        /**
         * Ambiently scans a noise sample buffer, computes the Power Spectral Density (PSD),
         * runs a 51-point moving median noise-floor estimation, and yields a 14-byte subcarrier notch mask.
         */
        fun generateNotchMask(
            samples: FloatArray,
            sampleRateHz: Int,
            fftSize: Int = 1024,
            bandStartHz: Float = 18500f,
            bandEndHz: Float = 23500f,
            thresholdDB: Float = 8.0f
        ): ByteArray {
            val notchMask = ByteArray(14) { 0xFF.toByte() }
            if (samples.size < fftSize) return notchMask

            val fft = FFT(fftSize)
            val real = FloatArray(fftSize)
            val imag = FloatArray(fftSize)

            // 1. Apply Hanning window to avoid spectral leakage
            for (i in 0 until fftSize) {
                val windowVal = 0.5f * (1.0f - cos(2.0f * PI.toFloat() * i.toFloat() / (fftSize - 1).toFloat()))
                real[i] = samples[i] * windowVal
                imag[i] = 0f
            }

            // 2. Perform forward FFT
            fft.transform(real, imag, forward = true)

            // 3. Compute PSD in dB
            val psdDB = FloatArray(fftSize / 2)
            for (i in 0 until fftSize / 2) {
                val power = (real[i] * real[i]) + (imag[i] * imag[i])
                psdDB[i] = 10.0f * log10(power + 1e-12f)
            }

            // 4. Compute moving 51-point median estimation of the noise floor
            val kernelSize = 51
            val noiseFloorEst = FloatArray(psdDB.size)
            for (i in 0 until psdDB.size) {
                val startIdx = max(0, i - kernelSize / 2)
                val endIdx = min(psdDB.size - 1, i + kernelSize / 2)
                val len = endIdx - startIdx + 1
                val slice = FloatArray(len)
                System.arraycopy(psdDB, startIdx, slice, 0, len)
                slice.sort()
                val median = if (len % 2 == 1) {
                    slice[len / 2]
                } else {
                    (slice[len / 2 - 1] + slice[len / 2]) / 2.0f
                }
                noiseFloorEst[i] = median
            }

            // 5. Map to Cyrinx active subcarriers
            val binWidth = sampleRateHz.toFloat() / fftSize.toFloat()
            val startBin = max(1, ceil(bandStartHz / binWidth).toInt())
            val endBin = min((fftSize / 2) - 1, floor(bandEndHz / binWidth).toInt())
            if (startBin > endBin) return notchMask

            val activeCarrierCount = endBin - startBin + 1
            if (activeCarrierCount <= 0) return notchMask

            // Zero out notch mask before setting active carrier bits
            notchMask.fill(0)

            for (i in 0 until activeCarrierCount) {
                val bin = startBin + i
                val diff = psdDB[bin] - noiseFloorEst[bin]

                val byteIdx = i / 8
                val bitIdx = i % 8
                if (byteIdx < 14) {
                    if (diff <= thresholdDB) {
                        notchMask[byteIdx] = (notchMask[byteIdx].toInt() or (1 shl (7 - bitIdx))).toByte()
                    }
                }
            }

            return notchMask
        }
    }
}
