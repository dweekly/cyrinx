package com.dweekly.cyrinxhil

import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.sin

/**
 * Highly optimized Cooley-Tukey Radix-2 in-place Fast Fourier Transform.
 * Precomputes twiddle factors and bit-reversal lookup tables during
 * initialization for high-speed real-time execution in the audio thread.
 */
class FFT(val size: Int) {
    private val log2N = run {
        var temp = size
        var count = 0
        while (temp > 1) {
            require(temp % 2 == 0) { "FFT size must be a power of 2" }
            temp /= 2
            count++
        }
        count
    }

    private val bitReversalTable = IntArray(size) { i ->
        var rev = 0
        var temp = i
        for (j in 0 until log2N) {
            rev = (rev shl 1) or (temp and 1)
            temp = temp ushr 1
        }
        rev
    }

    private val cosTable = FloatArray(size / 2)
    private val sinTable = FloatArray(size / 2)

    init {
        for (k in 0 until size / 2) {
            val angle = 2.0 * PI * k / size
            cosTable[k] = cos(angle).toFloat()
            sinTable[k] = sin(angle).toFloat()
        }
    }

    /**
     * Performs an in-place 1D Cooley-Tukey Radix-2 complex FFT.
     * Both arrays must have a size equal to the configured size.
     */
    fun transform(real: FloatArray, imag: FloatArray, forward: Boolean) {
        require(real.size == size && imag.size == size) {
            "Input arrays must be of size $size"
        }

        // 1. Bit-reversal sorting
        for (i in 0 until size) {
            val j = bitReversalTable[i]
            if (i < j) {
                val tempRe = real[i]
                real[i] = real[j]
                real[j] = tempRe

                val tempIm = imag[i]
                imag[i] = imag[j]
                imag[j] = tempIm
            }
        }

        // 2. Butterfly stages
        var len = 2
        while (len <= size) {
            val halfLen = len / 2
            val twiddleStep = size / len

            for (i in 0 until size step len) {
                for (j in 0 until halfLen) {
                    val k = i + j
                    val l = k + halfLen

                    val twiddleIdx = j * twiddleStep
                    val wr = cosTable[twiddleIdx]
                    val wi = if (forward) -sinTable[twiddleIdx] else sinTable[twiddleIdx]

                    val tRe = real[l] * wr - imag[l] * wi
                    val tIm = real[l] * wi + imag[l] * wr

                    real[l] = real[k] - tRe
                    imag[l] = imag[k] - tIm

                    real[k] += tRe
                    imag[k] += tIm
                }
            }
            len = len shl 1
        }
    }
}
