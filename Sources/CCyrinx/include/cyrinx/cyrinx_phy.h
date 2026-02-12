#ifndef CYRINX_PHY_H
#define CYRINX_PHY_H

#include <stddef.h>
#include <stdint.h>

#include "cyrinx.h"

#ifdef __cplusplus
extern "C" {
#endif

#define CYRINX_OFDM_FFT_SIZE 1024u
#define CYRINX_OFDM_SUBCARRIER_SPACING_HZ 46.875f
#define CYRINX_OFDM_ACTIVE_SUBCARRIERS 106u

typedef struct {
    float re;
    float im;
} cyrinx_complex_f32_t;

/* Generate a root Zadoff-Chu sequence (N should be prime and root coprime to N). */
CYRINX_API int cyrinx_zc_generate(uint16_t root, uint16_t length, cyrinx_complex_f32_t *out, size_t out_len);

/* Estimate CFO from two consecutive ZC correlation peaks. */
CYRINX_API float cyrinx_estimate_cfo_hz(const cyrinx_complex_f32_t *first_peak,
                                        const cyrinx_complex_f32_t *second_peak, uint16_t zc_length,
                                        uint32_t sample_rate_hz);

/* Dynamic CP selection hook for UX-driven channel shaping (e.g., cloth/mousepad path damping). */
CYRINX_API uint16_t cyrinx_select_cp_samples(const cyrinx_config_t *config, float measured_delay_spread_ms,
                                             uint8_t phone_static);

#ifdef __cplusplus
}
#endif

#endif
