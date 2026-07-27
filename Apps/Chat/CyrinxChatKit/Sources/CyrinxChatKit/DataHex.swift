import Foundation

/// Lowercase-hex helpers shared across the envelope codec, the contract
/// types (`ChatPeer.id`, `ChatMessage.senderPeerIdHex`, ...), and the
/// JSON-lines trace writer. Apps/Chat/ENVELOPE.md §8 and CONTRACT.md pin
/// every hex string in this package as lowercase, no `0x` prefix, no
/// separators -- matching the golden-vector fixture's `bytes_hex` /
/// `*IdHex` fields exactly.
extension Data {
    var hexString: String {
        var out = String()
        out.reserveCapacity(count * 2)
        for byte in self {
            out += String(format: "%02x", byte)
        }
        return out
    }
}

extension Array where Element == UInt8 {
    var hexString: String {
        Data(self).hexString
    }
}
