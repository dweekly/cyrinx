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

/* ---------------- Gray-coded QAM ----------------
 * Map `nbits` interleaved bits (MSB first) to one unit-average-power complex
 * symbol. nbits: 1 (BPSK), 2 (QPSK), 4 (16-QAM), 6 (64-QAM), 8 (256-QAM).
 * Matches modem.py:qam_map. */
void cyrinx_qam_map(const uint8_t *bits, int nbits, double *out_re, double *out_im);

/* ---------------- Wideband bulk-PHY TX ----------------
 * Uniform bit-loading config (all data bins carry `bits_per_bin` bits). Mixed
 * per-bin loading arrives with the adaptive sounder (PR 1.4). */
typedef struct {
    double f_lo, f_hi;   /* used-band edges in Hz */
    int pilot_every;     /* comb-pilot spacing in used bins (modem default 8) */
    int bits_per_bin;    /* 1/2/4/6/8 on every data bin */
    const char *rate;    /* code rate: "1/2","2/3","3/4","5/6" */
    int n_sym;           /* number of OFDM data symbols */
    int nfft, cp, sr;
    double amp, clip_sigma;
    double chirp_f0, chirp_f1;
} cyrinx_bulk_config;

/* Derived frame geometry (modem.py:Config.__init__). */
typedef struct {
    int bin_lo, bin_hi, n_used, n_pilots, n_data_bins;
    int bits_per_sym, cap, info_bits, n_blocks, payload_bytes;
    int frame_samples;
} cyrinx_bulk_geometry;

/* Compute geometry from config. Returns 0 on success, -1 on invalid config. */
int cyrinx_bulk_compute_geometry(const cyrinx_bulk_config *cfg,
                                 cyrinx_bulk_geometry *out);

/* Modulate one frame. `payload` must be geometry.payload_bytes long. Writes
 * geometry.frame_samples float samples to `wave_out` (capacity wave_cap). If
 * `data_freq_out` is non-NULL it receives n_sym*n_used complex values (re/im
 * interleaved doubles) — the per-data-symbol freq-domain vectors, for golden
 * validation. Returns the number of samples written, or -1 on error. */
long cyrinx_bulk_modulate(const cyrinx_bulk_config *cfg, const uint8_t *payload,
                          size_t payload_len, float *wave_out, size_t wave_cap,
                          double *data_freq_out);

/* Default chirp/guard constants (modem.py). */
#define CYRINX_BULK_CHIRP_LEN 4096
#define CYRINX_BULK_GUARD 2048
#define CYRINX_BULK_CHIRP_F0 2000.0
#define CYRINX_BULK_CHIRP_F1 16000.0
#define CYRINX_BULK_CRC_BLOCK 256

#ifdef __cplusplus
}
#endif

#endif /* CYRINX_BULK_H */
