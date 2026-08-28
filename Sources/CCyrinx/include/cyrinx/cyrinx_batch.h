/* Cyrinx 3.0 batch capture contract (C3-05).
 *
 * One-shot encode/decode of a complete capture against a validated profile,
 * wrapping the 2.x bulk PHY behind versioned, fixed-width structures.
 *
 * Channel model: each input channel is an explicit strided view — base
 * pointer, raw sample count, first-sample offset, stride, role. Mono, planar
 * stereo, and interleaved stereo all use the same checked frame-count
 * formula; nothing is inferred from pointer aliasing. Views may alias the
 * same backing storage (interleaved stereo is two views into one buffer with
 * offsets 0 and 1 and stride 2).
 *
 * Sample-count vocabulary: "raw" counts index the backing storage; "logical"
 * counts are per-channel samples after applying offset and stride. A view
 * with raw sample_count N, first_sample F < N, and stride S yields
 * ceil((N - F) / S) logical samples.
 *
 * Block validity is caller-provided storage, sized by the profile's block
 * count with no fixed maximum. On CYRINX_STATUS_ERR_BUFFER_TOO_SMALL the
 * result's capacity fields (payload_bytes_required, block_validity_required,
 * blocks_total, logical_* fields) are written so the caller can resize; the
 * payload and validity buffers themselves are untouched. On every other
 * rejection no output is written at all.
 *
 * Decode outcome classes: a capture that demodulates returns
 * CYRINX_STATUS_OK with the ordered validity mask recording exactly which
 * blocks were CRC-valid — zero valid blocks (an unsyncable or silent
 * capture) is still an ordered OK result, which is the
 * partial-block-recovery contract. CYRINX_STATUS_ERR_DECODE is reserved for
 * the demodulator failing to produce a result at all; the result then
 * carries the retained evidence (selection, input evidence) with produced
 * bytes zero.
 *
 * The v1 result reports no timing fields: propagation delay and symbol
 * timing are not measured by this decode path, and placeholders must not be
 * represented as measurements.
 */
#ifndef CYRINX_BATCH_H
#define CYRINX_BATCH_H

#include "cyrinx_base.h"
#include "cyrinx_profiles.h"

#ifdef __cplusplus
extern "C" {
#endif

#define CYRINX_BATCH_ROLE_PRIMARY 0u
#define CYRINX_BATCH_ROLE_SECONDARY 1u

typedef struct cyrinx_batch_channel_view {
    const float *samples;  /* base pointer of the backing storage */
    uint64_t sample_count; /* raw samples readable from `samples` */
    uint64_t first_sample; /* raw offset of this channel's first sample */
    uint32_t stride;       /* >= 1 raw samples between logical samples */
    uint32_t role;         /* CYRINX_BATCH_ROLE_* */
} cyrinx_batch_channel_view_t;

#define CYRINX_BATCH_INPUT_ABI_VERSION 1u
#define CYRINX_BATCH_INPUT_V1_SIZE 80u

typedef struct cyrinx_batch_input {
    uint32_t struct_size;
    uint32_t abi_version;
    uint32_t channel_count; /* 1 (primary) or 2 (primary + secondary) */
    uint32_t reserved0;     /* must be zero */
    cyrinx_batch_channel_view_t channels[2];
} cyrinx_batch_input_t;

#define CYRINX_BATCH_RESULT_ABI_VERSION 1u
#define CYRINX_BATCH_RESULT_V1_SIZE 136u

typedef struct cyrinx_batch_result {
    uint32_t struct_size;
    uint32_t abi_version;

    uint32_t profile_id;
    uint32_t selected_receiver;       /* CYRINX_BULK_DIVERSITY_* */
    uint32_t selection_reason;        /* CYRINX_BULK_AUTO_REASON_* */
    uint32_t blocks_total;            /* profile block count */
    uint32_t blocks_ok;               /* CRC-valid blocks */
    uint32_t block_validity_required; /* caller capacity needed, in bytes */

    uint64_t payload_bytes_required;  /* decode: caller payload capacity needed */
    uint64_t logical_frame_samples;   /* logical samples one frame occupies;
                                       * also encode's required out_samples
                                       * capacity */
    uint64_t logical_samples_decoded; /* decode: per-channel logical samples
                                       * handed to the demodulator (the
                                       * aligned minimum across channels) */
    uint64_t payload_bytes_produced;  /* decode only; 0 for encode */
    uint64_t samples_produced;        /* encode only; 0 for decode */

    uint32_t clipping_evidence;  /* |sample| >= 0.999 among decoded samples */
    uint32_t nonfinite_evidence; /* NaN/Inf among decoded samples */

    double evm_rms;
    double primary_holdout_pilot_rms;
    double mrc_holdout_pilot_rms;

    uint8_t profile_identity_sha256[32];
} cyrinx_batch_result_t;

/* Decode one capture. The profile must pass cyrinx_profile_validate (a stale
 * identity digest is rejected). `block_validity` is required caller storage:
 * on success entry [i] is exactly 0 or 1 for block i in payload order, for
 * all blocks_total entries. Error precedence: NULL pointers, then profile
 * validation, then input-layout validation, then capacity
 * (CYRINX_STATUS_ERR_BUFFER_TOO_SMALL, capacity fields written to
 * out_result), then decode (CYRINX_STATUS_ERR_DECODE, evidence written). */
CYRINX_API cyrinx_abi_status_t cyrinx_batch_decode(const cyrinx_profile_t *profile,
                                                   const cyrinx_batch_input_t *input, uint8_t *out_payload,
                                                   size_t out_payload_cap, uint8_t *block_validity,
                                                   size_t block_validity_cap,
                                                   cyrinx_batch_result_t *out_result);

/* Encode one frame. A payload shorter than the profile's frame payload is
 * zero-padded to the frame boundary (documented contract, mirrored by the
 * decode side returning the padded frame payload). Writes the frame's samples
 * to out_samples and, when out_result is non-NULL, the produced counts. */
CYRINX_API cyrinx_abi_status_t cyrinx_batch_encode(const cyrinx_profile_t *profile, const uint8_t *payload,
                                                   size_t payload_len, float *out_samples, size_t out_cap,
                                                   cyrinx_batch_result_t *out_result);

#ifdef __cplusplus
}
#endif

#endif /* CYRINX_BATCH_H */
