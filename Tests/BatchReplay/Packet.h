#ifndef CYRINX_REPLAY_PACKET_H
#define CYRINX_REPLAY_PACKET_H

#include "cyrinx/cyrinx_batch.h"
#include <string.h>

// Every binding writes the same little-endian record for one decode: a fixed header, then the
// ordered block mask and the payload when the decode returned CYRINX_STATUS_OK. The runner
// compares records field by field; see scripts/check-batch-replay.py.
enum { REPLAY_HEADER_BYTES = 124 };

static inline void replay_put32(uint8_t *out, uint32_t value) {
    for (int i = 0; i < 4; i++)
        out[i] = (uint8_t)(value >> (8 * i));
}

static inline void replay_put64(uint8_t *out, uint64_t value) {
    for (int i = 0; i < 8; i++)
        out[i] = (uint8_t)(value >> (8 * i));
}

static inline void replay_put_double(uint8_t *out, double value) {
    uint64_t bits;
    memcpy(&bits, &value, sizeof bits);
    replay_put64(out, bits);
}

static inline void replay_header(uint8_t out[REPLAY_HEADER_BYTES], int32_t status,
                                 const cyrinx_batch_result_t *result) {
    memset(out, 0, REPLAY_HEADER_BYTES);
    replay_put32(out, (uint32_t)status);
    memcpy(out + 4, result->profile_identity_sha256, 32);
    // Every field of cyrinx_batch_result_t except the caller's own struct_size and abi_version
    // and the encode-only samples_produced.
    replay_put32(out + 36, result->profile_id);
    replay_put32(out + 40, result->selected_receiver);
    replay_put32(out + 44, result->selection_reason);
    replay_put32(out + 48, result->blocks_total);
    replay_put32(out + 52, result->blocks_ok);
    replay_put32(out + 56, result->block_validity_required);
    replay_put32(out + 60, result->clipping_evidence);
    replay_put32(out + 64, result->nonfinite_evidence);
    replay_put64(out + 68, result->payload_bytes_required);
    replay_put64(out + 76, result->logical_frame_samples);
    replay_put64(out + 84, result->logical_samples_decoded);
    replay_put64(out + 92, result->payload_bytes_produced);
    replay_put_double(out + 100, result->evm_rms);
    replay_put_double(out + 108, result->primary_holdout_pilot_rms);
    replay_put_double(out + 116, result->mrc_holdout_pilot_rms);
}
#endif
