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

typedef enum { CYRINX_PHY_STUB_OFDM_QPSK = 0, CYRINX_PHY_STUB_DCSS = 1 } cyrinx_phy_stub_mode_t;

typedef struct {
    cyrinx_phy_stub_mode_t mode;
    uint32_t sample_rate_hz;
    uint16_t fft_size;
    uint16_t cp_samples;
} cyrinx_phy_stub_config_t;

typedef struct {
    cyrinx_phy_stub_mode_t mode;
    uint64_t tx_symbol_index;
    uint64_t rx_symbol_index;
    uint8_t initialized;
} cyrinx_phy_stub_state_t;

/* Generate a root Zadoff-Chu sequence (N should be prime and root coprime to N). */
CYRINX_API int cyrinx_zc_generate(uint16_t root, uint16_t length, cyrinx_complex_f32_t *out, size_t out_len);

/* Estimate CFO from two consecutive ZC correlation peaks. */
CYRINX_API float cyrinx_estimate_cfo_hz(const cyrinx_complex_f32_t *first_peak,
                                        const cyrinx_complex_f32_t *second_peak, uint16_t zc_length,
                                        uint32_t sample_rate_hz);

/* Dynamic CP selection hook for UX-driven channel shaping (e.g., cloth/mousepad path damping). */
CYRINX_API uint16_t cyrinx_select_cp_samples(const cyrinx_config_t *config, float measured_delay_spread_ms,
                                             uint8_t phone_static);

/*
 * Deterministic PHY DSP stubs.
 *
 * These are not production waveform implementations; they provide stable, testable interfaces for
 * future vDSP-backed OFDM and D-CSS blocks while supporting golden-vector CI.
 */
CYRINX_API void cyrinx_phy_stub_default_config(cyrinx_phy_stub_mode_t mode,
                                               cyrinx_phy_stub_config_t *out_config);
CYRINX_API int cyrinx_phy_modulate_stub(const cyrinx_phy_stub_config_t *config, const uint8_t *symbols,
                                        size_t symbol_len, cyrinx_complex_f32_t *out_samples,
                                        size_t *inout_sample_len);
CYRINX_API int cyrinx_phy_demodulate_stub(const cyrinx_phy_stub_config_t *config,
                                          const cyrinx_complex_f32_t *samples, size_t sample_len,
                                          uint8_t *out_symbols, size_t *inout_symbol_len);

/*
 * Stateful sequential variants for chunked processing.
 *
 * Symbol index state is carried across calls, so chunked mod/demod sequences are deterministic
 * and equivalent to monolithic processing when state is reset once and reused in-order.
 */
CYRINX_API void cyrinx_phy_stub_state_reset(cyrinx_phy_stub_state_t *state, cyrinx_phy_stub_mode_t mode);
CYRINX_API int cyrinx_phy_modulate_stub_seq(const cyrinx_phy_stub_config_t *config,
                                            cyrinx_phy_stub_state_t *state, const uint8_t *symbols,
                                            size_t symbol_len, cyrinx_complex_f32_t *out_samples,
                                            size_t *inout_sample_len);
CYRINX_API int cyrinx_phy_demodulate_stub_seq(const cyrinx_phy_stub_config_t *config,
                                              cyrinx_phy_stub_state_t *state,
                                              const cyrinx_complex_f32_t *samples, size_t sample_len,
                                              uint8_t *out_symbols, size_t *inout_symbol_len);

#ifdef __cplusplus
}
#endif

#endif
