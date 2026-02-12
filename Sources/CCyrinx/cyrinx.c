#include "include/cyrinx/cyrinx.h"

#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#if defined(_WIN32)
#include <windows.h>
#else
#include <unistd.h>
#endif

#define CYRINX_MAGIC0 0xC7u
#define CYRINX_MAGIC1 0x58u
#define CYRINX_VERSION_STR "0.1.0"

#define CYRINX_MAX_FRAME_PAYLOAD 1024u
#define CYRINX_MAX_LOGICAL_MESSAGE 4096u
#define CYRINX_HEADER_BITS 106u
#define CYRINX_HEADER_BYTES 14u
#define CYRINX_HEADER_NOCRC_BITS 90u
#define CYRINX_HEADER_NOCRC_BYTES 12u
#define CYRINX_FIXED_PREAMBLE_BYTES 2u
#define CYRINX_FRAME_CRC_BYTES 4u
#define CYRINX_FRAME_MIN_SIZE (CYRINX_FIXED_PREAMBLE_BYTES + CYRINX_HEADER_BYTES + CYRINX_FRAME_CRC_BYTES)

#define CYRINX_FLAG_FRAG_START 0x01u
#define CYRINX_FLAG_FRAG_END 0x02u

#define CYRINX_TIMEOUT_MS 1200u

#define CYRINX_ACK_REPORT_PAYLOAD_BYTES 10u

/*
 * Frame header used on the wire (bit-packed to 106 bits).
 *
 * The binary layout matches the PRD and is intentionally independent from host
 * ABI alignment.
 */
typedef struct {
    uint8_t version;
    uint8_t frame_type;
    uint32_t session_id;
    uint16_t seq;
    uint16_t ack;
    uint8_t gear_id;
    uint8_t fec_rate;
    uint16_t payload_len;
    uint8_t flags;
    uint16_t header_crc16;
} cyrinx_frame_header_t;

typedef struct cyrinx_message_node {
    uint8_t *data;
    size_t len;
    struct cyrinx_message_node *next;
} cyrinx_message_node_t;

struct cyrinx_session {
    cyrinx_config_t config;
    cyrinx_arc_policy_t policy;
    cyrinx_metrics_t metrics;

    bool started;
    uint32_t session_id;

    cyrinx_gear_t current_gear;
    uint64_t last_gear_change_ms;
    cyrinx_event_t current_event;

    uint16_t next_tx_seq;
    uint16_t last_rx_seq;

    bool awaiting_ack;
    uint16_t awaiting_ack_seq;
    uint64_t ack_deadline_ms;

    /* Used by ARC downshift logic when quality collapses. */
    uint8_t consecutive_crc_failures;

    cyrinx_channel_report_t last_report;

    uint8_t reassembly[CYRINX_MAX_LOGICAL_MESSAGE];
    size_t reassembly_len;
    bool reassembly_active;

    cyrinx_message_node_t *rx_head;
    cyrinx_message_node_t *rx_tail;

    struct cyrinx_session *linked_peer;
};

static uint64_t cyrinx_now_ms(void) {
#if defined(_WIN32)
    return (uint64_t)GetTickCount64();
#else
    struct timespec ts;
    (void)clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000u + (uint64_t)(ts.tv_nsec / 1000000u);
#endif
}

static void cyrinx_sleep_ms(uint32_t ms) {
#if defined(_WIN32)
    Sleep(ms);
#else
    usleep(ms * 1000u);
#endif
}

static uint16_t cyrinx_crc16_ccitt(const uint8_t *data, size_t len) {
    uint16_t crc = 0xFFFFu;
    for (size_t i = 0; i < len; ++i) {
        crc ^= (uint16_t)data[i] << 8;
        for (int j = 0; j < 8; ++j) {
            if (crc & 0x8000u) {
                crc = (uint16_t)((crc << 1) ^ 0x1021u);
            } else {
                crc <<= 1;
            }
        }
    }
    return crc;
}

static uint32_t cyrinx_crc32c(const uint8_t *data, size_t len) {
    uint32_t crc = 0xFFFFFFFFu;
    for (size_t i = 0; i < len; ++i) {
        crc ^= data[i];
        for (int j = 0; j < 8; ++j) {
            uint32_t mask = (uint32_t)-(int32_t)(crc & 1u);
            crc = (crc >> 1) ^ (0x82F63B78u & mask);
        }
    }
    return ~crc;
}

static void cyrinx_write_u16_be(uint8_t *out, uint16_t v) {
    out[0] = (uint8_t)((v >> 8) & 0xFFu);
    out[1] = (uint8_t)(v & 0xFFu);
}

static uint16_t cyrinx_read_u16_be(const uint8_t *in) {
    return (uint16_t)(((uint16_t)in[0] << 8) | in[1]);
}

static void cyrinx_write_u32_be(uint8_t *out, uint32_t v) {
    out[0] = (uint8_t)((v >> 24) & 0xFFu);
    out[1] = (uint8_t)((v >> 16) & 0xFFu);
    out[2] = (uint8_t)((v >> 8) & 0xFFu);
    out[3] = (uint8_t)(v & 0xFFu);
}

static uint32_t cyrinx_read_u32_be(const uint8_t *in) {
    return ((uint32_t)in[0] << 24) | ((uint32_t)in[1] << 16) | ((uint32_t)in[2] << 8) | (uint32_t)in[3];
}

static void cyrinx_bw_write(uint8_t *buf, size_t *bit_pos, uint32_t value, size_t bits) {
    /* MSB-first packing for deterministic cross-platform frame encoding. */
    for (size_t i = 0; i < bits; ++i) {
        size_t shift = bits - 1u - i;
        uint8_t bit = (uint8_t)((value >> shift) & 0x1u);
        size_t byte_index = *bit_pos / 8u;
        size_t bit_index = 7u - (*bit_pos % 8u);
        if (bit) {
            buf[byte_index] |= (uint8_t)(1u << bit_index);
        }
        (*bit_pos)++;
    }
}

static uint32_t cyrinx_br_read(const uint8_t *buf, size_t *bit_pos, size_t bits) {
    /* Mirror of cyrinx_bw_write: MSB-first bit unpacking. */
    uint32_t value = 0;
    for (size_t i = 0; i < bits; ++i) {
        size_t byte_index = *bit_pos / 8u;
        size_t bit_index = 7u - (*bit_pos % 8u);
        uint8_t bit = (uint8_t)((buf[byte_index] >> bit_index) & 0x1u);
        value = (value << 1u) | bit;
        (*bit_pos)++;
    }
    return value;
}

static uint8_t cyrinx_fec_rate_for_gear(cyrinx_gear_t gear) {
    switch (gear) {
    case CYRINX_GEAR_G2_ROBUST:
        return 1; /* 1/2 */
    case CYRINX_GEAR_G3_QPSK:
        return 2; /* 2/3 */
    case CYRINX_GEAR_G3_16QAM:
        return 2; /* 2/3 */
    case CYRINX_GEAR_G3_64QAM:
        return 3; /* 3/4 */
    case CYRINX_GEAR_G1_DISCOVERY:
    default:
        return 0;
    }
}

static bool cyrinx_serialize_header(const cyrinx_frame_header_t *h, uint8_t out[CYRINX_HEADER_BYTES]) {
    if (!h || !out) {
        return false;
    }
    memset(out, 0, CYRINX_HEADER_BYTES);

    size_t bit_pos = 0;
    cyrinx_bw_write(out, &bit_pos, h->version & 0xFu, 4);
    cyrinx_bw_write(out, &bit_pos, h->frame_type & 0xFu, 4);
    cyrinx_bw_write(out, &bit_pos, h->session_id & 0xFFFFFFu, 24);
    cyrinx_bw_write(out, &bit_pos, h->seq, 16);
    cyrinx_bw_write(out, &bit_pos, h->ack, 16);
    cyrinx_bw_write(out, &bit_pos, h->gear_id & 0x7u, 3);
    cyrinx_bw_write(out, &bit_pos, h->fec_rate & 0x7u, 3);
    cyrinx_bw_write(out, &bit_pos, h->payload_len & 0xFFFu, 12);
    cyrinx_bw_write(out, &bit_pos, h->flags, 8);

    if (bit_pos != CYRINX_HEADER_NOCRC_BITS) {
        return false;
    }

    /*
     * Header CRC intentionally covers only the first 90 bits.
     * Remaining bits in byte 12 are part of the CRC field itself.
     */
    uint16_t crc = cyrinx_crc16_ccitt(out, CYRINX_HEADER_NOCRC_BYTES);
    cyrinx_bw_write(out, &bit_pos, crc, 16);

    return bit_pos == CYRINX_HEADER_BITS;
}

static bool cyrinx_parse_header(const uint8_t in[CYRINX_HEADER_BYTES], cyrinx_frame_header_t *h) {
    if (!in || !h) {
        return false;
    }

    uint8_t header_nocrc[CYRINX_HEADER_NOCRC_BYTES];
    memcpy(header_nocrc, in, CYRINX_HEADER_NOCRC_BYTES);
    /* Only the first 90 bits are covered; clear trailing bits in the last byte. */
    header_nocrc[CYRINX_HEADER_NOCRC_BYTES - 1u] &= 0xC0u;
    uint16_t expected_crc = cyrinx_crc16_ccitt(header_nocrc, CYRINX_HEADER_NOCRC_BYTES);

    size_t bit_pos = 0;
    h->version = (uint8_t)cyrinx_br_read(in, &bit_pos, 4);
    h->frame_type = (uint8_t)cyrinx_br_read(in, &bit_pos, 4);
    h->session_id = cyrinx_br_read(in, &bit_pos, 24);
    h->seq = (uint16_t)cyrinx_br_read(in, &bit_pos, 16);
    h->ack = (uint16_t)cyrinx_br_read(in, &bit_pos, 16);
    h->gear_id = (uint8_t)cyrinx_br_read(in, &bit_pos, 3);
    h->fec_rate = (uint8_t)cyrinx_br_read(in, &bit_pos, 3);
    h->payload_len = (uint16_t)cyrinx_br_read(in, &bit_pos, 12);
    h->flags = (uint8_t)cyrinx_br_read(in, &bit_pos, 8);
    h->header_crc16 = (uint16_t)cyrinx_br_read(in, &bit_pos, 16);

    return (bit_pos == CYRINX_HEADER_BITS) && (h->header_crc16 == expected_crc);
}

static void cyrinx_emit_event(cyrinx_session_t *session, cyrinx_event_t event) {
    if (!session) {
        return;
    }
    if (session->current_event == event) {
        return;
    }
    session->current_event = event;
    if (session->config.event_callback) {
        session->config.event_callback(event, session->config.user_data);
    }
}

static void cyrinx_update_goodput(cyrinx_session_t *session, size_t payload_bytes) {
    if (!session) {
        return;
    }

    float gear_bps = 300.0f;
    switch (session->current_gear) {
    case CYRINX_GEAR_G3_QPSK:
        gear_bps = 4000.0f;
        break;
    case CYRINX_GEAR_G3_16QAM:
        gear_bps = 8000.0f;
        break;
    case CYRINX_GEAR_G3_64QAM:
        gear_bps = 12000.0f;
        break;
    case CYRINX_GEAR_G2_ROBUST:
        gear_bps = 450.0f;
        break;
    case CYRINX_GEAR_G1_DISCOVERY:
    default:
        gear_bps = 150.0f;
        break;
    }

    /* EMA smoothing so ARC can consume a stable throughput estimate. */
    float sample = (float)(payload_bytes * 8u);
    session->metrics.goodput_bps =
        (0.8f * session->metrics.goodput_bps) + (0.2f * (sample > gear_bps ? gear_bps : sample));
}

static void cyrinx_queue_message(cyrinx_session_t *session, const uint8_t *data, size_t len) {
    if (!session || !data || len == 0) {
        return;
    }

    cyrinx_message_node_t *node = (cyrinx_message_node_t *)calloc(1, sizeof(*node));
    if (!node) {
        return;
    }

    node->data = (uint8_t *)malloc(len);
    if (!node->data) {
        free(node);
        return;
    }

    memcpy(node->data, data, len);
    node->len = len;

    if (!session->rx_tail) {
        session->rx_head = session->rx_tail = node;
    } else {
        session->rx_tail->next = node;
        session->rx_tail = node;
    }
}

static void cyrinx_apply_channel_report(cyrinx_session_t *session, const cyrinx_channel_report_t *report) {
    if (!session || !report) {
        return;
    }

    session->last_report = *report;
    session->metrics.snr_db = report->snr_db;
    session->metrics.evm_pct = report->evm_pct;
    session->metrics.cfo_hz = report->cfo_hz;
    session->metrics.per_2s = report->per_2s;
    if (report->crc_fail) {
        session->metrics.crc_failures += 1u;
        session->consecutive_crc_failures = (uint8_t)(session->consecutive_crc_failures + 1u);
    } else {
        session->consecutive_crc_failures = 0;
    }
}

static void cyrinx_set_gear(cyrinx_session_t *session, cyrinx_gear_t next) {
    if (!session) {
        return;
    }
    if (session->current_gear == next) {
        return;
    }

    session->current_gear = next;
    session->metrics.current_gear = next;
    session->last_gear_change_ms = cyrinx_now_ms();

    /* Event mapping keeps UX state machine aligned with physical link state. */
    if (next == CYRINX_GEAR_G1_DISCOVERY) {
        cyrinx_emit_event(session, CYRINX_EVENT_DISCOVERY);
    } else if (next == CYRINX_GEAR_G2_ROBUST) {
        cyrinx_emit_event(session, CYRINX_EVENT_DEGRADED);
    } else {
        cyrinx_emit_event(session, CYRINX_EVENT_LINKED);
    }
}

static int cyrinx_dispatch_frame(cyrinx_session_t *session, const uint8_t *frame, size_t len) {
    if (!session || !frame || len == 0) {
        return CYRINX_ERR_INVALID_ARGUMENT;
    }

    if (session->config.tx_callback) {
        int rc = session->config.tx_callback(frame, len, session->config.user_data);
        if (rc != 0) {
            return CYRINX_ERR_INTERNAL;
        }
    }

    /*
     * In-memory link path provides deterministic simulation without real audio IO.
     * Real modem backends call cyrinx_ingest_frame after demodulation.
     */
    if (session->linked_peer) {
        int rc = cyrinx_ingest_frame(session->linked_peer, frame, len, NULL);
        if (rc != CYRINX_OK) {
            return rc;
        }
    }

    return CYRINX_OK;
}

static int cyrinx_send_internal(cyrinx_session_t *session, cyrinx_frame_type_t frame_type, uint16_t seq,
                                uint16_t ack, uint8_t flags, const uint8_t *payload, size_t payload_len,
                                bool track_ack) {
    if (!session) {
        return CYRINX_ERR_INVALID_ARGUMENT;
    }
    if (payload_len > CYRINX_MAX_FRAME_PAYLOAD) {
        return CYRINX_ERR_INVALID_ARGUMENT;
    }

    size_t frame_len =
        CYRINX_FIXED_PREAMBLE_BYTES + CYRINX_HEADER_BYTES + payload_len + CYRINX_FRAME_CRC_BYTES;
    uint8_t *frame = (uint8_t *)malloc(frame_len);
    if (!frame) {
        return CYRINX_ERR_INTERNAL;
    }

    frame[0] = CYRINX_MAGIC0;
    frame[1] = CYRINX_MAGIC1;

    cyrinx_frame_header_t h;
    memset(&h, 0, sizeof(h));
    h.version = 1u;
    h.frame_type = (uint8_t)frame_type;
    h.session_id = session->session_id;
    h.seq = seq;
    h.ack = ack;
    h.gear_id = (uint8_t)session->current_gear;
    h.fec_rate = cyrinx_fec_rate_for_gear(session->current_gear);
    h.payload_len = (uint16_t)payload_len;
    h.flags = flags;

    if (!cyrinx_serialize_header(&h, frame + CYRINX_FIXED_PREAMBLE_BYTES)) {
        free(frame);
        return CYRINX_ERR_INTERNAL;
    }

    if (payload_len > 0 && payload) {
        memcpy(frame + CYRINX_FIXED_PREAMBLE_BYTES + CYRINX_HEADER_BYTES, payload, payload_len);
    }

    uint32_t crc = cyrinx_crc32c(frame, frame_len - CYRINX_FRAME_CRC_BYTES);
    cyrinx_write_u32_be(frame + frame_len - CYRINX_FRAME_CRC_BYTES, crc);

    if (track_ack) {
        /* ACK deadline models the half-duplex ping-pong timing budget. */
        session->awaiting_ack = true;
        session->awaiting_ack_seq = seq;
        session->ack_deadline_ms = cyrinx_now_ms() + CYRINX_TIMEOUT_MS;
    }

    int rc = cyrinx_dispatch_frame(session, frame, frame_len);
    free(frame);

    if (rc == CYRINX_OK) {
        session->metrics.tx_frames += 1u;
        cyrinx_update_goodput(session, payload_len);
    }

    return rc;
}

static void cyrinx_encode_ack_report(const cyrinx_channel_report_t *report,
                                     uint8_t out[CYRINX_ACK_REPORT_PAYLOAD_BYTES]) {
    memset(out, 0, CYRINX_ACK_REPORT_PAYLOAD_BYTES);
    if (!report) {
        return;
    }

    int16_t snr = (int16_t)(report->snr_db * 10.0f);
    int16_t evm = (int16_t)(report->evm_pct * 10.0f);
    int16_t cfo = (int16_t)(report->cfo_hz * 10.0f);
    uint16_t per = (uint16_t)(report->per_2s * 1000.0f);

    cyrinx_write_u16_be(out + 0, (uint16_t)snr);
    cyrinx_write_u16_be(out + 2, (uint16_t)evm);
    cyrinx_write_u16_be(out + 4, (uint16_t)cfo);
    cyrinx_write_u16_be(out + 6, per);
    out[8] = report->crc_fail;
    out[9] = (uint8_t)0;
}

static void cyrinx_decode_ack_report(const uint8_t *in, size_t len, cyrinx_channel_report_t *out) {
    if (!in || !out || len < CYRINX_ACK_REPORT_PAYLOAD_BYTES) {
        return;
    }

    int16_t snr = (int16_t)cyrinx_read_u16_be(in + 0);
    int16_t evm = (int16_t)cyrinx_read_u16_be(in + 2);
    int16_t cfo = (int16_t)cyrinx_read_u16_be(in + 4);
    uint16_t per = cyrinx_read_u16_be(in + 6);

    out->snr_db = (float)snr / 10.0f;
    out->evm_pct = (float)evm / 10.0f;
    out->cfo_hz = (float)cfo / 10.0f;
    out->per_2s = (float)per / 1000.0f;
    out->crc_fail = in[8];
}

const char *cyrinx_version(void) {
    return CYRINX_VERSION_STR;
}

void cyrinx_default_config(cyrinx_config_t *out_config) {
    if (!out_config) {
        return;
    }
    memset(out_config, 0, sizeof(*out_config));
    out_config->role = CYRINX_ROLE_MASTER;
    out_config->sample_rate_hz = 48000u;
    out_config->band_start_hz = 18500u;
    out_config->band_end_hz = 23500u;
    out_config->tx_gain_cap = 0.70f;
    out_config->spectral_leakage_limit_dbfs = -45.0f;
    out_config->ofdm_cp_samples_default = 96u;
    out_config->ofdm_cp_samples_min = 10u;
    out_config->enable_sensor_assisted_arc = 1u;
    out_config->enable_dynamic_cp = 1u;
    out_config->enable_sfbc_static_mode = 1u;
    out_config->security_mode = CYRINX_SECURITY_EXTERNAL;
}

void cyrinx_default_arc_policy(cyrinx_arc_policy_t *out_policy) {
    if (!out_policy) {
        return;
    }
    memset(out_policy, 0, sizeof(*out_policy));

    /* Defaults follow the current PRD thresholds and hysteresis windows. */
    out_policy->up_g2_to_qpsk_snr_db = 14.0f;
    out_policy->up_qpsk_to_16qam_snr_db = 25.0f;
    out_policy->up_qpsk_to_16qam_max_evm_pct = 5.0f;
    out_policy->up_16qam_to_64qam_snr_db = 30.0f;
    out_policy->up_16qam_to_64qam_max_evm_pct = 4.5f;
    out_policy->down_64qam_to_16qam_snr_db = 28.0f;
    out_policy->down_64qam_to_16qam_max_evm_pct = 6.0f;
    out_policy->down_16qam_to_qpsk_snr_db = 18.0f;
    out_policy->down_16qam_to_qpsk_max_evm_pct = 10.0f;
    out_policy->down_qpsk_to_g2_snr_db = 15.0f;

    out_policy->up_g2_to_qpsk_max_per = 0.05f;
    out_policy->up_qpsk_to_16qam_max_per = 0.01f;
    out_policy->up_16qam_to_64qam_max_per = 0.005f;

    out_policy->up_g2_to_qpsk_hold_ms = 1500u;
    out_policy->up_qpsk_to_16qam_hold_ms = 2000u;
    out_policy->up_16qam_to_64qam_hold_ms = 3000u;
    out_policy->min_dwell_ms = 1000u;

    out_policy->qpsk_to_g2_crc_fail_count = 2u;
    out_policy->max_retransmissions = 4u;
    out_policy->window_size = 8u;
}

cyrinx_gear_t cyrinx_arc_select_gear(cyrinx_gear_t current, const cyrinx_metrics_t *metrics,
                                     const cyrinx_arc_policy_t *policy, uint32_t dwell_ms,
                                     uint8_t retransmission_active, uint8_t consecutive_crc_failures,
                                     uint8_t timeout_lost) {
    if (!metrics || !policy) {
        return current;
    }

    if (timeout_lost) {
        return CYRINX_GEAR_G1_DISCOVERY;
    }

    /*
     * Reliability-first policy:
     * evaluate all downshift conditions before considering any upshift.
     */
    if (current == CYRINX_GEAR_G3_64QAM) {
        if ((metrics->snr_db < policy->down_64qam_to_16qam_snr_db) ||
            (metrics->evm_pct > policy->down_64qam_to_16qam_max_evm_pct)) {
            return CYRINX_GEAR_G3_16QAM;
        }
    }

    if (current == CYRINX_GEAR_G3_16QAM) {
        if ((metrics->snr_db < policy->down_16qam_to_qpsk_snr_db) ||
            (metrics->evm_pct > policy->down_16qam_to_qpsk_max_evm_pct)) {
            return CYRINX_GEAR_G3_QPSK;
        }
    }

    if (current == CYRINX_GEAR_G3_QPSK) {
        if ((metrics->snr_db < policy->down_qpsk_to_g2_snr_db) ||
            (consecutive_crc_failures >= policy->qpsk_to_g2_crc_fail_count)) {
            return CYRINX_GEAR_G2_ROBUST;
        }
    }

    if (retransmission_active || dwell_ms < policy->min_dwell_ms) {
        /* Hold current MCS during retransmits and before dwell hysteresis expires. */
        return current;
    }

    if (current == CYRINX_GEAR_G2_ROBUST) {
        if ((metrics->snr_db >= policy->up_g2_to_qpsk_snr_db) &&
            (metrics->per_2s <= policy->up_g2_to_qpsk_max_per) &&
            (dwell_ms >= policy->up_g2_to_qpsk_hold_ms)) {
            return CYRINX_GEAR_G3_QPSK;
        }
    }

    if (current == CYRINX_GEAR_G3_QPSK) {
        if ((metrics->snr_db >= policy->up_qpsk_to_16qam_snr_db) &&
            (metrics->evm_pct <= policy->up_qpsk_to_16qam_max_evm_pct) &&
            (metrics->per_2s <= policy->up_qpsk_to_16qam_max_per) &&
            (dwell_ms >= policy->up_qpsk_to_16qam_hold_ms)) {
            return CYRINX_GEAR_G3_16QAM;
        }
    }

    if (current == CYRINX_GEAR_G3_16QAM) {
        if ((metrics->snr_db >= policy->up_16qam_to_64qam_snr_db) &&
            (metrics->evm_pct <= policy->up_16qam_to_64qam_max_evm_pct) &&
            (metrics->per_2s <= policy->up_16qam_to_64qam_max_per) &&
            (dwell_ms >= policy->up_16qam_to_64qam_hold_ms)) {
            return CYRINX_GEAR_G3_64QAM;
        }
    }

    return current;
}

static void cyrinx_apply_arc(cyrinx_session_t *session, uint8_t timeout_lost) {
    if (!session) {
        return;
    }

    uint64_t now = cyrinx_now_ms();
    uint32_t dwell = (uint32_t)(now - session->last_gear_change_ms);

    cyrinx_gear_t next = cyrinx_arc_select_gear(session->current_gear, &session->metrics, &session->policy,
                                                dwell, session->metrics.retransmission_active,
                                                session->consecutive_crc_failures, timeout_lost);

    if (timeout_lost) {
        /* Link timeout is treated as hard loss; reset to discovery gear. */
        session->metrics.link_resets += 1u;
        cyrinx_emit_event(session, CYRINX_EVENT_FAILED);
    }

    cyrinx_set_gear(session, next);
}

cyrinx_session_t *cyrinx_open(const cyrinx_config_t *config) {
    if (!config) {
        return NULL;
    }
    if (!config->sample_rate_hz) {
        return NULL;
    }
    if (config->band_start_hz >= config->band_end_hz) {
        return NULL;
    }

    cyrinx_session_t *session = (cyrinx_session_t *)calloc(1, sizeof(*session));
    if (!session) {
        return NULL;
    }

    session->config = *config;
    cyrinx_default_arc_policy(&session->policy);

    session->session_id = (uint32_t)((cyrinx_now_ms() ^ (uintptr_t)session) & 0xFFFFFFu);
    session->current_gear = CYRINX_GEAR_G1_DISCOVERY;
    session->metrics.current_gear = CYRINX_GEAR_G1_DISCOVERY;
    session->last_gear_change_ms = cyrinx_now_ms();
    session->current_event = CYRINX_EVENT_IDLE;

    return session;
}

int cyrinx_start(cyrinx_session_t *session) {
    if (!session) {
        return CYRINX_ERR_INVALID_ARGUMENT;
    }
    if (session->started) {
        return CYRINX_OK;
    }

    session->started = true;
    session->metrics.current_gear = CYRINX_GEAR_G1_DISCOVERY;
    session->current_gear = CYRINX_GEAR_G1_DISCOVERY;
    session->last_gear_change_ms = cyrinx_now_ms();

    cyrinx_emit_event(session, CYRINX_EVENT_DISCOVERY);
    return CYRINX_OK;
}

int cyrinx_send(cyrinx_session_t *session, const uint8_t *data, size_t len, cyrinx_qos_t qos) {
    if (!session || !data || len == 0 || len > CYRINX_MAX_LOGICAL_MESSAGE) {
        return CYRINX_ERR_INVALID_ARGUMENT;
    }
    if (!session->started) {
        return CYRINX_ERR_NOT_RUNNING;
    }

    size_t sent = 0;
    size_t fragment_index = 0;

    /* Fragment logical payload into PHY-sized frames with per-fragment reliability. */
    while (sent < len) {
        size_t remaining = len - sent;
        size_t frag_len = remaining > CYRINX_MAX_FRAME_PAYLOAD ? CYRINX_MAX_FRAME_PAYLOAD : remaining;

        uint8_t flags = 0;
        if (fragment_index == 0) {
            flags |= CYRINX_FLAG_FRAG_START;
        }
        if (sent + frag_len == len) {
            flags |= CYRINX_FLAG_FRAG_END;
        }

        uint16_t seq = session->next_tx_seq++;

        uint8_t attempts = 0;
        int rc = CYRINX_OK;
        bool delivered = false;

        do {
            session->metrics.retransmission_active = (attempts > 0);
            rc = cyrinx_send_internal(session, CYRINX_FRAME_DATA, seq, session->last_rx_seq, flags,
                                      data + sent, frag_len, qos == CYRINX_QOS_RELIABLE);
            if (rc != CYRINX_OK) {
                return rc;
            }

            if (qos != CYRINX_QOS_RELIABLE) {
                delivered = true;
                break;
            }

            /* Busy wait is sufficient for simulation; audio backend will become event-driven. */
            while (session->awaiting_ack && cyrinx_now_ms() < session->ack_deadline_ms) {
                cyrinx_sleep_ms(1u);
            }

            if (!session->awaiting_ack) {
                delivered = true;
                break;
            }

            attempts++;
            session->metrics.tx_retries += 1u;
            session->metrics.per_2s = 1.0f;
        } while (attempts <= session->policy.max_retransmissions);

        session->metrics.retransmission_active = 0;

        if (!delivered) {
            /* Timeout after retry budget: force recovery through discovery. */
            session->awaiting_ack = false;
            cyrinx_apply_arc(session, 1u);
            return CYRINX_ERR_TIMEOUT;
        }

        sent += frag_len;
        fragment_index += 1u;
        session->metrics.per_2s *= 0.95f;
        cyrinx_apply_arc(session, 0u);
    }

    return CYRINX_OK;
}

int cyrinx_recv(cyrinx_session_t *session, uint8_t *out, size_t *inout_len, uint32_t timeout_ms) {
    if (!session || !inout_len) {
        return CYRINX_ERR_INVALID_ARGUMENT;
    }
    if (!session->started) {
        return CYRINX_ERR_NOT_RUNNING;
    }

    uint64_t deadline = cyrinx_now_ms() + timeout_ms;

    while (!session->rx_head) {
        if (timeout_ms == 0u) {
            return CYRINX_ERR_TIMEOUT;
        }
        if (cyrinx_now_ms() >= deadline) {
            return CYRINX_ERR_TIMEOUT;
        }
        cyrinx_sleep_ms(1u);
    }

    cyrinx_message_node_t *node = session->rx_head;
    if (*inout_len < node->len) {
        *inout_len = node->len;
        return CYRINX_ERR_BUFFER_TOO_SMALL;
    }

    memcpy(out, node->data, node->len);
    *inout_len = node->len;

    session->rx_head = node->next;
    if (!session->rx_head) {
        session->rx_tail = NULL;
    }

    free(node->data);
    free(node);

    return CYRINX_OK;
}

int cyrinx_get_metrics(cyrinx_session_t *session, cyrinx_metrics_t *out) {
    if (!session || !out) {
        return CYRINX_ERR_INVALID_ARGUMENT;
    }
    *out = session->metrics;
    return CYRINX_OK;
}

int cyrinx_set_arc_policy(cyrinx_session_t *session, const cyrinx_arc_policy_t *policy) {
    if (!session || !policy) {
        return CYRINX_ERR_INVALID_ARGUMENT;
    }
    session->policy = *policy;
    return CYRINX_OK;
}

int cyrinx_update_channel_report(cyrinx_session_t *session, const cyrinx_channel_report_t *report) {
    if (!session || !report) {
        return CYRINX_ERR_INVALID_ARGUMENT;
    }
    cyrinx_apply_channel_report(session, report);
    cyrinx_apply_arc(session, 0u);
    return CYRINX_OK;
}

int cyrinx_ingest_frame(cyrinx_session_t *session, const uint8_t *frame, size_t len,
                        const cyrinx_channel_report_t *report) {
    if (!session || !frame || len < CYRINX_FRAME_MIN_SIZE) {
        return CYRINX_ERR_INVALID_ARGUMENT;
    }
    if (!session->started) {
        return CYRINX_ERR_NOT_RUNNING;
    }

    if (frame[0] != CYRINX_MAGIC0 || frame[1] != CYRINX_MAGIC1) {
        return CYRINX_ERR_CRC;
    }

    uint32_t expected = cyrinx_read_u32_be(frame + len - CYRINX_FRAME_CRC_BYTES);
    uint32_t actual = cyrinx_crc32c(frame, len - CYRINX_FRAME_CRC_BYTES);
    if (expected != actual) {
        session->metrics.crc_failures += 1u;
        session->consecutive_crc_failures = (uint8_t)(session->consecutive_crc_failures + 1u);
        cyrinx_apply_arc(session, 0u);
        return CYRINX_ERR_CRC;
    }

    cyrinx_frame_header_t h;
    if (!cyrinx_parse_header(frame + CYRINX_FIXED_PREAMBLE_BYTES, &h)) {
        session->metrics.crc_failures += 1u;
        session->consecutive_crc_failures = (uint8_t)(session->consecutive_crc_failures + 1u);
        cyrinx_apply_arc(session, 0u);
        return CYRINX_ERR_CRC;
    }

    size_t payload_len = h.payload_len;
    if (CYRINX_FIXED_PREAMBLE_BYTES + CYRINX_HEADER_BYTES + payload_len + CYRINX_FRAME_CRC_BYTES != len) {
        return CYRINX_ERR_INVALID_ARGUMENT;
    }

    const uint8_t *payload = frame + CYRINX_FIXED_PREAMBLE_BYTES + CYRINX_HEADER_BYTES;

    if (report) {
        cyrinx_apply_channel_report(session, report);
    }

    session->last_rx_seq = h.seq;
    session->metrics.rx_frames += 1u;
    session->consecutive_crc_failures = 0u;

    if (h.frame_type == CYRINX_FRAME_ACK) {
        /* ACK frames may carry channel report feedback for ARC decisions. */
        if (session->awaiting_ack && h.ack == session->awaiting_ack_seq) {
            session->awaiting_ack = false;
        }
        if (payload_len >= CYRINX_ACK_REPORT_PAYLOAD_BYTES) {
            cyrinx_channel_report_t ack_report;
            memset(&ack_report, 0, sizeof(ack_report));
            cyrinx_decode_ack_report(payload, payload_len, &ack_report);
            cyrinx_apply_channel_report(session, &ack_report);
        }
        cyrinx_apply_arc(session, 0u);
        return CYRINX_OK;
    }

    if (h.frame_type == CYRINX_FRAME_DATA) {
        /* Reassembly accumulates fragments until FRAG_END flag arrives. */
        if ((h.flags & CYRINX_FLAG_FRAG_START) != 0u) {
            session->reassembly_len = 0;
            session->reassembly_active = true;
        } else if (!session->reassembly_active) {
            session->reassembly_len = 0;
            session->reassembly_active = true;
        }

        if ((session->reassembly_len + payload_len) > CYRINX_MAX_LOGICAL_MESSAGE) {
            session->reassembly_len = 0;
            session->reassembly_active = false;
            return CYRINX_ERR_BUFFER_TOO_SMALL;
        }

        memcpy(session->reassembly + session->reassembly_len, payload, payload_len);
        session->reassembly_len += payload_len;

        if ((h.flags & CYRINX_FLAG_FRAG_END) != 0u) {
            cyrinx_queue_message(session, session->reassembly, session->reassembly_len);
            session->reassembly_len = 0;
            session->reassembly_active = false;
        }

        /* Immediate ACK keeps ping-pong control loop tight. */
        uint8_t ack_payload[CYRINX_ACK_REPORT_PAYLOAD_BYTES];
        cyrinx_encode_ack_report(&session->last_report, ack_payload);
        int rc = cyrinx_send_internal(session, CYRINX_FRAME_ACK, session->next_tx_seq++, h.seq, 0u,
                                      ack_payload, CYRINX_ACK_REPORT_PAYLOAD_BYTES, false);
        cyrinx_apply_arc(session, 0u);
        return rc;
    }

    cyrinx_apply_arc(session, 0u);
    return CYRINX_OK;
}

int cyrinx_link_in_memory(cyrinx_session_t *a, cyrinx_session_t *b) {
    if (!a || !b || a == b) {
        return CYRINX_ERR_INVALID_ARGUMENT;
    }
    a->linked_peer = b;
    b->linked_peer = a;
    return CYRINX_OK;
}

void cyrinx_close(cyrinx_session_t *session) {
    if (!session) {
        return;
    }

    cyrinx_message_node_t *node = session->rx_head;
    while (node) {
        cyrinx_message_node_t *next = node->next;
        free(node->data);
        free(node);
        node = next;
    }

    if (session->linked_peer && session->linked_peer->linked_peer == session) {
        session->linked_peer->linked_peer = NULL;
    }

    free(session);
}
