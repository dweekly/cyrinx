import CCyrinx
import Testing

@testable import Cyrinx

/// Pins the transmitted waveform of every released profile.
///
/// Profile identity digests cover parameters, not samples: a change in shared modulation code
/// can leave every digest alone and still alter the sound on the wire. These references are the
/// samples themselves, for one fixed payload per profile, so such a change fails here.
///
/// Two digests are kept per profile. The exact one is over the float32 samples and is checked
/// only on the platform it was recorded on, macOS arm64, where the default build and the
/// Accelerate build both reproduce it. The 16-bit one is over each sample rounded to a 16-bit
/// integer, so that low-order numeric differences on another platform are unlikely to move it
/// while any real change to the waveform does. It has been measured on macOS arm64 only.
///
/// To change a reference deliberately, the profile's waveform must be meant to change. Released
/// profiles 1 to 5 are not: a new format gets a new profile ID (docs/SCRAMBLER_PLAN.md).
@Suite struct ReleasedWaveformTests {
    struct Reference: Sendable, CustomTestStringConvertible {
        let id: UInt32
        let sixteenBit: String
        let exact: String
        var testDescription: String { "profile \(id)" }
    }

    static let one = Reference(
        id: 1, sixteenBit: "389c6af6ce7b8a90e5d8a54da764167abfeab021dc0cd5b3200883fa05f77d07",
        exact: "20bb10edaa42a445b2cdb310ac4410fa140a7e35fb2459a5e58b0701f9841736")
    static let two = Reference(
        id: 2, sixteenBit: "53937932a2a7247dc2cb4f4afbac233fb1b43447c09a75a2eec73df9e5b99fc0",
        exact: "5936930dfac4ba7985288acf9abec2c1f94004e2c56a7b25ad9c62b7f88a7811")
    static let three = Reference(
        id: 3, sixteenBit: "56d2bef2f0d41856ce881726d2f6ae372b9ac368ac081e829b1761be574d4992",
        exact: "993370798bdbfed88f7b0ccc9b9ef0a87562c319a732a1ff613bbb96025a5f84")
    static let four = Reference(
        id: 4, sixteenBit: "3903970b19e53816987efa084bb1bf38812e4340a7f35d7b272d5f374b5c8c37",
        exact: "397c3ae42c4bf96e5b1ed07b5e0d8ddef29fd4c2a0e988242f57337511d66319")
    static let five = Reference(
        id: 5, sixteenBit: "feaee3f3307a8a8c2f9ace7bfcc3ad2baef048867a5005823d2f118a71518b11",
        exact: "ff13fb67e4a26f0880a7933734e246b1dc5ba80144ae76ba3be49c1e45fab3d8")
    static let references = [one, two, three, four, five]

    /// A fixed, varied payload: a linear congruential sequence seeded by the profile ID.
    static func payload(_ count: Int, seed: UInt32) -> [UInt8] {
        var state = seed
        return (0..<count).map { _ in
            state = state &* 1_664_525 &+ 1_013_904_223
            return UInt8(truncatingIfNeeded: state >> 24)
        }
    }

    static func digests(id: UInt32) throws -> (sixteenBit: String, exact: String) {
        let profile = try BatchPHY.Profile(id: id)
        let samples = try BatchPHY.encode(payload(profile.payloadByteCount, seed: id), profile: profile)
            .samples
        var quantized = [UInt8]()
        var exact = [UInt8]()
        quantized.reserveCapacity(samples.count * 2)
        exact.reserveCapacity(samples.count * 4)
        for sample in samples {
            let level = Int16((sample * 32767).rounded())
            withUnsafeBytes(of: level.littleEndian) { quantized.append(contentsOf: $0) }
            withUnsafeBytes(of: sample.bitPattern.littleEndian) { exact.append(contentsOf: $0) }
        }
        return (C3Support.sha256Hex(quantized), C3Support.sha256Hex(exact))
    }

    @Test(arguments: references)
    func transmittedWaveformIsUnchanged(reference: Reference) throws {
        let observed = try Self.digests(id: reference.id)
        #expect(observed.sixteenBit == reference.sixteenBit)
        #if os(macOS) && arch(arm64)
            #expect(observed.exact == reference.exact)
        #endif
    }

    @Test func everyRegisteredProfileHasAReference() throws {
        #expect(try BatchPHY.Profile.registered().map(\.id) == Self.references.map(\.id))
    }

    /// The reference would be worthless if it could not see a change: one flipped payload bit
    /// must move both digests.
    @Test func aOneBitPayloadChangeMovesTheDigests() throws {
        let profile = try BatchPHY.Profile(id: 1)
        var changed = Self.payload(profile.payloadByteCount, seed: 1)
        changed[0] ^= 1
        let samples = try BatchPHY.encode(changed, profile: profile).samples
        let reference = try BatchPHY.encode(Self.payload(profile.payloadByteCount, seed: 1), profile: profile)
        let differing = zip(samples, reference.samples).filter { abs($0 - $1) > 1.0 / 32767 }.count
        #expect(differing > 100)
    }
}
