/* Cyrinx wideband bulk PHY — portable C core. See cyrinx/cyrinx_bulk.h.
 * Reference: scratch/hw20k/modem.py. Validated against Tests/Fixtures/golden. */
#include "cyrinx/cyrinx_bulk.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>

#include "cyrinx/cyrinx_fft.h"

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

/* ---------------- Gray-coded QAM ---------------- */
/* modem.py: _QAM_NORM = {1:1, 2:sqrt2, 4:sqrt10, 6:sqrt42, 8:sqrt170}. */
static double cyrinx_qam_norm(int nbits) {
    switch (nbits) {
    case 1: return 1.0;
    case 2: return sqrt(2.0);
    case 4: return sqrt(10.0);
    case 6: return sqrt(42.0);
    case 8: return sqrt(170.0);
    default: return 1.0;
    }
}

/* One Gray-coded PAM axis: integer g (na bits) -> level (2*order[g]-(L-1)),
 * where order is the inverse Gray permutation (order[gray[k]] = k). */
static double cyrinx_pam_level(uint32_t g, int na) {
    int L = 1 << na;
    /* order[gray] = k  ==>  for our g we need k with gray[k] == g. */
    int order_g = 0;
    for (int k = 0; k < L; ++k) {
        if ((uint32_t)(k ^ (k >> 1)) == g) {
            order_g = k;
            break;
        }
    }
    return (double)(2 * order_g - (L - 1));
}

void cyrinx_qam_map(const uint8_t *bits, int nbits, double *out_re, double *out_im) {
    if (nbits == 1) {
        *out_re = 1.0 - 2.0 * (bits[0] & 1u); /* BPSK */
        *out_im = 0.0;
        return;
    }
    int na = nbits / 2;
    uint32_t gi = 0, gq = 0;
    for (int b = 0; b < na; ++b) {            /* MSB first */
        gi = (gi << 1) | (bits[b] & 1u);
        gq = (gq << 1) | (bits[na + b] & 1u);
    }
    double norm = cyrinx_qam_norm(nbits);
    *out_re = cyrinx_pam_level(gi, na) / norm;
    *out_im = cyrinx_pam_level(gq, na) / norm;
}

/* ---------------- Frame geometry ---------------- */
static double cyrinx_rate_value(const char *rate) {
    if (strcmp(rate, "1/2") == 0) return 1.0 / 2.0;
    if (strcmp(rate, "2/3") == 0) return 2.0 / 3.0;
    if (strcmp(rate, "3/4") == 0) return 3.0 / 4.0;
    if (strcmp(rate, "5/6") == 0) return 5.0 / 6.0;
    return 0.0;
}

int cyrinx_bulk_compute_geometry(const cyrinx_bulk_config *cfg,
                                 cyrinx_bulk_geometry *out) {
    double rate = cyrinx_rate_value(cfg->rate);
    if (rate <= 0.0 || cfg->nfft <= 0 || cfg->pilot_every <= 0) {
        return -1;
    }
    double bin_hz = (double)cfg->sr / (double)cfg->nfft;
    int bin_lo = (int)ceil(cfg->f_lo / bin_hz);
    int bin_hi = (int)floor(cfg->f_hi / bin_hz);
    int n_used = bin_hi - bin_lo + 1;
    /* pilots at used[::pilot_every] */
    int n_pilots = (n_used + cfg->pilot_every - 1) / cfg->pilot_every;
    int n_data_bins = n_used - n_pilots;
    int bits_per_sym = n_data_bins * cfg->bits_per_bin;
    int cap = bits_per_sym * cfg->n_sym;
    int info = (int)floor((double)cap * rate) - 6;
    int blk_bits = (CYRINX_BULK_CRC_BLOCK + 4) * 8;
    int n_blocks = info / blk_bits;
    out->bin_lo = bin_lo;
    out->bin_hi = bin_hi;
    out->n_used = n_used;
    out->n_pilots = n_pilots;
    out->n_data_bins = n_data_bins;
    out->bits_per_sym = bits_per_sym;
    out->cap = cap;
    out->info_bits = info;
    out->n_blocks = n_blocks;
    out->payload_bytes = n_blocks * CYRINX_BULK_CRC_BLOCK;
    out->frame_samples =
        CYRINX_BULK_CHIRP_LEN + CYRINX_BULK_GUARD + (2 + cfg->n_sym) * (cfg->nfft + cfg->cp);
    return 0;
}

/* ---------------- TX helpers ---------------- */
/* modem.py:make_chirp — linear chirp f0..f1 with 128-sample raised-cosine ramps. */
static void cyrinx_make_chirp(double *out, int n, double sr, double f0, double f1) {
    double T = (double)n / sr;
    int r = 128;
    for (int i = 0; i < n; ++i) {
        double t = (double)i / sr;
        double ph = 2.0 * M_PI * (f0 * t + 0.5 * (f1 - f0) * t * t / T);
        double w = sin(ph);
        double env = 1.0;
        if (i < r) {
            env = 0.5 - 0.5 * cos(M_PI * (double)i / (double)r);
        } else if (i >= n - r) {
            int j = n - 1 - i; /* mirror of the leading ramp */
            env = 0.5 - 0.5 * cos(M_PI * (double)j / (double)r);
        }
        out[i] = w * env;
    }
}

/* QPSK unit symbols from a DetRng phase stream (pilots / sync). */
static void cyrinx_phase_symbols(uint64_t seed, int n, double *re, double *im) {
    cyrinx_detrng r;
    cyrinx_detrng_init(&r, seed);
    for (int i = 0; i < n; ++i) {
        uint64_t ph = cyrinx_detrng_mod(&r, 4);
        double theta = M_PI / 4.0 + M_PI / 2.0 * (double)ph;
        re[i] = cos(theta);
        im[i] = sin(theta);
    }
}

/* OFDM symbol: place freq values on used bins, irfft, prepend CP. Writes
 * (cp+nfft) real samples to `out`. */
static void cyrinx_ofdm_mod_symbol(cyrinx_irfft_plan *plan, int nfft, int cp,
                                   int bin_lo, int n_used, const double *fv_re,
                                   const double *fv_im, double *spec_re,
                                   double *spec_im, double *tbuf, double *out) {
    int half = nfft / 2 + 1;
    memset(spec_re, 0, (size_t)half * sizeof(double));
    memset(spec_im, 0, (size_t)half * sizeof(double));
    for (int k = 0; k < n_used; ++k) {
        spec_re[bin_lo + k] = fv_re[k];
        spec_im[bin_lo + k] = fv_im[k];
    }
    cyrinx_irfft(plan, spec_re, spec_im, tbuf);
    for (int i = 0; i < cp; ++i) {
        out[i] = tbuf[nfft - cp + i]; /* cyclic prefix */
    }
    memcpy(out + cp, tbuf, (size_t)nfft * sizeof(double));
}

long cyrinx_bulk_modulate(const cyrinx_bulk_config *cfg, const uint8_t *payload,
                          size_t payload_len, float *wave_out, size_t wave_cap,
                          double *data_freq_out) {
    cyrinx_bulk_geometry g;
    if (cyrinx_bulk_compute_geometry(cfg, &g) != 0) {
        return -1;
    }
    if ((int)payload_len != g.payload_bytes || (size_t)g.frame_samples > wave_cap) {
        return -1;
    }
    const int nfft = cfg->nfft, cp = cfg->cp, sym = nfft + cp;
    const int n_used = g.n_used, cap = g.cap;

    /* ---- bits: blocks + CRC -> stream -> info bits (+ seed-7 pad) ---- */
    int stream_bytes = g.n_blocks * (CYRINX_BULK_CRC_BLOCK + 4);
    uint8_t *bits = (uint8_t *)malloc((size_t)g.info_bits + 16);
    int nbit = 0;
    for (int blk = 0; blk < g.n_blocks; ++blk) {
        const uint8_t *src = payload + blk * CYRINX_BULK_CRC_BLOCK;
        uint32_t crc = cyrinx_bulk_crc32(src, CYRINX_BULK_CRC_BLOCK);
        uint8_t framed[CYRINX_BULK_CRC_BLOCK + 4];
        memcpy(framed, src, CYRINX_BULK_CRC_BLOCK);
        framed[CYRINX_BULK_CRC_BLOCK + 0] = (uint8_t)((crc >> 24) & 0xFF);
        framed[CYRINX_BULK_CRC_BLOCK + 1] = (uint8_t)((crc >> 16) & 0xFF);
        framed[CYRINX_BULK_CRC_BLOCK + 2] = (uint8_t)((crc >> 8) & 0xFF);
        framed[CYRINX_BULK_CRC_BLOCK + 3] = (uint8_t)(crc & 0xFF);
        for (int b = 0; b < CYRINX_BULK_CRC_BLOCK + 4; ++b) {
            for (int k = 7; k >= 0; --k) { /* unpackbits: MSB first */
                bits[nbit++] = (framed[b] >> k) & 1u;
            }
        }
    }
    (void)stream_bytes;
    if (nbit < g.info_bits) {
        cyrinx_prbs_bits(bits + nbit, (size_t)(g.info_bits - nbit), 7);
        nbit = g.info_bits;
    }

    /* ---- conv encode + puncture + seed-8 capacity fill ---- */
    uint8_t *coded = (uint8_t *)malloc((size_t)(2 * (g.info_bits + 6)) + 16);
    size_t coded_n = cyrinx_conv_encode(bits, (size_t)g.info_bits, coded);
    const uint8_t *pat = NULL;
    size_t patlen = cyrinx_puncture_pattern(cfg->rate, &pat);
    uint8_t *punc = (uint8_t *)malloc(coded_n + 16);
    size_t punc_n = cyrinx_puncture(coded, coded_n, pat, patlen, punc);
    uint8_t *filled = (uint8_t *)malloc((size_t)cap + 16);
    memcpy(filled, punc, punc_n < (size_t)cap ? punc_n : (size_t)cap);
    if (punc_n < (size_t)cap) {
        cyrinx_prbs_bits(filled + punc_n, (size_t)cap - punc_n, 8);
    }

    /* ---- interleave: inter[perm[i]] = filled[i] ---- */
    int64_t *perm = (int64_t *)malloc((size_t)cap * sizeof(int64_t));
    cyrinx_detrng_permutation(0x1EAF, perm, (size_t)cap);
    uint8_t *inter = (uint8_t *)malloc((size_t)cap);
    for (int i = 0; i < cap; ++i) {
        inter[perm[i]] = filled[i];
    }

    /* ---- pilots and pilot positions within `used` ---- */
    double *pilot_re = (double *)malloc((size_t)g.n_pilots * sizeof(double));
    double *pilot_im = (double *)malloc((size_t)g.n_pilots * sizeof(double));
    cyrinx_phase_symbols(0xBEEF, g.n_pilots, pilot_re, pilot_im);
    /* used position is a pilot iff (position % pilot_every) == 0 */

    /* ---- FFT plan + scratch ---- */
    cyrinx_irfft_plan *plan = cyrinx_irfft_create(nfft);
    int half = nfft / 2 + 1;
    double *spec_re = (double *)malloc((size_t)half * sizeof(double));
    double *spec_im = (double *)malloc((size_t)half * sizeof(double));
    double *tbuf = (double *)malloc((size_t)nfft * sizeof(double));
    double *fv_re = (double *)malloc((size_t)n_used * sizeof(double));
    double *fv_im = (double *)malloc((size_t)n_used * sizeof(double));

    int total_syms = 2 + cfg->n_sym;
    double *x = (double *)malloc((size_t)total_syms * (size_t)sym * sizeof(double));

    /* ---- 2 sync symbols ---- */
    double *sync_re = (double *)malloc((size_t)n_used * sizeof(double));
    double *sync_im = (double *)malloc((size_t)n_used * sizeof(double));
    for (int w = 0; w < 2; ++w) {
        cyrinx_phase_symbols(0x5EED + (uint64_t)w, n_used, sync_re, sync_im);
        cyrinx_ofdm_mod_symbol(plan, nfft, cp, g.bin_lo, n_used, sync_re, sync_im,
                               spec_re, spec_im, tbuf, x + (size_t)w * sym);
    }

    /* ---- data symbols ---- */
    int pos = 0; /* bit cursor into `inter` */
    for (int s = 0; s < cfg->n_sym; ++s) {
        memset(fv_re, 0, (size_t)n_used * sizeof(double));
        memset(fv_im, 0, (size_t)n_used * sizeof(double));
        int pilot_i = 0;
        for (int k = 0; k < n_used; ++k) {
            if (k % cfg->pilot_every == 0) { /* pilot position */
                fv_re[k] = pilot_re[pilot_i];
                fv_im[k] = pilot_im[pilot_i];
                pilot_i++;
            } else { /* data bin */
                double re, im;
                cyrinx_qam_map(inter + pos, cfg->bits_per_bin, &re, &im);
                pos += cfg->bits_per_bin;
                fv_re[k] = re;
                fv_im[k] = im;
            }
        }
        if (data_freq_out) {
            for (int k = 0; k < n_used; ++k) {
                data_freq_out[((size_t)s * n_used + k) * 2 + 0] = fv_re[k];
                data_freq_out[((size_t)s * n_used + k) * 2 + 1] = fv_im[k];
            }
        }
        cyrinx_ofdm_mod_symbol(plan, nfft, cp, g.bin_lo, n_used, fv_re, fv_im,
                               spec_re, spec_im, tbuf, x + (size_t)(2 + s) * sym);
    }

    /* ---- normalize: clip to clip_sigma*std, then scale peak to amp ---- */
    int xn = total_syms * sym;
    double mean = 0.0;
    for (int i = 0; i < xn; ++i) {
        mean += x[i];
    }
    mean /= xn;
    double var = 0.0;
    for (int i = 0; i < xn; ++i) {
        double d = x[i] - mean;
        var += d * d;
    }
    double sigma = sqrt(var / xn); /* population std (ddof=0) */
    double lim = cfg->clip_sigma * sigma;
    double peak = 0.0;
    for (int i = 0; i < xn; ++i) {
        if (x[i] > lim) x[i] = lim;
        else if (x[i] < -lim) x[i] = -lim;
        double a = fabs(x[i]);
        if (a > peak) peak = a;
    }
    double scale = (peak > 0.0) ? (cfg->amp / peak) : 0.0;

    /* ---- assemble wave: chirp*amp, GUARD zeros, normalized x ---- */
    double *chirp = (double *)malloc((size_t)CYRINX_BULK_CHIRP_LEN * sizeof(double));
    double cf0 = cfg->chirp_f0 > 0 ? cfg->chirp_f0 : CYRINX_BULK_CHIRP_F0;
    double cf1 = cfg->chirp_f1 > 0 ? cfg->chirp_f1 : CYRINX_BULK_CHIRP_F1;
    cyrinx_make_chirp(chirp, CYRINX_BULK_CHIRP_LEN, (double)cfg->sr, cf0, cf1);
    long w = 0;
    for (int i = 0; i < CYRINX_BULK_CHIRP_LEN; ++i) {
        wave_out[w++] = (float)(chirp[i] * cfg->amp);
    }
    for (int i = 0; i < CYRINX_BULK_GUARD; ++i) {
        wave_out[w++] = 0.0f;
    }
    for (int i = 0; i < xn; ++i) {
        wave_out[w++] = (float)(x[i] * scale);
    }

    free(bits); free(coded); free(punc); free(filled); free(perm); free(inter);
    free(pilot_re); free(pilot_im); free(spec_re); free(spec_im); free(tbuf);
    free(fv_re); free(fv_im); free(x); free(sync_re); free(sync_im); free(chirp);
    cyrinx_irfft_destroy(plan);
    return w;
}
