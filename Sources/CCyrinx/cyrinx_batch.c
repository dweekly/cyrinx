#include "cyrinx/cyrinx_batch.h"
#include "cyrinx/cyrinx_bulk.h"
#include <string.h>
#include <stdlib.h>
#include <math.h>

#if defined(_WIN32)
#else
#include <stdint.h>
#endif

static const char *batch_code_rate_to_str(cyrinx_code_rate_t cr) {
    switch (cr) {
    case CYRINX_CODE_RATE_1_2:
        return "1/2";
    case CYRINX_CODE_RATE_2_3:
        return "2/3";
    case CYRINX_CODE_RATE_3_4:
        return "3/4";
    case CYRINX_CODE_RATE_5_6:
        return "5/6";
    default:
        return NULL;
    }
}

static int batch_modulation_to_bits(cyrinx_modulation_t mod) {
    switch (mod) {
    case CYRINX_MODULATION_BPSK:
        return 1;
    case CYRINX_MODULATION_QPSK:
        return 2;
    case CYRINX_MODULATION_16QAM:
        return 4;
    case CYRINX_MODULATION_64QAM:
        return 6;
    case CYRINX_MODULATION_256QAM:
        return 8;
    default:
        return -1;
    }
}

static void map_profile_to_config(const cyrinx_profile_t *p, cyrinx_bulk_config *cfg) {
    memset(cfg, 0, sizeof(cyrinx_bulk_config));
    cfg->f_lo = p->low_frequency_hz;
    cfg->f_hi = p->high_frequency_hz;
    cfg->pilot_every = (int)p->pilot_every;
    cfg->bits_per_bin = batch_modulation_to_bits((cyrinx_modulation_t)p->modulation);
    cfg->rate = batch_code_rate_to_str((cyrinx_code_rate_t)p->code_rate);
    cfg->n_sym = (int)p->symbol_count;
    cfg->nfft = (int)p->fft_size;
    cfg->cp = (int)p->cyclic_prefix;
    cfg->sr = (int)p->sample_rate;
    cfg->amp = p->amplitude;
    cfg->clip_sigma = p->clip_sigma;
    cfg->chirp_f0 = p->chirp_f0;
    cfg->chirp_f1 = p->chirp_f1;
}

CYRINX_API cyrinx_status_t cyrinx_batch_decode(const cyrinx_profile_t *profile,
                                               const cyrinx_batch_input_layout_t *input, uint8_t *out_payload,
                                               size_t out_cap, cyrinx_batch_result_t *out_result) {
    if (profile == NULL || input == NULL || out_payload == NULL) {
        return CYRINX_ERR_INVALID_ARGUMENT;
    }

    if (!CYRINX_VALIDATE_ABI(profile, cyrinx_profile_t, CYRINX_PROFILE_ABI_VERSION)) {
        return CYRINX_ERR_INVALID_ARGUMENT;
    }

    if (!CYRINX_VALIDATE_ABI(input, cyrinx_batch_input_layout_t, CYRINX_BATCH_INPUT_LAYOUT_ABI_VERSION)) {
        return CYRINX_ERR_INVALID_ARGUMENT;
    }

    if (out_result != NULL) {
        if (!CYRINX_VALIDATE_ABI(out_result, cyrinx_batch_result_t, CYRINX_BATCH_RESULT_ABI_VERSION)) {
            return CYRINX_ERR_INVALID_ARGUMENT;
        }
    }

    cyrinx_bulk_config cfg;
    map_profile_to_config(profile, &cfg);

    cyrinx_bulk_geometry geom;
    if (cyrinx_bulk_compute_geometry(&cfg, &geom) != 0) {
        return CYRINX_ERR_INVALID_ARGUMENT;
    }

    if (out_cap < (size_t)geom.payload_bytes) {
        return CYRINX_ERR_BUFFER_TOO_SMALL;
    }

    if (input->primary_samples == NULL) {
        return CYRINX_ERR_INVALID_ARGUMENT;
    }

    size_t frame_samples = (size_t)geom.frame_samples;
    if (input->primary_count < frame_samples) {
        return CYRINX_ERR_INVALID_ARGUMENT;
    }

    // Safety checks on incoming samples
    uint32_t clipping_evidence = 0;
    uint32_t nonfinite_evidence = 0;

    for (size_t i = 0; i < input->primary_count; i++) {
        float val = input->primary_samples[i];
        if (isnan(val) || isinf(val)) {
            nonfinite_evidence++;
        } else if (fabs(val) >= 0.999f) {
            clipping_evidence++;
        }
    }

    if (input->secondary_samples != NULL && input->secondary_samples != input->primary_samples) {
        for (size_t i = 0; i < input->secondary_count; i++) {
            float val = input->secondary_samples[i];
            if (isnan(val) || isinf(val)) {
                nonfinite_evidence++;
            } else if (fabs(val) >= 0.999f) {
                clipping_evidence++;
            }
        }
    }

    // Extract channel samples (full capture duration)
    size_t primary_count = input->primary_count;
    float *rx = (float *)malloc(primary_count * sizeof(float));
    float *rx2 = NULL;
    if (rx == NULL) {
        return CYRINX_ERR_INTERNAL;
    }

    size_t primary_stride = (input->channel_stride == 0) ? 1 : input->channel_stride;
    for (size_t i = 0; i < primary_count; i++) {
        rx[i] = input->primary_samples[i * primary_stride];
    }

    int has_secondary = 0;
    if (input->channel_count >= 2) {
        if (input->secondary_samples != NULL) {
            has_secondary = 1;
        } else if (primary_stride > 1) {
            has_secondary = 1;
        }
    }

    if (has_secondary) {
        rx2 = (float *)malloc(primary_count * sizeof(float));
        if (rx2 == NULL) {
            free(rx);
            return CYRINX_ERR_INTERNAL;
        }

        const float *sec_ptr = input->secondary_samples;
        size_t secondary_stride = primary_stride;
        if (sec_ptr == NULL) {
            sec_ptr = input->primary_samples + 1;
        }

        for (size_t i = 0; i < primary_count; i++) {
            rx2[i] = sec_ptr[i * secondary_stride];
        }
    }

    int blocks_ok = 0;
    int blocks_total = 0;
    double evm_rms = 0.0;
    uint8_t block_valid_mask[256];
    memset(block_valid_mask, 0, sizeof(block_valid_mask));

    cyrinx_bulk_diversity_diagnostics diag;
    memset(&diag, 0, sizeof(diag));
    diag.struct_size = sizeof(diag);
    diag.abi_version = CYRINX_BULK_DIVERSITY_DIAGNOSTICS_ABI_VERSION;

    long decode_res = cyrinx_bulk_demodulate2_auto_v1_with_block_validity(
        &cfg, rx, primary_count, rx2, rx2 ? primary_count : 0, out_payload, out_cap, &blocks_ok,
        &blocks_total, &evm_rms, block_valid_mask, 256, &diag);

    free(rx);
    if (rx2) {
        free(rx2);
    }

    if (decode_res < 0) {
        // Demodulation failed (e.g. synchronization failure)
        if (out_result != NULL) {
            out_result->profile_id = profile->id;
            memcpy(out_result->profile_hash, profile->hash, 32);
            out_result->selected_receiver = CYRINX_BULK_DIVERSITY_PRIMARY;
            out_result->selection_reason = CYRINX_BULK_AUTO_REASON_NOT_EVALUATED;
            out_result->primary_holdout_pilot_rms = 0.0;
            out_result->mrc_holdout_pilot_rms = 0.0;
            out_result->evm_rms = 0.0;
            out_result->blocks_ok = 0;
            out_result->blocks_total = geom.n_blocks;
            memset(out_result->block_valid_mask, 0, 256);
            out_result->consumed_samples = primary_count;
            out_result->produced_samples = 0;
            out_result->clipping_evidence = clipping_evidence;
            out_result->nonfinite_evidence = nonfinite_evidence;
            out_result->propagation_delay_ms = 0.0;
            out_result->symbol_timing_error = 0.0;
        }
        return CYRINX_ERR_CRC;
    }

    if (out_result != NULL) {
        out_result->profile_id = profile->id;
        memcpy(out_result->profile_hash, profile->hash, 32);

        if (rx2 != NULL) {
            out_result->selected_receiver = (uint32_t)diag.selected_receiver;
            out_result->selection_reason = (uint32_t)diag.selection_reason;
            out_result->primary_holdout_pilot_rms = diag.primary_holdout_pilot_rms;
            out_result->mrc_holdout_pilot_rms = diag.mrc_holdout_pilot_rms;
        } else {
            out_result->selected_receiver = CYRINX_BULK_DIVERSITY_PRIMARY;
            out_result->selection_reason = CYRINX_BULK_AUTO_REASON_SECOND_UNAVAILABLE;
            out_result->primary_holdout_pilot_rms = 0.0;
            out_result->mrc_holdout_pilot_rms = 0.0;
        }

        out_result->evm_rms = evm_rms;
        out_result->blocks_ok = blocks_ok;
        out_result->blocks_total = blocks_total;
        memcpy(out_result->block_valid_mask, block_valid_mask, 256);

        out_result->consumed_samples = primary_count;
        out_result->produced_samples = (size_t)decode_res;
        out_result->clipping_evidence = clipping_evidence;
        out_result->nonfinite_evidence = nonfinite_evidence;
        out_result->propagation_delay_ms = 0.0;
        out_result->symbol_timing_error = 0.0;
    }

    return CYRINX_OK;
}

CYRINX_API cyrinx_status_t cyrinx_batch_encode(const cyrinx_profile_t *profile, const uint8_t *payload,
                                               size_t payload_len, float *out_samples, size_t out_cap,
                                               cyrinx_batch_result_t *out_result) {
    if (profile == NULL || payload == NULL || out_samples == NULL) {
        return CYRINX_ERR_INVALID_ARGUMENT;
    }

    if (!CYRINX_VALIDATE_ABI(profile, cyrinx_profile_t, CYRINX_PROFILE_ABI_VERSION)) {
        return CYRINX_ERR_INVALID_ARGUMENT;
    }

    if (out_result != NULL) {
        if (!CYRINX_VALIDATE_ABI(out_result, cyrinx_batch_result_t, CYRINX_BATCH_RESULT_ABI_VERSION)) {
            return CYRINX_ERR_INVALID_ARGUMENT;
        }
    }

    cyrinx_bulk_config cfg;
    map_profile_to_config(profile, &cfg);

    cyrinx_bulk_geometry geom;
    if (cyrinx_bulk_compute_geometry(&cfg, &geom) != 0) {
        return CYRINX_ERR_INVALID_ARGUMENT;
    }

    if (payload_len > (size_t)geom.payload_bytes) {
        return CYRINX_ERR_INVALID_ARGUMENT;
    }

    if (out_cap < (size_t)geom.frame_samples) {
        return CYRINX_ERR_BUFFER_TOO_SMALL;
    }

    const uint8_t *payload_ptr = payload;
    uint8_t *padded_payload = NULL;
    if (payload_len < (size_t)geom.payload_bytes) {
        padded_payload = (uint8_t *)calloc(geom.payload_bytes, 1);
        if (padded_payload == NULL) {
            return CYRINX_ERR_INTERNAL;
        }
        memcpy(padded_payload, payload, payload_len);
        payload_ptr = padded_payload;
    }

    long samples_written =
        cyrinx_bulk_modulate(&cfg, payload_ptr, geom.payload_bytes, out_samples, out_cap, NULL);
    if (padded_payload) {
        free(padded_payload);
    }

    if (samples_written < 0) {
        return CYRINX_ERR_INTERNAL;
    }

    if (out_result != NULL) {
        out_result->profile_id = profile->id;
        memcpy(out_result->profile_hash, profile->hash, 32);
        out_result->selected_receiver = CYRINX_BULK_DIVERSITY_PRIMARY;
        out_result->selection_reason = CYRINX_BULK_AUTO_REASON_NOT_EVALUATED;
        out_result->primary_holdout_pilot_rms = 0.0;
        out_result->mrc_holdout_pilot_rms = 0.0;
        out_result->evm_rms = 0.0;
        out_result->blocks_ok = geom.n_blocks;
        out_result->blocks_total = geom.n_blocks;
        memset(out_result->block_valid_mask, 1, geom.n_blocks);
        if (geom.n_blocks < 256) {
            memset(out_result->block_valid_mask + geom.n_blocks, 0, 256 - geom.n_blocks);
        }
        out_result->consumed_samples = 0;
        out_result->produced_samples = (size_t)samples_written;

        // Safety scan of produced samples
        uint32_t clip_cnt = 0;
        uint32_t nonfin_cnt = 0;
        for (long i = 0; i < samples_written; i++) {
            float s = out_samples[i];
            if (isnan(s) || isinf(s)) {
                nonfin_cnt++;
            } else if (fabs(s) >= 0.999f) {
                clip_cnt++;
            }
        }
        out_result->clipping_evidence = clip_cnt;
        out_result->nonfinite_evidence = nonfin_cnt;
        out_result->propagation_delay_ms = 0.0;
        out_result->symbol_timing_error = 0.0;
    }

    return CYRINX_OK;
}
