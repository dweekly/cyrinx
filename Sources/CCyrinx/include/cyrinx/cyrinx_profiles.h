#ifndef CYRINX_PROFILES_H
#define CYRINX_PROFILES_H

#include "cyrinx_base.h"
#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/*
 * ============================================================================
 * Profile Registry Enums
 * ============================================================================
 */

typedef enum {
    CYRINX_MODULATION_BPSK = 0,
    CYRINX_MODULATION_QPSK = 1,
    CYRINX_MODULATION_16QAM = 2,
    CYRINX_MODULATION_64QAM = 3,
    CYRINX_MODULATION_256QAM = 4
} cyrinx_modulation_t;

typedef enum {
    CYRINX_CODE_RATE_1_2 = 0,
    CYRINX_CODE_RATE_2_3 = 1,
    CYRINX_CODE_RATE_3_4 = 2,
    CYRINX_CODE_RATE_5_6 = 3
} cyrinx_code_rate_t;

typedef enum {
    CYRINX_CLASSIFICATION_COMPATIBILITY = 0,
    CYRINX_CLASSIFICATION_QUALIFIED = 1,
    CYRINX_CLASSIFICATION_EXPERIMENTAL = 2,
    CYRINX_CLASSIFICATION_INTERNAL_TEST_FIXTURE = 3
} cyrinx_classification_t;

/*
 * ============================================================================
 * Profile Structure (Versioned, stable C ABI layout)
 * ============================================================================
 */
#define CYRINX_PROFILE_ABI_VERSION 1

typedef struct cyrinx_profile {
    size_t struct_size;
    uint32_t abi_version;

    uint32_t id;
    uint32_t classification; /* cyrinx_classification_t */

    /* Modulation & Coding */
    uint32_t modulation; /* cyrinx_modulation_t */
    uint32_t code_rate;  /* cyrinx_code_rate_t */

    /* Band (Hz) */
    double low_frequency_hz;
    double high_frequency_hz;

    /* FFT & CP */
    uint32_t fft_size;
    uint32_t cyclic_prefix;
    uint32_t sample_rate;

    /* Pilot & Block/Symbols */
    uint32_t pilot_every;
    uint32_t symbol_count;
    uint32_t block_count;

    /* Extra transmitter constraints */
    double amplitude;
    double clip_sigma;
    double chirp_f0;
    double chirp_f1;

    /* Canonical Content Hash */
    uint8_t hash[32];
} cyrinx_profile_t;

/*
 * ============================================================================
 * Registry Functions
 * ============================================================================
 */

/* Returns the total number of prepopulated profiles in the registry. */
size_t cyrinx_profile_get_count(void);

/*
 * Retrieves a copy of a profile from the registry by its 0-based index.
 *
 * @param index The profile index (0 to cyrinx_profile_get_count() - 1).
 * @param out_profile Pointer to the structure to receive the profile.
 * @return CYRINX_OK on success, CYRINX_ERR_INVALID_ARGUMENT if out_profile is NULL
 *         or index is out of bounds.
 */
int cyrinx_profile_get_by_index(size_t index, cyrinx_profile_t *out_profile);

/*
 * Retrieves a copy of a profile from the registry by its unique ID.
 *
 * @param id The stable on-wire ID.
 * @param out_profile Pointer to the structure to receive the profile.
 * @return CYRINX_OK on success, CYRINX_ERR_INVALID_ARGUMENT if out_profile is NULL,
 *         or a status indicating the ID was not found.
 */
int cyrinx_profile_get_by_id(uint32_t id, cyrinx_profile_t *out_profile);

/*
 * Computes the canonical content hash from the configuration parameters of the profile.
 *
 * @param profile The profile structure to hash.
 * @param out_hash A 32-byte buffer to receive the hash.
 * @return CYRINX_OK on success, or CYRINX_ERR_INVALID_ARGUMENT if profile or out_hash is NULL.
 */
int cyrinx_profile_compute_hash(const cyrinx_profile_t *profile, uint8_t *out_hash);

/*
 * Validates a profile structure.
 * Checks struct size, ABI version, parameter ranges/compatibility, and verifies
 * that the stored hash matches the computed hash.
 *
 * @param profile The profile structure to validate.
 * @return CYRINX_OK if valid, or appropriate error codes.
 */
int cyrinx_profile_validate(const cyrinx_profile_t *profile);

#ifdef __cplusplus
}
#endif

#endif /* CYRINX_PROFILES_H */
