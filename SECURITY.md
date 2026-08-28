# Security Policy

Cyrinx is a research prototype. It is **not** a hardened, audited secure
communications product, and should not be relied on as one.

## Reporting a vulnerability

Please report security issues privately to **david@weekly.org** rather than
opening a public issue. Include a description, reproduction steps, and the
affected component (C core, Swift/Kotlin binding, or harness). We'll acknowledge
receipt and work with you on a disclosure timeline.

## Threat-model notes (important)

- The legacy Apple and Android HIL paths contain an optional X25519 plus custom
  XOR/HMAC envelope. It is **opt-in and OFF by default**, is not a standard AEAD
  construction, and is not integrated with the measured Cyrinx 2 bulk-PHY path.
  It does not authenticate peer identity or provide receive-side replay
  protection. Applications that need confidentiality or integrity must use a
  reviewed application-layer protocol. See
  [docs/CRYPTO_TRADEOFF.md](docs/CRYPTO_TRADEOFF.md) for the implementation and
  frame-space accounting record.
- Acoustic links are inherently **broadcast over the air**: anyone within
  earshot (or with a microphone) can receive the signal. Confidentiality, if
  required, must come from the application layer, not the medium.
- The crypto envelope has **not** undergone independent cryptographic review.
  Treat it as an interoperability prototype, not a security control.
