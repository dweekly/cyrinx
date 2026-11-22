import Foundation
import ReplayProfiles

// Standalone host adapter for the public C batch contract.
enum ReplayError: Error { case input, decode(Int32), output }

func replay(_ key: Int32, samples: [Float], channels: UInt32) throws -> Data {
    var profile = cyrinx_profile_t()
    guard replay_profile(key, &profile), !samples.isEmpty, (1...2).contains(channels),
        samples.count.isMultiple(of: Int(channels))
    else { throw ReplayError.input }
    var mask = [UInt8](repeating: 0, count: Int(profile.block_count))
    var payload = [UInt8](repeating: 0, count: mask.count * 256)
    var result = cyrinx_batch_result_t()
    result.struct_size = UInt32(MemoryLayout.size(ofValue: result))
    result.abi_version = CYRINX_BATCH_RESULT_ABI_VERSION
    let status = samples.withUnsafeBufferPointer { buffer in
        var input = cyrinx_batch_input_t()
        input.struct_size = UInt32(MemoryLayout.size(ofValue: input))
        input.abi_version = CYRINX_BATCH_INPUT_ABI_VERSION
        input.channel_count = channels
        input.channels.0 = cyrinx_batch_channel_view_t(
            samples: buffer.baseAddress, sample_count: UInt64(buffer.count), first_sample: 0,
            stride: channels, role: CYRINX_BATCH_ROLE_PRIMARY)
        if channels == 2 {
            input.channels.1 = cyrinx_batch_channel_view_t(
                samples: buffer.baseAddress, sample_count: UInt64(buffer.count), first_sample: 1,
                stride: channels, role: CYRINX_BATCH_ROLE_SECONDARY)
        }
        return cyrinx_batch_decode(&profile, &input, &payload, payload.count, &mask, mask.count, &result)
    }
    guard status == CYRINX_STATUS_OK else { throw ReplayError.decode(status) }
    guard result.blocks_total == mask.count, result.payload_bytes_produced == payload.count else {
        throw ReplayError.output
    }
    var output = withUnsafeBytes(of: result.profile_identity_sha256) { Data($0) }
    for field in [result.selected_receiver, result.selection_reason] {
        withUnsafeBytes(of: field.littleEndian) { output.append(contentsOf: $0) }
    }
    return output + mask + payload
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
