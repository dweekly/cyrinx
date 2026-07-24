# ADR 0003: On-device production processing and host-assisted tooling

- **Status:** Accepted for Cyrinx 3.0
- **Date:** 2026-07-23
- **Plan:** C3-01

## Context

Current hardware campaigns can capture PCM on a phone and decode it later on a
Mac. That is valuable experimental evidence, but it measures a capture and a
decoder rather than an integrated mobile transport. The term “host DSP” is also
ambiguous: a Mac can be the actual acoustic endpoint, or it can be a second
computer assisting a phone endpoint.

Cyrinx needs the independent replay path for regression tests, failure
analysis, and numerical comparison. It must not let that path become an
unreported production dependency.

## Decision

### Production endpoint

The device that owns the active microphone or speaker route executes the full
latency-critical Cyrinx runtime for that endpoint:

- streaming synchronization and PHY processing;
- framing, FEC, block validation, and reassembly;
- session, connection, transfer, retry, and event reduction;
- profile and receiver selection; and
- application payload dispatch.

A Mac using its own audio route is on-device. A phone exporting PCM over ADB,
USB, a network socket, or a file for another computer to decode is
host-assisted and is not an accepted Cyrinx 3.0 production path.

Production may use platform accelerators available on the endpoint, including
Accelerate, vDSP, NEON, or hardware audio conversion, when the portable C
contract and parity gates permit them. “On-device” does not require one CPU
core or forbid a local accelerator.

### Host-assisted facilities

Host batch encode/decode, replay, simulation, Python oracles, waveform
inspection, and campaign analysis remain first-class engineering facilities.
They may:

- generate and verify golden vectors;
- replay captured PCM deterministically;
- compare C, Swift, Kotlin/JNI, and research algorithms;
- inspect failure evidence that a phone retained; and
- perform offline research that is not linked into the SDK.

They may not be required for a shipping send, receive, discovery, negotiation,
or recovery operation. A test that passes only after exporting PCM is labeled a
replay result, not an on-device transfer.

### DSP block contract

Production streaming and host replay use the same canonical C operators and
profiles. Operators accept explicit configuration, state, workspace, sample
position, discontinuity, and input/output views. They do not read wall clocks,
files, global mutable policy, or device APIs.

Streaming contexts accept arbitrary positive chunk lengths. They may retain
bounded history required for resampling, overlap, synchronization, channel
estimation, or frame assembly. The bound and reset behavior are part of the
operator contract. A discontinuity invalidates any history that cannot safely
cross the missing sample range.

Batch entry points are adapters over the same canonical behavior. Batch and
streaming paths must produce equivalent ordered validity and payload evidence
within declared numerical tolerances; neither may use expected payload bytes,
future CRC results, or same-frame payload decisions to select a receiver.

### Performance and evidence

C3-01 does not assign unsupported fixed CPU percentages or heap sizes. The
streaming RX/TX work sets measured real-time gates in C3-09 and C3-10.
Platform adapters establish device-specific latency, underrun, CPU, memory, and
energy evidence in C3-14 through C3-16.

Every production queue and workspace is bounded before integration. A profile
cannot become default merely because host batch decode is fast or successful.

### Capture and support data

Raw PCM is not uploaded or exported by default. Diagnostics state whether they
contain raw or derived audio evidence, their retention bound, and the user or
test action that enabled collection. Support tooling consumes versioned
artifacts and does not silently turn a production session into host-assisted
processing.

## Consequences

- The SDK can make a defensible real-time endpoint claim.
- Offline replay remains available as an independent referee and debugging
  tool.
- Streaming and batch implementations cannot diverge into separate shipping
  modems.
- Device qualification must exercise the endpoint-local path.

## Rejected alternatives

- **Remove host replay:** rejected because it would weaken reproducibility and
  failure analysis.
- **Treat ADB decode as an Android runtime:** rejected because availability,
  latency, lifecycle, and deployment differ from an application-local SDK.
- **Forbid internal rebuffering:** rejected because a bounded streaming
  synchronizer must preserve history across arbitrary callback boundaries.
