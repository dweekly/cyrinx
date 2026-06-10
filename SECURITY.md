# Security Policy

Cyrinx is a research prototype. It is **not** a hardened, audited secure
communications product, and should not be relied on as one.

## Reporting a vulnerability

Please report security issues privately to **david@weekly.org** rather than
opening a public issue. Include a description, reproduction steps, and the
affected component (C core, Swift/Kotlin binding, or harness). We'll acknowledge
receipt and work with you on a disclosure timeline.

## Threat-model notes (important)

- The optional crypto envelope (X25519 ECDH + CTR/HMAC) is **opt-in and OFF by
  default**. The PHY does not encrypt or authenticate by default. An application
  that needs confidentiality or integrity must explicitly enable the envelope
  and accept its throughput cost — which is near-free on fast MCS tiers but can
  dominate goodput on the slow fallback tiers (see docs/PUBLICATION.md PR 1.9
  for the cost/security tradeoff).
- Acoustic links are inherently **broadcast over the air**: anyone within
  earshot (or with a microphone) can receive the signal. Confidentiality, if
  required, must come from the application layer, not the medium.
- The crypto envelope has **not** undergone independent cryptographic review.
  Treat it as experimental.
