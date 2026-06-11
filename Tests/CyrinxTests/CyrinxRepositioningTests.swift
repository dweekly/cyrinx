import XCTest

@testable import Cyrinx

/// Unit tests for the repositioning-guidance pure function (PR 1.4b): each
/// channel pathology maps to the right actionable hint, and the priority order
/// (clipping > incoherence > delay spread > low SNR > HF roll-off) holds.
final class CyrinxRepositioningTests: XCTestCase {
    // A healthy near-field channel as the baseline.
    private let healthy = ChannelMetrics(
        medianSNRdB: 22, rxPeak: 0.4, delaySpreadMs: 8, cyclicPrefixMs: 16,
        highFreqRolloffDB: 0, coherence: 1.0, isUltrasonic: false)

    func testHealthyIsOK() {
        XCTAssertEqual(repositioningAdvice(for: healthy).hint, .ok)
    }

    func testClippingLowersVolume() {
        var m = healthy
        m.rxPeak = 0.99
        XCTAssertEqual(repositioningAdvice(for: m).hint, .lowerVolume)
    }

    func testReverberantSuggestsSoftSurface() {
        var m = healthy
        m.delaySpreadMs = 36  // >> 16 ms CP
        let a = repositioningAdvice(for: m)
        XCTAssertEqual(a.hint, .softSurface)
        XCTAssertEqual(a.evidence, 36)
    }

    func testWeakSignalMovesCloser() {
        var m = healthy
        m.medianSNRdB = 4
        m.rxPeak = 0.02
        let a = repositioningAdvice(for: m)
        XCTAssertEqual(a.hint, .moveCloser)
        XCTAssertEqual(a.severity, 2)
    }

    func testMarginalSNRIsAdvisoryMoveCloser() {
        var m = healthy
        m.medianSNRdB = 8  // marginal, but capture isn't weak
        let a = repositioningAdvice(for: m)
        XCTAssertEqual(a.hint, .moveCloser)
        XCTAssertEqual(a.severity, 1)
    }

    func testHFRolloffAimsBottomEdge() {
        var m = healthy
        m.highFreqRolloffDB = -14
        XCTAssertEqual(repositioningAdvice(for: m).hint, .aimBottomEdge)
    }

    func testUltrasonicIncoherenceUsesAudible() {
        var m = healthy
        m.isUltrasonic = true
        m.coherence = 0.05
        XCTAssertEqual(repositioningAdvice(for: m).hint, .useAudible)
    }

    /// Clipping wins even when other pathologies are also present.
    func testClippingHasHighestPriority() {
        var m = healthy
        m.rxPeak = 0.99
        m.delaySpreadMs = 40
        m.medianSNRdB = 3
        XCTAssertEqual(repositioningAdvice(for: m).hint, .lowerVolume)
    }

    func testHintsHaveText() {
        for hint in [
            RepositioningHint.ok, .moveCloser, .softSurface, .aimBottomEdge, .lowerVolume,
            .useAudible,
        ] {
            XCTAssertFalse(hint.text.isEmpty)
        }
    }
}
