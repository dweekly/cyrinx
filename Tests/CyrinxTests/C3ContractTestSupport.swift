import CCyrinx
import Foundation

/// Shared helpers for the C3-03/04/05 contract suites: registry access, digest
/// formatting, and batch encode/decode plumbing over Swift arrays.
enum C3Support {
    /// A caller-side profile with the v1 ABI prefix pre-set.
    static func emptyProfile() -> cyrinx_profile_t {
        var profile = cyrinx_profile_t()
        profile.struct_size = UInt32(MemoryLayout<cyrinx_profile_t>.size)
        profile.abi_version = CYRINX_PROFILE_ABI_VERSION
        return profile
    }

    static func registryProfile(id: UInt32) -> cyrinx_profile_t? {
        var profile = emptyProfile()
        guard cyrinx_profile_get_by_id(id, &profile) == CYRINX_STATUS_OK else { return nil }
        return profile
    }

    static func identityHex(_ profile: cyrinx_profile_t) -> String {
        withUnsafeBytes(of: profile.identity_sha256) { raw in
            raw.map { String(format: "%02x", $0) }.joined()
        }
    }

    static func digestHex(_ digest: [UInt8]) -> String {
        digest.map { String(format: "%02x", $0) }.joined()
    }

    static func sha256Hex(_ data: [UInt8]) -> String {
        var digest = [UInt8](repeating: 0, count: 32)
        data.withUnsafeBufferPointer { buffer in
            cyrinx_sha256(buffer.baseAddress, buffer.count, &digest)
        }
        return digestHex(digest)
    }

    static func geometry(for profile: cyrinx_profile_t) -> cyrinx_bulk_geometry? {
        rateString(profile.code_rate).withCString { ratePointer in
            var config = cyrinx_bulk_config()
            config.f_lo = profile.low_frequency_hz
            config.f_hi = profile.high_frequency_hz
            config.pilot_every = Int32(profile.pilot_every)
            config.bits_per_bin = modulationBits(profile.modulation)
            config.rate = ratePointer
            config.n_sym = Int32(profile.symbol_count)
            config.nfft = Int32(profile.fft_size)
            config.cp = Int32(profile.cyclic_prefix)
            config.sr = Int32(profile.sample_rate)
            config.amp = profile.amplitude
            config.clip_sigma = profile.clip_sigma
            config.chirp_f0 = profile.chirp_f0
            config.chirp_f1 = profile.chirp_f1
            var geometry = cyrinx_bulk_geometry()
            guard cyrinx_bulk_compute_geometry(&config, &geometry) == 0 else { return nil }
            return geometry
        }
    }

    private static func modulationBits(_ modulation: UInt32) -> Int32 {
        switch modulation {
        case CYRINX_MODULATION_BPSK.rawValue: return 1
        case CYRINX_MODULATION_QPSK.rawValue: return 2
        case CYRINX_MODULATION_16QAM.rawValue: return 4
        case CYRINX_MODULATION_64QAM.rawValue: return 6
        default: return 8
        }
    }

    private static func rateString(_ rate: UInt32) -> String {
        switch rate {
        case CYRINX_CODE_RATE_1_2.rawValue: return "1/2"
        case CYRINX_CODE_RATE_2_3.rawValue: return "2/3"
        case CYRINX_CODE_RATE_3_4.rawValue: return "3/4"
        default: return "5/6"
        }
    }

    /// Encode one frame for `profile`, returning the frame samples.
    static func encodeFrame(profile: inout cyrinx_profile_t, payload: [UInt8]) -> [Float]? {
        guard let geometry = geometry(for: profile) else { return nil }
        var samples = [Float](repeating: 0, count: Int(geometry.frame_samples))
        var result = emptyResult()
        let status = payload.withUnsafeBufferPointer { payloadBuffer in
            samples.withUnsafeMutableBufferPointer { sampleBuffer in
                cyrinx_batch_encode(
                    &profile, payloadBuffer.baseAddress, payloadBuffer.count,
                    sampleBuffer.baseAddress, sampleBuffer.count, &result)
            }
        }
        guard status == CYRINX_STATUS_OK else { return nil }
        return samples
    }

    static func emptyResult() -> cyrinx_batch_result_t {
        var result = cyrinx_batch_result_t()
        result.struct_size = UInt32(MemoryLayout<cyrinx_batch_result_t>.size)
        result.abi_version = CYRINX_BATCH_RESULT_ABI_VERSION
        return result
    }

    struct DecodeOutcome {
        var status: cyrinx_abi_status_t
        var payload: [UInt8]
        var validity: [UInt8]
        var result: cyrinx_batch_result_t
    }

    /// Decode with explicit channel views over one or two backing buffers.
    /// When `secondaryStorage` is nil but `secondaryView` is set, the
    /// secondary view aliases the primary storage (interleaved layouts).
    static func decode(
        profile: inout cyrinx_profile_t, primaryStorage: [Float],
        primaryView: (offset: UInt64, stride: UInt32),
        secondaryStorage: [Float]? = nil,
        secondaryView: (offset: UInt64, stride: UInt32)? = nil,
        payloadCap: Int, validityCap: Int,
        mutateInput: ((inout cyrinx_batch_input_t) -> Void)? = nil
    ) -> DecodeOutcome {
        var payload = [UInt8](repeating: 0, count: payloadCap)
        var validity = [UInt8](repeating: 0, count: validityCap)
        var result = emptyResult()
        let secondaryBacking = secondaryStorage ?? []

        let status = primaryStorage.withUnsafeBufferPointer { primary in
            secondaryBacking.withUnsafeBufferPointer { separate -> cyrinx_abi_status_t in
                var input = cyrinx_batch_input_t()
                input.struct_size = UInt32(MemoryLayout<cyrinx_batch_input_t>.size)
                input.abi_version = CYRINX_BATCH_INPUT_ABI_VERSION
                input.channel_count = secondaryView == nil ? 1 : 2
                input.channels.0 = cyrinx_batch_channel_view_t(
                    samples: primary.baseAddress, sample_count: UInt64(primary.count),
                    first_sample: primaryView.offset, stride: primaryView.stride,
                    role: CYRINX_BATCH_ROLE_PRIMARY)
                if let view = secondaryView {
                    let storage = secondaryStorage == nil ? primary : separate
                    input.channels.1 = cyrinx_batch_channel_view_t(
                        samples: storage.baseAddress, sample_count: UInt64(storage.count),
                        first_sample: view.offset, stride: view.stride,
                        role: CYRINX_BATCH_ROLE_SECONDARY)
                }
                mutateInput?(&input)
                return payload.withUnsafeMutableBufferPointer { payloadBuffer in
                    validity.withUnsafeMutableBufferPointer { validityBuffer in
                        cyrinx_batch_decode(
                            &profile, &input, payloadBuffer.baseAddress,
                            payloadBuffer.count, validityBuffer.baseAddress,
                            validityBuffer.count, &result)
                    }
                }
            }
        }
        return DecodeOutcome(status: status, payload: payload, validity: validity, result: result)
    }
}
