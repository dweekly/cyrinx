import CCyrinx
import Testing

@testable import Cyrinx

@Suite struct BatchPHYTests {
    /// Deterministic bytes that differ within and between blocks, so a reordering changes them.
    private func variedPayload(_ count: Int, seed: UInt32) -> [UInt8] {
        var state = seed
        return (0..<count).map { _ in
            state = state &* 1_664_525 &+ 1_013_904_223
            return UInt8(truncatingIfNeeded: state >> 24)
        }
    }

    @Test(arguments: [1, 2, 3, 4, 5] as [UInt32])
    func registeredFrameRoundTrip(id: UInt32) throws {
        let profile = try BatchPHY.Profile(id: id)
        let raw = try #require(C3Support.registryProfile(id: id))
        #expect(profile.identitySHA256 == withUnsafeBytes(of: raw.identity_sha256) { Array($0) })
        #expect(profile.symbolCount == raw.symbol_count)
        #expect(profile.blockCount == raw.block_count)
        let source = variedPayload(profile.payloadByteCount, seed: id)
        #expect(source != Array(source.reversed()))
        let encoded = try BatchPHY.encode(source, profile: profile)
        #expect(encoded.samples.count == profile.frameSampleCount)
        #expect(encoded.metrics.samplesProduced == UInt64(encoded.samples.count))
        let decoded = try BatchPHY.decode(primary: .init(encoded.samples), profile: profile)
        #expect(decoded.payload == source)
        #expect(decoded.blockValidity == Array(repeating: 1, count: Int(raw.block_count)))
        #expect(decoded.metrics.profileID == id)
        #expect(decoded.metrics.profileIdentitySHA256 == profile.identitySHA256)
        #expect(decoded.metrics.blocksOK == profile.blockCount)
        #expect(decoded.metrics.payloadBytesProduced == UInt64(source.count))
        #expect(decoded.metrics.logicalSamplesDecoded == UInt64(encoded.samples.count))
        #expect(decoded.metrics.selectionReason == CYRINX_BULK_AUTO_REASON_SECOND_UNAVAILABLE)
    }

    @Test func registeredIDsAndMissingProfile() throws {
        #expect(try BatchPHY.Profile.registered().map(\.id) == [1, 2, 3, 4, 5])
        do {
            _ = try BatchPHY.Profile(id: .max)
            Issue.record("Unknown profile was accepted")
        } catch BatchPHY.Failure.profileNotFound {}
    }

    @Test func invalidLayoutAndOversizePayload() throws {
        let profile = try BatchPHY.Profile(id: 1)
        // The same silent capture is accepted with a valid stride, so stride is the only fault.
        let capture = [Float](repeating: 0, count: profile.frameSampleCount)
        #expect(try BatchPHY.decode(primary: .init(capture), profile: profile).metrics.blocksOK == 0)
        do {
            _ = try BatchPHY.decode(primary: .init(capture, stride: 0), profile: profile)
            Issue.record("Zero stride was accepted")
        } catch BatchPHY.Failure.invalidArgument {}
        do {
            _ = try BatchPHY.encode(
                Array(repeating: 0, count: profile.payloadByteCount + 1), profile: profile)
            Issue.record("Oversize payload was accepted")
        } catch BatchPHY.Failure.invalidArgument {}
    }

    @Test func metricFieldsPreserveRawCodesAndValues() {
        var raw = cyrinx_batch_result_t()
        raw.selected_receiver = UInt32.max
        raw.selection_reason = UInt32.max - 1
        raw.evm_rms = .nan
        let metrics = BatchPHY.Metrics(raw)
        #expect(metrics.selectedReceiver == UInt32.max)
        #expect(metrics.selectionReason == UInt32.max - 1)
        #expect(metrics.evmRMS.isNaN)
    }

    @Test func unequalPlanarChannelsMatchCResults() throws {
        var raw = try #require(C3Support.registryProfile(id: 1))
        let profile = try BatchPHY.Profile(id: 1)
        let wave = try BatchPHY.encode(
            Array(repeating: 113, count: profile.payloadByteCount), profile: profile)
        let primary = [Float.nan, 1] + wave.samples
        let secondary = primary + [0, 0, 0]
        let expected = C3Support.decode(
            profile: &raw, primaryStorage: primary, primaryView: (0, 1),
            secondaryStorage: secondary, secondaryView: (0, 1),
            payloadCap: profile.payloadByteCount, validityCap: Int(profile.blockCount))
        #expect(expected.status == CYRINX_STATUS_OK)
        let result = try BatchPHY.decode(
            primary: .init(primary), secondary: .init(secondary), profile: profile)
        #expect(result.payload.elementsEqual(expected.payload))
        #expect(result.blockValidity == expected.validity)
        let actual = result.metrics
        let c = expected.result
        #expect(actual.abiVersion == c.abi_version && actual.structSize == c.struct_size)
        #expect(actual.payloadBytesRequired == c.payload_bytes_required)
        #expect(actual.blockValidityRequired == c.block_validity_required)
        #expect(actual.logicalFrameSamples == c.logical_frame_samples)
        #expect(actual.logicalSamplesDecoded == c.logical_samples_decoded)
        #expect(actual.clippingEvidence == c.clipping_evidence && actual.nonfiniteEvidence == 2)
        #expect(actual.nonfiniteEvidence == c.nonfinite_evidence)
        #expect(actual.evmRMS.bitPattern == c.evm_rms.bitPattern)
        #expect(actual.primaryHoldoutPilotRMS.bitPattern == c.primary_holdout_pilot_rms.bitPattern)
        #expect(actual.mrcHoldoutPilotRMS.bitPattern == c.mrc_holdout_pilot_rms.bitPattern)
    }

    @Test(arguments: [1, 2, 3, 4, 5] as [UInt32])
    func shortPayloadIsRejected(id: UInt32) throws {
        let profile = try BatchPHY.Profile(id: id)
        #expect(profile.payloadByteCount == Int(profile.blockCount) * BatchPHY.Profile.blockByteCount)
        for count in [0, 1, profile.payloadByteCount - 1] {
            do {
                _ = try BatchPHY.encode(Array(repeating: 113, count: count), profile: profile)
                Issue.record("A \(count)-byte payload was accepted")
            } catch BatchPHY.Failure.invalidArgument {}
        }
    }

    // Records a codec limitation (docs/NEGATIVE_FINDINGS.md entry 26), not a requirement: with no
    // scrambler, these two zero-heavy frames lose every block in a clean loopback. A scrambler
    // should turn these expectations around.
    @Test(arguments: [1, 2, 3, 4, 5] as [UInt32])
    func zeroHeavyFrameRecoversNoBlocks(id: UInt32) throws {
        let profile = try BatchPHY.Profile(id: id)
        var record = [UInt8](repeating: 0, count: profile.payloadByteCount)
        for index in 0..<128 { record[index] = UInt8(truncatingIfNeeded: index &* 73 &+ 41) }
        for payload in [[UInt8](repeating: 0, count: profile.payloadByteCount), record] {
            let frame = try BatchPHY.encode(payload, profile: profile)
            let decoded = try BatchPHY.decode(primary: .init(frame.samples), profile: profile)
            #expect(decoded.metrics.blocksOK == 0)
            #expect(decoded.blockValidity.allSatisfy { $0 == 0 })
        }
    }

    // The one decode failure a real capture can reach: the chirp is found so late that the
    // frame it announces runs past the end of the capture. C returns its decode-failure status
    // with the evidence it had gathered, and Swift carries that evidence in the thrown failure.
    @Test(arguments: [1, 2, 3, 4, 5] as [UInt32])
    func captureEndingInsideTheFrameReportsDecodeFailure(id: UInt32) throws {
        let profile = try BatchPHY.Profile(id: id)
        let frame = try BatchPHY.encode(variedPayload(profile.payloadByteCount, seed: id), profile: profile)
        let preamble = Int(CYRINX_BULK_CHIRP_LEN + CYRINX_BULK_GUARD)
        var late = [Float](repeating: 0, count: frame.samples.count - preamble)
        late += frame.samples.prefix(preamble)
        late[0] = 1  // One full-scale sample, so the evidence count is not trivially zero.
        #expect(late.count == profile.frameSampleCount)
        do {
            _ = try BatchPHY.decode(primary: .init(late), profile: profile)
            Issue.record("A capture that ends inside the frame decoded")
        } catch BatchPHY.Failure.decodeFailed(let metrics) {
            #expect(metrics.profileID == id)
            #expect(metrics.profileIdentitySHA256 == profile.identitySHA256)
            #expect(metrics.blocksOK == 0 && metrics.payloadBytesProduced == 0)
            #expect(metrics.blocksTotal == profile.blockCount)
            #expect(metrics.logicalSamplesDecoded == UInt64(late.count))
            #expect(metrics.clippingEvidence == 1 && metrics.nonfiniteEvidence == 0)
        }
        // The same capture with the whole frame present decodes, so the ending is the only fault.
        let whole = late.dropLast(preamble) + frame.samples
        let decoded = try BatchPHY.decode(primary: .init(Array(whole)), profile: profile)
        #expect(decoded.metrics.blocksOK == profile.blockCount)
    }

    @Test func statusCodesMapToFailures() throws {
        try BatchPHY.check(CYRINX_STATUS_OK)
        func failure(_ status: Int32) -> BatchPHY.Failure? {
            do { try BatchPHY.check(status) } catch { return error as? BatchPHY.Failure }
            return nil
        }
        guard case .invalidArgument? = failure(CYRINX_STATUS_ERR_INVALID_ARGUMENT),
            case .profileNotFound? = failure(CYRINX_STATUS_ERR_NOT_FOUND),
            case .bufferTooSmall? = failure(CYRINX_STATUS_ERR_BUFFER_TOO_SMALL),
            case .status(let unknown)? = failure(Int32.min), unknown == Int32.min
        else {
            Issue.record("A C status mapped to the wrong failure")
            return
        }
    }
}
