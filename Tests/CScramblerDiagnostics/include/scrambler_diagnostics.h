/* Test support: statistics of the frame a v2 profile transmits, measured before
 * the transmitter's clip can act. See diagnostics.c. */
#ifndef CYRINX_SCRAMBLER_DIAGNOSTICS_H
#define CYRINX_SCRAMBLER_DIAGNOSTICS_H

#include "cyrinx/cyrinx_profiles.h"

typedef struct {
    /* Largest sample magnitude of the multicarrier section over its standard
     * deviation, with the clip set to CYRINX_DIAGNOSTIC_CLIP_SIGMA. A frame
     * whose peak the clip would have reached reports at least that value. */
    double peak_to_rms;
    /* Share of data bins, over the whole frame, that carry the most common
     * constellation point. */
    double commonest_point_share;
    /* Data bins counted. */
    uint64_t data_bins;
} cyrinx_transmit_statistics_t;

/* The widest clip the codec accepts. */
#define CYRINX_DIAGNOSTIC_CLIP_SIGMA 10.0

/* Modulate `payload`, a full frame, with `profile` through the same internal
 * entry point cyrinx_batch_encode_v2 uses, with the profile's own scrambler
 * mode and every parameter except the clip. Returns a CYRINX_STATUS_* value. */
int32_t cyrinx_transmit_statistics(const cyrinx_profile_v2_t *profile, const uint8_t *payload,
                                   size_t payload_len, cyrinx_transmit_statistics_t *out);

#endif /* CYRINX_SCRAMBLER_DIAGNOSTICS_H */
