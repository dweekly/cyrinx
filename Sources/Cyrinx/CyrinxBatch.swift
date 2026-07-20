import CCyrinx
import Foundation

public struct CyrinxBatchInputLayout: Sendable {
    public var primarySamples: [Float]
    public var secondarySamples: [Float]?
    public var channelCount: UInt32
    public var channelStride: UInt32
    public var monotonicStartIndex: UInt64
    public var discontinuityFlags: UInt32

    public init(
        primarySamples: [Float],
        secondarySamples: [Float]? = nil,
        channelCount: UInt32 = 1,
        channelStride: UInt32 = 1,
        monotonicStartIndex: UInt64 = 0,
        discontinuityFlags: UInt32 = 0
    ) {
        self.primarySamples = primarySamples
        self.secondarySamples = secondarySamples
        self.channelCount = channelCount
        self.channelStride = channelStride
        self.monotonicStartIndex = monotonicStartIndex
        self.discontinuityFlags = discontinuityFlags
    }
}

public struct CyrinxBatchResult: Sendable, Equatable {
    public var profileID: UInt32
    public var profileHash: Data
    public var selectedReceiver: UInt32
    public var selectionReason: UInt32
    public var primaryHoldoutPilotRMS: Double
    public var mrcHoldoutPilotRMS: Double
    public var evmRMS: Double
    public var blocksOk: Int32
    public var blocksTotal: Int32
    public var blockValidMask: Data
    public var consumedSamples: Int
    public var producedSamples: Int
    public var clippingEvidence: UInt32
    public var nonfiniteEvidence: UInt32
    public var propagationDelayMs: Double
    public var symbolTimingError: Double

    public init(
        profileID: UInt32 = 0,
        profileHash: Data = Data(repeating: 0, count: 32),
        selectedReceiver: UInt32 = 0,
        selectionReason: UInt32 = 0,
        primaryHoldoutPilotRMS: Double = 0.0,
        mrcHoldoutPilotRMS: Double = 0.0,
        evmRMS: Double = 0.0,
        blocksOk: Int32 = 0,
        blocksTotal: Int32 = 0,
        blockValidMask: Data = Data(repeating: 0, count: 256),
        consumedSamples: Int = 0,
        producedSamples: Int = 0,
        clippingEvidence: UInt32 = 0,
        nonfiniteEvidence: UInt32 = 0,
        propagationDelayMs: Double = 0.0,
        symbolTimingError: Double = 0.0
    ) {
        self.profileID = profileID
        self.profileHash = profileHash
        self.selectedReceiver = selectedReceiver
        self.selectionReason = selectionReason
        self.primaryHoldoutPilotRMS = primaryHoldoutPilotRMS
        self.mrcHoldoutPilotRMS = mrcHoldoutPilotRMS
        self.evmRMS = evmRMS
        self.blocksOk = blocksOk
        self.blocksTotal = blocksTotal
        self.blockValidMask = blockValidMask
        self.consumedSamples = consumedSamples
        self.producedSamples = producedSamples
        self.clippingEvidence = clippingEvidence
        self.nonfiniteEvidence = nonfiniteEvidence
        self.propagationDelayMs = propagationDelayMs
        self.symbolTimingError = symbolTimingError
    }
}

public enum CyrinxBatch {
    public static func decode(
        profile: CyrinxProfile,
        input: CyrinxBatchInputLayout,
        outCap: Int? = nil
    ) throws -> (payload: Data, result: CyrinxBatchResult) {
        var rawProfile = profile.toCStruct()

        var rawInput = cyrinx_batch_input_layout_t()
        rawInput.struct_size = MemoryLayout<cyrinx_batch_input_layout_t>.size
        rawInput.abi_version = UInt32(CYRINX_BATCH_INPUT_LAYOUT_ABI_VERSION)
        rawInput.channel_count = input.channelCount
        rawInput.channel_stride = input.channelStride
        rawInput.monotonic_start_index = input.monotonicStartIndex
        rawInput.discontinuity_flags = input.discontinuityFlags

        let geom = try computeGeometry(profile: profile, rawProfile: rawProfile)

        let cap = outCap ?? Int(geom.payload_bytes)
        var payload = Data(repeating: 0, count: cap)
        var rawResult = cyrinx_batch_result_t()
        rawResult.struct_size = MemoryLayout<cyrinx_batch_result_t>.size
        rawResult.abi_version = UInt32(CYRINX_BATCH_RESULT_ABI_VERSION)

        _ = try input.primarySamples.withUnsafeBufferPointer { primaryPtr in
            rawInput.primary_samples = primaryPtr.baseAddress
            rawInput.primary_count = input.primarySamples.count

            if let secondary = input.secondarySamples {
                return try secondary.withUnsafeBufferPointer { secPtr in
                    rawInput.secondary_samples = secPtr.baseAddress
                    rawInput.secondary_count = secondary.count
                    return try performDecode(&rawProfile, &rawInput, &payload, &rawResult)
                }
            } else {
                rawInput.secondary_samples = nil
                rawInput.secondary_count = 0
                return try performDecode(&rawProfile, &rawInput, &payload, &rawResult)
            }
        }

        return (payload: payload.prefix(Int(rawResult.produced_samples)), result: makeResult(rawResult))
    }

    private static func computeGeometry(
        profile: CyrinxProfile,
        rawProfile: cyrinx_profile_t
    ) throws -> cyrinx_bulk_geometry {
        var cfg = cyrinx_bulk_config()
        cfg.f_lo = rawProfile.low_frequency_hz
        cfg.f_hi = rawProfile.high_frequency_hz
        cfg.pilot_every = Int32(rawProfile.pilot_every)
        cfg.bits_per_bin = Int32(profile.modulation.bitsPerBin)
        let rateStr = profile.codeRate.stringValue
        var geom = cyrinx_bulk_geometry()
        let rcGeom = rateStr.withCString { ratePtr in
            cfg.rate = ratePtr
            cfg.n_sym = Int32(rawProfile.symbol_count)
            cfg.nfft = Int32(rawProfile.fft_size)
            cfg.cp = Int32(rawProfile.cyclic_prefix)
            cfg.sr = Int32(rawProfile.sample_rate)
            cfg.amp = rawProfile.amplitude
            cfg.clip_sigma = rawProfile.clip_sigma
            cfg.chirp_f0 = rawProfile.chirp_f0
            cfg.chirp_f1 = rawProfile.chirp_f1
            return cyrinx_bulk_compute_geometry(&cfg, &geom)
        }
        guard rcGeom == 0 else {
            throw CyrinxError.invalidArgument
        }
        return geom
    }

    private static func performDecode(
        _ profile: UnsafePointer<cyrinx_profile_t>,
        _ input: UnsafePointer<cyrinx_batch_input_layout_t>,
        _ payload: inout Data,
        _ result: UnsafeMutablePointer<cyrinx_batch_result_t>
    ) throws -> Int32 {
        let rc = payload.withUnsafeMutableBytes { payloadBytes in
            cyrinx_batch_decode(
                profile,
                input,
                payloadBytes.bindMemory(to: UInt8.self).baseAddress,
                payloadBytes.count,
                result
            )
        }
        if rc.rawValue != 0 {
            throw CyrinxError.status(rc.rawValue)
        }
        return rc.rawValue
    }

    public static func encode(
        profile: CyrinxProfile,
        payload: Data,
        outCap: Int? = nil
    ) throws -> (samples: [Float], result: CyrinxBatchResult) {
        var rawProfile = profile.toCStruct()
        let geom = try computeGeometry(profile: profile, rawProfile: rawProfile)

        let cap = outCap ?? Int(geom.frame_samples)
        var samples = [Float](repeating: 0.0, count: cap)
        var rawResult = cyrinx_batch_result_t()
        rawResult.struct_size = MemoryLayout<cyrinx_batch_result_t>.size
        rawResult.abi_version = UInt32(CYRINX_BATCH_RESULT_ABI_VERSION)

        _ = try payload.withUnsafeBytes { payloadBytes in
            try samples.withUnsafeMutableBufferPointer { samplesPtr in
                let rc = cyrinx_batch_encode(
                    &rawProfile,
                    payloadBytes.bindMemory(to: UInt8.self).baseAddress,
                    payload.count,
                    samplesPtr.baseAddress,
                    samplesPtr.count,
                    &rawResult
                )
                if rc.rawValue != 0 {
                    throw CyrinxError.status(rc.rawValue)
                }
                return rc.rawValue
            }
        }

        return (
            samples: Array(samples.prefix(Int(rawResult.produced_samples))), result: makeResult(rawResult)
        )
    }

    private static func makeResult(_ r: cyrinx_batch_result_t) -> CyrinxBatchResult {
        var hashBytes = [UInt8]()
        hashBytes.reserveCapacity(32)
        withUnsafeBytes(of: r.profile_hash) { ptr in
            hashBytes.append(contentsOf: ptr.prefix(32))
        }

        var maskBytes = [UInt8]()
        maskBytes.reserveCapacity(256)
        withUnsafeBytes(of: r.block_valid_mask) { ptr in
            maskBytes.append(contentsOf: ptr.prefix(256))
        }

        return CyrinxBatchResult(
            profileID: r.profile_id,
            profileHash: Data(hashBytes),
            selectedReceiver: r.selected_receiver,
            selectionReason: r.selection_reason,
            primaryHoldoutPilotRMS: r.primary_holdout_pilot_rms,
            mrcHoldoutPilotRMS: r.mrc_holdout_pilot_rms,
            evmRMS: r.evm_rms,
            blocksOk: r.blocks_ok,
            blocksTotal: r.blocks_total,
            blockValidMask: Data(maskBytes),
            consumedSamples: Int(r.consumed_samples),
            producedSamples: Int(r.produced_samples),
            clippingEvidence: r.clipping_evidence,
            nonfiniteEvidence: r.nonfinite_evidence,
            propagationDelayMs: r.propagation_delay_ms,
            symbolTimingError: r.symbol_timing_error
        )
    }
}
