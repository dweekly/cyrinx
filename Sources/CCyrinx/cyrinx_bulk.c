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

/* ================= RX ================= */
/* Reference: modem.py:demodulate_frame (single-mic, track_alpha=0 path). */

/* Max-log QAM LLRs (log P0/P1) for one symbol with noise var n0. Writes nbits
 * values. modem.py:qam_llr. */
static void cyrinx_qam_llr(double zre, double zim, int nbits, double n0,
                           double *out) {
    double inv = 1.0 / (n0 > 1e-9 ? n0 : 1e-9);
    if (nbits == 1) {
        out[0] = 4.0 * zre * inv; /* BPSK */
        return;
    }
    int na = nbits / 2;
    int L = 1 << na;
    double norm = cyrinx_qam_norm(nbits);
    for (int axis = 0; axis < 2; ++axis) {
        double y = (axis == 0) ? zre : zim;
        for (int bit = 0; bit < na; ++bit) {
            double d0 = 1e300, d1 = 1e300;
            for (int k = 0; k < L; ++k) {
                double lvn = (double)(2 * k - (L - 1)) / norm;
                double d = y - lvn;
                double d2 = d * d;
                int gray = k ^ (k >> 1);
                if ((gray >> (na - 1 - bit)) & 1) {
                    if (d2 < d1) d1 = d2;
                } else {
                    if (d2 < d0) d0 = d2;
                }
            }
            out[axis * na + bit] = (d1 - d0) * inv;
        }
    }
}

/* Soft Viterbi for the K=7 (171,133) code. llr0/llr1: per-step LLRs (log P0/P1)
 * for the two coded bits. Writes n_info decoded bits. modem.py:viterbi_decode. */
static void cyrinx_viterbi(const double *llr0, const double *llr1, int n,
                           int n_info, uint8_t *out) {
    /* trellis: next[s][b], out0[s][b], out1[s][b] */
    static uint8_t next_s[64][2], o0[64][2], o1[64][2];
    static int built = 0;
    if (!built) {
        for (int s = 0; s < 64; ++s) {
            for (int b = 0; b < 2; ++b) {
                uint32_t reg = ((uint32_t)b << 6) | (uint32_t)s;
                next_s[s][b] = (uint8_t)(reg >> 1);
                o0[s][b] = cyrinx_parity7(reg & CYRINX_CONV_G0);
                o1[s][b] = cyrinx_parity7(reg & CYRINX_CONV_G1);
            }
        }
        built = 1;
    }
    double *metric = (double *)malloc(64 * sizeof(double));
    double *nm = (double *)malloc(64 * sizeof(double));
    uint8_t *back = (uint8_t *)malloc((size_t)n * 64); /* back[i*64+state] = s*2+b */
    for (int s = 0; s < 64; ++s) metric[s] = -1e12;
    metric[0] = 0.0;
    for (int i = 0; i < n; ++i) {
        double l0 = llr0[i], l1 = llr1[i];
        for (int s = 0; s < 64; ++s) nm[s] = -1e12;
        for (int s = 0; s < 64; ++s) {
            if (metric[s] <= -1e11) continue;
            for (int b = 0; b < 2; ++b) {
                double bm = (o0[s][b] == 0 ? 0.5 * l0 : -0.5 * l0) +
                            (o1[s][b] == 0 ? 0.5 * l1 : -0.5 * l1);
                double cand = metric[s] + bm;
                int nx = next_s[s][b];
                if (cand >= nm[nx]) { /* >= so later (ties) win, matching argsort */
                    nm[nx] = cand;
                    back[(size_t)i * 64 + nx] = (uint8_t)(s * 2 + b);
                }
            }
        }
        double *t = metric; metric = nm; nm = t;
    }
    /* traceback from state 0 (zero-terminated) */
    int s = 0;
    uint8_t *bits = (uint8_t *)malloc((size_t)n);
    for (int i = n - 1; i >= 0; --i) {
        uint8_t o = back[(size_t)i * 64 + s];
        bits[i] = o & 1u;
        s = o >> 1;
    }
    for (int i = 0; i < n_info; ++i) out[i] = bits[i];
    free(metric); free(nm); free(back); free(bits);
}

/* Cross-correlation matched filter (np.correlate valid): returns argmax |sum
 * sig[i+k]*ref[k]| over i in [0, sig_len-ref_len]. */
static int cyrinx_matched_filter(const double *sig, int sig_len, const double *ref,
                                 int ref_len, int start) {
    int best_i = start;
    double best = -1.0;
    for (int i = start; i + ref_len <= sig_len; ++i) {
        double acc = 0.0;
        for (int k = 0; k < ref_len; ++k) acc += sig[i + k] * ref[k];
        double a = fabs(acc);
        if (a > best) { best = a; best_i = i; }
    }
    return best_i;
}

long cyrinx_bulk_demodulate(const cyrinx_bulk_config *cfg, const float *rx,
                            size_t rx_len, uint8_t *out_payload, size_t out_cap,
                            int *blocks_ok, int *blocks_total, double *evm_rms) {
    cyrinx_bulk_geometry g;
    if (cyrinx_bulk_compute_geometry(cfg, &g) != 0) return -1;
    if ((size_t)g.payload_bytes > out_cap) return -1;
    const int nfft = cfg->nfft, cp = cfg->cp, sym = nfft + cp;
    const int n_used = g.n_used, cap = g.cap;
    const int half = nfft / 2 + 1;

    double *sig = (double *)malloc(rx_len * sizeof(double));
    for (size_t i = 0; i < rx_len; ++i) sig[i] = (double)rx[i];

    /* ---- coarse sync: chirp matched filter ---- */
    double *chirp = (double *)malloc((size_t)CYRINX_BULK_CHIRP_LEN * sizeof(double));
    double cf0 = cfg->chirp_f0 > 0 ? cfg->chirp_f0 : CYRINX_BULK_CHIRP_F0;
    double cf1 = cfg->chirp_f1 > 0 ? cfg->chirp_f1 : CYRINX_BULK_CHIRP_F1;
    cyrinx_make_chirp(chirp, CYRINX_BULK_CHIRP_LEN, (double)cfg->sr, cf0, cf1);
    int start = cyrinx_matched_filter(sig, (int)rx_len, chirp, CYRINX_BULK_CHIRP_LEN, 0);
    int base = start + CYRINX_BULK_CHIRP_LEN + CYRINX_BULK_GUARD;

    /* ---- fine sync: correlate against the first sync OFDM symbol ---- */
    cyrinx_irfft_plan *iplan = cyrinx_irfft_create(nfft);
    double *spec_re = (double *)malloc((size_t)half * sizeof(double));
    double *spec_im = (double *)malloc((size_t)half * sizeof(double));
    double *tbuf = (double *)malloc((size_t)nfft * sizeof(double));
    double *sync0_re = (double *)malloc((size_t)n_used * sizeof(double));
    double *sync0_im = (double *)malloc((size_t)n_used * sizeof(double));
    double *ref = (double *)malloc((size_t)sym * sizeof(double));
    cyrinx_phase_symbols(0x5EED, n_used, sync0_re, sync0_im);
    cyrinx_ofdm_mod_symbol(iplan, nfft, cp, g.bin_lo, n_used, sync0_re, sync0_im,
                           spec_re, spec_im, tbuf, ref);
    int fine_window = 400;
    int lo = base - fine_window; if (lo < 0) lo = 0;
    int seg_len = (base + fine_window + sym) - lo;
    if (lo + seg_len > (int)rx_len) seg_len = (int)rx_len - lo;
    int off = cyrinx_matched_filter(sig + lo, seg_len, ref, sym, 0);
    base = lo + off;
    base -= 24; /* bias early so pre-cursor taps stay in the CP */
    cyrinx_irfft_destroy(iplan);

    /* ---- channel estimation from the 2 sync symbols ---- */
    cyrinx_rfft_plan *fplan = cyrinx_rfft_create(nfft);
    double *Yr = (double *)malloc((size_t)half * sizeof(double));
    double *Yi = (double *)malloc((size_t)half * sizeof(double));
    double *Hr = (double *)malloc((size_t)n_used * sizeof(double));
    double *Hi = (double *)malloc((size_t)n_used * sizeof(double));
    double *nv = (double *)malloc((size_t)n_used * sizeof(double));
    double *dre = (double *)malloc((size_t)n_used * sizeof(double));
    double *dim = (double *)malloc((size_t)n_used * sizeof(double));
    double *sre = (double *)malloc((size_t)n_used * sizeof(double));
    double *sim = (double *)malloc((size_t)n_used * sizeof(double));
    /* H0 from sync0, H1 from sync1; H = mean, diff for noise var */
    for (int w = 0; w < 2; ++w) {
        int pos = base + w * sym + cp;
        if (pos < 0 || pos + nfft > (int)rx_len) { free(sig); return -1; }
        cyrinx_rfft(fplan, sig + pos, Yr, Yi);
        double *xr, *xi;
        if (w == 0) { xr = sync0_re; xi = sync0_im; }
        else {
            cyrinx_phase_symbols(0x5EED + 1, n_used, sre, sim);
            xr = sre; xi = sim;
        }
        for (int k = 0; k < n_used; ++k) {
            int b = g.bin_lo + k;
            /* H = Y / X (X unit-modulus): Y * conj(X) / |X|^2, |X|=1 */
            double yr = Yr[b], yi = Yi[b];
            double hr = yr * xr[k] + yi * xi[k];   /* conj(X)=xr-ixi; Y*conj(X) */
            double hi = yi * xr[k] - yr * xi[k];
            if (w == 0) { Hr[k] = hr; Hi[k] = hi; }
            else {
                dre[k] = Hr[k] - hr; dim[k] = Hi[k] - hi;  /* H0 - H1 */
                Hr[k] = 0.5 * (Hr[k] + hr);
                Hi[k] = 0.5 * (Hi[k] + hi);
            }
        }
    }
    /* noise var: box-filter(|H0-H1|^2/2, 9) + 1e-12 */
    double *nraw = (double *)malloc((size_t)n_used * sizeof(double));
    for (int k = 0; k < n_used; ++k) nraw[k] = (dre[k] * dre[k] + dim[k] * dim[k]) * 0.5;
    for (int k = 0; k < n_used; ++k) {
        double acc = 0.0; int cnt = 0;
        for (int j = k - 4; j <= k + 4; ++j) {
            if (j >= 0 && j < n_used) { acc += nraw[j]; }
            cnt++; /* np.convolve 'same' with ones/9 divides by 9 incl. zero-pad */
        }
        nv[k] = acc / 9.0 + 1e-12;
    }
    /* snr_bin = |H|^2 / nv */
    double *snr = (double *)malloc((size_t)n_used * sizeof(double));
    for (int k = 0; k < n_used; ++k)
        snr[k] = (Hr[k] * Hr[k] + Hi[k] * Hi[k]) / nv[k];

    /* ---- pilots ---- */
    double *pil_re = (double *)malloc((size_t)g.n_pilots * sizeof(double));
    double *pil_im = (double *)malloc((size_t)g.n_pilots * sizeof(double));
    cyrinx_phase_symbols(0xBEEF, g.n_pilots, pil_re, pil_im);

    /* ---- per-symbol demap ---- */
    double *llr_stream = (double *)malloc((size_t)cap * sizeof(double));
    double *Zr = (double *)malloc((size_t)n_used * sizeof(double));
    double *Zi = (double *)malloc((size_t)n_used * sizeof(double));
    double evm_acc = 0.0;
    int lpos = 0;
    for (int s = 0; s < cfg->n_sym; ++s) {
        int pos = base + (2 + s) * sym + cp;
        if (pos < 0 || pos + nfft > (int)rx_len) { free(sig); return -1; }
        cyrinx_rfft(fplan, sig + pos, Yr, Yi);
        /* Z = Y / H  (G = 1) */
        for (int k = 0; k < n_used; ++k) {
            int b = g.bin_lo + k;
            double yr = Yr[b], yi = Yi[b], hr = Hr[k], hi = Hi[k];
            double den = hr * hr + hi * hi + 0.0;
            if (den < 1e-300) den = 1e-300;
            Zr[k] = (yr * hr + yi * hi) / den;
            Zi[k] = (yi * hr - yr * hi) / den;
        }
        /* pilot phase tracking: 3-pass slope, then CPE */
        int np = g.n_pilots;
        double *ewr = (double *)malloc((size_t)np * sizeof(double));
        double *ewi = (double *)malloc((size_t)np * sizeof(double));
        for (int m = 0; m < np; ++m) {
            int k = m * cfg->pilot_every;            /* pilot used-position */
            /* e = Z[pil] * conj(pilot) */
            ewr[m] = Zr[k] * pil_re[m] + Zi[k] * pil_im[m];
            ewi[m] = Zi[k] * pil_re[m] - Zr[k] * pil_im[m];
        }
        double slope_tot = 0.0;
        for (int it = 0; it < 3; ++it) {
            double sr = 0.0, si = 0.0;
            for (int m = 1; m < np; ++m) {
                /* d = ew[m]*conj(ew[m-1]) */
                double dr = ewr[m] * ewr[m - 1] + ewi[m] * ewi[m - 1];
                double di = ewi[m] * ewr[m - 1] - ewr[m] * ewi[m - 1];
                sr += dr; si += di;
            }
            double slope = atan2(si, sr) / (double)cfg->pilot_every;
            slope_tot += slope;
            for (int m = 0; m < np; ++m) {
                double ang = -slope * (double)(m * cfg->pilot_every);
                double cr = cos(ang), ci = sin(ang);
                double nr = ewr[m] * cr - ewi[m] * ci;
                double ni = ewr[m] * ci + ewi[m] * cr;
                ewr[m] = nr; ewi[m] = ni;
            }
        }
        double sr = 0.0, si = 0.0;
        for (int m = 0; m < np; ++m) { sr += ewr[m]; si += ewi[m]; }
        double ph0 = atan2(si, sr);
        for (int k = 0; k < n_used; ++k) {
            double ang = -(ph0 + slope_tot * (double)k);
            double cr = cos(ang), ci = sin(ang);
            double nr = Zr[k] * cr - Zi[k] * ci;
            double ni = Zr[k] * ci + Zi[k] * cr;
            Zr[k] = nr; Zi[k] = ni;
        }
        free(ewr); free(ewi);
        /* EVM^2 on pilots after correction */
        double evm2 = 0.0;
        for (int m = 0; m < np; ++m) {
            int k = m * cfg->pilot_every;
            double pr = Zr[k] * pil_re[m] + Zi[k] * pil_im[m];
            double pi = Zi[k] * pil_re[m] - Zr[k] * pil_im[m];
            double er = pr - 1.0;
            evm2 += er * er + pi * pi;
        }
        evm2 /= np;
        evm_acc += sqrt(evm2);
        /* demap data bins (positions k with k % pilot_every != 0) */
        for (int k = 0; k < n_used; ++k) {
            if (k % cfg->pilot_every == 0) continue;
            double s_ = snr[k] > 0.1 ? snr[k] : 0.1;
            double n0 = 1.0 / s_ + evm2;
            cyrinx_qam_llr(Zr[k], Zi[k], cfg->bits_per_bin, n0, llr_stream + lpos);
            lpos += cfg->bits_per_bin;
        }
    }
    if (evm_rms) *evm_rms = evm_acc / cfg->n_sym;

    /* ---- deinterleave: llr[i] = llr_stream[perm[i]] ---- */
    int64_t *perm = (int64_t *)malloc((size_t)cap * sizeof(int64_t));
    cyrinx_detrng_permutation(0x1EAF, perm, (size_t)cap);
    double *llr = (double *)malloc((size_t)cap * sizeof(double));
    for (int i = 0; i < cap; ++i) llr[i] = llr_stream[perm[i]];

    /* ---- depuncture into the full rate-1/2 stream ---- */
    int n_coded_full = (g.info_bits + 6) * 2;
    const uint8_t *pat = NULL;
    size_t patlen = cyrinx_puncture_pattern(cfg->rate, &pat);
    double *llr_full = (double *)calloc((size_t)n_coded_full, sizeof(double));
    int j = 0;
    for (int i = 0; i < n_coded_full; ++i) {
        if (pat[i % patlen]) llr_full[i] = llr[j++];
    }
    /* split even/odd -> the two coded-bit LLR streams */
    int nstep = n_coded_full / 2;
    double *l0 = (double *)malloc((size_t)nstep * sizeof(double));
    double *l1 = (double *)malloc((size_t)nstep * sizeof(double));
    for (int i = 0; i < nstep; ++i) { l0[i] = llr_full[2 * i]; l1[i] = llr_full[2 * i + 1]; }
    uint8_t *info = (uint8_t *)malloc((size_t)g.info_bits);
    cyrinx_viterbi(l0, l1, nstep, g.info_bits, info);

    /* ---- repack info bits -> stream bytes; CRC per block ---- */
    int ok = 0;
    for (int blk = 0; blk < g.n_blocks; ++blk) {
        uint8_t framed[CYRINX_BULK_CRC_BLOCK + 4];
        for (int by = 0; by < CYRINX_BULK_CRC_BLOCK + 4; ++by) {
            int bitbase = (blk * (CYRINX_BULK_CRC_BLOCK + 4) + by) * 8;
            uint8_t v = 0;
            for (int k = 0; k < 8; ++k) v = (uint8_t)((v << 1) | (info[bitbase + k] & 1u));
            framed[by] = v;
        }
        uint32_t crc = cyrinx_bulk_crc32(framed, CYRINX_BULK_CRC_BLOCK);
        uint32_t got = ((uint32_t)framed[CYRINX_BULK_CRC_BLOCK] << 24) |
                       ((uint32_t)framed[CYRINX_BULK_CRC_BLOCK + 1] << 16) |
                       ((uint32_t)framed[CYRINX_BULK_CRC_BLOCK + 2] << 8) |
                       (uint32_t)framed[CYRINX_BULK_CRC_BLOCK + 3];
        if (crc == got) ok++;
        memcpy(out_payload + blk * CYRINX_BULK_CRC_BLOCK, framed, CYRINX_BULK_CRC_BLOCK);
    }
    if (blocks_ok) *blocks_ok = ok;
    if (blocks_total) *blocks_total = g.n_blocks;

    cyrinx_rfft_destroy(fplan);
    free(sig); free(chirp); free(spec_re); free(spec_im); free(tbuf);
    free(sync0_re); free(sync0_im); free(ref); free(Yr); free(Yi); free(Hr);
    free(Hi); free(nv); free(dre); free(dim); free(sre); free(sim); free(nraw);
    free(snr); free(pil_re); free(pil_im); free(llr_stream); free(Zr); free(Zi);
    free(perm); free(llr); free(llr_full); free(l0); free(l1); free(info);
    return g.payload_bytes;
}
