/* Research prototype: estimate and remove a sample-clock offset before bulk decode.
 *
 * Two devices' audio clocks differ by a fixed fraction (tens of parts per
 * million). On the long symbols an off-contact link needs, that defeats the
 * decoder (docs/research/range-ladder-2026-11-10.md). This file measures the
 * offset from the frame's own two training symbols, resamples the capture, and
 * hands it to the unmodified decoder. Nothing here sees the payload.
 *
 * It is deliberately NOT part of the public C surface. It textually includes
 * the codec source so it can reuse the codec's own synchronization routines,
 * and it is built only into the research dylib (see scratch/garage/README.md).
 * The shipped decoder in Sources/CCyrinx is not modified. Promoting this means
 * moving it into Sources/CCyrinx with an API-inventory decision.
 */
static void cyrinx_research_fit_dispatch(double *ewr, double *ewi, const double *weights, int n,
                                         int pilot_every, double *slope_out, double *cpe_out);
#define CYRINX_PILOT_PHASE_FIT cyrinx_research_fit_dispatch
#include "../../Sources/CCyrinx/cyrinx_bulk.c"

/* ---------------- pilot timing fit ----------------
 * The shipped fit estimates each symbol's timing slope from the average phase
 * step between neighbouring pilots. With about a thousand pilots across a wide
 * band, a small error in that average is multiplied by the width of the band:
 * on the retained 2.5 ft captures it reports slides of up to a sample that are
 * not there, which rotates the band edges by one to two radians and loses the
 * frame. The fit below starts from the shipped estimate and then searches near
 * it for the slope that makes all pilots add up in phase (the maximum of
 * |sum_i w_i e_i exp(-j slope k_i)|), which uses the whole band at once. */
#define CYRINX_RESEARCH_FIT_SHIPPED 0
#define CYRINX_RESEARCH_FIT_COHERENT 1
#define CYRINX_FIT_SEARCH_LOBES 4 /* search +/- this many main-lobe half-widths */
#define CYRINX_FIT_STEPS_PER_LOBE 8
#define CYRINX_FIT_REFINEMENTS 3

static _Atomic int cyrinx_research_fit_mode = CYRINX_RESEARCH_FIT_SHIPPED;

/* Select the pilot timing fit used by every decode through this library:
 * 0 = the shipped fit (default; the library then decodes exactly as the
 * codec), 1 = the coherent fit. Returns the previous mode, or -1 for an
 * unknown mode. Process-wide; meant for offline replay, not concurrent use. */
int cyrinx_research_set_pilot_fit(int mode) {
    if (mode != CYRINX_RESEARCH_FIT_SHIPPED && mode != CYRINX_RESEARCH_FIT_COHERENT)
        return -1;
    return atomic_exchange(&cyrinx_research_fit_mode, mode);
}

static double cyrinx_research_fit_score(const double *ewr, const double *ewi, const double *weights, int n,
                                        int pilot_every, double slope, double *sum_re, double *sum_im) {
    double sr = 0.0, si = 0.0;
    for (int i = 0; i < n; ++i) {
        double weight = isfinite(weights[i]) && weights[i] > 0.0 ? weights[i] : 0.0;
        double angle = -slope * (double)(i * pilot_every);
        double c = cos(angle), s = sin(angle);
        sr += weight * (ewr[i] * c - ewi[i] * s);
        si += weight * (ewr[i] * s + ewi[i] * c);
    }
    if (sum_re != NULL)
        *sum_re = sr;
    if (sum_im != NULL)
        *sum_im = si;
    return sr * sr + si * si;
}

static void cyrinx_research_fit_coherent(double *ewr, double *ewi, const double *weights, int n,
                                         int pilot_every, double *slope_out, double *cpe_out) {
    /* The shipped fit projects the observations onto the unit circle, rotates
     * them by its slope in place, and gives the search its starting point. */
    double start = 0.0, unused = 0.0;
    cyrinx_fit_pilot_phase(ewr, ewi, weights, n, pilot_every, &start, &unused);
    /* ewr/ewi now hold unit phasors with `start` already removed; search the
     * remaining slope around zero. */
    double lobe = 2.0 * CYRINX_PI / (double)((n > 1 ? n - 1 : 1) * pilot_every);
    double step = lobe / (double)CYRINX_FIT_STEPS_PER_LOBE;
    double best = 0.0, best_score = -1.0;
    int reach = CYRINX_FIT_SEARCH_LOBES * CYRINX_FIT_STEPS_PER_LOBE;
    for (int pass = 0; pass <= CYRINX_FIT_REFINEMENTS; ++pass) {
        double centre = best;
        for (int i = -reach; i <= reach; ++i) {
            double slope = centre + step * (double)i;
            double score = cyrinx_research_fit_score(ewr, ewi, weights, n, pilot_every, slope, NULL, NULL);
            if (score > best_score) {
                best_score = score;
                best = slope;
            }
        }
        step /= (double)CYRINX_FIT_STEPS_PER_LOBE;
        reach = CYRINX_FIT_STEPS_PER_LOBE;
    }
    double sr = 0.0, si = 0.0;
    cyrinx_research_fit_score(ewr, ewi, weights, n, pilot_every, best, &sr, &si);
    for (int i = 0; i < n; ++i) { /* leave the observations de-sloped, as the shipped fit does */
        double angle = -best * (double)(i * pilot_every);
        double c = cos(angle), s = sin(angle);
        double nr = ewr[i] * c - ewi[i] * s;
        ewi[i] = ewr[i] * s + ewi[i] * c;
        ewr[i] = nr;
    }
    *slope_out = start + best;
    *cpe_out = atan2(si, sr);
}

static void cyrinx_research_fit_dispatch(double *ewr, double *ewi, const double *weights, int n,
                                         int pilot_every, double *slope_out, double *cpe_out) {
    if (atomic_load(&cyrinx_research_fit_mode) == CYRINX_RESEARCH_FIT_COHERENT)
        cyrinx_research_fit_coherent(ewr, ewi, weights, n, pilot_every, slope_out, cpe_out);
    else
        cyrinx_fit_pilot_phase(ewr, ewi, weights, n, pilot_every, slope_out, cpe_out);
}

#define CYRINX_CLOCK_TAPS 128    /* resampler filter length, samples */
#define CYRINX_CLOCK_PHASES 1024 /* fractional-delay table resolution */
#define CYRINX_CLOCK_KAISER_BETA 5.0
#define CYRINX_CLOCK_COARSE_STEP 0.1 /* samples of slide per training symbol */
#define CYRINX_CLOCK_MIN_APPLY_PPM 0.05

static double cyrinx_clock_bessel_i0(double x) {
    double sum = 1.0, term = 1.0, half = x * 0.5;
    for (int k = 1; k < 64; ++k) {
        term *= (half / (double)k) * (half / (double)k);
        sum += term;
        if (term < sum * 1e-17)
            break;
    }
    return sum;
}

/* out[i] = in evaluated at time i * (1 + ppm * 1e-6), by Kaiser-windowed sinc
 * interpolation. Samples outside the input read as zero. Returns the number of
 * samples written (in_len), or -1 on invalid input or allocation failure. */
long cyrinx_research_resample(const float *in, size_t in_len, double ppm, float *out, size_t out_cap) {
    if (in == NULL || out == NULL || out_cap < in_len || in_len > (size_t)INT_MAX || !isfinite(ppm) ||
        fabs(ppm) > 1e5)
        return -1;
    const int taps = CYRINX_CLOCK_TAPS, half = taps / 2, phases = CYRINX_CLOCK_PHASES;
    double *table = (double *)malloc((size_t)(phases + 1) * (size_t)taps * sizeof(double));
    if (table == NULL)
        return -1;
    double i0_beta = cyrinx_clock_bessel_i0(CYRINX_CLOCK_KAISER_BETA);
    for (int p = 0; p <= phases; ++p) {
        double frac = (double)p / (double)phases;
        double *row = table + (size_t)p * (size_t)taps;
        double sum = 0.0;
        for (int j = 0; j < taps; ++j) {
            double x = frac - (double)(j - half + 1); /* distance from tap j to the read point */
            double sinc = fabs(x) < 1e-12 ? 1.0 : sin(CYRINX_PI * x) / (CYRINX_PI * x);
            double r = x / (double)half;
            double w = fabs(r) >= 1.0
                           ? 0.0
                           : cyrinx_clock_bessel_i0(CYRINX_CLOCK_KAISER_BETA * sqrt(1.0 - r * r)) / i0_beta;
            row[j] = sinc * w;
            sum += row[j];
        }
        for (int j = 0; j < taps; ++j)
            row[j] /= sum; /* unity gain at DC for every fractional delay */
    }
    double scale = 1.0 + ppm * 1e-6;
    long n = (long)in_len;
    for (long i = 0; i < n; ++i) {
        double t = (double)i * scale;
        double fl = floor(t);
        long base = (long)fl;
        double pos = (t - fl) * (double)phases;
        int p = (int)pos;
        if (p >= phases)
            p = phases - 1;
        double a = pos - (double)p;
        const double *r0 = table + (size_t)p * (size_t)taps, *r1 = r0 + taps;
        double acc0 = 0.0, acc1 = 0.0;
        long first = base - half + 1;
        for (int j = 0; j < taps; ++j) {
            long idx = first + j;
            if (idx < 0 || idx >= n)
                continue;
            double v = (double)in[idx];
            acc0 += v * r0[j];
            acc1 += v * r1[j];
        }
        out[i] = (float)((1.0 - a) * acc0 + a * acc1);
    }
    free(table);
    return n;
}

/* The decoder's own synchronization, repeated here so the estimate looks at the
 * same samples the decoder will. Returns 0 and the first training symbol's
 * start, or -1 when the frame does not fit in the capture. */
static int cyrinx_clock_locate(const cyrinx_bulk_config *cfg, const cyrinx_bulk_geometry *g,
                               const double *sig, size_t rx_len, const double *sync0_re,
                               const double *sync0_im, int *base_out) {
    const int nfft = cfg->nfft, cp = cfg->cp, sym = nfft + cp, half = nfft / 2 + 1;
    int result = -1;
    double *chirp = (double *)malloc((size_t)CYRINX_BULK_CHIRP_LEN * sizeof(double));
    double *spec_re = (double *)malloc((size_t)half * sizeof(double));
    double *spec_im = (double *)malloc((size_t)half * sizeof(double));
    double *tbuf = (double *)malloc((size_t)nfft * sizeof(double));
    double *ref = (double *)malloc((size_t)sym * sizeof(double));
    cyrinx_irfft_plan *iplan = cyrinx_irfft_create(nfft);
    if (chirp == NULL || spec_re == NULL || spec_im == NULL || tbuf == NULL || ref == NULL || iplan == NULL)
        goto done;
    double cf0 = cfg->chirp_f0 > 0 ? cfg->chirp_f0 : CYRINX_BULK_CHIRP_F0;
    double cf1 = cfg->chirp_f1 > 0 ? cfg->chirp_f1 : CYRINX_BULK_CHIRP_F1;
    cyrinx_make_chirp(chirp, CYRINX_BULK_CHIRP_LEN, (double)cfg->sr, cf0, cf1);
    int start = cyrinx_matched_filter(sig, (int)rx_len, chirp, CYRINX_BULK_CHIRP_LEN, 0);
    int64_t coarse_base = (int64_t)start + CYRINX_BULK_CHIRP_LEN + CYRINX_BULK_GUARD;
    cyrinx_ofdm_mod_symbol(iplan, nfft, cp, g->bin_lo, g->n_used, sync0_re, sync0_im, spec_re, spec_im, tbuf,
                           ref);
    int fine_window = 400;
    int64_t lo64 = coarse_base - fine_window;
    if (lo64 < 0)
        lo64 = 0;
    if (coarse_base > INT_MAX || lo64 > INT_MAX || lo64 + sym > (int64_t)rx_len)
        goto done;
    int lo = (int)lo64;
    int64_t desired_stop = coarse_base + fine_window + sym;
    int stop = desired_stop < (int64_t)rx_len ? (int)desired_stop : (int)rx_len;
    int base = lo + cyrinx_matched_filter(sig + lo, stop - lo, ref, sym, 0) - 24;
    if (base < 0 || (int64_t)base + ((int64_t)2 + cfg->n_sym) * sym > (int64_t)rx_len)
        goto done;
    *base_out = base;
    result = 0;
done:
    if (iplan != NULL)
        cyrinx_irfft_destroy(iplan);
    free(chirp);
    free(spec_re);
    free(spec_im);
    free(tbuf);
    free(ref);
    return result;
}

/* |sum_k cross[k] * exp(+j 2 pi bin_k t / nfft)|: how well a slide of t samples
 * explains the phase difference between the two training symbols. */
static double cyrinx_clock_slide_score(const double *cr, const double *ci, int n_used, int bin_lo, int nfft,
                                       double t) {
    double step = 2.0 * CYRINX_PI * t / (double)nfft;
    double sr = 0.0, si = 0.0;
    for (int k = 0; k < n_used; ++k) {
        double ang = step * (double)(bin_lo + k);
        double c = cos(ang), s = sin(ang);
        sr += cr[k] * c - ci[k] * s;
        si += cr[k] * s + ci[k] * c;
    }
    return sqrt(sr * sr + si * si);
}

/* Estimate the clock offset of one captured frame from its two training
 * symbols. A clock offset slides the second symbol against the first by
 * ppm * 1e-6 * (nfft + cp) samples, which is a phase ramp across frequency in
 * the ratio of the two channel estimates. The slide that best explains that
 * ramp is found by search within +/- max_abs_ppm.
 *
 * *ppm_out uses the resampler's convention: resampling the capture by *ppm_out
 * removes the offset. *coherence_out (optional) is the fraction of the
 * training energy the best slide explains, 0 to 1; it is low when the two
 * symbols disagree for reasons a slide cannot explain. Returns 0, or -1 when
 * the config is invalid or the frame does not fit. */
int cyrinx_research_estimate_clock_offset(const cyrinx_bulk_config *cfg, const float *rx, size_t rx_len,
                                          double max_abs_ppm, double *ppm_out, double *coherence_out) {
    cyrinx_bulk_geometry g;
    if (ppm_out == NULL || rx == NULL || !(max_abs_ppm > 0.0) || max_abs_ppm > 1000.0 ||
        cyrinx_bulk_compute_geometry(cfg, &g) != 0 || rx_len < (size_t)g.frame_samples || rx_len > INT_MAX)
        return -1;
    const int nfft = cfg->nfft, cp = cfg->cp, sym = nfft + cp, half = nfft / 2 + 1, n_used = g.n_used;
    int result = -1;
    double *sig = (double *)malloc(rx_len * sizeof(double));
    double *x_re = (double *)malloc((size_t)2 * (size_t)n_used * sizeof(double));
    double *x_im = (double *)malloc((size_t)2 * (size_t)n_used * sizeof(double));
    double *h_re = (double *)malloc((size_t)n_used * sizeof(double));
    double *h_im = (double *)malloc((size_t)n_used * sizeof(double));
    double *cr = (double *)malloc((size_t)n_used * sizeof(double));
    double *ci = (double *)malloc((size_t)n_used * sizeof(double));
    double *Yr = (double *)malloc((size_t)half * sizeof(double));
    double *Yi = (double *)malloc((size_t)half * sizeof(double));
    cyrinx_rfft_plan *fplan = cyrinx_rfft_create(nfft);
    if (sig == NULL || x_re == NULL || x_im == NULL || h_re == NULL || h_im == NULL || cr == NULL ||
        ci == NULL || Yr == NULL || Yi == NULL || fplan == NULL)
        goto done;
    for (size_t i = 0; i < rx_len; ++i)
        sig[i] = (double)rx[i];
    cyrinx_phase_symbols(0x5EED, n_used, x_re, x_im);
    cyrinx_phase_symbols(0x5EED + 1, n_used, x_re + n_used, x_im + n_used);
    int base = 0;
    if (cyrinx_clock_locate(cfg, &g, sig, rx_len, x_re, x_im, &base) != 0)
        goto done;
    double total = 0.0;
    for (int w = 0; w < 2; ++w) {
        cyrinx_rfft(fplan, sig + base + w * sym + cp, Yr, Yi);
        const double *xr = x_re + w * n_used, *xi = x_im + w * n_used;
        for (int k = 0; k < n_used; ++k) {
            double yr = Yr[g.bin_lo + k], yi = Yi[g.bin_lo + k];
            double hr = yr * xr[k] + yi * xi[k], hi = yi * xr[k] - yr * xi[k];
            if (w == 0) {
                h_re[k] = hr;
                h_im[k] = hi;
            } else { /* cross = H1 * conj(H0) */
                cr[k] = hr * h_re[k] + hi * h_im[k];
                ci[k] = hi * h_re[k] - hr * h_im[k];
                total += sqrt(cr[k] * cr[k] + ci[k] * ci[k]);
            }
        }
    }
    if (!(total > 0.0) || !isfinite(total))
        goto done;
    double limit = max_abs_ppm * 1e-6 * (double)sym;
    double best_t = 0.0, best = -1.0;
    int steps = (int)ceil(limit / CYRINX_CLOCK_COARSE_STEP);
    for (int i = -steps; i <= steps; ++i) {
        double t = (double)i * CYRINX_CLOCK_COARSE_STEP;
        double score = cyrinx_clock_slide_score(cr, ci, n_used, g.bin_lo, nfft, t);
        if (score > best) {
            best = score;
            best_t = t;
        }
    }
    double span = CYRINX_CLOCK_COARSE_STEP;
    for (int pass = 0; pass < 3; ++pass) { /* three tenfold refinements: 1e-4 sample */
        double centre = best_t;
        for (int i = -10; i <= 10; ++i) {
            double t = centre + span * (double)i / 10.0;
            double score = cyrinx_clock_slide_score(cr, ci, n_used, g.bin_lo, nfft, t);
            if (score > best) {
                best = score;
                best_t = t;
            }
        }
        span /= 10.0;
    }
    *ppm_out = best_t / (double)sym * 1e6;
    if (coherence_out != NULL)
        *coherence_out = best / total;
    result = 0;
done:
    if (fplan != NULL)
        cyrinx_rfft_destroy(fplan);
    free(sig);
    free(x_re);
    free(x_im);
    free(h_re);
    free(h_im);
    free(cr);
    free(ci);
    free(Yr);
    free(Yi);
    return result;
}

/* Decode one single-microphone frame after estimating and removing its clock
 * offset. `passes` estimates are taken, each on the capture as corrected so
 * far, and summed (the second sees less within-symbol smearing than the
 * first). The corrected capture then goes to the unmodified decoder, so every
 * other contract of cyrinx_bulk_demodulate_with_block_validity holds.
 * *ppm_out (optional) receives the total correction applied. If no estimate
 * can be made the capture is decoded as given and *ppm_out is 0. */
long cyrinx_research_demodulate_clock_corrected(const cyrinx_bulk_config *cfg, const float *rx, size_t rx_len,
                                                double max_abs_ppm, int passes, uint8_t *out_payload,
                                                size_t out_cap, int *blocks_ok, int *blocks_total,
                                                double *evm_rms, uint8_t *block_valid_out,
                                                size_t block_valid_cap, double *ppm_out) {
    if (ppm_out != NULL)
        *ppm_out = 0.0;
    if (rx == NULL || passes < 1 || passes > 4)
        return -1;
    float *work = (float *)malloc(rx_len * sizeof(float));
    if (work == NULL)
        return -1;
    const float *current = rx;
    double total_ppm = 0.0;
    for (int pass = 0; pass < passes; ++pass) {
        double step_ppm = 0.0;
        if (cyrinx_research_estimate_clock_offset(cfg, current, rx_len, max_abs_ppm, &step_ppm, NULL) != 0)
            break;
        total_ppm += step_ppm;
        if (fabs(total_ppm) < CYRINX_CLOCK_MIN_APPLY_PPM) {
            total_ppm = 0.0;
            current = rx;
            break;
        }
        /* always resample from the original, so filter error does not compound */
        if (cyrinx_research_resample(rx, rx_len, total_ppm, work, rx_len) < 0) {
            free(work);
            return -1;
        }
        current = work;
        if (fabs(step_ppm) < CYRINX_CLOCK_MIN_APPLY_PPM)
            break;
    }
    if (ppm_out != NULL)
        *ppm_out = current == rx ? 0.0 : total_ppm;
    long n =
        cyrinx_bulk_demodulate_with_block_validity(cfg, current, rx_len, out_payload, out_cap, blocks_ok,
                                                   blocks_total, evm_rms, block_valid_out, block_valid_cap);
    free(work);
    return n;
}
