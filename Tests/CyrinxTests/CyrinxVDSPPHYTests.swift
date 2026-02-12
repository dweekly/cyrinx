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
}
