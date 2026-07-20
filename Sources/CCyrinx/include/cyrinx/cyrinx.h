#ifndef CYRINX_H
#define CYRINX_H

#include "cyrinx_base.h"

#if defined(_WIN32)
#if defined(CYRINX_BUILD)
#define CYRINX_API __declspec(dllexport)
#else
#define CYRINX_API __declspec(dllimport)
#endif
#else
#define CYRINX_API __attribute__((visibility("default")))
#endif

#ifdef __cplusplus
extern "C" {
#endif

typedef struct cyrinx_session cyrinx_session_t;

typedef enum { CYRINX_ROLE_MASTER = 0, CYRINX_ROLE_SLAVE = 1 } cyrinx_role_t;

typedef enum { CYRINX_QOS_BEST_EFFORT = 0, CYRINX_QOS_RELIABLE = 1 } cyrinx_qos_t;

typedef enum { CYRINX_SECURITY_NONE = 0, CYRINX_SECURITY_EXTERNAL = 1 } cyrinx_security_mode_t;

typedef enum { CYRINX_FRAME_DATA = 0, CYRINX_FRAME_ACK = 1, CYRINX_FRAME_CONTROL = 2 } cyrinx_frame_type_t;

/*
 * Multiplexing stream IDs:
 * - 0 is reserved for control plane traffic.
 * - application payloads should use stream IDs 1...4095.
 */
#define CYRINX_STREAM_CONTROL 0u
#define CYRINX_STREAM_DEFAULT 1u

/*
 * Frame flags used by the transport:
 * - FRAG_* are internal fragmentation markers.
 * - STREAM_* are exposed to applications for logical stream semantics.
 */
#define CYRINX_FLAG_FRAG_START 0x01u
#define CYRINX_FLAG_FRAG_END 0x02u
#define CYRINX_STREAM_FLAG_FIN 0x04u
#define CYRINX_STREAM_FLAG_RST 0x08u

typedef enum {
    CYRINX_GEAR_G1_DISCOVERY = 0,
    CYRINX_GEAR_G2_ROBUST = 1,
    CYRINX_GEAR_G3_QPSK = 2,
    CYRINX_GEAR_G3_16QAM = 3,
    CYRINX_GEAR_G3_64QAM = 4
} cyrinx_gear_t;

typedef enum {
    CYRINX_EVENT_IDLE = 0,
    CYRINX_EVENT_DISCOVERY = 1,
    CYRINX_EVENT_LINKED = 2,
    CYRINX_EVENT_DEGRADED = 3,
    CYRINX_EVENT_RECOVERING = 4,
    CYRINX_EVENT_FAILED = 5
} cyrinx_event_t;

typedef struct {
    float snr_db;
    float evm_pct;
    float cfo_hz;
    float per_2s;
    uint8_t crc_fail;
} cyrinx_channel_report_t;

#define CYRINX_DEVICE_GENERIC 0x00
#define CYRINX_DEVICE_MACBOOK_PRO 0x01
#define CYRINX_DEVICE_PIXEL_7A 0x02
#define CYRINX_NOTCH_MASK_BYTES 14
#define CYRINX_PUBLIC_KEY_BYTES 32
#define CYRINX_CAP_PAYLOAD_SECURE_BYTES 56

typedef struct {
    cyrinx_gear_t current_gear;
    float snr_db;
    float evm_pct;
    float cfo_hz;
    float per_2s;
    float goodput_bps;
    uint32_t tx_retries;
    uint32_t tx_frames;
    uint32_t rx_frames;
    uint32_t crc_failures;
    uint32_t link_resets;
    uint8_t retransmission_active;
    uint8_t peer_mics_count;
    uint8_t peer_speakers_count;
    uint8_t peer_device_signature;
    uint32_t peer_max_buffer_capacity;
    uint8_t peer_notch_mask[CYRINX_NOTCH_MASK_BYTES];
    uint8_t peer_public_key[CYRINX_PUBLIC_KEY_BYTES];
} cyrinx_metrics_t;

typedef struct {
    float up_g2_to_qpsk_snr_db;
    float up_qpsk_to_16qam_snr_db;
    float up_qpsk_to_16qam_max_evm_pct;
    float up_16qam_to_64qam_snr_db;
    float up_16qam_to_64qam_max_evm_pct;
    float down_64qam_to_16qam_snr_db;
    float down_64qam_to_16qam_max_evm_pct;
    float down_16qam_to_qpsk_snr_db;
    float down_16qam_to_qpsk_max_evm_pct;
    float down_qpsk_to_g2_snr_db;
    float up_g2_to_qpsk_max_per;
    float up_qpsk_to_16qam_max_per;
    float up_16qam_to_64qam_max_per;
    uint32_t up_g2_to_qpsk_hold_ms;
    uint32_t up_qpsk_to_16qam_hold_ms;
    uint32_t up_16qam_to_64qam_hold_ms;
    uint32_t min_dwell_ms;
    uint8_t qpsk_to_g2_crc_fail_count;
    uint8_t max_retransmissions;
    uint8_t window_size;
} cyrinx_arc_policy_t;

typedef struct {
    uint16_t stream_id;
    uint8_t priority;
    uint8_t flags;
    size_t payload_len;
} cyrinx_message_meta_t;

typedef int (*cyrinx_tx_callback_t)(const uint8_t *frame, size_t len, void *user_data);
typedef void (*cyrinx_event_callback_t)(cyrinx_event_t event, void *user_data);

/*
 * Session bootstrap configuration.
 *
 * The modem currently runs as a transport core with test/in-memory connectivity.
 * Real audio backends should honor:
 * - sample_rate_hz (48 kHz default)
 * - tx_gain_cap (speaker safety cap)
 * - band_start_hz / band_end_hz (ultrasonic band plan)
 * - CP and sensor-assisted ARC flags for dynamic geometry handling.
 * - device_signature & max_buffer_capacity for peer capability handshake.
 */
typedef struct {
    cyrinx_role_t role;
    uint32_t sample_rate_hz;
    uint32_t band_start_hz;
    uint32_t band_end_hz;
    float tx_gain_cap;
    float spectral_leakage_limit_dbfs;
    uint16_t ofdm_cp_samples_default;
    uint16_t ofdm_cp_samples_min;
    uint8_t enable_sensor_assisted_arc;
    uint8_t enable_dynamic_cp;
    uint8_t enable_sfbc_static_mode;
    cyrinx_security_mode_t security_mode;
    cyrinx_tx_callback_t tx_callback;
    cyrinx_event_callback_t event_callback;
    void *user_data;
    uint8_t mics_count;
    uint8_t speakers_count;
    uint8_t device_signature;
    uint32_t max_buffer_capacity;
    uint8_t notch_mask[CYRINX_NOTCH_MASK_BYTES];
    uint8_t local_public_key[CYRINX_PUBLIC_KEY_BYTES];
} cyrinx_config_t;

/* Returns the semantic version string of the linked cyrinx core. */
CYRINX_API const char *cyrinx_version(void);

/*
 * Returns a stable symbolic token for a status code.
 *
 * Example: -4 -> "CYRINX_ERR_TIMEOUT".
 */
CYRINX_API const char *cyrinx_status_name(int status);

/*
 * Returns a concise human-readable explanation for a status code.
 *
 * Example: -4 -> "Operation timed out waiting for link progress or ACK."
 */
CYRINX_API const char *cyrinx_status_description(int status);

/* Populate config/policy with PRD-aligned defaults. */
CYRINX_API void cyrinx_default_config(cyrinx_config_t *out_config);
CYRINX_API void cyrinx_default_arc_policy(cyrinx_arc_policy_t *out_policy);

/* Session lifecycle. */
CYRINX_API cyrinx_session_t *cyrinx_open(const cyrinx_config_t *config);
CYRINX_API int cyrinx_start(cyrinx_session_t *session);

/*
 * Stream-aware API for multiplexed logical channels.
 *
 * - stream_id: 1...4095 for app streams (0 reserved for control)
 * - priority: 0 (lowest) to 3 (highest)
 * - stream_flags: bitmask of CYRINX_STREAM_FLAG_*
 */
CYRINX_API int cyrinx_send_stream(cyrinx_session_t *session, const uint8_t *data, size_t len,
                                  cyrinx_qos_t qos, uint16_t stream_id, uint8_t priority,
                                  uint8_t stream_flags);
CYRINX_API int cyrinx_recv_stream(cyrinx_session_t *session, uint8_t *out, size_t *inout_len,
                                  uint32_t timeout_ms, cyrinx_message_meta_t *out_meta);

CYRINX_API int cyrinx_get_metrics(cyrinx_session_t *session, cyrinx_metrics_t *out);
CYRINX_API int cyrinx_set_arc_policy(cyrinx_session_t *session, const cyrinx_arc_policy_t *policy);

/* Stops and releases all resources associated with a session handle. */
CYRINX_API void cyrinx_close(cyrinx_session_t *session);

/* Transport integration hooks for modem layers. */
CYRINX_API int cyrinx_ingest_frame(cyrinx_session_t *session, const uint8_t *frame, size_t len,
                                   const cyrinx_channel_report_t *report);
CYRINX_API int cyrinx_update_channel_report(cyrinx_session_t *session, const cyrinx_channel_report_t *report);

/* Testing/demo helper: connect sessions in memory with no audio path. */
CYRINX_API int cyrinx_link_in_memory(cyrinx_session_t *a, cyrinx_session_t *b);

/*
 * Pure ARC policy evaluation helper.
 *
 * Used by tests/simulation to validate deterministic gear decisions independent
 * of session state.
 */
CYRINX_API cyrinx_gear_t cyrinx_arc_select_gear(cyrinx_gear_t current, const cyrinx_metrics_t *metrics,
                                                const cyrinx_arc_policy_t *policy, uint32_t dwell_ms,
                                                uint8_t retransmission_active,
                                                uint8_t consecutive_crc_failures, uint8_t timeout_lost);

#include "cyrinx_phy.h"
#include "cyrinx_profiles.h"
#include "cyrinx_batch.h"

#ifdef __cplusplus
}
#endif

#endif
