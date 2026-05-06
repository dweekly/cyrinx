import CCyrinx
import XCTest

@testable import Cyrinx

final class CyrinxTests: XCTestCase {
    func testVersionIsNonEmpty() {
        XCTAssertFalse(Cyrinx.version.isEmpty)
    }

    func testStatusHelpersExposeNamesAndDescriptions() {
        XCTAssertEqual(Cyrinx.statusName(for: -4), "CYRINX_ERR_TIMEOUT")
        XCTAssertTrue(Cyrinx.statusDescription(for: -4).localizedCaseInsensitiveContains("timed out"))
        XCTAssertEqual(Cyrinx.statusName(for: -1234), "CYRINX_ERR_UNKNOWN")
    }

    func testCyrinxErrorDescriptionIncludesStatusMetadata() {
        let error = CyrinxError.status(-4)
        XCTAssertEqual(error.statusCode, -4)
        XCTAssertEqual(error.statusName, "CYRINX_ERR_TIMEOUT")
        XCTAssertTrue(error.description.contains("CYRINX_ERR_TIMEOUT"))
        XCTAssertTrue(error.description.localizedCaseInsensitiveContains("timed out"))
        XCTAssertNotNil(error.recoverySuggestion)
    }

    func testLoopbackBidirectionalReliableTransfer() throws {
        let a = try CyrinxSession(config: Config(role: .master))
        let b = try CyrinxSession(config: Config(role: .slave))

        try CyrinxSession.linkInMemory(a, b)
        try a.start()
        try b.start()

        let first = Data("mac-to-phone".utf8)
        try a.send(first, streamID: 1, qos: .reliable, priority: .high, flags: [.fin])
        let receivedAtB = try b.receive(timeoutMS: 100)
        XCTAssertEqual(receivedAtB?.data, first)
        XCTAssertEqual(receivedAtB?.streamID, 1)
        XCTAssertEqual(receivedAtB?.priority, .high)
        XCTAssertEqual(receivedAtB?.flags, [.fin])

        let second = Data("phone-to-mac".utf8)
        try b.send(second, streamID: 3, qos: .reliable, priority: .normal, flags: [.fin])
        let receivedAtA = try a.receive(timeoutMS: 100)
        XCTAssertEqual(receivedAtA?.data, second)
        XCTAssertEqual(receivedAtA?.streamID, 3)
        XCTAssertEqual(receivedAtA?.flags, [.fin])
    }

    func testFirstValidInboundFramePromotesDiscoveryToRobust() throws {
        let a = try CyrinxSession(config: Config(role: .master))
        let b = try CyrinxSession(config: Config(role: .slave))

        try CyrinxSession.linkInMemory(a, b)
        try a.start()
        try b.start()

        XCTAssertEqual(b.metrics.gear, .discovery)

        try a.send(Data("hello".utf8), streamID: 1, qos: .bestEffort, priority: .normal, flags: [.fin])
        let received = try b.receive(timeoutMS: 100)
        XCTAssertEqual(received?.data, Data("hello".utf8))

        XCTAssertNotEqual(b.metrics.gear, .discovery)
    }

    func testFragmentationAndReassemblyAt4KB() throws {
        let a = try CyrinxSession(config: Config(role: .master))
        let b = try CyrinxSession(config: Config(role: .slave))

        try CyrinxSession.linkInMemory(a, b)
        try a.start()
        try b.start()

        let payload = Data((0..<4096).map { UInt8($0 % 251) })
        try a.send(payload, streamID: 5, qos: .reliable, priority: .critical, flags: [.fin])

        let received = try b.receive(timeoutMS: 200)
        XCTAssertEqual(received?.data, payload)
        XCTAssertEqual(received?.streamID, 5)
        XCTAssertEqual(received?.priority, .critical)
    }

    func testMultiplexedStreamsInterleaveWithoutLosingMetadata() throws {
        let a = try CyrinxSession(config: Config(role: .master))
        let b = try CyrinxSession(config: Config(role: .slave))

        try CyrinxSession.linkInMemory(a, b)
        try a.start()
        try b.start()

        let alpha = Data("alpha".utf8)
        let beta = Data("beta".utf8)
        let gamma = Data("gamma".utf8)

        try a.send(alpha, streamID: 7, qos: .reliable, priority: .low)
        try a.send(beta, streamID: 9, qos: .reliable, priority: .critical, flags: [.fin])
        try a.send(gamma, streamID: 7, qos: .reliable, priority: .high, flags: [.fin])

        let r1 = try b.receive(timeoutMS: 100)
        let r2 = try b.receive(timeoutMS: 100)
        let r3 = try b.receive(timeoutMS: 100)

        XCTAssertEqual(r1?.data, alpha)
        XCTAssertEqual(r1?.streamID, 7)
        XCTAssertEqual(r1?.priority, .low)

        XCTAssertEqual(r2?.data, beta)
        XCTAssertEqual(r2?.streamID, 9)
        XCTAssertEqual(r2?.priority, .critical)
        XCTAssertEqual(r2?.flags, [.fin])

        XCTAssertEqual(r3?.data, gamma)
        XCTAssertEqual(r3?.streamID, 7)
        XCTAssertEqual(r3?.priority, .high)
        XCTAssertEqual(r3?.flags, [.fin])
    }

    func testControlStreamIsRejectedForApplicationSend() throws {
        let a = try CyrinxSession(config: Config(role: .master))
        try a.start()

        XCTAssertThrowsError(try a.send(Data("x".utf8), streamID: 0, qos: .reliable)) { error in
            guard case CyrinxError.status(let code) = error else {
                return XCTFail("Expected cyrinx status error")
            }
            XCTAssertEqual(code, CYRINX_ERR_INVALID_ARGUMENT.rawValue)
        }
    }

    func testAudibleBeaconRequiresAudioBackend() throws {
        let session = try CyrinxSession(config: Config(role: .master, transportBackend: .inMemory))
        XCTAssertThrowsError(try session.playLocalAudibleBeacon()) { error in
            guard case CyrinxError.status(let code) = error else {
                return XCTFail("Expected cyrinx status error")
            }
            XCTAssertEqual(code, CYRINX_ERR_UNSUPPORTED.rawValue)
        }
    }

    func testRawNibbleConfiguredAudibleBandRoundTrip() {
        let config = Config(
            role: .master,
            transportBackend: .inMemory,
            sampleRateHz: 44_100,
            bandStartHz: 1_200,
            bandEndHz: 2_200,
            txGainCap: 0.80
        )
        let payload = Data("hi".utf8)
        var txCodec = NibbleToneCodec(config: config, symbolSamples: 1_920)
        let waveform = txCodec.encode(payload: payload)
        var rxCodec = NibbleToneCodec(config: config, symbolSamples: 1_920)
        let burst = [Float](repeating: 0, count: 4_096) + waveform + [Float](repeating: 0, count: 4_096)
        let decoded = rxCodec.decodeWaveform(burst)

        XCTAssertEqual(decoded, [payload])
    }

    func testARCSelectGearTransitions() {
        var metrics = cyrinx_metrics_t()
        var policy = cyrinx_arc_policy_t()
        cyrinx_default_arc_policy(&policy)

        metrics.snr_db = 26.0
        metrics.evm_pct = 4.5
        metrics.per_2s = 0.002

        let up = cyrinx_arc_select_gear(
            CYRINX_GEAR_G3_QPSK,
            &metrics,
            &policy,
            3000,
            0,
            0,
            0
        )
        XCTAssertEqual(up, CYRINX_GEAR_G3_16QAM)

        metrics.snr_db = 12.0
        metrics.evm_pct = 18.0
        metrics.per_2s = 0.2

        let down = cyrinx_arc_select_gear(
            CYRINX_GEAR_G3_QPSK,
            &metrics,
            &policy,
            3000,
            0,
            2,
            0
        )
        XCTAssertEqual(down, CYRINX_GEAR_G2_ROBUST)

        let lost = cyrinx_arc_select_gear(
            CYRINX_GEAR_G3_16QAM,
            &metrics,
            &policy,
            3000,
            0,
            0,
            1
        )
        XCTAssertEqual(lost, CYRINX_GEAR_G1_DISCOVERY)
    }

    func testPHYUtilitiesZCAndCFOAndDynamicCP() {
        var zc = [cyrinx_complex_f32_t](repeating: cyrinx_complex_f32_t(re: 0, im: 0), count: 127)
        let zcRC = cyrinx_zc_generate(25, 127, &zc, zc.count)
        XCTAssertEqual(zcRC, CYRINX_OK.rawValue)

        let mag0 = sqrt((zc[0].re * zc[0].re) + (zc[0].im * zc[0].im))
        let mag10 = sqrt((zc[10].re * zc[10].re) + (zc[10].im * zc[10].im))
        XCTAssertEqual(mag0, 1.0, accuracy: 1e-5)
        XCTAssertEqual(mag10, 1.0, accuracy: 1e-5)

        let expectedCFO: Float = 58.0
        let deltaT = Float(127) / 48_000.0
        let phase = 2.0 * Float.pi * expectedCFO * deltaT
        var p1 = cyrinx_complex_f32_t(re: 1.0, im: 0.0)
        var p2 = cyrinx_complex_f32_t(re: cos(phase), im: sin(phase))

        let estimated = cyrinx_estimate_cfo_hz(&p1, &p2, 127, 48_000)
        XCTAssertEqual(estimated, expectedCFO, accuracy: 0.5)

        var config = cyrinx_config_t()
        cyrinx_default_config(&config)
        config.enable_dynamic_cp = 1
        config.enable_sfbc_static_mode = 1
        config.ofdm_cp_samples_default = 96
        config.ofdm_cp_samples_min = 10

        let cpQuietStatic = cyrinx_select_cp_samples(&config, 0.2, 1)
        let cpNoisy = cyrinx_select_cp_samples(&config, 2.5, 0)

        XCTAssertLessThan(cpQuietStatic, 96)
        XCTAssertGreaterThanOrEqual(cpQuietStatic, 10)
        XCTAssertEqual(cpNoisy, 96)
    }

    func testPHYStubOFDMQPSKGoldenVectorCAPI() {
        var config = cyrinx_phy_stub_config_t(
            mode: CYRINX_PHY_STUB_OFDM_QPSK, sample_rate_hz: 0, fft_size: 0, cp_samples: 0)
        cyrinx_phy_stub_default_config(CYRINX_PHY_STUB_OFDM_QPSK, &config)

        let symbols: [UInt8] = [0, 1, 2, 3]
        var samples = [cyrinx_complex_f32_t](
            repeating: cyrinx_complex_f32_t(re: 0, im: 0), count: symbols.count)
        var sampleCount = samples.count

        let rcMod = symbols.withUnsafeBufferPointer { ptr in
            cyrinx_phy_modulate_stub(&config, ptr.baseAddress, symbols.count, &samples, &sampleCount)
        }
        XCTAssertEqual(rcMod, CYRINX_OK.rawValue)
        XCTAssertEqual(sampleCount, symbols.count)

        let k: Float = 0.70710677
        XCTAssertEqual(samples[0].re, k, accuracy: 1e-5)
        XCTAssertEqual(samples[0].im, k, accuracy: 1e-5)
        XCTAssertEqual(samples[1].re, -k, accuracy: 1e-5)
        XCTAssertEqual(samples[1].im, k, accuracy: 1e-5)
        XCTAssertEqual(samples[2].re, k, accuracy: 1e-5)
        XCTAssertEqual(samples[2].im, -k, accuracy: 1e-5)
        XCTAssertEqual(samples[3].re, -k, accuracy: 1e-5)
        XCTAssertEqual(samples[3].im, -k, accuracy: 1e-5)

        var demod = [UInt8](repeating: 0, count: symbols.count)
        var demodCount = demod.count
        let rcDemod = cyrinx_phy_demodulate_stub(&config, &samples, sampleCount, &demod, &demodCount)
        XCTAssertEqual(rcDemod, CYRINX_OK.rawValue)
        XCTAssertEqual(demodCount, symbols.count)
        XCTAssertEqual(demod, symbols)
    }

    func testPHYStubSwiftDCSSRoundTrip() throws {
        let config = PHYStub.defaultConfig(for: .dcss)
        let symbols: [UInt8] = [0, 64, 128, 192]
        let samples = try PHYStub.modulate(symbols: symbols, config: config)
        XCTAssertEqual(samples.count, symbols.count)

        XCTAssertEqual(samples[0].re, 1.0, accuracy: 1e-5)
        XCTAssertEqual(samples[0].im, 0.0, accuracy: 1e-5)
        XCTAssertEqual(samples[1].re, 0.0, accuracy: 1e-4)
        XCTAssertEqual(samples[1].im, 1.0, accuracy: 1e-4)
        XCTAssertEqual(samples[2].re, -1.0, accuracy: 1e-4)
        XCTAssertEqual(samples[2].im, 0.0, accuracy: 1e-4)

        let decoded = try PHYStub.demodulate(samples: samples, config: config)
        XCTAssertEqual(decoded, symbols)
    }

    func testSimulationRunnerProducesDeterministicCoreMetrics() throws {
        let options = SimulationOptions(
            packetCount: 24,
            payloadBytes: 64,
            streamID: 33,
            priority: .normal,
            interPacketIntervalMS: 0,
            seed: 12345
        )

        let resultA = try SimulationRunner.run(profile: .quietDesktop, options: options)
        let resultB = try SimulationRunner.run(profile: .quietDesktop, options: options)

        XCTAssertEqual(resultA.packetCount, 24)
        XCTAssertEqual(resultA.packetsDelivered, 24)
        XCTAssertEqual(resultA.packetsFailed, 0)
        XCTAssertEqual(resultA.packetsDelivered, resultB.packetsDelivered)
        XCTAssertEqual(resultA.packetsFailed, resultB.packetsFailed)
        XCTAssertEqual(resultA.finalGear, resultB.finalGear)
        XCTAssertEqual(resultA.gearHistogram, resultB.gearHistogram)
        XCTAssertGreaterThan(resultA.goodputBps, 0)
    }

    func testSimulationJSONEncodingContainsProfileAndCounts() throws {
        let options = SimulationOptions(
            packetCount: 8, payloadBytes: 48, streamID: 5, interPacketIntervalMS: 0)
        let result = try SimulationRunner.run(profile: .officeBurst, options: options)
        let json = try SimulationRunner.makeJSON(result)

        XCTAssertTrue(json.contains("\"profile\""))
        XCTAssertTrue(json.contains("office-burst"))
        XCTAssertTrue(json.contains("\"packetsDelivered\""))
        XCTAssertTrue(json.contains("\"packetsFailed\""))
    }

}
