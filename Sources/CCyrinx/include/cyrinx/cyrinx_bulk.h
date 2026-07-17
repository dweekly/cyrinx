/* Cyrinx wideband bulk PHY — portable C core.
 *
 * This is the canonical portable C implementation used by the strict Cyrinx
 * 2.0 host decoder. It supports both the stable CP768/p8/16-QAM/r3/4 control
 * geometry and explicitly configured low-CP, sparse-pilot, 64-QAM profiles.
 * Measured profiles are route-specific: the retained Moto G 2026 clean-cell
 * result used 16-QAM rate 3/4 with CP 240. The Pixel 7a Cyrinx 2.0 campaigns
 * used CP 96 and 64-QAM rate 2/3: pilots/16 with 64 symbols for the
 * schedule-comparable flagship and pilots/64 with 96 symbols for the separate
 * zero-gap peak-goodput profile. An earlier
 * CP96/rate-5/6 Moto result survives only in an unversioned local narrative;
 * its manifest and raw bundle are missing, so it is not durable evidence.
 * Callers must characterize and stage each physical route before selecting a
 * profile. Android BulkDemod.kt is a separate legacy Kotlin implementation,
 * not a JNI binding to this library.
 *
 * Integer stages are bit-exact and FFT stages float-tolerant against the golden
 * vectors in Tests/Fixtures/golden (see scratch/hw20k/golden_vectors.py).
 */
#ifndef CYRINX_BULK_H
#define CYRINX_BULK_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ---------------- Deterministic RNG (splitmix64) ----------------
 * Bit-exact with modem.py:DetRng (and the Kotlin/Swift ports), so pilots, sync
 * symbols, the interleaver permutation, and PRBS padding reproduce identically
 * across languages. */
typedef struct {
    uint64_t s;
} cyrinx_detrng;

void cyrinx_detrng_init(cyrinx_detrng *r, uint64_t seed);
uint64_t cyrinx_detrng_u64(cyrinx_detrng *r);
uint64_t cyrinx_detrng_mod(cyrinx_detrng *r, uint64_t m);

/* One bit per output byte (0/1), each = top bit (u64 >> 63). */
void cyrinx_detrng_bits(cyrinx_detrng *r, uint8_t *out, size_t n);
/* One byte per output (u64 >> 56) & 0xFF. */
void cyrinx_detrng_bytes(cyrinx_detrng *r, uint8_t *out, size_t n);
/* Fisher-Yates permutation of 0..n-1 (matches DetRng.permutation). out is
 * int64 to mirror the golden interleave_perm artifact dtype. `n == 0` is a
 * no-op and `n == 1` writes the identity permutation. A NULL out is a no-op. */
void cyrinx_detrng_permutation(uint64_t seed, int64_t *out, size_t n);

/* PRBS bit stream: DetRng(seed).bits(n) — one bit per byte. */
void cyrinx_prbs_bits(uint8_t *out, size_t n, uint64_t seed);

/* ---------------- CRC-32 (IEEE, zlib-compatible) ----------------
 * NOT CRC-32C: the bulk PHY uses zlib.crc32 (poly 0xEDB88320 reflected,
 * init/xorout 0xFFFFFFFF). The per-block 4-byte CRC is appended big-endian. */
uint32_t cyrinx_bulk_crc32(const uint8_t *data, size_t len);

/* ---------------- Convolutional code K=7 (171,133) ----------------
 * Rate-1/2, 6 zero tail bits, MSB = newest bit. Output length = 2*(n+6).
 * `out` must hold at least 2*(n+6) bytes (1 coded bit per byte). Returns the
 * number of coded bits written. */
size_t cyrinx_conv_encode(const uint8_t *bits, size_t n, uint8_t *out);

/* Puncture pattern lookup by rate name ("1/2","2/3","3/4","5/6"). Returns the
 * pattern length and writes the pattern pointer; returns 0 on unknown rate. */
size_t cyrinx_puncture_pattern(const char *rate, const uint8_t **out_pattern);

/* Puncture `coded` (length n) by `pattern` (length p, tiled): keep coded[i]
 * where pattern[i % p] != 0. Writes kept bits to `out` (capacity >= n) and
 * returns the kept count. */
size_t cyrinx_puncture(const uint8_t *coded, size_t n, const uint8_t *pattern, size_t p, uint8_t *out);

/* ---------------- Gray-coded QAM ----------------
 * Map `nbits` interleaved bits (MSB first) to one unit-average-power complex
 * symbol. nbits: 1 (BPSK), 2 (QPSK), 4 (16-QAM), 6 (64-QAM), 8 (256-QAM).
 * Matches modem.py:qam_map. */
void cyrinx_qam_map(const uint8_t *bits, int nbits, double *out_re, double *out_im);

/* ---------------- Wideband bulk-PHY TX ----------------
 * Uniform bit-loading config (all data bins carry `bits_per_bin` bits). Mixed
 * per-bin loading arrives with the adaptive sounder (PR 1.4). */
typedef struct {
    double f_lo, f_hi;         /* finite band; selected bins exclude DC and Nyquist */
    int pilot_every;           /* comb-pilot spacing in used bins (modem default 8) */
    int bits_per_bin;          /* 1/2/4/6/8 on every data bin */
    const char *rate;          /* code rate: "1/2","2/3","3/4","5/6" */
    int n_sym;                 /* number of OFDM data symbols */
    int nfft, cp, sr;          /* positive even NFFT; 0 <= cp <= nfft; sr > 0 */
    double amp;                /* finite normalized peak in (0, 1] */
    double clip_sigma;         /* finite clipping threshold in (0, 10] sigma */
    double chirp_f0, chirp_f1; /* 0 selects defaults; resolved f0 < f1 <= sr/2 */
} cyrinx_bulk_config;

/* Derived frame geometry (modem.py:Config.__init__). */
typedef struct {
    int bin_lo, bin_hi, n_used, n_pilots, n_data_bins;
    int bits_per_sym, cap, info_bits, n_blocks, payload_bytes;
    int frame_samples;
} cyrinx_bulk_geometry;

/* ---------------- Receiver implementation contract ----------------
 * This fixed-layout query lets campaign tooling verify the receiver semantics
 * implemented by the loaded C binary instead of trusting independently
 * recorded source metadata. It is a same-process native ABI, not a serialized
 * or cross-endian wire format. The enum values and field order are stable ABI.
 * Reserved fields are zero and must be ignored by readers. */
#define CYRINX_BULK_RECEIVER_CONTRACT_ABI_VERSION 1
#define CYRINX_BULK_RECEIVER_SEMANTICS_VERSION 1

#define CYRINX_BULK_RECEIVER_ESTIMATOR_KNOWN_PILOT_LOCAL_LINEAR_BOXCAR_V1 1
#define CYRINX_BULK_RECEIVER_EDGE_REPLICATE 1
#define CYRINX_BULK_RECEIVER_FINAL_COMB_EXTEND_LAST 1

#define CYRINX_BULK_RECEIVER_LOCAL_PILOT_WINDOW_V1 11
#define CYRINX_BULK_RECEIVER_GLOBAL_WEIGHT_NUMERATOR_V1 25
#define CYRINX_BULK_RECEIVER_LOCAL_WEIGHT_NUMERATOR_V1 75
#define CYRINX_BULK_RECEIVER_WEIGHT_DENOMINATOR_V1 100
#define CYRINX_BULK_RECEIVER_SNR_FLOOR_V1 0.1
#define CYRINX_BULK_RECEIVER_NONFINITE_RESIDUAL_CEILING_V1 1e9

typedef struct {
    uint32_t struct_size;
    uint32_t abi_version;
    uint32_t semantics_version;
    uint32_t reliability_estimator;
    uint32_t local_pilot_window;
    uint32_t edge_mode;
    uint32_t global_weight_numerator;
    uint32_t local_weight_numerator;
    uint32_t weight_denominator;
    uint32_t final_comb_mode;
    uint32_t reserved[4];
    double snr_floor;
    double nonfinite_residual_ceiling;
} cyrinx_bulk_receiver_contract_v1;

/* Query the receiver contract implemented by this binary. `out_size` must be
 * exactly sizeof(cyrinx_bulk_receiver_contract_v1). On any error, including a
 * NULL output or size mismatch, returns -1 without modifying output. */
int cyrinx_bulk_get_receiver_contract_v1(cyrinx_bulk_receiver_contract_v1 *out, size_t out_size);

/* Diagnostic surface for the production known-pilot reliability helpers.
 * Inputs must contain `n_pilots >= 1` finite, nonnegative residual powers;
 * `pilot_every` must be positive. Each requested used-bin position must be
 * nonnegative. `smoothed_out` must have at least n_pilots elements. When
 * `used_position_count` is nonzero, both `used_positions` and
 * `interpolated_out` are required and interpolation_cap must be sufficient;
 * when it is zero, those pointers must be NULL and interpolation_cap zero.
 * The function returns -1 without modifying either output on invalid input or
 * resource failure. On success it returns 0, writes the endpoint-replicated
 * boxcar to smoothed_out, and writes linearly interpolated values (with the
 * final-pilot estimate extended across a partial final comb) in request order. */
int cyrinx_bulk_receiver_reliability_diagnostic_v1(const double *pilot_evm2, int n_pilots, int pilot_every,
                                                   const int *used_positions, size_t used_position_count,
                                                   double *smoothed_out, size_t smoothed_cap,
                                                   double *interpolated_out, size_t interpolation_cap);

/* Compute geometry from config. The selected band must exclude the
 * self-conjugate DC and Nyquist bins, contain at least two pilots and one data
 * bin, and carry at least one CRC block. All derived values must fit their
 * public int fields. Returns 0 on success; returns -1 for NULL pointers,
 * invalid config, or arithmetic overflow without modifying `out`. */
int cyrinx_bulk_compute_geometry(const cyrinx_bulk_config *cfg, cyrinx_bulk_geometry *out);

/* Modulate one frame. `payload` must be geometry.payload_bytes long. Writes
 * geometry.frame_samples float samples to `wave_out` (capacity wave_cap). If
 * `data_freq_out` is non-NULL it receives n_sym*n_used complex values (re/im
 * interleaved doubles) — the per-data-symbol freq-domain vectors, for golden
 * validation. Payload and waveform pointers must be non-NULL. Returns the
 * number of samples written, or -1 on error. */
long cyrinx_bulk_modulate(const cyrinx_bulk_config *cfg, const uint8_t *payload, size_t payload_len,
                          float *wave_out, size_t wave_cap, double *data_freq_out);

/* Demodulate one captured frame from one microphone. The C receiver does not
 * perform decision-directed channel tracking; the corresponding Python golden
 * comparisons use track_alpha=0. `rx` is the float capture. On success writes
 * geometry.payload_bytes to `out_payload`,
 * sets *blocks_ok / *blocks_total (CRC-valid blocks) and *evm_rms (optional,
 * may be NULL), and returns payload_bytes. `rx` and `out_payload` must be
 * non-NULL, and rx_len must hold at least geometry.frame_samples. Invalid
 * inputs are rejected before capture allocation or synchronization. Returns
 * -1 on error. */
long cyrinx_bulk_demodulate(const cyrinx_bulk_config *cfg, const float *rx, size_t rx_len,
                            uint8_t *out_payload, size_t out_cap, int *blocks_ok, int *blocks_total,
                            double *evm_rms);

/* Extended single-microphone demodulator that reports CRC validity in payload
 * order. On success, block_valid_out[i] is exactly 0 or 1 for payload bytes
 * [i*CYRINX_BULK_CRC_BLOCK, (i+1)*CYRINX_BULK_CRC_BLOCK), and the sum of the
 * first geometry.n_blocks entries equals *blocks_ok. block_valid_out may be
 * NULL only when block_valid_cap is zero. A non-NULL buffer must have capacity
 * for at least geometry.n_blocks bytes; otherwise the function returns -1
 * before decoding. The existing cyrinx_bulk_demodulate ABI is unchanged. */
long cyrinx_bulk_demodulate_with_block_validity(const cyrinx_bulk_config *cfg, const float *rx, size_t rx_len,
                                                uint8_t *out_payload, size_t out_cap, int *blocks_ok,
                                                int *blocks_total, double *evm_rms, uint8_t *block_valid_out,
                                                size_t block_valid_cap);

/* Two-microphone demodulate with per-subcarrier maximal-ratio combining
 * (the diversity path measured to rescue placements where NEITHER mic decodes
 * alone — see docs/ACOUSTIC_BULK_PHY.md robustness layer and
 * scratch/hw20k/modem.py demodulate_frame(rx2=...), the validated reference
 * this ports). `rx` and `rx2` must be sample-aligned captures of the same
 * frame (two channels of one stereo capture). Sync (chirp + fine alignment)
 * runs on `rx`; each mic gets its own channel/noise estimate from the sync
 * symbols. A shared per-bin floor caps the branch noise-variance ratio at
 * 40 dB so a spuriously tiny two-symbol estimate cannot dominate. Data
 * symbols are combined per subcarrier:
 *   Z = sum_m(conj(H_m) Y_m / nv_m) / sum_m(|H_m|^2 / nv_m)
 * and the per-bin effective SNR feeding the soft LLRs is the sum across mics,
 * so a null or high-noise branch is downweighted. After per-symbol pilot phase
 * correction, both mono and MRC demappers also estimate frequency-selective
 * reliability from known-pilot residual power: an 11-pilot centered moving
 * average is interpolated to data bins and blended 75:25 with the global pilot
 * EVM term. Payload-bearing data-symbol values, decoded bytes, and CRCs do not
 * enter this estimate. A non-NULL second capture
 * must hold at least geometry.frame_samples. `rx2 == NULL, rx2_len == 0`
 * preserves the mono fallback and is bit-identical to cyrinx_bulk_demodulate;
 * any other pointer/length mismatch is rejected. */
long cyrinx_bulk_demodulate2(const cyrinx_bulk_config *cfg, const float *rx, size_t rx_len, const float *rx2,
                             size_t rx2_len, uint8_t *out_payload, size_t out_cap, int *blocks_ok,
                             int *blocks_total, double *evm_rms);

/* Extended two-microphone demodulator with the same ordered CRC-validity mask
 * and capacity contract as cyrinx_bulk_demodulate_with_block_validity. The
 * existing cyrinx_bulk_demodulate2 ABI is unchanged; this entry point uses the
 * same current MRC algorithm as cyrinx_bulk_demodulate2. */
long cyrinx_bulk_demodulate2_with_block_validity(const cyrinx_bulk_config *cfg, const float *rx,
                                                 size_t rx_len, const float *rx2, size_t rx2_len,
                                                 uint8_t *out_payload, size_t out_cap, int *blocks_ok,
                                                 int *blocks_total, double *evm_rms, uint8_t *block_valid_out,
                                                 size_t block_valid_cap);

/* Receiver selected by the automatic two-microphone decoder. These values are
 * stable ABI constants and are also recorded in hardware-campaign artifacts. */
#define CYRINX_BULK_DIVERSITY_PRIMARY 0
#define CYRINX_BULK_DIVERSITY_MRC 1

/* Auto-diversity policy v1 accepts MRC only when its payload-independent
 * held-out pilot RMS error is strictly below this fraction of the primary
 * receiver's error. The square is used when comparing mean squared errors. */
#define CYRINX_BULK_AUTO_V1_POLICY_VERSION 1
#define CYRINX_BULK_AUTO_V1_MAX_MRC_PILOT_RMS_RATIO 0.95

#define CYRINX_BULK_AUTO_REASON_NOT_EVALUATED 0
#define CYRINX_BULK_AUTO_REASON_MRC_IMPROVED 1
#define CYRINX_BULK_AUTO_REASON_PRIMARY_MARGIN_NOT_MET 2
#define CYRINX_BULK_AUTO_REASON_INSUFFICIENT_PILOTS 3
#define CYRINX_BULK_AUTO_REASON_NONFINITE_SCORE 4
#define CYRINX_BULK_AUTO_REASON_RESOURCE_FAILURE 5
#define CYRINX_BULK_AUTO_REASON_SECOND_UNAVAILABLE 6

#define CYRINX_BULK_DIVERSITY_DIAGNOSTICS_ABI_VERSION 1

/* Payload-independent evidence and the receiver selected by automatic diversity.
 * `validation_observations` counts known odd-ordinal pilot observations across
 * all data symbols. Invalid or insufficient pilot evidence fails closed to the
 * primary receiver and reports infinite pilot RMS values. `struct_size` and
 * `abi_version` allow readers to reject layouts they do not understand. */
typedef struct {
    uint32_t struct_size;
    uint32_t abi_version;
    int policy_version;
    int selected_receiver; /* CYRINX_BULK_DIVERSITY_PRIMARY or _MRC */
    int scores_valid;      /* exactly 0 or 1 */
    int selection_reason;  /* CYRINX_BULK_AUTO_REASON_* */
    int validation_observations;
    double primary_holdout_pilot_rms;
    double mrc_holdout_pilot_rms;
    double observed_mrc_to_primary_pilot_rms_ratio;
    double maximum_mrc_to_primary_pilot_rms_ratio;
} cyrinx_bulk_diversity_diagnostics;

/* Versioned automatic two-microphone demodulator. Before demapping any data
 * bins, it evaluates the primary receiver and the existing MRC receiver using
 * known pilots only. Per symbol, even-ordinal pilots fit phase and timing slope
 * while odd-ordinal pilots provide held-out squared error. Errors are
 * accumulated over the full frame. MRC is selected only when both scores are
 * finite and its RMS error is strictly more than 5% lower; every other case
 * fails closed to the primary receiver. CRCs, decoded bytes, data-bin values,
 * and payload EVM never enter the selection. When primary is selected, the
 * decode is arithmetic-identical to cyrinx_bulk_demodulate. When MRC is
 * selected, it is arithmetic-identical to cyrinx_bulk_demodulate2. Existing
 * mono and raw-MRC ABIs and behavior are unchanged. `diagnostics` may be NULL.
 * A NULL `rx2` with zero length selects the primary receiver with a
 * SECOND_UNAVAILABLE reason. */
long cyrinx_bulk_demodulate2_auto_v1(const cyrinx_bulk_config *cfg, const float *rx, size_t rx_len,
                                     const float *rx2, size_t rx2_len, uint8_t *out_payload, size_t out_cap,
                                     int *blocks_ok, int *blocks_total, double *evm_rms,
                                     cyrinx_bulk_diversity_diagnostics *diagnostics);

/* Versioned automatic two-microphone demodulator with the ordered CRC-validity
 * contract of cyrinx_bulk_demodulate2_with_block_validity. */
long cyrinx_bulk_demodulate2_auto_v1_with_block_validity(const cyrinx_bulk_config *cfg, const float *rx,
                                                         size_t rx_len, const float *rx2, size_t rx2_len,
                                                         uint8_t *out_payload, size_t out_cap, int *blocks_ok,
                                                         int *blocks_total, double *evm_rms,
                                                         uint8_t *block_valid_out, size_t block_valid_cap,
                                                         cyrinx_bulk_diversity_diagnostics *diagnostics);

/* Default chirp/guard constants (modem.py). */
#define CYRINX_BULK_CHIRP_LEN 4096
#define CYRINX_BULK_GUARD 2048
#define CYRINX_BULK_CHIRP_F0 2000.0
#define CYRINX_BULK_CHIRP_F1 16000.0
#define CYRINX_BULK_CRC_BLOCK 256

#ifdef __cplusplus
}
#endif

#endif /* CYRINX_BULK_H */
