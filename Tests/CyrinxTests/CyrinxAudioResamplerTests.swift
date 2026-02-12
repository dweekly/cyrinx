import XCTest

@testable import Cyrinx

final class CyrinxAudioResamplerTests: XCTestCase {
    func testLinearStreamResamplerUpsampleRatio() {
        let inputRate = 44_100.0
        let outputRate = 48_000.0
        let input = makeSineWave(sampleRateHz: inputRate, frequencyHz: 1_000, sampleCount: 4_410)

        let resampler = LinearStreamResampler(inputRateHz: inputRate, outputRateHz: outputRate)
        let output = processInChunks(input, chunkSize: 137, resampler: resampler)

        XCTAssertGreaterThan(output.count, 4_700)
        XCTAssertLessThan(output.count, 4_900)
    }

    func testLinearStreamResamplerDownsampleRatio() {
        let inputRate = 48_000.0
        let outputRate = 44_100.0
        let input = makeSineWave(sampleRateHz: inputRate, frequencyHz: 1_000, sampleCount: 4_800)

        let resampler = LinearStreamResampler(inputRateHz: inputRate, outputRateHz: outputRate)
        let output = processInChunks(input, chunkSize: 193, resampler: resampler)

        XCTAssertGreaterThan(output.count, 4_250)
        XCTAssertLessThan(output.count, 4_500)
    }

    func testLinearStreamResamplerChunkingStability() {
        let inputRate = 44_100.0
        let outputRate = 48_000.0
        let input = makeSineWave(sampleRateHz: inputRate, frequencyHz: 2_500, sampleCount: 8_820)

        let chunkedResampler = LinearStreamResampler(inputRateHz: inputRate, outputRateHz: outputRate)
        let chunked = processInChunks(input, chunkSize: 211, resampler: chunkedResampler)

        let oneShotResampler = LinearStreamResampler(inputRateHz: inputRate, outputRateHz: outputRate)
        let oneShot = oneShotResampler.process(input)

        XCTAssertEqual(chunked.count, oneShot.count)
        XCTAssertEqual(Array(chunked.prefix(128)), Array(oneShot.prefix(128)))
    }

    private func processInChunks(
        _ samples: [Float],
        chunkSize: Int,
        resampler: LinearStreamResampler
    ) -> [Float] {
        if chunkSize <= 0 {
            return resampler.process(samples)
        }

        var output: [Float] = []
        output.reserveCapacity(Int(Double(samples.count) * 1.2))
        var index = 0
        while index < samples.count {
            let end = min(index + chunkSize, samples.count)
            output.append(contentsOf: resampler.process(Array(samples[index..<end])))
            index = end
        }
        return output
    }

    private func makeSineWave(sampleRateHz: Double, frequencyHz: Double, sampleCount: Int) -> [Float] {
        (0..<sampleCount).map { index in
            let phase = (2.0 * Double.pi * frequencyHz * Double(index)) / sampleRateHz
            return Float(sin(phase))
        }
    }
}
