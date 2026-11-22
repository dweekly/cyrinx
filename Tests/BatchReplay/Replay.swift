import Foundation
import ReplayProfiles

@testable import Cyrinx

enum ReplayError: Error { case input }

func replay(_ key: Int32, samples: [Float], channels: UInt32) throws -> Data {
    var raw = cyrinx_profile_t()
    guard replay_profile(key, &raw), (1...2).contains(channels),
        samples.count.isMultiple(of: Int(channels))
    else { throw ReplayError.input }
    let profile = try BatchPHY.Profile(validating: raw)
    let primary = BatchPHY.Channel(samples, stride: channels)
    let secondary = channels == 2 ? BatchPHY.Channel(samples, firstSample: 1, stride: 2) : nil
    let decoded = try BatchPHY.decode(primary: primary, secondary: secondary, profile: profile)
    var output = Data(decoded.metrics.profileIdentitySHA256)
    for field in [decoded.metrics.selectedReceiver, decoded.metrics.selectionReason] {
        withUnsafeBytes(of: field.littleEndian) { output.append(contentsOf: $0) }
    }
    return output + decoded.blockValidity + decoded.payload
}

guard CommandLine.arguments.count == 4, let key = Int32(CommandLine.arguments[1]),
    let channels = UInt32(CommandLine.arguments[3])
else {
    throw ReplayError.input
}
let data = try Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[2]))
guard data.count.isMultiple(of: 4) else { throw ReplayError.input }
let samples = data.withUnsafeBytes { bytes in
    stride(from: 0, to: bytes.count, by: 4).map {
        Float(bitPattern: UInt32(littleEndian: bytes.loadUnaligned(fromByteOffset: $0, as: UInt32.self)))
    }
}
FileHandle.standardOutput.write(try replay(key, samples: samples, channels: channels))
