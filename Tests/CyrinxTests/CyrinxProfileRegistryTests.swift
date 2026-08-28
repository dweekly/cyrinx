import CCyrinx
import Foundation
import Testing

/// C3-04: the profile registry — lookup contracts, the prefix copy rule,
/// identity serialization/digest behavior, validation boundaries, and parity
/// with the canonical JSON registry source.
@Suite struct CyrinxProfileRegistryTests {
    @Test func registryHasFiveUniqueProfiles() {
        #expect(cyrinx_profile_get_count() == 5)
        var ids = Set<UInt32>()
        var identities = Set<String>()
        for index in 0..<cyrinx_profile_get_count() {
            var profile = C3Support.emptyProfile()
            #expect(cyrinx_profile_get_by_index(index, &profile) == CYRINX_STATUS_OK)
            ids.insert(profile.id)
            identities.insert(C3Support.identityHex(profile))
        }
        #expect(ids.count == 5)
        #expect(identities.count == 5)
    }

    @Test func everyRegisteredProfileValidates() {
        for index in 0..<cyrinx_profile_get_count() {
            var profile = C3Support.emptyProfile()
            #expect(cyrinx_profile_get_by_index(index, &profile) == CYRINX_STATUS_OK)
            #expect(cyrinx_profile_validate(&profile) == CYRINX_STATUS_OK, "profile \(profile.id)")
            #expect(profile.block_count > 0)
        }
    }

    @Test func lookupRejectsBadPrefixesAndUnknownKeys() {
        var profile = C3Support.emptyProfile()
        #expect(cyrinx_profile_get_by_id(9999, &profile) == CYRINX_STATUS_ERR_NOT_FOUND)
        #expect(
            cyrinx_profile_get_by_index(cyrinx_profile_get_count(), &profile)
                == CYRINX_STATUS_ERR_NOT_FOUND)
        #expect(cyrinx_profile_get_by_index(0, nil) == CYRINX_STATUS_ERR_INVALID_ARGUMENT)

        profile.struct_size = UInt32(MemoryLayout<cyrinx_profile_t>.size) - 8
        #expect(cyrinx_profile_get_by_index(0, &profile) == CYRINX_STATUS_ERR_INVALID_ARGUMENT)

        profile = C3Support.emptyProfile()
        profile.abi_version = 2
        #expect(cyrinx_profile_get_by_index(0, &profile) == CYRINX_STATUS_ERR_INVALID_ARGUMENT)
    }

    @Test func oversizedCallerTailIsPreserved() {
        let v1Size = MemoryLayout<cyrinx_profile_t>.size
        let totalSize = v1Size + 16
        let raw = UnsafeMutableRawPointer.allocate(byteCount: totalSize, alignment: 8)
        defer { raw.deallocate() }
        raw.initializeMemory(as: UInt8.self, repeating: 0xAB, count: totalSize)

        let profile = raw.bindMemory(to: cyrinx_profile_t.self, capacity: 1)
        profile.pointee.struct_size = UInt32(totalSize)
        profile.pointee.abi_version = CYRINX_PROFILE_ABI_VERSION
        #expect(cyrinx_profile_get_by_index(0, profile) == CYRINX_STATUS_OK)
        #expect(profile.pointee.struct_size == UInt32(totalSize))
        #expect(profile.pointee.id == 1)
        let tail = raw.advanced(by: v1Size).bindMemory(to: UInt8.self, capacity: 16)
        for i in 0..<16 {
            #expect(tail[i] == 0xAB, "tail byte \(i)")
        }
    }

    @Test func identitySerializationIsPinned() throws {
        var profile = try #require(C3Support.registryProfile(id: 1))
        var serialized = [UInt8](repeating: 0, count: Int(CYRINX_PROFILE_IDENTITY_V1_SERIALIZED_SIZE))
        #expect(
            cyrinx_profile_serialize_identity_v1(&profile, &serialized, serialized.count)
                == CYRINX_STATUS_OK)
        let fixture = try registryFixture()
        let expectedHex = try #require(fixture.profiles.first { $0.id == 1 }?.identitySerializedHex)
        #expect(C3Support.digestHex(serialized) == expectedHex)

        var short = [UInt8](repeating: 0, count: 8)
        #expect(
            cyrinx_profile_serialize_identity_v1(&profile, &short, short.count)
                == CYRINX_STATUS_ERR_BUFFER_TOO_SMALL)
    }

    @Test func identityExcludesClassificationIdAndBlockCount() throws {
        var profile = try #require(C3Support.registryProfile(id: 1))
        var baseline = [UInt8](repeating: 0, count: 32)
        #expect(cyrinx_profile_compute_identity(&profile, &baseline) == CYRINX_STATUS_OK)

        profile.classification = CYRINX_CLASSIFICATION_EXPERIMENTAL.rawValue
        profile.id = 77
        profile.block_count &+= 1
        var mutated = [UInt8](repeating: 0, count: 32)
        #expect(cyrinx_profile_compute_identity(&profile, &mutated) == CYRINX_STATUS_OK)
        #expect(baseline == mutated)
    }

    @Test func identityCoversEveryWireParameter() throws {
        let profile = try #require(C3Support.registryProfile(id: 1))
        var baseline = [UInt8](repeating: 0, count: 32)
        var mutable = profile
        #expect(cyrinx_profile_compute_identity(&mutable, &baseline) == CYRINX_STATUS_OK)

        var mutations: [(inout cyrinx_profile_t) -> Void] = []
        mutations.append { $0.low_frequency_hz += 1 }
        mutations.append { $0.high_frequency_hz -= 1 }
        mutations.append { $0.fft_size = 4096 }
        mutations.append { $0.cyclic_prefix += 1 }
        mutations.append { $0.sample_rate = 44100 }
        mutations.append { $0.pilot_every += 1 }
        mutations.append { $0.symbol_count += 1 }
        mutations.append { $0.modulation = CYRINX_MODULATION_QPSK.rawValue }
        mutations.append { $0.code_rate = CYRINX_CODE_RATE_1_2.rawValue }
        mutations.append { $0.amplitude += 0.01 }
        mutations.append { $0.clip_sigma += 0.1 }
        mutations.append { $0.chirp_f0 += 1 }
        mutations.append { $0.chirp_f1 -= 1 }
        for (index, mutate) in mutations.enumerated() {
            var changed = profile
            mutate(&changed)
            var digest = [UInt8](repeating: 0, count: 32)
            #expect(cyrinx_profile_compute_identity(&changed, &digest) == CYRINX_STATUS_OK)
            #expect(digest != baseline, "mutation \(index) must change identity")
        }
    }

    @Test func validationRejectsCorruptProfiles() throws {
        let profile = try #require(C3Support.registryProfile(id: 1))

        var staleDigest = profile
        staleDigest.identity_sha256.0 ^= 0xFF
        #expect(cyrinx_profile_validate(&staleDigest) == CYRINX_STATUS_ERR_INVALID_ARGUMENT)

        var staleParams = profile
        staleParams.cyclic_prefix += 1
        #expect(cyrinx_profile_validate(&staleParams) == CYRINX_STATUS_ERR_INVALID_ARGUMENT)

        var nonFinite = profile
        nonFinite.low_frequency_hz = .nan
        #expect(cyrinx_profile_validate(&nonFinite) == CYRINX_STATUS_ERR_INVALID_ARGUMENT)
        #expect(cyrinx_profile_compute_identity(&nonFinite, nil) == CYRINX_STATUS_ERR_INVALID_ARGUMENT)

        var beyondNyquist = profile
        beyondNyquist.high_frequency_hz = Double(profile.sample_rate) / 2.0 + 1.0
        #expect(cyrinx_profile_validate(&beyondNyquist) == CYRINX_STATUS_ERR_INVALID_ARGUMENT)

        var staleBlockCount = profile
        staleBlockCount.block_count += 1
        #expect(cyrinx_profile_validate(&staleBlockCount) == CYRINX_STATUS_ERR_INVALID_ARGUMENT)
    }

    @Test func registryMatchesCanonicalJSON() throws {
        let fixture = try registryFixture()
        #expect(fixture.profiles.count == Int(cyrinx_profile_get_count()))
        for row in fixture.profiles {
            let profile = try #require(C3Support.registryProfile(id: row.id), "profile \(row.id)")
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
            #expect(C3Support.identityHex(profile) == row.identitySha256, "digest of profile \(row.id)")
        }
    }

    private func registryFixture() throws -> RegistryFixture {
        let url = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("Fixtures/profiles/cyrinx_profiles_v1.json")
        let fixture = try JSONDecoder().decode(RegistryFixture.self, from: Data(contentsOf: url))
        #expect(fixture.identityScheme == "sha256/cyrinx-profile-identity-v1")
        return fixture
    }
}

struct RegistryFixture: Decodable {

    var identityScheme: String
    var profiles: [RegistryFixtureRow]
}

struct RegistryFixtureRow: Decodable {
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
    var identitySha256: String
    var identitySerializedHex: String?
}
