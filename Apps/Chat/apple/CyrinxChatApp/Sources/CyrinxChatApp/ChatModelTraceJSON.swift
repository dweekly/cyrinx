import Foundation

/// Minimal, hand-rolled JSON text builder for `ChatModelTraceRecorder`,
/// mirroring `CyrinxChatKit`'s own `ChatTraceJSON` (Apps/Chat/CyrinxChatKit/
/// Sources/CyrinxChatKit/ChatTraceJSON.swift) field for field -- same
/// escaping rules, same `": "`/`", "` spacing, no `Codable`/`JSONEncoder`
/// (whose key ordering is an implementation detail, not something the
/// design brief's pinned model-trace schema can depend on). Not reused
/// directly from `CyrinxChatKit` because that type's members are
/// `internal`, not `public` (see `DataHex.swift`'s doc comment for the
/// identical situation with `Data.hexString`).
enum ChatModelTraceJSON {
    static func string(_ value: String) -> String {
        var out = "\""
        for scalar in value.unicodeScalars {
            switch scalar {
            case "\"":
                out += "\\\""
            case "\\":
                out += "\\\\"
            case "\n":
                out += "\\n"
            case "\r":
                out += "\\r"
            case "\t":
                out += "\\t"
            default:
                if scalar.value < 0x20 {
                    out += String(format: "\\u%04x", scalar.value)
                } else {
                    out.unicodeScalars.append(scalar)
                }
            }
        }
        out += "\""
        return out
    }

    static func stringOrNull(_ value: String?) -> String {
        value.map(string) ?? "null"
    }

    static func bool(_ value: Bool) -> String { value ? "true" : "false" }
    static func uint64(_ value: UInt64) -> String { String(value) }

    /// `[item1, item2, ...]` from already-encoded JSON-value-text items, in
    /// the given order.
    static func array(_ items: [String]) -> String {
        "[" + items.joined(separator: ", ") + "]"
    }

    /// `{"key1": value1, "key2": value2, ...}` from already-encoded
    /// `(key, JSON-value-text)` pairs, in the given order.
    static func object(_ fields: [(String, String)]) -> String {
        "{" + fields.map { "\(string($0.0)): \($0.1)" }.joined(separator: ", ") + "}"
    }
}
