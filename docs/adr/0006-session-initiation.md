# ADR 0006: Staged, cost-bounded session initiation

- **Status:** Accepted for Cyrinx 3.0
- **Date:** 2026-09-06
- **Plan:** C3-18, C3-20a, C3-20b, C3-21, C3-23

## In plain English

Before two devices can talk over sound at full speed, they have to work out who
they are talking to, which version of the protocol they both speak, whether
anything is encrypted, and how good the acoustic path between them actually is.
That last part means each device listens to itself, listens to the room, and
then the two take turns making test noises at each other.

All of that is worth doing, and it is allowed to be slow — but not
unconditionally. Measured from numbers already in this repository, a full
two-way characterization costs about 30 seconds, which is twice the entire
budget we have set for delivering a 4 KiB message.

So it is a ladder rather than a gate. Two devices first establish only that the
other is there and can exchange small messages, which is quick. Ordinary traffic
then keeps refining what each side knows about the path, for free, as it flows.
The expensive listening happens when someone actually asks for it — normally
because a large transfer is about to start and the setup cost will be earned
back.

## Context

Session initiation establishes several things at once: protocol version, roles,
peer capability, security posture, and the quality of each directed link. It is
reasonable for this to be heavier than sending a message, because the result is
reused for everything that follows.

Two constraints bound how heavy it may be.

**Initiation competes with the payload.** Costed from constants already in the
repository — `freqresp.SWEEP_DUR` at 6.0 s per exponential sine sweep, roughly
2 s of room tone, the 3.0 s speaker-settle preroll a phone transmitter needs
(`scratch/hw20k/NOTES.md`, A5), and one 4.0 s conservative probe frame — a full
mutual sounding is about **30 s** before any turnaround or retry. Against the
deadlines in the garage plan that is 200% of the 15 s budget for a 4 KiB message
and 33% of the 90 s budget for 64 KiB. A monolithic heavyweight initiation makes
small messages impossible while making bulk transfers better.

**Security negotiation exists, but 3.0's accepted set is baseline only.**
`SecurityStatus` carries separate authentication, confidentiality, and
peer-identity claims, and §10 of the
[semantic contract](../CYRINX_3_SEMANTIC_CONTRACT.md) is normative for what
those may hold. This ADR does not restate those rules; it records only that
security posture is settled during initiation like every other negotiated
property, and that in 3.0 the negotiation has exactly one acceptable outcome.

## Decision

**Initiation is a staged sequence, not one heavyweight exchange.** Three phases,
and only the last needs the peer:

1. **Self-calibration** — local, no peer. Each endpoint measures its own speaker
   against its own microphone: response, clipping, nonlinear products, route
   identity, safe gain envelope. This is why capability is measured rather than
   declared — the same phone has different real capability with and without its
   vendor audio effects enabled (`NOTES.md`, A5). Owned by C3-20b.
2. **Environmental sampling** — local, passive. Room tone and noise floor at the
   session's fixed gain. Not optional bookkeeping: a decay measurement needs a
   noise-only reference at the same gain to know where its curve stops being
   signal, so this is captured per position and per direction.
3. **Mutual characterization** — turn-taking, needs the peer. Each endpoint
   probes in turn while the other measures, producing one estimate per *directed*
   link. Owned by C3-21.

**Contention is a pre-association problem.** C3-18 already places
listen-before-talk and randomized bounded slots at discovery, where an unknown
number of advertisers may transmit. An associated pair has a turn to hand over,
not a channel to contend for, so an established two-node link uses explicit
half-duplex turn handover rather than continuous carrier-sense arbitration.

**Roles are elected, never assumed, and capability is per endpoint.** C3-18's
merge gate is that two symmetric peers converge on complementary roles. No
transport property may be derived from a device class or from a role name. In
particular the occupied band of a directed link is the intersection of the
transmitting endpoint's emission capability and the receiving endpoint's capture
capability: a laptop pair is wide in both directions, a phone pair narrow in
both, and a mixed pair asymmetric in whichever direction the measurements say.
An uncharacterized endpoint resolves to the most conservative known band.

**Initiation cost is bounded by what it enables, and escalates on demand.** A
pair must reach a usable conservative bearer without completing phase 3.
Characterization is a ladder, and each rung is entered because of the question
being asked, not merely because of payload size:

| Tier | What it costs | What it can answer | Owner |
|---|---|---|---|
| 0 — association | one beacon exchange | is there a peer, which version, which capabilities, what security posture | C3-18 |
| 1 — data-aided tracking | free, continuous | phase, timing drift, and per-symbol reliability *within the band already occupied* | receiver, extended by plan stage 3 |
| 2 — in-band probe | ~4 s, one conservative frame | delay spread and per-bin quality within the occupied band | C3-21 |
| 3 — out-of-band sweep | ~30 s mutual | whether a *different* band or guard would be better | C3-20a/C3-21 |

Small messages ride tier 0 immediately. Either endpoint may request escalation,
and a large transfer is the ordinary reason to ask for tier 3; the request and
its outcome are observable negotiated transitions, not an implicit policy
choice. An implementation may not make a small message wait on a tier it did not
need.

**Tier 1 is not hypothetical and its limit is the reason tier 3 exists.** The
receiver already fits phase slope and common phase error from known pilots on
every data symbol (`cyrinx_fit_pilot_phase` in `cyrinx_bulk.c`, with odd pilot
ordinals held out for scoring), so ordinary traffic already refines the channel
estimate as it flows. What it cannot do is say anything about subcarriers the
transmitter is not currently using: an estimate derived from occupied bins is
silent about whether widening from 11 kHz to 14 kHz would help. Because band
width is the dominant throughput lever on an asymmetric pair, that question is
exactly the one worth 30 s — and it is the only question that genuinely requires
transmitting outside current occupancy.

**Version and security posture are settled at association.** Both are negotiated
properties of the connection, carried in the beacon's capability hash and
resolved before message admission. In 3.0 the accepted security outcome is the
baseline value alone, and a stronger or unknown requirement fails establishment
rather than being negotiated down — the fail-closed direction is deliberate, and
§10 of the semantic contract owns the exact rules.

## Consequences

- The fast path to first byte does not include a sweep. C3-19 delivers
  best-effort messages on a conservative profile before C3-21 sounding exists,
  and that ordering is now a contract rather than an artifact of the plan's
  sequence.
- Any adaptive policy must account for probe and activation cost in the session
  time it reports, not only in the profile it selects.
- A "negotiate encryption" flow is present and exercised in 3.0 with an empty
  set of stronger outcomes. Adding a mechanism later changes the accepted set
  and requires the superseding contract §10 already demands; it does not require
  inventing a negotiation slot.
- Endpoint capability tables written before C3-20b exists are stand-ins. They
  must cite their bench measurement and be replaced by self-characterization,
  not promoted to assumptions.

## Alternatives considered

**Monolithic heavyweight initiation.** Simpler to reason about, and it makes the
bulk case optimal. Rejected on the measured cost: 30 s of setup cannot precede a
message with a 15 s deadline.

**Replacing explicit sounding entirely with data-aided estimation.** Attractive,
and partly what tier 1 already is, but it cannot answer the band-widening
question above. Worth noting that 802.11 went the other way for its own reasons:
802.11n defined both staggered sounding, embedded in ordinary data preambles,
and dedicated null data packets, and 802.11ac then dropped staggered sounding in
favour of NDP alone — for MU-MIMO precoder accuracy, immediate SIFS feedback,
and eliminating interoperability variants. None of those three motives apply
here: Cyrinx has no MU-MIMO, no SIFS-scale turnaround, and no third-party
implementations to interoperate with. So the trajectory is informative but not
binding, and tier 1 deserves more weight here than Wi-Fi's history would
suggest.

**CSMA on the established link.** Carrier sense earns its complexity when an
unknown number of transmitters contend. A two-node associated pair has a
deterministic turn to hand over, so this would be machinery for a problem that
is not present. Listen-before-talk is retained where contention is real, at
discovery, and as courtesy against third-party audio.

**Frequency-division duplex using the asymmetric bands.** Tempting when two
endpoints have disjoint usable bands, since it would allow simultaneous
bidirectional transfer. Rejected for 3.0: an endpoint's own speaker sits
centimetres from its own microphone, so near-end echo dominates, and full-duplex
echo cancellation is already out of scope. Nonlinear products between the two
bands would also need measuring — `scratch/ultra_intermod_safety.py` is the
existing probe for that question.
