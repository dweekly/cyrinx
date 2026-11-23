import CCyrinx
import CScramblerDiagnostics
import Testing

/// The peak gate of docs/SCRAMBLER_PLAN.md. The defect was a waveform with a few tall peaks that
/// the transmitter's clip cut off. These tests measure each scrambled profile's frame before the
/// clip can act, for the payload patterns that made such peaks, and require the peaks to be gone.
@Suite struct CyrinxScramblerPeakTests {
    /// The patterns of NEGATIVE_FINDINGS entry 26 that fail unscrambled, as full frames.
    private typealias Patterns = CyrinxScrambledBatchTests
    static let sparsePatterns = [Patterns.zero, Patterns.record, Patterns.sparse, Patterns.xFF]

    private func capacity(id: UInt32) throws -> Int {
        let profile = try #require(C3V2.registryProfile(id: id))
        return try #require(C3V2.sizes(profile)).payloadBytes
    }

    private func statistics(id: UInt32, frame: [UInt8]) throws -> cyrinx_transmit_statistics_t {
        var profile = try #require(C3V2.registryProfile(id: id))
        var result = cyrinx_transmit_statistics_t()
        let status = cyrinx_transmit_statistics(&profile, frame, frame.count, &result)
        try #require(status == CYRINX_STATUS_OK, "statistics for profile \(id)")
        try #require(result.data_bins > 0)
        return result
    }

    /// Peak-to-RMS at most 6.5, and the commonest constellation point no more common than in a
    /// frame of random bytes, give or take 0.02. A single frame's peak is an extreme value, so
    /// the bound is an absolute one and not a ratio between two frames.
    @Test(arguments: UInt32(6)...10)
    func scrambledFramesHaveNoTallPeak(id: UInt32) throws {
        let capacity = try capacity(id: id)
        let random = try statistics(id: id, frame: Patterns.random.frame(capacity))
        #expect(random.peak_to_rms <= 6.5, "random bytes on profile \(id): \(random.peak_to_rms)")
        for pattern in Self.sparsePatterns {
            let measured = try statistics(id: id, frame: pattern.frame(capacity))
            #expect(measured.peak_to_rms <= 6.5, "\(pattern.name) on profile \(id): \(measured.peak_to_rms)")
            #expect(
                abs(measured.commonest_point_share - random.commonest_point_share) <= 0.02,
                "\(pattern.name) on profile \(id): share \(measured.commonest_point_share)")
        }
    }

    /// The control: the same measurement on the unscrambled parents sees the defect. A zero
    /// frame peaks at or beyond the widest clip the codec accepts and puts nearly every data bin
    /// on one point, and a frame of random bytes does neither. So a pass above is the
    /// scrambler's doing, not a measurement that cannot fail.
    @Test(arguments: UInt32(1)...5)
    func unscrambledZeroFrameShowsThePeakTheGateLooksFor(id: UInt32) throws {
        let capacity = try capacity(id: id)
        let zero = try statistics(id: id, frame: Patterns.zero.frame(capacity))
        #expect(zero.peak_to_rms >= CYRINX_DIAGNOSTIC_CLIP_SIGMA, "profile \(id): \(zero.peak_to_rms)")
        #expect(zero.commonest_point_share >= 0.9, "profile \(id): share \(zero.commonest_point_share)")
        let random = try statistics(id: id, frame: Patterns.random.frame(capacity))
        #expect(random.peak_to_rms <= 6.5, "profile \(id): \(random.peak_to_rms)")
        #expect(random.commonest_point_share <= 0.1, "profile \(id): share \(random.commonest_point_share)")
    }

    @Test func statisticsRefuseWhatTheyCannotMeasure() throws {
        var profile = try #require(C3V2.registryProfile(id: 6))
        var result = cyrinx_transmit_statistics_t()
        let short = [UInt8](repeating: 0, count: 128)
        #expect(
            cyrinx_transmit_statistics(&profile, short, short.count, &result)
                == CYRINX_STATUS_ERR_INVALID_ARGUMENT)
        profile.identity_sha256.0 ^= 0xFF
        let capacity = Int(profile.block_count) * 256
        let frame = [UInt8](repeating: 0, count: capacity)
        #expect(
            cyrinx_transmit_statistics(&profile, frame, frame.count, &result)
                == CYRINX_STATUS_ERR_INVALID_ARGUMENT)
    }
}
