import CCyrinx
import Foundation
import Testing

/// The scrambled golden case: a frame made by the Python reference modem with the payload
/// scrambler on (scratch/hw20k/modem.py), compared with the C core through the v2 batch entry
/// points. The Python scrambler was written from the plan's text, so agreement here is between
/// two implementations of it.
@Suite struct CyrinxScrambledGoldenTests {
    private struct Fixture {
        let golden: GoldenVectors.Case
        let profile: cyrinx_profile_v2_t

        func bytes(_ stage: String) throws -> [UInt8] {
            GoldenVectors.bytes(try data(stage))
        }

        func floats(_ stage: String) throws -> [Float] {
            GoldenVectors.float32s(try data(stage))
        }

        private func data(_ stage: String) throws -> Data {
            let artifact = try #require(golden.artifacts.first { $0.stage == stage }, "missing \(stage)")
            return try GoldenVectors.rawData(golden.name, artifact)
        }
    }

    /// Every scrambled case in the manifest, each with the fixture profile its config describes.
    private func fixtures() throws -> [Fixture] {
        let scrambled = try GoldenVectors.loadManifest().cases.filter { $0.config.isScrambled }
        try #require(!scrambled.isEmpty, "the manifest holds no scrambled case")
        return try scrambled.map { golden in
            let config = golden.config
            var profile = C3V2.emptyProfile()
            profile.id = 0x7F00_0001
            profile.classification = CYRINX_CLASSIFICATION_INTERNAL_TEST_FIXTURE.rawValue
            profile.modulation = try #require([1: 0, 2: 1, 4: 2, 6: 3, 8: 4][config.bitsPerBin])
            profile.code_rate = try #require(["1/2": 0, "2/3": 1, "3/4": 2, "5/6": 3][config.rate])
            profile.low_frequency_hz = config.fLo
            profile.high_frequency_hz = config.fHi
            profile.fft_size = UInt32(config.nfft)
            profile.cyclic_prefix = UInt32(config.cp)
            profile.sample_rate = UInt32(config.sr)
            profile.pilot_every = UInt32(config.pilotEvery)
            profile.symbol_count = UInt32(config.nSym)
            profile.block_count = UInt32(config.nBlocks)
            profile.amplitude = config.amp
            profile.clip_sigma = config.clipSigma
            profile.chirp_f0 = config.chirpF0
            profile.chirp_f1 = config.chirpF1
            profile.payload_scrambler = UInt32(try #require(config.payloadScrambler))
            #expect(C3V2.restamp(&profile) == CYRINX_STATUS_OK)
            #expect(cyrinx_profile_v2_validate(&profile) == CYRINX_STATUS_OK, "\(golden.name)")
            return Fixture(golden: golden, profile: profile)
        }
    }

    /// The generator computed the profile's v2 identity with hashlib; the C library must agree.
    @Test func fixtureProfileHasTheIdentityTheGeneratorPinned() throws {
        for fixture in try fixtures() {
            let pinned = try #require(fixture.golden.config.profileIdentitySha256)
            #expect(C3V2.identityHex(fixture.profile) == pinned, "\(fixture.golden.name)")
            #expect(fixture.profile.payload_scrambler == CYRINX_PAYLOAD_SCRAMBLER_FRAMED_BITS_V1.rawValue)
        }
    }

    /// The Python frame's information bits are its framed bits XORed with the mask, as this
    /// test computes the mask, followed by the unmasked padding. The case has more than one
    /// block, so the mask is checked across the first CRC into the second block.
    @Test func referenceInfoBitsAreTheFramedBitsUnderTheMask() throws {
        for fixture in try fixtures() {
            let framed = try fixture.bytes("stream_with_crc")
            let info = try fixture.bytes("info_bits")
            let padding = try fixture.bytes("pad_info_bits")
            #expect(fixture.golden.config.nBlocks >= 2)
            #expect(framed.count == fixture.golden.config.nBlocks * 260)
            var state: UInt64 = 0x4359_5258_5343_5231
            var expected = [UInt8]()
            expected.reserveCapacity(info.count)
            for byte in framed {
                for bit in 0..<8 {
                    state &+= 0x9E37_79B9_7F4A_7C15
                    var z = state
                    z = (z ^ (z >> 30)) &* 0xBF58_476D_1CE4_E5B9
                    z = (z ^ (z >> 27)) &* 0x94D0_49BB_1331_11EB
                    expected.append(((byte >> (7 - bit)) & 1) ^ UInt8((z ^ (z >> 31)) >> 63))
                }
            }
            #expect(info == expected + padding, "\(fixture.golden.name)")
        }
    }

    /// Transmit: the C core's frame for the case's payload matches the Python waveform.
    @Test func encodedFrameMatchesTheReferenceWaveform() throws {
        let tolerance = Float(try GoldenVectors.loadManifest().floatAbsTol)
        for fixture in try fixtures() {
            let reference = try fixture.floats("wave")
            let samples = try #require(C3V2.encode(fixture.profile, payload: try fixture.bytes("payload")))
            try #require(samples.count == reference.count, "\(fixture.golden.name): frame length")
            let worst = zip(samples, reference).map { abs($0 - $1) }.max() ?? 0
            #expect(worst < tolerance, "\(fixture.golden.name): largest sample difference \(worst)")
        }
    }

    /// Receive: the C core decodes Python-made audio, sent through the generator's echo channel.
    @Test func referenceAudioDecodesToThePayload() throws {
        for fixture in try fixtures() {
            let decoded = try #require(C3V2.decode(fixture.profile, samples: try fixture.floats("rx_wave")))
            #expect(decoded.status == CYRINX_STATUS_OK)
            #expect(decoded.mask == [UInt8](repeating: 1, count: fixture.golden.config.nBlocks))
            #expect(decoded.payload == (try fixture.bytes("decoded_payload")))
            #expect(decoded.payload == (try fixture.bytes("payload")))
        }
    }

    /// The case's payload is one the unscrambled codec loses, and the scrambler is what saves
    /// it: the same audio read by the unscrambled twin of the profile recovers no block, and
    /// the generator's record of the peak before the clip is on the right side of each bound.
    @Test func payloadIsOneTheScramblerRescues() throws {
        for fixture in try fixtures() {
            let config = fixture.golden.config
            #expect(try #require(config.preClipPeakToRms) <= 6.5)
            #expect(try #require(config.unscrambledPreClipPeakToRms) >= 10)

            var twin = fixture.profile
            twin.payload_scrambler = CYRINX_PAYLOAD_SCRAMBLER_NONE.rawValue
            #expect(C3V2.restamp(&twin) == CYRINX_STATUS_OK)
            let payload = try fixture.bytes("payload")
            let plain = try #require(C3V2.encode(twin, payload: payload))
            let lost = try #require(C3V2.decode(twin, samples: plain))
            #expect(lost.result.blocks_ok == 0, "the payload survives without the scrambler")
            let crossed = try #require(C3V2.decode(twin, samples: try fixture.floats("rx_wave")))
            #expect(crossed.result.blocks_ok == 0)
        }
    }
}
