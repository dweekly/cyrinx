import Foundation

/// Lowercase-hex helpers, `public` so both this package's own model code AND
/// the SwiftUI app target (a separate module that depends on this package
/// as a product, e.g. to turn a `ChatPeer.id` into the hex string
/// `ChatModel.connect(toPeerIdHex:)` expects) can use them. `CyrinxChatKit`
/// already has an identical-shaped `Data.hexString` extension (Apps/Chat/
/// CyrinxChatKit/Sources/CyrinxChatKit/DataHex.swift), but that extension's
/// members carry no `public` modifier, so they default to `internal` and
/// are not visible outside the `CyrinxChatKit` module -- this package
/// cannot see or reuse them even though it depends on that package. This
/// is a small, deliberate duplication (matching Apps/Chat/CONTRACT.md's own
/// hex conventions: lowercase, no `0x` prefix, no separators) rather than
/// widening CyrinxChatKit's own public surface just for this, made public
/// here (unlike CyrinxChatKit's private copy) since this package DOES own
/// its API surface and both this package's model code and the app UI
/// legitimately need it.
extension Data {
    public var hexString: String {
        var out = String()
        out.reserveCapacity(count * 2)
        for byte in self {
            out += String(format: "%02x", byte)
        }
        return out
    }

    /// Parses a lowercase- or uppercase-hex string (even length) back into
    /// `Data`. `nil` on malformed input -- used to reconstruct the `Data`
    /// backing an outgoing `ChatMessage.id` from the `messageIdHex` string
    /// `ChatTransportClient.send(body:)` returns (CONTRACT.md §1.8), which
    /// is the only form that call gives back. Failable (not a
    /// `precondition`) because, unlike this package's own test fixtures,
    /// the string here originates from a transport implementation this
    /// model does not control -- a live SDK adapter (C3-31) misbehaving
    /// should surface as a handled composer error, not a crash.
    public init?(hexString: String) {
        guard hexString.count % 2 == 0 else { return nil }
        var data = Data(capacity: hexString.count / 2)
        var index = hexString.startIndex
        while index < hexString.endIndex {
            let next = hexString.index(index, offsetBy: 2)
            guard let byte = UInt8(hexString[index..<next], radix: 16) else { return nil }
            data.append(byte)
            index = next
        }
        self = data
    }
}
