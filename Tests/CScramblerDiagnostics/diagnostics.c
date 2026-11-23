/* Statistics of a transmitted frame, for the scrambler's peak gate.
 *
 * The public batch encoder returns the frame after the transmitter's clip, where
 * a tall peak has already been cut off. This calls the internal modulator the
 * batch encoder calls, with the same profile parameters and scrambler mode, and
 * with the clip at the widest setting the codec accepts. A frame whose peak is
 * below that setting is returned unclipped, so its peak is the true one; a
 * taller peak reports at least the setting, which is far above the gate.
 *
 * It reads two internal headers of the library by relative path. They are not
 * installed, so only a test in this repository can do this.
 */
#include "scrambler_diagnostics.h"

#include <math.h>
#include <stdlib.h>

#include "../../Sources/CCyrinx/cyrinx_bulk_internal.h"
#include "../../Sources/CCyrinx/cyrinx_profiles_internal.h"

static int compare_keys(const void *a, const void *b) {
    int64_t x = *(const int64_t *)a, y = *(const int64_t *)b;
    return (x > y) - (x < y);
}

int32_t cyrinx_transmit_statistics(const cyrinx_profile_v2_t *profile, const uint8_t *payload,
                                   size_t payload_len, cyrinx_transmit_statistics_t *out) {
    if (profile == NULL || payload == NULL || out == NULL) {
        return CYRINX_STATUS_ERR_INVALID_ARGUMENT;
    }
    cyrinx_abi_status_t status = cyrinx_profile_v2_validate(profile);
    if (status != CYRINX_STATUS_OK) {
        return status;
    }
    cyrinx_profile_t view;
    cyrinx_internal_profile_v1_view(profile, &view);
    cyrinx_bulk_config cfg;
    status = cyrinx_internal_profile_to_bulk_config(&view, &cfg);
    if (status != CYRINX_STATUS_OK) {
        return status;
    }
    cfg.clip_sigma = CYRINX_DIAGNOSTIC_CLIP_SIGMA;
    cyrinx_bulk_geometry geometry;
    if (cyrinx_bulk_compute_geometry(&cfg, &geometry) != 0 || payload_len != (size_t)geometry.payload_bytes) {
        return CYRINX_STATUS_ERR_INVALID_ARGUMENT;
    }

    size_t frame = (size_t)geometry.frame_samples;
    size_t bins = (size_t)cfg.n_sym * (size_t)geometry.n_used;
    float *wave = (float *)malloc(frame * sizeof(float));
    double *points = (double *)malloc(bins * 2 * sizeof(double));
    int64_t *keys = (int64_t *)malloc(bins * sizeof(int64_t));
    if (wave == NULL || points == NULL || keys == NULL) {
        free(wave);
        free(points);
        free(keys);
        return CYRINX_STATUS_ERR_INTERNAL;
    }
    long written = cyrinx_internal_bulk_modulate(&cfg, profile->payload_scrambler, payload, payload_len, wave,
                                                 frame, points);
    if (written != (long)frame) {
        free(wave);
        free(points);
        free(keys);
        return CYRINX_STATUS_ERR_INTERNAL;
    }

    /* Peak over standard deviation of everything after the chirp and guard,
     * which is the span the transmitter's clip is computed over. */
    size_t start = CYRINX_BULK_CHIRP_LEN + CYRINX_BULK_GUARD, count = frame - start;
    double mean = 0.0, peak = 0.0, variance = 0.0;
    for (size_t i = start; i < frame; ++i) {
        mean += wave[i];
    }
    mean /= (double)count;
    for (size_t i = start; i < frame; ++i) {
        double centred = wave[i] - mean, magnitude = fabs((double)wave[i]);
        variance += centred * centred;
        if (magnitude > peak) {
            peak = magnitude;
        }
    }
    out->peak_to_rms = peak / sqrt(variance / (double)count);

    /* The most common constellation point among data bins. A position within
     * the used band is a pilot when it is a multiple of pilot_every. Points are
     * at least 0.15 apart for every supported modulation, so a grid of 1/64
     * tells them apart. */
    size_t data = 0;
    for (size_t symbol = 0; symbol < (size_t)cfg.n_sym; ++symbol) {
        for (size_t k = 0; k < (size_t)geometry.n_used; ++k) {
            if (k % (size_t)cfg.pilot_every == 0) {
                continue;
            }
            size_t at = (symbol * (size_t)geometry.n_used + k) * 2;
            keys[data++] = llround(points[at] * 64.0) * 4096 + llround(points[at + 1] * 64.0);
        }
    }
    qsort(keys, data, sizeof(keys[0]), compare_keys);
    size_t longest = 0, run = 0;
    for (size_t i = 0; i < data; ++i) {
        run = (i > 0 && keys[i] == keys[i - 1]) ? run + 1 : 1;
        if (run > longest) {
            longest = run;
        }
    }
    out->commonest_point_share = data > 0 ? (double)longest / (double)data : 0.0;
    out->data_bins = data;

    free(wave);
    free(points);
    free(keys);
    return CYRINX_STATUS_OK;
}
