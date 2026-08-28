import CCyrinx
import Foundation
import Testing

/// C3-05: the batch capture contract — strided channel views, capacity
/// negotiation with caller-provided block validity, error precedence, and the
/// digital loopbacks that pin the regression targets from the branch-tip
/// review (exact interleaved lengths, odd tails, >256-block validity).
@Suite struct CyrinxBatchContractTests {
    private func loopbackFixture() throws -> LoopbackFixture {
        var profile = try #require(C3Support.registryProfile(id: 1))
        let geometry = try #require(C3Support.geometry(for: profile))
        var rng = SystemRandomNumberGenerator()
        var payload = [UInt8](repeating: 0, count: Int(geometry.payload_bytes))
        for i in payload.indices {
            payload[i] = UInt8.random(in: .min ... .max, using: &rng)
        }
        let frame = try #require(C3Support.encodeFrame(profile: &profile, payload: payload))
        return LoopbackFixture(profile: profile, payload: payload, frame: frame, geometry: geometry)
    }

    private func expectFullDecode(
        _ outcome: C3Support.DecodeOutcome, payload: [UInt8],
        geometry: cyrinx_bulk_geometry
    ) {
        #expect(outcome.status == CYRINX_STATUS_OK)
        #expect(outcome.payload == payload)
        #expect(outcome.result.blocks_ok == UInt32(geometry.n_blocks))
        #expect(outcome.result.blocks_total == UInt32(geometry.n_blocks))
        #expect(outcome.result.payload_bytes_produced == UInt64(geometry.payload_bytes))
        #expect(outcome.validity.allSatisfy { $0 == 1 })
    }

    @Test func monoLoopbackDecodes() throws {
        var fx = try loopbackFixture()
        let outcome = C3Support.decode(
            profile: &fx.profile, primaryStorage: fx.frame, primaryView: (0, 1),
            payloadCap: fx.payload.count, validityCap: Int(fx.geometry.n_blocks))
        expectFullDecode(outcome, payload: fx.payload, geometry: fx.geometry)
        #expect(outcome.result.logical_samples_decoded == UInt64(fx.frame.count))
        #expect(outcome.result.selected_receiver == UInt32(CYRINX_BULK_DIVERSITY_PRIMARY))
    }

    @Test func planarStereoLoopbackDecodes() throws {
        var fx = try loopbackFixture()
        let outcome = C3Support.decode(
            profile: &fx.profile, primaryStorage: fx.frame, primaryView: (0, 1),
            secondaryStorage: fx.frame, secondaryView: (0, 1),
            payloadCap: fx.payload.count, validityCap: Int(fx.geometry.n_blocks))
        expectFullDecode(outcome, payload: fx.payload, geometry: fx.geometry)
    }

    /// Regression for the branch tip's interleaved off-by-one: an exactly
    /// sized interleaved buffer must yield frame_samples logical samples on
    /// BOTH channels (the old `(2N-1)/2` arithmetic lost the final sample of
    /// the offset-1 channel and the decode was rejected as short).
    @Test func exactLengthInterleavedStereoDecodes() throws {
        var fx = try loopbackFixture()
        var interleaved = [Float](repeating: 0, count: fx.frame.count * 2)
        for i in fx.frame.indices {
            interleaved[2 * i] = fx.frame[i]
            interleaved[2 * i + 1] = fx.frame[i]
        }
        let outcome = C3Support.decode(
            profile: &fx.profile, primaryStorage: interleaved, primaryView: (0, 2),
            secondaryView: (1, 2), payloadCap: fx.payload.count,
            validityCap: Int(fx.geometry.n_blocks))
        expectFullDecode(outcome, payload: fx.payload, geometry: fx.geometry)
        #expect(outcome.result.logical_samples_decoded == UInt64(fx.frame.count))
    }

    @Test func oddInterleavedTailAlignsToMinimum() throws {
        var fx = try loopbackFixture()
        var interleaved = [Float](repeating: 0, count: fx.frame.count * 2 + 1)
        for i in fx.frame.indices {
            interleaved[2 * i] = fx.frame[i]
            interleaved[2 * i + 1] = fx.frame[i]
        }
        let outcome = C3Support.decode(
            profile: &fx.profile, primaryStorage: interleaved, primaryView: (0, 2),
            secondaryView: (1, 2), payloadCap: fx.payload.count,
            validityCap: Int(fx.geometry.n_blocks))
        expectFullDecode(outcome, payload: fx.payload, geometry: fx.geometry)
        #expect(outcome.result.logical_samples_decoded == UInt64(fx.frame.count))
    }

    @Test func malformedViewsAreRejectedBeforeAnyOutput() throws {
        var fx = try loopbackFixture()
        let caps = (payload: Int(fx.geometry.payload_bytes), validity: Int(fx.geometry.n_blocks))

        var mutations: [(inout cyrinx_batch_input_t) -> Void] = []
        mutations.append { $0.channels.0.stride = 0 }
        mutations.append { $0.channels.0.first_sample = $0.channels.0.sample_count }
        mutations.append { $0.reserved0 = 1 }
        mutations.append { $0.channel_count = 0 }
        mutations.append { $0.channel_count = 3 }
        mutations.append { $0.channels.0.role = CYRINX_BATCH_ROLE_SECONDARY }
        mutations.append { $0.abi_version = 99 }
        mutations.append { $0.struct_size = 8 }
        for (index, mutate) in mutations.enumerated() {
            let outcome = C3Support.decode(
                profile: &fx.profile, primaryStorage: fx.frame, primaryView: (0, 1),
                payloadCap: caps.payload, validityCap: caps.validity,
                mutateInput: mutate)
            #expect(outcome.status == CYRINX_STATUS_ERR_INVALID_ARGUMENT, "mutation \(index)")
        }
    }

    @Test func shortCapturesAreMalformedInput() throws {
        var profile = try #require(C3Support.registryProfile(id: 1))
        let geometry = try #require(C3Support.geometry(for: profile))
        for count in [1, Int(geometry.frame_samples) - 1] {
            let outcome = C3Support.decode(
                profile: &profile,
                primaryStorage: [Float](repeating: 0, count: count),
                primaryView: (0, 1), payloadCap: Int(geometry.payload_bytes),
                validityCap: Int(geometry.n_blocks))
            #expect(outcome.status == CYRINX_STATUS_ERR_INVALID_ARGUMENT, "count \(count)")
        }
    }

    @Test func undersizedPayloadCapacityNegotiates() throws {
        var fx = try loopbackFixture()
        let outcome = C3Support.decode(
            profile: &fx.profile, primaryStorage: fx.frame, primaryView: (0, 1),
            payloadCap: 16, validityCap: Int(fx.geometry.n_blocks))
        #expect(outcome.status == CYRINX_STATUS_ERR_BUFFER_TOO_SMALL)
        #expect(outcome.result.payload_bytes_required == UInt64(fx.geometry.payload_bytes))
        #expect(outcome.result.block_validity_required == UInt32(fx.geometry.n_blocks))
        #expect(outcome.result.logical_frame_samples == UInt64(fx.geometry.frame_samples))
        #expect(outcome.payload.allSatisfy { $0 == 0 })
        #expect(outcome.result.payload_bytes_produced == 0)
    }

    @Test func undersizedValidityCapacityNegotiates() throws {
        var fx = try loopbackFixture()
        let outcome = C3Support.decode(
            profile: &fx.profile, primaryStorage: fx.frame, primaryView: (0, 1),
            payloadCap: Int(fx.geometry.payload_bytes),
            validityCap: Int(fx.geometry.n_blocks) - 1)
        #expect(outcome.status == CYRINX_STATUS_ERR_BUFFER_TOO_SMALL)
        #expect(outcome.result.block_validity_required == UInt32(fx.geometry.n_blocks))
    }

    @Test func staleIdentityDigestIsRejected() throws {
        var fx = try loopbackFixture()
        fx.profile.identity_sha256.0 ^= 0xFF
        let outcome = C3Support.decode(
            profile: &fx.profile, primaryStorage: fx.frame, primaryView: (0, 1),
            payloadCap: Int(fx.geometry.payload_bytes),
            validityCap: Int(fx.geometry.n_blocks))
        #expect(outcome.status == CYRINX_STATUS_ERR_INVALID_ARGUMENT)
    }

    /// A silent capture still demodulates into an ordered result: OK status,
    /// zero CRC-valid blocks, every mask entry 0 — the partial-block-recovery
    /// contract, not an error.
    @Test func silentCaptureReturnsOrderedAllInvalidMask() throws {
        var profile = try #require(C3Support.registryProfile(id: 1))
        let geometry = try #require(C3Support.geometry(for: profile))
        let silence = [Float](repeating: 0, count: Int(geometry.frame_samples))
        let outcome = C3Support.decode(
            profile: &profile, primaryStorage: silence, primaryView: (0, 1),
            payloadCap: Int(geometry.payload_bytes),
            validityCap: Int(geometry.n_blocks))
        #expect(outcome.status == CYRINX_STATUS_OK)
        #expect(outcome.result.blocks_ok == 0)
        #expect(outcome.result.blocks_total == UInt32(geometry.n_blocks))
        #expect(outcome.validity.allSatisfy { $0 == 0 })
        #expect(outcome.result.profile_id == profile.id)
    }

    @Test func nonFiniteAndClippedSamplesAreCounted() throws {
        var fx = try loopbackFixture()
        var noisy = fx.frame
        noisy[100] = .nan
        noisy[200] = 1.0
        let outcome = C3Support.decode(
            profile: &fx.profile, primaryStorage: noisy, primaryView: (0, 1),
            payloadCap: fx.payload.count, validityCap: Int(fx.geometry.n_blocks))
        #expect(outcome.result.nonfinite_evidence == 1)
        #expect(outcome.result.clipping_evidence == 1)
    }

    /// The >256-block boundary from the branch-tip review, reachable through a
    /// synthetic INTERNAL_TEST_FIXTURE profile: caller-provided validity
    /// storage carries every block with no truncating fixed maximum.
    @Test func syntheticProfileBeyond256BlocksCarriesFullValidity() throws {
        var profile = try #require(C3Support.registryProfile(id: 1))
        profile.symbol_count = 256
        profile.classification = CYRINX_CLASSIFICATION_INTERNAL_TEST_FIXTURE.rawValue
        let geometry = try #require(C3Support.geometry(for: profile))
        try #require(geometry.n_blocks > 256, "synthetic profile must exceed the old mask bound")
        profile.block_count = UInt32(geometry.n_blocks)
        var digest = [UInt8](repeating: 0, count: 32)
        #expect(cyrinx_profile_compute_identity(&profile, &digest) == CYRINX_STATUS_OK)
        withUnsafeMutableBytes(of: &profile.identity_sha256) { $0.copyBytes(from: digest) }
        #expect(cyrinx_profile_validate(&profile) == CYRINX_STATUS_OK)

        let payload = [UInt8]((0..<Int(geometry.payload_bytes)).map { UInt8(truncatingIfNeeded: $0) })
        let frame = try #require(C3Support.encodeFrame(profile: &profile, payload: payload))
        let outcome = C3Support.decode(
            profile: &profile, primaryStorage: frame, primaryView: (0, 1),
            payloadCap: payload.count, validityCap: Int(geometry.n_blocks))
        expectFullDecode(outcome, payload: payload, geometry: geometry)
        #expect(outcome.validity.count == Int(geometry.n_blocks))
    }
}

struct LoopbackFixture {
    var profile: cyrinx_profile_t
    var payload: [UInt8]
    var frame: [Float]
    var geometry: cyrinx_bulk_geometry
}
