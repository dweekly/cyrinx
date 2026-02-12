import XCTest

@testable import Cyrinx

final class CyrinxAudioStubTests: XCTestCase {
    func testStubWaveSynthesizerIsDeterministic() {
        let symbols: [UInt8] = [1, 7, 19, 31, 42, 88]
        let a = StubWaveSynthesizer.synthesize(
            symbols: symbols,
            sampleRate: 48_000,
            txGainCap: 0.7
        )
        let b = StubWaveSynthesizer.synthesize(
            symbols: symbols,
            sampleRate: 48_000,
            txGainCap: 0.7
        )

        XCTAssertEqual(a.count, b.count)
        XCTAssertEqual(a, b)
    }

    func testStubWaveSynthesizerRespectsSymbolLimit() {
        let symbols: [UInt8] = (0..<120).map { UInt8($0 % 255) }
        let out = StubWaveSynthesizer.synthesize(
            symbols: symbols,
            sampleRate: 48_000,
            txGainCap: 0.7,
            symbolLimit: 64
        )

        let samplesPerSymbol = max(Int(48_000 * 0.002), 48)
        XCTAssertEqual(out.count, 64 * samplesPerSymbol)
    }

    func testStubWaveSynthesizerHonorsGainCap() {
        let symbols: [UInt8] = [3, 8, 13, 21]
        let out = StubWaveSynthesizer.synthesize(
            symbols: symbols,
            sampleRate: 48_000,
            txGainCap: 0.03
        )

        let peak = out.map { abs($0) }.max() ?? 0
        XCTAssertLessThanOrEqual(peak, 0.0301)
    }
}
