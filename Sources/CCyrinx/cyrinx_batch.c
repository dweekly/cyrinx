/* Cyrinx 3.0 batch capture contract. See cyrinx/cyrinx_batch.h. */
#include "cyrinx/cyrinx_batch.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>

#include "cyrinx_profiles_internal.h"

_Static_assert(sizeof(cyrinx_batch_channel_view_t) == 32, "channel view v1 size");
_Static_assert(sizeof(cyrinx_batch_input_t) == CYRINX_BATCH_INPUT_V1_SIZE, "batch input v1 size");
_Static_assert(sizeof(cyrinx_batch_result_t) == CYRINX_BATCH_RESULT_V1_SIZE, "batch result v1 size");
_Static_assert(offsetof(cyrinx_batch_input_t, channels) == 16, "batch input v1 layout");
_Static_assert(offsetof(cyrinx_batch_result_t, payload_bytes_required) == 32, "batch result v1 layout");
_Static_assert(offsetof(cyrinx_batch_result_t, clipping_evidence) == 72, "batch result v1 layout");
_Static_assert(offsetof(cyrinx_batch_result_t, evm_rms) == 80, "batch result v1 layout");
_Static_assert(offsetof(cyrinx_batch_result_t, profile_identity_sha256) == 104, "batch result v1 layout");

/* Logical sample count of one strided view; 0 for an invalid view. */
static uint64_t view_logical_samples(const cyrinx_batch_channel_view_t *view) {
    if (view->samples == NULL || view->stride == 0 || view->first_sample >= view->sample_count) {
        return 0;
    }
    uint64_t reachable = view->sample_count - view->first_sample;
    return (reachable + view->stride - 1) / view->stride;
}

static bool validate_input_layout(const cyrinx_batch_input_t *input) {
    if (!cyrinx_abi_accepts(input, CYRINX_BATCH_INPUT_V1_SIZE, CYRINX_BATCH_INPUT_ABI_VERSION)) {
        return false;
    }
    if (input->reserved0 != 0) {
        return false;
    }
    if (input->channel_count < 1 || input->channel_count > 2) {
        return false;
    }
    if (input->channels[0].role != CYRINX_BATCH_ROLE_PRIMARY) {
        return false;
    }
    if (input->channel_count == 2 && input->channels[1].role != CYRINX_BATCH_ROLE_SECONDARY) {
        return false;
    }
    for (uint32_t c = 0; c < input->channel_count; ++c) {
        if (view_logical_samples(&input->channels[c]) == 0) {
            return false;
        }
    }
    return true;
}

/* Copy `count` logical samples of `view` into contiguous storage, counting
 * clipping and non-finite evidence over exactly the copied samples. */
static void extract_logical_samples(const cyrinx_batch_channel_view_t *view, uint64_t count, float *out,
                                    uint32_t *clipping, uint32_t *nonfinite) {
    for (uint64_t i = 0; i < count; ++i) {
        float sample = view->samples[view->first_sample + i * view->stride];
        out[i] = sample;
        if (isnan(sample) || isinf(sample)) {
            if (*nonfinite < UINT32_MAX) {
                ++*nonfinite;
            }
        } else if (fabsf(sample) >= 0.999f) {
            if (*clipping < UINT32_MAX) {
                ++*clipping;
            }
        }
    }
}

static void write_capacity_fields(cyrinx_batch_result_t *out_result, const cyrinx_profile_t *profile,
                                  const cyrinx_bulk_geometry *geometry, uint64_t logical_min) {
    if (out_result == NULL) {
        return;
    }
    uint32_t caller_size = out_result->struct_size;
    memset(out_result, 0, sizeof(*out_result));
    out_result->struct_size = caller_size;
    out_result->abi_version = CYRINX_BATCH_RESULT_ABI_VERSION;
    out_result->profile_id = profile->id;
    memcpy(out_result->profile_identity_sha256, profile->identity_sha256, 32);
    out_result->selection_reason = CYRINX_BULK_AUTO_REASON_NOT_EVALUATED;
    out_result->blocks_total = (uint32_t)geometry->n_blocks;
    out_result->block_validity_required = (uint32_t)geometry->n_blocks;
    out_result->payload_bytes_required = (uint64_t)geometry->payload_bytes;
    out_result->logical_frame_samples = (uint64_t)geometry->frame_samples;
    out_result->logical_samples_decoded = logical_min;
}

cyrinx_abi_status_t cyrinx_batch_decode(const cyrinx_profile_t *profile, const cyrinx_batch_input_t *input,
                                        uint8_t *out_payload, size_t out_payload_cap, uint8_t *block_validity,
                                        size_t block_validity_cap, cyrinx_batch_result_t *out_result) {
    if (profile == NULL || input == NULL || out_payload == NULL || block_validity == NULL) {
        return CYRINX_STATUS_ERR_INVALID_ARGUMENT;
    }
    cyrinx_abi_status_t status = cyrinx_profile_validate(profile);
    if (status != CYRINX_STATUS_OK) {
        return status;
    }
    if (!validate_input_layout(input)) {
        return CYRINX_STATUS_ERR_INVALID_ARGUMENT;
    }
    if (out_result != NULL &&
        !cyrinx_abi_accepts(out_result, CYRINX_BATCH_RESULT_V1_SIZE, CYRINX_BATCH_RESULT_ABI_VERSION)) {
        return CYRINX_STATUS_ERR_INVALID_ARGUMENT;
    }

    cyrinx_bulk_config cfg;
    status = cyrinx_internal_profile_to_bulk_config(profile, &cfg);
    if (status != CYRINX_STATUS_OK) {
        return status;
    }
    cyrinx_bulk_geometry geometry;
    if (cyrinx_bulk_compute_geometry(&cfg, &geometry) != 0 || geometry.n_blocks < 0 ||
        geometry.payload_bytes < 0 || geometry.frame_samples < 0) {
        return CYRINX_STATUS_ERR_INVALID_ARGUMENT;
    }

    /* Aligned logical sample count: the minimum across channels, so both
     * demodulator inputs cover the same time span. */
    uint64_t logical_min = view_logical_samples(&input->channels[0]);
    bool has_secondary = input->channel_count == 2;
    if (has_secondary) {
        uint64_t secondary = view_logical_samples(&input->channels[1]);
        if (secondary < logical_min) {
            logical_min = secondary;
        }
    }
    if (logical_min < (uint64_t)geometry.frame_samples) {
        return CYRINX_STATUS_ERR_INVALID_ARGUMENT;
    }
    if (logical_min > SIZE_MAX / sizeof(float)) {
        return CYRINX_STATUS_ERR_INVALID_ARGUMENT;
    }

    if (out_payload_cap < (size_t)geometry.payload_bytes || block_validity_cap < (size_t)geometry.n_blocks) {
        write_capacity_fields(out_result, profile, &geometry, logical_min);
        return CYRINX_STATUS_ERR_BUFFER_TOO_SMALL;
    }

    float *primary = (float *)malloc((size_t)logical_min * sizeof(float));
    if (primary == NULL) {
        return CYRINX_STATUS_ERR_INTERNAL;
    }
    float *secondary = NULL;
    if (has_secondary) {
        secondary = (float *)malloc((size_t)logical_min * sizeof(float));
        if (secondary == NULL) {
            free(primary);
            return CYRINX_STATUS_ERR_INTERNAL;
        }
    }

    uint32_t clipping = 0;
    uint32_t nonfinite = 0;
    extract_logical_samples(&input->channels[0], logical_min, primary, &clipping, &nonfinite);
    if (has_secondary) {
        extract_logical_samples(&input->channels[1], logical_min, secondary, &clipping, &nonfinite);
    }

    cyrinx_bulk_diversity_diagnostics diagnostics;
    memset(&diagnostics, 0, sizeof(diagnostics));
    diagnostics.struct_size = sizeof(diagnostics);
    diagnostics.abi_version = CYRINX_BULK_DIVERSITY_DIAGNOSTICS_ABI_VERSION;

    int blocks_ok = 0;
    int blocks_total = 0;
    double evm_rms = 0.0;
    long decode_result = cyrinx_bulk_demodulate2_auto_v1_with_block_validity(
        &cfg, primary, (size_t)logical_min, secondary, has_secondary ? (size_t)logical_min : 0, out_payload,
        out_payload_cap, &blocks_ok, &blocks_total, &evm_rms, block_validity, block_validity_cap,
        &diagnostics);

    free(primary);
    free(secondary);

    if (out_result != NULL) {
        write_capacity_fields(out_result, profile, &geometry, logical_min);
        out_result->clipping_evidence = clipping;
        out_result->nonfinite_evidence = nonfinite;
        out_result->selected_receiver = (uint32_t)diagnostics.selected_receiver;
        out_result->selection_reason = (uint32_t)diagnostics.selection_reason;
        out_result->primary_holdout_pilot_rms = diagnostics.primary_holdout_pilot_rms;
        out_result->mrc_holdout_pilot_rms = diagnostics.mrc_holdout_pilot_rms;
        if (decode_result >= 0) {
            out_result->evm_rms = evm_rms;
            out_result->blocks_ok = (uint32_t)blocks_ok;
            out_result->blocks_total = (uint32_t)blocks_total;
            out_result->payload_bytes_produced = (uint64_t)decode_result;
        }
    }

    return decode_result >= 0 ? CYRINX_STATUS_OK : CYRINX_STATUS_ERR_DECODE;
}

cyrinx_abi_status_t cyrinx_batch_encode(const cyrinx_profile_t *profile, const uint8_t *payload,
                                        size_t payload_len, float *out_samples, size_t out_cap,
                                        cyrinx_batch_result_t *out_result) {
    if (profile == NULL || payload == NULL || out_samples == NULL) {
        return CYRINX_STATUS_ERR_INVALID_ARGUMENT;
    }
    cyrinx_abi_status_t status = cyrinx_profile_validate(profile);
    if (status != CYRINX_STATUS_OK) {
        return status;
    }
    if (out_result != NULL &&
        !cyrinx_abi_accepts(out_result, CYRINX_BATCH_RESULT_V1_SIZE, CYRINX_BATCH_RESULT_ABI_VERSION)) {
        return CYRINX_STATUS_ERR_INVALID_ARGUMENT;
    }

    cyrinx_bulk_config cfg;
    status = cyrinx_internal_profile_to_bulk_config(profile, &cfg);
    if (status != CYRINX_STATUS_OK) {
        return status;
    }
    cyrinx_bulk_geometry geometry;
    if (cyrinx_bulk_compute_geometry(&cfg, &geometry) != 0 || geometry.n_blocks < 0 ||
        geometry.payload_bytes < 0 || geometry.frame_samples < 0) {
        return CYRINX_STATUS_ERR_INVALID_ARGUMENT;
    }

    if (payload_len > (size_t)geometry.payload_bytes) {
        return CYRINX_STATUS_ERR_INVALID_ARGUMENT;
    }
    if (out_cap < (size_t)geometry.frame_samples) {
        write_capacity_fields(out_result, profile, &geometry, 0);
        return CYRINX_STATUS_ERR_BUFFER_TOO_SMALL;
    }

    const uint8_t *frame_payload = payload;
    uint8_t *padded = NULL;
    if (payload_len < (size_t)geometry.payload_bytes) {
        padded = (uint8_t *)calloc((size_t)geometry.payload_bytes, 1);
        if (padded == NULL) {
            return CYRINX_STATUS_ERR_INTERNAL;
        }
        memcpy(padded, payload, payload_len);
        frame_payload = padded;
    }

    long samples_written =
        cyrinx_bulk_modulate(&cfg, frame_payload, (size_t)geometry.payload_bytes, out_samples, out_cap, NULL);
    free(padded);

    if (samples_written < 0) {
        return CYRINX_STATUS_ERR_INTERNAL;
    }

    if (out_result != NULL) {
        write_capacity_fields(out_result, profile, &geometry, 0);
        out_result->blocks_ok = (uint32_t)geometry.n_blocks;
        out_result->samples_produced = (uint64_t)samples_written;
    }

    return CYRINX_STATUS_OK;
}
