/* Cyrinx 3.0 versioned C ABI base (C3-03).
 *
 * Shared conventions for every versioned Cyrinx 3.0+ structure and entry
 * point: fixed-width status storage, the two-field ABI prefix, the prefix
 * validation contract, and a reviewed portable SHA-256 used by the profile
 * identity scheme (cyrinx_profiles.h).
 *
 * Layering: this header depends only on the C standard library. It does not
 * include the 2.x session API (cyrinx.h) and may be included beside it; the
 * shared CYRINX_STATUS_* values are numerically identical to the 2.x
 * cyrinx_status_t enumerators of the same suffix, which the test suite
 * asserts.
 */
#ifndef CYRINX_BASE_H
#define CYRINX_BASE_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* Single export macro for the whole library. Identical token sequence to the
 * definition in cyrinx.h so the two headers can be included in either order;
 * one build-side macro (CYRINX_BUILD) selects dllexport on Windows. */
#ifndef CYRINX_API
#if defined(_WIN32)
#if defined(CYRINX_BUILD)
#define CYRINX_API __declspec(dllexport)
#else
#define CYRINX_API __declspec(dllimport)
#endif
#else
#define CYRINX_API __attribute__((visibility("default")))
#endif
#endif

#ifdef __cplusplus
extern "C" {
#endif

/* ---------------- Fixed-width status ----------------
 * Storage is exactly int32_t on every target; symbolic values are defined
 * separately from the storage width. Values -1..-9 are numerically identical
 * to the 2.x cyrinx_status_t enumerators; -10 and below are new in 3.0. */
typedef int32_t cyrinx_abi_status_t;

#define CYRINX_STATUS_OK 0
#define CYRINX_STATUS_ERR_INVALID_ARGUMENT (-1)
#define CYRINX_STATUS_ERR_NOT_RUNNING (-2)
#define CYRINX_STATUS_ERR_BUFFER_TOO_SMALL (-3)
#define CYRINX_STATUS_ERR_TIMEOUT (-4)
#define CYRINX_STATUS_ERR_CRC (-5)
#define CYRINX_STATUS_ERR_BUSY (-6)
#define CYRINX_STATUS_ERR_UNSUPPORTED (-7)
#define CYRINX_STATUS_ERR_STATE (-8)
#define CYRINX_STATUS_ERR_INTERNAL (-9)
/* The demodulator could not produce an ordered result at all (hard
 * synchronization or resource failure). A capture that demodulates with few
 * or zero CRC-valid blocks is NOT this error: that returns
 * CYRINX_STATUS_OK with the invalid blocks recorded in the ordered validity
 * mask, which is the partial-block-recovery contract. */
#define CYRINX_STATUS_ERR_DECODE (-10)
/* A registry lookup key did not match any registered entry. */
#define CYRINX_STATUS_ERR_NOT_FOUND (-11)

/* ---------------- Versioned ABI prefix ----------------
 * Every versioned Cyrinx 3.0+ structure begins with these two fixed-width
 * fields. Callers set struct_size to the byte size of the structure they
 * allocated and abi_version to the version they compiled against.
 *
 * Acceptance contract: an entry point taking a versioned structure accepts it
 * when abi_version matches a version it implements AND struct_size is at
 * least that version's named minimum size constant (for example
 * CYRINX_PROFILE_V1_SIZE). On acceptance the library reads/writes only the
 * bytes of the version it implements; caller bytes beyond that prefix are
 * never read and never modified. On rejection the entry point returns
 * CYRINX_STATUS_ERR_INVALID_ARGUMENT and writes no output at all. */
typedef struct cyrinx_abi_header {
    uint32_t struct_size;
    uint32_t abi_version;
} cyrinx_abi_header_t;

#define CYRINX_ABI_HEADER_SIZE 8u

/* Prefix acceptance check per the contract above. min_version_size is the
 * named size constant of the version this library implements, never a bare
 * sizeof of a possibly-evolved type. */
static inline bool cyrinx_abi_accepts(const void *versioned_struct, uint32_t min_version_size,
                                      uint32_t expected_abi_version) {
    if (versioned_struct == NULL) {
        return false;
    }
    const cyrinx_abi_header_t *header = (const cyrinx_abi_header_t *)versioned_struct;
    return header->struct_size >= min_version_size && header->abi_version == expected_abi_version;
}

/* ---------------- Portable SHA-256 ----------------
 * FIPS 180-4 SHA-256 with no allocation and no global state, validated
 * against the NIST reference vectors by the test suite. Used for the
 * profile identity scheme; exposed so consumers and tests can reproduce
 * identity digests without a second implementation. Writes exactly 32 bytes
 * to out_digest32. NULL data is permitted only when len is 0. */
CYRINX_API void cyrinx_sha256(const uint8_t *data, size_t len, uint8_t *out_digest32);

#ifdef __cplusplus
}
#endif

#endif /* CYRINX_BASE_H */
