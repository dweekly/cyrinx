import CCyrinx
import Foundation
import Testing

@testable import Cyrinx

@Suite("Cyrinx Profile Registry and Wrapper Tests")
struct CyrinxProfileTests {

    @Test("Verify registry counts and lookup by index/id")
    func testRegistryLookup() throws {
        let count = CyrinxProfileRegistry.count
        #expect(count == 5)

        // Check index lookup
        for i in 0..<count {
            let profile = try #require(CyrinxProfileRegistry.get(index: i))
            #expect(profile.id == UInt32(i + 1))

            // Check lookup by ID matches
            let byId = try #require(CyrinxProfileRegistry.get(id: profile.id))
            #expect(byId == profile)

            // Verify C and Swift validation passes
            #expect(CyrinxProfileRegistry.validate(profile))
        }
    }

    @Test("Verify reject unknown IDs and out-of-bounds indices")
    func testInvalidLookups() {
        #expect(CyrinxProfileRegistry.get(id: 0) == nil)
        #expect(CyrinxProfileRegistry.get(id: 6) == nil)
        #expect(CyrinxProfileRegistry.get(id: 999) == nil)
        #expect(CyrinxProfileRegistry.get(index: -1) == nil)
        #expect(CyrinxProfileRegistry.get(index: 5) == nil)
    }

    @Test("Verify profile configurations properties")
    func testProfileDetails() throws {
        // 1. Stable Cyrinx 1.x Control Profile (Default: 16-QAM, rate 3/4, CP 768, etc.)
        let p1 = try #require(CyrinxProfileRegistry.get(id: 1))
        #expect(p1.classification == .compatibility)
        #expect(p1.modulation == .qam16)
        #expect(p1.codeRate == .rate3_4)
        #expect(p1.fftSize == 2048)
        #expect(p1.cyclicPrefix == 768)
        #expect(p1.sampleRate == 48000)

        // 2. Moto G 2026 Near-Field High-Goodput Profile (16-QAM, rate 3/4, CP 240, etc.)
        let p2 = try #require(CyrinxProfileRegistry.get(id: 2))
        #expect(p2.classification == .qualified)
        #expect(p2.modulation == .qam16)
        #expect(p2.codeRate == .rate3_4)
        #expect(p2.fftSize == 2048)
        #expect(p2.cyclicPrefix == 240)

        // 3. Pixel 7a Near-Field Flagship Profile (64-QAM, rate 2/3, CP 96, etc.)
        let p3 = try #require(CyrinxProfileRegistry.get(id: 3))
        #expect(p3.classification == .qualified)
        #expect(p3.modulation == .qam64)
        #expect(p3.codeRate == .rate2_3)
        #expect(p3.fftSize == 2048)
        #expect(p3.cyclicPrefix == 96)

        // 4. Pixel 7a Near-Field Peak-Goodput Research Profile (64-QAM, rate 2/3, CP 96, pilot spacing 64, symbols 96, etc.)
        let p4 = try #require(CyrinxProfileRegistry.get(id: 4))
        #expect(p4.classification == .experimental)
        #expect(p4.modulation == .qam64)
        #expect(p4.codeRate == .rate2_3)
        #expect(p4.fftSize == 2048)
        #expect(p4.cyclicPrefix == 96)
        #expect(p4.pilotEvery == 64)
        #expect(p4.symbolCount == 96)

        // 5. Cyrinx 2.x Experimental Profile (64-QAM, rate 5/6, CP 96, etc.)
        let p5 = try #require(CyrinxProfileRegistry.get(id: 5))
        #expect(p5.classification == .experimental)
        #expect(p5.modulation == .qam64)
        #expect(p5.codeRate == .rate5_6)
        #expect(p5.fftSize == 2048)
        #expect(p5.cyclicPrefix == 96)
    }

    @Test("Verify mapping profiles to BulkPHY.Configuration and physical correctness")
    func testBulkPHYIntegration() throws {
        let count = CyrinxProfileRegistry.count
        for i in 0..<count {
            let profile = try #require(CyrinxProfileRegistry.get(index: i))
            let config = profile.toBulkConfiguration()
            let phy = BulkPHY(configuration: config)
            let geom = try #require(phy.geometry())
            #expect(geom.blockCount == Int(profile.blockCount))
        }
    }

    @Test("Verify invalid version and struct size rejection")
    func testInvalidABIHeader() throws {
        let p1 = try #require(CyrinxProfileRegistry.get(id: 1))
        var raw = p1.toCStruct()

        // Test invalid struct size
        raw.struct_size = MemoryLayout<cyrinx_profile_t>.size - 1
        #expect(cyrinx_profile_validate(&raw) != 0)

        // Test invalid ABI version
        raw.struct_size = MemoryLayout<cyrinx_profile_t>.size
        raw.abi_version = 999
        #expect(cyrinx_profile_validate(&raw) != 0)
    }

    @Test("Verify invalid parameter ranges rejection")
    func testInvalidParameterValidation() throws {
        let p1 = try #require(CyrinxProfileRegistry.get(id: 1))

        // Helper to validate raw C struct and check if it's invalid
        func checkInvalid(_ modifier: (inout cyrinx_profile_t) -> Void) {
            var raw = p1.toCStruct()
            modifier(&raw)
            #expect(cyrinx_profile_validate(&raw) != 0)
        }

        // Test invalid modulation
        checkInvalid { $0.modulation = 99 }

        // Test invalid code rate
        checkInvalid { $0.code_rate = 99 }

        // Test invalid classification
        checkInvalid { $0.classification = 99 }

        // Test invalid frequencies
        checkInvalid { $0.low_frequency_hz = -100.0 }
        checkInvalid {
            $0.high_frequency_hz = 50.0
            $0.low_frequency_hz = 100.0
        }

        // Test invalid FFT/CP
        checkInvalid { $0.fft_size = 0 }
        checkInvalid { $0.fft_size = 2047 }  // odd FFT size
        checkInvalid { $0.cyclic_prefix = 2049 }  // CP > FFT size

        // Test invalid pilot
        checkInvalid { $0.pilot_every = 0 }

        // Test invalid symbol count
        checkInvalid { $0.symbol_count = 0 }

        // Test invalid amplitude
        checkInvalid { $0.amplitude = -0.5 }
        checkInvalid { $0.amplitude = 0.0 }
        checkInvalid { $0.amplitude = 1.5 }

        // Test invalid clip sigma
        checkInvalid { $0.clip_sigma = -1.0 }
        checkInvalid { $0.clip_sigma = 0.0 }
    }

    @Test("Verify profile hash validation and round-tripping")
    func testHashRoundTripping() throws {
        let p1 = try #require(CyrinxProfileRegistry.get(id: 1))

        // 1. Modifying a configuration field must break the hash and cause validation to fail.
        var modified = CyrinxProfile(
            id: p1.id,
            classification: p1.classification,
            modulation: p1.modulation,
            codeRate: p1.codeRate,
            lowFrequencyHz: p1.lowFrequencyHz,
            highFrequencyHz: p1.highFrequencyHz,
            fftSize: p1.fftSize,
            cyclicPrefix: p1.cyclicPrefix,
            sampleRate: p1.sampleRate,
            pilotEvery: p1.pilotEvery,
            symbolCount: p1.symbolCount,
            blockCount: p1.blockCount,
            amplitude: p1.amplitude + 0.05,  // modified
            clipSigma: p1.clipSigma,
            chirpF0: p1.chirpF0,
            chirpF1: p1.chirpF1,
            hash: p1.hash
        )
        #expect(!CyrinxProfileRegistry.validate(modified))

        // 2. Re-computing the hash for the new parameters must allow it to validate again.
        let newHash = try #require(CyrinxProfileRegistry.computeHash(modified))
        #expect(newHash != p1.hash)

        modified = CyrinxProfile(
            id: modified.id,
            classification: modified.classification,
            modulation: modified.modulation,
            codeRate: modified.codeRate,
            lowFrequencyHz: modified.lowFrequencyHz,
            highFrequencyHz: modified.highFrequencyHz,
            fftSize: modified.fftSize,
            cyclicPrefix: modified.cyclicPrefix,
            sampleRate: modified.sampleRate,
            pilotEvery: modified.pilotEvery,
            symbolCount: modified.symbolCount,
            blockCount: modified.blockCount,
            amplitude: modified.amplitude,
            clipSigma: modified.clipSigma,
            chirpF0: modified.chirpF0,
            chirpF1: modified.chirpF1,
            hash: newHash  // updated hash
        )
        #expect(CyrinxProfileRegistry.validate(modified))
    }
}
