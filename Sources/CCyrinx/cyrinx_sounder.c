/* Cyrinx adaptive sounder — MCS recommendation. See cyrinx/cyrinx_sounder.h.
 * Reference: scratch/hw20k/sounder.py:recommend(). */
#include "cyrinx/cyrinx_sounder.h"

/* MCS ladder, fastest -> most robust. tier gated by (min median SNR dB, max
 * -15 dB delay spread ms). Mirrors sounder.py LADDER. */
typedef struct {
    cyrinx_mcs_tier tier;
    const char *mcs;
    const char *rate;
    int bits_per_bin;
    double snr_min;
    double ds_max;
} ladder_entry;

static const ladder_entry kLadder[] = {
    {CYRINX_TIER_FAST, "16-QAM", "3/4", 4, 22.0, 12.0},
    {CYRINX_TIER_MEDIUM, "16-QAM", "1/2", 4, 16.0, 18.0},
    {CYRINX_TIER_QPSK, "QPSK", "1/2", 2, 9.0, 24.0},
    {CYRINX_TIER_BPSK, "BPSK", "1/2", 1, 4.0, CYRINX_SOUNDER_CP_CAP_MS},
};

cyrinx_mcs_recommendation cyrinx_sounder_recommend(double median_snr_db, double delay_spread_ms_15, int sr) {
    cyrinx_mcs_recommendation r;
    const ladder_entry *chosen = NULL;
    for (size_t i = 0; i < sizeof(kLadder) / sizeof(kLadder[0]); ++i) {
        if (median_snr_db >= kLadder[i].snr_min && delay_spread_ms_15 <= kLadder[i].ds_max) {
            chosen = &kLadder[i];
            break;
        }
    }
    if (chosen) {
        r.tier = chosen->tier;
        r.mcs = chosen->mcs;
        r.rate = chosen->rate;
        r.bits_per_bin = chosen->bits_per_bin;
        r.noncoherent = 0;
    } else {
        r.tier = CYRINX_TIER_NONCOHERENT;
        r.mcs = "MT-FSK";
        r.rate = "n/a";
        r.bits_per_bin = 0;
        r.noncoherent = 1;
    }
    /* CP covers the -15 dB delay spread + 25% headroom, clamped to [5 ms, cap]. */
    double cp_ms = delay_spread_ms_15 * 1.25;
    if (cp_ms < 5.0)
        cp_ms = 5.0;
    if (cp_ms > CYRINX_SOUNDER_CP_CAP_MS)
        cp_ms = CYRINX_SOUNDER_CP_CAP_MS;
    r.cp_ms = cp_ms;
    r.cp = (int)(cp_ms / 1000.0 * sr);
    r.nfft = (r.cp <= 1024) ? 2048 : 4096;
    r.advise_reposition =
        (r.noncoherent && (median_snr_db < 6.0 || delay_spread_ms_15 > CYRINX_SOUNDER_CP_CAP_MS)) ? 1 : 0;
    return r;
}

void cyrinx_sounder_bit_loading(const double *snr_db, int n, uint8_t *bits_out) {
    for (int i = 0; i < n; ++i) {
        double s = snr_db[i];
        uint8_t b = (s >= 5.0) ? 2 : 0;
        if (s >= 15.0)
            b = 4;
        if (s >= 23.0)
            b = 6;
        bits_out[i] = b;
    }
}
