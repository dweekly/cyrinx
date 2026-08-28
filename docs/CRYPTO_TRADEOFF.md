# Experimental Crypto Envelope — Implementation and Cost Boundary

Fresh as of 2026-08-27. Status: **documentation quarantine**. The legacy Apple
and Android HIL paths contain an opt-in confidentiality/integrity prototype,
off by default. It is not a standard authenticated-encryption construction,
has not undergone independent review, and must not be presented as a secure
product feature. It is also not integrated with the measured Cyrinx 2 bulk-PHY
path.

See [SECURITY.md](../SECURITY.md) for the supported security posture.

## Implementation record

The Swift and Kotlin implementations currently perform:

1. X25519 key agreement after the peer public key is supplied;
2. HKDF-SHA256 expansion into separate 32-byte encryption and MAC keys;
3. a custom XOR stream generated as
   `SHA256(encryption_key || sequence_u64_be || counter_u32_be)`; and
4. HMAC-SHA256 over `sequence || ciphertext`, truncated to eight bytes.

Despite the internal `encryptCTR` function name, step 3 is not AES-CTR. The
combined construction is not a standard AEAD mode.

The encoded frame envelope is:

```text
8-byte sequence || ciphertext || 8-byte truncated HMAC tag
```

It therefore consumes 16 bytes per protected frame, not 28. The code exchanges
32-byte X25519 public keys through existing session fields, but no explicit
key-confirmation message is implemented. Handshake airtime is transport- and
schedule-dependent and is not quantified here.

## Security limitations

- Public keys are not authenticated or bound to a trusted identity. An active
  attacker can substitute keys during exchange.
- The receiver verifies the truncated tag but does not maintain a receive-side
  replay window for envelope sequence numbers.
- The construction has no external security analysis or standard test-vector
  conformance suite.
- Existing tests demonstrate successful in-memory key exchange and round-trip
  operation. They do not establish tamper, replay, active-attacker,
  cross-language, side-channel, or protocol-composition security.
- A 64-bit truncated tag has a different forgery bound from a full-length tag;
  no attempt budget or rekey policy is specified.

These limitations are product-blocking for a security claim. Applications
that require confidentiality, peer authentication, or replay protection must
use a reviewed application-layer protocol rather than relying on this
prototype.

## Frame-space cost only

The following table corrects the prior frame-overhead arithmetic. It estimates
only the loss of application payload space when 16 envelope bytes occupy a PHY
payload. It excludes public-key exchange, retransmission, session setup, and
all security-processing time.

| Illustrative tier | Application payload | Nominal PHY goodput | Envelope / payload | Approx. application goodput |
|---|---:|---:|---:|---:|
| 16-QAM r3/4 | 19,200 B | 38.4 kbps | 0.083% | 38.368 kbps |
| 16-QAM r1/2 | 12,800 B | 25.6 kbps | 0.125% | 25.568 kbps |
| QPSK r1/2 | 6,400 B | 12.8 kbps | 0.250% | 12.768 kbps |
| BPSK r1/2 | 3,072 B | 6.1 kbps | 0.521% | 6.068 kbps |
| 267 bps floor with 32 B application payload | 32 B | 0.267 kbps | 50.0% | 0.178 kbps |

The percentages use `16 / application_payload_bytes`. Approximate application
goodput uses
`nominal_goodput * application_payload_bytes / (application_payload_bytes + 16)`.
The table is capacity accounting, not a recommendation to enable the prototype.

## Replacement boundary

Selecting a standard construction, authenticating peer identity, defining
nonces and replay behavior, adding cross-language vectors, and obtaining
independent review are separate security work. Until that program completes,
the public security status remains unauthenticated and the prototype remains
off by default.
