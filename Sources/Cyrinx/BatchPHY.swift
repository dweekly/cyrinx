import CCyrinx

/// One complete PCM frame encoded or decoded by the versioned C batch PHY.
public enum BatchPHY {
    public enum Failure: Error {
        case invalidArgument
        case profileNotFound
        case bufferTooSmall
        case decodeFailed(Metrics)
        case status(Int32)
    }

    /// An immutable, validated profile. Integer discriminators preserve their C values.
    ///
    /// Profiles 1 to 5 are unscrambled. Profiles 6 to 10 are the same five with a payload
    /// scrambler, which lets a frame of mostly zero bytes decode; see docs/BATCH_PHY.md.
    public struct Profile: Sendable {
        fileprivate let raw: cyrinx_profile_v2_t
        public let frameSampleCount: Int
        public let payloadByteCount: Int
        /// Bytes in one payload block. Block `i` occupies `i * blockByteCount` onward in a payload.
        public static let blockByteCount = Int(CYRINX_BULK_CRC_BLOCK)

        /// Looks up a registered profile, scrambled or not, by its ID.
        public init(id: UInt32) throws {
            var profile = Self.empty()
            try BatchPHY.check(cyrinx_profile_v2_get_by_id(id, &profile))
            try self.init(validating: profile)
        }

        /// The unscrambled registered profiles. This list does not grow when scrambled profiles
        /// are added, so code that offers every entry of it keeps offering the same formats.
        public static func registered() throws -> [Self] {
            try profiles(count: cyrinx_profile_get_count())
        }

        /// Every registered profile: the unscrambled ones in the order of `registered()`, then
        /// the scrambled ones.
        public static func registeredIncludingScrambled() throws -> [Self] {
            try profiles(count: cyrinx_profile_v2_get_count())
        }

        // The C registry lists the unscrambled profiles first, in their original order.
        private static func profiles(count: Int) throws -> [Self] {
            try (0..<count).map { index in
                var profile = Self.empty()
                try BatchPHY.check(cyrinx_profile_v2_get_by_index(index, &profile))
                return try Self(validating: profile)
            }
        }

        private static func empty() -> cyrinx_profile_v2_t {
            var profile = cyrinx_profile_v2_t()
            profile.struct_size = CYRINX_PROFILE_V2_SIZE
            profile.abi_version = CYRINX_PROFILE_V2_ABI_VERSION
            return profile
        }

        /// A custom unscrambled profile in the v1 layout, such as a test fixture. It must pass
        /// the v1 validation before it is carried over to the v2 layout this type holds.
        init(validating value: cyrinx_profile_t) throws {
            var v1 = value
            try BatchPHY.check(cyrinx_profile_validate(&v1))
            var profile = Self.empty()
            // The v2 layout begins with the v1 fields at the same offsets.
            withUnsafeMutableBytes(of: &profile) { target in
                withUnsafeBytes(of: v1) { target.copyMemory(from: $0) }
            }
            profile.struct_size = CYRINX_PROFILE_V2_SIZE
            profile.abi_version = CYRINX_PROFILE_V2_ABI_VERSION
            try self.init(validating: profile)
        }

        // Uses the C capacity query for both dimensions, including custom fixture profiles.
        init(validating value: cyrinx_profile_v2_t) throws {
            var profile = value
            var byte: UInt8 = 0
            var sample: Float = 0
            var result = BatchPHY.emptyResult()
            let status = cyrinx_batch_encode_v2(&profile, &byte, 0, &sample, 0, &result)
            guard status == CYRINX_STATUS_ERR_BUFFER_TOO_SMALL else {
                try BatchPHY.check(status)
                throw Failure.invalidArgument
            }
            raw = profile
            frameSampleCount = Int(result.logical_frame_samples)
            payloadByteCount = Int(result.payload_bytes_required)
        }

        public var id: UInt32 { raw.id }
        public var identitySHA256: [UInt8] { withUnsafeBytes(of: raw.identity_sha256) { Array($0) } }
        public var classification: UInt32 { raw.classification }
        public var modulation: UInt32 { raw.modulation }
        public var codeRate: UInt32 { raw.code_rate }
        public var lowFrequencyHz: Double { raw.low_frequency_hz }
        public var highFrequencyHz: Double { raw.high_frequency_hz }
        public var fftSize: UInt32 { raw.fft_size }
        public var cyclicPrefix: UInt32 { raw.cyclic_prefix }
        public var sampleRate: UInt32 { raw.sample_rate }
        public var pilotEvery: UInt32 { raw.pilot_every }
        public var symbolCount: UInt32 { raw.symbol_count }
        public var blockCount: UInt32 { raw.block_count }
        public var amplitude: Double { raw.amplitude }
        public var clipSigma: Double { raw.clip_sigma }
        public var chirpStartHz: Double { raw.chirp_f0 }
        public var chirpEndHz: Double { raw.chirp_f1 }
        /// The C scrambler mode, `CYRINX_PAYLOAD_SCRAMBLER_*`. It is part of the identity.
        public var payloadScrambler: UInt32 { raw.payload_scrambler }
        public var isScrambled: Bool { raw.payload_scrambler != CYRINX_PAYLOAD_SCRAMBLER_NONE.rawValue }
    }

    /// A logical microphone channel in owned planar or interleaved PCM.
    public struct Channel: Sendable {
        public let samples: [Float]
        public let firstSample: UInt64
        public let stride: UInt32

        public init(_ samples: [Float], firstSample: UInt64 = 0, stride: UInt32 = 1) {
            self.samples = samples
            self.firstSample = firstSample
            self.stride = stride
        }

        fileprivate func withView<T>(
            role: UInt32, _ body: (cyrinx_batch_channel_view_t) throws -> T
        ) rethrows -> T {
            try samples.withUnsafeBufferPointer {
                try body(
                    cyrinx_batch_channel_view_t(
                        samples: $0.baseAddress, sample_count: UInt64($0.count),
                        first_sample: firstSample, stride: stride, role: role))
            }
        }
    }

    /// Snapshot of the v1 batch result, including raw receiver and reason codes.
    public struct Metrics: Sendable {
        private let raw: cyrinx_batch_result_t
        init(_ raw: cyrinx_batch_result_t) { self.raw = raw }

        public var abiVersion: UInt32 { raw.abi_version }
        public var structSize: UInt32 { raw.struct_size }
        public var profileID: UInt32 { raw.profile_id }
        public var profileIdentitySHA256: [UInt8] {
            withUnsafeBytes(of: raw.profile_identity_sha256) { Array($0) }
        }
        public var selectedReceiver: UInt32 { raw.selected_receiver }
        public var selectionReason: UInt32 { raw.selection_reason }
        public var blocksTotal: UInt32 { raw.blocks_total }
        public var blocksOK: UInt32 { raw.blocks_ok }
        public var blockValidityRequired: UInt32 { raw.block_validity_required }
        public var payloadBytesRequired: UInt64 { raw.payload_bytes_required }
        public var logicalFrameSamples: UInt64 { raw.logical_frame_samples }
        public var logicalSamplesDecoded: UInt64 { raw.logical_samples_decoded }
        public var payloadBytesProduced: UInt64 { raw.payload_bytes_produced }
        public var samplesProduced: UInt64 { raw.samples_produced }
        public var clippingEvidence: UInt32 { raw.clipping_evidence }
        public var nonfiniteEvidence: UInt32 { raw.nonfinite_evidence }
        public var evmRMS: Double { raw.evm_rms }
        public var primaryHoldoutPilotRMS: Double { raw.primary_holdout_pilot_rms }
        public var mrcHoldoutPilotRMS: Double { raw.mrc_holdout_pilot_rms }
    }

    public struct Encoded: Sendable {
        public let samples: [Float]
        public let metrics: Metrics
    }

    public struct Decoded: Sendable {
        public let payload: [UInt8]
        /// One byte per payload block in wire order. A valid block has value 1.
        public let blockValidity: [UInt8]
        public let metrics: Metrics
    }

    /// Encodes exactly one frame.
    ///
    /// On a scrambled profile `payload` may hold from no bytes to `profile.payloadByteCount`. A
    /// shorter payload is followed by zero bytes to fill the frame, and the decoder returns the
    /// whole frame: the frame carries no length, so the application keeps it.
    ///
    /// On an unscrambled profile `payload` must hold exactly `profile.payloadByteCount` bytes,
    /// and a shorter one throws `Failure.invalidArgument`. Zero fill there can lose the whole
    /// frame: in the cases measured, a short record followed by zeros decoded no blocks,
    /// including the block that carried the record. See docs/BATCH_PHY.md.
    public static func encode(_ payload: [UInt8], profile: Profile) throws -> Encoded {
        let capacity = profile.payloadByteCount
        guard payload.count == capacity || (profile.isScrambled && payload.count < capacity) else {
            throw Failure.invalidArgument
        }
        // The fill is done here, not left to the C entry point: C refuses the null pointer that
        // an empty array becomes.
        let frame = payload + [UInt8](repeating: 0, count: capacity - payload.count)
        var raw = profile.raw
        var result = emptyResult()
        var samples = [Float](repeating: 0, count: profile.frameSampleCount)
        try check(cyrinx_batch_encode_v2(&raw, frame, frame.count, &samples, samples.count, &result))
        return Encoded(samples: samples, metrics: Metrics(result))
    }

    public static func decode(primary: Channel, secondary: Channel? = nil, profile: Profile) throws -> Decoded
    {
        var raw = profile.raw
        var input = cyrinx_batch_input_t()
        input.struct_size = CYRINX_BATCH_INPUT_V1_SIZE
        input.abi_version = CYRINX_BATCH_INPUT_ABI_VERSION
        input.channel_count = secondary == nil ? 1 : 2
        var result = emptyResult()
        var payload = [UInt8](repeating: 0, count: profile.payloadByteCount)
        var mask = [UInt8](repeating: 0, count: Int(profile.blockCount))
        func decodeInput() -> Int32 {
            cyrinx_batch_decode_v2(&raw, &input, &payload, payload.count, &mask, mask.count, &result)
        }
        let status = primary.withView(role: CYRINX_BATCH_ROLE_PRIMARY) {
            input.channels.0 = $0
            guard let secondary else { return decodeInput() }
            return secondary.withView(role: CYRINX_BATCH_ROLE_SECONDARY) {
                input.channels.1 = $0
                return decodeInput()
            }
        }
        if status == CYRINX_STATUS_ERR_DECODE { throw Failure.decodeFailed(Metrics(result)) }
        try check(status)
        return Decoded(payload: payload, blockValidity: mask, metrics: Metrics(result))
    }

    private static func emptyResult() -> cyrinx_batch_result_t {
        var result = cyrinx_batch_result_t()
        result.struct_size = CYRINX_BATCH_RESULT_V1_SIZE
        result.abi_version = CYRINX_BATCH_RESULT_ABI_VERSION
        return result
    }

    static func check(_ status: Int32) throws {
        switch status {
        case CYRINX_STATUS_OK: return
        case CYRINX_STATUS_ERR_INVALID_ARGUMENT: throw Failure.invalidArgument
        case CYRINX_STATUS_ERR_NOT_FOUND: throw Failure.profileNotFound
        case CYRINX_STATUS_ERR_BUFFER_TOO_SMALL: throw Failure.bufferTooSmall
        default: throw Failure.status(status)
        }
    }
}
