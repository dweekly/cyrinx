import Foundation
import Testing

@testable import CyrinxChatKit

/// Envelope property tests at the pinned bounds (ENVELOPE.md §1.2:
/// `bodyLen` valid range `0..2048`; §4: `MAX_ENVELOPE_LEN` arithmetic),
/// independent of the golden fixture (which already covers the same
/// bounds with fixed hex vectors -- these tests instead construct values
/// programmatically, to catch a boundary regression that happens to slip
/// past a hand-picked hex vector).
@Suite("Chat envelope property tests at bounds")
struct ChatEnvelopePropertyTests {
    @Test("body exactly 2048 bytes encodes and round-trips")
    func bodyAtMaxRoundTrips() throws {
        let body = String(repeating: "A", count: ChatEnvelope.maxBodyLength)
        let envelope = ChatEnvelope(
            messageId: Data(repeating: 0x01, count: ChatEnvelope.messageIdLength),
            replyToId: nil,
            senderId: Data(repeating: 0xAB, count: 4),
            body: body
        )
        let encoded = try ChatEnvelopeCodec.encode(envelope)
        // version(1) + kind(1) + flags(1) + messageId(16) + senderIdLen(1)
        // + senderId(4) + bodyLen(2) + body(2048), no replyTo (ENVELOPE.md §1.3).
        #expect(encoded.count == 3 + 16 + 1 + 4 + 2 + ChatEnvelope.maxBodyLength)

        let decoded = try ChatEnvelopeCodec.decode(encoded)
        #expect(decoded.body == body)
        #expect(decoded.body.utf8.count == ChatEnvelope.maxBodyLength)
    }

    @Test("body 2049 bytes (utf8) fails to encode with oversizeBody")
    func bodyOverMaxFailsToEncode() {
        let body = String(repeating: "A", count: ChatEnvelope.maxBodyLength + 1)
        let envelope = ChatEnvelope(
            messageId: Data(repeating: 0x01, count: ChatEnvelope.messageIdLength),
            replyToId: nil,
            senderId: Data(repeating: 0xAB, count: 4),
            body: body
        )
        do {
            _ = try ChatEnvelopeCodec.encode(envelope)
            Issue.record("expected oversizeBody, encode succeeded")
        } catch let error as ChatEnvelopeError {
            #expect(error == .oversizeBody)
        } catch {
            Issue.record("threw non-ChatEnvelopeError \(error)")
        }
    }

    @Test("crafted bodyLen=2049 with zero body bytes decodes as oversizeBody, not truncated")
    func craftedBodyLenOverMaxIsOversizeNotTruncated() {
        // version, kind, flags=0 (no replyTo), messageId(16), senderIdLen=4,
        // senderId(4), bodyLen=2049 (0x0801) big-endian, then NO body bytes.
        var bytes: [UInt8] = [ChatEnvelope.version, ChatEnvelope.kindText, 0x00]
        bytes.append(contentsOf: Array(repeating: 0x01, count: ChatEnvelope.messageIdLength))
        bytes.append(0x04)
        bytes.append(contentsOf: [0xDE, 0xAD, 0xBE, 0xEF])
        let overMaxBodyLen = UInt16(ChatEnvelope.maxBodyLength + 1)
        bytes.append(UInt8(overMaxBodyLen >> 8))
        bytes.append(UInt8(overMaxBodyLen & 0x00FF))

        do {
            _ = try ChatEnvelopeCodec.decode(Data(bytes))
            Issue.record("expected oversizeBody, decode succeeded")
        } catch let error as ChatEnvelopeError {
            #expect(error == .oversizeBody)
        } catch {
            Issue.record("threw non-ChatEnvelopeError \(error)")
        }
    }

    @Test("bodyLen exactly 2048 with all 2048 body bytes present decodes cleanly")
    func craftedBodyLenAtMaxDecodesCleanly() throws {
        var bytes: [UInt8] = [ChatEnvelope.version, ChatEnvelope.kindText, 0x00]
        bytes.append(contentsOf: Array(repeating: 0x02, count: ChatEnvelope.messageIdLength))
        bytes.append(0x04)
        bytes.append(contentsOf: [0xDE, 0xAD, 0xBE, 0xEF])
        let maxBodyLen = UInt16(ChatEnvelope.maxBodyLength)
        bytes.append(UInt8(maxBodyLen >> 8))
        bytes.append(UInt8(maxBodyLen & 0x00FF))
        bytes.append(contentsOf: Array(repeating: UInt8(ascii: "z"), count: ChatEnvelope.maxBodyLength))

        let decoded = try ChatEnvelopeCodec.decode(Data(bytes))
        #expect(decoded.body.utf8.count == ChatEnvelope.maxBodyLength)
    }

    @Test("senderId at max length 32 round-trips")
    func senderIdAtMaxRoundTrips() throws {
        let maxSenderId = Data((0..<ChatEnvelope.maxSenderIdLength).map { UInt8($0) })
        let envelope = ChatEnvelope(
            messageId: Data(repeating: 0x02, count: ChatEnvelope.messageIdLength),
            replyToId: nil,
            senderId: maxSenderId,
            body: "ok"
        )
        let encoded = try ChatEnvelopeCodec.encode(envelope)
        let decoded = try ChatEnvelopeCodec.decode(encoded)
        #expect(decoded.senderId == maxSenderId)
    }

    @Test("senderId at 33 bytes (one over max) fails to encode with malformed")
    func senderIdOverMaxFailsToEncode() {
        let overSenderId = Data((0..<(ChatEnvelope.maxSenderIdLength + 1)).map { UInt8($0) })
        let envelope = ChatEnvelope(
            messageId: Data(repeating: 0x02, count: ChatEnvelope.messageIdLength),
            replyToId: nil,
            senderId: overSenderId,
            body: "ok"
        )
        do {
            _ = try ChatEnvelopeCodec.encode(envelope)
            Issue.record("expected malformed, encode succeeded")
        } catch let error as ChatEnvelopeError {
            #expect(error == .malformed)
        } catch {
            Issue.record("threw non-ChatEnvelopeError \(error)")
        }
    }

    @Test("maximum encoded envelope is exactly 2118 bytes (replyTo + 32-byte senderId + 2048-byte body)")
    func maxEnvelopeSizeArithmetic() throws {
        let envelope = ChatEnvelope(
            messageId: Data(repeating: 0x01, count: ChatEnvelope.messageIdLength),
            replyToId: Data(repeating: 0xF0, count: ChatEnvelope.replyToIdLength),
            senderId: Data((0..<ChatEnvelope.maxSenderIdLength).map { UInt8($0) }),
            body: String(repeating: "A", count: ChatEnvelope.maxBodyLength)
        )
        let encoded = try ChatEnvelopeCodec.encode(envelope)
        #expect(encoded.count == 2118)
        #expect(encoded.count == ChatEnvelope.maxEncodedLength)
    }
}
