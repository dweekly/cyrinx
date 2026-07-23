package com.dweekly.cyrinx.chat.app.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import com.dweekly.cyrinx.chat.ChatPeer
import com.dweekly.cyrinx.chat.ChatTestTags
import com.dweekly.cyrinx.chat.toHexString

/**
 * "Peer browser → connect → one conversation" (design brief's product scope).
 * One row per discovered [ChatPeer] (already sorted by
 * [com.dweekly.cyrinx.chat.app.ChatProjection.upsertPeerSorted]), each with its
 * own [ChatTestTags.CONNECT_BUTTON].
 */
@Composable
fun PeerBrowser(peers: List<ChatPeer>, onConnect: (peerIdHex: String) -> Unit, modifier: Modifier = Modifier) {
    if (peers.isEmpty()) {
        Text(
            text = "Searching for nearby peers…",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = modifier.padding(16.dp).testTag(ChatTestTags.PEER_LIST),
        )
        return
    }
    LazyColumn(modifier = modifier.testTag(ChatTestTags.PEER_LIST)) {
        items(peers, key = { it.id.toHexString() }) { peer ->
            PeerRow(peer, onConnect = { onConnect(peer.id.toHexString()) })
        }
    }
}

@Composable
private fun PeerRow(peer: ChatPeer, onConnect: () -> Unit) {
    Card(
        modifier =
            Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 4.dp)
                .testTag(ChatTestTags.PEER_ROW)
                .semantics { contentDescription = "Peer ${peer.displayName}" },
    ) {
        Row(
            modifier = Modifier.fillMaxWidth().padding(16.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            // displayName only (cosmetic, per CONTRACT.md section 1.1) -- the
            // peer's idHex is passed to onConnect but never shown in the UI.
            Text(text = peer.displayName, style = MaterialTheme.typography.titleMedium)
            Button(
                onClick = onConnect,
                modifier = Modifier.testTag(ChatTestTags.CONNECT_BUTTON).semantics {
                    contentDescription = "Connect to ${peer.displayName}"
                },
            ) {
                Text("Connect")
            }
        }
    }
}
