import CCyrinx
import CV1HeaderConsumer
import Foundation
import Testing

/// Identity of v2 profiles (docs/SCRAMBLER_PLAN.md): the scheme each registry row uses, what the
/// v1 functions do when handed a v2 structure, a consumer built against the old header, and
/// parity with the canonical JSON source.
@Suite struct CyrinxProfileV2IdentityTests {
    @Test func changingTheModeChangesTheIdentity() throws {
        let scrambled = try #require(C3V2.registryProfile(id: 6))
        let parent = try #require(C3V2.registryProfile(id: 1))

        // The mode alone, with the digest left as it was: no longer a valid profile.
        var switchedOff = scrambled
        switchedOff.payload_scrambler = CYRINX_PAYLOAD_SCRAMBLER_NONE.rawValue
        #expect(cyrinx_profile_v2_validate(&switchedOff) == CYRINX_STATUS_ERR_INVALID_ARGUMENT)
        var switchedOn = parent
        switchedOn.payload_scrambler = CYRINX_PAYLOAD_SCRAMBLER_FRAMED_BITS_V1.rawValue
        #expect(cyrinx_profile_v2_validate(&switchedOn) == CYRINX_STATUS_ERR_INVALID_ARGUMENT)

        // Restamped, each becomes the other's wire format and takes the other's identity.
        #expect(C3V2.restamp(&switchedOff) == CYRINX_STATUS_OK)
        #expect(C3V2.identityHex(switchedOff) == C3V2.identityHex(parent))
        #expect(C3V2.restamp(&switchedOn) == CYRINX_STATUS_OK)
        #expect(C3V2.identityHex(switchedOn) == C3V2.identityHex(scrambled))
        #expect(C3V2.identityHex(scrambled) != C3V2.identityHex(parent))
    }

    @Test func identityStreamIsTheV1OneUnlessScrambled() throws {
        var parent = try #require(C3V2.registryProfile(id: 1))
        var parentV1 = try #require(C3Support.registryProfile(id: 1))
        var expected = [UInt8](repeating: 0, count: Int(CYRINX_PROFILE_IDENTITY_V1_SERIALIZED_SIZE))
        #expect(
            cyrinx_profile_serialize_identity_v1(&parentV1, &expected, expected.count) == CYRINX_STATUS_OK)

        var stream = [UInt8](repeating: 0xAB, count: 120)
        var length = 0
        #expect(
            cyrinx_profile_v2_serialize_identity(&parent, &stream, stream.count, &length) == CYRINX_STATUS_OK)
        #expect(length == Int(CYRINX_PROFILE_IDENTITY_V1_SERIALIZED_SIZE))
        #expect(Array(stream[..<length]) == expected)
        #expect(stream[length...].allSatisfy { $0 == 0xAB })

        var scrambled = try #require(C3V2.registryProfile(id: 6))
        stream = [UInt8](repeating: 0xAB, count: 120)
        #expect(
            cyrinx_profile_v2_serialize_identity(&scrambled, &stream, stream.count, &length)
                == CYRINX_STATUS_OK)
        #expect(length == Int(CYRINX_PROFILE_IDENTITY_V2_SERIALIZED_SIZE))
        #expect(Array(stream[..<26]) == Array("cyrinx-profile-identity-v2".utf8))
        // The v1 fields follow the domain unchanged, then the mode as a big-endian 32-bit value.
        #expect(Array(stream[26..<102]) == Array(expected[26...]))
        #expect(Array(stream[102..<106]) == [0, 0, 0, 1])
        #expect(stream[106...].allSatisfy { $0 == 0xAB })

        // One byte too few for the scrambled stream, though enough for the v1 one: nothing written.
        var short = [UInt8](repeating: 0xAB, count: 105)
        length = -1
        #expect(
            cyrinx_profile_v2_serialize_identity(&scrambled, &short, short.count, &length)
                == CYRINX_STATUS_ERR_BUFFER_TOO_SMALL)
        #expect(length == -1)
        #expect(short.allSatisfy { $0 == 0xAB })
    }

    /// A v2 structure handed to a v1 function through a cast is refused, so a scrambled profile
    /// cannot be given its parent's identity or sent through the unscrambled path by mistake.
    @Test func v1FunctionsRefuseAV2Structure() throws {
        var scrambled = try #require(C3V2.registryProfile(id: 6))
        withUnsafeMutablePointer(to: &scrambled) { pointer in
            pointer.withMemoryRebound(to: cyrinx_profile_t.self, capacity: 1) { asV1 in
                var stream = [UInt8](repeating: 0xAB, count: 120)
                #expect(
                    cyrinx_profile_serialize_identity_v1(asV1, &stream, stream.count)
                        == CYRINX_STATUS_ERR_INVALID_ARGUMENT)
                #expect(stream.allSatisfy { $0 == 0xAB })
                var digest = [UInt8](repeating: 0xAB, count: 32)
                #expect(cyrinx_profile_compute_identity(asV1, &digest) == CYRINX_STATUS_ERR_INVALID_ARGUMENT)
                #expect(digest.allSatisfy { $0 == 0xAB })
                #expect(cyrinx_profile_validate(asV1) == CYRINX_STATUS_ERR_INVALID_ARGUMENT)
                var byte: UInt8 = 0
                var sample: Float = 0
                var result = C3Support.emptyResult()
                #expect(
                    cyrinx_batch_encode(asV1, &byte, 0, &sample, 0, &result)
                        == CYRINX_STATUS_ERR_INVALID_ARGUMENT)
                #expect(result.logical_frame_samples == 0)
            }
        }
    }

    /// Code built against the header from before the v2 type, run against this library, still
    /// enumerates the five profiles it knew, validates them, and does not find a scrambled ID.
    @Test func consumerBuiltAgainstTheOldHeaderSeesFiveUnchangedProfiles() throws {
        #expect(cyrinx_v1_header_consumer_profile_size() == CYRINX_PROFILE_V1_SIZE)
        #expect(cyrinx_v1_header_consumer_count() == 5)
        for index in 0..<5 {
            var id: UInt32 = 0
            var identity = [UInt8](repeating: 0, count: 32)
            #expect(cyrinx_v1_header_consumer_profile_at(index, &id, &identity) == CYRINX_STATUS_OK)
            let current = try #require(C3Support.registryProfile(id: UInt32(index + 1)))
            #expect(id == current.id)
            #expect(C3Support.digestHex(identity) == C3Support.identityHex(current))
        }
        var id: UInt32 = 0
        var identity = [UInt8](repeating: 0, count: 32)
        #expect(cyrinx_v1_header_consumer_profile_at(5, &id, &identity) == CYRINX_STATUS_ERR_NOT_FOUND)
        #expect(cyrinx_v1_header_consumer_lookup(3) == CYRINX_STATUS_OK)
        #expect(cyrinx_v1_header_consumer_lookup(8) == CYRINX_STATUS_ERR_NOT_FOUND)
    }

    @Test func v2RegistryMatchesCanonicalJSON() throws {
        let url = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("Fixtures/profiles/cyrinx_profiles_v2.json")
        let fixture = try JSONDecoder().decode(RegistryV2Fixture.self, from: Data(contentsOf: url))
        #expect(fixture.profiles.count == Int(cyrinx_profile_v2_get_count()))
        for (index, row) in fixture.profiles.enumerated() {
            var profile = C3V2.emptyProfile()
            #expect(cyrinx_profile_v2_get_by_index(index, &profile) == CYRINX_STATUS_OK)
            #expect(profile.id == row.id, "order of row \(index)")
            #expect(profile.classification == row.classification)
            #expect(profile.modulation == row.modulation)
            #expect(profile.code_rate == row.codeRate)
            #expect(profile.low_frequency_hz == row.lowFrequencyHz)
            #expect(profile.high_frequency_hz == row.highFrequencyHz)
            #expect(profile.fft_size == row.fftSize)
            #expect(profile.cyclic_prefix == row.cyclicPrefix)
            #expect(profile.sample_rate == row.sampleRate)
            #expect(profile.pilot_every == row.pilotEvery)
            #expect(profile.symbol_count == row.symbolCount)
            #expect(profile.block_count == row.blockCount)
            #expect(profile.amplitude == row.amplitude)
            #expect(profile.clip_sigma == row.clipSigma)
            #expect(profile.chirp_f0 == row.chirpF0)
            #expect(profile.chirp_f1 == row.chirpF1)
            #expect(profile.payload_scrambler == row.payloadScrambler)
            // The digest in the file was computed outside the C library.
            #expect(C3V2.identityHex(profile) == row.identitySha256, "digest of profile \(row.id)")
            let scheme = row.payloadScrambler == 0 ? "v1" : "v2"
            #expect(row.identityScheme == "sha256/cyrinx-profile-identity-\(scheme)", "scheme of \(row.id)")
            if let expectedHex = row.identitySerializedHex {
                var stream = [UInt8](repeating: 0, count: Int(CYRINX_PROFILE_IDENTITY_V2_SERIALIZED_SIZE))
                var length = 0
                #expect(
                    cyrinx_profile_v2_serialize_identity(&profile, &stream, stream.count, &length)
                        == CYRINX_STATUS_OK)
                #expect(C3Support.digestHex(Array(stream[..<length])) == expectedHex)
                #expect(C3Support.sha256Hex(Array(stream[..<length])) == row.identitySha256)
            }
        }
        #expect(fixture.profiles.contains { $0.identitySerializedHex != nil }, "no pinned v2 byte string")
    }
}

struct RegistryV2Fixture: Decodable {
    var profiles: [RegistryV2FixtureRow]
}

struct RegistryV2FixtureRow: Decodable {
    var id: UInt32
    var classification: UInt32
    var modulation: UInt32
    var codeRate: UInt32
    var lowFrequencyHz: Double
    var highFrequencyHz: Double
    var fftSize: UInt32
    var cyclicPrefix: UInt32
    var sampleRate: UInt32
    var pilotEvery: UInt32
    var symbolCount: UInt32
    var blockCount: UInt32
    var amplitude: Double
    var clipSigma: Double
    var chirpF0: Double
    var chirpF1: Double
    var payloadScrambler: UInt32
    var identityScheme: String
    var identitySha256: String
    var identitySerializedHex: String?
}
