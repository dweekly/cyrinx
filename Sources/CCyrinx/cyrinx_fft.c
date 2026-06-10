/* Cyrinx FFT-plan interface — KISS FFT backend. See cyrinx/cyrinx_fft.h. */
#include "cyrinx/cyrinx_fft.h"

#include <stdlib.h>

#include "kiss_fftr.h" /* vendored; compiled with -Dkiss_fft_scalar=double */

struct cyrinx_irfft_plan {
    int nfft;
    kiss_fftr_cfg cfg;     /* inverse real-FFT config */
    kiss_fft_cpx *freq;    /* nfft/2+1 */
    kiss_fft_scalar *time; /* nfft */
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
