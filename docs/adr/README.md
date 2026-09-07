# Cyrinx architecture decisions

These records freeze the Cyrinx 3.0 architectural contract. A later pull request
may refine an implementation detail, but changing one of these decisions
requires a superseding ADR and an explicit compatibility impact.

| ADR | Decision |
|---|---|
| [0001](0001-canonical-ownership-and-versioning.md) | Canonical ownership and independent version axes |
| [0002](0002-executor-and-thread-ownership.md) | Executor, thread, effect, and queue ownership |
| [0003](0003-processing-boundaries.md) | On-device production processing and host-assisted tooling |
| [0004](0004-module-boundaries.md) | Module responsibilities and dependency direction |
| [0005](0005-message-first-api.md) | Message-first public vocabulary and delivery evidence |
| [0006](0006-session-initiation.md) | Staged, cost-bounded session initiation and negotiated posture |

The normative lifecycle, connection, transfer, event-generation, snapshot, and
error rules shared by these ADRs are in the
[Cyrinx 3.0 semantic contract](../CYRINX_3_SEMANTIC_CONTRACT.md).
