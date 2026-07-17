/* Cyrinx wideband bulk PHY — portable C core. See cyrinx/cyrinx_bulk.h.
 * Reference: scratch/hw20k/modem.py. Validated against Tests/Fixtures/golden. */
#include "cyrinx/cyrinx_bulk.h"

#include <limits.h>
#include <math.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>

#include "cyrinx/cyrinx_fft.h"

/* ---------------- splitmix64 DetRng ---------------- */
/* modem.py:DetRng — constants are the standard splitmix64 mixer. */
#define CYRINX_SM64_GAMMA 0x9E3779B97F4A7C15ULL
#define CYRINX_SM64_MIX1 0xBF58476D1CE4E5B9ULL
#define CYRINX_SM64_MIX2 0x94D049BB133111EBULL
#define CYRINX_PI 3.14159265358979323846264338327950288

/* A two-symbol noise estimate can be spuriously tiny. Limit the per-bin
 * inverse-variance weight ratio between microphone branches to 40 dB. */
#define CYRINX_MRC_NV_RELATIVE_FLOOR 1e-4

/* Pilot phase fit weights are sync-SNR (inverse phase-variance) estimates.
 * Bound their leverage because a two-symbol noise estimate has optimistic
 * outliers. Keep these values in lockstep with modem.py. */
#define CYRINX_PILOT_SNR_WEIGHT_FLOOR 0.1
#define CYRINX_PILOT_SNR_WEIGHT_PERCENTILE 0.90

/* Turn the known-pilot residuals into a frequency-selective LLR reliability
 * estimate. The window and blend were frozen on one development capture before
 * replaying the held-out set; neither payload-bearing data-symbol bins nor
 * decoded results contribute. Public integer constants are the single source
 * of truth for both production arithmetic and the binary contract query. */
#define CYRINX_GLOBAL_PILOT_EVM_WEIGHT                                                                       \
    ((double)CYRINX_BULK_RECEIVER_GLOBAL_WEIGHT_NUMERATOR_V1 /                                               \
     (double)CYRINX_BULK_RECEIVER_WEIGHT_DENOMINATOR_V1)
#define CYRINX_LOCAL_PILOT_EVM_WEIGHT                                                                        \
    ((double)CYRINX_BULK_RECEIVER_LOCAL_WEIGHT_NUMERATOR_V1 /                                                \
     (double)CYRINX_BULK_RECEIVER_WEIGHT_DENOMINATOR_V1)

_Static_assert(sizeof(cyrinx_bulk_receiver_contract_v1) == 72,
               "receiver contract v1 must retain its fixed 72-byte layout");
_Static_assert(offsetof(cyrinx_bulk_receiver_contract_v1, snr_floor) == 56,
               "receiver contract v1 floating-point fields moved");
_Static_assert(offsetof(cyrinx_bulk_receiver_contract_v1, nonfinite_residual_ceiling) == 64,
               "receiver contract v1 residual field moved");
_Static_assert(CYRINX_BULK_RECEIVER_LOCAL_PILOT_WINDOW_V1 > 0 &&
                   (CYRINX_BULK_RECEIVER_LOCAL_PILOT_WINDOW_V1 % 2) == 1,
               "receiver local-pilot window must be positive and odd");
_Static_assert(CYRINX_BULK_RECEIVER_WEIGHT_DENOMINATOR_V1 > 0,
               "receiver reliability blend denominator must be positive");
_Static_assert(CYRINX_BULK_RECEIVER_GLOBAL_WEIGHT_NUMERATOR_V1 +
                       CYRINX_BULK_RECEIVER_LOCAL_WEIGHT_NUMERATOR_V1 ==
                   CYRINX_BULK_RECEIVER_WEIGHT_DENOMINATOR_V1,
               "receiver reliability blend weights must sum to the denominator");

static int cyrinx_compare_double(const void *a, const void *b) {
    double x = *(const double *)a;
    double y = *(const double *)b;
    return (x > y) - (x < y);
}

/* Build fixed weights from the two sync symbols only. Percentile interpolation
 * matches numpy.percentile's default linear method: index=(n-1)*q. */
static int cyrinx_pilot_phase_weights(const double *sync_snr, int n, double *weights) {
    double *ordered = (double *)malloc((size_t)n * sizeof(double));
    if (ordered == NULL)
        return -1;
    for (int i = 0; i < n; ++i) {
        double value = sync_snr[i];
        ordered[i] = (isfinite(value) && value > 0.0) ? value : 0.0;
    }
    qsort(ordered, (size_t)n, sizeof(double), cyrinx_compare_double);
    double index = (double)(n - 1) * CYRINX_PILOT_SNR_WEIGHT_PERCENTILE;
    int lower = (int)floor(index);
    int upper = (int)ceil(index);
    double fraction = index - (double)lower;
    double cap = ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction;
    if (cap < CYRINX_PILOT_SNR_WEIGHT_FLOOR)
        cap = CYRINX_PILOT_SNR_WEIGHT_FLOOR;
    double mean = 0.0;
    for (int i = 0; i < n; ++i) {
        double value = (isfinite(sync_snr[i]) && sync_snr[i] > 0.0) ? sync_snr[i] : 0.0;
        if (value < CYRINX_PILOT_SNR_WEIGHT_FLOOR)
            value = CYRINX_PILOT_SNR_WEIGHT_FLOOR;
        if (value > cap)
            value = cap;
        weights[i] = value;
        mean += value;
    }
    mean /= (double)n;
    for (int i = 0; i < n; ++i)
        weights[i] /= mean;
    free(ordered);
    return 0;
}

/* Fit slope and CPE from received pilot phases. The observations are projected
 * onto the unit circle, so their data-symbol magnitudes cannot become hidden
 * reliability weights. All explicit weights come from sync_snr above. */
static void cyrinx_fit_pilot_phase(double *ewr, double *ewi, const double *weights, int n, int pilot_every,
                                   double *slope_out, double *cpe_out) {
    for (int i = 0; i < n; ++i) {
        double magnitude = hypot(ewr[i], ewi[i]);
        if (isfinite(ewr[i]) && isfinite(ewi[i]) && isfinite(magnitude) && magnitude > 1e-12) {
            ewr[i] /= magnitude;
            ewi[i] /= magnitude;
        } else {
            /* Nonfinite or null observations carry no phase evidence. In
             * particular, never evaluate infinity/infinity while projecting
             * onto the unit circle. */
            ewr[i] = 0.0;
            ewi[i] = 0.0;
        }
    }
    double slope_total = 0.0;
    for (int it = 0; it < 3; ++it) {
        double sr = 0.0, si = 0.0;
        for (int i = 1; i < n; ++i) {
            /* A phase difference is only as reliable as its weaker endpoint.
             * Independent phase variances add, giving the harmonic SNR. */
            double left_weight = isfinite(weights[i - 1]) && weights[i - 1] > 0.0 ? weights[i - 1] : 0.0;
            double right_weight = isfinite(weights[i]) && weights[i] > 0.0 ? weights[i] : 0.0;
            double pair_weight = left_weight * right_weight / fmax(left_weight + right_weight, 1e-12);
            double dr = ewr[i] * ewr[i - 1] + ewi[i] * ewi[i - 1];
            double di = ewi[i] * ewr[i - 1] - ewr[i] * ewi[i - 1];
            sr += pair_weight * dr;
            si += pair_weight * di;
        }
        double slope = atan2(si, sr) / (double)pilot_every;
        slope_total += slope;
        for (int i = 0; i < n; ++i) {
            double angle = -slope * (double)(i * pilot_every);
            double cr = cos(angle), ci = sin(angle);
            double nr = ewr[i] * cr - ewi[i] * ci;
            double ni = ewr[i] * ci + ewi[i] * cr;
            ewr[i] = nr;
            ewi[i] = ni;
        }
    }
    double sr = 0.0, si = 0.0;
    for (int i = 0; i < n; ++i) {
        double weight = isfinite(weights[i]) && weights[i] > 0.0 ? weights[i] : 0.0;
        sr += weight * ewr[i];
        si += weight * ewi[i];
    }
    *slope_out = slope_total;
    *cpe_out = atan2(si, sr);
}

/* Smooth residual power over nearby known pilots only. Replicate the endpoint
 * pilot at the band edges (numpy.pad(mode="edge") followed by an 11-tap
 * boxcar in the frozen replay prototype), so every output has the same window
 * mass and a frequency-uniform residual remains uniform. */
static void cyrinx_smooth_pilot_evm2(const double *pilot_evm2, int n, double *smoothed_evm2) {
    const int radius = CYRINX_BULK_RECEIVER_LOCAL_PILOT_WINDOW_V1 / 2;
    for (int i = 0; i < n; ++i) {
        double sum = 0.0;
        for (int offset = -radius; offset <= radius; ++offset) {
            int64_t candidate = (int64_t)i + (int64_t)offset;
            if (candidate < 0)
                candidate = 0;
            else if (candidate >= n)
                candidate = n - 1;
            sum += pilot_evm2[(int)candidate];
        }
        smoothed_evm2[i] = sum / (double)CYRINX_BULK_RECEIVER_LOCAL_PILOT_WINDOW_V1;
    }
}

/* Linearly interpolate the smoothed pilot reliability onto a used-bin
 * position. The last comb interval can end without a right-hand pilot; extend
 * the final estimate over that short edge interval. */
static double cyrinx_interpolate_pilot_evm2(const double *smoothed_evm2, int n_pilots, int pilot_every,
                                            int used_position) {
    int left = used_position / pilot_every;
    if (left >= n_pilots - 1)
        return smoothed_evm2[n_pilots - 1];
    double fraction = (double)(used_position - left * pilot_every) / (double)pilot_every;
    return smoothed_evm2[left] * (1.0 - fraction) + smoothed_evm2[left + 1] * fraction;
}

int cyrinx_bulk_get_receiver_contract_v1(cyrinx_bulk_receiver_contract_v1 *out, size_t out_size) {
    if (out == NULL || out_size != sizeof(*out))
        return -1;

    cyrinx_bulk_receiver_contract_v1 contract;
    memset(&contract, 0, sizeof(contract));
    contract.struct_size = (uint32_t)sizeof(contract);
    contract.abi_version = CYRINX_BULK_RECEIVER_CONTRACT_ABI_VERSION;
    contract.semantics_version = CYRINX_BULK_RECEIVER_SEMANTICS_VERSION;
    contract.reliability_estimator = CYRINX_BULK_RECEIVER_ESTIMATOR_KNOWN_PILOT_LOCAL_LINEAR_BOXCAR_V1;
    contract.local_pilot_window = CYRINX_BULK_RECEIVER_LOCAL_PILOT_WINDOW_V1;
    contract.edge_mode = CYRINX_BULK_RECEIVER_EDGE_REPLICATE;
    contract.global_weight_numerator = CYRINX_BULK_RECEIVER_GLOBAL_WEIGHT_NUMERATOR_V1;
    contract.local_weight_numerator = CYRINX_BULK_RECEIVER_LOCAL_WEIGHT_NUMERATOR_V1;
    contract.weight_denominator = CYRINX_BULK_RECEIVER_WEIGHT_DENOMINATOR_V1;
    contract.final_comb_mode = CYRINX_BULK_RECEIVER_FINAL_COMB_EXTEND_LAST;
    contract.snr_floor = CYRINX_BULK_RECEIVER_SNR_FLOOR_V1;
    contract.nonfinite_residual_ceiling = CYRINX_BULK_RECEIVER_NONFINITE_RESIDUAL_CEILING_V1;
    *out = contract;
    return 0;
}

int cyrinx_bulk_receiver_reliability_diagnostic_v1(const double *pilot_evm2, int n_pilots, int pilot_every,
                                                   const int *used_positions, size_t used_position_count,
                                                   double *smoothed_out, size_t smoothed_cap,
                                                   double *interpolated_out, size_t interpolation_cap) {
    if (pilot_evm2 == NULL || n_pilots < 1 || pilot_every <= 0 || smoothed_out == NULL ||
        smoothed_cap < (size_t)n_pilots || (size_t)n_pilots > SIZE_MAX / sizeof(double) ||
        (used_position_count == 0 &&
         (used_positions != NULL || interpolated_out != NULL || interpolation_cap != 0)) ||
        (used_position_count != 0 &&
         (used_positions == NULL || interpolated_out == NULL || interpolation_cap < used_position_count)) ||
        used_position_count > SIZE_MAX / sizeof(double)) {
        return -1;
    }
    for (int i = 0; i < n_pilots; ++i) {
        if (!isfinite(pilot_evm2[i]) || pilot_evm2[i] < 0.0)
            return -1;
    }
    for (size_t i = 0; i < used_position_count; ++i) {
        if (used_positions[i] < 0)
            return -1;
    }

    double *temporary_smoothed = (double *)malloc((size_t)n_pilots * sizeof(double));
    double *temporary_interpolated = NULL;
    if (used_position_count != 0)
        temporary_interpolated = (double *)malloc(used_position_count * sizeof(double));
    if (temporary_smoothed == NULL || (used_position_count != 0 && temporary_interpolated == NULL)) {
        free(temporary_smoothed);
        free(temporary_interpolated);
        return -1;
    }

    cyrinx_smooth_pilot_evm2(pilot_evm2, n_pilots, temporary_smoothed);
    for (size_t i = 0; i < used_position_count; ++i) {
        temporary_interpolated[i] =
            cyrinx_interpolate_pilot_evm2(temporary_smoothed, n_pilots, pilot_every, used_positions[i]);
    }
    memcpy(smoothed_out, temporary_smoothed, (size_t)n_pilots * sizeof(double));
    if (used_position_count != 0)
        memcpy(interpolated_out, temporary_interpolated, used_position_count * sizeof(double));
    free(temporary_smoothed);
    free(temporary_interpolated);
    return 0;
}

void cyrinx_detrng_init(cyrinx_detrng *r, uint64_t seed) {
    r->s = seed;
}

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
    if (out == NULL || n == 0) {
        return;
    }
    cyrinx_detrng r;
    cyrinx_detrng_init(&r, seed);
    for (size_t i = 0; i < n; ++i) {
        out[i] = (int64_t)i;
    }
    if (n < 2) {
        return;
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
/* Reflected polynomial 0xEDB88320, init/xorout 0xFFFFFFFF. The table is built
 * once behind a C11 atomic lock because BulkPHY is Sendable and concurrent
 * first use must not race on mutable global state. */
static uint32_t g_crc32_table[256];
static atomic_bool g_crc32_ready = 0;
static atomic_flag g_crc32_init_lock = ATOMIC_FLAG_INIT;

static void cyrinx_crc32_init_table(void) {
    for (uint32_t i = 0; i < 256; ++i) {
        uint32_t c = i;
        for (int k = 0; k < 8; ++k) {
            c = (c & 1u) ? (0xEDB88320u ^ (c >> 1)) : (c >> 1);
        }
        g_crc32_table[i] = c;
    }
}

static void cyrinx_crc32_ensure_table(void) {
    if (atomic_load_explicit(&g_crc32_ready, memory_order_acquire)) {
        return;
    }
    while (atomic_flag_test_and_set_explicit(&g_crc32_init_lock, memory_order_acquire)) {
    }
    if (!atomic_load_explicit(&g_crc32_ready, memory_order_relaxed)) {
        cyrinx_crc32_init_table();
        atomic_store_explicit(&g_crc32_ready, 1, memory_order_release);
    }
    atomic_flag_clear_explicit(&g_crc32_init_lock, memory_order_release);
}

uint32_t cyrinx_bulk_crc32(const uint8_t *data, size_t len) {
    cyrinx_crc32_ensure_table();
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

size_t cyrinx_puncture(const uint8_t *coded, size_t n, const uint8_t *pattern, size_t p, uint8_t *out) {
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
    case 1:
        return 1.0;
    case 2:
        return sqrt(2.0);
    case 4:
        return sqrt(10.0);
    case 6:
        return sqrt(42.0);
    case 8:
        return sqrt(170.0);
    default:
        return 1.0;
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
    for (int b = 0; b < na; ++b) { /* MSB first */
        gi = (gi << 1) | (bits[b] & 1u);
        gq = (gq << 1) | (bits[na + b] & 1u);
    }
    double norm = cyrinx_qam_norm(nbits);
    *out_re = cyrinx_pam_level(gi, na) / norm;
    *out_im = cyrinx_pam_level(gq, na) / norm;
}

/* ---------------- Frame geometry ---------------- */
static int cyrinx_rate_fraction(const char *rate, int64_t *numerator, int64_t *denominator) {
    if (rate == NULL) {
        return -1;
    }
    if (strcmp(rate, "1/2") == 0) {
        *numerator = 1;
        *denominator = 2;
        return 0;
    }
    if (strcmp(rate, "2/3") == 0) {
        *numerator = 2;
        *denominator = 3;
        return 0;
    }
    if (strcmp(rate, "3/4") == 0) {
        *numerator = 3;
        *denominator = 4;
        return 0;
    }
    if (strcmp(rate, "5/6") == 0) {
        *numerator = 5;
        *denominator = 6;
        return 0;
    }
    return -1;
}

static int cyrinx_checked_mul_nonnegative_i64(int64_t lhs, int64_t rhs, int64_t *out) {
    if (lhs < 0 || rhs < 0 || (lhs != 0 && rhs > INT64_MAX / lhs)) {
        return -1;
    }
    *out = lhs * rhs;
    return 0;
}

static int cyrinx_is_supported_bits_per_bin(int bits_per_bin) {
    return bits_per_bin == 1 || bits_per_bin == 2 || bits_per_bin == 4 || bits_per_bin == 6 ||
           bits_per_bin == 8;
}

int cyrinx_bulk_compute_geometry(const cyrinx_bulk_config *cfg, cyrinx_bulk_geometry *out) {
    if (cfg == NULL || out == NULL) {
        return -1;
    }

    int64_t rate_numerator = 0;
    int64_t rate_denominator = 0;
    if (cyrinx_rate_fraction(cfg->rate, &rate_numerator, &rate_denominator) != 0 || cfg->sr <= 0 ||
        cfg->nfft < 2 || (cfg->nfft & 1) != 0 || cfg->cp < 0 || cfg->cp > cfg->nfft ||
        cfg->pilot_every <= 0 || !cyrinx_is_supported_bits_per_bin(cfg->bits_per_bin) || cfg->n_sym <= 0 ||
        !isfinite(cfg->f_lo) || !isfinite(cfg->f_hi) || !isfinite(cfg->amp) || cfg->amp <= 0.0 ||
        cfg->amp > 1.0 || !isfinite(cfg->clip_sigma) || cfg->clip_sigma <= 0.0 || cfg->clip_sigma > 10.0 ||
        !isfinite(cfg->chirp_f0) || !isfinite(cfg->chirp_f1) || cfg->chirp_f0 < 0.0 || cfg->chirp_f1 < 0.0) {
        return -1;
    }

    double nyquist = (double)cfg->sr / 2.0;
    if (cfg->f_lo < 0.0 || cfg->f_lo >= cfg->f_hi || cfg->f_hi > nyquist) {
        return -1;
    }
    double chirp_f0 = cfg->chirp_f0 > 0.0 ? cfg->chirp_f0 : CYRINX_BULK_CHIRP_F0;
    double chirp_f1 = cfg->chirp_f1 > 0.0 ? cfg->chirp_f1 : CYRINX_BULK_CHIRP_F1;
    if (chirp_f0 < 0.0 || chirp_f0 >= chirp_f1 || chirp_f1 > nyquist) {
        return -1;
    }

    double bin_hz = (double)cfg->sr / (double)cfg->nfft;
    int bin_lo = (int)ceil(cfg->f_lo / bin_hz);
    int bin_hi = (int)floor(cfg->f_hi / bin_hz);
    /* DC and Nyquist are self-conjugate in a real FFT and therefore cannot
     * carry the complex QAM/pilot values used by this modem. */
    if (bin_lo <= 0 || bin_hi >= cfg->nfft / 2) {
        return -1;
    }
    int64_t n_used = (int64_t)bin_hi - (int64_t)bin_lo + 1;
    if (n_used <= 0 || n_used > INT_MAX) {
        return -1;
    }
    /* pilots at used[::pilot_every] */
    int64_t n_pilots = (n_used + (int64_t)cfg->pilot_every - 1) / (int64_t)cfg->pilot_every;
    int64_t n_data_bins = n_used - n_pilots;
    if (n_pilots < 2 || n_pilots > INT_MAX || n_data_bins < 1 || n_data_bins > INT_MAX) {
        return -1;
    }

    int64_t bits_per_sym = 0;
    int64_t cap = 0;
    int64_t rated_capacity = 0;
    if (cyrinx_checked_mul_nonnegative_i64(n_data_bins, cfg->bits_per_bin, &bits_per_sym) != 0 ||
        bits_per_sym > INT_MAX || cyrinx_checked_mul_nonnegative_i64(bits_per_sym, cfg->n_sym, &cap) != 0 ||
        cap > INT_MAX || cyrinx_checked_mul_nonnegative_i64(cap, rate_numerator, &rated_capacity) != 0) {
        return -1;
    }
    int64_t info = rated_capacity / rate_denominator - 6;
    int64_t block_bits = (CYRINX_BULK_CRC_BLOCK + 4) * 8;
    int64_t n_blocks = info / block_bits;
    int64_t payload_bytes = 0;
    int64_t coded_bits = 0;
    if (info <= 0 || info > INT_MAX || n_blocks < 1 || n_blocks > INT_MAX ||
        cyrinx_checked_mul_nonnegative_i64(n_blocks, CYRINX_BULK_CRC_BLOCK, &payload_bytes) != 0 ||
        payload_bytes > INT_MAX || cyrinx_checked_mul_nonnegative_i64(info + 6, 2, &coded_bits) != 0 ||
        coded_bits > INT_MAX) {
        return -1;
    }

    int64_t symbol_samples = (int64_t)cfg->nfft + cfg->cp;
    int64_t frame_body_samples = 0;
    if (symbol_samples > INT_MAX ||
        cyrinx_checked_mul_nonnegative_i64((int64_t)cfg->n_sym + 2, symbol_samples, &frame_body_samples) !=
            0 ||
        frame_body_samples > INT_MAX - CYRINX_BULK_CHIRP_LEN - CYRINX_BULK_GUARD) {
        return -1;
    }
    int64_t frame_samples = CYRINX_BULK_CHIRP_LEN + CYRINX_BULK_GUARD + frame_body_samples;

    cyrinx_bulk_geometry geometry;
    geometry.bin_lo = bin_lo;
    geometry.bin_hi = bin_hi;
    geometry.n_used = (int)n_used;
    geometry.n_pilots = (int)n_pilots;
    geometry.n_data_bins = (int)n_data_bins;
    geometry.bits_per_sym = (int)bits_per_sym;
    geometry.cap = (int)cap;
    geometry.info_bits = (int)info;
    geometry.n_blocks = (int)n_blocks;
    geometry.payload_bytes = (int)payload_bytes;
    geometry.frame_samples = (int)frame_samples;
    *out = geometry;
    return 0;
}

/* ---------------- TX helpers ---------------- */
/* modem.py:make_chirp — linear chirp f0..f1 with 128-sample raised-cosine ramps. */
static void cyrinx_make_chirp(double *out, int n, double sr, double f0, double f1) {
    double T = (double)n / sr;
    int r = 128;
    for (int i = 0; i < n; ++i) {
        double t = (double)i / sr;
        double ph = 2.0 * CYRINX_PI * (f0 * t + 0.5 * (f1 - f0) * t * t / T);
        double w = sin(ph);
        double env = 1.0;
        if (i < r) {
            env = 0.5 - 0.5 * cos(CYRINX_PI * (double)i / (double)r);
        } else if (i >= n - r) {
            int j = n - 1 - i; /* mirror of the leading ramp */
            env = 0.5 - 0.5 * cos(CYRINX_PI * (double)j / (double)r);
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
        double theta = CYRINX_PI / 4.0 + CYRINX_PI / 2.0 * (double)ph;
        re[i] = cos(theta);
        im[i] = sin(theta);
    }
}

/* OFDM symbol: place freq values on used bins, irfft, prepend CP. Writes
 * (cp+nfft) real samples to `out`. */
static void cyrinx_ofdm_mod_symbol(cyrinx_irfft_plan *plan, int nfft, int cp, int bin_lo, int n_used,
                                   const double *fv_re, const double *fv_im, double *spec_re, double *spec_im,
                                   double *tbuf, double *out) {
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

long cyrinx_bulk_modulate(const cyrinx_bulk_config *cfg, const uint8_t *payload, size_t payload_len,
                          float *wave_out, size_t wave_cap, double *data_freq_out) {
    cyrinx_bulk_geometry g;
    if (cyrinx_bulk_compute_geometry(cfg, &g) != 0) {
        return -1;
    }
    if (payload == NULL || wave_out == NULL || payload_len != (size_t)g.payload_bytes ||
        (size_t)g.frame_samples > wave_cap) {
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
        cyrinx_ofdm_mod_symbol(plan, nfft, cp, g.bin_lo, n_used, sync_re, sync_im, spec_re, spec_im, tbuf,
                               x + (size_t)w * sym);
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
        cyrinx_ofdm_mod_symbol(plan, nfft, cp, g.bin_lo, n_used, fv_re, fv_im, spec_re, spec_im, tbuf,
                               x + (size_t)(2 + s) * sym);
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
        if (x[i] > lim)
            x[i] = lim;
        else if (x[i] < -lim)
            x[i] = -lim;
        double a = fabs(x[i]);
        if (a > peak)
            peak = a;
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

    free(bits);
    free(coded);
    free(punc);
    free(filled);
    free(perm);
    free(inter);
    free(pilot_re);
    free(pilot_im);
    free(spec_re);
    free(spec_im);
    free(tbuf);
    free(fv_re);
    free(fv_im);
    free(x);
    free(sync_re);
    free(sync_im);
    free(chirp);
    cyrinx_irfft_destroy(plan);
    return w;
}

/* ================= RX ================= */
/* Reference: modem.py:demodulate_frame (single-mic, track_alpha=0 path). */

/* Max-log QAM LLRs (log P0/P1) for one symbol with noise var n0. Writes nbits
 * values. modem.py:qam_llr. */
static void cyrinx_qam_llr(double zre, double zim, int nbits, double n0, double *out) {
    /* Erasures are safer than allowing a nonfinite observation or variance to
     * inject NaNs (or accidental high confidence) into Viterbi path metrics. */
    if (!isfinite(zre) || !isfinite(zim) || !isfinite(n0)) {
        for (int i = 0; i < nbits; ++i)
            out[i] = 0.0;
        return;
    }
    double inv = 1.0 / (n0 > 1e-9 ? n0 : 1e-9);
    if (nbits == 1) {
        double llr = 4.0 * zre * inv; /* BPSK */
        out[0] = isfinite(llr) ? llr : 0.0;
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
                    if (d2 < d1)
                        d1 = d2;
                } else {
                    if (d2 < d0)
                        d0 = d2;
                }
            }
            double llr = (d1 - d0) * inv;
            out[axis * na + bit] = isfinite(llr) ? llr : 0.0;
        }
    }
}

/* Soft Viterbi for the K=7 (171,133) code. llr0/llr1: per-step LLRs (log P0/P1)
 * for the two coded bits. Writes n_info decoded bits. modem.py:viterbi_decode. */
static void cyrinx_viterbi(const double *llr0, const double *llr1, int n, int n_info, uint8_t *out) {
    /* trellis: next[s][b], out0[s][b], out1[s][b] */
    uint8_t next_s[64][2], o0[64][2], o1[64][2];
    for (int s = 0; s < 64; ++s) {
        for (int b = 0; b < 2; ++b) {
            uint32_t reg = ((uint32_t)b << 6) | (uint32_t)s;
            next_s[s][b] = (uint8_t)(reg >> 1);
            o0[s][b] = cyrinx_parity7(reg & CYRINX_CONV_G0);
            o1[s][b] = cyrinx_parity7(reg & CYRINX_CONV_G1);
        }
    }
    double *metric = (double *)malloc(64 * sizeof(double));
    double *nm = (double *)malloc(64 * sizeof(double));
    uint8_t *back = (uint8_t *)malloc((size_t)n * 64); /* back[i*64+state] = s*2+b */
    for (int s = 0; s < 64; ++s)
        metric[s] = -1e12;
    metric[0] = 0.0;
    for (int i = 0; i < n; ++i) {
        double l0 = llr0[i], l1 = llr1[i];
        for (int s = 0; s < 64; ++s)
            nm[s] = -1e12;
        for (int s = 0; s < 64; ++s) {
            if (metric[s] <= -1e11)
                continue;
            for (int b = 0; b < 2; ++b) {
                double bm = (o0[s][b] == 0 ? 0.5 * l0 : -0.5 * l0) + (o1[s][b] == 0 ? 0.5 * l1 : -0.5 * l1);
                double cand = metric[s] + bm;
                int nx = next_s[s][b];
                if (cand >= nm[nx]) { /* >= so later (ties) win, matching argsort */
                    nm[nx] = cand;
                    back[(size_t)i * 64 + nx] = (uint8_t)(s * 2 + b);
                }
            }
        }
        double *t = metric;
        metric = nm;
        nm = t;
    }
    /* traceback from state 0 (zero-terminated) */
    int s = 0;
    uint8_t *bits = (uint8_t *)malloc((size_t)n);
    for (int i = n - 1; i >= 0; --i) {
        uint8_t o = back[(size_t)i * 64 + s];
        bits[i] = o & 1u;
        s = o >> 1;
    }
    for (int i = 0; i < n_info; ++i)
        out[i] = bits[i];
    free(metric);
    free(nm);
    free(back);
    free(bits);
}

/* Cross-correlation matched filter (np.correlate valid): returns argmax |sum
 * sig[i+k]*ref[k]| over i in [0, sig_len-ref_len]. */
static int cyrinx_matched_filter(const double *sig, int sig_len, const double *ref, int ref_len, int start) {
    int best_i = start;
    double best = -1.0;
    for (int i = start; i + ref_len <= sig_len; ++i) {
        double acc = 0.0;
        for (int k = 0; k < ref_len; ++k)
            acc += sig[i + k] * ref[k];
        double a = fabs(acc);
        if (a > best) {
            best = a;
            best_i = i;
        }
    }
    return best_i;
}

static void cyrinx_init_diversity_diagnostics(cyrinx_bulk_diversity_diagnostics *diagnostics) {
    if (diagnostics == NULL)
        return;
    diagnostics->struct_size = (uint32_t)sizeof(*diagnostics);
    diagnostics->abi_version = CYRINX_BULK_DIVERSITY_DIAGNOSTICS_ABI_VERSION;
    diagnostics->policy_version = CYRINX_BULK_AUTO_V1_POLICY_VERSION;
    diagnostics->selected_receiver = CYRINX_BULK_DIVERSITY_PRIMARY;
    diagnostics->scores_valid = 0;
    diagnostics->selection_reason = CYRINX_BULK_AUTO_REASON_NOT_EVALUATED;
    diagnostics->validation_observations = 0;
    diagnostics->primary_holdout_pilot_rms = INFINITY;
    diagnostics->mrc_holdout_pilot_rms = INFINITY;
    diagnostics->observed_mrc_to_primary_pilot_rms_ratio = INFINITY;
    diagnostics->maximum_mrc_to_primary_pilot_rms_ratio = CYRINX_BULK_AUTO_V1_MAX_MRC_PILOT_RMS_RATIO;
}

static void cyrinx_apply_mrc_noise_floor(double *nv, int n_used) {
    for (int k = 0; k < n_used; ++k) {
        double shared_nv = fmax(nv[k], nv[n_used + k]);
        double relative_floor = shared_nv * CYRINX_MRC_NV_RELATIVE_FLOOR;
        if (nv[k] < relative_floor)
            nv[k] = relative_floor;
        if (nv[n_used + k] < relative_floor)
            nv[n_used + k] = relative_floor;
    }
}

static void cyrinx_snr_for_receiver(const double *hr, const double *hi, const double *nv, int n_used,
                                    int use_mrc, double *snr) {
    int channels = use_mrc ? 2 : 1;
    for (int k = 0; k < n_used; ++k) {
        double value = 0.0;
        for (int ch = 0; ch < channels; ++ch) {
            double h_re = hr[ch * n_used + k];
            double h_im = hi[ch * n_used + k];
            value += (h_re * h_re + h_im * h_im) / nv[ch * n_used + k];
        }
        snr[k] = value;
    }
}

/* Compare the primary and MRC front ends using known pilots only. Even pilot
 * ordinals estimate phase/timing slope; odd ordinals are held out for scoring.
 * This function runs before LLR generation, Viterbi decoding, or CRC checks. */
static int cyrinx_select_auto_v1_receiver(const cyrinx_bulk_config *cfg, const cyrinx_bulk_geometry *g,
                                          cyrinx_rfft_plan *fplan, const double *sig0, const double *sig1,
                                          int base, const double *hr, const double *hi,
                                          const double *nv_primary, const double *nv_mrc,
                                          const double *pil_re, const double *pil_im,
                                          cyrinx_bulk_diversity_diagnostics *diagnostics) {
    cyrinx_init_diversity_diagnostics(diagnostics);
    int n_pilots = g->n_pilots;
    int training_count = (n_pilots + 1) / 2;
    int holdout_count = n_pilots / 2;
    if (training_count < 2 || holdout_count < 1 || cfg->pilot_every > INT_MAX / 2) {
        if (diagnostics != NULL)
            diagnostics->selection_reason = CYRINX_BULK_AUTO_REASON_INSUFFICIENT_PILOTS;
        return CYRINX_BULK_DIVERSITY_PRIMARY;
    }

    int64_t observation_count64 = (int64_t)cfg->n_sym * (int64_t)holdout_count;
    if (observation_count64 <= 0 || observation_count64 > INT_MAX) {
        if (diagnostics != NULL)
            diagnostics->selection_reason = CYRINX_BULK_AUTO_REASON_INSUFFICIENT_PILOTS;
        return CYRINX_BULK_DIVERSITY_PRIMARY;
    }
    int observation_count = (int)observation_count64;
    int n_used = g->n_used;
    int half = cfg->nfft / 2 + 1;
    size_t pilot_values = (size_t)2 * (size_t)n_pilots;

    double *yr = (double *)malloc((size_t)half * sizeof(double));
    double *yi = (double *)malloc((size_t)half * sizeof(double));
    double *y_re = (double *)malloc(pilot_values * sizeof(double));
    double *y_im = (double *)malloc(pilot_values * sizeof(double));
    double *z_re = (double *)malloc((size_t)n_pilots * sizeof(double));
    double *z_im = (double *)malloc((size_t)n_pilots * sizeof(double));
    double *training_re = (double *)malloc((size_t)training_count * sizeof(double));
    double *training_im = (double *)malloc((size_t)training_count * sizeof(double));
    double *training_weights = (double *)malloc((size_t)training_count * sizeof(double));
    double *pilot_snr = (double *)malloc((size_t)n_pilots * sizeof(double));
    double *primary_weights = (double *)malloc((size_t)n_pilots * sizeof(double));
    double *mrc_weights = (double *)malloc((size_t)n_pilots * sizeof(double));
    if (yr == NULL || yi == NULL || y_re == NULL || y_im == NULL || z_re == NULL || z_im == NULL ||
        training_re == NULL || training_im == NULL || training_weights == NULL || pilot_snr == NULL ||
        primary_weights == NULL || mrc_weights == NULL)
        goto resource_failure;

    for (int m = 0; m < n_pilots; ++m) {
        int k = m * cfg->pilot_every;
        double h_re = hr[k], h_im = hi[k];
        pilot_snr[m] = (h_re * h_re + h_im * h_im) / nv_primary[k];
    }
    if (cyrinx_pilot_phase_weights(pilot_snr, n_pilots, primary_weights) != 0)
        goto resource_failure;
    for (int m = 0; m < n_pilots; ++m) {
        int k = m * cfg->pilot_every;
        double value = 0.0;
        for (int ch = 0; ch < 2; ++ch) {
            double h_re = hr[ch * n_used + k], h_im = hi[ch * n_used + k];
            value += (h_re * h_re + h_im * h_im) / nv_mrc[ch * n_used + k];
        }
        pilot_snr[m] = value;
    }
    if (cyrinx_pilot_phase_weights(pilot_snr, n_pilots, mrc_weights) != 0)
        goto resource_failure;

    double squared_error[2] = {0.0, 0.0};
    const double *signals[2] = {sig0, sig1};
    for (int symbol = 0; symbol < cfg->n_sym; ++symbol) {
        int pos = base + (2 + symbol) * (cfg->nfft + cfg->cp) + cfg->cp;
        for (int ch = 0; ch < 2; ++ch) {
            cyrinx_rfft(fplan, signals[ch] + pos, yr, yi);
            for (int m = 0; m < n_pilots; ++m) {
                int k = m * cfg->pilot_every;
                int b = g->bin_lo + k;
                y_re[ch * n_pilots + m] = yr[b];
                y_im[ch * n_pilots + m] = yi[b];
            }
        }

        for (int receiver = 0; receiver < 2; ++receiver) {
            const double *weights = receiver == CYRINX_BULK_DIVERSITY_PRIMARY ? primary_weights : mrc_weights;
            for (int m = 0; m < n_pilots; ++m) {
                int k = m * cfg->pilot_every;
                double numerator_re = 0.0, numerator_im = 0.0, denominator = 0.0;
                int channels = receiver == CYRINX_BULK_DIVERSITY_MRC ? 2 : 1;
                for (int ch = 0; ch < channels; ++ch) {
                    double h_re = hr[ch * n_used + k], h_im = hi[ch * n_used + k];
                    double sample_re = y_re[ch * n_pilots + m];
                    double sample_im = y_im[ch * n_pilots + m];
                    if (receiver == CYRINX_BULK_DIVERSITY_PRIMARY) {
                        numerator_re += sample_re * h_re + sample_im * h_im;
                        numerator_im += sample_im * h_re - sample_re * h_im;
                        denominator += h_re * h_re + h_im * h_im;
                    } else {
                        double inverse_noise = 1.0 / nv_mrc[ch * n_used + k];
                        numerator_re += (sample_re * h_re + sample_im * h_im) * inverse_noise;
                        numerator_im += (sample_im * h_re - sample_re * h_im) * inverse_noise;
                        denominator += (h_re * h_re + h_im * h_im) * inverse_noise;
                    }
                }
                double divisor = receiver == CYRINX_BULK_DIVERSITY_PRIMARY ? fmax(denominator, 1e-300)
                                                                           : denominator + 1e-12;
                z_re[m] = numerator_re / divisor;
                z_im[m] = numerator_im / divisor;
            }

            int training_index = 0;
            for (int m = 0; m < n_pilots; m += 2) {
                training_re[training_index] = z_re[m] * pil_re[m] + z_im[m] * pil_im[m];
                training_im[training_index] = z_im[m] * pil_re[m] - z_re[m] * pil_im[m];
                training_weights[training_index] = weights[m];
                training_index++;
            }
            double slope = 0.0, phase = 0.0;
            cyrinx_fit_pilot_phase(training_re, training_im, training_weights, training_count,
                                   cfg->pilot_every * 2, &slope, &phase);
            for (int m = 1; m < n_pilots; m += 2) {
                int k = m * cfg->pilot_every;
                double angle = -(phase + slope * (double)k);
                double cosine = cos(angle), sine = sin(angle);
                double corrected_re = z_re[m] * cosine - z_im[m] * sine;
                double corrected_im = z_re[m] * sine + z_im[m] * cosine;
                double residual_re = corrected_re * pil_re[m] + corrected_im * pil_im[m] - 1.0;
                double residual_im = corrected_im * pil_re[m] - corrected_re * pil_im[m];
                squared_error[receiver] += residual_re * residual_re + residual_im * residual_im;
            }
        }
    }

    double primary_mse = squared_error[CYRINX_BULK_DIVERSITY_PRIMARY] / (double)observation_count;
    double mrc_mse = squared_error[CYRINX_BULK_DIVERSITY_MRC] / (double)observation_count;
    int scores_valid = isfinite(primary_mse) && primary_mse >= 0.0 && isfinite(mrc_mse) && mrc_mse >= 0.0;
    double maximum_ratio = CYRINX_BULK_AUTO_V1_MAX_MRC_PILOT_RMS_RATIO;
    int selected = CYRINX_BULK_DIVERSITY_PRIMARY;
    if (scores_valid && mrc_mse < maximum_ratio * maximum_ratio * primary_mse)
        selected = CYRINX_BULK_DIVERSITY_MRC;
    if (diagnostics != NULL) {
        double primary_pilot_rms = scores_valid ? sqrt(primary_mse) : INFINITY;
        double mrc_pilot_rms = scores_valid ? sqrt(mrc_mse) : INFINITY;
        double observed_ratio =
            scores_valid && primary_pilot_rms > 0.0 ? mrc_pilot_rms / primary_pilot_rms : INFINITY;
        diagnostics->selected_receiver = selected;
        diagnostics->scores_valid = scores_valid;
        diagnostics->selection_reason =
            !scores_valid
                ? CYRINX_BULK_AUTO_REASON_NONFINITE_SCORE
                : (selected == CYRINX_BULK_DIVERSITY_MRC ? CYRINX_BULK_AUTO_REASON_MRC_IMPROVED
                                                         : CYRINX_BULK_AUTO_REASON_PRIMARY_MARGIN_NOT_MET);
        diagnostics->validation_observations = observation_count;
        diagnostics->primary_holdout_pilot_rms = primary_pilot_rms;
        diagnostics->mrc_holdout_pilot_rms = mrc_pilot_rms;
        diagnostics->observed_mrc_to_primary_pilot_rms_ratio = observed_ratio;
    }

    free(yr);
    free(yi);
    free(y_re);
    free(y_im);
    free(z_re);
    free(z_im);
    free(training_re);
    free(training_im);
    free(training_weights);
    free(pilot_snr);
    free(primary_weights);
    free(mrc_weights);
    return selected;

resource_failure:
    free(yr);
    free(yi);
    free(y_re);
    free(y_im);
    free(z_re);
    free(z_im);
    free(training_re);
    free(training_im);
    free(training_weights);
    free(pilot_snr);
    free(primary_weights);
    free(mrc_weights);
    if (diagnostics != NULL) {
        cyrinx_init_diversity_diagnostics(diagnostics);
        diagnostics->selection_reason = CYRINX_BULK_AUTO_REASON_RESOURCE_FAILURE;
    }
    return CYRINX_BULK_DIVERSITY_PRIMARY;
}

/* Shared single/dual-mic demodulator. rx2 == NULL -> single-mic (bit-identical
 * to the original cyrinx_bulk_demodulate); rx2 != NULL -> per-subcarrier MRC
 * (ports modem.py demodulate_frame(rx2=...), the validated Python reference).
 * Sync runs on rx; rx2 must be sample-aligned (a second channel of the same
 * capture). */
static long cyrinx_bulk_demod_impl(const cyrinx_bulk_config *cfg, const float *rx, size_t rx_len,
                                   const float *rx2, size_t rx2_len, uint8_t *out_payload, size_t out_cap,
                                   int *blocks_ok, int *blocks_total, double *evm_rms,
                                   uint8_t *block_valid_out, size_t block_valid_cap, int auto_v1,
                                   cyrinx_bulk_diversity_diagnostics *diagnostics) {
    if (auto_v1)
        cyrinx_init_diversity_diagnostics(diagnostics);
    cyrinx_bulk_geometry g;
    if (cyrinx_bulk_compute_geometry(cfg, &g) != 0)
        return -1;
    if (rx == NULL || out_payload == NULL || rx_len < (size_t)g.frame_samples || rx_len > INT_MAX ||
        (rx2 == NULL && rx2_len != 0) ||
        (rx2 != NULL && (rx2_len < (size_t)g.frame_samples || rx2_len > INT_MAX)) ||
        (size_t)g.payload_bytes > out_cap)
        return -1;
    if ((block_valid_out == NULL) != (block_valid_cap == 0))
        return -1;
    if (block_valid_out != NULL && (size_t)g.n_blocks > block_valid_cap)
        return -1;
    if (block_valid_out != NULL) {
        memset(block_valid_out, 0, (size_t)g.n_blocks * sizeof(*block_valid_out));
    }
    const int nfft = cfg->nfft, cp = cfg->cp, sym = nfft + cp;
    const int n_used = g.n_used, cap = g.cap;
    const int half = nfft / 2 + 1;
    const int nch = rx2 ? 2 : 1;

    double *sig = (double *)malloc(rx_len * sizeof(double));
    for (size_t i = 0; i < rx_len; ++i)
        sig[i] = (double)rx[i];
    double *sig1 = NULL;
    if (rx2) {
        sig1 = (double *)malloc(rx2_len * sizeof(double));
        for (size_t i = 0; i < rx2_len; ++i)
            sig1[i] = (double)rx2[i];
    }

    /* ---- coarse sync: chirp matched filter ---- */
    double *chirp = (double *)malloc((size_t)CYRINX_BULK_CHIRP_LEN * sizeof(double));
    double cf0 = cfg->chirp_f0 > 0 ? cfg->chirp_f0 : CYRINX_BULK_CHIRP_F0;
    double cf1 = cfg->chirp_f1 > 0 ? cfg->chirp_f1 : CYRINX_BULK_CHIRP_F1;
    cyrinx_make_chirp(chirp, CYRINX_BULK_CHIRP_LEN, (double)cfg->sr, cf0, cf1);
    int start = cyrinx_matched_filter(sig, (int)rx_len, chirp, CYRINX_BULK_CHIRP_LEN, 0);
    int64_t coarse_base = (int64_t)start + CYRINX_BULK_CHIRP_LEN + CYRINX_BULK_GUARD;

    /* ---- fine sync: correlate against the first sync OFDM symbol ---- */
    cyrinx_irfft_plan *iplan = cyrinx_irfft_create(nfft);
    double *spec_re = (double *)malloc((size_t)half * sizeof(double));
    double *spec_im = (double *)malloc((size_t)half * sizeof(double));
    double *tbuf = (double *)malloc((size_t)nfft * sizeof(double));
    double *sync0_re = (double *)malloc((size_t)n_used * sizeof(double));
    double *sync0_im = (double *)malloc((size_t)n_used * sizeof(double));
    double *ref = (double *)malloc((size_t)sym * sizeof(double));
    cyrinx_phase_symbols(0x5EED, n_used, sync0_re, sync0_im);
    cyrinx_ofdm_mod_symbol(iplan, nfft, cp, g.bin_lo, n_used, sync0_re, sync0_im, spec_re, spec_im, tbuf,
                           ref);
    int fine_window = 400;
    int64_t lo64 = coarse_base - fine_window;
    if (lo64 < 0)
        lo64 = 0;
    if (coarse_base > INT_MAX || lo64 > INT_MAX || lo64 + sym > (int64_t)rx_len) {
        cyrinx_irfft_destroy(iplan);
        free(sig);
        free(sig1);
        free(chirp);
        free(spec_re);
        free(spec_im);
        free(tbuf);
        free(sync0_re);
        free(sync0_im);
        free(ref);
        return -1;
    }
    int lo = (int)lo64;
    int64_t desired_stop = coarse_base + fine_window + sym;
    int stop = desired_stop < (int64_t)rx_len ? (int)desired_stop : (int)rx_len;
    int seg_len = stop - lo;
    int off = cyrinx_matched_filter(sig + lo, seg_len, ref, sym, 0);
    int base = lo + off;
    base -= 24; /* bias early so pre-cursor taps stay in the CP */
    cyrinx_irfft_destroy(iplan);

    int64_t required_stop = (int64_t)base + ((int64_t)2 + cfg->n_sym) * sym;
    if (base < 0 || required_stop > (int64_t)rx_len || (rx2 != NULL && required_stop > (int64_t)rx2_len)) {
        free(sig);
        free(sig1);
        free(chirp);
        free(spec_re);
        free(spec_im);
        free(tbuf);
        free(sync0_re);
        free(sync0_im);
        free(ref);
        return -1;
    }

    /* ---- channel estimation from the 2 sync symbols, per microphone ---- */
    cyrinx_rfft_plan *fplan = cyrinx_rfft_create(nfft);
    double *Yr = (double *)malloc((size_t)half * sizeof(double));
    double *Yi = (double *)malloc((size_t)half * sizeof(double));
    double *Hr = (double *)malloc((size_t)(nch * n_used) * sizeof(double));
    double *Hi = (double *)malloc((size_t)(nch * n_used) * sizeof(double));
    double *nv = (double *)malloc((size_t)(nch * n_used) * sizeof(double));
    double *dre = (double *)malloc((size_t)n_used * sizeof(double));
    double *dim = (double *)malloc((size_t)n_used * sizeof(double));
    double *sre = (double *)malloc((size_t)n_used * sizeof(double));
    double *sim = (double *)malloc((size_t)n_used * sizeof(double));
    double *nraw = (double *)malloc((size_t)n_used * sizeof(double));
    cyrinx_phase_symbols(0x5EED + 1, n_used, sre, sim);
    for (int ch = 0; ch < nch; ++ch) {
        const double *csig = ch ? sig1 : sig;
        double *chr = Hr + ch * n_used, *chi = Hi + ch * n_used;
        double *cnv = nv + ch * n_used;
        /* H0 from sync0, H1 from sync1; H = mean, diff for noise var */
        for (int w = 0; w < 2; ++w) {
            int pos = base + w * sym + cp;
            cyrinx_rfft(fplan, csig + pos, Yr, Yi);
            const double *xr = (w == 0) ? sync0_re : sre;
            const double *xi = (w == 0) ? sync0_im : sim;
            for (int k = 0; k < n_used; ++k) {
                int b = g.bin_lo + k;
                /* H = Y / X (X unit-modulus): Y * conj(X) / |X|^2, |X|=1 */
                double yr = Yr[b], yi = Yi[b];
                double h_r = yr * xr[k] + yi * xi[k]; /* conj(X)=xr-ixi; Y*conj(X) */
                double h_i = yi * xr[k] - yr * xi[k];
                if (w == 0) {
                    chr[k] = h_r;
                    chi[k] = h_i;
                } else {
                    dre[k] = chr[k] - h_r;
                    dim[k] = chi[k] - h_i; /* H0 - H1 */
                    chr[k] = 0.5 * (chr[k] + h_r);
                    chi[k] = 0.5 * (chi[k] + h_i);
                }
            }
        }
        /* noise var: box-filter(|H0-H1|^2/2, 9) + 1e-12 */
        for (int k = 0; k < n_used; ++k)
            nraw[k] = (dre[k] * dre[k] + dim[k] * dim[k]) * 0.5;
        for (int k = 0; k < n_used; ++k) {
            double acc = 0.0;
            for (int j = k - 4; j <= k + 4; ++j) {
                /* np.convolve 'same' with ones/9 divides by 9 incl. zero-pad */
                if (j >= 0 && j < n_used)
                    acc += nraw[j];
            }
            cnv[k] = acc / 9.0 + 1e-12;
        }
    }
    /* ---- pilots ---- */
    double *pil_re = (double *)malloc((size_t)g.n_pilots * sizeof(double));
    double *pil_im = (double *)malloc((size_t)g.n_pilots * sizeof(double));
    cyrinx_phase_symbols(0xBEEF, g.n_pilots, pil_re, pil_im);

    /* Raw MRC keeps its historical in-place floor and arithmetic. Auto-v1
     * diversity preserves the original primary variance separately, so a
     * primary selection is bit-identical to the mono decoder. */
    double *nv_mrc = nv;
    if (nch == 2 && auto_v1) {
        nv_mrc = (double *)malloc((size_t)2 * (size_t)n_used * sizeof(double));
        if (nv_mrc != NULL) {
            memcpy(nv_mrc, nv, (size_t)2 * (size_t)n_used * sizeof(double));
            cyrinx_apply_mrc_noise_floor(nv_mrc, n_used);
        } else if (diagnostics != NULL) {
            diagnostics->selection_reason = CYRINX_BULK_AUTO_REASON_RESOURCE_FAILURE;
        }
    } else if (nch == 2) {
        cyrinx_apply_mrc_noise_floor(nv, n_used);
    }

    int use_mrc = nch == 2 && !auto_v1;
    if (auto_v1 && nch == 2 && nv_mrc != NULL) {
        use_mrc = cyrinx_select_auto_v1_receiver(cfg, &g, fplan, sig, sig1, base, Hr, Hi, nv, nv_mrc, pil_re,
                                                 pil_im, diagnostics) == CYRINX_BULK_DIVERSITY_MRC;
    } else if (auto_v1 && nch == 1 && diagnostics != NULL) {
        diagnostics->selection_reason = CYRINX_BULK_AUTO_REASON_SECOND_UNAVAILABLE;
    }
    const double *decode_nv = use_mrc ? nv_mrc : nv;

    /* Per-bin effective SNR belongs to the selected receiver. */
    double *snr = (double *)malloc((size_t)n_used * sizeof(double));
    double *pilot_snr = (double *)malloc((size_t)g.n_pilots * sizeof(double));
    double *pilot_weights = (double *)malloc((size_t)g.n_pilots * sizeof(double));
    if (snr == NULL || pilot_snr == NULL || pilot_weights == NULL)
        goto pilot_weight_resource_failure;
    cyrinx_snr_for_receiver(Hr, Hi, decode_nv, n_used, use_mrc, snr);
    for (int m = 0; m < g.n_pilots; ++m)
        pilot_snr[m] = snr[m * cfg->pilot_every];
    if (cyrinx_pilot_phase_weights(pilot_snr, g.n_pilots, pilot_weights) != 0)
        goto pilot_weight_resource_failure;

    /* ---- per-symbol demap ---- */
    double *llr_stream = (double *)malloc((size_t)cap * sizeof(double));
    double *Zr = (double *)malloc((size_t)n_used * sizeof(double));
    double *Zi = (double *)malloc((size_t)n_used * sizeof(double));
    double *den = (double *)malloc((size_t)n_used * sizeof(double));
    double *ewr = (double *)malloc((size_t)g.n_pilots * sizeof(double));
    double *ewi = (double *)malloc((size_t)g.n_pilots * sizeof(double));
    double *pilot_evm2 = (double *)malloc((size_t)g.n_pilots * sizeof(double));
    double *smoothed_pilot_evm2 = (double *)malloc((size_t)g.n_pilots * sizeof(double));
    if (llr_stream == NULL || Zr == NULL || Zi == NULL || den == NULL || ewr == NULL || ewi == NULL ||
        pilot_evm2 == NULL || smoothed_pilot_evm2 == NULL)
        goto demap_resource_failure;
    double evm_acc = 0.0;
    int lpos = 0;
    for (int s = 0; s < cfg->n_sym; ++s) {
        /* equalize: one mic Z = Y/H (G = 1); two mics combine per subcarrier
         * with inverse-noise-variance MRC:
         *   Z = sum_m(conj(H_m) Y_m / nv_m) / sum_m(|H_m|^2 / nv_m)
         * matching modem.py demodulate_frame */
        for (int k = 0; k < n_used; ++k) {
            Zr[k] = 0.0;
            Zi[k] = 0.0;
            den[k] = 0.0;
        }
        int decode_channels = use_mrc ? 2 : 1;
        for (int ch = 0; ch < decode_channels; ++ch) {
            const double *csig = ch ? sig1 : sig;
            int pos = base + (2 + s) * sym + cp;
            cyrinx_rfft(fplan, csig + pos, Yr, Yi);
            const double *chr = Hr + ch * n_used, *chi = Hi + ch * n_used;
            const double *cnv = decode_nv + ch * n_used;
            for (int k = 0; k < n_used; ++k) {
                int b = g.bin_lo + k;
                double yr = Yr[b], yi = Yi[b], hr = chr[k], hi = chi[k];
                if (!use_mrc) {
                    /* Keep the original mono arithmetic exactly. */
                    Zr[k] += yr * hr + yi * hi; /* conj(H) * Y, real */
                    Zi[k] += yi * hr - yr * hi; /* conj(H) * Y, imag */
                    den[k] += hr * hr + hi * hi;
                } else {
                    double inv_nv = 1.0 / cnv[k];
                    Zr[k] += (yr * hr + yi * hi) * inv_nv;
                    Zi[k] += (yi * hr - yr * hi) * inv_nv;
                    den[k] += (hr * hr + hi * hi) * inv_nv;
                }
            }
        }
        for (int k = 0; k < n_used; ++k) {
            double d = !use_mrc ? (den[k] < 1e-300 ? 1e-300 : den[k]) : den[k] + 1e-12;
            Zr[k] /= d;
            Zi[k] /= d;
        }
        /* Pilot phase tracking: weak/null pilots cannot have the same leverage
         * as strong pilots. Fit unit phasors with sync-only SNR weights, so no
         * data-symbol magnitude or decoded value leaks into reliability. */
        int np = g.n_pilots;
        for (int m = 0; m < np; ++m) {
            int k = m * cfg->pilot_every; /* pilot used-position */
            /* e = Z[pil] * conj(pilot) */
            ewr[m] = Zr[k] * pil_re[m] + Zi[k] * pil_im[m];
            ewi[m] = Zi[k] * pil_re[m] - Zr[k] * pil_im[m];
        }
        double slope_tot = 0.0, ph0 = 0.0;
        cyrinx_fit_pilot_phase(ewr, ewi, pilot_weights, np, cfg->pilot_every, &slope_tot, &ph0);
        for (int k = 0; k < n_used; ++k) {
            double ang = -(ph0 + slope_tot * (double)k);
            double cr = cos(ang), ci = sin(ang);
            double nr = Zr[k] * cr - Zi[k] * ci;
            double ni = Zr[k] * ci + Zi[k] * cr;
            Zr[k] = nr;
            Zi[k] = ni;
        }
        /* Pilot EVM^2 after correction. Its global mean preserves the existing
         * per-symbol burst/dropout weighting. The local estimate adds only
         * known-pilot frequency structure and is therefore payload-independent. */
        double evm2 = 0.0;
        for (int m = 0; m < np; ++m) {
            int k = m * cfg->pilot_every;
            double pr = Zr[k] * pil_re[m] + Zi[k] * pil_im[m];
            double pi = Zi[k] * pil_re[m] - Zr[k] * pil_im[m];
            double er = pr - 1.0;
            double residual = er * er + pi * pi;
            /* A nonfinite observation must not produce spuriously confident
             * LLRs. Saturation is preferable to decoding with NaN/zero noise. */
            if (!isfinite(residual) || residual < 0.0 ||
                residual > CYRINX_BULK_RECEIVER_NONFINITE_RESIDUAL_CEILING_V1)
                residual = CYRINX_BULK_RECEIVER_NONFINITE_RESIDUAL_CEILING_V1;
            pilot_evm2[m] = residual;
            evm2 += residual;
        }
        evm2 /= np;
        evm_acc += sqrt(evm2);
        cyrinx_smooth_pilot_evm2(pilot_evm2, np, smoothed_pilot_evm2);
        /* demap data bins (positions k with k % pilot_every != 0) */
        for (int k = 0; k < n_used; ++k) {
            if (k % cfg->pilot_every == 0)
                continue;
            double s_ =
                snr[k] > CYRINX_BULK_RECEIVER_SNR_FLOOR_V1 ? snr[k] : CYRINX_BULK_RECEIVER_SNR_FLOOR_V1;
            double local_evm2 = cyrinx_interpolate_pilot_evm2(smoothed_pilot_evm2, np, cfg->pilot_every, k);
            double n0 =
                1.0 / s_ + CYRINX_GLOBAL_PILOT_EVM_WEIGHT * evm2 + CYRINX_LOCAL_PILOT_EVM_WEIGHT * local_evm2;
            cyrinx_qam_llr(Zr[k], Zi[k], cfg->bits_per_bin, n0, llr_stream + lpos);
            lpos += cfg->bits_per_bin;
        }
    }
    if (evm_rms)
        *evm_rms = evm_acc / cfg->n_sym;

    /* ---- deinterleave: llr[i] = llr_stream[perm[i]] ---- */
    int64_t *perm = (int64_t *)malloc((size_t)cap * sizeof(int64_t));
    cyrinx_detrng_permutation(0x1EAF, perm, (size_t)cap);
    double *llr = (double *)malloc((size_t)cap * sizeof(double));
    for (int i = 0; i < cap; ++i)
        llr[i] = llr_stream[perm[i]];

    /* ---- depuncture into the full rate-1/2 stream ---- */
    int n_coded_full = (g.info_bits + 6) * 2;
    const uint8_t *pat = NULL;
    size_t patlen = cyrinx_puncture_pattern(cfg->rate, &pat);
    double *llr_full = (double *)calloc((size_t)n_coded_full, sizeof(double));
    int j = 0;
    for (int i = 0; i < n_coded_full; ++i) {
        if (pat[i % patlen])
            llr_full[i] = llr[j++];
    }
    /* split even/odd -> the two coded-bit LLR streams */
    int nstep = n_coded_full / 2;
    double *l0 = (double *)malloc((size_t)nstep * sizeof(double));
    double *l1 = (double *)malloc((size_t)nstep * sizeof(double));
    for (int i = 0; i < nstep; ++i) {
        l0[i] = llr_full[2 * i];
        l1[i] = llr_full[2 * i + 1];
    }
    uint8_t *info = (uint8_t *)malloc((size_t)g.info_bits);
    cyrinx_viterbi(l0, l1, nstep, g.info_bits, info);

    /* ---- repack info bits -> stream bytes; CRC per block ---- */
    int ok = 0;
    for (int blk = 0; blk < g.n_blocks; ++blk) {
        uint8_t framed[CYRINX_BULK_CRC_BLOCK + 4];
        for (int by = 0; by < CYRINX_BULK_CRC_BLOCK + 4; ++by) {
            int bitbase = (blk * (CYRINX_BULK_CRC_BLOCK + 4) + by) * 8;
            uint8_t v = 0;
            for (int k = 0; k < 8; ++k)
                v = (uint8_t)((v << 1) | (info[bitbase + k] & 1u));
            framed[by] = v;
        }
        uint32_t crc = cyrinx_bulk_crc32(framed, CYRINX_BULK_CRC_BLOCK);
        uint32_t got = ((uint32_t)framed[CYRINX_BULK_CRC_BLOCK] << 24) |
                       ((uint32_t)framed[CYRINX_BULK_CRC_BLOCK + 1] << 16) |
                       ((uint32_t)framed[CYRINX_BULK_CRC_BLOCK + 2] << 8) |
                       (uint32_t)framed[CYRINX_BULK_CRC_BLOCK + 3];
        int valid = crc == got;
        if (block_valid_out != NULL)
            block_valid_out[blk] = (uint8_t)valid;
        if (valid)
            ok++;
        memcpy(out_payload + blk * CYRINX_BULK_CRC_BLOCK, framed, CYRINX_BULK_CRC_BLOCK);
    }
    if (blocks_ok)
        *blocks_ok = ok;
    if (blocks_total)
        *blocks_total = g.n_blocks;

    cyrinx_rfft_destroy(fplan);
    free(sig);
    free(sig1);
    free(chirp);
    free(spec_re);
    free(spec_im);
    free(tbuf);
    free(sync0_re);
    free(sync0_im);
    free(ref);
    free(Yr);
    free(Yi);
    free(Hr);
    if (nv_mrc != nv)
        free(nv_mrc);
    free(Hi);
    free(nv);
    free(dre);
    free(dim);
    free(sre);
    free(sim);
    free(nraw);
    free(snr);
    free(pil_re);
    free(pil_im);
    free(pilot_snr);
    free(pilot_weights);
    free(llr_stream);
    free(Zr);
    free(Zi);
    free(den);
    free(ewr);
    free(ewi);
    free(pilot_evm2);
    free(smoothed_pilot_evm2);
    free(perm);
    free(llr);
    free(llr_full);
    free(l0);
    free(l1);
    free(info);
    return g.payload_bytes;

demap_resource_failure:
    free(llr_stream);
    free(Zr);
    free(Zi);
    free(den);
    free(ewr);
    free(ewi);
    free(pilot_evm2);
    free(smoothed_pilot_evm2);
pilot_weight_resource_failure:
    if (auto_v1 && diagnostics != NULL) {
        cyrinx_init_diversity_diagnostics(diagnostics);
        diagnostics->selection_reason = CYRINX_BULK_AUTO_REASON_RESOURCE_FAILURE;
    }
    cyrinx_rfft_destroy(fplan);
    free(sig);
    free(sig1);
    free(chirp);
    free(spec_re);
    free(spec_im);
    free(tbuf);
    free(sync0_re);
    free(sync0_im);
    free(ref);
    free(Yr);
    free(Yi);
    free(Hr);
    if (nv_mrc != nv)
        free(nv_mrc);
    free(Hi);
    free(nv);
    free(dre);
    free(dim);
    free(sre);
    free(sim);
    free(nraw);
    free(snr);
    free(pil_re);
    free(pil_im);
    free(pilot_snr);
    free(pilot_weights);
    return -1;
}

long cyrinx_bulk_demodulate(const cyrinx_bulk_config *cfg, const float *rx, size_t rx_len,
                            uint8_t *out_payload, size_t out_cap, int *blocks_ok, int *blocks_total,
                            double *evm_rms) {
    return cyrinx_bulk_demod_impl(cfg, rx, rx_len, NULL, 0, out_payload, out_cap, blocks_ok, blocks_total,
                                  evm_rms, NULL, 0, 0, NULL);
}

long cyrinx_bulk_demodulate2(const cyrinx_bulk_config *cfg, const float *rx, size_t rx_len, const float *rx2,
                             size_t rx2_len, uint8_t *out_payload, size_t out_cap, int *blocks_ok,
                             int *blocks_total, double *evm_rms) {
    return cyrinx_bulk_demod_impl(cfg, rx, rx_len, rx2, rx2_len, out_payload, out_cap, blocks_ok,
                                  blocks_total, evm_rms, NULL, 0, 0, NULL);
}

long cyrinx_bulk_demodulate_with_block_validity(const cyrinx_bulk_config *cfg, const float *rx, size_t rx_len,
                                                uint8_t *out_payload, size_t out_cap, int *blocks_ok,
                                                int *blocks_total, double *evm_rms, uint8_t *block_valid_out,
                                                size_t block_valid_cap) {
    return cyrinx_bulk_demod_impl(cfg, rx, rx_len, NULL, 0, out_payload, out_cap, blocks_ok, blocks_total,
                                  evm_rms, block_valid_out, block_valid_cap, 0, NULL);
}

long cyrinx_bulk_demodulate2_with_block_validity(const cyrinx_bulk_config *cfg, const float *rx,
                                                 size_t rx_len, const float *rx2, size_t rx2_len,
                                                 uint8_t *out_payload, size_t out_cap, int *blocks_ok,
                                                 int *blocks_total, double *evm_rms, uint8_t *block_valid_out,
                                                 size_t block_valid_cap) {
    return cyrinx_bulk_demod_impl(cfg, rx, rx_len, rx2, rx2_len, out_payload, out_cap, blocks_ok,
                                  blocks_total, evm_rms, block_valid_out, block_valid_cap, 0, NULL);
}

long cyrinx_bulk_demodulate2_auto_v1(const cyrinx_bulk_config *cfg, const float *rx, size_t rx_len,
                                     const float *rx2, size_t rx2_len, uint8_t *out_payload, size_t out_cap,
                                     int *blocks_ok, int *blocks_total, double *evm_rms,
                                     cyrinx_bulk_diversity_diagnostics *diagnostics) {
    return cyrinx_bulk_demod_impl(cfg, rx, rx_len, rx2, rx2_len, out_payload, out_cap, blocks_ok,
                                  blocks_total, evm_rms, NULL, 0, 1, diagnostics);
}

long cyrinx_bulk_demodulate2_auto_v1_with_block_validity(const cyrinx_bulk_config *cfg, const float *rx,
                                                         size_t rx_len, const float *rx2, size_t rx2_len,
                                                         uint8_t *out_payload, size_t out_cap, int *blocks_ok,
                                                         int *blocks_total, double *evm_rms,
                                                         uint8_t *block_valid_out, size_t block_valid_cap,
                                                         cyrinx_bulk_diversity_diagnostics *diagnostics) {
    return cyrinx_bulk_demod_impl(cfg, rx, rx_len, rx2, rx2_len, out_payload, out_cap, blocks_ok,
                                  blocks_total, evm_rms, block_valid_out, block_valid_cap, 1, diagnostics);
}
