import XCTest

@testable import Cyrinx

/// Locks the bulk-PHY golden-vector contract from the library side (PR 1.1).
/// These tests do not yet exercise a Swift/C DSP port — they prove the fixtures
/// load and are intact, so PRs 1.2/1.3 can assert the port against a trusted
/// reference. See docs/PUBLICATION.md and scratch/hw20k/golden_vectors.py.
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
                XCTAssertEqual(data.count, a.bytes,
                               "\(c.name)/\(a.stage): byte length")
                XCTAssertEqual(GoldenVectors.sha256Hex(data), a.sha256,
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
            XCTAssertEqual(payload.count, c.config.payloadBytes,
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

    // MARK: helpers

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
