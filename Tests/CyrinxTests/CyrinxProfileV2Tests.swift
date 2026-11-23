import CCyrinx
import Testing

/// The v2 profile type (docs/SCRAMBLER_PLAN.md): its layout, its lookup contracts, the two views
/// of one registry, and validation. Identity is in CyrinxProfileV2IdentityTests.
@Suite struct CyrinxProfileV2Tests {
    @Test func layoutIsTheNamedSizeWithTheNewFieldsAfterTheV1Ones() {
        #expect(MemoryLayout<cyrinx_profile_v2_t>.size == Int(CYRINX_PROFILE_V2_SIZE))
        #expect(MemoryLayout<cyrinx_profile_t>.size == Int(CYRINX_PROFILE_V1_SIZE))
        #expect(MemoryLayout<cyrinx_profile_v2_t>.offset(of: \.payload_scrambler) == 128)
        #expect(MemoryLayout<cyrinx_profile_v2_t>.offset(of: \.reserved0) == 132)
        #expect(MemoryLayout<cyrinx_profile_v2_t>.offset(of: \.identity_sha256) == 96)
    }

    @Test func v1ViewKeepsFiveProfilesAndDoesNotContainScrambledIDs() {
        #expect(cyrinx_profile_get_count() == 5)
        // A true 128-byte allocation: the v1 lookup writes exactly the v1 structure.
        let found = C3V2.sentinel(bytes: 128, .v1) { cyrinx_profile_get_by_id(1, C3V2.v1($0)) }
        #expect(found.status == CYRINX_STATUS_OK)
        #expect(found.raw[8] == 1)
        for id in UInt32(6)...10 {
            let missing = C3V2.sentinel(bytes: 128, .v1) {
                cyrinx_profile_get_by_id(id, C3V2.v1($0))
            }
            #expect(missing.status == CYRINX_STATUS_ERR_NOT_FOUND, "v1 lookup of scrambled ID \(id)")
            #expect(missing.untouched, "v1 lookup of scrambled ID \(id) wrote output")
        }
        let pastEnd = C3V2.sentinel(bytes: 128, .v1) {
            cyrinx_profile_get_by_index(5, C3V2.v1($0))
        }
        #expect(pastEnd.status == CYRINX_STATUS_ERR_NOT_FOUND)
        #expect(pastEnd.untouched)
    }

    @Test func v2ViewHoldsTheV1ProfilesThenTheirScrambledTwins() throws {
        #expect(cyrinx_profile_v2_get_count() == 10)
        var ids = Set<UInt32>()
        var identities = Set<String>()
        for index in 0..<10 {
            var profile = C3V2.emptyProfile()
            #expect(cyrinx_profile_v2_get_by_index(index, &profile) == CYRINX_STATUS_OK)
            #expect(cyrinx_profile_v2_validate(&profile) == CYRINX_STATUS_OK, "profile \(profile.id)")
            #expect(profile.struct_size == CYRINX_PROFILE_V2_SIZE)
            #expect(profile.reserved0 == 0)
            ids.insert(profile.id)
            identities.insert(C3V2.identityHex(profile))

            var parent = C3Support.emptyProfile()
            #expect(cyrinx_profile_get_by_index(index % 5, &parent) == CYRINX_STATUS_OK)
            if index < 5 {
                // The same profile in both views: every field after the header, digest included.
                #expect(profile.payload_scrambler == CYRINX_PAYLOAD_SCRAMBLER_NONE.rawValue)
                #expect(C3V2.bytes(profile, 8..<128) == C3V2.bytes(parent, 8..<128), "profile \(profile.id)")
            } else {
                // A twin: its parent's parameters and block count, its own ID, mode and digest.
                #expect(profile.id == parent.id + 5)
                #expect(profile.payload_scrambler == CYRINX_PAYLOAD_SCRAMBLER_FRAMED_BITS_V1.rawValue)
                #expect(profile.classification == CYRINX_CLASSIFICATION_EXPERIMENTAL.rawValue)
                #expect(C3V2.bytes(profile, 16..<96) == C3V2.bytes(parent, 16..<96), "profile \(profile.id)")
                #expect(C3V2.identityHex(profile) != C3Support.identityHex(parent))
            }
            let byID = try #require(C3V2.registryProfile(id: profile.id))
            #expect(C3V2.bytes(byID, 0..<136) == C3V2.bytes(profile, 0..<136))
        }
        #expect(ids == Set(1...10))
        #expect(identities.count == 10)
    }

    @Test func v2LookupAcceptsExactAndOversizedStructuresOnly() {
        let exact = C3V2.sentinel(bytes: 136, .v2) {
            cyrinx_profile_v2_get_by_id(6, C3V2.v2($0))
        }
        #expect(exact.status == CYRINX_STATUS_OK)
        #expect(exact.raw[8] == 6)
        #expect(exact.raw[128] == 1)

        let oversized = C3V2.sentinel(bytes: 152, C3V2.Header(structSize: 152, version: 2)) {
            cyrinx_profile_v2_get_by_index(5, C3V2.v2($0))
        }
        #expect(oversized.status == CYRINX_STATUS_OK)
        #expect(oversized.raw[0] == 152, "the caller's struct_size is kept")
        #expect(oversized.raw[8] == 6)
        #expect(oversized.raw[136...].allSatisfy { $0 == 0xAB }, "bytes past the v2 size were written")

        // Every rejection leaves the caller's bytes as they were.
        var rejections: [(label: String, header: C3V2.Header)] = []
        rejections.append(("a v1-sized structure", C3V2.Header(structSize: 128, version: 2)))
        rejections.append(("one byte short", C3V2.Header(structSize: 135, version: 2)))
        rejections.append(("a v1 header", C3V2.Header(structSize: 136, version: 1)))
        rejections.append(("an unknown version", C3V2.Header(structSize: 136, version: 3)))
        let expected = CYRINX_STATUS_ERR_INVALID_ARGUMENT
        for (label, header) in rejections {
            let byID = C3V2.sentinel(bytes: 136, header) {
                cyrinx_profile_v2_get_by_id(6, C3V2.v2($0))
            }
            #expect(byID.status == expected, "lookup by ID with \(label)")
            #expect(byID.untouched, "lookup by ID with \(label) wrote output")
            let byIndex = C3V2.sentinel(bytes: 136, header) {
                cyrinx_profile_v2_get_by_index(0, C3V2.v2($0))
            }
            #expect(byIndex.status == expected, "lookup by index with \(label)")
            #expect(byIndex.untouched, "lookup by index with \(label) wrote output")
        }
        let unknownID = C3V2.sentinel(bytes: 136, .v2) {
            cyrinx_profile_v2_get_by_id(11, C3V2.v2($0))
        }
        #expect(unknownID.status == CYRINX_STATUS_ERR_NOT_FOUND)
        #expect(unknownID.untouched)
        let pastEnd = C3V2.sentinel(bytes: 136, .v2) {
            cyrinx_profile_v2_get_by_index(10, C3V2.v2($0))
        }
        #expect(pastEnd.status == CYRINX_STATUS_ERR_NOT_FOUND)
        #expect(pastEnd.untouched)
        #expect(cyrinx_profile_v2_get_by_id(6, nil) == CYRINX_STATUS_ERR_INVALID_ARGUMENT)
        #expect(cyrinx_profile_v2_get_by_index(0, nil) == CYRINX_STATUS_ERR_INVALID_ARGUMENT)
    }

    /// A v1 lookup into a larger buffer writes the v1 structure and nothing after it, as before.
    @Test func v1LookupLeavesAnOversizedTailAlone() {
        let oversized = C3V2.sentinel(bytes: 144, C3V2.Header(structSize: 144, version: 1)) {
            cyrinx_profile_get_by_id(3, C3V2.v1($0))
        }
        #expect(oversized.status == CYRINX_STATUS_OK)
        #expect(oversized.raw[8] == 3)
        #expect(oversized.raw[128...].allSatisfy { $0 == 0xAB })
    }

    @Test func validationRejectsWhatTheLibraryDoesNotImplement() throws {
        let scrambled = try #require(C3V2.registryProfile(id: 6))

        var unsupported = scrambled
        unsupported.payload_scrambler = 2
        #expect(cyrinx_profile_v2_validate(&unsupported) == CYRINX_STATUS_ERR_INVALID_ARGUMENT)
        // No identity exists for a mode the library does not implement, so it cannot be stamped.
        #expect(C3V2.restamp(&unsupported) == CYRINX_STATUS_ERR_INVALID_ARGUMENT)

        var reserved = scrambled
        reserved.reserved0 = 1
        #expect(cyrinx_profile_v2_validate(&reserved) == CYRINX_STATUS_ERR_INVALID_ARGUMENT)

        var staleDigest = scrambled
        staleDigest.identity_sha256.0 ^= 0xFF
        #expect(cyrinx_profile_v2_validate(&staleDigest) == CYRINX_STATUS_ERR_INVALID_ARGUMENT)

        var staleBlocks = scrambled
        staleBlocks.block_count += 1
        #expect(cyrinx_profile_v2_validate(&staleBlocks) == CYRINX_STATUS_ERR_INVALID_ARGUMENT)

        var nonFinite = scrambled
        nonFinite.amplitude = .infinity
        #expect(cyrinx_profile_v2_validate(&nonFinite) == CYRINX_STATUS_ERR_INVALID_ARGUMENT)

        var v1Header = scrambled
        v1Header.abi_version = CYRINX_PROFILE_ABI_VERSION
        #expect(cyrinx_profile_v2_validate(&v1Header) == CYRINX_STATUS_ERR_INVALID_ARGUMENT)
        #expect(cyrinx_profile_v2_validate(nil) == CYRINX_STATUS_ERR_INVALID_ARGUMENT)
    }
}
