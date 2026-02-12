import CCyrinx
import Foundation

public struct PHYComplex: Sendable, Equatable {
    public let re: Float
    public let im: Float

    public init(re: Float, im: Float) {
        self.re = re
        self.im = im
    }
}

public enum PHYStubMode: Sendable {
    case ofdmQPSK
    case dcss

    fileprivate var cValue: cyrinx_phy_stub_mode_t {
        switch self {
        case .ofdmQPSK:
            return CYRINX_PHY_STUB_OFDM_QPSK
        case .dcss:
            return CYRINX_PHY_STUB_DCSS
        }
    }
}

public struct PHYStubConfig: Sendable {
    public var mode: PHYStubMode
    public var sampleRateHz: UInt32
    public var fftSize: UInt16
    public var cpSamples: UInt16

    public init(
        mode: PHYStubMode,
        sampleRateHz: UInt32 = 48_000,
        fftSize: UInt16 = UInt16(CYRINX_OFDM_FFT_SIZE),
        cpSamples: UInt16 = 96
    ) {
        self.mode = mode
        self.sampleRateHz = sampleRateHz
        self.fftSize = fftSize
        self.cpSamples = cpSamples
    }

    fileprivate func toC() -> cyrinx_phy_stub_config_t {
        cyrinx_phy_stub_config_t(
            mode: mode.cValue,
            sample_rate_hz: sampleRateHz,
            fft_size: fftSize,
            cp_samples: cpSamples
        )
    }
}

public struct PHYStubSequentialState: Sendable {
    fileprivate var cState: cyrinx_phy_stub_state_t

    public init(mode: PHYStubMode) {
        cState = cyrinx_phy_stub_state_t(
            mode: mode.cValue, tx_symbol_index: 0, rx_symbol_index: 0, initialized: 0)
        cyrinx_phy_stub_state_reset(&cState, mode.cValue)
    }

    public mutating func reset(mode: PHYStubMode) {
        cyrinx_phy_stub_state_reset(&cState, mode.cValue)
    }

    public var txSymbolIndex: UInt64 {
        cState.tx_symbol_index
    }

    public var rxSymbolIndex: UInt64 {
        cState.rx_symbol_index
    }
}

public enum PHYStub {
    public static func modulate(symbols: [UInt8], config: PHYStubConfig) throws -> [PHYComplex] {
        if symbols.isEmpty {
            return []
        }

        var cConfig = config.toC()
        var out = [cyrinx_complex_f32_t](
            repeating: cyrinx_complex_f32_t(re: 0, im: 0),
            count: symbols.count
        )
        var outCount = out.count

        let rc = symbols.withUnsafeBufferPointer { inPtr in
            cyrinx_phy_modulate_stub(
                &cConfig,
                inPtr.baseAddress,
                symbols.count,
                &out,
                &outCount
            )
        }
        try checkPHYStatus(rc)

        return out.prefix(outCount).map { PHYComplex(re: $0.re, im: $0.im) }
    }

    public static func demodulate(samples: [PHYComplex], config: PHYStubConfig) throws -> [UInt8] {
        if samples.isEmpty {
            return []
        }

        var cConfig = config.toC()
        let cSamples = samples.map { cyrinx_complex_f32_t(re: $0.re, im: $0.im) }
        var symbols = [UInt8](repeating: 0, count: cSamples.count)
        var symbolCount = symbols.count

        let rc = cSamples.withUnsafeBufferPointer { samplePtr in
            cyrinx_phy_demodulate_stub(
                &cConfig,
                samplePtr.baseAddress,
                samplePtr.count,
                &symbols,
                &symbolCount
            )
        }
        try checkPHYStatus(rc)
        return Array(symbols.prefix(symbolCount))
    }

    public static func modulateSequential(
        symbols: [UInt8],
        config: PHYStubConfig,
        state: inout PHYStubSequentialState
    ) throws -> [PHYComplex] {
        if symbols.isEmpty {
            return []
        }

        var cConfig = config.toC()
        var cState = state.cState
        var out = [cyrinx_complex_f32_t](
            repeating: cyrinx_complex_f32_t(re: 0, im: 0),
            count: symbols.count
        )
        var outCount = out.count

        let rc = symbols.withUnsafeBufferPointer { inPtr in
            cyrinx_phy_modulate_stub_seq(
                &cConfig,
                &cState,
                inPtr.baseAddress,
                symbols.count,
                &out,
                &outCount
            )
        }
        state.cState = cState
        try checkPHYStatus(rc)
        return out.prefix(outCount).map { PHYComplex(re: $0.re, im: $0.im) }
    }

    public static func demodulateSequential(
        samples: [PHYComplex],
        config: PHYStubConfig,
        state: inout PHYStubSequentialState
    ) throws -> [UInt8] {
        if samples.isEmpty {
            return []
        }

        var cConfig = config.toC()
        var cState = state.cState
        let cSamples = samples.map { cyrinx_complex_f32_t(re: $0.re, im: $0.im) }
        var symbols = [UInt8](repeating: 0, count: cSamples.count)
        var symbolCount = symbols.count

        let rc = cSamples.withUnsafeBufferPointer { samplePtr in
            cyrinx_phy_demodulate_stub_seq(
                &cConfig,
                &cState,
                samplePtr.baseAddress,
                samplePtr.count,
                &symbols,
                &symbolCount
            )
        }
        state.cState = cState
        try checkPHYStatus(rc)
        return Array(symbols.prefix(symbolCount))
    }

    public static func defaultConfig(for mode: PHYStubMode) -> PHYStubConfig {
        var c = cyrinx_phy_stub_config_t(mode: mode.cValue, sample_rate_hz: 0, fft_size: 0, cp_samples: 0)
        cyrinx_phy_stub_default_config(mode.cValue, &c)
        return PHYStubConfig(
            mode: mode,
            sampleRateHz: c.sample_rate_hz,
            fftSize: c.fft_size,
            cpSamples: c.cp_samples
        )
    }

    private static func checkPHYStatus(_ rc: Int32) throws {
        if rc != CYRINX_OK.rawValue {
            throw CyrinxError.status(rc)
        }
    }
}
