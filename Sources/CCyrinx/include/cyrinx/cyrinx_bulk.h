/* Cyrinx wideband bulk PHY — portable C core (docs/PUBLICATION.md Phase 1).
 *
 * This is the canonical, portable implementation of the bulk OFDM modem that
 * achieved the measured 36.6 / 27.3 kbps over-the-air result. It is ported
 * against three living reference implementations — the Python oracle
 * (scratch/hw20k/modem.py) and the Swift/Kotlin BulkDemod files — and validated
 * bit-exact (integer stages) / float-tolerant (FFT stages) against the golden
 * vectors in Tests/Fixtures/golden (see scratch/hw20k/golden_vectors.py).
 *
 * STATUS: under construction (PR 1.2 = TX). The API is evolving and not yet a
 * stable part of the shipping ABI. This header currently exposes the
 * deterministic TX primitives so they can be validated independently.
 *
 * All constants are cited from modem.py (the reference) so the two stay in sync.
 */
#ifndef CYRINX_BULK_H
#define CYRINX_BULK_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ---------------- Deterministic RNG (splitmix64) ----------------
 * Bit-exact with modem.py:DetRng (and the Kotlin/Swift ports), so pilots, sync
 * symbols, the interleaver permutation, and PRBS padding reproduce identically
 * across languages. */
typedef struct {
    uint64_t s;
} cyrinx_detrng;

void cyrinx_detrng_init(cyrinx_detrng *r, uint64_t seed);
uint64_t cyrinx_detrng_u64(cyrinx_detrng *r);
uint64_t cyrinx_detrng_mod(cyrinx_detrng *r, uint64_t m);

/* One bit per output byte (0/1), each = top bit (u64 >> 63). */
void cyrinx_detrng_bits(cyrinx_detrng *r, uint8_t *out, size_t n);
/* One byte per output (u64 >> 56) & 0xFF. */
void cyrinx_detrng_bytes(cyrinx_detrng *r, uint8_t *out, size_t n);
/* Fisher-Yates permutation of 0..n-1 (matches DetRng.permutation). out is
 * int64 to mirror the golden interleave_perm artifact dtype. */
void cyrinx_detrng_permutation(uint64_t seed, int64_t *out, size_t n);

/* PRBS bit stream: DetRng(seed).bits(n) — one bit per byte. */
void cyrinx_prbs_bits(uint8_t *out, size_t n, uint64_t seed);

/* ---------------- CRC-32 (IEEE, zlib-compatible) ----------------
 * NOT CRC-32C: the bulk PHY uses zlib.crc32 (poly 0xEDB88320 reflected,
 * init/xorout 0xFFFFFFFF). The per-block 4-byte CRC is appended big-endian. */
uint32_t cyrinx_bulk_crc32(const uint8_t *data, size_t len);

/* ---------------- Convolutional code K=7 (171,133) ----------------
 * Rate-1/2, 6 zero tail bits, MSB = newest bit. Output length = 2*(n+6).
 * `out` must hold at least 2*(n+6) bytes (1 coded bit per byte). Returns the
 * number of coded bits written. */
size_t cyrinx_conv_encode(const uint8_t *bits, size_t n, uint8_t *out);

/* Puncture pattern lookup by rate name ("1/2","2/3","3/4","5/6"). Returns the
 * pattern length and writes the pattern pointer; returns 0 on unknown rate. */
size_t cyrinx_puncture_pattern(const char *rate, const uint8_t **out_pattern);

/* Puncture `coded` (length n) by `pattern` (length p, tiled): keep coded[i]
 * where pattern[i % p] != 0. Writes kept bits to `out` (capacity >= n) and
 * returns the kept count. */
size_t cyrinx_puncture(const uint8_t *coded, size_t n, const uint8_t *pattern,
                       size_t p, uint8_t *out);

#ifdef __cplusplus
}
#endif

#endif /* CYRINX_BULK_H */
