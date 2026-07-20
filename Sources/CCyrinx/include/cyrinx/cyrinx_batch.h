#ifndef CYRINX_BATCH_H
#define CYRINX_BATCH_H

#include "cyrinx_base.h"
#include "cyrinx_profiles.h"

#if defined(_WIN32)
#if defined(CYRINX_BUILD)
#define CYRINX_API __declspec(dllexport)
#else
#define CYRINX_API __declspec(dllimport)
#endif
#else
#define CYRINX_API __attribute__((visibility("default")))
#endif

#ifdef __cplusplus
extern "C" {
#endif

#define CYRINX_BATCH_INPUT_LAYOUT_ABI_VERSION 1
#define CYRINX_BATCH_RESULT_ABI_VERSION 1

typedef struct cyrinx_batch_input_layout {
    size_t struct_size;
    uint32_t abi_version;

    const float *primary_samples;
    size_t primary_count;
    const float *secondary_samples;
    size_t secondary_count;

    uint32_t channel_count;
    uint32_t channel_stride; /* in samples */
    uint64_t monotonic_start_index;
    uint32_t discontinuity_flags;
} cyrinx_batch_input_layout_t;

typedef struct cyrinx_batch_result {
    size_t struct_size;
    uint32_t abi_version;

    uint32_t profile_id;
    uint8_t profile_hash[32];
    uint32_t selected_receiver; /* primary (0) vs MRC (1) vs none */
    uint32_t selection_reason;
    double primary_holdout_pilot_rms;
    double mrc_holdout_pilot_rms;
    double evm_rms;
    int32_t blocks_ok;
    int32_t blocks_total;
    uint8_t block_valid_mask[256];
    size_t consumed_samples;
    size_t produced_samples;
    uint32_t clipping_evidence;
    uint32_t nonfinite_evidence;
    double propagation_delay_ms;
    double symbol_timing_error;
} cyrinx_batch_result_t;

CYRINX_API cyrinx_status_t cyrinx_batch_decode(const cyrinx_profile_t *profile,
                                               const cyrinx_batch_input_layout_t *input, uint8_t *out_payload,
                                               size_t out_cap, cyrinx_batch_result_t *out_result);

CYRINX_API cyrinx_status_t cyrinx_batch_encode(const cyrinx_profile_t *profile, const uint8_t *payload,
                                               size_t payload_len, float *out_samples, size_t out_cap,
                                               cyrinx_batch_result_t *out_result);

#ifdef __cplusplus
}
#endif

#endif /* CYRINX_BATCH_H */
