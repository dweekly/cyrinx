import CCyrinx
import XCTest

@testable import Cyrinx

final class CyrinxTests: XCTestCase {
    func testVersionIsNonEmpty() {
        XCTAssertFalse(Cyrinx.version.isEmpty)
    }

    func testLoopbackBidirectionalReliableTransfer() throws {
        let a = try CyrinxSession(config: Config(role: .master))
        let b = try CyrinxSession(config: Config(role: .slave))

        try CyrinxSession.linkInMemory(a, b)
        try a.start()
        try b.start()

        let first = Data("mac-to-phone".utf8)
        try a.send(first, qos: .reliable)
        let receivedAtB = try b.receive(timeoutMS: 100)
        XCTAssertEqual(receivedAtB, first)

        let second = Data("phone-to-mac".utf8)
        try b.send(second, qos: .reliable)
        let receivedAtA = try a.receive(timeoutMS: 100)
        XCTAssertEqual(receivedAtA, second)
    }

    func testFragmentationAndReassemblyAt4KB() throws {
        let a = try CyrinxSession(config: Config(role: .master))
        let b = try CyrinxSession(config: Config(role: .slave))

        try CyrinxSession.linkInMemory(a, b)
        try a.start()
        try b.start()

        let payload = Data((0..<4096).map { UInt8($0 % 251) })
        try a.send(payload, qos: .reliable)

        let received = try b.receive(timeoutMS: 200)
        XCTAssertEqual(received, payload)
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
}
