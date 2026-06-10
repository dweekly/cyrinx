/* Cyrinx wideband bulk PHY — portable C core. See cyrinx/cyrinx_bulk.h.
 * Reference: scratch/hw20k/modem.py. Validated against Tests/Fixtures/golden. */
#include "cyrinx/cyrinx_bulk.h"

#include <string.h>

/* ---------------- splitmix64 DetRng ---------------- */
/* modem.py:DetRng — constants are the standard splitmix64 mixer. */
#define CYRINX_SM64_GAMMA 0x9E3779B97F4A7C15ULL
#define CYRINX_SM64_MIX1 0xBF58476D1CE4E5B9ULL
#define CYRINX_SM64_MIX2 0x94D049BB133111EBULL

void cyrinx_detrng_init(cyrinx_detrng *r, uint64_t seed) { r->s = seed; }

uint64_t cyrinx_detrng_u64(cyrinx_detrng *r) {
    r->s += CYRINX_SM64_GAMMA;
    uint64_t z = r->s;
    z = (z ^ (z >> 30)) * CYRINX_SM64_MIX1;
    z = (z ^ (z >> 27)) * CYRINX_SM64_MIX2;
    return z ^ (z >> 31);
}

uint64_t cyrinx_detrng_mod(cyrinx_detrng *r, uint64_t m) {
    return cyrinx_detrng_u64(r) % m;
}

void cyrinx_detrng_bits(cyrinx_detrng *r, uint8_t *out, size_t n) {
    for (size_t i = 0; i < n; ++i) {
        out[i] = (uint8_t)(cyrinx_detrng_u64(r) >> 63);
    }
}

void cyrinx_detrng_bytes(cyrinx_detrng *r, uint8_t *out, size_t n) {
    for (size_t i = 0; i < n; ++i) {
        out[i] = (uint8_t)((cyrinx_detrng_u64(r) >> 56) & 0xFF);
    }
}

void cyrinx_detrng_permutation(uint64_t seed, int64_t *out, size_t n) {
    cyrinx_detrng r;
    cyrinx_detrng_init(&r, seed);
    for (size_t i = 0; i < n; ++i) {
        out[i] = (int64_t)i;
    }
    /* for i in range(n-1, 0, -1): j = mod(i+1); swap(p[i], p[j]) */
    for (size_t i = n - 1; i >= 1; --i) {
        uint64_t j = cyrinx_detrng_mod(&r, (uint64_t)i + 1);
        int64_t tmp = out[i];
        out[i] = out[j];
        out[j] = tmp;
        if (i == 1) {
            break; /* size_t underflow guard (loop would wrap below 0) */
        }
    }
}

void cyrinx_prbs_bits(uint8_t *out, size_t n, uint64_t seed) {
    cyrinx_detrng r;
    cyrinx_detrng_init(&r, seed);
    cyrinx_detrng_bits(&r, out, n);
}

/* ---------------- CRC-32 (IEEE / zlib) ---------------- */
/* Reflected polynomial 0xEDB88320, init/xorout 0xFFFFFFFF. Table built lazily. */
static uint32_t g_crc32_table[256];
static int g_crc32_ready = 0;

static void cyrinx_crc32_init_table(void) {
    for (uint32_t i = 0; i < 256; ++i) {
        uint32_t c = i;
        for (int k = 0; k < 8; ++k) {
            c = (c & 1u) ? (0xEDB88320u ^ (c >> 1)) : (c >> 1);
        }
        g_crc32_table[i] = c;
    }
    g_crc32_ready = 1;
}

uint32_t cyrinx_bulk_crc32(const uint8_t *data, size_t len) {
    if (!g_crc32_ready) {
        cyrinx_crc32_init_table();
    }
    uint32_t crc = 0xFFFFFFFFu;
    for (size_t i = 0; i < len; ++i) {
        crc = g_crc32_table[(crc ^ data[i]) & 0xFFu] ^ (crc >> 8);
    }
    return crc ^ 0xFFFFFFFFu;
}

/* ---------------- Convolutional code K=7 (171,133) ---------------- */
/* modem.py: G0 = 0o171 (121), G1 = 0o133 (91); reg = (bit << 6) | state(6 bits).
 * NB: Python 0o171 == C 0171 (decimal 121); 0o133 == C 0133 (decimal 91). */
#define CYRINX_CONV_G0 0171 /* octal 171 == 121 */
#define CYRINX_CONV_G1 0133 /* octal 133 == 91 */

static uint8_t cyrinx_parity7(uint32_t v) {
    /* parity of the low 7 bits */
    v &= 0x7Fu;
    v ^= v >> 4;
    v ^= v >> 2;
    v ^= v >> 1;
    return (uint8_t)(v & 1u);
}

size_t cyrinx_conv_encode(const uint8_t *bits, size_t n, uint8_t *out) {
    uint32_t s = 0;
    size_t o = 0;
    for (size_t i = 0; i < n + 6; ++i) {
        uint32_t b = (i < n) ? (bits[i] & 1u) : 0u; /* 6 zero tail bits */
        uint32_t reg = (b << 6) | s;
        out[o++] = cyrinx_parity7(reg & CYRINX_CONV_G0);
        out[o++] = cyrinx_parity7(reg & CYRINX_CONV_G1);
        s = reg >> 1;
    }
    return o;
}

/* ---------------- Puncture ---------------- */
/* modem.py PUNCTURE table. */
static const uint8_t kPat12[] = {1, 1};
static const uint8_t kPat23[] = {1, 1, 0, 1};
static const uint8_t kPat34[] = {1, 1, 0, 1, 1, 0};
static const uint8_t kPat56[] = {1, 1, 0, 1, 1, 0, 0, 1, 1, 0};

size_t cyrinx_puncture_pattern(const char *rate, const uint8_t **out_pattern) {
    if (strcmp(rate, "1/2") == 0) {
        *out_pattern = kPat12;
        return sizeof(kPat12);
    }
    if (strcmp(rate, "2/3") == 0) {
        *out_pattern = kPat23;
        return sizeof(kPat23);
    }
    if (strcmp(rate, "3/4") == 0) {
        *out_pattern = kPat34;
        return sizeof(kPat34);
    }
    if (strcmp(rate, "5/6") == 0) {
        *out_pattern = kPat56;
        return sizeof(kPat56);
    }
    *out_pattern = NULL;
    return 0;
}

size_t cyrinx_puncture(const uint8_t *coded, size_t n, const uint8_t *pattern,
                       size_t p, uint8_t *out) {
    size_t kept = 0;
    for (size_t i = 0; i < n; ++i) {
        if (pattern[i % p]) {
            out[kept++] = coded[i];
        }
    }
    return kept;
}
