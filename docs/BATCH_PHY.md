# Using the Swift batch PHY

## In plain English

`BatchPHY` turns a payload into one acoustic frame or decodes a complete PCM recording into payload blocks. Each block has its own validity byte, so a caller can retain the successfully decoded pieces of an incomplete transmission. Swift exposes the profile, decisions and measurements supplied by the C decoder.

## Moving from BulkPHY

For the 2.x default geometry a typical caller used the optional encoder and decoder.

```swift
let modem = BulkPHY()
if let samples = modem.encode(payload), let decoded = modem.decode(samples) {
    // Consume the valid blocks from decoded.
}
```

The versioned API looks up the corresponding registry entry and throws on an invalid operation. Here `payload` is Foundation Data holding exactly `profile.payloadByteCount` bytes. The older encoder returned nil for any other length. The new one throws `BatchPHY.Failure.invalidArgument`.

```swift
let profile = try BatchPHY.Profile(id: 1)
let frame = try BatchPHY.encode(Array(payload), profile: profile)
let decoded = try BatchPHY.decode(primary: .init(frame.samples), profile: profile)
for (slot, valid) in decoded.blockValidity.enumerated() where valid == 1 {
    let size = BatchPHY.Profile.blockByteCount
    let bytes = decoded.payload[(slot * size)..<((slot + 1) * size)]
    // Deliver these verified bytes with their slot index.
}
```

Use `try BatchPHY.Profile.registered()` to enumerate the five unscrambled profiles and inspect their wire identity and physical parameters. `try BatchPHY.Profile.registeredIncludingScrambled()` lists all ten; see Scrambled profiles below. A successful decode can return zero valid blocks. Use the ordered mask before consuming bytes.

## Scrambled profiles

Profiles 6 to 10 are profiles 1 to 5 with a payload scrambler: the sender XORs each frame's payload and CRC bits with a fixed pseudorandom pattern before coding, and the receiver removes it. Their parameters, frame length and capacity equal their parents'. Their identities differ, and a receiver built before they existed cannot decode them. `Profile.isScrambled` and `Profile.payloadScrambler` report the mode. All five are experimental: they are measured in digital loopback and by replay on two phones, and none has been sent over the air.

`Profile(id:)` finds all ten. `Profile.registered()` still returns the five unscrambled profiles, so code that offers every entry of that list keeps offering the same formats.

A decoder is told its profile by the caller. Nothing in a frame says whether it is scrambled, and decoding a frame with the twin of the profile that made it returns a completed decode with no valid block.

## Payload size and content

On a scrambled profile the caller may supply any length from nothing to `profile.payloadByteCount`. A shorter payload is followed by zero bytes to fill the frame. The frame carries no length: the decoder returns the whole frame, and the application keeps the original length or puts it in the payload. In digital loopback each scrambled profile recovers every block for payloads of 0, 1, 128, 255, 256 and 257 bytes, one byte short of full, and full (package test `scrambledProfileAcceptsShortPayloads`).

On an unscrambled profile the caller supplies a full frame, and the bytes chosen for unused space decide whether the frame decodes. In a digital loopback with no audio hardware, a frame of zero bytes recovered no blocks on any of the five unscrambled profiles. A frame holding 128 data bytes followed by zeros also recovered none, including the block that carried the data. Frames filled with pseudorandom bytes, or with a repeated 0x71 byte, recovered every block. A repeated 0xff byte did not on profiles 3 to 5: like zeros, it codes to a single constellation point, and the transmitter's amplitude clip then removes the peaks that carry the frame ([NEGATIVE_FINDINGS entry 26](NEGATIVE_FINDINGS.md)). Zero fill is not always fatal: on profile 1, a frame one-third full of a repeated 0x71 byte and zero after that recovered 11 of 75 blocks, and a frame one byte short of full recovered all 75. Where the boundary lies is uncharacterized. The package test `zeroHeavyFrameRecoversNoBlocks` records this behavior.

So on an unscrambled profile, fill unused space with varied bytes, such as a seeded pseudorandom sequence that the receiving application can recognize and discard, or use the scrambled twin. Swift rejects a short payload there because, for a short record, the zero-padded frame lost the record in every case measured. The C entry points accept a short payload on either kind of profile and fill it with zeros.

The scrambler has a limit of its own. It makes common payloads safe, not every payload: a payload equal to the scrambler's own pattern cancels it and is expected to fail as a zero frame does unscrambled ([SCRAMBLER_PLAN](SCRAMBLER_PLAN.md), Known limits).

## Raw codes

`Profile.classification`, `Profile.modulation`, `Profile.codeRate`, `Metrics.selectedReceiver` and `Metrics.selectionReason` are the C values, passed through unchanged so a value added by a later C version survives. Their names are the `CYRINX_CLASSIFICATION_*`, `CYRINX_MODULATION_*`, `CYRINX_CODE_RATE_*`, `CYRINX_BULK_DIVERSITY_*` and `CYRINX_BULK_AUTO_REASON_*` constants. Add `import CCyrinx` to compare against them.

## Channel layouts

For interleaved stereo supply two channels backed by the same Swift array, with stride 2 and first-sample offsets 0 and 1. For planar inputs supply separate arrays. The C decoder aligns the channels to their common logical length. Its result includes the selected receiver, selection reason, clipping and nonfinite counts, and pilot error measurements. A hard demodulation failure carries the retained measurements in `BatchPHY.Failure.decodeFailed`. The calling code owns all returned arrays.
