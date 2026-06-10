import CryptoKit
import Foundation

/// Loader for the bulk-PHY golden vectors (the cross-implementation contract;
/// see scratch/hw20k/golden_vectors.py and docs/PUBLICATION.md PR 1.1).
///
/// The Python modem is the semantic oracle; these fixtures pin every TX pipeline
/// stage so the portable-C core (PRs 1.2/1.3) and this Swift binding can be
/// validated at the manifest's tiered tolerance:
///   - "exact": byte-for-byte (the deterministic integer pipeline + decoded payload)
///   - "float": within `floatAbsTol` (FFT/IFFT-derived values; backend-dependent)
///
/// Fixtures are located on the filesystem relative to this source file rather
/// than bundled as SwiftPM resources, so the very same paths are reachable from
/// the C test rig (which cannot read a SwiftPM bundle).
enum GoldenVectors {
    struct Manifest: Decodable {
        let format: Int
        let floatAbsTol: Double
        let cases: [Case]
        enum CodingKeys: String, CodingKey {
            case format
            case floatAbsTol = "float_abs_tol"
            case cases
        }
    }

    struct Case: Decodable {
        let name: String
        let config: Config
        let artifacts: [Artifact]
    }

    /// Only the config fields the Swift/C ports actually consume are decoded;
    /// the rest of the Python config dict is ignored.
    struct Config: Decodable {
        let nfft: Int
        let cp: Int
        let sr: Int
        let rate: String
        let nSym: Int
        let payloadBytes: Int
        let infoBits: Int
        let bitsPerSym: Int
        let nBlocks: Int
        let binLo: Int
        let binHi: Int
        let rxOffset: Int
        let seed: Int
        enum CodingKeys: String, CodingKey {
            case nfft, cp, sr, rate, seed
            case nSym = "n_sym"
            case payloadBytes = "payload_bytes"
            case infoBits = "info_bits"
            case bitsPerSym = "bits_per_sym"
            case nBlocks = "n_blocks"
            case binLo = "bin_lo"
            case binHi = "bin_hi"
            case rxOffset = "rx_offset"
        }
    }

    struct Artifact: Decodable {
        let stage: String
        let file: String
        let tolerance: String   // "exact" | "float"
        let dtype: String       // "uint8" | "int64" | "float32" | "complex64"
        let shape: [Int]
        let bytes: Int
        let sha256: String
    }

    /// Repo-relative fixtures directory: <repo>/Tests/Fixtures/golden.
    static var directory: URL {
        // this file is <repo>/Tests/CyrinxTests/GoldenVectors.swift
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()      // Tests/CyrinxTests
            .deletingLastPathComponent()      // Tests
            .appendingPathComponent("Fixtures/golden", isDirectory: true)
    }

    static func loadManifest() throws -> Manifest {
        let url = directory.appendingPathComponent("manifest.json")
        let data = try Data(contentsOf: url)
        return try JSONDecoder().decode(Manifest.self, from: data)
    }

    static func rawData(_ caseName: String, _ artifact: Artifact) throws -> Data {
        try Data(contentsOf: directory.appendingPathComponent(caseName)
            .appendingPathComponent(artifact.file))
    }

    static func sha256Hex(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }

    // MARK: typed accessors (for the DSP-port tests in 1.2/1.3)

    static func bytes(_ data: Data) -> [UInt8] { [UInt8](data) }

    static func int64s(_ data: Data) -> [Int64] {
        data.withUnsafeBytes { Array($0.bindMemory(to: Int64.self)) }
    }

    static func float32s(_ data: Data) -> [Float] {
        data.withUnsafeBytes { Array($0.bindMemory(to: Float.self)) }
    }

    /// complex64 = interleaved float32 (re, im); returned as (re, im) pairs.
    static func complex64(_ data: Data) -> [(re: Float, im: Float)] {
        let f = float32s(data)
        return stride(from: 0, to: f.count, by: 2).map { (f[$0], f[$0 + 1]) }
    }
}
