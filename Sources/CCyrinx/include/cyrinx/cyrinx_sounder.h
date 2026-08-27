/* Cyrinx adaptive sounder — MCS recommendation engine (docs/PUBLICATION.md 1.4).
 *
 * The decision half of the environment-adaptive scheduler: given measured
 * channel metrics (median per-bin SNR, -15 dB delay spread, and per-bin SNR for
 * loading), choose the most aggressive MCS the channel clears, size the cyclic
 * prefix, and pick per-subcarrier bit loading — never refusing to transmit,
 * falling back to a non-coherent floor when cyclic-prefix OFDM can't equalize.
 *
 * This is a PURE FUNCTION of metrics (no audio); the capture->metrics half
 * reuses the receiver's channel estimation. Reference: scratch/hw20k/sounder.py.
 */
#ifndef CYRINX_SOUNDER_H
#define CYRINX_SOUNDER_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    CYRINX_TIER_FAST = 0,   /* 16-QAM r3/4 */
    CYRINX_TIER_MEDIUM,     /* 16-QAM r1/2 */
    CYRINX_TIER_QPSK,       /* QPSK r1/2 */
    CYRINX_TIER_BPSK,       /* BPSK r1/2 — coherent OFDM floor */
    CYRINX_TIER_NONCOHERENT /* MT-FSK — delay spread beyond the CP cap */
} cyrinx_mcs_tier;

typedef struct {
    cyrinx_mcs_tier tier;
    const char *mcs;       /* "16-QAM" / "QPSK" / "BPSK" / "MT-FSK" */
    const char *rate;      /* "3/4" / "1/2" / "n/a" */
    int bits_per_bin;      /* uniform loading: 4/2/1, 0 for the non-coherent floor */
    int noncoherent;       /* nonzero when the non-coherent floor was selected */
    int cp;                /* recommended cyclic prefix (samples) */
    int nfft;              /* recommended FFT size (2048 or 4096) */
    double cp_ms;          /* CP duration (ms) */
    int advise_reposition; /* advisory: a better physical spot would help */
} cyrinx_mcs_recommendation;

/* Practical cyclic-prefix ceiling (ms): beyond this, OFDM overhead isn't worth
 * it and a non-coherent waveform (or repositioning) is indicated. */
#define CYRINX_SOUNDER_CP_CAP_MS 32.0

/* Recommend an MCS tier + CP + NFFT from the measured median per-bin SNR (dB)
 * and the -15 dB Schroeder delay spread (ms). Always returns a transmittable
 * recommendation (down to the non-coherent floor). */
cyrinx_mcs_recommendation cyrinx_sounder_recommend(double median_snr_db, double delay_spread_ms_15, int sr);

/* Per-bin bit loading from per-bin SNR (dB): 0 (<5), 2 (>=5), 4 (>=15), 6 (>=23).
 * Thresholds calibrated to the rate-1/2 conv code's FEC reach (QPSK floor ~5 dB).
 * Writes `n` values to `bits_out`. */
void cyrinx_sounder_bit_loading(const double *snr_db, int n, uint8_t *bits_out);

#ifdef __cplusplus
}
#endif

#endif /* CYRINX_SOUNDER_H */
