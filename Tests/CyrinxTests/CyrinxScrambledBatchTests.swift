import CCyrinx
import Testing

/// The v2 batch entry points and the scrambler they carry (docs/SCRAMBLER_PLAN.md), in digital
/// loopback: the encoder's samples go straight to the decoder.
@Suite struct CyrinxScrambledBatchTests {
    /// The payload patterns of NEGATIVE_FINDINGS entry 26. `bytes` is what the encoder is given;
    /// `frame` is what a full recovery returns.
    struct Pattern: Sendable, CustomTestStringConvertible {
        let name: String
        let bytes: @Sendable (Int) -> [UInt8]
        var testDescription: String { name }

        func frame(_ capacity: Int) -> [UInt8] {
            let given = bytes(capacity)
            return given + [UInt8](repeating: 0, count: capacity - given.count)
        }
    }

    static let zero = Pattern(name: "zero") { [UInt8](repeating: 0, count: $0) }
    /// A true 128-byte input: the encoder pads it, as its contract says.
    static let record = Pattern(name: "128-byte record") { _ in C3V2.varied(128, seed: 99) }
    static let sparse = Pattern(name: "sparse") { capacity in
        (0..<capacity).map { $0 % 256 < 8 ? UInt8(truncatingIfNeeded: 0x31 &+ $0) : 0 }
    }
    static let x71 = Pattern(name: "0x71") { [UInt8](repeating: 0x71, count: $0) }
    static let xFF = Pattern(name: "0xff") { [UInt8](repeating: 0xFF, count: $0) }
    static let random = Pattern(name: "dense random") { C3V2.varied($0, seed: 7) }
    static let patterns = [zero, record, sparse, x71, xFF, random]

    /// SplitMix64, written here from its published definition and not taken from the library.
    private func maskBits(_ count: Int) -> [UInt8] {
        var state: UInt64 = 0x4359_5258_5343_5231
        return (0..<count).map { _ in
            state &+= 0x9E37_79B9_7F4A_7C15
            var z = state
            z = (z ^ (z >> 30)) &* 0xBF58_476D_1CE4_E5B9
            z = (z ^ (z >> 27)) &* 0x94D0_49BB_1331_11EB
            return UInt8((z ^ (z >> 31)) >> 63)
        }
    }

    @Test(arguments: UInt32(6)...10, patterns)
    func scrambledProfileRecoversEveryBlock(id: UInt32, pattern: Pattern) throws {
        let profile = try #require(C3V2.registryProfile(id: id))
        let sizes = try #require(C3V2.sizes(profile))
        let samples = try #require(C3V2.encode(profile, payload: pattern.bytes(sizes.payloadBytes)))
        let decoded = try #require(C3V2.decode(profile, samples: samples))
        #expect(decoded.status == CYRINX_STATUS_OK)
        #expect(decoded.mask == [UInt8](repeating: 1, count: sizes.blocks))
        #expect(decoded.payload == pattern.frame(sizes.payloadBytes))
        #expect(decoded.result.blocks_ok == UInt32(sizes.blocks))
        #expect(decoded.result.profile_id == id)
        #expect(
            withUnsafeBytes(of: decoded.result.profile_identity_sha256) { Array($0) }
                == withUnsafeBytes(of: profile.identity_sha256) { Array($0) })
    }

    /// The control beside the recovery test: the same patterns through the unscrambled parents
    /// lose what entry 26 says they lose, so the recovery above is the scrambler's doing.
    @Test(arguments: UInt32(1)...5)
    func unscrambledParentStillLosesTheSparsePatterns(id: UInt32) throws {
        let profile = try #require(C3V2.registryProfile(id: id))
        let sizes = try #require(C3V2.sizes(profile))
        for pattern in [Self.zero, Self.record, Self.sparse] {
            let samples = try #require(C3V2.encode(profile, payload: pattern.bytes(sizes.payloadBytes)))
            let decoded = try #require(C3V2.decode(profile, samples: samples))
            #expect(decoded.status == CYRINX_STATUS_OK)
            #expect(decoded.result.blocks_ok == 0, "\(pattern.name) on profile \(id)")
        }
    }

    @Test(arguments: UInt32(1)...5)
    func unscrambledV2ProfileMakesTheV1Waveform(id: UInt32) throws {
        let v2 = try #require(C3V2.registryProfile(id: id))
        var v1 = try #require(C3Support.registryProfile(id: id))
        let sizes = try #require(C3V2.sizes(v2))
        let payload = C3V2.varied(sizes.payloadBytes, seed: id)
        let fromV1 = try #require(C3Support.encodeFrame(profile: &v1, payload: payload))
        let fromV2 = try #require(C3V2.encode(v2, payload: payload))
        #expect(fromV1.map(\.bitPattern) == fromV2.map(\.bitPattern))
        let decoded = try #require(C3V2.decode(v2, samples: fromV1))
        #expect(decoded.payload == payload)
        #expect(decoded.result.blocks_ok == UInt32(sizes.blocks))
    }

    /// A decoder is told its profile. Told the twin of the one that made the frame, it
    /// demodulates normally and finds no block whose CRC holds.
    @Test(arguments: UInt32(1)...5)
    func decodingWithTheTwinRecoversNoBlock(id: UInt32) throws {
        let plain = try #require(C3V2.registryProfile(id: id))
        let scrambled = try #require(C3V2.registryProfile(id: id + 5))
        let sizes = try #require(C3V2.sizes(plain))
        let payload = C3V2.varied(sizes.payloadBytes, seed: 40 + id)
        for (sender, receiver) in [(plain, scrambled), (scrambled, plain)] {
            let samples = try #require(C3V2.encode(sender, payload: payload))
            let decoded = try #require(C3V2.decode(receiver, samples: samples))
            #expect(decoded.status == CYRINX_STATUS_OK)
            #expect(
                decoded.mask == [UInt8](repeating: 0, count: sizes.blocks), "\(sender.id) to \(receiver.id)")
            #expect(decoded.result.profile_id == receiver.id)
        }
    }

    /// Pins the mask: its seed, one high bit per SplitMix64 output, most significant bit first,
    /// running on across the first block's CRC into the second block, and restarting per frame.
    /// An unscrambled decoder returns the bytes it decoded for every block, valid or not, so a
    /// scrambled all-zero frame read by its unscrambled twin shows the mask at payload positions.
    @Test func maskIsThePublishedSequence() throws {
        let scrambled = try #require(C3V2.registryProfile(id: 6))
        let plain = try #require(C3V2.registryProfile(id: 1))
        let sizes = try #require(C3V2.sizes(scrambled))
        let zeros = [UInt8](repeating: 0, count: sizes.payloadBytes)
        let bits = maskBits(sizes.blocks * 260 * 8)
        // Payload byte `index` of block `block` sits 260 bytes per block into the framed stream.
        let expected = (0..<sizes.payloadBytes).map { offset -> UInt8 in
            let framedByte = (offset / 256) * 260 + offset % 256
            return (0..<8).reduce(UInt8(0)) { ($0 << 1) | bits[framedByte * 8 + $1] }
        }
        // The same bytes computed by a third implementation, outside this repository's code.
        #expect(Array(expected[0..<8]) == [0x21, 0x83, 0xC5, 0xBA, 0xB1, 0xB5, 0x5E, 0x61])
        #expect(Array(expected[256..<264]) == [0x48, 0x80, 0x62, 0x83, 0x3E, 0xB9, 0x9F, 0x9C])
        for _ in 0..<2 {
            let samples = try #require(C3V2.encode(scrambled, payload: zeros))
            let decoded = try #require(C3V2.decode(plain, samples: samples))
            #expect(decoded.payload == expected)
        }
    }

    /// Damage that loses some blocks: every block the mask calls valid holds the transmitted
    /// bytes at its own offset, including blocks that follow a lost one. The noise level sits on
    /// the decoder's cliff, where about a third of this frame's blocks are lost.
    @Test func damagedScrambledFrameKeepsLaterBlocksInPlace() throws {
        let profile = try #require(C3V2.registryProfile(id: 6))
        let sizes = try #require(C3V2.sizes(profile))
        let payload = Self.sparse.bytes(sizes.payloadBytes)
        var samples = try #require(C3V2.encode(profile, payload: payload))
        var state: UInt32 = 12345
        for index in samples.indices {
            state = state &* 1_664_525 &+ 1_013_904_223
            samples[index] += 0.052 * (Float(state >> 8) / Float(1 << 23) - 1)
        }
        let decoded = try #require(C3V2.decode(profile, samples: samples))
        #expect(decoded.status == CYRINX_STATUS_OK)
        #expect(decoded.mask.allSatisfy { $0 <= 1 })
        let valid = decoded.mask.indices.filter { decoded.mask[$0] == 1 }
        let firstLost = try #require(decoded.mask.firstIndex(of: 0), "the damage lost no block")
        #expect(valid.contains { $0 > firstLost }, "no block was recovered after a lost one")
        #expect(decoded.result.blocks_ok == UInt32(valid.count))
        for block in valid {
            let range = block * 256..<(block + 1) * 256
            #expect(Array(decoded.payload[range]) == Array(payload[range]), "block \(block)")
        }
    }

    /// Profiles the v2 entry points must refuse: each is registered profile 8 with one thing wrong.
    private func refusedProfiles() throws -> [(label: String, profile: cyrinx_profile_v2_t)] {
        let profile = try #require(C3V2.registryProfile(id: 8))
        var stale = profile
        stale.identity_sha256.0 ^= 0xFF
        var v1Header = profile
        v1Header.abi_version = CYRINX_PROFILE_ABI_VERSION
        var unsupported = profile
        unsupported.payload_scrambler = 2
        return [("a stale digest", stale), ("a v1 header", v1Header), ("an unsupported mode", unsupported)]
    }

    @Test func v2EncodeKeepsTheBatchContract() throws {
        var profile = try #require(C3V2.registryProfile(id: 8))
        let sizes = try #require(C3V2.sizes(profile))
        #expect(sizes.blocks == Int(profile.block_count))
        #expect(sizes.payloadBytes == sizes.blocks * 256)
        let payload = C3V2.varied(sizes.payloadBytes, seed: 3)
        var samples = [Float](repeating: 0, count: sizes.frameSamples)
        var result = C3Support.emptyResult()

        // Rejections write nothing, to the samples or to the result.
        for (label, value) in try refusedProfiles() {
            var bad = value
            #expect(
                cyrinx_batch_encode_v2(&bad, payload, payload.count, &samples, samples.count, &result)
                    == CYRINX_STATUS_ERR_INVALID_ARGUMENT, "encode with \(label)")
            #expect(samples.allSatisfy { $0 == 0 }, "encode with \(label) wrote samples")
            #expect(result.logical_frame_samples == 0, "encode with \(label) wrote a result")
        }
        #expect(
            cyrinx_batch_encode_v2(&profile, nil, 0, &samples, samples.count, &result)
                == CYRINX_STATUS_ERR_INVALID_ARGUMENT)
        let tooLong = payload + [0]
        #expect(
            cyrinx_batch_encode_v2(&profile, tooLong, tooLong.count, &samples, samples.count, &result)
                == CYRINX_STATUS_ERR_INVALID_ARGUMENT)
        #expect(samples.allSatisfy { $0 == 0 })
        #expect(result.logical_frame_samples == 0)

        #expect(
            cyrinx_batch_encode_v2(&profile, payload, payload.count, &samples, samples.count, &result)
                == CYRINX_STATUS_OK)
        #expect(result.samples_produced == UInt64(sizes.frameSamples))
        #expect(result.profile_id == 8)
    }

    @Test func v2DecodeKeepsTheBatchContract() throws {
        let profile = try #require(C3V2.registryProfile(id: 8))
        let sizes = try #require(C3V2.sizes(profile))
        let samples = try #require(C3V2.encode(profile, payload: C3V2.varied(sizes.payloadBytes, seed: 3)))

        // A bad profile is refused before any output is written.
        for (label, value) in try refusedProfiles() {
            let refused = C3V2.decode(
                value, samples: samples, payloadBytes: sizes.payloadBytes, blocks: sizes.blocks)
            #expect(refused.status == CYRINX_STATUS_ERR_INVALID_ARGUMENT, "decode with \(label)")
            #expect(refused.payload.allSatisfy { $0 == 0 }, "decode with \(label) wrote payload")
            #expect(refused.mask.allSatisfy { $0 == 0xEE }, "decode with \(label) wrote a mask")
            #expect(refused.result.blocks_total == 0, "decode with \(label) wrote a result")
        }
        // A buffer that is too small reports the sizes needed and writes no payload or mask.
        let small = C3V2.decode(profile, samples: samples, payloadBytes: 16, blocks: sizes.blocks)
        #expect(small.status == CYRINX_STATUS_ERR_BUFFER_TOO_SMALL)
        #expect(small.result.payload_bytes_required == UInt64(sizes.payloadBytes))
        #expect(small.result.block_validity_required == UInt32(sizes.blocks))
        #expect(small.payload.allSatisfy { $0 == 0 })
        #expect(small.mask.allSatisfy { $0 == 0xEE })
        let shortMask = C3V2.decode(
            profile, samples: samples, payloadBytes: sizes.payloadBytes, blocks: sizes.blocks - 1)
        #expect(shortMask.status == CYRINX_STATUS_ERR_BUFFER_TOO_SMALL)
        #expect(shortMask.mask.allSatisfy { $0 == 0xEE })
    }
}
