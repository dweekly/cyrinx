# ADR 0005: Message-first public API and delivery evidence

- **Status:** Accepted for Cyrinx 3.0
- **Date:** 2026-07-23
- **Plan:** C3-01

## Context

An acoustic link has variable acquisition time, intermittent loss, route
changes, and bounded airtime. A byte-stream facade can conceal these properties
behind buffer growth and ambiguous completion. Cyrinx 2.x also uses “send
succeeded” for stages that can mean only local queueing or frame emission.

Cyrinx 3.0 needs a small public vocabulary that states ownership, progress, and
evidence without pretending to provide TCP semantics.

## Decision

The stable public model is message-first. An application submits one bounded
message and receives a transfer handle. Streams, if added later, are an adapter
over ordered messages and do not change the base contract.

The intended Swift call-site grammar is:

```swift
let transfer = try await connection.send(message, options: options)
```

This ADR freezes semantics and names, not the declarations that C3-24 will
implement.

### Stable vocabulary

| Type | Meaning and lifetime |
|---|---|
| `CyrinxTransport` | Persistent root coordinator for one local endpoint and at most one active run epoch; owns discovery, connection, transfer, and inbound-mailbox registries |
| `CyrinxTransport.Configuration` | Immutable endpoint configuration containing injected adapters, advertised resource bounds, discovery policy, and diagnostic policy; it does not contain protocol state |
| `CyrinxTransport.Event` | One composite, generation-tagged reducer commit containing all public deltas for that generation |
| `CyrinxPeer` | Immutable observation of a nearby endpoint, scoped to a transport epoch; not a stable hardware or authenticated identity |
| `CyrinxConnection` | Handle to one logical association and negotiated capabilities/profile state for a peer observation |
| `CyrinxTransfer` | Stable handle and immutable snapshots for one accepted outbound or observed inbound message |
| `LinkEstimate` | Timestamped/generation-tagged value snapshot of measured path evidence and its validity interval |
| `SendOptions` | Value describing priority, delivery mode, deadline, and permitted profile policy without embedding mutable session state |
| `SendOptions.DeliveryMode` | Either local-render best effort or remote-endpoint receipt required; it never implies cryptographic authentication |
| `SecurityStatus` | Snapshot value that reports authentication, confidentiality, and peer-identity evidence; Cyrinx 3.0 supports only the explicit unsecured value |
| `CyrinxError` | Structured machine-readable failure with a stable category, operation/stage context, retryability, and underlying C status when present |

`CyrinxPeer` identifiers are ephemeral and opaque. Cyrinx 3.0 does not claim
authenticated identity or confidentiality. A device signature, audio route, or
protocol receipt must not be presented as cryptographic identity.

The `Configuration`, `Event`, and `DeliveryMode` names above are semantic
replacement targets in the C3-01 inventory. C3-24 may choose concrete
declaration layout and argument labels, but cannot change their meaning.

### Acceptance, cancellation, and backpressure

`send` returning a transfer means the reducer accepted ownership of a bounded
copy or explicitly transferred buffer and assigned a transfer ID. It does not
mean samples were rendered or a peer received the message.

The connection advertises the effective message-size and queue bounds. An
oversized message, full queue, invalid state, unsupported delivery mode, or
expired pre-acceptance deadline throws a structured error before a transfer is
created. There is no silently growing queue and no transfer that begins in an
ambiguous “maybe queued” state.

Reducer admission is the linearization point against caller-task cancellation.
Cancellation observed first creates no transfer and throws cancellation.
Admission observed first transfers payload ownership and the caller receives
the transfer handle even if its task becomes cancelled before continuation
resumption. That cancellation is then an independently ordered request to
cancel the returned transfer. An accepted transfer is never orphaned behind a
cancellation throw.

### Progress and terminal evidence

The public transfer states and outcomes are defined in
[`CYRINX_3_SEMANTIC_CONTRACT.md`](../CYRINX_3_SEMANTIC_CONTRACT.md). In
particular:

- `accepted` means queue ownership only;
- `rendered` means the final local sample was handed to the audio sink;
- `bestEffortComplete` claims no peer receipt;
- outbound `receiptConfirmed` means a current-epoch protocol receipt reported
  remote endpoint-mailbox acceptance;
- inbound `received` means a complete message is stored in the bounded local
  endpoint mailbox and can be recovered independently of event delivery;
- `partial` is terminal diagnostic evidence, not a successfully delivered
  application message; and
- `failed` and `cancelled` are distinct terminal outcomes.

A receipt used by receipt-required mode is protocol evidence tied to a
transfer and session epoch. Its CRC detects accidental corruption; it is not
cryptographic integrity or authentication. The receipt does not prove a human
identity, device ownership, distance, or resistance to relay or replay.

The inbound mailbox, not an application event queue, owns completed payloads.
It does not evict an unclaimed payload after issuing its protocol receipt.
When the bounded mailbox is full, the receiver withholds a positive receipt
and terminates or retries according to the negotiated policy. Claiming a
payload through the facade is ordered atomically; after a successful claim,
the application owns an immutable value. Stopping the endpoint may discard
unclaimed in-memory messages, so a receipt is not a durable-storage claim.

### Events and snapshots

Applications observe immutable state changes. Events are ordered composite
deltas and may be dropped by a bounded facade queue; snapshots and the inbound
mailbox are authoritative. Every event and snapshot carries the reducer epoch
and generation so a facade can detect a gap and recover without inventing
transitions. Subscription setup atomically returns a baseline snapshot and an
event cursor; it cannot lose a commit between registration and snapshot read.

Transfer and connection handles remain usable for their terminal snapshot after
the core releases active resources. Retention bounds are documented by the
implementing facade; retaining a handle cannot retain an unbounded capture.

### Structured errors

Errors are matched by code, not localized description. The stable category set
must cover at least invalid argument, lifecycle/state, queue full, unsupported
capability, cancellation, deadline/timeout, route change, profile mismatch,
protocol violation, data-integrity failure, resource exhaustion, observation
loss, and internal failure.

An error records the operation and stage that failed and whether retry can be
attempted without rebuilding the connection. Human-readable text is diagnostic
and may be localized. C status and OS error values are nested evidence, not the
only public classification.

### Security status

Transport and connection snapshots expose `SecurityStatus`. The only valid
Cyrinx 3.0 value reports no authenticated identity, no confidentiality, and no
trusted peer key. There is no `SendOptions` switch that upgrades this claim.
Unknown or required nonzero security mechanisms fail negotiation explicitly;
they never fall back to unsecured operation after admission. Adding any
positive security value requires the separate cryptographic program, external
review, and a superseding semantic contract.

### 2.x mapping

| Cyrinx 2.x concept | 3.0 direction |
|---|---|
| `CyrinxSession` | `CyrinxTransport` plus explicit `CyrinxConnection` |
| `ReceivedMessage` | Inbound `CyrinxTransfer` with a mailbox-backed received message |
| `Config` | Transport configuration, profile registry, and `SendOptions` |
| `QoS` / `StreamFlags` | Explicit delivery mode and message options |
| `Gear` / rate strings | Negotiated immutable profile identity |
| `Metrics` / `ChannelMetrics` | `LinkEstimate` and versioned diagnostics |
| `Event` | Generation-tagged connection/transfer deltas and snapshots |

The old declarations follow their machine-readable inventory disposition and
remain available for migration as specified by ADR 0001.

## Consequences

- Applications can distinguish local admission, local rendering, best-effort
  completion, and receipt evidence.
- Backpressure is an API result rather than hidden memory growth.
- The API can add a stream adapter later without making the modem a byte stream.
- Identity and delivery claims stay within the evidence Cyrinx 3.0 actually
  provides.

## Rejected alternatives

- **A raw reliable byte stream:** rejected because it conceals message bounds,
  airtime, partial evidence, and route recovery.
- **Return `Void` after queueing:** rejected because the caller cannot track or
  cancel the accepted transfer.
- **Stable hardware-derived peer IDs:** rejected because they create privacy and
  authentication implications not supported by the 3.0 protocol.
