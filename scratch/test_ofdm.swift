import Foundation
import Cyrinx

func main() {
    let payload = (0..<96).map { UInt8($0 % 251) }
    let config = VDSPOFDMConfig(sampleRateHz: 48_000)

    print("Payload size: \(payload.count) bytes")
    do {
        let samples = try VDSPPHY.modulateOFDM(payload: payload, mode: 2, config: config)
        print("Waveform samples size: \(samples.count)")
        let maxPeak = samples.reduce(0.0) { max($0, abs($1)) }
        print("Waveform max peak: \(maxPeak)")
        let decoded = try VDSPPHY.demodulateOFDM(samples: samples, mode: 2, config: config)
        print("Demodulated successfully! Decoded size: \(decoded.count)")
        if decoded == payload {
            print("SUCCESS! MATCH!")
        } else {
            print("FAILURE! MISMATCH!")
            for i in 0..<max(decoded.count, payload.count) {
                let decVal = i < decoded.count ? "\(decoded[i])" : "nil"
                let payVal = i < payload.count ? "\(payload[i])" : "nil"
                if decVal != payVal {
                    print("Index \(i): decoded=\(decVal), expected=\(payVal)")
                }
            }
        }
    } catch {
        print("Error: \(error)")
    }
}

main()
