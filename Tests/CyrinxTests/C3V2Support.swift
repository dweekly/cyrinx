import CCyrinx
import Foundation

/// Helpers for the v2 profile and batch suites: lookup, sizing through the C capacity query, and
/// encode and decode over Swift arrays.
enum C3V2 {
    static func emptyProfile() -> cyrinx_profile_v2_t {
        var profile = cyrinx_profile_v2_t()
        profile.struct_size = CYRINX_PROFILE_V2_SIZE
        profile.abi_version = CYRINX_PROFILE_V2_ABI_VERSION
        return profile
    }

    static func registryProfile(id: UInt32) -> cyrinx_profile_v2_t? {
        var profile = emptyProfile()
        guard cyrinx_profile_v2_get_by_id(id, &profile) == CYRINX_STATUS_OK else { return nil }
        return profile
    }

    static func identityHex(_ profile: cyrinx_profile_v2_t) -> String {
        withUnsafeBytes(of: profile.identity_sha256) { C3Support.digestHex(Array($0)) }
    }

    /// Recompute and store the identity of a profile whose parameters were changed.
    static func restamp(_ profile: inout cyrinx_profile_v2_t) -> cyrinx_abi_status_t {
        var digest = [UInt8](repeating: 0, count: 32)
        let status = cyrinx_profile_v2_compute_identity(&profile, &digest)
        if status == CYRINX_STATUS_OK {
            withUnsafeMutableBytes(of: &profile.identity_sha256) { $0.copyBytes(from: digest) }
        }
        return status
    }

    struct Sizes {
        var frameSamples: Int
        var payloadBytes: Int
        var blocks: Int
    }

    /// Frame, payload and block counts as the v2 encoder's capacity query reports them.
    static func sizes(_ profile: cyrinx_profile_v2_t) -> Sizes? {
        var profile = profile
        var byte: UInt8 = 0
        var sample: Float = 0
        var result = C3Support.emptyResult()
        guard
            cyrinx_batch_encode_v2(&profile, &byte, 0, &sample, 0, &result)
                == CYRINX_STATUS_ERR_BUFFER_TOO_SMALL
        else { return nil }
        return Sizes(
            frameSamples: Int(result.logical_frame_samples), payloadBytes: Int(result.payload_bytes_required),
            blocks: Int(result.blocks_total))
    }

    static func encode(_ profile: cyrinx_profile_v2_t, payload: [UInt8]) -> [Float]? {
        guard let sizes = sizes(profile) else { return nil }
        var profile = profile
        var samples = [Float](repeating: 0, count: sizes.frameSamples)
        var result = C3Support.emptyResult()
        let status = cyrinx_batch_encode_v2(
            &profile, payload, payload.count, &samples, samples.count, &result)
        return status == CYRINX_STATUS_OK ? samples : nil
    }

    struct Decoded {
        var status: cyrinx_abi_status_t
        var payload: [UInt8]
        var mask: [UInt8]
        var result: cyrinx_batch_result_t
    }

    /// Decode one mono capture with exactly sized outputs.
    static func decode(_ profile: cyrinx_profile_v2_t, samples: [Float]) -> Decoded? {
        guard let sizes = sizes(profile) else { return nil }
        return decode(profile, samples: samples, payloadBytes: sizes.payloadBytes, blocks: sizes.blocks)
    }

    /// Decode one mono capture into outputs of the given sizes. The mask starts as 0xEE, so a
    /// call that writes none of it can be told from one that clears it.
    static func decode(
        _ profile: cyrinx_profile_v2_t, samples: [Float], payloadBytes: Int, blocks: Int
    ) -> Decoded {
        var profile = profile
        var payload = [UInt8](repeating: 0, count: payloadBytes)
        var mask = [UInt8](repeating: 0xEE, count: blocks)
        var result = C3Support.emptyResult()
        let status = samples.withUnsafeBufferPointer { buffer -> cyrinx_abi_status_t in
            var input = cyrinx_batch_input_t()
            input.struct_size = CYRINX_BATCH_INPUT_V1_SIZE
            input.abi_version = CYRINX_BATCH_INPUT_ABI_VERSION
            input.channel_count = 1
            input.channels.0 = cyrinx_batch_channel_view_t(
                samples: buffer.baseAddress, sample_count: UInt64(buffer.count), first_sample: 0, stride: 1,
                role: CYRINX_BATCH_ROLE_PRIMARY)
            return cyrinx_batch_decode_v2(
                &profile, &input, &payload, payload.count, &mask, mask.count, &result)
        }
        return Decoded(status: status, payload: payload, mask: mask, result: result)
    }

    /// The two header fields a caller sets before a lookup.
    struct Header {
        var structSize: UInt32
        var version: UInt32
        static let v1 = Header(structSize: CYRINX_PROFILE_V1_SIZE, version: CYRINX_PROFILE_ABI_VERSION)
        static let v2 = Header(structSize: CYRINX_PROFILE_V2_SIZE, version: CYRINX_PROFILE_V2_ABI_VERSION)
    }

    /// What a call did to a caller's raw structure.
    struct Probe {
        var status: cyrinx_abi_status_t
        /// The header is as the caller set it and every later byte still holds the sentinel.
        var untouched: Bool
        var raw: [UInt8]
    }

    /// Run `body` on a raw allocation of `bytes` bytes filled with 0xAB, with `header` set.
    static func sentinel(
        bytes: Int, _ header: Header, _ body: (UnsafeMutableRawPointer) -> cyrinx_abi_status_t
    ) -> Probe {
        let raw = UnsafeMutableRawPointer.allocate(byteCount: bytes, alignment: 8)
        defer { raw.deallocate() }
        raw.initializeMemory(as: UInt8.self, repeating: 0xAB, count: bytes)
        raw.storeBytes(of: header.structSize, as: UInt32.self)
        raw.storeBytes(of: header.version, toByteOffset: 4, as: UInt32.self)
        let status = body(raw)
        let after = Array(UnsafeRawBufferPointer(start: raw, count: bytes))
        let headerKept =
            raw.load(as: UInt32.self) == header.structSize
            && raw.load(fromByteOffset: 4, as: UInt32.self) == header.version
        return Probe(
            status: status, untouched: headerKept && after[8...].allSatisfy { $0 == 0xAB }, raw: after)
    }

    static func v1(_ raw: UnsafeMutableRawPointer) -> UnsafeMutablePointer<cyrinx_profile_t> {
        raw.assumingMemoryBound(to: cyrinx_profile_t.self)
    }

    static func v2(_ raw: UnsafeMutableRawPointer) -> UnsafeMutablePointer<cyrinx_profile_v2_t> {
        raw.assumingMemoryBound(to: cyrinx_profile_v2_t.self)
    }

    /// A run of a structure's bytes, for comparing several fields at once.
    static func bytes<T>(_ value: T, _ range: Range<Int>) -> [UInt8] {
        withUnsafeBytes(of: value) { Array($0[range]) }
    }

    /// A varied payload: a linear congruential sequence, so no block is constant.
    static func varied(_ count: Int, seed: UInt32) -> [UInt8] {
        var state = seed
        return (0..<count).map { _ in
            state = state &* 1_664_525 &+ 1_013_904_223
            return UInt8(truncatingIfNeeded: state >> 24)
        }
    }
}
