import CGoldenVectors
import XCTest

@testable import Cyrinx

/// Locks the bulk-PHY golden-vector contract from the library side (PR 1.1).
/// These tests do not yet exercise a Swift/C DSP *modem* port — they prove the
/// fixtures load and are intact (Swift loader) and that the portable-C side can
/// read/size them (the CGoldenVectors loader), so PRs 1.2/1.3 can assert the DSP
/// ports against a trusted reference. See docs/PUBLICATION.md and
/// scratch/hw20k/golden_vectors.py.
final class CyrinxGoldenVectorTests: XCTestCase {
    func testManifestLoads() throws {
        let m = try GoldenVectors.loadManifest()
        XCTAssertEqual(m.format, 1)
        XCTAssertGreaterThan(m.floatAbsTol, 0)
        XCTAssertFalse(m.cases.isEmpty, "expected at least one golden case")
        // The canonical QPSK and 16-QAM cases must be present.
        let names = Set(m.cases.map(\.name))
        XCTAssertTrue(names.contains("qpsk_r12"))
        XCTAssertTrue(names.contains("qam16_r34"))
    }

    /// Every artifact file exists, is the recorded length, and hashes to the
    /// recorded SHA-256 — the fixtures have not drifted from the manifest.
    func testArtifactIntegrity() throws {
        let m = try GoldenVectors.loadManifest()
        for c in m.cases {
            for a in c.artifacts {
                let data = try GoldenVectors.rawData(c.name, a)
                XCTAssertEqual(
                    data.count, a.bytes,
                    "\(c.name)/\(a.stage): byte length")
                XCTAssertEqual(
                    GoldenVectors.sha256Hex(data), a.sha256,
                    "\(c.name)/\(a.stage): sha256")
            }
        }
    }

    /// The byte-exact RX target (decoded_payload) equals the TX input (payload):
    /// the round-trip fixture the receiver port (1.3) must reproduce is self-
    /// consistent, and matches the config's declared payload size.
    func testRoundTripTargetSelfConsistent() throws {
        let m = try GoldenVectors.loadManifest()
        for c in m.cases {
            let payload = try artifact(c, "payload")
            let decoded = try artifact(c, "decoded_payload")
            XCTAssertEqual(payload, decoded, "\(c.name): decoded != input payload")
            XCTAssertEqual(
                payload.count, c.config.payloadBytes,
                "\(c.name): payload size vs config")
        }
    }

    /// Sanity on the deterministic-stage shapes/dtypes the C port must match
    /// bit-exactly (catches an accidental fixture-format change early).
    func testExactStageShapes() throws {
        let m = try GoldenVectors.loadManifest()
        for c in m.cases {
            let interleaved = try artifactObj(c, "interleaved_bits")
            XCTAssertEqual(interleaved.dtype, "uint8")
            XCTAssertEqual(interleaved.tolerance, "exact")
            // interleaved bits == bits_per_sym * n_sym (the frame symbol capacity)
            let cap = c.config.bitsPerSym * c.config.nSym
            XCTAssertEqual(interleaved.shape, [cap], "\(c.name): interleave length")

            let perm = try artifactObj(c, "interleave_perm")
            XCTAssertEqual(perm.dtype, "int64")
            XCTAssertEqual(perm.shape, [cap], "\(c.name): perm length")
        }
    }

    /// At least one case ships a non-empty `rx_wave` (the receive fixture for
    /// the RX port, 1.3), with the expected dtype/tolerance, and every case's
    /// rx_wave parses as float32 of the recorded length.
    func testRxWaveFixturePresent() throws {
        let m = try GoldenVectors.loadManifest()
        for c in m.cases {
            let rx = try artifactObj(c, "rx_wave")
            XCTAssertEqual(rx.dtype, "float32")
            XCTAssertEqual(rx.tolerance, "input")
            let samples = GoldenVectors.float32s(try GoldenVectors.rawData(c.name, rx))
            XCTAssertEqual(samples.count, rx.bytes / 4, "\(c.name): rx_wave sample count")
            XCTAssertGreaterThan(samples.count, c.config.nfft, "\(c.name): rx_wave too short")
        }
    }

    /// At least one case exercises the PRBS capacity-fill branch (non-empty
    /// pad_fill_bits) — so a C port that omits seed-8 fill changes the
    /// interleaved bits and fails. (pad_fill is structurally <=1 bit by design.)
    func testFillBranchCovered() throws {
        let m = try GoldenVectors.loadManifest()
        let fills = try m.cases.map { try artifactObj($0, "pad_fill_bits").shape.first ?? 0 }
        XCTAssertTrue(
            fills.contains { $0 > 0 },
            "no case exercises the non-empty PRBS capacity-fill branch")
    }

    /// Exercises the unaligned little-endian typed loaders (#4): the interleave
    /// permutation must be a genuine permutation of 0..<cap.
    func testInterleavePermIsValid() throws {
        let m = try GoldenVectors.loadManifest()
        for c in m.cases {
            let perm = GoldenVectors.int64s(
                try GoldenVectors.rawData(
                    c.name,
                    try artifactObj(c, "interleave_perm")))
            let cap = c.config.bitsPerSym * c.config.nSym
            XCTAssertEqual(perm.count, cap, "\(c.name): perm length")
            XCTAssertEqual(
                Set(perm), Set((0..<Int64(cap))),
                "\(c.name): perm is not a bijection of 0..<cap")
        }
    }

    /// The portable-C loader (CGoldenVectors) reads and sizes every artifact
    /// consistently with the manifest, and reads the same bytes Swift does.
    func testCGoldenLoaderMatchesManifest() throws {
        let dir = GoldenVectors.directory.path
        let m = try GoldenVectors.loadManifest()

        var checked = 0
        let mismatches = dir.withCString { cyrinx_golden_verify_sizes($0, &checked) }
        XCTAssertEqual(mismatches, 0, "C loader found size mismatches")
        XCTAssertEqual(checked, m.cases.reduce(0) { $0 + $1.artifacts.count })
        XCTAssertEqual(Int(cyrinx_golden_case_count()), m.cases.count)
        XCTAssertEqual(cyrinx_golden_float_abs_tol(), m.floatAbsTol, accuracy: 1e-12)

        // C-read a blob and compare to the Swift-read bytes.
        var buf: UnsafeMutablePointer<UInt8>?
        var len = 0
        let rc = dir.withCString { d in
            "qpsk_r12/payload.bin".withCString { rel in
                cyrinx_golden_read(d, rel, &buf, &len)
            }
        }
        XCTAssertEqual(rc, 0)
        let cData = Data(bytes: try XCTUnwrap(buf), count: len)
        cyrinx_golden_free(buf)
        let sData = try GoldenVectors.rawData(
            "qpsk_r12",
            artifactObj(try loadCase("qpsk_r12"), "payload"))
        XCTAssertEqual(cData, sData)
    }

    // MARK: helpers

    private func loadCase(_ name: String) throws -> GoldenVectors.Case {
        let m = try GoldenVectors.loadManifest()
        return try XCTUnwrap(m.cases.first { $0.name == name }, "missing case \(name)")
    }

    private func artifactObj(_ c: GoldenVectors.Case, _ stage: String) throws
        -> GoldenVectors.Artifact
    {
        let a = c.artifacts.first { $0.stage == stage }
        return try XCTUnwrap(a, "\(c.name): missing artifact \(stage)")
    }

    private func artifact(_ c: GoldenVectors.Case, _ stage: String) throws -> [UInt8] {
        GoldenVectors.bytes(try GoldenVectors.rawData(c.name, try artifactObj(c, stage)))
    }
}
