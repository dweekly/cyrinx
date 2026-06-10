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

    /// Full TX orchestration: geometry, the per-symbol freq-domain vectors
    /// (`data_freq`, exercises QAM map + pilot/interleave placement), and the
    /// final `wave` (exercises the OFDM IFFT + CP + normalization). data_freq is
    /// float-tol; wave is float-tol vs the float32 reference.
    func testModulateDataFreqAndWave() throws {
        let tol = Float(try GoldenVectors.loadManifest().floatAbsTol)
        for c in try cases() {
            let cfg = c.config
            let expDataFreq = GoldenVectors.complex64(
                try GoldenVectors.rawData(c.name, try artifactObj(c, "data_freq")))
            let expWave = GoldenVectors.float32s(
                try GoldenVectors.rawData(c.name, try artifactObj(c, "wave")))
            let payload = try bytes(c, "payload")

            var bc = cyrinx_bulk_config()
            bc.f_lo = cfg.fLo
            bc.f_hi = cfg.fHi
            bc.pilot_every = Int32(cfg.pilotEvery)
            bc.bits_per_bin = Int32(cfg.bitsPerBin)
            bc.n_sym = Int32(cfg.nSym)
            bc.nfft = Int32(cfg.nfft)
            bc.cp = Int32(cfg.cp)
            bc.sr = Int32(cfg.sr)
            bc.amp = cfg.amp
            bc.clip_sigma = cfg.clipSigma
            bc.chirp_f0 = cfg.chirpF0
            bc.chirp_f1 = cfg.chirpF1

            try cfg.rate.withCString { rptr in
                bc.rate = rptr
                var geo = cyrinx_bulk_geometry()
                XCTAssertEqual(cyrinx_bulk_compute_geometry(&bc, &geo), 0, "\(c.name): geometry")
                XCTAssertEqual(Int(geo.payload_bytes), cfg.payloadBytes, "\(c.name): payload_bytes")
                XCTAssertEqual(Int(geo.n_blocks), cfg.nBlocks, "\(c.name): n_blocks")
                XCTAssertEqual(Int(geo.bits_per_sym), cfg.bitsPerSym, "\(c.name): bits_per_sym")

                let nUsed = Int(geo.n_used)
                var dataFreq = [Double](repeating: 0, count: cfg.nSym * nUsed * 2)
                var wave = [Float](repeating: 0, count: Int(geo.frame_samples))
                let written = payload.withUnsafeBufferPointer { p in
                    cyrinx_bulk_modulate(&bc, p.baseAddress, p.count, &wave, wave.count, &dataFreq)
                }
                XCTAssertEqual(written, Int(geo.frame_samples), "\(c.name): samples written")

                // data_freq (QAM symbols + pilots, pre-IFFT)
                XCTAssertEqual(dataFreq.count, expDataFreq.count * 2, "\(c.name): data_freq count")
                var dfErr: Float = 0
                for k in 0..<expDataFreq.count {
                    dfErr = max(dfErr, abs(Float(dataFreq[k * 2]) - expDataFreq[k].re))
                    dfErr = max(dfErr, abs(Float(dataFreq[k * 2 + 1]) - expDataFreq[k].im))
                }
                XCTAssertLessThan(dfErr, tol, "\(c.name): data_freq max abs err \(dfErr)")

                // wave (post-IFFT, CP, normalization)
                XCTAssertEqual(wave.count, expWave.count, "\(c.name): wave length")
                var wErr: Float = 0
                for i in 0..<min(wave.count, expWave.count) {
                    wErr = max(wErr, abs(wave[i] - expWave[i]))
                }
                XCTAssertLessThan(wErr, tol, "\(c.name): wave max abs err \(wErr)")
            }
        }
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
