import Foundation
import ReplayProfiles

@testable import Cyrinx

enum ReplayError: Error { case input }

func bytes<T: FixedWidthInteger>(_ value: T) -> Data {
    withUnsafeBytes(of: value.littleEndian) { Data($0) }
}

/// The record layout is Tests/BatchReplay/Packet.h.
func header(status: Int32, metrics: BatchPHY.Metrics?) -> Data {
    guard let metrics else { return bytes(status) + Data(count: 120) }
    var output = bytes(status) + Data(metrics.profileIdentitySHA256)
    let decisions = [metrics.profileID, metrics.selectedReceiver, metrics.selectionReason]
    let blocks = [metrics.blocksTotal, metrics.blocksOK, metrics.blockValidityRequired]
    let evidence = [metrics.clippingEvidence, metrics.nonfiniteEvidence]
    for field in decisions + blocks + evidence { output += bytes(field) }
    let capacities = [metrics.payloadBytesRequired, metrics.logicalFrameSamples]
    for field in capacities + [metrics.logicalSamplesDecoded, metrics.payloadBytesProduced] {
        output += bytes(field)
    }
    for field in [metrics.evmRMS, metrics.primaryHoldoutPilotRMS, metrics.mrcHoldoutPilotRMS] {
        output += bytes(field.bitPattern)
    }
    return output
}

func replay(_ key: Int32, samples: [Float], channels: UInt32) throws -> Data {
    var raw = cyrinx_profile_t()
    guard replay_profile(key, &raw), (1...2).contains(channels),
        samples.count.isMultiple(of: Int(channels))
    else { throw ReplayError.input }
    let profile = try BatchPHY.Profile(validating: raw)
    let primary = BatchPHY.Channel(samples, stride: channels)
    let secondary = channels == 2 ? BatchPHY.Channel(samples, firstSample: 1, stride: 2) : nil
    do {
        let decoded = try BatchPHY.decode(primary: primary, secondary: secondary, profile: profile)
        return header(status: CYRINX_STATUS_OK, metrics: decoded.metrics) + decoded.blockValidity
            + decoded.payload
    } catch let failure as BatchPHY.Failure {
        // A typed failure is the Swift form of a C status; the record carries that status back.
        switch failure {
        case .invalidArgument: return header(status: CYRINX_STATUS_ERR_INVALID_ARGUMENT, metrics: nil)
        case .profileNotFound: return header(status: CYRINX_STATUS_ERR_NOT_FOUND, metrics: nil)
        case .bufferTooSmall: return header(status: CYRINX_STATUS_ERR_BUFFER_TOO_SMALL, metrics: nil)
        case .decodeFailed(let metrics): return header(status: CYRINX_STATUS_ERR_DECODE, metrics: metrics)
        case .status(let status): return header(status: status, metrics: nil)
        }
    }
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
