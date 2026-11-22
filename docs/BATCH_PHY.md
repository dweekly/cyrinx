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

Use `try BatchPHY.Profile.registered()` to enumerate profiles and inspect their wire identity and physical parameters. A successful decode can return zero valid blocks. Use the ordered mask before consuming bytes.

## Payload size and content

The caller supplies a full frame. The codec has no scrambler, so the bytes chosen for unused space decide whether the frame decodes. In a digital loopback with no audio hardware, a frame of zero bytes recovered no blocks on any of the five registered profiles. A frame holding 128 data bytes followed by zeros also recovered none, including the block that carried the data. Frames filled with pseudorandom bytes, or with a repeated nonzero byte, recovered every block. How much zero fill a frame tolerates is uncharacterized. The package test `zeroHeavyFrameRecoversNoBlocks` records this behavior.

Fill unused space with varied bytes, such as a seeded pseudorandom sequence that the receiving application can recognize and discard. A reversible XOR mask restored recovery in the same loopback ([NEGATIVE_FINDINGS entry 26](NEGATIVE_FINDINGS.md)). That mask is application-level research and is absent from the wire format. The C entry point `cyrinx_batch_encode` still accepts a short payload and fills it with zeros. Swift rejects that input because the resulting frame carries no recoverable data.

## Raw codes

`Profile.classification`, `Profile.modulation`, `Profile.codeRate`, `Metrics.selectedReceiver` and `Metrics.selectionReason` are the C values, passed through unchanged so a value added by a later C version survives. Their names are the `CYRINX_CLASSIFICATION_*`, `CYRINX_MODULATION_*`, `CYRINX_CODE_RATE_*`, `CYRINX_BULK_DIVERSITY_*` and `CYRINX_BULK_AUTO_REASON_*` constants. Add `import CCyrinx` to compare against them.

## Channel layouts

For interleaved stereo supply two channels backed by the same Swift array, with stride 2 and first-sample offsets 0 and 1. For planar inputs supply separate arrays. The C decoder aligns the channels to their common logical length. Its result includes the selected receiver, selection reason, clipping and nonfinite counts, and pilot error measurements. A hard demodulation failure carries the retained measurements in `BatchPHY.Failure.decodeFailed`. The calling code owns all returned arrays.
