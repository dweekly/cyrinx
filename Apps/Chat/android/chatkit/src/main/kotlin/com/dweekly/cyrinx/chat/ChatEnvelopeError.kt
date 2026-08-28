package com.dweekly.cyrinx.chat

/**
 * The six envelope v1 codec errors, identical names across all three
 * implementations (Swift `ChatEnvelopeError`, this Kotlin sealed class, and the
 * Python fixture generator's exception classes) per ../../../ENVELOPE.md section 5.
 *
 * [errorName] mirrors the Python oracle's `error_name` class attribute
 * (../../../ENVELOPE.md section 5) and is exactly the string used in the
 * `"error"` field of ../../../fixtures/chat-envelope-golden.json's error vectors --
 * tests compare against [errorName], not against `this::class.simpleName`, so a
 * refactor that renames the Kotlin class does not silently break golden-vector
 * comparison.
 */
sealed class ChatEnvelopeError(val errorName: String, message: String) : Exception(message) {
    /** `version` byte is not `0x01`. ../../../ENVELOPE.md section 1.1, row 1. */
    class UnknownVersion(actual: Int) :
        ChatEnvelopeError("unknownVersion", "unknown envelope version byte: $actual")

    /** `kind` byte is not `0x01` (text). ../../../ENVELOPE.md section 1.1, row 2. */
    class UnknownKind(actual: Int) :
        ChatEnvelopeError("unknownKind", "unknown envelope kind byte: $actual")

    /** Undefined `flags` bit set; `senderIdLen` outside `1..32`; or trailing bytes
     * after an otherwise-complete envelope. ../../../ENVELOPE.md section 5. */
    class Malformed(reason: String) : ChatEnvelopeError("malformed", reason)

    /** Buffer ends before a field (of any kind) can be fully read.
     * ../../../ENVELOPE.md section 5. */
    class Truncated(reason: String) : ChatEnvelopeError("truncated", reason)

    /** `bodyLen` field value exceeds 2048, checked before reading body bytes.
     * ../../../ENVELOPE.md section 5. */
    class OversizeBody(actualLen: Int) : ChatEnvelopeError(
        "oversizeBody",
        "bodyLen $actualLen exceeds MAX_BODY_LEN ${ChatEnvelopeCodec.MAX_BODY_LEN}",
    )

    /** `body` bytes, once fully read, do not form strict, valid UTF-8.
     * ../../../ENVELOPE.md section 5. */
    class InvalidUtf8(reason: String) : ChatEnvelopeError("invalidUtf8", reason)
}
