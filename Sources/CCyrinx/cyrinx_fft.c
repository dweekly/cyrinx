/* Cyrinx FFT-plan interface. See cyrinx/cyrinx_fft.h.
 *
 * Two backends behind one interface:
 *   - default: vendored KISS FFT (portable, the correctness reference).
 *   - CYRINX_FFT_ACCELERATE on Apple: vDSP/Accelerate double-precision DFT.
 * Both are validated against the same golden vectors (Tests/Fixtures/golden).
 * numpy semantics: rfft unnormalized; irfft 1/nfft normalized.
 */
#include "cyrinx/cyrinx_fft.h"

#include <stdlib.h>

#if defined(__APPLE__) && defined(CYRINX_FFT_ACCELERATE)

/* ---------------- Accelerate / vDSP backend ---------------- */
/* Uses the full complex DFT (zop) rather than the packed real FFT: simpler and
 * unambiguous (no zrip pack/scale pitfalls), still Accelerate-vectorized. The
 * real input is given a zero imaginary part; for irfft the Hermitian half is
 * mirrored to a full spectrum. */
#include <Accelerate/Accelerate.h>

struct cyrinx_irfft_plan {
    int nfft;
    vDSP_DFT_SetupD setup; /* inverse */
    double *ir, *ii, *or_, *oi;
};

cyrinx_irfft_plan *cyrinx_irfft_create(int nfft) {
    if (nfft <= 0 || (nfft & 1)) return NULL;
    cyrinx_irfft_plan *p = (cyrinx_irfft_plan *)calloc(1, sizeof(*p));
    if (!p) return NULL;
    p->nfft = nfft;
    p->setup = vDSP_DFT_zop_CreateSetupD(NULL, (vDSP_Length)nfft, vDSP_DFT_INVERSE);
    p->ir = (double *)malloc((size_t)nfft * sizeof(double));
    p->ii = (double *)malloc((size_t)nfft * sizeof(double));
    p->or_ = (double *)malloc((size_t)nfft * sizeof(double));
    p->oi = (double *)malloc((size_t)nfft * sizeof(double));
    if (!p->setup || !p->ir || !p->ii || !p->or_ || !p->oi) {
        cyrinx_irfft_destroy(p);
        return NULL;
    }
    return p;
}

void cyrinx_irfft(cyrinx_irfft_plan *plan, const double *freq_re,
                  const double *freq_im, double *time_out) {
    const int n = plan->nfft, half = n / 2;
    for (int k = 0; k <= half; ++k) {
        plan->ir[k] = freq_re[k];
        plan->ii[k] = freq_im[k];
    }
    for (int k = 1; k < half; ++k) { /* Hermitian mirror: X[n-k] = conj(X[k]) */
        plan->ir[n - k] = freq_re[k];
        plan->ii[n - k] = -freq_im[k];
    }
    vDSP_DFT_ExecuteD(plan->setup, plan->ir, plan->ii, plan->or_, plan->oi);
    const double inv = 1.0 / (double)n; /* numpy.irfft 1/N */
    for (int i = 0; i < n; ++i) time_out[i] = plan->or_[i] * inv;
}

void cyrinx_irfft_destroy(cyrinx_irfft_plan *plan) {
    if (!plan) return;
    if (plan->setup) vDSP_DFT_DestroySetupD(plan->setup);
    free(plan->ir); free(plan->ii); free(plan->or_); free(plan->oi);
    free(plan);
}

struct cyrinx_rfft_plan {
    int nfft;
    vDSP_DFT_SetupD setup; /* forward */
    double *ir, *ii, *or_, *oi;
};

cyrinx_rfft_plan *cyrinx_rfft_create(int nfft) {
    if (nfft <= 0 || (nfft & 1)) return NULL;
    cyrinx_rfft_plan *p = (cyrinx_rfft_plan *)calloc(1, sizeof(*p));
    if (!p) return NULL;
    p->nfft = nfft;
    p->setup = vDSP_DFT_zop_CreateSetupD(NULL, (vDSP_Length)nfft, vDSP_DFT_FORWARD);
    p->ir = (double *)malloc((size_t)nfft * sizeof(double));
    p->ii = (double *)malloc((size_t)nfft * sizeof(double));
    p->or_ = (double *)malloc((size_t)nfft * sizeof(double));
    p->oi = (double *)malloc((size_t)nfft * sizeof(double));
    if (!p->setup || !p->ir || !p->ii || !p->or_ || !p->oi) {
        cyrinx_rfft_destroy(p);
        return NULL;
    }
    return p;
}

void cyrinx_rfft(cyrinx_rfft_plan *plan, const double *time_in, double *freq_re,
                 double *freq_im) {
    const int n = plan->nfft, half = n / 2;
    for (int i = 0; i < n; ++i) {
        plan->ir[i] = time_in[i];
        plan->ii[i] = 0.0;
    }
    vDSP_DFT_ExecuteD(plan->setup, plan->ir, plan->ii, plan->or_, plan->oi);
    for (int k = 0; k <= half; ++k) { /* numpy.rfft half-spectrum, unnormalized */
        freq_re[k] = plan->or_[k];
        freq_im[k] = plan->oi[k];
    }
}

void cyrinx_rfft_destroy(cyrinx_rfft_plan *plan) {
    if (!plan) return;
    if (plan->setup) vDSP_DFT_DestroySetupD(plan->setup);
    free(plan->ir); free(plan->ii); free(plan->or_); free(plan->oi);
    free(plan);
}

#else

/* ---------------- KISS FFT backend (portable default) ---------------- */
#include "kiss_fftr.h" /* vendored; compiled with -Dkiss_fft_scalar=double */

struct cyrinx_irfft_plan {
    int nfft;
    kiss_fftr_cfg cfg;
    kiss_fft_cpx *freq;
    kiss_fft_scalar *time;
};

cyrinx_irfft_plan *cyrinx_irfft_create(int nfft) {
    if (nfft <= 0 || (nfft & 1)) {
        return NULL;
    }
    cyrinx_irfft_plan *p = (cyrinx_irfft_plan *)calloc(1, sizeof(*p));
    if (!p) {
        return NULL;
    }
    p->nfft = nfft;
    p->cfg = kiss_fftr_alloc(nfft, /*inverse=*/1, NULL, NULL);
    p->freq = (kiss_fft_cpx *)malloc((size_t)(nfft / 2 + 1) * sizeof(kiss_fft_cpx));
    p->time = (kiss_fft_scalar *)malloc((size_t)nfft * sizeof(kiss_fft_scalar));
    if (!p->cfg || !p->freq || !p->time) {
        cyrinx_irfft_destroy(p);
        return NULL;
    }
    return p;
}

void cyrinx_irfft(cyrinx_irfft_plan *plan, const double *freq_re,
                  const double *freq_im, double *time_out) {
    const int half = plan->nfft / 2 + 1;
    for (int i = 0; i < half; ++i) {
        plan->freq[i].r = (kiss_fft_scalar)freq_re[i];
        plan->freq[i].i = (kiss_fft_scalar)freq_im[i];
    }
    kiss_fftri(plan->cfg, plan->freq, plan->time);
    /* numpy.irfft normalizes by 1/nfft; KISS is unnormalized. */
    const double inv = 1.0 / (double)plan->nfft;
    for (int i = 0; i < plan->nfft; ++i) {
        time_out[i] = (double)plan->time[i] * inv;
    }
}

void cyrinx_irfft_destroy(cyrinx_irfft_plan *plan) {
    if (!plan) {
        return;
    }
    if (plan->cfg) {
        kiss_fftr_free(plan->cfg);
    }
    free(plan->freq);
    free(plan->time);
    free(plan);
}

struct cyrinx_rfft_plan {
    int nfft;
    kiss_fftr_cfg cfg;
    kiss_fft_scalar *time;
    kiss_fft_cpx *freq;
};

cyrinx_rfft_plan *cyrinx_rfft_create(int nfft) {
    if (nfft <= 0 || (nfft & 1)) {
        return NULL;
    }
    cyrinx_rfft_plan *p = (cyrinx_rfft_plan *)calloc(1, sizeof(*p));
    if (!p) {
        return NULL;
    }
    p->nfft = nfft;
    p->cfg = kiss_fftr_alloc(nfft, /*inverse=*/0, NULL, NULL);
    p->time = (kiss_fft_scalar *)malloc((size_t)nfft * sizeof(kiss_fft_scalar));
    p->freq = (kiss_fft_cpx *)malloc((size_t)(nfft / 2 + 1) * sizeof(kiss_fft_cpx));
    if (!p->cfg || !p->time || !p->freq) {
        cyrinx_rfft_destroy(p);
        return NULL;
    }
    return p;
}

void cyrinx_rfft(cyrinx_rfft_plan *plan, const double *time_in, double *freq_re,
                 double *freq_im) {
    for (int i = 0; i < plan->nfft; ++i) {
        plan->time[i] = (kiss_fft_scalar)time_in[i];
    }
    kiss_fftr(plan->cfg, plan->time, plan->freq);
    const int half = plan->nfft / 2 + 1;
    for (int i = 0; i < half; ++i) {
        freq_re[i] = (double)plan->freq[i].r;
        freq_im[i] = (double)plan->freq[i].i;
    }
}

void cyrinx_rfft_destroy(cyrinx_rfft_plan *plan) {
    if (!plan) {
        return;
    }
    if (plan->cfg) {
        kiss_fftr_free(plan->cfg);
    }
    free(plan->time);
    free(plan->freq);
    free(plan);
}

#endif /* backend select */
