/* Internal (non-installed) bulk-PHY entry points that take a payload scrambler
 * mode. The 2.x cyrinx_bulk_config layout is frozen and has no room for the
 * mode, so the v2 batch layer reaches the modulator and demodulator through
 * these. The public 2.x entry points call them with
 * CYRINX_PAYLOAD_SCRAMBLER_NONE and are otherwise unchanged. Not part of the
 * public ABI surface. */
#ifndef CYRINX_BULK_INTERNAL_H
#define CYRINX_BULK_INTERNAL_H

#include "cyrinx/cyrinx_bulk.h"
#include "cyrinx/cyrinx_profiles.h"

/* True for a scrambler mode this library implements. */
bool cyrinx_internal_payload_scrambler_supported(uint32_t payload_scrambler);

/* As cyrinx_bulk_modulate, with the framed bits (each block's payload and CRC)
 * XORed with the mode's mask before convolutional coding. Returns -1 for an
 * unsupported mode. */
long cyrinx_internal_bulk_modulate(const cyrinx_bulk_config *cfg, uint32_t payload_scrambler,
                                   const uint8_t *payload, size_t payload_len, float *wave_out,
                                   size_t wave_cap, double *data_freq_out);

/* As cyrinx_bulk_demodulate2_auto_v1_with_block_validity, with the mode's mask
 * removed from the decoded framed bits before the CRC checks. Returns -1 for
 * an unsupported mode, before any output is written. */
long cyrinx_internal_bulk_demodulate2_auto_v1_with_block_validity(
    const cyrinx_bulk_config *cfg, uint32_t payload_scrambler, const float *rx, size_t rx_len,
    const float *rx2, size_t rx2_len, uint8_t *out_payload, size_t out_cap, int *blocks_ok, int *blocks_total,
    double *evm_rms, uint8_t *block_valid_out, size_t block_valid_cap,
    cyrinx_bulk_diversity_diagnostics *diagnostics);

#endif /* CYRINX_BULK_INTERNAL_H */
