package com.dweekly.cyrinx.chat

/**
 * Renders [ChatTraceEntry] values to the canonical JSON-lines trace format pinned
 * in ../../../CONTRACT.md section 4: top-level field order
 * `eventSeq, virtualTimeMs, client, event`, and the per-`event.type` field orders
 * from that section's table. Hand-rolled (no JSON library dependency -- this
 * module's only dependencies are kotlinx-coroutines-core and, for tests, JUnit
 * and kotlinx-coroutines-test) but restricted to exactly the fixed shapes
 * CONTRACT.md section 4 defines, not a general-purpose JSON writer.
 */
object ChatTraceJson {
    fun renderLine(entry: ChatTraceEntry): String {
        val sb = StringBuilder()
        sb.append('{')
        appendField(sb, "eventSeq", entry.eventSeq.toString(), first = true)
        appendField(sb, "virtualTimeMs", entry.virtualTimeMs.toString())
        appendField(sb, "client", jsonString(entry.client.toString()))
        appendField(sb, "event", renderEvent(entry.event))
        sb.append('}')
        return sb.toString()
    }

    private fun renderEvent(event: ChatEvent): String {
        val sb = StringBuilder()
        sb.append('{')
        when (event) {
            is ChatEvent.PeerFound -> {
                appendField(sb, "type", jsonString("peerFound"), first = true)
                appendField(sb, "peer", renderPeer(event.peer))
            }

            is ChatEvent.PeerUpdated -> {
                appendField(sb, "type", jsonString("peerUpdated"), first = true)
                appendField(sb, "peer", renderPeer(event.peer))
            }

            is ChatEvent.PeerLost -> {
                appendField(sb, "type", jsonString("peerLost"), first = true)
                appendField(sb, "peerIdHex", jsonString(event.peerIdHex))
                appendField(sb, "reason", jsonString(event.reason))
            }

            is ChatEvent.ConnectionChanged -> {
                appendField(sb, "type", jsonString("connectionChanged"), first = true)
                appendField(sb, "state", jsonString(event.state.wireName))
                // reason is null except when state == "disconnected" (CONTRACT.md
                // section 4's per-type field table).
                val reason = (event.state as? ChatConnectionState.Disconnected)?.reason
                appendField(sb, "reason", reason?.let { jsonString(it) } ?: JSON_NULL)
            }

            is ChatEvent.LinkBudgetChanged -> {
                appendField(sb, "type", jsonString("linkBudgetChanged"), first = true)
                appendField(sb, "budget", renderLinkBudget(event.budget))
            }

            is ChatEvent.MessageReceived -> {
                appendField(sb, "type", jsonString("messageReceived"), first = true)
                appendField(sb, "message", renderMessage(event.message))
            }

            is ChatEvent.MessageStatusChanged -> {
                appendField(sb, "type", jsonString("messageStatusChanged"), first = true)
                appendField(sb, "messageIdHex", jsonString(event.messageIdHex))
                appendField(sb, "status", jsonString(event.status.wireName))
                // failureReason is null except when status == "failed"
                // (CONTRACT.md section 4's per-type field table).
                val failureReason = (event.status as? ChatMessageDisplayStatus.Failed)?.reason
                appendField(sb, "failureReason", failureReason?.let { jsonString(it) } ?: JSON_NULL)
            }

            is ChatEvent.ClientFailed -> {
                appendField(sb, "type", jsonString("clientFailed"), first = true)
                appendField(sb, "reason", jsonString(event.reason))
            }
        }
        sb.append('}')
        return sb.toString()
    }

    private fun renderPeer(peer: ChatPeer): String {
        val sb = StringBuilder()
        sb.append('{')
        appendField(sb, "idHex", jsonString(peer.id.toHexString()), first = true)
        appendField(sb, "displayName", jsonString(peer.displayName))
        appendField(sb, "discoveredAtMs", peer.discoveredAtMs.toString())
        sb.append('}')
        return sb.toString()
    }

    private fun renderLinkBudget(budget: ChatLinkBudget): String {
        val sb = StringBuilder()
        sb.append('{')
        appendField(sb, "classification", jsonString(budget.classification.wireName), first = true)
        appendField(sb, "txLowerBoundBps", budget.txLowerBoundBps?.toString() ?: JSON_NULL)
        appendField(sb, "rxLowerBoundBps", budget.rxLowerBoundBps?.toString() ?: JSON_NULL)
        appendField(sb, "confidence", budget.confidence.toString())
        appendField(sb, "ageMs", budget.ageMs.toString())
        sb.append('}')
        return sb.toString()
    }

    private fun renderMessage(message: ChatMessage): String {
        val sb = StringBuilder()
        sb.append('{')
        appendField(sb, "idHex", jsonString(message.id.toHexString()), first = true)
        appendField(sb, "direction", jsonString(message.direction.wireName))
        appendField(sb, "body", jsonString(message.body))
        appendField(sb, "senderPeerIdHex", jsonString(message.senderPeerIdHex))
        appendField(sb, "sentAtWallClockMs", message.sentAtWallClockMs.toString())
        appendField(sb, "status", jsonString(message.status.wireName))
        sb.append('}')
        return sb.toString()
    }

    /**
     * Appends `"name": value` to [sb], matching CONTRACT.md section 4's pinned
     * formatting verbatim: `": "` after keys, `", "` between fields, no other
     * whitespace (see also the Swift `ChatTraceJSON` counterpart, which the
     * committed golden traces are generated from).
     */
    private fun appendField(sb: StringBuilder, name: String, valueJson: String, first: Boolean = false) {
        if (!first) sb.append(", ")
        sb.append('"').append(name).append('"').append(": ").append(valueJson)
    }

    /** Minimal JSON string escaping: the two mandatory escapes (`"`, `\`), the
     * common single-character escapes for control characters, and `\uXXXX` for
     * any other C0 control character. Non-ASCII Unicode (e.g. this codec's own
     * emoji/CJK golden vectors, if ever sent as a chat body) is emitted as raw
     * UTF-8, not escaped -- valid per RFC 8259 section 7, and JSON strings do not
     * require ASCII-only output. */
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
