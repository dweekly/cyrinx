import Foundation
import XCTest

@testable import Cyrinx

/// Exercises the Swift `BulkPHY` binding over the portable-C codec: a pure-Swift
/// encode → digital-loopback → decode round trip recovers the payload with every
/// CRC block valid. The DSP correctness is pinned by the golden vectors
/// (CyrinxBulkTXTests); this proves the ergonomic Swift surface is wired up.
final class CyrinxBulkPHYTests: XCTestCase {
    private func roundTrip(
        _ config: BulkPHY.Configuration,
        verifyAutomaticDiversity: Bool = false,
        line: UInt = #line
    ) {
        let phy = BulkPHY(configuration: config)
        guard let geo = phy.geometry() else {
            return XCTFail("geometry nil", line: line)
        }
        XCTAssertGreaterThan(geo.payloadBytes, 0, "payload capacity", line: line)

        let payload = Data((0..<geo.payloadBytes).map { UInt8(($0 * 31 + 7) & 0xFF) })
        guard let wave = phy.encode(payload) else {
            return XCTFail("encode nil", line: line)
        }
        XCTAssertEqual(wave.count, geo.frameSamples, "frame length", line: line)

        // digital loopback: leading/trailing silence around the frame
        var rx = [Float](repeating: 0, count: 3000)
        rx.append(contentsOf: wave)
        rx.append(contentsOf: [Float](repeating: 0, count: 2000))

        guard let decoded = phy.decode(rx) else {
            return XCTFail("decode nil", line: line)
        }
        XCTAssertTrue(
            decoded.isComplete, "blocks \(decoded.blocksOK)/\(decoded.blockCount)", line: line)
        XCTAssertEqual(decoded.payload, payload, "payload round trip", line: line)

        if verifyAutomaticDiversity {
            guard let automatic = phy.decode(rx, automaticallyCombining: rx),
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

    func testRoundTripQPSK() {
        roundTrip(.init(bitsPerBin: 2, rate: "1/2", symbolCount: 8))
    }

    func testRoundTrip16QAM() {
        roundTrip(.init(bitsPerBin: 4, rate: "3/4", symbolCount: 8))
    }

    func testRoundTrip64QAM() {
        roundTrip(.init(bitsPerBin: 6, rate: "3/4", symbolCount: 8))
    }

    /// A deterministic 88-bin interferer corrupts one 11-pilot-wide region.
    /// The former global-only pilot-EVM weighting recovered 2/14 blocks from
    /// this exact waveform. Local known-pilot reliability makes those bad-bin
    /// LLRs honest enough for the unchanged interleaver/FEC to recover 14/14.
    func testLocalPilotReliabilityRecoversFrequencySelectiveInterference() {
        let configuration = BulkPHY.Configuration(
            pilotEvery: 8, bitsPerBin: 6, rate: "3/4", symbolCount: 8,
            cyclicPrefix: 96, amplitude: 0.18)
        let phy = BulkPHY(configuration: configuration)
        guard let geometry = phy.geometry() else { return XCTFail("geometry nil") }
        XCTAssertEqual(geometry.blockCount, 14)
        let payload = Data((0..<geometry.payloadBytes).map { UInt8(($0 * 31 + 7) & 0xFF) })
        guard var wave = phy.encode(payload) else { return XCTFail("encode nil") }

        addSelectiveFrequencyInterference(
            to: &wave, configuration: configuration, firstUsedPosition: 40 * 8,
            usedPositionCount: 11 * 8, frequencyAmplitude: 2.573_705_196_380_615_2 * 0.5)
        var capture = [Float](repeating: 0, count: 3000)
        capture.append(contentsOf: wave)
        capture.append(contentsOf: [Float](repeating: 0, count: 2000))

        guard let mono = phy.decode(capture) else { return XCTFail("mono decode nil") }
        XCTAssertTrue(mono.isComplete, "blocks \(mono.blocksOK)/\(mono.blockCount)")
        XCTAssertEqual(mono.payload, payload)
        XCTAssertEqual(mono.evmRMS, 0.131_515_879_046_995_33, accuracy: 1e-6)

        guard let mrc = phy.decode(capture, combining: capture) else {
            return XCTFail("MRC decode nil")
        }
        XCTAssertTrue(mrc.isComplete, "MRC blocks \(mrc.blocksOK)/\(mrc.blockCount)")
        XCTAssertEqual(mrc.payload, payload)
    }

    /// Scaling every data-symbol pilot by the same factor creates uniform
    /// post-correction pilot residual power. The endpoint-replicated boxcar and
    /// interpolation must therefore remain uniform, making the 25:75 blend
    /// algebraically equal to the legacy global EVM term. The frozen legacy
    /// decoder and this decoder both recover 14/14 with EVM 0.402005 on the
    /// resulting waveform.
    func testUniformPilotResidualRemainsTolerant() {
        let configuration = BulkPHY.Configuration(
            pilotEvery: 8, bitsPerBin: 6, rate: "3/4", symbolCount: 8,
            cyclicPrefix: 96, amplitude: 0.18)
        let phy = BulkPHY(configuration: configuration)
        guard let geometry = phy.geometry() else { return XCTFail("geometry nil") }
        let payload = Data((0..<geometry.payloadBytes).map { UInt8(($0 * 31 + 7) & 0xFF) })
        guard var wave = phy.encode(payload) else { return XCTFail("encode nil") }

        scaleEveryDataPilot(to: &wave, configuration: configuration, additionalScale: 0.4)
        var capture = [Float](repeating: 0, count: 3000)
        capture.append(contentsOf: wave)
        capture.append(contentsOf: [Float](repeating: 0, count: 2000))

        guard let decoded = phy.decode(capture) else { return XCTFail("decode nil") }
        XCTAssertTrue(decoded.isComplete, "blocks \(decoded.blocksOK)/\(decoded.blockCount)")
        XCTAssertEqual(decoded.payload, payload)
        XCTAssertEqual(decoded.evmRMS, 0.402_005_042_837_308_87, accuracy: 1e-6)
    }

    /// A wrong-length payload is rejected rather than silently truncated.
    func testEncodeRejectsWrongLength() {
        let phy = BulkPHY(configuration: .init(bitsPerBin: 2, rate: "1/2", symbolCount: 8))
        XCTAssertNil(phy.encode(Data([1, 2, 3])))
    }

    /// Two-mic MRC surface: identical channels decode like mono (plumbing).
    func testMRCDecodeIdenticalChannels() {
        let phy = BulkPHY(configuration: .init(bitsPerBin: 4, rate: "3/4", symbolCount: 8))
        guard let geo = phy.geometry() else { return XCTFail("geometry nil") }
        let payload = Data((0..<geo.payloadBytes).map { UInt8(($0 * 31 + 7) & 0xFF) })
        guard let wave = phy.encode(payload) else { return XCTFail("encode nil") }
        var rx = [Float](repeating: 0, count: 3000)
        rx.append(contentsOf: wave)
        rx.append(contentsOf: [Float](repeating: 0, count: 2000))
        guard let mono = phy.decode(rx) else { return XCTFail("mono decode nil") }
        guard let decoded = phy.decode(rx, combining: rx) else { return XCTFail("decode nil") }
        XCTAssertTrue(decoded.isComplete, "blocks \(decoded.blocksOK)/\(decoded.blockCount)")
        XCTAssertEqual(decoded.payload, payload)
        XCTAssertEqual(decoded.blocksOK, mono.blocksOK)
        XCTAssertEqual(decoded.blockCount, mono.blockCount)
        XCTAssertEqual(decoded.payload, mono.payload)
        XCTAssertEqual(decoded.evmRMS, mono.evmRMS, accuracy: 1e-12)

        guard let automatic = phy.decode(rx, automaticallyCombining: rx),
            let diagnostics = automatic.automaticDiversity
        else { return XCTFail("automatic decode nil") }
        XCTAssertEqual(diagnostics.selectedReceiver, .primary)
        XCTAssertEqual(diagnostics.reason, .primaryMarginNotMet)
        XCTAssertTrue(diagnostics.hasValidScores)
        XCTAssertEqual(diagnostics.policyVersion, 1)
        XCTAssertEqual(diagnostics.abiVersion, 1)
        XCTAssertEqual(
            diagnostics.maximumMRCToPrimaryPilotRMSRatio, 0.95, accuracy: 1e-12)
        XCTAssertEqual(automatic.payload, mono.payload)
        XCTAssertEqual(automatic.blocksOK, mono.blocksOK)
        XCTAssertEqual(automatic.evmRMS, mono.evmRMS, accuracy: 1e-12)
    }

    /// A much noisier second microphone must not corrupt a clean first branch.
    /// The deterministic wideband sequence spans sync and data so its variance
    /// is measured by the two sync symbols and can be downweighted by MRC.
    func testMRCDownweightsNoisySecondChannel() {
        let phy = BulkPHY(
            configuration: .init(bitsPerBin: 4, rate: "3/4", symbolCount: 8))
        guard let geo = phy.geometry() else { return XCTFail("geometry nil") }
        let payload = Data((0..<geo.payloadBytes).map { UInt8(($0 * 31 + 7) & 0xFF) })
        guard let wave = phy.encode(payload) else { return XCTFail("encode nil") }

        var clean = [Float](repeating: 0, count: 3000)
        clean.append(contentsOf: wave)
        clean.append(contentsOf: [Float](repeating: 0, count: 2000))

        var noisy = clean
        var state: UInt32 = 0xC0FF_EE11
        for i in noisy.indices {
            state = 1_664_525 &* state &+ 1_013_904_223
            let unit = Float(state >> 8) / Float(0x00FF_FFFF)
            noisy[i] += 2 * (2 * unit - 1)
        }

        guard let cleanDecoded = phy.decode(clean) else {
            return XCTFail("clean decode nil")
        }
        XCTAssertTrue(cleanDecoded.isComplete)
        if let noisyDecoded = phy.decode(noisy) {
            XCTAssertFalse(noisyDecoded.isComplete, "noisy branch unexpectedly decoded alone")
        }
        guard let combined = phy.decode(clean, combining: noisy) else {
            return XCTFail("MRC decode nil")
        }
        XCTAssertTrue(
            combined.isComplete,
            "noisy branch destroyed clean branch: \(combined.blocksOK)/\(combined.blockCount)")
        XCTAssertEqual(combined.payload, payload)
    }

    /// Two-mic MRC rescue: mic0 keeps only chirp + sync (its data region is
    /// zeroed, so alone it CANNOT decode); mic1 is clean. QPSK survives the
    /// MRC scale factor from mic0's healthy-H/zero-Y data region (sign-only
    /// decisions), so the combined decode recovers everything — deterministic,
    /// no RNG. (The committed golden MRC fixture covers the notched
    /// complementary-band case; this pins the Swift binding surface.)
    func testMRCRescuesZeroedDataMic() {
        let config = BulkPHY.Configuration(bitsPerBin: 2, rate: "1/2", symbolCount: 8)
        let phy = BulkPHY(configuration: config)
        guard let geo = phy.geometry() else { return XCTFail("geometry nil") }
        let payload = Data((0..<geo.payloadBytes).map { UInt8(($0 * 31 + 7) & 0xFF) })
        guard let wave = phy.encode(payload) else { return XCTFail("encode nil") }
        let pre = 3000
        var clean = [Float](repeating: 0, count: pre)
        clean.append(contentsOf: wave)
        clean.append(contentsOf: [Float](repeating: 0, count: 2000))
        // frame layout: chirp(4096) + guard(2048) + 2 sync symbols + data
        let sym = config.fftSize + config.cyclicPrefix
        let dataStart = pre + 4096 + 2048 + 2 * sym
        var mic0 = clean
        for i in dataStart..<mic0.count { mic0[i] = 0 }

        if let mono = phy.decode(mic0) {
            XCTAssertFalse(mono.isComplete, "mic0 alone must not decode")
        }
        guard let mrc = phy.decode(mic0, combining: clean) else {
            return XCTFail("MRC decode nil")
        }
        XCTAssertTrue(mrc.isComplete, "MRC blocks \(mrc.blocksOK)/\(mrc.blockCount)")
        XCTAssertEqual(mrc.payload, payload)

        guard let automatic = phy.decode(mic0, automaticallyCombining: clean),
            let diagnostics = automatic.automaticDiversity
        else { return XCTFail("automatic decode nil") }
        XCTAssertEqual(diagnostics.selectedReceiver, .maximalRatioCombined)
        XCTAssertEqual(diagnostics.reason, .mrcImproved)
        XCTAssertTrue(diagnostics.hasValidScores)
        XCTAssertLessThan(
            diagnostics.observedMRCToPrimaryPilotRMSRatio,
            diagnostics.maximumMRCToPrimaryPilotRMSRatio)
        XCTAssertTrue(automatic.isComplete)
        XCTAssertEqual(automatic.payload, payload)
    }
}

final class CyrinxAutomaticDiversityTests: XCTestCase {
    /// Data-bin corruption must not leak decoded payload or CRC evidence into
    /// the pilot-only automatic-diversity decision.
    func testAutomaticDiversityDiagnosticsIgnoreDataSubcarrier() {
        let config = BulkPHY.Configuration(bitsPerBin: 6, rate: "3/4", symbolCount: 8)
        let phy = BulkPHY(configuration: config)
        guard let geometry = phy.geometry() else { return XCTFail("geometry nil") }
        let payload = Data((0..<geometry.payloadBytes).map { UInt8(($0 * 31 + 7) & 0xFF) })
        guard let wave = phy.encode(payload) else { return XCTFail("encode nil") }
        let leadingSilence = 3000
        var primary = [Float](repeating: 0, count: leadingSilence)
        primary.append(contentsOf: wave)
        primary.append(contentsOf: [Float](repeating: 0, count: 2000))

        let lowBin = Int(
            ceil(
                config.lowFrequencyHz * Double(config.fftSize) / Double(config.sampleRate)))
        let dataBin = lowBin + 1
        XCTAssertNotEqual((dataBin - lowBin) % config.pilotEvery, 0)
        let symbolSamples = config.fftSize + config.cyclicPrefix
        let dataStart = leadingSilence + 4096 + 2048 + 2 * symbolSamples
        var dataCorrupted = primary
        addCyclicDataSubcarrier(
            to: &dataCorrupted, startingAt: dataStart, configuration: config,
            bin: dataBin, amplitude: 1.0)

        guard let baseline = phy.decode(primary, automaticallyCombining: primary),
            let baselineDiagnostics = baseline.automaticDiversity,
            let perturbed = phy.decode(primary, automaticallyCombining: dataCorrupted),
            let perturbedDiagnostics = perturbed.automaticDiversity,
            let rawBaseline = phy.decode(primary, combining: primary),
            let rawPerturbed = phy.decode(primary, combining: dataCorrupted)
        else { return XCTFail("decode nil") }

        XCTAssertTrue(
            rawPerturbed.payload != rawBaseline.payload
                || rawPerturbed.blocksOK != rawBaseline.blocksOK,
            "data-subcarrier perturbation did not alter raw-MRC payload/CRC outcome")
        XCTAssertEqual(perturbedDiagnostics.selectedReceiver, baselineDiagnostics.selectedReceiver)
        XCTAssertEqual(perturbedDiagnostics.reason, baselineDiagnostics.reason)
        XCTAssertEqual(perturbedDiagnostics.hasValidScores, baselineDiagnostics.hasValidScores)
        XCTAssertEqual(
            perturbedDiagnostics.validationObservations,
            baselineDiagnostics.validationObservations)
        XCTAssertEqual(
            perturbedDiagnostics.primaryHoldoutPilotRMS,
            baselineDiagnostics.primaryHoldoutPilotRMS,
            accuracy: 1e-10)
        XCTAssertEqual(
            perturbedDiagnostics.mrcHoldoutPilotRMS,
            baselineDiagnostics.mrcHoldoutPilotRMS,
            accuracy: 1e-7)
        XCTAssertEqual(
            perturbedDiagnostics.observedMRCToPrimaryPilotRMSRatio,
            baselineDiagnostics.observedMRCToPrimaryPilotRMSRatio,
            accuracy: 1e-6)
    }

    /// A secondary branch whose two sync symbols look clean but whose data
    /// region is corrupt must not override a complete primary decode.
    func testAutomaticDiversityRejectsStaleSecondaryConfidence() {
        let config = BulkPHY.Configuration(bitsPerBin: 2, rate: "1/2", symbolCount: 8)
        let phy = BulkPHY(configuration: config)
        guard let geometry = phy.geometry() else { return XCTFail("geometry nil") }
        let payload = Data((0..<geometry.payloadBytes).map { UInt8(($0 * 31 + 7) & 0xFF) })
        guard let wave = phy.encode(payload) else { return XCTFail("encode nil") }
        let leadingSilence = 3000
        var primary = [Float](repeating: 0, count: leadingSilence)
        primary.append(contentsOf: wave)
        primary.append(contentsOf: [Float](repeating: 0, count: 2000))
        var stale = primary
        let symbolSamples = config.fftSize + config.cyclicPrefix
        let dataStart = leadingSilence + 4096 + 2048 + 2 * symbolSamples
        let dataEnd = dataStart + config.symbolCount * symbolSamples
        var state: UInt32 = 0xA17E_5EED
        for index in dataStart..<dataEnd {
            state = 1_664_525 &* state &+ 1_013_904_223
            let unit = Float(state >> 8) / Float(0x00FF_FFFF)
            stale[index] = 2 * unit - 1
        }

        guard let mono = phy.decode(primary) else { return XCTFail("mono decode nil") }
        guard let rawMRC = phy.decode(primary, combining: stale) else {
            return XCTFail("raw MRC decode nil")
        }
        XCTAssertTrue(mono.isComplete)
        XCTAssertFalse(rawMRC.isComplete, "adversarial raw MRC unexpectedly completed")

        guard let automatic = phy.decode(primary, automaticallyCombining: stale),
            let diagnostics = automatic.automaticDiversity
        else { return XCTFail("automatic decode nil") }
        XCTAssertEqual(diagnostics.selectedReceiver, .primary)
        XCTAssertEqual(diagnostics.reason, .primaryMarginNotMet)
        XCTAssertTrue(diagnostics.hasValidScores)
        XCTAssertGreaterThanOrEqual(
            diagnostics.observedMRCToPrimaryPilotRMSRatio,
            diagnostics.maximumMRCToPrimaryPilotRMSRatio)
        XCTAssertEqual(automatic.payload, mono.payload)
        XCTAssertEqual(automatic.blocksOK, mono.blocksOK)
        XCTAssertEqual(automatic.blockCount, mono.blockCount)
        XCTAssertEqual(automatic.evmRMS, mono.evmRMS, accuracy: 1e-12)
    }

    /// Nonfinite secondary data fails closed without contaminating primary arithmetic.
    func testAutomaticDiversityFailsClosedForNonfiniteSecondaryData() {
        let config = BulkPHY.Configuration(bitsPerBin: 2, rate: "1/2", symbolCount: 8)
        let phy = BulkPHY(configuration: config)
        guard let geometry = phy.geometry() else { return XCTFail("geometry nil") }
        let payload = Data((0..<geometry.payloadBytes).map { UInt8(($0 * 31 + 7) & 0xFF) })
        guard let wave = phy.encode(payload) else { return XCTFail("encode nil") }
        let leadingSilence = 3000
        var primary = [Float](repeating: 0, count: leadingSilence)
        primary.append(contentsOf: wave)
        primary.append(contentsOf: [Float](repeating: 0, count: 2000))
        var nonfinite = primary
        let symbolSamples = config.fftSize + config.cyclicPrefix
        let dataStart = leadingSilence + 4096 + 2048 + 2 * symbolSamples
        let dataEnd = dataStart + config.symbolCount * symbolSamples
        for index in dataStart..<dataEnd { nonfinite[index] = .nan }

        guard let mono = phy.decode(primary),
            let automatic = phy.decode(primary, automaticallyCombining: nonfinite),
            let diagnostics = automatic.automaticDiversity
        else { return XCTFail("decode nil") }
        XCTAssertEqual(diagnostics.selectedReceiver, .primary)
        XCTAssertEqual(diagnostics.reason, .nonfiniteScore)
        XCTAssertFalse(diagnostics.hasValidScores)
        XCTAssertTrue(diagnostics.primaryHoldoutPilotRMS.isInfinite)
        XCTAssertTrue(diagnostics.mrcHoldoutPilotRMS.isInfinite)
        XCTAssertEqual(automatic.payload, mono.payload)
        XCTAssertEqual(automatic.blocksOK, mono.blocksOK)
        XCTAssertEqual(automatic.evmRMS, mono.evmRMS, accuracy: 1e-12)
    }

    /// Omitting the second channel preserves the documented mono fallback.
    func testAutomaticDiversityReportsUnavailableSecondChannel() {
        let config = BulkPHY.Configuration(bitsPerBin: 2, rate: "1/2", symbolCount: 8)
        let phy = BulkPHY(configuration: config)
        guard let geometry = phy.geometry() else { return XCTFail("geometry nil") }
        let payload = Data((0..<geometry.payloadBytes).map { UInt8(($0 * 31 + 7) & 0xFF) })
        guard let wave = phy.encode(payload) else { return XCTFail("encode nil") }
        var primary = [Float](repeating: 0, count: 3000)
        primary.append(contentsOf: wave)
        primary.append(contentsOf: [Float](repeating: 0, count: 2000))

        guard let mono = phy.decode(primary),
            let automatic = phy.decode(primary, automaticallyCombining: []),
            let diagnostics = automatic.automaticDiversity
        else { return XCTFail("decode nil") }
        XCTAssertEqual(diagnostics.selectedReceiver, .primary)
        XCTAssertEqual(diagnostics.reason, .secondUnavailable)
        XCTAssertFalse(diagnostics.hasValidScores)
        XCTAssertEqual(diagnostics.validationObservations, 0)
        XCTAssertEqual(automatic.payload, mono.payload)
        XCTAssertEqual(automatic.blocksOK, mono.blocksOK)
        XCTAssertEqual(automatic.evmRMS, mono.evmRMS, accuracy: 1e-12)
    }

    /// Configurations without two train and one holdout pilot fail closed.
    func testAutomaticDiversityFailsClosedWhenPilotsAreInsufficient() {
        let config = BulkPHY.Configuration(
            lowFrequencyHz: 750,
            highFrequencyHz: 7500,
            pilotEvery: 8,
            bitsPerBin: 2,
            rate: "1/2",
            symbolCount: 272,
            fftSize: 64,
            cyclicPrefix: 16)
        let phy = BulkPHY(configuration: config)
        guard let geometry = phy.geometry() else { return XCTFail("geometry nil") }
        let payload = Data((0..<geometry.payloadBytes).map { UInt8(($0 * 31 + 7) & 0xFF) })
        guard let wave = phy.encode(payload) else { return XCTFail("encode nil") }
        var primary = [Float](repeating: 0, count: 3000)
        primary.append(contentsOf: wave)
        primary.append(contentsOf: [Float](repeating: 0, count: 2000))

        guard let mono = phy.decode(primary),
            let automatic = phy.decode(primary, automaticallyCombining: primary),
            let diagnostics = automatic.automaticDiversity
        else { return XCTFail("decode nil") }
        XCTAssertTrue(mono.isComplete)
        XCTAssertEqual(diagnostics.selectedReceiver, .primary)
        XCTAssertEqual(diagnostics.reason, .insufficientPilots)
        XCTAssertFalse(diagnostics.hasValidScores)
        XCTAssertEqual(diagnostics.validationObservations, 0)
        XCTAssertEqual(automatic.payload, mono.payload)
        XCTAssertEqual(automatic.evmRMS, mono.evmRMS, accuracy: 1e-12)
    }
}

/// Adds one real-valued OFDM bin to every data symbol. Indexing the sinusoid
/// from `-cyclicPrefix` makes each prefix an exact continuation of the FFT body.
private func addCyclicDataSubcarrier(
    to samples: inout [Float],
    startingAt dataStart: Int,
    configuration: BulkPHY.Configuration,
    bin: Int,
    amplitude: Double
) {
    let symbolSamples = configuration.fftSize + configuration.cyclicPrefix
    for symbolIndex in 0..<configuration.symbolCount {
        let symbolStart = dataStart + symbolIndex * symbolSamples
        for offset in 0..<symbolSamples {
            let fftIndex = offset - configuration.cyclicPrefix
            let angle =
                2 * Double.pi * Double(bin) * Double(fftIndex) / Double(configuration.fftSize)
            samples[symbolStart + offset] += Float(amplitude * cos(angle))
        }
    }
}

/// Adds deterministic complex frequency-domain noise to a contiguous used-bin
/// region in every data symbol. The inverse real-DFT contribution is evaluated
/// directly so the test remains independent of Accelerate and platform FFTs.
private func addSelectiveFrequencyInterference(
    to samples: inout [Float],
    configuration: BulkPHY.Configuration,
    firstUsedPosition: Int,
    usedPositionCount: Int,
    frequencyAmplitude: Double
) {
    let symbolSamples = configuration.fftSize + configuration.cyclicPrefix
    let dataStart = 4096 + 2048 + 2 * symbolSamples
    let binLow = Int(
        ceil(
            configuration.lowFrequencyHz * Double(configuration.fftSize)
                / Double(configuration.sampleRate)))
    var state: UInt32 = 0xC0FF_EE11
    for symbolIndex in 0..<configuration.symbolCount {
        var coefficients: [(bin: Int, real: Double, imaginary: Double)] = []
        coefficients.reserveCapacity(usedPositionCount)
        for usedPosition in firstUsedPosition..<(firstUsedPosition + usedPositionCount) {
            state = 1_664_525 &* state &+ 1_013_904_223
            let real = 2 * Double(state >> 8) / Double(0x00FF_FFFF) - 1
            state = 1_664_525 &* state &+ 1_013_904_223
            let imaginary = 2 * Double(state >> 8) / Double(0x00FF_FFFF) - 1
            coefficients.append(
                (
                    binLow + usedPosition, frequencyAmplitude * real,
                    frequencyAmplitude * imaginary
                ))
        }
        let symbolStart = dataStart + symbolIndex * symbolSamples
        var delta = [Double](repeating: 0, count: configuration.fftSize)
        for coefficient in coefficients {
            for fftIndex in 0..<configuration.fftSize {
                let angle =
                    2 * Double.pi * Double(coefficient.bin) * Double(fftIndex)
                    / Double(configuration.fftSize)
                delta[fftIndex] +=
                    2
                    * (coefficient.real * cos(angle) - coefficient.imaginary * sin(angle))
                    / Double(configuration.fftSize)
            }
        }
        for fftIndex in 0..<configuration.fftSize {
            let index = symbolStart + configuration.cyclicPrefix + fftIndex
            samples[index] += Float(delta[fftIndex])
        }
        for prefixIndex in 0..<configuration.cyclicPrefix {
            samples[symbolStart + prefixIndex] =
                samples[
                    symbolStart + configuration.cyclicPrefix + configuration.fftSize
                        - configuration.cyclicPrefix + prefixIndex]
        }
    }
}

/// Scales each pilot coefficient in every data symbol by `1 + additionalScale`.
/// Direct DFTs recover the transmitted pilot coefficients without duplicating
/// the modem's pilot RNG in the test.
private func scaleEveryDataPilot(
    to samples: inout [Float],
    configuration: BulkPHY.Configuration,
    additionalScale: Double
) {
    let symbolSamples = configuration.fftSize + configuration.cyclicPrefix
    let dataStart = 4096 + 2048 + 2 * symbolSamples
    let binLow = Int(
        ceil(
            configuration.lowFrequencyHz * Double(configuration.fftSize)
                / Double(configuration.sampleRate)))
    let binHigh = Int(
        floor(
            configuration.highFrequencyHz * Double(configuration.fftSize)
                / Double(configuration.sampleRate)))
    let usedBins = binHigh - binLow + 1
    for symbolIndex in 0..<configuration.symbolCount {
        let symbolStart = dataStart + symbolIndex * symbolSamples
        let bodyStart = symbolStart + configuration.cyclicPrefix
        var additions: [(bin: Int, real: Double, imaginary: Double)] = []
        for usedPosition in stride(from: 0, to: usedBins, by: configuration.pilotEvery) {
            let bin = binLow + usedPosition
            var real = 0.0
            var imaginary = 0.0
            for fftIndex in 0..<configuration.fftSize {
                let angle =
                    2 * Double.pi * Double(bin) * Double(fftIndex)
                    / Double(configuration.fftSize)
                let sample = Double(samples[bodyStart + fftIndex])
                real += sample * cos(angle)
                imaginary -= sample * sin(angle)
            }
            additions.append(
                (bin, additionalScale * real, additionalScale * imaginary))
        }
        var delta = [Double](repeating: 0, count: configuration.fftSize)
        for addition in additions {
            for fftIndex in 0..<configuration.fftSize {
                let angle =
                    2 * Double.pi * Double(addition.bin) * Double(fftIndex)
                    / Double(configuration.fftSize)
                delta[fftIndex] +=
                    2 * (addition.real * cos(angle) - addition.imaginary * sin(angle))
                    / Double(configuration.fftSize)
            }
        }
        for fftIndex in 0..<configuration.fftSize {
            samples[bodyStart + fftIndex] += Float(delta[fftIndex])
        }
        for prefixIndex in 0..<configuration.cyclicPrefix {
            samples[symbolStart + prefixIndex] =
                samples[
                    bodyStart + configuration.fftSize - configuration.cyclicPrefix
                        + prefixIndex]
        }
    }
}
