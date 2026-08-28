import Foundation
import Testing

@testable import CyrinxChatKit

/// Golden-vector conformance for the envelope v1 codec, parameterized over
/// every vector in Apps/Chat/fixtures/chat-envelope-golden.json (22
/// vectors: 6 decode, 16 error -- ENVELOPE.md §10's traceability table,
/// amended by the C3-28 sequence amendment to add `sequence_max_u64_accepted`,
/// `reject_sequence_zero`, and `truncated_mid_sequence`). Checks:
/// decode-field equality, canonical re-encode byte-identity (ENVELOPE.md
/// §3), and exact error case per vector (ENVELOPE.md §5).
@Suite("Chat envelope golden vectors")
struct ChatEnvelopeGoldenTests {
    static let allVectors = GoldenFixture.loadVectors()
    static let decodeVectors = allVectors.filter { $0.expect == "decode" }
    static let errorVectors = allVectors.filter { $0.expect == "error" }

    @Test("fixture has the documented vector counts (22 total: 6 decode, 16 error)")
    func fixtureShape() {
        #expect(Self.allVectors.count == 22)
        #expect(Self.decodeVectors.count == 6)
        #expect(Self.errorVectors.count == 16)
    }

    @Test(
        "decode vector: field equality and canonical re-encode byte-identity",
        arguments: decodeVectors
    )
    func decodeVectorRoundTrips(_ vector: GoldenVectorJSON) throws {
        let bytes = Data(hexString: vector.bytesHex)
        guard let decodedFields = vector.decoded else {
            Issue.record("vector \(vector.name): expect=decode but no 'decoded' object present")
            return
        }

        let envelope = try ChatEnvelopeCodec.decode(bytes)

        #expect(decodedFields.version == 1, "vector \(vector.name)")
        #expect(decodedFields.kind == "text", "vector \(vector.name)")
        #expect(envelope.messageId.hexString == decodedFields.messageIdHex, "vector \(vector.name)")
        #expect(envelope.replyToId?.hexString == decodedFields.replyToIdHex, "vector \(vector.name)")
        #expect(envelope.senderId.hexString == decodedFields.senderIdHex, "vector \(vector.name)")
        #expect(envelope.sequence == decodedFields.sequence, "vector \(vector.name)")
        #expect(envelope.body == decodedFields.body, "vector \(vector.name)")

        // Canonical encoding (ENVELOPE.md §3): re-encoding the decoded
        // value must reproduce the input bytes exactly.
        let reencoded = try ChatEnvelopeCodec.encode(envelope)
        #expect(reencoded == bytes, "vector \(vector.name): re-encode is not byte-identical")
    }

    @Test("error vector: decode raises exactly the named error", arguments: errorVectors)
    func errorVectorRaisesExactCase(_ vector: GoldenVectorJSON) {
        let bytes = Data(hexString: vector.bytesHex)
        guard let errorName = vector.error else {
            Issue.record("vector \(vector.name): expect=error but no 'error' field present")
            return
        }
        let expectedError = GoldenFixture.envelopeError(named: errorName)

        do {
            _ = try ChatEnvelopeCodec.decode(bytes)
            Issue.record("vector \(vector.name): expected \(expectedError), decode succeeded")
        } catch let actual as ChatEnvelopeError {
            #expect(actual == expectedError, "vector \(vector.name)")
        } catch {
            Issue.record("vector \(vector.name): threw non-ChatEnvelopeError \(error)")
        }
    }
}
