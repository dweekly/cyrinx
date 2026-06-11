/* Cyrinx repositioning guidance. See cyrinx/cyrinx_guidance.h. */
#include "cyrinx/cyrinx_guidance.h"

/* Thresholds (documented; sourced from the measured findings):
 *  - CLIP_PEAK: a normalized capture peak this close to full scale means the ADC
 *    is clipping, which corrupts every subcarrier — checked first.
 *  - INCOHERENT: below this phase-coherence proxy a >18 kHz uplink cannot carry
 *    coherent OFDM (NEGATIVE_FINDINGS #9: consumer micro-speakers scramble phase
 *    above ~18 kHz). Audible mode is the fix.
 *  - DELAY_MARGIN: when the -15 dB delay spread exceeds the CP by more than this
 *    factor, ISI dominates (NEGATIVE_FINDINGS #13: reverberant desk). A soft
 *    surface / moving away from hard reflectors shortens the strong-tap spread.
 *  - SNR_LOW / SNR_MARGINAL with PEAK_WEAK: low broadband SNR with a weak capture
 *    means the link is range/level limited — move closer.
 *  - HF_ROLLOFF: a large high-minus-low SNR deficit indicates the phone's
 *    ports/lobe are pointed away — aim the bottom edge at the speaker.
 */
#define CYRINX_CLIP_PEAK 0.98
#define CYRINX_INCOHERENT 0.30
#define CYRINX_DELAY_MARGIN 1.0 /* delay_spread > cp_ms * (1+margin) */
#define CYRINX_SNR_LOW 6.0
#define CYRINX_SNR_MARGINAL 10.0
#define CYRINX_PEAK_WEAK 0.05
#define CYRINX_HF_ROLLOFF_DB -10.0

cyrinx_reposition_advice cyrinx_repositioning_hint(const cyrinx_channel_metrics *m) {
    cyrinx_reposition_advice a;
    a.hint = CYRINX_HINT_OK;
    a.severity = 0;
    a.evidence_value = 0.0;

    if (m->rx_peak >= CYRINX_CLIP_PEAK) {
        a.hint = CYRINX_HINT_LOWER_VOLUME;
        a.severity = 2;
        a.evidence_value = m->rx_peak;
        return a;
    }
    if (m->ultrasonic && m->coherence < CYRINX_INCOHERENT) {
        a.hint = CYRINX_HINT_USE_AUDIBLE;
        a.severity = 2;
        a.evidence_value = m->coherence;
        return a;
    }
    if (m->cp_ms > 0.0 && m->delay_spread_ms > m->cp_ms * (1.0 + CYRINX_DELAY_MARGIN)) {
        a.hint = CYRINX_HINT_SOFT_SURFACE;
        a.severity = 2;
        a.evidence_value = m->delay_spread_ms;
        return a;
    }
    if (m->median_snr_db < CYRINX_SNR_LOW && m->rx_peak < CYRINX_PEAK_WEAK) {
        a.hint = CYRINX_HINT_MOVE_CLOSER;
        a.severity = 2;
        a.evidence_value = m->median_snr_db;
        return a;
    }
    if (m->median_snr_db < CYRINX_SNR_MARGINAL) {
        a.hint = CYRINX_HINT_MOVE_CLOSER;
        a.severity = 1;
        a.evidence_value = m->median_snr_db;
        return a;
    }
    if (m->hf_rolloff_db < CYRINX_HF_ROLLOFF_DB) {
        a.hint = CYRINX_HINT_AIM_BOTTOM_EDGE;
        a.severity = 1;
        a.evidence_value = m->hf_rolloff_db;
        return a;
    }
    return a;
}

const char *cyrinx_reposition_hint_text(cyrinx_reposition_hint hint) {
    switch (hint) {
    case CYRINX_HINT_OK:
        return "Link looks good.";
    case CYRINX_HINT_MOVE_CLOSER:
        return "Signal is weak — move the devices closer together.";
    case CYRINX_HINT_SOFT_SURFACE:
        return "Too much echo — set the phone on a soft surface or away from hard walls.";
    case CYRINX_HINT_AIM_BOTTOM_EDGE:
        return "Point the bottom edge of the phone toward the other device's speaker.";
    case CYRINX_HINT_LOWER_VOLUME:
        return "Audio is clipping — lower the volume or move the devices slightly apart.";
    case CYRINX_HINT_USE_AUDIBLE:
        return "This phone can't carry an inaudible uplink — switch to audible mode.";
    }
    return "Link looks good.";
}
