import XCTest

@testable import Cyrinx

/// Exercises the Swift `BulkPHY` binding over the portable-C codec: a pure-Swift
/// encode → digital-loopback → decode round trip recovers the payload with every
/// CRC block valid. The DSP correctness is pinned by the golden vectors
/// (CyrinxBulkTXTests); this proves the ergonomic Swift surface is wired up.
final class CyrinxBulkPHYTests: XCTestCase {
    private func roundTrip(_ config: BulkPHY.Configuration, line: UInt = #line) {
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
        guard let decoded = phy.decode(rx, combining: rx) else { return XCTFail("decode nil") }
        XCTAssertTrue(decoded.isComplete, "blocks \(decoded.blocksOK)/\(decoded.blockCount)")
        XCTAssertEqual(decoded.payload, payload)
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
    }
}
