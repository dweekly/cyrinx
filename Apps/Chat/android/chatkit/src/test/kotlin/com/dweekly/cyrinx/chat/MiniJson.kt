package com.dweekly.cyrinx.chat

/**
 * A minimal, recursive-descent JSON parser for test-only use, so this test suite
 * can read ../fixtures/chat-envelope-golden.json without adding a JSON library
 * dependency (chatkit's dependencies are deliberately limited to
 * kotlinx-coroutines-core plus JUnit/kotlinx-coroutines-test for tests; see
 * chatkit/build.gradle.kts). Not a general-purpose JSON library -- it covers
 * exactly what RFC 8259 requires and nothing more (no comments, no trailing
 * commas), which is sufficient for this repo's own generated fixture files.
 *
 * Produces plain Kotlin values: `Map<String, Any?>` for objects (insertion-order
 * preserved via `LinkedHashMap`), `List<Any?>` for arrays, `String`, `Boolean`,
 * `null`, and for JSON numbers: [java.math.BigInteger] for an integer literal (no
 * `.`/`e`/`E`), or [Double] otherwise. The [java.math.BigInteger] case matters for
 * ../fixtures/chat-envelope-golden.json's `sequence` field specifically --
 * ../../../ENVELOPE.md section 8 requires `sequence_max_u64_accepted`'s
 * `18446744073709551615` to "round-trip exactly," which a `Double` intermediate
 * cannot represent (a `u64` has 64 significant bits; an IEEE-754 `double` has only
 * 53). See [JsonAccess.uLong].
 */
object MiniJson {
    fun parse(text: String): Any? {
        val parser = Parser(text)
        parser.skipWhitespace()
        val value = parser.parseValue()
        parser.skipWhitespace()
        require(parser.atEnd()) { "trailing content after JSON value at offset ${parser.pos}" }
        return value
    }

    private class Parser(private val text: String) {
        var pos: Int = 0

        fun atEnd(): Boolean = pos >= text.length

        fun skipWhitespace() {
            while (pos < text.length && text[pos].isWhitespace()) pos++
        }

        fun parseValue(): Any? {
            skipWhitespace()
            require(pos < text.length) { "unexpected end of JSON input" }
            return when (text[pos]) {
                '{' -> parseObject()
                '[' -> parseArray()
                '"' -> parseString()
                't' -> parseLiteral("true", true)
                'f' -> parseLiteral("false", false)
                'n' -> parseLiteral("null", null)
                else -> parseNumber()
            }
        }

        private fun parseLiteral(literal: String, value: Any?): Any? {
            require(text.regionMatches(pos, literal, 0, literal.length)) {
                "expected literal \"$literal\" at offset $pos"
            }
            pos += literal.length
            return value
        }

        private fun parseObject(): Map<String, Any?> {
            expect('{')
            val result = LinkedHashMap<String, Any?>()
            skipWhitespace()
            if (peek() == '}') {
                pos++
                return result
            }
            while (true) {
                skipWhitespace()
                val key = parseString()
                skipWhitespace()
                expect(':')
                val value = parseValue()
                result[key] = value
                skipWhitespace()
                when (peek()) {
                    ',' -> {
                        pos++
                    }
                    '}' -> {
                        pos++
                        return result
                    }
                    else -> error("expected ',' or '}' at offset $pos")
                }
            }
        }

        private fun parseArray(): List<Any?> {
            expect('[')
            val result = mutableListOf<Any?>()
            skipWhitespace()
            if (peek() == ']') {
                pos++
                return result
            }
            while (true) {
                result.add(parseValue())
                skipWhitespace()
                when (peek()) {
                    ',' -> {
                        pos++
                    }
                    ']' -> {
                        pos++
                        return result
                    }
                    else -> error("expected ',' or ']' at offset $pos")
                }
            }
        }

        private fun parseString(): String {
            expect('"')
            val sb = StringBuilder()
            while (true) {
                require(pos < text.length) { "unterminated string starting before offset $pos" }
                val c = text[pos++]
                when (c) {
                    '"' -> return sb.toString()
                    '\\' -> {
                        require(pos < text.length) { "unterminated escape at offset $pos" }
                        when (val esc = text[pos++]) {
                            '"' -> sb.append('"')
                            '\\' -> sb.append('\\')
                            '/' -> sb.append('/')
                            'b' -> sb.append('\b')
                            'f' -> sb.append('\u000C')
                            'n' -> sb.append('\n')
                            'r' -> sb.append('\r')
                            't' -> sb.append('\t')
                            'u' -> {
                                require(pos + 4 <= text.length) { "truncated \\u escape at offset $pos" }
                                val hex = text.substring(pos, pos + 4)
                                pos += 4
                                sb.append(hex.toInt(16).toChar())
                            }
                            else -> error("invalid escape \\$esc at offset ${pos - 1}")
                        }
                    }
                    else -> sb.append(c)
                }
            }
        }

        /** Returns [java.math.BigInteger] for an integral literal (arbitrary
         * precision -- see this file's top-level doc comment) or [Double]
         * otherwise. */
        private fun parseNumber(): Any {
            val start = pos
            var isIntegral = true
            if (peek() == '-') pos++
            while (pos < text.length && text[pos].isDigit()) pos++
            if (pos < text.length && text[pos] == '.') {
                isIntegral = false
                pos++
                while (pos < text.length && text[pos].isDigit()) pos++
            }
            if (pos < text.length && (text[pos] == 'e' || text[pos] == 'E')) {
                isIntegral = false
                pos++
                if (pos < text.length && (text[pos] == '+' || text[pos] == '-')) pos++
                while (pos < text.length && text[pos].isDigit()) pos++
            }
            require(pos > start) { "expected a number at offset $start" }
            val raw = text.substring(start, pos)
            return if (isIntegral) java.math.BigInteger(raw) else raw.toDouble()
        }

        private fun peek(): Char {
            require(pos < text.length) { "unexpected end of JSON input" }
            return text[pos]
        }

        private fun expect(c: Char) {
            require(pos < text.length && text[pos] == c) { "expected '$c' at offset $pos" }
            pos++
        }
    }
}

/** Typed accessors for the `Map<String, Any?>` / `List<Any?>` shape [MiniJson]
 * produces, to keep golden-vector test code free of repeated unchecked casts. */
@Suppress("UNCHECKED_CAST")
object JsonAccess {
    fun obj(value: Any?): Map<String, Any?> = value as Map<String, Any?>

    fun arr(value: Any?): List<Any?> = value as List<Any?>

    fun str(value: Any?): String = value as String

    fun strOrNull(value: Any?): String? = value as String?

    fun int(value: Any?): Int = (value as java.math.BigInteger).intValueExact()

    /** Reads a JSON integer literal as the bit pattern of an unsigned 64-bit
     * value -- e.g. `18446744073709551615` (`u64` max) becomes `-1L` -- matching
     * ../../../ENVELOPE.md section 8's "decode it straight into `UInt64`/`ULong`,
     * never through a `Double` intermediate" requirement for the golden
     * fixture's `sequence` field. [java.math.BigInteger.toLong] returns exactly
     * the low-order 64 bits for a value too large to fit in a signed [Long],
     * which for a value already range-checked to `0..2^64-1` (as every
     * `sequence` in the fixture is) is precisely the `u64` wire bit pattern. */
    fun uLong(value: Any?): Long = (value as java.math.BigInteger).toLong()
}
