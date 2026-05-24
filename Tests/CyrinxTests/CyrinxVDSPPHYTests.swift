import XCTest

@testable import Cyrinx

final class CyrinxVDSPPHYTests: XCTestCase {
    func testOFDMQPSKRoundTrip() throws {
        let payload = Data((0..<96).map { UInt8($0 % 251) })
        let config = VDSPOFDMConfig(sampleRateHz: 48_000)
        let samples = try VDSPPHY.modulateOFDMQPSK(payload: Array(payload), config: config)
        XCTAssertFalse(samples.isEmpty)

        let decoded = try VDSPPHY.demodulateOFDMQPSK(samples: samples, config: config)
        XCTAssertEqual(decoded, Array(payload))
    }

    func testOFDMQPSKDeterministicWaveform() throws {
        let payload: [UInt8] = [7, 9, 13, 21, 42, 84, 168]
        let config = VDSPOFDMConfig(sampleRateHz: 48_000)

        let a = try VDSPPHY.modulateOFDMQPSK(payload: payload, config: config)
        let b = try VDSPPHY.modulateOFDMQPSK(payload: payload, config: config)
        XCTAssertEqual(a, b)
    }

    func testDCSSRoundTrip() throws {
        let payload = Data((0..<40).map { UInt8(($0 * 3) % 251) })
        let config = VDSPDCSSConfig(sampleRateHz: 48_000, symbolSamples: 256, symbolBins: 256)
        let samples = try VDSPPHY.modulateDCSS(payload: Array(payload), config: config)
        XCTAssertFalse(samples.isEmpty)

        let decoded = try VDSPPHY.demodulateDCSS(samples: samples, config: config)
        XCTAssertEqual(decoded, Array(payload))
    }

    func testDCSSRejectsIncompleteFrame() throws {
        let payload: [UInt8] = [1, 2, 3, 4]
        let config = VDSPDCSSConfig(sampleRateHz: 48_000, symbolSamples: 256, symbolBins: 256)
        let samples = try VDSPPHY.modulateDCSS(payload: payload, config: config)
        let truncated = Array(samples.prefix(200))

        XCTAssertThrowsError(try VDSPPHY.demodulateDCSS(samples: truncated, config: config))
    }

    func testEqualizerPreEmphasisFilter() throws {
        let payload = Data((0..<96).map { UInt8($0 % 251) })
        
        // Modulate with peer device signature 0 (generic, no EQ)
        let configGeneric = VDSPOFDMConfig(sampleRateHz: 48_000, peerDeviceSignature: 0)
        let samplesGeneric = try VDSPPHY.modulateOFDMQPSK(payload: Array(payload), config: configGeneric)
        
        // Modulate with peer device signature 1 (MacBook Pro, EQ pre-emphasis)
        let configMac = VDSPOFDMConfig(sampleRateHz: 48_000, peerDeviceSignature: 0x01)
        let samplesMac = try VDSPPHY.modulateOFDMQPSK(payload: Array(payload), config: configMac)
        
        // Pre-emphasis must change the waveform amplitudes (frequency-specific boost)
        XCTAssertNotEqual(samplesGeneric, samplesMac)
        
        // Verify peak safety cap (no sample should exceed 0.95 in absolute value)
        for s in samplesMac {
            XCTAssertLessThanOrEqual(abs(s), 0.9501)
        }
        
        // Verify we can still demodulate the pre-emphasized waveform cleanly
        let decoded = try VDSPPHY.demodulateOFDMQPSK(samples: samplesMac, config: configMac)
        XCTAssertEqual(decoded, Array(payload))
    }
}
