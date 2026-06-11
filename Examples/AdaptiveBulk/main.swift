import Cyrinx
import Foundation

/// Demonstrates the portable bulk-PHY codec plus the adaptive surfaces:
///   1. encode a payload to a waveform and decode it back (digital loopback),
///   2. ask the sounder which MCS to use for a few measured channel conditions,
///   3. turn channel metrics into a human repositioning hint.
@main
struct AdaptiveBulkExample {
    static func main() {
        // 1. Bulk codec round trip (16-QAM r3/4 near-field profile).
        let phy = BulkPHY(configuration: .init(bitsPerBin: 4, rate: "3/4", symbolCount: 8))
        guard let geo = phy.geometry() else {
            print("invalid configuration")
            return
        }
        let payload = Data((0..<geo.payloadBytes).map { UInt8(($0 * 37 + 11) & 0xFF) })
        guard let wave = phy.encode(payload) else {
            print("encode failed")
            return
        }
        var rx = [Float](repeating: 0, count: 3000)
        rx.append(contentsOf: wave)
        rx.append(contentsOf: [Float](repeating: 0, count: 2000))
        let decoded = phy.decode(rx)

        print("=== bulk codec ===")
        print("  frame: \(geo.payloadBytes) B payload, \(wave.count) samples")
        if let d = decoded {
            print(
                "  decoded \(d.blocksOK)/\(d.blockCount) blocks, "
                    + "payload match=\(d.payload == payload), evm=\(String(format: "%.3f", d.evmRMS))")
        }

        // 2. Adaptive MCS recommendation across channel conditions.
        print("\n=== adaptive sounder (median SNR, -15 dB delay spread) -> MCS ===")
        let conditions: [(String, Double, Double)] = [
            ("clean near-field", 24, 8),
            ("direct contact", 10.5, 12),
            ("reverberant desk", 25, 36),
        ]
        for (name, snr, ds) in conditions {
            let r = recommendMCS(medianSNRdB: snr, delaySpreadMs15: ds)
            let reposition = r.adviseReposition ? "  (a better spot would help)" : ""
            print(
                "  \(name.padding(toLength: 18, withPad: " ", startingAt: 0)) "
                    + "-> \(r.modulation) r\(r.rate), CP \(String(format: "%.0f", r.cyclicPrefixMs)) ms"
                    + reposition)
        }

        // 3. Repositioning guidance.
        print("\n=== repositioning guidance ===")
        let samples: [(String, ChannelMetrics)] = [
            ("weak signal", .init(medianSNRdB: 4, rxPeak: 0.02, delaySpreadMs: 8, cyclicPrefixMs: 16)),
            ("clipping", .init(medianSNRdB: 22, rxPeak: 0.99, delaySpreadMs: 8, cyclicPrefixMs: 16)),
            ("echoey room", .init(medianSNRdB: 20, rxPeak: 0.4, delaySpreadMs: 36, cyclicPrefixMs: 16)),
        ]
        for (name, m) in samples {
            let advice = repositioningAdvice(for: m)
            print("  \(name.padding(toLength: 14, withPad: " ", startingAt: 0)) -> \(advice.hint.text)")
        }
    }
}
