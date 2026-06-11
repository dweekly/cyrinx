/* Cyrinx repositioning guidance (docs/PUBLICATION.md PR 1.4b).
 *
 * Turns measured channel metrics into a small, stable set of human-actionable
 * hints, so a library consumer can coach its user toward a better acoustic link
 * instead of just failing. This is a PURE FUNCTION of a metrics struct — no
 * audio I/O, no allocation — so it is trivially unit-testable. The metrics are
 * produced by the channel sounder / receiver (per-bin SNR, delay spread, RX
 * peak, high-band roll-off, ultrasonic phase coherence).
 *
 * Thresholds are derived from the measured findings in docs/NEGATIVE_FINDINGS.md
 * and docs/ACOUSTIC_BULK_PHY.md and are documented at each rule below.
 */
#ifndef CYRINX_GUIDANCE_H
#define CYRINX_GUIDANCE_H

#ifdef __cplusplus
extern "C" {
#endif

/* Measured channel metrics (all from a single sounding/decode). */
typedef struct {
    double median_snr_db;    /* median per-bin SNR (dB) */
    double rx_peak;          /* peak |sample| of the capture, 0..~1 (clip near 1) */
    double delay_spread_ms;  /* -15 dB Schroeder delay spread (ms) */
    double cp_ms;            /* current cyclic-prefix duration (ms) */
    double hf_rolloff_db;    /* high-band minus low-band SNR (dB); negative = HF lost */
    double coherence;        /* phase-coherence proxy 0..1 (1 = coherent) */
    int ultrasonic;          /* nonzero if operating in the >18 kHz band */
} cyrinx_channel_metrics;

typedef enum {
    CYRINX_HINT_OK = 0,            /* link is healthy; no action needed */
    CYRINX_HINT_MOVE_CLOSER,      /* too far / too quiet: move the devices closer */
    CYRINX_HINT_SOFT_SURFACE,     /* reflective surface: use a soft surface / move from walls */
    CYRINX_HINT_AIM_BOTTOM_EDGE,  /* directional loss: point the phone's bottom edge at the speaker */
    CYRINX_HINT_LOWER_VOLUME,     /* clipping: lower volume or back off slightly */
    CYRINX_HINT_USE_AUDIBLE       /* ultrasonic uplink is phase-incoherent: switch to audible mode */
} cyrinx_reposition_hint;

typedef struct {
    cyrinx_reposition_hint hint;
    int severity;          /* 0 none, 1 advisory, 2 strong */
    double evidence_value; /* the metric value that triggered the hint */
} cyrinx_reposition_advice;

/* Evaluate the metrics and return the single highest-priority hint. Priority
 * (most destructive first): clipping > ultrasonic-incoherence > excess delay
 * spread > low SNR > high-frequency roll-off > OK. */
cyrinx_reposition_advice cyrinx_repositioning_hint(const cyrinx_channel_metrics *m);

/* Human-readable, English default string for a hint (consumers may localize). */
const char *cyrinx_reposition_hint_text(cyrinx_reposition_hint hint);

#ifdef __cplusplus
}
#endif

#endif /* CYRINX_GUIDANCE_H */
