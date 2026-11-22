# Plan: scrambled profiles for payload-independent decoding

Status: **proposed, awaiting David's approval. No codec change has been made.**
Stage: product. Written 2026-11-22.

## In plain English

Today a Cyrinx frame can fail to decode because of what it carries. A message
that is mostly zero bytes, or mostly any one repeated value, turns into a sound
with a few very tall spikes. The transmitter trims tall spikes to protect the
speaker, and for these messages the trimmed part was the message. Nothing
decodes, even with no microphone or room involved.

The usual cure is a scrambler: the sender mixes the message with a fixed
pseudorandom pattern before turning it into sound, and the receiver removes the
same pattern afterwards. The sound then looks the same whatever the message
is. Because this changes the sound on the wire, a receiver built before the
change cannot decode it. This plan adds scrambled formats beside the existing
ones and leaves the existing ones exactly as released.

## Why

[NEGATIVE_FINDINGS entry 26](NEGATIVE_FINDINGS.md) records the defect and its
measured cause. In digital loopback on all five registered profiles:

- an all-zero frame, a 128-byte record padded with zeros, and a frame with
  eight data bytes per otherwise-zero block each recover 0 of 560 blocks;
- a zero payload puts about 95% of data bins on one constellation point and
  has a peak-to-RMS ratio of 26 to 28, against about 4.6 for random data;
- with the transmitter's clip switched off, every pattern recovers every block
  through the unchanged receiver, so the clip is the cause; and
- XOR with a fixed pseudorandom mask before encoding restores 560 of 560, and
  took a Moto G (2026) from 0 of 2 to 2 of 2 records over the air.

`cyrinx_batch_encode` zero-pads a short payload as its documented contract, so
the documented way to send a short message loses it. A reader could serve the
same need another way, for example by lowering the peak with a different
technique. Scrambling is chosen because it is the measured fix, it costs no
airtime, and it is the standard remedy.

## Decisions already made

- **New profiles alongside** (David, 2026-11-22). Profiles 1 to 5 keep their
  waveforms, IDs and identity digests. Scrambled formats get new IDs.
- **Approval before any codec change** (David, 2026-11-22, by choosing the
  scope "Scrambler Then Gates", whose terms were plan first).

## Proposed design

Everything in this section is proposed by an agent and not yet confirmed.

1. **Where the scrambler sits.** After the payload blocks and their CRCs are
   laid out as bits, and before convolutional coding, XOR the information bits
   with a fixed pseudorandom sequence from the existing deterministic bit
   generator, using a new seed. The receiver XORs the decoded bits with the
   same sequence before checking CRCs. The CRC still covers the caller's
   bytes, so every existing meaning of "block valid" is unchanged.
2. **How a profile says it is scrambled.** Append one fixed-width field,
   `payload_scrambler` (0 none, 1 the sequence above), to the profile
   structure as a v2 layout of 136 bytes. The v1 prefix is unchanged, as the
   ABI rules require. A caller that passes a v1-sized structure and asks for a
   scrambled profile is refused with no output, because it could not represent
   what it was given.
3. **Identity.** Profiles with `payload_scrambler` 0 keep the v1 identity
   serialization, so their digests do not move. Scrambled profiles use a v2
   serialization with its own domain string and the new field appended.
4. **Registry.** Add profiles 6 to 10, each a copy of 1 to 5 with the scrambler
   on, classified experimental until measured over the air. The registry
   source JSON gains them and the existing field-for-field parity test covers
   them.
5. **2.x symbols.** `cyrinx_bulk_config` and the 2.x entry points keep their
   layout and behavior. The scrambler flag reaches the modulator and
   demodulator through an internal entry point that the batch layer calls.
6. **Short payloads.** The C contract keeps zero-padding. On a scrambled
   profile the padding is harmless, so `BatchPHY.encode` accepts a short
   payload there and keeps refusing one on profiles 1 to 5.
7. **Oracle.** The Python reference modem gains the same scrambler, and the
   golden-vector generator emits vectors for one scrambled profile, so the C
   implementation is checked against an independent one as today.

Known limit of the design: a payload equal to the scrambler sequence is
turned back into zeros. A fixed additive scrambler always has such an input.
Varying the seed per frame would remove it and is left out of this plan.

## Graduation gates

Each is a command that passes or fails.

- `swift test --filter CyrinxProfileRegistryTests` passes with profiles 1 to 5
  byte-identical in identity and ten profiles registered.
- `./scripts/test-accelerate.sh` and `swift test` pass with the existing
  golden vectors unchanged and the new scrambled vectors matching on both FFT
  backends.
- `.venv/bin/python scratch/garage/payload_whitening.py --report <path>`,
  extended to the scrambled profiles, reports 560 of 560 for the zero, short
  and sparse patterns with no whitening applied by the caller.
- `.venv/bin/python scratch/garage/zero_payload_mechanism.py --report <path>`,
  extended likewise, reports a peak-to-RMS ratio for the zero payload within
  20% of the random payload's on every scrambled profile.
- `.venv/bin/python scripts/check-batch-replay.py --android <pixel> --android <moto>`
  passes with at least one scrambled fixture in the list, on host and both
  phones.
- `./scripts/check.sh` passes, including the API inventory with the new
  symbols classified.

A scrambled profile is promoted from experimental only after an over-the-air
repeat of the sparse-record broadcast shows the plain record recovered on both
phones.

## Out of scope

- Any change to profiles 1 to 5, to the clip, or to the 2.x API.
- Other ways of lowering the peak, per-frame seeds, and encryption.
- Choosing a scrambled profile as anyone's default.
- Streaming, discovery and negotiation of profiles between devices.

## Questions for David

1. Approve this design, or change it? The two choices most worth a second look
   are the v2 profile layout (item 2) and accepting short payloads on scrambled
   profiles (item 6).
2. Should the per-frame seed stay out, given the known limit above?
