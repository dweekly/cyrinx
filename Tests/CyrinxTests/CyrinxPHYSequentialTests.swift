import CCyrinx
import XCTest

@testable import Cyrinx

final class CyrinxPHYSequentialTests: XCTestCase {
    func testChunkedSequentialProcessingMatchesMonolithic() {
        var config = makeConfig(mode: CYRINX_PHY_STUB_OFDM_QPSK)
        let symbols: [UInt8] = (0..<24).map { UInt8($0 % 4) }

        var monoTx = makeState(mode: config.mode)
        let monoSamples = modulateSeq(config: &config, state: &monoTx, symbols: symbols)

        var chunkTx = makeState(mode: config.mode)
        let firstChunk = modulateSeq(config: &config, state: &chunkTx, symbols: Array(symbols.prefix(7)))
        let secondChunk = modulateSeq(config: &config, state: &chunkTx, symbols: Array(symbols.dropFirst(7)))
        let stitched = firstChunk + secondChunk
        assertComplexVectorsEqual(stitched, monoSamples)

        var monoRx = makeState(mode: config.mode)
        let decodedMono = demodulateSeq(config: &config, state: &monoRx, samples: monoSamples)
        XCTAssertEqual(decodedMono, symbols)

        var chunkRx = makeState(mode: config.mode)
        let decodedA = demodulateSeq(config: &config, state: &chunkRx, samples: firstChunk)
        let decodedB = demodulateSeq(config: &config, state: &chunkRx, samples: secondChunk)
        XCTAssertEqual(decodedA + decodedB, symbols)
    }

    func testSequentialStateModeMismatchReturnsStateError() {
        var config = makeConfig(mode: CYRINX_PHY_STUB_OFDM_QPSK)
        var state = makeState(mode: CYRINX_PHY_STUB_DCSS)

        let symbols: [UInt8] = [0, 1, 2, 3]
        var out = [cyrinx_complex_f32_t](repeating: cyrinx_complex_f32_t(re: 0, im: 0), count: symbols.count)
        var outCount = out.count

        let rc = symbols.withUnsafeBufferPointer { ptr in
            cyrinx_phy_modulate_stub_seq(&config, &state, ptr.baseAddress, symbols.count, &out, &outCount)
        }
        XCTAssertEqual(rc, CYRINX_ERR_STATE.rawValue)
    }

    func testSequentialBufferTooSmallReportsRequiredLengths() {
        var config = makeConfig(mode: CYRINX_PHY_STUB_DCSS)
        let symbols: [UInt8] = [1, 2, 3, 4, 5, 6]

        var txState = makeState(mode: config.mode)
        var tooSmallSamples = [cyrinx_complex_f32_t](
            repeating: cyrinx_complex_f32_t(re: 0, im: 0),
            count: 3
        )
        var requiredSamples = tooSmallSamples.count
        let rcMod = symbols.withUnsafeBufferPointer { ptr in
            cyrinx_phy_modulate_stub_seq(
                &config,
                &txState,
                ptr.baseAddress,
                symbols.count,
                &tooSmallSamples,
                &requiredSamples
            )
        }
        XCTAssertEqual(rcMod, CYRINX_ERR_BUFFER_TOO_SMALL.rawValue)
        XCTAssertEqual(requiredSamples, symbols.count)

        var fullSamples = [cyrinx_complex_f32_t](
            repeating: cyrinx_complex_f32_t(re: 0, im: 0),
            count: symbols.count
        )
        var fullSampleCount = fullSamples.count
        let rcStateless = symbols.withUnsafeBufferPointer { ptr in
            cyrinx_phy_modulate_stub(
                &config,
                ptr.baseAddress,
                symbols.count,
                &fullSamples,
                &fullSampleCount
            )
        }
        XCTAssertEqual(rcStateless, CYRINX_OK.rawValue)

        var rxState = makeState(mode: config.mode)
        var tooSmallSymbols = [UInt8](repeating: 0, count: 2)
        var requiredSymbols = tooSmallSymbols.count
        let rcDemod = cyrinx_phy_demodulate_stub_seq(
            &config,
            &rxState,
            &fullSamples,
            fullSampleCount,
            &tooSmallSymbols,
            &requiredSymbols
        )
        XCTAssertEqual(rcDemod, CYRINX_ERR_BUFFER_TOO_SMALL.rawValue)
        XCTAssertEqual(requiredSymbols, fullSampleCount)
    }

    func testSwiftSequentialStatefulRoundTripAndResetBehavior() throws {
        let config = PHYStub.defaultConfig(for: .ofdmQPSK)
        let symbols: [UInt8] = [0, 1, 2, 3]
        var txState = PHYStubSequentialState(mode: .ofdmQPSK)

        let first = try PHYStub.modulateSequential(symbols: symbols, config: config, state: &txState)
        let second = try PHYStub.modulateSequential(symbols: symbols, config: config, state: &txState)
        XCTAssertNotEqual(first, second)
        XCTAssertEqual(txState.txSymbolIndex, 8)

        txState.reset(mode: .ofdmQPSK)
        let afterReset = try PHYStub.modulateSequential(symbols: symbols, config: config, state: &txState)
        XCTAssertEqual(first, afterReset)

        var rxState = PHYStubSequentialState(mode: .ofdmQPSK)
        let decodedFirst = try PHYStub.demodulateSequential(samples: first, config: config, state: &rxState)
        let decodedSecond = try PHYStub.demodulateSequential(samples: second, config: config, state: &rxState)
        XCTAssertEqual(decodedFirst, symbols)
        XCTAssertEqual(decodedSecond, symbols)
        XCTAssertEqual(rxState.rxSymbolIndex, 8)
    }

    private func makeConfig(mode: cyrinx_phy_stub_mode_t) -> cyrinx_phy_stub_config_t {
        var config = cyrinx_phy_stub_config_t(mode: mode, sample_rate_hz: 0, fft_size: 0, cp_samples: 0)
        cyrinx_phy_stub_default_config(mode, &config)
        return config
    }

    private func makeState(mode: cyrinx_phy_stub_mode_t) -> cyrinx_phy_stub_state_t {
        var state = cyrinx_phy_stub_state_t(
            mode: mode, tx_symbol_index: 0, rx_symbol_index: 0, initialized: 0)
        cyrinx_phy_stub_state_reset(&state, mode)
        return state
    }

    private func modulateSeq(
        config: inout cyrinx_phy_stub_config_t,
        state: inout cyrinx_phy_stub_state_t,
        symbols: [UInt8]
    ) -> [cyrinx_complex_f32_t] {
        var out = [cyrinx_complex_f32_t](repeating: cyrinx_complex_f32_t(re: 0, im: 0), count: symbols.count)
        var outCount = out.count
        let rc = symbols.withUnsafeBufferPointer { ptr in
            cyrinx_phy_modulate_stub_seq(
                &config,
                &state,
                ptr.baseAddress,
                symbols.count,
                &out,
                &outCount
            )
        }
        XCTAssertEqual(rc, CYRINX_OK.rawValue)
        return Array(out.prefix(outCount))
    }

    private func demodulateSeq(
        config: inout cyrinx_phy_stub_config_t,
        state: inout cyrinx_phy_stub_state_t,
        samples: [cyrinx_complex_f32_t]
    ) -> [UInt8] {
        var mutableSamples = samples
        var symbols = [UInt8](repeating: 0, count: samples.count)
        var symbolCount = symbols.count
        let rc = cyrinx_phy_demodulate_stub_seq(
            &config,
            &state,
            &mutableSamples,
            mutableSamples.count,
            &symbols,
            &symbolCount
        )
        XCTAssertEqual(rc, CYRINX_OK.rawValue)
        return Array(symbols.prefix(symbolCount))
    }

    private func assertComplexVectorsEqual(
        _ lhs: [cyrinx_complex_f32_t],
        _ rhs: [cyrinx_complex_f32_t],
        accuracy: Float = 1e-6,
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        XCTAssertEqual(lhs.count, rhs.count, file: file, line: line)
        for i in 0..<min(lhs.count, rhs.count) {
            XCTAssertEqual(lhs[i].re, rhs[i].re, accuracy: accuracy, file: file, line: line)
            XCTAssertEqual(lhs[i].im, rhs[i].im, accuracy: accuracy, file: file, line: line)
        }
    }
}
