#ifndef CYRINX_REPLAY_PROFILES_H
#define CYRINX_REPLAY_PROFILES_H

#include "cyrinx/cyrinx_batch.h"
#include <stdlib.h>
#include <string.h>

// A probe is told its profile in one of two ways: a key naming one of the fixed test geometries
// below, or "registered:<id>" naming a profile in the library's registry. A registered profile
// is looked up through the v2 type, so the scrambled ones are reachable.
#define REPLAY_REGISTERED_PREFIX "registered:"

// Whether the argument is of the "registered:<id>" form at all. An argument of that form that
// names no registered profile is an error, never a test geometry.
static inline bool replay_names_registered(const char *argument) {
    return strncmp(argument, REPLAY_REGISTERED_PREFIX, strlen(REPLAY_REGISTERED_PREFIX)) == 0;
}

// Look up the registered profile a "registered:<id>" argument names. False for a malformed ID
// or one that is not registered.
static inline bool replay_registered(const char *argument, cyrinx_profile_v2_t *out) {
    const char *digits = argument + strlen(REPLAY_REGISTERED_PREFIX);
    char *end = NULL;
    unsigned long id = strtoul(digits, &end, 10);
    if (*digits < '0' || *digits > '9' || *end != '\0' || id > UINT32_MAX) {
        return false;
    }
    *out = (cyrinx_profile_v2_t){.struct_size = CYRINX_PROFILE_V2_SIZE,
                                 .abi_version = CYRINX_PROFILE_V2_ABI_VERSION};
    return cyrinx_profile_v2_get_by_id((uint32_t)id, out) == CYRINX_STATUS_OK;
}

// The key of a test geometry: decimal digits and nothing else. False for anything that is not,
// so that a mistyped argument is refused and never read as key zero.
static inline bool replay_key(const char *argument, int *out) {
    char *end = NULL;
    long key = strtol(argument, &end, 10);
    if (*argument < '0' || *argument > '9' || *end != '\0' || key > 1000) {
        return false;
    }
    *out = (int)key;
    return true;
}

// Fixed test geometries. These keys are local to the replay runner.
enum { REPLAY_QPSK = 0, REPLAY_DESK = 1, REPLAY_CONTROL = 2, REPLAY_QAM16 = 3 };

static inline bool replay_profile(int key, cyrinx_profile_t *out) {
    const struct {
        double low, high;
        uint32_t fft, cp, symbols, blocks;
    } rows[] = {{1101.5625, 22992.1875, 2048, 768, 4, 1},
                {12000, 23000, 4096, 2048, 27, 10},
                {300, 11000, 16384, 8192, 3, 4},
                {1101.5625, 22992.1875, 2048, 768, 4, 4}};
    if (key < 0 || (size_t)key >= sizeof(rows) / sizeof(rows[0])) {
        return false;
    }
    *out = (cyrinx_profile_t){.struct_size = sizeof(*out),
                              .abi_version = CYRINX_PROFILE_ABI_VERSION,
                              .id = 0xf000u + (uint32_t)key,
                              .classification = CYRINX_CLASSIFICATION_INTERNAL_TEST_FIXTURE,
                              .modulation =
                                  key == REPLAY_QAM16 ? CYRINX_MODULATION_16QAM : CYRINX_MODULATION_QPSK,
                              .code_rate = key == REPLAY_QAM16 ? CYRINX_CODE_RATE_3_4 : CYRINX_CODE_RATE_1_2,
                              .low_frequency_hz = rows[key].low,
                              .high_frequency_hz = rows[key].high,
                              .fft_size = rows[key].fft,
                              .cyclic_prefix = rows[key].cp,
                              .sample_rate = 48000,
                              .pilot_every = 8,
                              .symbol_count = rows[key].symbols,
                              .block_count = rows[key].blocks,
                              .amplitude = 0.5,
                              .clip_sigma = 3.3,
                              .chirp_f0 = 2000,
                              .chirp_f1 = 16000};
    return cyrinx_profile_compute_identity(out, out->identity_sha256) == CYRINX_STATUS_OK;
}
#endif
