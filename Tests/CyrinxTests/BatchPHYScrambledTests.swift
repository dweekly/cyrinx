import CCyrinx
import Testing

@testable import Cyrinx

/// `BatchPHY` over the scrambled profiles: which lists they appear in, what a short payload does
/// on each kind of profile, and custom profiles in both C layouts.
@Suite struct BatchPHYScrambledTests {
    /// The lengths docs/SCRAMBLER_PLAN.md names, for a profile holding `capacity` bytes.
    private func lengths(_ capacity: Int) -> [Int] {
        [0, 1, 128, 255, 256, 257, capacity - 1, capacity]
    }

    private func expectInvalid(_ label: String, _ body: () throws -> Void) {
        do {
            try body()
            Issue.record("\(label) was accepted")
        } catch BatchPHY.Failure.invalidArgument {
        } catch {
            Issue.record("\(label) failed with \(error)")
        }
    }

    @Test func registeredListKeepsItsFiveAndTheFullListAddsTheTwins() throws {
        let plain = try BatchPHY.Profile.registered()
        #expect(plain.map(\.id) == [1, 2, 3, 4, 5])
        #expect(plain.allSatisfy { !$0.isScrambled })
        #expect(plain.allSatisfy { $0.payloadScrambler == CYRINX_PAYLOAD_SCRAMBLER_NONE.rawValue })

        let all = try BatchPHY.Profile.registeredIncludingScrambled()
        #expect(all.map(\.id) == Array(1...10))
        #expect(all.prefix(5).map(\.identitySHA256) == plain.map(\.identitySHA256))
        #expect(all.suffix(5).allSatisfy { $0.isScrambled })
        #expect(
            all.suffix(5).allSatisfy {
                $0.payloadScrambler == CYRINX_PAYLOAD_SCRAMBLER_FRAMED_BITS_V1.rawValue
            })
        #expect(Set(all.map(\.identitySHA256)).count == 10)
    }

    @Test(arguments: UInt32(6)...10)
    func scrambledProfileIsItsParentWithAnotherIdentity(id: UInt32) throws {
        let profile = try BatchPHY.Profile(id: id)
        let parent = try BatchPHY.Profile(id: id - 5)
        let raw = try #require(C3V2.registryProfile(id: id))
        #expect(profile.isScrambled)
        #expect(profile.identitySHA256 == withUnsafeBytes(of: raw.identity_sha256) { Array($0) })
        #expect(profile.identitySHA256 != parent.identitySHA256)
        #expect(profile.payloadByteCount == parent.payloadByteCount)
        #expect(profile.frameSampleCount == parent.frameSampleCount)
        #expect(profile.blockCount == parent.blockCount)
        #expect(profile.modulation == parent.modulation)
        #expect(profile.codeRate == parent.codeRate)
        #expect(profile.cyclicPrefix == parent.cyclicPrefix)
        #expect(profile.amplitude == parent.amplitude)
    }

    /// A scrambled profile takes any length up to a full frame, gives the waveform of the same
    /// bytes followed by zeros, and decodes to that whole frame.
    @Test(arguments: UInt32(6)...10)
    func scrambledProfileAcceptsShortPayloads(id: UInt32) throws {
        let profile = try BatchPHY.Profile(id: id)
        let capacity = profile.payloadByteCount
        for length in lengths(capacity) {
            let payload = C3V2.varied(length, seed: id &+ UInt32(length))
            let frame = payload + [UInt8](repeating: 0, count: capacity - length)
            let short = try BatchPHY.encode(payload, profile: profile)
            let padded = try BatchPHY.encode(frame, profile: profile)
            #expect(short.samples.map(\.bitPattern) == padded.samples.map(\.bitPattern), "\(length) bytes")
            let decoded = try BatchPHY.decode(primary: .init(short.samples), profile: profile)
            #expect(decoded.payload == frame, "\(length) bytes")
            #expect(decoded.metrics.blocksOK == profile.blockCount, "\(length) bytes")
            #expect(decoded.metrics.profileID == id)
            #expect(decoded.metrics.profileIdentitySHA256 == profile.identitySHA256)
        }
        expectInvalid("one byte too many on profile \(id)") {
            _ = try BatchPHY.encode([UInt8](repeating: 1, count: capacity + 1), profile: profile)
        }
    }

    /// An unscrambled profile still takes a full frame and nothing else.
    @Test(arguments: UInt32(1)...5)
    func unscrambledProfileStillRefusesShortPayloads(id: UInt32) throws {
        let profile = try BatchPHY.Profile(id: id)
        let capacity = profile.payloadByteCount
        for length in lengths(capacity) + [capacity + 1] where length != capacity {
            expectInvalid("\(length) bytes on profile \(id)") {
                _ = try BatchPHY.encode(C3V2.varied(length, seed: 5), profile: profile)
            }
        }
        _ = try BatchPHY.encode(C3V2.varied(capacity, seed: 5), profile: profile)
    }

    /// A custom profile in each C layout: the v1 form of a small test geometry, and the same
    /// geometry scrambled in the v2 form.
    @Test func customProfilesInBothLayouts() throws {
        var v1 = try #require(C3Support.registryProfile(id: 1))
        v1.symbol_count = 4
        v1.classification = CYRINX_CLASSIFICATION_INTERNAL_TEST_FIXTURE.rawValue
        v1.block_count = UInt32(try #require(C3Support.geometry(for: v1)).n_blocks)
        var digest = [UInt8](repeating: 0, count: 32)
        #expect(cyrinx_profile_compute_identity(&v1, &digest) == CYRINX_STATUS_OK)
        withUnsafeMutableBytes(of: &v1.identity_sha256) { $0.copyBytes(from: digest) }
        let plain = try BatchPHY.Profile(validating: v1)
        #expect(!plain.isScrambled)
        #expect(plain.identitySHA256 == digest)
        #expect(plain.blockCount == v1.block_count)

        // A v1 structure that would not pass the v1 validation is refused, as before.
        var badHeader = v1
        badHeader.abi_version = 9
        expectInvalid("a v1 profile with an unknown version") {
            _ = try BatchPHY.Profile(validating: badHeader)
        }

        var v2 = try #require(C3V2.registryProfile(id: 6))
        v2.symbol_count = 4
        v2.classification = CYRINX_CLASSIFICATION_INTERNAL_TEST_FIXTURE.rawValue
        v2.block_count = v1.block_count
        #expect(C3V2.restamp(&v2) == CYRINX_STATUS_OK)
        let scrambled = try BatchPHY.Profile(validating: v2)
        #expect(scrambled.isScrambled)
        #expect(scrambled.identitySHA256 != plain.identitySHA256)
        #expect(scrambled.payloadByteCount == plain.payloadByteCount)
        let record = C3V2.varied(40, seed: 11)
        let encoded = try BatchPHY.encode(record, profile: scrambled)
        let decoded = try BatchPHY.decode(primary: .init(encoded.samples), profile: scrambled)
        #expect(Array(decoded.payload.prefix(40)) == record)
        #expect(decoded.payload.dropFirst(40).allSatisfy { $0 == 0 })
        #expect(decoded.metrics.blocksOK == scrambled.blockCount)
    }
}
