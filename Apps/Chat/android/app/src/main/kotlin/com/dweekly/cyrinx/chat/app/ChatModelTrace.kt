package com.dweekly.cyrinx.chat.app

import com.dweekly.cyrinx.chat.toHexString
import java.util.concurrent.CopyOnWriteArrayList

/**
 * One recorded model-trace line's constituent data, before JSON rendering. This
 * is the PROJECTION-level trace schema pinned by
 * `/private/tmp/.../C3_29_30_DESIGN_BRIEF.md`'s "Model-trace schema (pinned)"
 * section -- distinct from `com.dweekly.cyrinx.chat.ChatTraceEntry`
 * (chatkit's own per-`ChatEvent` wire-level trace, CONTRACT.md section 4, which
 * already exists and is unrelated to this one): after EVERY consumed
 * [com.dweekly.cyrinx.chat.ChatEvent], the MODEL (this app's [ChatViewModel])
 * appends one line describing its FULL current [ChatUiState] projection at that
 * point, not the event's own payload fields.
 */
data class ChatModelTraceEntry(
    /** The just-consumed event's own eventSeq (client A's, per the design
     * brief: "model attached to client A"). */
    val eventSeq: Long,
    /** [com.dweekly.cyrinx.chat.ChatConnectionState.wireName]. */
    val connection: String,
    /** [com.dweekly.cyrinx.chat.LinkBudgetClass.wireName]. */
    val budget: String,
    /** idHex array, in the pinned peer sort order (already the order
     * [ChatUiState.peers] is maintained in -- see
     * [ChatProjection.upsertPeerSorted]). */
    val peers: List<String>,
    val messages: List<ChatModelTraceMessage>,
    /** [ChatUiState.banner], verbatim (already the pinned plain-language
     * string or `null`). */
    val banner: String?,
    /** [ChatUiState.eventSeqGapDetected]. */
    val gap: Boolean,
)

/** One [ChatModelTraceEntry.messages] row: "entries in list order with only
 * idHex/direction/status" (design brief). */
data class ChatModelTraceMessage(
    val idHex: String,
    /** [com.dweekly.cyrinx.chat.ChatMessage.Direction.wireName]. */
    val direction: String,
    /** [com.dweekly.cyrinx.chat.ChatMessageDisplayStatus.wireName]. */
    val status: String,
)

/** Projects the pinned subset of [ChatUiState] into one [ChatModelTraceEntry],
 * for the just-consumed event's [eventSeq]. */
fun ChatUiState.toModelTraceEntry(eventSeq: Long): ChatModelTraceEntry =
    ChatModelTraceEntry(
        eventSeq = eventSeq,
        connection = connection.wireName,
        budget = budget.classification.wireName,
        peers = peers.map { it.id.toHexString() },
        messages = messages.map { ChatModelTraceMessage(it.id.toHexString(), it.direction.wireName, it.status.wireName) },
        banner = banner,
        gap = eventSeqGapDetected,
    )

/**
 * Renders [ChatModelTraceEntry] values to the canonical JSON-lines format the
 * design brief pins: "canonical field order and `\": \"`/`\", \"` spacing
 * exactly as CONTRACT.md section 4" (i.e. the SAME formatting convention as
 * `com.dweekly.cyrinx.chat.ChatTraceJson`, applied to this different schema).
 * Hand-rolled, no JSON library dependency -- same rationale as chatkit's own
 * `ChatTraceJson`.
 */
object ChatModelTraceJson {
    fun renderLine(entry: ChatModelTraceEntry): String {
        val sb = StringBuilder()
        sb.append('{')
        appendField(sb, "eventSeq", entry.eventSeq.toString(), first = true)
        appendField(sb, "connection", jsonString(entry.connection))
        appendField(sb, "budget", jsonString(entry.budget))
        appendField(sb, "peers", renderStringArray(entry.peers))
        appendField(sb, "messages", renderMessages(entry.messages))
        appendField(sb, "banner", entry.banner?.let { jsonString(it) } ?: JSON_NULL)
        appendField(sb, "gap", entry.gap.toString())
        sb.append('}')
        return sb.toString()
    }

    private fun renderStringArray(values: List<String>): String {
        if (values.isEmpty()) return "[]"
        return values.joinToString(separator = ", ", prefix = "[", postfix = "]") { jsonString(it) }
    }

    private fun renderMessages(messages: List<ChatModelTraceMessage>): String {
        if (messages.isEmpty()) return "[]"
        return messages.joinToString(separator = ", ", prefix = "[", postfix = "]") { renderMessage(it) }
    }

    private fun renderMessage(message: ChatModelTraceMessage): String {
        val sb = StringBuilder()
        sb.append('{')
        appendField(sb, "idHex", jsonString(message.idHex), first = true)
        appendField(sb, "direction", jsonString(message.direction))
        appendField(sb, "status", jsonString(message.status))
        sb.append('}')
        return sb.toString()
    }

    private fun appendField(sb: StringBuilder, name: String, valueJson: String, first: Boolean = false) {
        if (!first) sb.append(", ")
        sb.append('"').append(name).append('"').append(": ").append(valueJson)
    }

    /** Same minimal escaping policy as chatkit's `ChatTraceJson.jsonString` --
     * see that function's doc comment. Not shared/reused directly because it
     * is `private` there and this schema deliberately has no other coupling to
     * that class. */
    private fun jsonString(value: String): String {
        val sb = StringBuilder(value.length + 2)
        sb.append('"')
        for (c in value) {
            when (c) {
                '"' -> sb.append("\\\"")
                '\\' -> sb.append("\\\\")
                '\n' -> sb.append("\\n")
                '\r' -> sb.append("\\r")
                '\t' -> sb.append("\\t")
                else ->
                    if (c.code < 0x20) {
                        sb.append("\\u").append(c.code.toString(16).padStart(4, '0'))
                    } else {
                        sb.append(c)
                    }
            }
        }
        sb.append('"')
        return sb.toString()
    }

    private const val JSON_NULL: String = "null"
}

/**
 * Collects one [ChatModelTraceEntry] per consumed [com.dweekly.cyrinx.chat.ChatEvent],
 * in consumption order -- the model-level counterpart to chatkit's
 * `ChatTraceRecorder`, wired into [ChatViewModel]'s event-collection loop.
 */
class ChatModelTraceRecorder {
    private val lines = CopyOnWriteArrayList<String>()

    fun record(state: ChatUiState, eventSeq: Long) {
        lines.add(ChatModelTraceJson.renderLine(state.toModelTraceEntry(eventSeq)))
    }

    /** Newline-terminated JSON-lines text (including after the last line, when
     * non-empty), matching chatkit's `ChatTraceRecorder.toJsonLines()`
     * convention exactly. */
    fun toJsonLines(): String {
        if (lines.isEmpty()) return ""
        return lines.joinToString(separator = "\n", postfix = "\n")
    }
}
