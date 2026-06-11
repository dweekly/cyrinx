import XCTest

@testable import Cyrinx

/// Unit tests for the adaptive-sounder decision engine (PR 1.4): tier selection
/// across the SNR/delay-spread space, CP sizing, the non-coherent fallback, and
/// per-bin bit loading. Pure logic — no audio.
final class CyrinxSounderTests: XCTestCase {
    func testCleanNearFieldPicksFast() {
        let r = recommendMCS(medianSNRdB: 24, delaySpreadMs15: 8)
        XCTAssertEqual(r.tier, .fast)
        XCTAssertEqual(r.modulation, "16-QAM")
        XCTAssertEqual(r.rate, "3/4")
        XCTAssertFalse(r.isNonCoherent)
    }

    func testModerateSNRDropsToMedium() {
        let r = recommendMCS(medianSNRdB: 18, delaySpreadMs15: 10)
        XCTAssertEqual(r.tier, .medium)
        XCTAssertEqual(r.rate, "1/2")
    }

    func testContactChannelPicksQPSK() {
        // ~10.5 dB direct-contact channel from the measured findings.
        let r = recommendMCS(medianSNRdB: 10.5, delaySpreadMs15: 12)
        XCTAssertEqual(r.tier, .qpsk)
    }

    func testLowSNRPicksBPSK() {
        let r = recommendMCS(medianSNRdB: 5, delaySpreadMs15: 20)
        XCTAssertEqual(r.tier, .bpsk)
        XCTAssertEqual(r.bitsPerBin, 1)
    }

    /// Excess delay spread (reverberant desk) forces the non-coherent floor at
    /// ANY SNR, and flags repositioning.
    func testExcessDelaySpreadForcesNonCoherent() {
        let r = recommendMCS(medianSNRdB: 25, delaySpreadMs15: 36)
        XCTAssertEqual(r.tier, .nonCoherent)
        XCTAssertTrue(r.isNonCoherent)
        XCTAssertEqual(r.modulation, "MT-FSK")
        XCTAssertTrue(r.adviseReposition)
    }

    func testCPGrowsWithDelaySpreadButClampsToCap() {
        let small = recommendMCS(medianSNRdB: 22, delaySpreadMs15: 8)
        let large = recommendMCS(medianSNRdB: 10, delaySpreadMs15: 24)
        XCTAssertGreaterThan(large.cyclicPrefixMs, small.cyclicPrefixMs)
        XCTAssertLessThanOrEqual(large.cyclicPrefixMs, 32.0)  // CP cap
        // 8 ms * 1.25 = 10 ms CP -> within the 2048 FFT (<= 1024 samples @48k)
        XCTAssertEqual(small.fftSize, 2048)
    }

    func testBitLoadingByBinSNR() {
        let loads = bitLoading(perBinSNRdB: [-3, 4.9, 5.0, 14.9, 15.0, 23.0, 30.0])
        XCTAssertEqual(loads, [0, 0, 2, 2, 4, 6, 6])
    }
}
