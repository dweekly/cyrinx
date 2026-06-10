import Foundation
import Accelerate

class CooleyTukeyFFT {
    let size: Int
    private let log2N: Int
    private let bitReversalTable: [Int]
    private var cosTable: [Float]
    private var sinTable: [Float]

    init(size: Int) {
        self.size = size
        var temp = size
        var count = 0
        while temp > 1 {
            temp /= 2
            count += 1
        }
        self.log2N = count

        var table = [Int](repeating: 0, count: size)
        for i in 0..<size {
            var rev = 0
            var t = i
            for _ in 0..<count {
                rev = (rev << 1) | (t & 1)
                t >>= 1
            }
            table[i] = rev
        }
        self.bitReversalTable = table

        cosTable = [Float](repeating: 0, count: size / 2)
        sinTable = [Float](repeating: 0, count: size / 2)
        for k in 0..<(size / 2) {
            let angle = 2.0 * Double.pi * Double(k) / Double(size)
            cosTable[k] = Float(cos(angle))
            sinTable[k] = Float(sin(angle))
        }
    }

    func transform(real: inout [Float], imag: inout [Float], forward: Bool) {
        for i in 0..<size {
            let j = bitReversalTable[i]
            if i < j {
                real.swapAt(i, j)
                imag.swapAt(i, j)
            }
        }

        var len = 2
        while len <= size {
            let halfLen = len / 2
            let twiddleStep = size / len

            for i in stride(from: 0, to: size, by: len) {
                for j in 0..<halfLen {
                    let k = i + j
                    let l = k + halfLen

                    let twiddleIdx = j * twiddleStep
                    let wr = cosTable[twiddleIdx]
                    let wi = forward ? -sinTable[twiddleIdx] : sinTable[twiddleIdx]

                    let tRe = real[l] * wr - imag[l] * wi
                    let tIm = real[l] * wi + imag[l] * wr

                    real[l] = real[k] - tRe
                    imag[l] = imag[k] - tIm

                    real[k] += tRe
                    imag[k] += tIm
                }
            }
            len <<= 1
        }
    }
}

func runInverseComparison() {
    let size = 1024
    var spectrumReal = [Float](repeating: 0, count: size)
    var spectrumImag = [Float](repeating: 0, count: size)

    // Enforce conjugate symmetry for a real-valued time-domain signal
    for i in 1..<(size / 2) {
        let r = Float.random(in: -1...1)
        let im = Float.random(in: -1...1)
        spectrumReal[i] = r
        spectrumImag[i] = im
        spectrumReal[size - i] = r
        spectrumImag[size - i] = -im
    }

    // A. vDSP Inverse DFT
    var vdRe = [Float](repeating: 0, count: size)
    var vdIm = [Float](repeating: 0, count: size)
    do {
        let dft = try vDSP.DiscreteFourierTransform(
            count: size,
            direction: .inverse,
            transformType: .complexComplex,
            ofType: Float.self
        )
        dft.transform(
            inputReal: spectrumReal,
            inputImaginary: spectrumImag,
            outputReal: &vdRe,
            outputImaginary: &vdIm
        )
    } catch {
        print("vDSP Init failed: \(error)")
        return
    }

    // B. Cooley-Tukey Inverse FFT
    let ct = CooleyTukeyFFT(size: size)
    var ctRe = spectrumReal
    var ctIm = spectrumImag
    ct.transform(real: &ctRe, imag: &ctIm, forward: false)

    // C. Print first 10 bins difference
    print(String(format: "%-5@ | %-30@ | %-30@", "Idx", "vDSP (Real, Imag)", "Cooley-Tukey (Real, Imag)"))
    print(String(repeating: "-", count: 75))
    var maxDiffRe: Float = 0.0
    var maxDiffIm: Float = 0.0
    for i in 0..<15 {
        let diffRe = abs(vdRe[i] - ctRe[i])
        let diffIm = abs(vdIm[i] - ctIm[i])
        maxDiffRe = max(maxDiffRe, diffRe)
        maxDiffIm = max(maxDiffIm, diffIm)
        print(String(format: "%-5d | (%8.4f, %8.4f) | (%8.4f, %8.4f)", i, vdRe[i], vdIm[i], ctRe[i], ctIm[i]))
    }
    print(String(repeating: "-", count: 75))
    print(String(format: "Max Absolute Difference over all bins - Real: %.6e, Imag: %.6e", maxDiffRe, maxDiffIm))
}

runInverseComparison()
