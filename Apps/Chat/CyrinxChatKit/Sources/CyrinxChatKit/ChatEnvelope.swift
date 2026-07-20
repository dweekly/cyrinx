import Foundation

/// Chat envelope v1 error taxonomy. Six cases, one per row of
/// Apps/Chat/ENVELOPE.md §5's error table, identical *names* to the Kotlin
/// sealed class and the Python oracle's `error_name` values (the three
/// implementations are checked against the same golden vectors, not against
/// each other directly -- see ENVELOPE.md's opening paragraph).
public enum ChatEnvelopeError: Error, Equatable {
    /// `version` byte is not `0x01` (ENVELOPE.md §1.1).
    case unknownVersion
    /// `kind` byte is not `0x01` (ENVELOPE.md §1.1).
    case unknownKind
    /// Undefined `flags` bit set; `senderIdLen` outside `1..32`; trailing
    /// bytes after a complete envelope (ENVELOPE.md §5).
    case malformed
    /// Buffer ends before a field (of any kind) can be fully read
    /// (ENVELOPE.md §5).
    case truncated
    /// `bodyLen` exceeds 2048, checked before reading body bytes
    /// (ENVELOPE.md §5, §2 step 8).
    case oversizeBody
    /// `body` bytes, once fully read, do not form strict, valid UTF-8
    /// (ENVELOPE.md §5, §2 step 10).
    case invalidUtf8
}

/// Chat envelope v1 value type: the decoded/pre-encode form of the wire
/// layout pinned in Apps/Chat/ENVELOPE.md §1. `version` and `kind` are not
/// stored fields -- v1 defines exactly one value for each (`0x01`), so the
/// codec always writes those constants and a successful decode always
/// implies them; storing them would only ever hold one legal value.
public struct ChatEnvelope: Equatable, Sendable {
    /// Wire `version` byte (ENVELOPE.md §1.1, offset 0). The only value v1
    /// accepts on decode or emits on encode.
    public static let version: UInt8 = 0x01
    /// Wire `kind` byte (ENVELOPE.md §1.1, offset 1). `0x01` = text, the
    /// only kind v1 defines.
    public static let kindText: UInt8 = 0x01

    /// `messageId` width in bytes (ENVELOPE.md §1.1, offset 3): a 128-bit
    /// opaque ID, always present.
    public static let messageIdLength = 16
    /// `replyToId` width in bytes when `flags` bit0 is set (ENVELOPE.md
    /// §1.2): also 128-bit, same width as `messageId`.
    public static let replyToIdLength = 16
    /// `senderIdLen` valid range, inclusive (ENVELOPE.md §1.2): `0` or
    /// `>32` is `malformed`.
    public static let minSenderIdLength = 1
    public static let maxSenderIdLength = 32
    /// `bodyLen` valid maximum in bytes (ENVELOPE.md §1.2): `>2048` is
    /// `oversizeBody`. Matches the Phase F product-scope 2 KiB body cap
    /// (Apps/Chat/README.md "Scope").
    public static let maxBodyLength = 2048

    /// Maximum possible encoded envelope size, per ENVELOPE.md §4:
    /// `version(1) + kind(1) + flags(1) + messageId(16) + replyToId(16)
    /// + senderIdLen(1) + senderId(32 max) + bodyLen(2) + body(2048 max)
    /// = 2118 bytes`. The `body_max_2048` golden vector is the concrete,
    /// hand-verifiable instance of this arithmetic.
    public static let maxEncodedLength =
        1 + 1 + 1 + messageIdLength + replyToIdLength + 1 + maxSenderIdLength + 2 + maxBodyLength

    /// 128-bit opaque message ID. Always exactly `messageIdLength` (16)
    /// bytes for a value produced by this package; the codec's encoder
    /// enforces this and throws `.malformed` otherwise (ENVELOPE.md §3).
    public var messageId: Data
    /// 128-bit opaque reply-target ID, present iff the wire `flags` bit0 is
    /// set. `nil` means "no replyTo" (`flags` bit0 clear); a non-nil value
    /// must be exactly `replyToIdLength` (16) bytes.
    public var replyToId: Data?
    /// Opaque, ephemeral transport peer ID of the sender (ENVELOPE.md
    /// §1.2). Length must be within `minSenderIdLength...maxSenderIdLength`
    /// (1..32).
    public var senderId: Data
    /// Decoded/to-be-encoded UTF-8 text. Every Swift `String` is already
    /// valid Unicode, so the encoder's `invalidUtf8` guard (ENVELOPE.md §3)
    /// can never actually fire from this initializer -- only the decoder's
    /// UTF-8 validation of externally supplied bytes can produce that
    /// error. `body.utf8.count` (not `body.count`, which counts grapheme
    /// clusters) is what the encoder checks against `maxBodyLength`.
    public var body: String

    public init(messageId: Data, replyToId: Data? = nil, senderId: Data, body: String) {
        self.messageId = messageId
        self.replyToId = replyToId
        self.senderId = senderId
        self.body = body
    }
}

/// Strict sequential codec for chat envelope v1, implementing the parse
/// order pinned in Apps/Chat/ENVELOPE.md §2 field for field (including the
/// two "range check before availability" rules for `senderIdLen` and
/// `bodyLen`, §2 steps 6 and 8) and the canonical-encoding guarantee of §3:
/// re-encoding any successfully decoded value reproduces the original bytes
/// exactly. Validated against every vector in
/// Apps/Chat/fixtures/chat-envelope-golden.json (the same fixture the
/// Python oracle in fixtures/tools/generate_golden.py self-tests against;
/// all three implementations are checked against that fixture, not against
/// each other -- ENVELOPE.md's opening paragraph).
public enum ChatEnvelopeCodec {
    /// Encodes `envelope` to its canonical wire form. Throws `.malformed` if
    /// `messageId`/`replyToId` are the wrong width or `senderId` is outside
    /// `1..32` bytes, `.oversizeBody` if the body's UTF-8 byte length
    /// exceeds 2048. `.unknownVersion`/`.unknownKind`/`.invalidUtf8` are
    /// listed by ENVELOPE.md §3 as errors an encoder must be able to raise
    /// in principle, but are unreachable through this API: `version`/`kind`
    /// are fixed v1 constants (not caller-supplied fields on
    /// `ChatEnvelope`), and `body: String` is always already valid Unicode
    /// by construction of the Swift `String` type.
    public static func encode(_ envelope: ChatEnvelope) throws -> Data {
        guard envelope.messageId.count == ChatEnvelope.messageIdLength else {
            throw ChatEnvelopeError.malformed
        }
        if let replyToId = envelope.replyToId, replyToId.count != ChatEnvelope.replyToIdLength {
            throw ChatEnvelopeError.malformed
        }
        guard
            (ChatEnvelope.minSenderIdLength...ChatEnvelope.maxSenderIdLength)
                .contains(envelope.senderId.count)
        else {
            throw ChatEnvelopeError.malformed
        }
        let bodyBytes = [UInt8](envelope.body.utf8)
        guard bodyBytes.count <= ChatEnvelope.maxBodyLength else {
            throw ChatEnvelopeError.oversizeBody
        }

        let hasReplyTo = envelope.replyToId != nil
        var out = [UInt8]()
        out.reserveCapacity(
            3 + ChatEnvelope.messageIdLength
                + (hasReplyTo ? ChatEnvelope.replyToIdLength : 0)
                + 1 + envelope.senderId.count + 2 + bodyBytes.count
        )
        out.append(ChatEnvelope.version)
        out.append(ChatEnvelope.kindText)
        out.append(hasReplyTo ? 0x01 : 0x00)
        out.append(contentsOf: envelope.messageId)
        if let replyToId = envelope.replyToId {
            out.append(contentsOf: replyToId)
        }
        out.append(UInt8(envelope.senderId.count))
        out.append(contentsOf: envelope.senderId)
        let bodyLen = UInt16(bodyBytes.count)
        out.append(UInt8(bodyLen >> 8))
        out.append(UInt8(bodyLen & 0x00FF))
        out.append(contentsOf: bodyBytes)
        return Data(out)
    }

    /// Decodes `data` per the strict sequential parse order in
    /// Apps/Chat/ENVELOPE.md §2 (steps 1-11), stopping at the first
    /// violation encountered -- no lookahead, no partial recovery.
    public static func decode(_ data: Data) throws -> ChatEnvelope {
        let bytes = [UInt8](data)
        var pos = 0

        func takeByte() throws -> UInt8 {
            guard pos < bytes.count else { throw ChatEnvelopeError.truncated }
            defer { pos += 1 }
            return bytes[pos]
        }
        func take(_ n: Int) throws -> [UInt8] {
            guard bytes.count - pos >= n else { throw ChatEnvelopeError.truncated }
            let slice = Array(bytes[pos..<(pos + n)])
            pos += n
            return slice
        }

        // Step 1: version (ENVELOPE.md §2.1).
        let version = try takeByte()
        guard version == ChatEnvelope.version else { throw ChatEnvelopeError.unknownVersion }

        // Step 2: kind (ENVELOPE.md §2.2).
        let kind = try takeByte()
        guard kind == ChatEnvelope.kindText else { throw ChatEnvelopeError.unknownKind }

        // Step 3: flags; any bit outside bit0 set is malformed (ENVELOPE.md §2.3).
        let flags = try takeByte()
        guard flags & ~0x01 == 0 else { throw ChatEnvelopeError.malformed }
        let replyToPresent = flags & 0x01 != 0

        // Step 4: messageId, fixed 16 bytes (ENVELOPE.md §2.4).
        let messageId = try take(ChatEnvelope.messageIdLength)

        // Step 5: replyToId, present iff flags bit0 (ENVELOPE.md §2.5).
        var replyToId: [UInt8]?
        if replyToPresent {
            replyToId = try take(ChatEnvelope.replyToIdLength)
        }

        // Step 6: senderIdLen, range-checked BEFORE reading senderId bytes
        // (ENVELOPE.md §2.6 -- the "range before availability" rule that
        // makes senderIdLen=33 with zero trailing bytes `malformed`, not
        // `truncated`).
        let senderIdLen = try takeByte()
        guard
            (ChatEnvelope.minSenderIdLength...ChatEnvelope.maxSenderIdLength)
                .contains(Int(senderIdLen))
        else {
            throw ChatEnvelopeError.malformed
        }

        // Step 7: senderId (ENVELOPE.md §2.7).
        let senderId = try take(Int(senderIdLen))

        // Step 8: bodyLen (big-endian u16), range-checked BEFORE reading
        // body bytes -- same "range before availability" rule as step 6
        // (ENVELOPE.md §2.8; the bodyLen=2049 golden vector supplies zero
        // body bytes and must still be oversizeBody, not truncated).
        let bodyLenHi = try takeByte()
        let bodyLenLo = try takeByte()
        let bodyLen = (Int(bodyLenHi) << 8) | Int(bodyLenLo)
        guard bodyLen <= ChatEnvelope.maxBodyLength else { throw ChatEnvelopeError.oversizeBody }

        // Step 9: body bytes (ENVELOPE.md §2.9).
        let bodyBytes = try take(bodyLen)

        // Step 10: strict UTF-8 validation (ENVELOPE.md §2.10). Foundation's
        // `String(bytes:encoding:.utf8)` performs strict validation --
        // overlong encodings, truncated multibyte sequences, and lone
        // continuation/lead bytes are all rejected, returning `nil` -- as
        // opposed to the standard library's `String(decoding:as:)`, which
        // *repairs* invalid input with U+FFFD rather than failing. This
        // choice was verified empirically against all three invalid-UTF-8
        // golden-vector shapes (0xFF lead byte, truncated multibyte,
        // overlong 2-byte encoding) before being relied on here; see the
        // C3-28 spec-stage report for the probe. `String(validating:as:)`
        // (Swift 6 stdlib) would be the more obvious choice but requires
        // macOS 15+ / iOS 18+, newer than this package's macOS 13 / iOS 17
        // floor (Package.swift, matching the repository root's minimums).
        guard let body = String(bytes: bodyBytes, encoding: .utf8) else {
            throw ChatEnvelopeError.invalidUtf8
        }

        // Step 11: no trailing bytes (ENVELOPE.md §2.11).
        guard pos == bytes.count else { throw ChatEnvelopeError.malformed }

        return ChatEnvelope(
            messageId: Data(messageId),
            replyToId: replyToId.map { Data($0) },
            senderId: Data(senderId),
            body: body
        )
    }
}
