# Plan: scrambled profiles for payload-independent decoding

Status: **proposed, awaiting David's approval. No codec change has been made.**
Stage: product. Written 2026-11-22; revised the same day after one plan review.

## In plain English

Today a Cyrinx frame can fail to decode because of what it carries. Some
messages, such as one that is mostly zero bytes, turn into a sound with a few
very tall spikes. The transmitter trims tall spikes to protect the speaker,
and for these messages the trimmed part was the message. Nothing decodes, even
with no microphone or room involved.

The usual cure is a scrambler: the sender mixes the message with a fixed
pseudorandom pattern before turning it into sound, and the receiver removes the
same pattern afterwards. The sound then looks much the same whatever the
message is. Because this changes the sound on the wire, a receiver built
before the change cannot decode it. This plan adds scrambled formats beside
the existing ones and leaves the existing ones exactly as released.

## Why

[NEGATIVE_FINDINGS entry 26](NEGATIVE_FINDINGS.md) records the defect and its
measured cause. In digital loopback on all five registered profiles:

- an all-zero frame, a 128-byte record padded with zeros, and a frame with
  eight data bytes per otherwise-zero block each recover 0 of 560 blocks;
- a zero payload puts about 95% of data bins on one constellation point and
  has a peak-to-RMS ratio of 26 to 28, against about 4.6 for random data;
- with the transmitter's clip switched off, every pattern recovers every block
  through the unchanged receiver, so the clip is the cause;
- not every constant input fails: a repeated 0x71 byte recovers 560 of 560,
  and a repeated 0xff byte recovers every block on profiles 1 and 2 and none
  on 3 to 5; and
- XOR of the application bytes with a fixed pseudorandom mask before encoding
  restores 560 of 560, and on a 19-block QPSK research geometry took a Moto G
  (2026) from 0 of 2 to 2 of 2 records over the air.

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

1. **The exact bit stream.** `payload_scrambler = 1` means: XOR exactly
   `block_count * 260 * 8` framed bits, which are each block's 256 payload
   bytes followed by its four big-endian CRC bytes, unpacked most significant
   bit first. The mask starts at bit zero of the frame, runs continuously
   across CRCs and block boundaries, and resets once per frame. The existing
   information padding (seed 7), the six convolutional termination bits and
   the capacity fill (seed 8) are unchanged. The mask comes from the existing
   deterministic generator, one high bit per SplitMix64 output, with a literal
   64-bit seed published in the header. The receiver applies the same mask to
   the decoded bits before checking CRCs. A pinned vector that crosses the
   first CRC into the second block fixes the seed, the extraction rule and the
   reset convention.
2. **A separately named v2 profile type.** The v1 structure stays 128 bytes and
   its operations stay as they are. A v2 structure adds a 32-bit
   `payload_scrambler` at offset 128 and four reserved zero bytes, 136 bytes
   in all on every target, with its own lookup, validation and identity
   functions. A v1 lookup of a scrambled ID returns
   `CYRINX_STATUS_ERR_NOT_FOUND` with no output, since the v1 view does not
   contain it, and an unsupported scrambler value fails validation with
   `CYRINX_STATUS_ERR_INVALID_ARGUMENT`. Swift and JNI map those two statuses
   as they do today. Tests cover a true
   128-byte allocation, an oversized v1 buffer with a sentinel tail, exact,
   oversized and undersized v2, an unknown version and an unsupported
   scrambler value, with every rejection leaving outputs untouched.
3. **Enumeration does not change for existing callers.**
   `cyrinx_profile_get_count` and `cyrinx_profile_get_by_index` keep returning
   the five v1 profiles, so `BatchPHY.Profile.registered()` and the JNI
   enumeration keep working unchanged. A new version-aware enumeration returns
   all ten. One registry table backs both. A consumer compiled against the old
   header is tested enumerating five entries against the new library.
4. **Identity.** The registry source records an identity scheme per row.
   Profiles 1 to 5 keep the v1 serialization and their digests. Scrambled
   profiles use a v2 serialization with its own domain string and the new
   field. The v1 serializer refuses a scrambled profile, so no caller can
   obtain its parent's identity by mistake. One full v2 byte string and its
   independently computed digest are pinned, and a test shows that changing
   the mode invalidates the digest.
5. **Registry.** Profiles 6 to 10 are copies of 1 to 5 with the scrambler on,
   classified experimental.
6. **2.x symbols.** `cyrinx_bulk_config` and the 2.x entry points keep their
   layout and behavior. The scrambler mode reaches the modulator and
   demodulator through an internal entry point that the batch layer calls.
7. **Short payloads.** The C contract keeps zero-padding. On a scrambled
   profile `BatchPHY.encode` accepts 0 to `payloadByteCount` bytes, exposes
   the scrambler mode, and returns the same waveform as explicit zero padding.
   Decode still returns a full padded frame; the application keeps the
   original length. Profiles 1 to 5 keep refusing a short payload. Tests cover
   lengths 0, 1, 128, 255, 256, 257, capacity minus one, capacity and
   oversize, including the empty input that C rejects today for its null
   pointer.
8. **Oracle.** The Python reference modem gains the same scrambler. One new
   golden case is committed: four symbols and the existing long cyclic
   prefix, like the present cases, so it stays small and survives the
   generator's echo channel, with a sparse payload so it also proves the
   peak is gone. It is an internal test-fixture profile with a pinned v2
   identity, and its record states the scrambler mode. The golden tests
   compare transmit with the independent Python waveform and decode
   Python-made audio, through the scrambled path, on both FFT backends. A
   full registered profile is not committed as a vector: its files would run
   to several megabytes, and profiles 7 to 10 have cyclic prefixes shorter
   than the generator's echoes.
9. **Call sites that assume five v1 profiles.** `BatchPHY.Profile` requests
   the v2 layout and exposes the scrambler mode; the JNI probe does the same.
   Tests that assert exactly five registered profiles keep asserting it for
   the v1 view and gain the ten-profile check. The API inventory and its
   documented counts are regenerated.

## Graduation gates

Each is a command that passes or fails. Diagnostics exit non-zero on a miss.

- **Released waveforms unchanged.** Before the codec is touched, capture a
  deterministic transmit reference for each of IDs 1 to 5 on one fixed
  backend. After the change the same command produces identical PCM, and the
  four existing golden cases still pass within their existing tolerance on
  both backends.
- `swift test --filter CyrinxProfileRegistryTests` passes with five v1
  profiles byte-identical, ten in the version-aware view, and the ABI cases in
  design item 2.
- `swift test` and `./scripts/test-accelerate.sh` pass with the scrambled
  golden case added to the Accelerate filter.
- A recovery diagnostic drives real registry lookup and batch encode and
  decode. For each of IDs 6 to 10 and each pattern (zero, a true 128-byte
  input, sparse, 0x71, 0xff, dense random) it requires the expected ordered
  bytes and mask and no CRC-valid block with wrong bytes. The legacy profiles
  run beside them as controls.
- A peak diagnostic measures the pre-clip waveform through the same scrambled
  transmit path on each of IDs 6 to 10. For the zero, short, sparse and 0xff
  patterns it requires a peak-to-RMS ratio of at most 6.5 and a
  commonest-point share within 0.02 of the random payload's. A single
  frame's peak is an extreme value, so a tight ratio between two frames would
  fail by chance; the unscrambled figures are 26 to 28 and about 0.95.
- Wrong-mode and partial recovery: a clean varied frame cross-decoded in both
  directions between a profile and its scrambled twin yields the expected
  masks, and a scrambled fixture with an invalid block followed by a valid
  one recovers the later block at its original offset.
- `scripts/check-batch-replay.py` passes with a scrambled fixture looked up by
  its registered ID in all three adapters, in the checkout-only lane and on
  both phones.
- `./scripts/check.sh` passes, including the API inventory with the new
  symbols classified.

**Over the air.** The earlier broadcast used a different geometry, whitened
before the CRC, and a research receiver, so repeating it cannot qualify any of
IDs 6 to 10. The screen for this plan names one candidate ID and digest, sends
a 128-byte record through its batch encoder, decodes both phones' captures
through its batch receiver, and requires a valid first block holding the
original bytes on each phone, with dense controls and the paired legacy
transmission kept. That is a screen. Changing a profile's classification
follows the ROADMAP's qualification criteria.

## Out of scope

- Any change to profiles 1 to 5, to the clip, or to the 2.x API.
- Other ways of lowering the peak, per-frame seeds, and encryption.
- Choosing a scrambled profile as anyone's default.
- Streaming, discovery and negotiation of profiles between devices. The batch
  decoder is told its profile by the caller; the digest it returns does not
  show which mode the transmitter used.

## Known limits, for the roadmap

- **Worst-case payloads.** The claim is recovery of the listed patterns. A
  payload equal to the mask's payload positions cancels it there, leaving
  only the CRC bits varied, about as sparse as an unscrambled zero payload,
  so it is expected to fail the same way. One characterization case should
  record that. On a link with no authentication a sender can craft such a
  frame, and a retransmission of an unchanged frame repeats the same peaks.
  A different seed per frame only moves the bad inputs and needs a way for
  the receiver to learn the seed, which this one-frame decoder does not have.
  Seed selection and retry behavior belong with the session and transport
  work; encrypted payloads are already pseudorandom.
- **No length on the wire.** Decode returns the full padded frame. A receiver
  cannot tell trailing zero bytes of a message from padding, and a short
  message still occupies a whole frame of airtime, until the message envelope
  and shorter burst geometries exist.
- **Twice the profiles.** Future geometries should be registered scrambled
  only, and automatic selection should offer only scrambled profiles, so
  sounding and negotiation do not carry both.
- **The on-device apps.** The Android and iOS hardware-in-the-loop apps still
  run their own demodulators and will not decode IDs 6 to 10 until they move
  to the C core.

## Review

One round, 2026-11-22, by Codex (`gpt-6.2-astra`, high) and Gemini
(`gemini-4-carbon-high`) at the same time. Folded in from Codex: enumeration
that would have broken v1 callers (item 3); a separately named v2 type with
tail-preserving tests (item 2); the exact bit stream (item 1); diagnostics
that drive the batch API, send a true short input and fail on a miss; the
golden-vector identity mismatch and the Accelerate filter (item 8); a
captured transmit reference for the released profiles; the over-the-air
screen restated for the geometry it would use; a per-row identity scheme;
exact short-input behavior; wrong-mode and partial-recovery tests; and the
corrected statements about repeated bytes and the worst-case payload.
Folded in from Gemini: named statuses for the two refusals; the 136-byte size
on every target; a small committed golden case and the reason a full
registered one is not committed; the call sites that assume five v1 profiles;
a peak gate that does not fail by chance; and four roadmap limits, including
the correction that a mask-matching payload is expected to fail outright.
One point was reconciled between the two: Codex asked for the golden case to
carry a registered identity, Gemini showed a registered profile is too large
and, for most, incompatible with the generator's channel, so the committed
case is a small fixture profile and the registered IDs are covered by the
recovery, peak and replay gates. Nothing else was declined.

## Questions for David

1. Approve this design, or change it? The choices most worth a second look
   are the separate v2 profile type with its own enumeration (items 2 and 3)
   and accepting short payloads on scrambled profiles (item 7).
2. Should the per-frame seed stay out, given the known limit above?
