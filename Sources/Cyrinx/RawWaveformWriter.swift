import Foundation

@inline(__always)
func writeWaveform(_ source: [Float], into destination: inout [Float], offset: Int) {
    for index in 0..<source.count {
        destination[offset + index] = source[index]
    }
}
