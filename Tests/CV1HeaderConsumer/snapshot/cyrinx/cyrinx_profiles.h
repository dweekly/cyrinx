/* Cyrinx 3.0 profile registry and identity (C3-04).
 *
 * A profile freezes one complete physical-layer configuration behind a stable
 * on-wire ID and a 32-byte SHA-256 identity digest. The canonical registry
 * source is Tests/Fixtures/profiles/cyrinx_profiles_v1.json; the C table
 * below it is proved field-for-field and digest-for-digest against that JSON
 * by CyrinxProfileRegistryTests. (The Kotlin/JNI view of the same source is
 * a documented remaining C3-04 gate item, not yet present.)
 *
 * Identity boundary (recorded decision): the identity digest covers the
 * physical/wire parameters only — band, FFT/CP/sample rate, pilot spacing,
 * symbol count, modulation, code rate, and transmitter constraints. It
 * deliberately excludes: `id` (a registry key, not a wire parameter),
 * `classification` (mutable evidence metadata; requalifying a profile must
 * not change its wire identity), and `block_count` (derived from geometry).
 * The digest is SHA-256 over the domain-separated big-endian serialization
 * produced by cyrinx_profile_serialize_identity_v1, so it is identical on
 * every architecture and endianness.
 */
#ifndef CYRINX_PROFILES_H
#define CYRINX_PROFILES_H

#include "cyrinx_base.h"

#ifdef __cplusplus
extern "C" {
#endif

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

/* Evidence classification. Mutable metadata, never part of identity. */
typedef enum {
    CYRINX_CLASSIFICATION_COMPATIBILITY = 0,
    CYRINX_CLASSIFICATION_QUALIFIED = 1,
    CYRINX_CLASSIFICATION_EXPERIMENTAL = 2,
    CYRINX_CLASSIFICATION_INTERNAL_TEST_FIXTURE = 3
} cyrinx_classification_t;

#define CYRINX_PROFILE_ABI_VERSION 1u
/* Named v1 layout size; the acceptance minimum per cyrinx_abi_accepts.
 * Frozen: _Static_asserts in cyrinx_profiles.c pin every field offset. */
#define CYRINX_PROFILE_V1_SIZE 128u

typedef struct cyrinx_profile {
    uint32_t struct_size;
    uint32_t abi_version;

    uint32_t id;             /* stable on-wire registry key */
    uint32_t classification; /* cyrinx_classification_t; evidence metadata */

    uint32_t modulation; /* cyrinx_modulation_t */
    uint32_t code_rate;  /* cyrinx_code_rate_t */

    double low_frequency_hz;
    double high_frequency_hz;

    uint32_t fft_size;
    uint32_t cyclic_prefix;
    uint32_t sample_rate;
    uint32_t pilot_every;
    uint32_t symbol_count;
    uint32_t block_count; /* derived from geometry at lookup; not identity */

    double amplitude;
    double clip_sigma;
    double chirp_f0;
    double chirp_f1;

    uint8_t identity_sha256[32];
} cyrinx_profile_t;

/* Canonical identity serialization v1: a fixed ASCII domain prefix followed
 * by the identity fields big-endian in declaration order (doubles as their
 * IEEE-754 bit patterns). 26 domain bytes + 76 field bytes. */
#define CYRINX_PROFILE_IDENTITY_V1_DOMAIN "cyrinx-profile-identity-v1"
#define CYRINX_PROFILE_IDENTITY_V1_SERIALIZED_SIZE 102u

/* Number of registered profiles. */
CYRINX_API size_t cyrinx_profile_get_count(void);

/* Copy the profile at 0-based `index` into the caller's structure. The caller
 * must pre-set struct_size and abi_version; acceptance and the no-output-on-
 * rejection guarantee follow the cyrinx_abi_header contract. Bytes beyond the
 * v1 prefix of an oversized caller structure are left unmodified, and the
 * caller's struct_size is preserved. Returns CYRINX_STATUS_ERR_NOT_FOUND for
 * an out-of-range index. */
CYRINX_API cyrinx_abi_status_t cyrinx_profile_get_by_index(size_t index, cyrinx_profile_t *out_profile);

/* As cyrinx_profile_get_by_index, keyed by the stable on-wire ID. */
CYRINX_API cyrinx_abi_status_t cyrinx_profile_get_by_id(uint32_t id, cyrinx_profile_t *out_profile);

/* Write the canonical identity byte stream (exactly
 * CYRINX_PROFILE_IDENTITY_V1_SERIALIZED_SIZE bytes) for `profile` to `out`.
 * Rejects a profile whose identity doubles are not all finite. */
CYRINX_API cyrinx_abi_status_t cyrinx_profile_serialize_identity_v1(const cyrinx_profile_t *profile,
                                                                    uint8_t *out, size_t out_cap);

/* SHA-256 of the canonical identity serialization. Writes exactly 32 bytes.
 * Same rejection rules as cyrinx_profile_serialize_identity_v1. */
CYRINX_API cyrinx_abi_status_t cyrinx_profile_compute_identity(const cyrinx_profile_t *profile,
                                                               uint8_t *out_digest32);

/* Full validation: ABI prefix, enum ranges, finite/positive parameter ranges,
 * band inside (0, Nyquist], geometry viability via
 * cyrinx_bulk_compute_geometry, block_count equal to the derived geometry,
 * and identity_sha256 equal to the recomputed digest. */
CYRINX_API cyrinx_abi_status_t cyrinx_profile_validate(const cyrinx_profile_t *profile);

#ifdef __cplusplus
}
#endif

#endif /* CYRINX_PROFILES_H */
