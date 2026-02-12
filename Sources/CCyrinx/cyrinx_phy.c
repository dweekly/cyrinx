#include "include/cyrinx/cyrinx_phy.h"

#include <math.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

int cyrinx_zc_generate(uint16_t root, uint16_t length, cyrinx_complex_f32_t *out, size_t out_len) {
    if (!out || length == 0 || out_len < length || root == 0) {
        return CYRINX_ERR_INVALID_ARGUMENT;
    }

    const float n_f = (float)length;
    for (uint16_t n = 0; n < length; ++n) {
        float n_term = (float)n;
        /* Standard CAZAC phase progression for root-ZC sequence generation. */
        float phase = -(float)M_PI * (float)root * n_term * (n_term + 1.0f) / n_f;
        out[n].re = cosf(phase);
        out[n].im = sinf(phase);
    }

    return CYRINX_OK;
}

float cyrinx_estimate_cfo_hz(const cyrinx_complex_f32_t *first_peak, const cyrinx_complex_f32_t *second_peak,
                             uint16_t zc_length, uint32_t sample_rate_hz) {
    if (!first_peak || !second_peak || zc_length == 0 || sample_rate_hz == 0) {
        return 0.0f;
    }

    /* phase(c2 * conj(c1)) gives accumulated phase rotation between repeated blocks. */
    float re = (second_peak->re * first_peak->re) + (second_peak->im * first_peak->im);
    float im = (second_peak->im * first_peak->re) - (second_peak->re * first_peak->im);

    float phase = atan2f(im, re);
    float delta_t = (float)zc_length / (float)sample_rate_hz;
    if (delta_t <= 0.0f) {
        return 0.0f;
    }

    return phase / (2.0f * (float)M_PI * delta_t);
}

uint16_t cyrinx_select_cp_samples(const cyrinx_config_t *config, float measured_delay_spread_ms,
                                  uint8_t phone_static) {
    if (!config) {
        return 96u;
    }

    uint16_t cp_default = config->ofdm_cp_samples_default ? config->ofdm_cp_samples_default : 96u;
    uint16_t cp_min = config->ofdm_cp_samples_min ? config->ofdm_cp_samples_min : 10u;
    if (cp_min > cp_default) {
        cp_min = cp_default;
    }

    if (!config->enable_dynamic_cp) {
        return cp_default;
    }

    if (measured_delay_spread_ms < 0.0f) {
        measured_delay_spread_ms = 0.0f;
    }

    /*
     * CP budget is sized from delay spread plus safety margin.
     * Static phone geometry allows a tighter SFBC-friendly CP budget.
     */
    float delay_samples = measured_delay_spread_ms * ((float)config->sample_rate_hz / 1000.0f);
    float safety = 1.25f;
    uint16_t needed = (uint16_t)ceilf(delay_samples * safety);

    if (config->enable_sfbc_static_mode && phone_static) {
        /* In static geometry with SFBC, allow a tighter CP budget. */
        needed = (uint16_t)ceilf((float)needed * 0.8f);
    }

    if (needed < cp_min) {
        return cp_min;
    }
    if (needed > cp_default) {
        return cp_default;
    }
    return needed;
}
