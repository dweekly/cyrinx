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

func runForwardComparison() {
    let size = 512
    var inputReal = [Float](repeating: 0, count: size)
    for i in 0..<size {
        inputReal[i] = Float.random(in: -1...1)
    }
    let inputImag = [Float](repeating: 0, count: size)

    // A. vDSP Forward DFT
    var vdRe = [Float](repeating: 0, count: size)
    var vdIm = [Float](repeating: 0, count: size)
    do {
        let dft = try vDSP.DiscreteFourierTransform(
            count: size,
            direction: .forward,
            transformType: .complexComplex,
            ofType: Float.self
        )
        dft.transform(
            inputReal: inputReal,
            inputImaginary: inputImag,
            outputReal: &vdRe,
            outputImaginary: &vdIm
        )
    } catch {
        print("vDSP Init failed: \(error)")
        return
    }

    // B. Cooley-Tukey Forward FFT
    let ct = CooleyTukeyFFT(size: size)
    var ctRe = inputReal
    var ctIm = inputImag
    ct.transform(real: &ctRe, imag: &ctIm, forward: true)

    // C. Print differences
    var maxDiffRe: Float = 0.0
    var maxDiffIm: Float = 0.0
    for i in 0..<size {
        let diffRe = abs(vdRe[i] - ctRe[i])
        let diffIm = abs(vdIm[i] - ctIm[i])
        maxDiffRe = max(maxDiffRe, diffRe)
        maxDiffIm = max(maxDiffIm, diffIm)
    }
    print(String(format: "Max Absolute Difference over all bins - Real: %.6e, Imag: %.6e", maxDiffRe, maxDiffIm))
}

runForwardComparison()
