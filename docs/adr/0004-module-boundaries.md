# ADR 0004: Module boundaries and dependency direction

- **Status:** Accepted for Cyrinx 3.0
- **Date:** 2026-07-23
- **Plan:** C3-01

## Context

The current `Cyrinx` target contains public transport types, Apple audio
scaffolding, simulation, debug codecs, and Swift DSP. `CCyrinx` similarly
contains portable DSP and session code behind one public-header directory.
These are workable 2.x packaging choices, but they do not express the 3.0
ownership boundaries or prevent a platform binding from becoming a second
modem.

The architecture needs dependency rules before C3-03 and later PRs create or
move targets. Linker artifacts and source ownership do not have to be
one-to-one.

## Decision

The target architecture has these logical modules:

```text
Cyrinx application
        |
        v
Cyrinx compatibility umbrella
        |
        +------> CyrinxAppleAudio
        |              |
        v              v
                  CyrinxCore
                       |
                       v
                  CCyrinxCore
                       |
                       v
                   CCyrinxDSP

CyrinxSimulation -----> CyrinxCore / CCyrinxCore
CyrinxExperimental ---> CCyrinxDSP
Android binding ------> CCyrinxCore
```

An arrow means “may depend on.” A lower layer never imports or calls a higher
layer.

### `CCyrinxDSP`

Owns portable modulation, demodulation, synchronization, FEC, coded PHY-block
formation and validation evidence, FFT abstraction, channel estimation,
diversity calculation, and measurement primitives. It has no application
callbacks, file or network I/O, platform audio API, global clock, or session
policy.

### `CCyrinxCore`

Owns profiles, wire/control/message framing, fragmentation/reassembly,
streaming contexts, session reduction, connection and transfer state,
timer/effect requests, adaptation policy, snapshots, and diagnostic records.
It depends on `CCyrinxDSP`, not on Swift, Kotlin, Android, or Apple frameworks.

`CCyrinxDSP` and `CCyrinxCore` may ship as one native library. They remain
separate source and ownership boundaries even when the linker artifact is
combined.

### `CyrinxCore`

Owns platform-neutral Swift value types, memory-safe C marshaling, the actor
facade, async commands, immutable snapshots, and event streams. It contains no
audio-device routing and no independent DSP or protocol reducer.

### `CyrinxAppleAudio`

Owns Apple route discovery, session/category configuration, input/output units,
sample-format conversion, callback endpoints, interruption handling, and route
change effects. It depends on `CyrinxCore`. It does not interpret frames or
select profiles.

### Android binding

Owns JNI lifetime and marshaling, a serialized coroutine facade, `Flow`
publication, Android route/audio objects, and callback endpoints. It binds the
same `CCyrinxCore` library and does not depend on Swift or contain a shipping
Kotlin modem.

### `Cyrinx`

Is the compatibility umbrella imported by existing Swift applications. During
migration it re-exports the stable facade and the 2.x shims. It does not regain
ownership merely because a symbol remains available through the umbrella.

### `CyrinxSimulation`

Owns deterministic clocks, fault links, in-memory audio adapters, replay
drivers, and test fixtures. It may drive public core contracts but is not a
dependency of a production application target.

### `CyrinxExperimental`

Owns unqualified codecs, PHY stubs, raw debug surfaces, and research algorithms.
It has no semantic-version compatibility promise and is excluded from the
default production product.

### Host research tools

Python and command-line analysis tools consume versioned fixtures, traces, and
batch APIs. Production modules do not execute or import them.

## Boundary enforcement

- A public type is declared in the lowest module that owns its semantics.
- Generated Swift/Kotlin values come from one C registry or are verified
  against the same canonical fixture.
- Platform adapters depend on buffer/effect protocols, not concrete session
  internals.
- Simulation and experimental modules cannot be re-exported accidentally by
  the stable umbrella.
- Public C headers distinguish core and DSP responsibilities even if one
  installed include directory remains during migration.
- Circular imports and callback paths that synchronously call upward across a
  boundary are prohibited.

The exact SwiftPM target, Kotlin package, AAR, XCFramework, and installed-header
names are implemented in later plan items. Renaming a logical module does not
permit its responsibility or dependency direction to change without a
superseding ADR.

## Migration

The current `Cyrinx` and `CCyrinx` products remain buildable while code moves.
C3-03 through C3-07 introduce versioned boundaries; C3-35 owns final shim
handling. A move is not complete until the old public declaration has the
inventory disposition and compatibility behavior recorded in
[`API_INVENTORY.md`](../API_INVENTORY.md).

The deprecated 2.x `TransportBackend.inMemory` implementation is a temporary,
quarantined compatibility exception through C3-35. It cannot be selected by
the new production core and cannot make `CyrinxSimulation` a production
dependency. New in-memory adapters are declared and implemented only in
`CyrinxSimulation`.

## Consequences

- The same portable core can back Apple, Android, simulation, and host replay.
- Platform concerns remain testable without granting them protocol authority.
- Experimental code can remain available without expanding the stable SDK.
- Packaging may be staged independently from logical ownership.

## Rejected alternatives

- **One undifferentiated target:** rejected because import visibility cannot
  enforce production, simulation, and experimental boundaries.
- **A Swift-owned core with a JNI port:** rejected because it recreates
  independent shipping protocol implementations.
- **A production dependency on simulation:** rejected because fault injection
  and deterministic clocks must remain optional tooling.
