# Optional Crypto Envelope — Cost / Security Tradeoff

Fresh as of 2026-06-10. Cyrinx's confidentiality/integrity layer (X25519 ECDH →
CTR/AEAD with a per-frame tag) is an **opt-in layer, OFF by default**, never a
hard dependency of the bulk PHY. This page quantifies what it costs so an
implementing app can choose plaintext-bulk vs authenticated-trickle **per the
channel quality it actually has** and its own threat model. See also
[SECURITY.md](../SECURITY.md) and [PUBLICATION.md](PUBLICATION.md) (PR 1.9).

## The model

- **One-time handshake** (per session): two 32-byte X25519 ephemeral public keys
  + a 16-byte confirmation tag = **80 bytes**.
- **Per-frame overhead**: a 12-byte nonce + a 16-byte authentication tag =
  **28 bytes** added to each frame's payload.

The per-frame tag as a fraction of goodput is just `28 / payload_bytes_per_frame`
— so it shrinks on fat frames and balloons on tiny ones. The handshake is a
fixed latency paid once.

## Per-MCS overhead (computed from the real codec geometry, n_sym = 64)

| MCS tier              | payload/frame | PHY goodput | per-frame tag | net goodput | handshake latency |
|-----------------------|--------------:|------------:|--------------:|------------:|------------------:|
| 16-QAM r3/4 (fast)    | 19 200 B      | 38.4 kbps   | **0.15 %**    | 38.3 kbps   | 17 ms             |
| 16-QAM r1/2 (medium)  | 12 800 B      | 25.6 kbps   | **0.22 %**    | 25.5 kbps   | 25 ms             |
| QPSK r1/2 (robust)    |  6 400 B      | 12.8 kbps   | **0.44 %**    | 12.7 kbps   | 50 ms             |
| BPSK r1/2 (floor)     |  3 072 B      |  6.1 kbps   | **0.91 %**    |  6.1 kbps   | 104 ms            |
| MT-FSK floor (267 bps, 32-B frames) | 32 B | 0.267 kbps | **87.5 %** | 0.033 kbps | **2.4 s** |

(Numbers from `scratch/hw20k/clib.py` geometry; the MT-FSK floor uses the
measured 267 bps and an illustrative 32-byte frame.)

## What this means for an implementing app

- **On the fast tiers, just turn it on.** At 38 kbps the tag is 0.15 % and the
  handshake is 17 ms — encryption is effectively free; there's rarely a reason
  to send the wideband bulk PHY in the clear.
- **At the non-coherent floor, think hard.** With tiny frames the 28-byte tag can
  *dominate* goodput (87.5 % in the illustrative case) and the handshake alone
  costs seconds. Options, in order of preference:
  1. **Batch larger floor frames** so the fixed tag amortizes (costs latency).
  2. **Authenticate, don't encrypt** the whole stream — a single signed session
     header + plaintext body, if integrity (not secrecy) is the requirement.
  3. **Skip the envelope** and rely on application-layer security, accepting that
     the acoustic medium is a broadcast anyone in earshot can receive.
- **The choice is per-session and dynamic.** The adaptive sounder (PR 1.4) already
  picks the MCS for the measured channel; the same decision point can flip the
  envelope policy: encrypt on a clean near-field link, fall back to
  authenticated-or-plaintext when the channel forces the slow tiers.

## Caveats

- The envelope has **not** undergone independent cryptographic review; treat it
  as experimental (see [SECURITY.md](../SECURITY.md)).
- Acoustic links are inherently broadcast — confidentiality, if required, must
  come from this layer or the application, never the medium.
