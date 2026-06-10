// Standalone validator: decodes a PCM16LE capture with the iOS BulkDemod Swift
// port and prints ordered-verified block count + EVM, for bit-compatibility
// comparison against modem.py (the same way BulkDemod.kt was validated).
//
// Build & run:
//   swiftc -O scratch/hw20k/validate_swift_decode.swift \
//          Apps/HIL/iOS/App/BulkDemod.swift -o /tmp/swiftdecode
//   /tmp/swiftdecode /tmp/ref_capture.pcm <channels> <fLo> <fHi> <nSym> <nPayloads> <seedBase>
//
// BulkDemod.swift is pure-Foundation (no UIKit/AVFoundation), so it links
// directly here. This keeps the on-device decoder honest: the same source file
// the app ships is exercised against the Python reference.

import Foundation

let args = CommandLine.arguments
let path = args.count > 1 ? args[1] : "/tmp/ref_capture.pcm"
let channels = args.count > 2 ? Int(args[2])! : 1
let fLo = args.count > 3 ? Double(args[3])! : 1_100
let fHi = args.count > 4 ? Double(args[4])! : 23_000
let nSym = args.count > 5 ? Int(args[5])! : 64
let nPayloads = args.count > 6 ? Int(args[6])! : 1
let seedBase = args.count > 7 ? UInt64(args[7])! : 1_000

let dec = BulkDemod.decodeCapture(path: path, channels: channels, fLo: fLo, fHi: fHi,
                                  nSym: nSym, nPayloads: nPayloads,
                                  payloadSeedBase: seedBase) { print($0) }
let spanS = Double(dec.spanSamples) / Double(BulkDemod.SRATE)
let gp = spanS > 0 ? Double(dec.verified * BulkDemod.CRC_BLOCK * 8) / spanS : 0
print(String(format: "SWIFT TOTAL: verified=%d blocks span=%.2fs goodput=%.0f bps", dec.verified, spanS, gp))
for fr in dec.frames {
    print(String(format: "SWIFT frame@%d: blocks_ok=%d/%d verified=%d evm=%.4f",
                 fr.start, fr.blocksOk, fr.blocksTotal, fr.verified, fr.evm))
}
