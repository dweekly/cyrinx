import Foundation
import Accelerate

let fftSize = 8
let dftForward = try! vDSP.DiscreteFourierTransform(
    count: fftSize,
    direction: .forward,
    transformType: .complexComplex,
    ofType: Float.self
)

var frame = [Float](repeating: 0, count: fftSize)
frame[1] = 1.0

var reOut = [Float](repeating: 0, count: fftSize)
var imOut = [Float](repeating: 0, count: fftSize)

dftForward.transform(
    inputReal: frame,
    inputImaginary: [Float](repeating: 0, count: fftSize),
    outputReal: &reOut,
    outputImaginary: &imOut
)

for i in 0..<fftSize {
    print("bin \(i): re=\(String(format: "%.6f", reOut[i])), im=\(String(format: "%.6f", imOut[i]))")
}
