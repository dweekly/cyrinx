import Foundation

/// Decoded acoustic frame metadata for offline interoperability testing.
public struct AcousticPHYDecodeResult: Sendable {
    public let frame: Data
    public let snrDB: Float
    public let evmPct: Float
    public let per2s: Float
    public let crcFail: Bool

    init(decoded: AcousticDecodedFrame) {
        frame = Data(decoded.frame)
        snrDB = decoded.report.snr_db
        evmPct = decoded.report.evm_pct
        per2s = decoded.report.per_2s
        crcFail = decoded.report.crc_fail != 0
    }
}

/// Debug helpers for deterministic acoustic waveform fixture generation and decode checks.
public enum AcousticPHYDebug {
    /// Encodes a single core frame into an acoustic waveform.
    public static func encodeFrame(
        config: Config,
        frame: Data,
        dcssSymbolSamples: Int? = nil,
        preambleSyncThreshold: Float? = nil
    ) throws -> [Float] {
        let phy = AcousticPHYLink(
            config: config,
            dcssSymbolSamplesOverride: dcssSymbolSamples,
            preambleSyncThresholdOverride: preambleSyncThreshold
        )
        return try phy.encode(frame: [UInt8](frame))
    }

    /// Decodes all complete frames available in the provided waveform sample buffer.
    public static func decodeWaveform(
        config: Config,
        samples: [Float],
        dcssSymbolSamples: Int? = nil,
        preambleSyncThreshold: Float? = nil
    ) -> [AcousticPHYDecodeResult] {
        let phy = AcousticPHYLink(
            config: config,
            dcssSymbolSamplesOverride: dcssSymbolSamples,
            preambleSyncThresholdOverride: preambleSyncThreshold
        )
        return phy.ingest(samples: samples).map(AcousticPHYDecodeResult.init(decoded:))
    }
}
