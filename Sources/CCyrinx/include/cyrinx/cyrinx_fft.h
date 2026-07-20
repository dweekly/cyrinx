/* Cyrinx FFT-plan interface (docs/PUBLICATION.md Phase 1).
 *
 * A thin abstraction over the FFT backend so the DSP code never hardcodes one.
 * The default/portable backend is KISS FFT (BSD-3, vendored under kissfft/),
 * compiled in double precision (-Dkiss_fft_scalar=double) so the C reference
 * agrees with the numpy oracle to ~1e-9 (well within the golden float tol).
 * Per-arch accelerated backends (vDSP/Accelerate, NEON, AVX) can be slotted in
 * behind this same interface later (PR 1.6), validated by the golden vectors.
 *
 * Conventions match numpy.fft so the golden vectors transfer directly:
 *   - irfft: inverse real FFT, input is the Hermitian half-spectrum of length
 *     nfft/2+1 (DC..Nyquist), output is nfft real samples, NORMALIZED by 1/nfft
 *     (KISS is unnormalized; we divide).
 */
#ifndef CYRINX_FFT_H
#define CYRINX_FFT_H

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct cyrinx_irfft_plan cyrinx_irfft_plan;

/* Create an inverse-real-FFT plan for size nfft (must be even). NULL on error. */
cyrinx_irfft_plan *cyrinx_irfft_create(int nfft);

/* Inverse real FFT, numpy.irfft semantics. freq_re/freq_im are the half-spectrum
 * (nfft/2+1 entries); time_out receives nfft real samples (1/nfft normalized). */
void cyrinx_irfft(cyrinx_irfft_plan *plan, const double *freq_re, const double *freq_im, double *time_out);

void cyrinx_irfft_destroy(cyrinx_irfft_plan *plan);

/* Forward real FFT (numpy.rfft semantics: unnormalized). */
typedef struct cyrinx_rfft_plan cyrinx_rfft_plan;

cyrinx_rfft_plan *cyrinx_rfft_create(int nfft);

/* time_in: nfft real samples; freq_re/freq_im receive the half-spectrum
 * (nfft/2+1 entries). Unnormalized, matching numpy.rfft. */
void cyrinx_rfft(cyrinx_rfft_plan *plan, const double *time_in, double *freq_re, double *freq_im);

void cyrinx_rfft_destroy(cyrinx_rfft_plan *plan);

#ifdef __cplusplus
}
#endif

#endif /* CYRINX_FFT_H */
