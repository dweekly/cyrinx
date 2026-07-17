import Foundation
import XCTest

@testable import Cyrinx

final class CyrinxBulkPHYProfileTests: XCTestCase {
    private func roundTrip(
        _ configuration: BulkPHY.Configuration,
        verifyAutomaticDiversity: Bool = false,
        line: UInt = #line
    ) {
        let phy = BulkPHY(configuration: configuration)
        guard let geometry = phy.geometry() else {
            return XCTFail("geometry nil", line: line)
        }
        let payload = Data(
            (0..<geometry.payloadBytes).map { UInt8(($0 * 31 + 7) & 0xFF) })
        guard let wave = phy.encode(payload) else {
            return XCTFail("encode nil", line: line)
        }
        var capture = [Float](repeating: 0, count: 3000)
        capture.append(contentsOf: wave)
        capture.append(contentsOf: [Float](repeating: 0, count: 2000))

        guard let decoded = phy.decode(capture) else {
            return XCTFail("decode nil", line: line)
        }
        XCTAssertTrue(decoded.isComplete, line: line)
        XCTAssertEqual(decoded.payload, payload, line: line)

        if verifyAutomaticDiversity {
            guard let automatic = phy.decode(capture, automaticallyCombining: capture),
                let diagnostics = automatic.automaticDiversity
            else {
                return XCTFail("automatic diversity decode nil", line: line)
            }
            XCTAssertTrue(automatic.isComplete, line: line)
            XCTAssertEqual(automatic.payload, payload, line: line)
            XCTAssertEqual(diagnostics.selectedReceiver, .primary, line: line)
            XCTAssertEqual(diagnostics.reason, .primaryMarginNotMet, line: line)
            XCTAssertTrue(diagnostics.hasValidScores, line: line)
        }
    }

    func testMotoG2026NearFieldHighGoodputProfilePinsMeasuredGeometry() {
        let configuration = BulkPHY.Configuration.makeMotoG2026NearFieldHighGoodputProfile(
            amplitude: 0.13,
            clipSigma: 3.1)
        let geometry = BulkPHY(configuration: configuration).geometry()

        XCTAssertEqual(configuration.lowFrequencyHz, 1100.0)
        XCTAssertEqual(configuration.highFrequencyHz, 23000.0)
        XCTAssertEqual(configuration.pilotEvery, 8)
        XCTAssertEqual(configuration.bitsPerBin, 4)
        XCTAssertEqual(configuration.rate, "3/4")
        XCTAssertEqual(configuration.symbolCount, 64)
        XCTAssertEqual(configuration.fftSize, 2048)
        XCTAssertEqual(configuration.cyclicPrefix, 240)
        XCTAssertEqual(configuration.sampleRate, 48000)
        XCTAssertEqual(configuration.amplitude, 0.13)
        XCTAssertEqual(configuration.clipSigma, 3.1)
        XCTAssertEqual(configuration.chirpF0, 2000.0)
        XCTAssertEqual(configuration.chirpF1, 16000.0)
        XCTAssertEqual(geometry?.payloadBytes, 19_200)
        XCTAssertEqual(geometry?.blockCount, 75)
        XCTAssertEqual(geometry?.frameSamples, 157_152)
        XCTAssertEqual(geometry?.bitsPerSymbol, 3_272)
        XCTAssertEqual(geometry?.usedBins, 935)
    }

    func testMotoG2026NearFieldHighGoodputProfileRoundTripsFullFrame() {
        let configuration = BulkPHY.Configuration.makeMotoG2026NearFieldHighGoodputProfile(
            amplitude: 0.13)

        roundTrip(configuration)
    }

    func testPixel7aNearFieldFlagshipProfilePinsMeasuredGeometry() {
        let configuration = BulkPHY.Configuration.makePixel7aNearFieldFlagshipProfile(
            amplitude: 0.18,
            clipSigma: 3.2)
        let geometry = BulkPHY(configuration: configuration).geometry()

        XCTAssertEqual(configuration.lowFrequencyHz, 1100.0)
        XCTAssertEqual(configuration.highFrequencyHz, 23000.0)
        XCTAssertEqual(configuration.pilotEvery, 16)
        XCTAssertEqual(configuration.bitsPerBin, 6)
        XCTAssertEqual(configuration.rate, "2/3")
        XCTAssertEqual(configuration.symbolCount, 64)
        XCTAssertEqual(configuration.fftSize, 2048)
        XCTAssertEqual(configuration.cyclicPrefix, 96)
        XCTAssertEqual(configuration.sampleRate, 48000)
        XCTAssertEqual(configuration.amplitude, 0.18)
        XCTAssertEqual(configuration.clipSigma, 3.2)
        XCTAssertEqual(configuration.chirpF0, 2000.0)
        XCTAssertEqual(configuration.chirpF1, 16000.0)
        XCTAssertEqual(geometry?.payloadBytes, 27_392)
        XCTAssertEqual(geometry?.blockCount, 107)
        XCTAssertEqual(geometry?.frameSamples, 147_648)
        XCTAssertEqual(geometry?.bitsPerSymbol, 5_256)
        XCTAssertEqual(geometry?.usedBins, 935)
    }

    func testPixel7aNearFieldFlagshipProfileRoundTripsFullFrame() {
        let configuration = BulkPHY.Configuration.makePixel7aNearFieldFlagshipProfile(
            amplitude: 0.18)

        roundTrip(configuration, verifyAutomaticDiversity: true)
    }

    func testPixel7aNearFieldPeakGoodputProfilePinsMeasuredGeometry() {
        let configuration = BulkPHY.Configuration.makePixel7aNearFieldPeakGoodputProfile(
            amplitude: 0.18,
            clipSigma: 3.2)
        let geometry = BulkPHY(configuration: configuration).geometry()

        XCTAssertEqual(configuration.lowFrequencyHz, 1100.0)
        XCTAssertEqual(configuration.highFrequencyHz, 23000.0)
        XCTAssertEqual(configuration.pilotEvery, 64)
        XCTAssertEqual(configuration.bitsPerBin, 6)
        XCTAssertEqual(configuration.rate, "2/3")
        XCTAssertEqual(configuration.symbolCount, 96)
        XCTAssertEqual(configuration.fftSize, 2048)
        XCTAssertEqual(configuration.cyclicPrefix, 96)
        XCTAssertEqual(configuration.sampleRate, 48000)
        XCTAssertEqual(configuration.amplitude, 0.18)
        XCTAssertEqual(configuration.clipSigma, 3.2)
        XCTAssertEqual(configuration.chirpF0, 2000.0)
        XCTAssertEqual(configuration.chirpF1, 16000.0)
        XCTAssertEqual(geometry?.payloadBytes, 43_264)
        XCTAssertEqual(geometry?.blockCount, 169)
        XCTAssertEqual(geometry?.frameSamples, 216_256)
        XCTAssertEqual(geometry?.bitsPerSymbol, 5_520)
        XCTAssertEqual(geometry?.usedBins, 935)
    }

    func testPixel7aNearFieldPeakGoodputProfileRoundTripsFullFrame() {
        let configuration = BulkPHY.Configuration.makePixel7aNearFieldPeakGoodputProfile(
            amplitude: 0.18)

        roundTrip(configuration, verifyAutomaticDiversity: true)
    }

    @available(*, deprecated, message: "Regression coverage for the deprecated compatibility API")
    func testDeprecatedCyrinx2FastProfilePinsCompatibilityGeometry() {
        let configuration = BulkPHY.Configuration.makeCyrinx2Fast(amplitude: 0.13)
        let geometry = BulkPHY(configuration: configuration).geometry()

        XCTAssertEqual(configuration.pilotEvery, 16)
        XCTAssertEqual(configuration.bitsPerBin, 6)
        XCTAssertEqual(configuration.rate, "5/6")
        XCTAssertEqual(configuration.symbolCount, 64)
        XCTAssertEqual(configuration.cyclicPrefix, 96)
        XCTAssertEqual(geometry?.payloadBytes, 34_304)
        XCTAssertEqual(geometry?.blockCount, 134)
        XCTAssertEqual(geometry?.frameSamples, 147_648)
        XCTAssertEqual(geometry?.bitsPerSymbol, 5_256)
        XCTAssertEqual(geometry?.usedBins, 935)
    }
}
