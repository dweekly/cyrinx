import Foundation
import Testing

@testable import CyrinxChatKit

/// Test-only lowercase-hex-string decoder (the codec itself only ever
/// needs to produce hex, via `Data.hexString`; tests need to go the other
/// way to turn golden-vector `bytes_hex` fields into `Data`).
extension Data {
    init(hexString: String) {
        precondition(hexString.count % 2 == 0, "hex string must have an even number of digits")
        var data = Data(capacity: hexString.count / 2)
        var index = hexString.startIndex
        while index < hexString.endIndex {
            let next = hexString.index(index, offsetBy: 2)
            guard let byte = UInt8(hexString[index..<next], radix: 16) else {
                preconditionFailure("invalid hex string: \(hexString)")
            }
            data.append(byte)
            index = next
        }
        self = data
    }
}

/// `Apps/Chat/fixtures/chat-envelope-golden.json`'s top-level shape
/// (ENVELOPE.md §8's JSON schema), decoded verbatim for test consumption.
struct GoldenFixtureFile: Codable {
    let schema: String
    let generator: String
    let vectors: [GoldenVectorJSON]
}

struct GoldenVectorJSON: Codable, Sendable, CustomTestStringConvertible {
    let name: String
    let expect: String
    let bytesHex: String
    let decoded: DecodedFieldsJSON?
    let error: String?
    let comment: String

    enum CodingKeys: String, CodingKey {
        case name
        case expect
        case bytesHex = "bytes_hex"
        case decoded
        case error
        case comment
    }

    /// Swift Testing display name for this argument -- shows the golden
    /// vector's own `name` in test output/failures instead of a dump of
    /// every field.
    var testDescription: String { name }
}

struct DecodedFieldsJSON: Codable, Sendable {
    let version: Int
    let kind: String
    let messageIdHex: String
    let replyToIdHex: String?
    let senderIdHex: String
    let body: String
}

enum GoldenFixture {
    /// Locates `Apps/Chat/fixtures/chat-envelope-golden.json` relative to
    /// this source file: `Tests/CyrinxChatKitTests/<this file>` ->
    /// (package root) `CyrinxChatKit` -> `../fixtures/...`, exactly the
    /// relative path the C3-28 task brief specifies.
    static func fixtureURL(sourceFile: String = #filePath) -> URL {
        let packageRoot = URL(fileURLWithPath: sourceFile)
            .deletingLastPathComponent()  // Tests/CyrinxChatKitTests
            .deletingLastPathComponent()  // Tests
            .deletingLastPathComponent()  // CyrinxChatKit (package root)
        return
            packageRoot
            .appendingPathComponent("..")
            .appendingPathComponent("fixtures")
            .appendingPathComponent("chat-envelope-golden.json")
            .standardizedFileURL
    }

    static func loadVectors() -> [GoldenVectorJSON] {
        let url = fixtureURL()
        guard let data = try? Data(contentsOf: url) else {
            fatalError("failed to read golden fixture at \(url.path)")
        }
        guard let file = try? JSONDecoder().decode(GoldenFixtureFile.self, from: data) else {
            fatalError("failed to decode golden fixture at \(url.path)")
        }
        return file.vectors
    }

    /// Maps a golden-vector `error` name (ENVELOPE.md §5's table) to the
    /// matching `ChatEnvelopeError` case.
    static func envelopeError(named name: String) -> ChatEnvelopeError {
        switch name {
        case "unknownVersion": return .unknownVersion
        case "unknownKind": return .unknownKind
        case "malformed": return .malformed
        case "truncated": return .truncated
        case "oversizeBody": return .oversizeBody
        case "invalidUtf8": return .invalidUtf8
        default: fatalError("unrecognized error name in golden fixture: \(name)")
        }
    }
}
