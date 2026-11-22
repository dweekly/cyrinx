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

The versioned API looks up the corresponding registry entry and throws on an invalid operation. Here `payload` is Foundation Data as in the older example.

```swift
let profile = try BatchPHY.Profile(id: 1)
let frame = try BatchPHY.encode(Array(payload), profile: profile)
let decoded = try BatchPHY.decode(primary: .init(frame.samples), profile: profile)
for (slot, valid) in decoded.blockValidity.enumerated() where valid == 1 {
    let bytes = decoded.payload[(slot * 256)..<((slot + 1) * 256)]
    // Deliver these verified bytes with their slot index.
}
```

Use `try BatchPHY.Profile.registered()` to enumerate profiles and inspect their wire identity and physical parameters. A short input is zero-padded to the profile payload size during encoding. A successful decode can return zero valid blocks. Use the ordered mask before consuming bytes.

For interleaved stereo supply two channels backed by the same Swift array, with stride 2 and first-sample offsets 0 and 1. For planar inputs supply separate arrays. The C decoder aligns the channels to their common logical length. Its result includes the selected receiver, selection reason, clipping and nonfinite counts, and pilot error measurements. A hard demodulation failure carries the retained measurements in `BatchPHY.Failure.decodeFailed`. The calling code owns all returned arrays.
