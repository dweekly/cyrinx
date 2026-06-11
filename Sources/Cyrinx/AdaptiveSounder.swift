import CCyrinx
import Foundation

/// Swift surface for the adaptive-sounder decision engine (`cyrinx_sounder`):
/// pick the most aggressive MCS the measured channel clears, size the cyclic
/// prefix, and load bits per subcarrier — the decision half of the
/// environment-adaptive scheduler. Pure function of metrics (no audio); the
/// capture→metrics half reuses the receiver's channel estimation.
public enum MCSTier: Sendable, Equatable {
    case fast  // 16-QAM r3/4
    case medium  // 16-QAM r1/2
    case qpsk  // QPSK r1/2
    case bpsk  // BPSK r1/2 (coherent OFDM floor)
    case nonCoherent  // MT-FSK (delay spread beyond the CP cap)

    fileprivate init(_ c: cyrinx_mcs_tier) {
        switch c {
        case CYRINX_TIER_FAST: self = .fast
        case CYRINX_TIER_MEDIUM: self = .medium
        case CYRINX_TIER_QPSK: self = .qpsk
        case CYRINX_TIER_BPSK: self = .bpsk
        default: self = .nonCoherent
        }
    }
}

public struct MCSRecommendation: Sendable {
    public let tier: MCSTier
    public let modulation: String
    public let rate: String
    public let bitsPerBin: Int
    public let isNonCoherent: Bool
    public let cyclicPrefix: Int
    public let fftSize: Int
    public let cyclicPrefixMs: Double
    /// Advisory only: the link still transmits, but a better physical spot would help.
    public let adviseReposition: Bool
}

/// Recommend an MCS tier + CP + FFT size from the measured median per-bin SNR and
/// the −15 dB Schroeder delay spread. Always returns a transmittable profile
/// (down to the non-coherent floor) — it never refuses to link.
public func recommendMCS(
    medianSNRdB: Double, delaySpreadMs15: Double, sampleRate: Int = 48000
) -> MCSRecommendation {
    let r = cyrinx_sounder_recommend(medianSNRdB, delaySpreadMs15, Int32(sampleRate))
    return MCSRecommendation(
        tier: MCSTier(r.tier),
        modulation: String(cString: r.mcs),
        rate: String(cString: r.rate),
        bitsPerBin: Int(r.bits_per_bin),
        isNonCoherent: r.noncoherent != 0,
        cyclicPrefix: Int(r.cp),
        fftSize: Int(r.nfft),
        cyclicPrefixMs: r.cp_ms,
        adviseReposition: r.advise_reposition != 0)
}

/// Per-subcarrier bit loading from per-bin SNR (dB): 0 (<5), 2 (≥5), 4 (≥15),
/// 6 (≥23). Thresholds calibrated to the rate-1/2 FEC reach.
public func bitLoading(perBinSNRdB snr: [Double]) -> [Int] {
    var out = [UInt8](repeating: 0, count: snr.count)
    snr.withUnsafeBufferPointer { p in
        cyrinx_sounder_bit_loading(p.baseAddress, Int32(snr.count), &out)
    }
    return out.map(Int.init)
}
