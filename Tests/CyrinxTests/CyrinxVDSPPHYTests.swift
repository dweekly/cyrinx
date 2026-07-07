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

    func testMultiMCSOFDMRoundTrip() throws {
        let payload = Data((0..<96).map { UInt8($0 % 251) })
        let config = VDSPOFDMConfig(sampleRateHz: 48_000)

        // Test 16-QAM (mode = 2)
        let samples16 = try VDSPPHY.modulateOFDM(payload: Array(payload), mode: 2, config: config)
        XCTAssertFalse(samples16.isEmpty)
        let decoded16 = try VDSPPHY.demodulateOFDM(samples: samples16, mode: 2, config: config)
        if decoded16 != Array(payload) {
            print("--- 16-QAM MISMATCH DETAILS ---")
            for i in 0..<max(decoded16.count, payload.count) {
                let dVal = i < decoded16.count ? "\(decoded16[i])" : "nil"
                let pVal = i < payload.count ? "\(payload[i])" : "nil"
                if dVal != pVal {
                    print("Index \(i): decoded=\(dVal), expected=\(pVal)")
                }
            }
        }
        XCTAssertEqual(decoded16, Array(payload))

        // Test 64-QAM (mode = 3)
        let samples64 = try VDSPPHY.modulateOFDM(payload: Array(payload), mode: 3, config: config)
        XCTAssertFalse(samples64.isEmpty)
        let decoded64 = try VDSPPHY.demodulateOFDM(samples: samples64, mode: 3, config: config)
        if decoded64 != Array(payload) {
            print("--- 64-QAM MISMATCH DETAILS ---")
            for i in 0..<max(decoded64.count, payload.count) {
                let dVal = i < decoded64.count ? "\(decoded64[i])" : "nil"
                let pVal = i < payload.count ? "\(payload[i])" : "nil"
                if dVal != pVal {
                    print("Index \(i): decoded=\(dVal), expected=\(pVal)")
                }
            }
        }
        XCTAssertEqual(decoded64, Array(payload))
    }

    func testOFDMRotationAndTimingParity() throws {
        let payload = Data((0..<96).map { UInt8($0 % 251) })
        let config = VDSPOFDMConfig(sampleRateHz: 48_000)
        let samples = try VDSPPHY.modulateOFDM(payload: Array(payload), mode: 2, config: config)

        var shiftedSamples = [Float](repeating: 0, count: samples.count)
        let symbolLength = config.fftSize + config.cpSamples
        let frameCount = samples.count / symbolLength

        for frameIdx in 0..<frameCount {
            let start = frameIdx * symbolLength
            let frame = Array(samples[start..<(start + symbolLength)])

            // Shift by exactly -2 samples (timing offset) and negate/scale (phase offset)
            var shiftedFrame = [Float](repeating: 0, count: symbolLength)
            for i in 0..<symbolLength {
                let srcIdx = i + 2
                if srcIdx < symbolLength {
                    // Negation corresponds to 180 degree phase rotation!
                    shiftedFrame[i] = -0.9 * frame[srcIdx]
                }
            }
            for i in 0..<symbolLength {
                shiftedSamples[start + i] = shiftedFrame[i]
            }
        }

        let decoded = try VDSPPHY.demodulateOFDM(
            samples: shiftedSamples, mode: 2, config: config, expectedLength: payload.count)
        XCTAssertEqual(decoded, Array(payload))
    }
}
