import CCyrinx
import XCTest

@testable import Cyrinx

/// Validates the portable-C bulk-PHY TX *primitives* (PR 1.2) bit-exact against
/// the golden vectors. This is the FFT-independent deterministic core: DetRng,
/// IEEE CRC-32, the convolutional code, puncturing, and PRBS padding. QAM symbol
/// mapping and the OFDM/FFT wave are validated in follow-on commits. Each test
/// uses a committed golden intermediate as the input to the next stage, so a
/// failure pinpoints exactly which stage diverged. See cyrinx/cyrinx_bulk.h.
final class CyrinxBulkTXTests: XCTestCase {
    // Seeds are from modem.py:modulate_frame.
    private let interleaveSeed: UInt64 = 0x1EAF
    private let padInfoSeed: UInt64 = 7
    private let padFillSeed: UInt64 = 8
    private let crcBlock = 256

    /// IEEE CRC-32 + per-block framing rebuilds `stream_with_crc` exactly.
    func testCrc32AndBlockFraming() throws {
        for c in try cases() {
            let payload = try bytes(c, "payload")
            let expected = try bytes(c, "stream_with_crc")
            var rebuilt = [UInt8]()
            for i in 0..<c.config.nBlocks {
                let blk = Array(payload[(i * crcBlock)..<((i + 1) * crcBlock)])
                let crc = blk.withUnsafeBufferPointer {
                    cyrinx_bulk_crc32($0.baseAddress, $0.count)
                }
                rebuilt += blk
                rebuilt += [
                    UInt8((crc >> 24) & 0xFF), UInt8((crc >> 16) & 0xFF),
                    UInt8((crc >> 8) & 0xFF), UInt8(crc & 0xFF),
                ]
            }
            XCTAssertEqual(rebuilt, expected, "\(c.name): CRC-32 / framing")
        }
    }

    /// conv_encode(info_bits) == coded_bits.
    func testConvolutionalEncode() throws {
        for c in try cases() {
            let info = try bytes(c, "info_bits")
            let expected = try bytes(c, "coded_bits")
            var out = [UInt8](repeating: 0, count: 2 * (info.count + 6))
            let written = info.withUnsafeBufferPointer {
                cyrinx_conv_encode($0.baseAddress, $0.count, &out)
            }
            XCTAssertEqual(written, out.count, "\(c.name): coded length")
            XCTAssertEqual(out, expected, "\(c.name): conv_encode")
        }
    }

    /// puncture(coded_bits, pattern[rate]) == punctured_bits.
    func testPuncture() throws {
        for c in try cases() {
            let coded = try bytes(c, "coded_bits")
            let expected = try bytes(c, "punctured_bits")
            var patPtr: UnsafePointer<UInt8>?
            let p = c.config.rate.withCString { cyrinx_puncture_pattern($0, &patPtr) }
            XCTAssertGreaterThan(p, 0, "\(c.name): unknown rate \(c.config.rate)")
            var out = [UInt8](repeating: 0, count: coded.count)
            let kept = coded.withUnsafeBufferPointer {
                cyrinx_puncture($0.baseAddress, $0.count, patPtr, p, &out)
            }
            XCTAssertEqual(Array(out[0..<kept]), expected, "\(c.name): puncture")
        }
    }

    /// PRBS padding (seed 7 = info pad, seed 8 = capacity fill) matches.
    func testPRBSPads() throws {
        for c in try cases() {
            for (stage, seed) in [("pad_info_bits", padInfoSeed), ("pad_fill_bits", padFillSeed)] {
                let expected = try bytes(c, stage)
                var out = [UInt8](repeating: 0, count: expected.count)
                if !out.isEmpty {
                    cyrinx_prbs_bits(&out, out.count, seed)
                }
                XCTAssertEqual(out, expected, "\(c.name): \(stage)")
            }
        }
    }

    /// The interleaver permutation matches DetRng(0x1EAF).permutation(cap).
    func testInterleavePermutation() throws {
        for c in try cases() {
            let expected = GoldenVectors.int64s(
                try GoldenVectors.rawData(c.name, try artifactObj(c, "interleave_perm")))
            var out = [Int64](repeating: 0, count: expected.count)
            cyrinx_detrng_permutation(interleaveSeed, &out, out.count)
            XCTAssertEqual(out, expected, "\(c.name): interleave perm")
        }
    }

    /// Spot-check the raw splitmix64 stream against modem.py:DetRng for a known
    /// seed (guards the mixer constants independent of any downstream stage).
    func testDetRngStream() {
        // DetRng(0x1234).u64() first three outputs (from the Python reference).
        var r = cyrinx_detrng()
        cyrinx_detrng_init(&r, 0x1234)
        XCTAssertEqual(cyrinx_detrng_u64(&r), expectedSplitmix(seed: 0x1234, index: 0))
        XCTAssertEqual(cyrinx_detrng_u64(&r), expectedSplitmix(seed: 0x1234, index: 1))
        XCTAssertEqual(cyrinx_detrng_u64(&r), expectedSplitmix(seed: 0x1234, index: 2))
    }

    // MARK: helpers

    /// Reference splitmix64 (independent reimplementation in Swift) so the test
    /// does not merely compare the C against itself.
    private func expectedSplitmix(seed: UInt64, index: Int) -> UInt64 {
        var s = seed
        var z: UInt64 = 0
        for _ in 0...index {
            s = s &+ 0x9E37_79B9_7F4A_7C15
            z = s
            z = (z ^ (z >> 30)) &* 0xBF58_476D_1CE4_E5B9
            z = (z ^ (z >> 27)) &* 0x94D0_49BB_1331_11EB
            z = z ^ (z >> 31)
        }
        return z
    }

    private func cases() throws -> [GoldenVectors.Case] {
        try GoldenVectors.loadManifest().cases
    }

    private func artifactObj(_ c: GoldenVectors.Case, _ stage: String) throws
        -> GoldenVectors.Artifact
    {
        try XCTUnwrap(c.artifacts.first { $0.stage == stage }, "\(c.name): missing \(stage)")
    }

    private func bytes(_ c: GoldenVectors.Case, _ stage: String) throws -> [UInt8] {
        GoldenVectors.bytes(try GoldenVectors.rawData(c.name, try artifactObj(c, stage)))
    }
}
