/* Cyrinx 3.0 profile registry and identity. See cyrinx/cyrinx_profiles.h. */
#include "cyrinx/cyrinx_profiles.h"

#include <limits.h>
#include <math.h>
#include <string.h>

#include "cyrinx_bulk_internal.h"
#include "cyrinx_profiles_internal.h"

/* Freeze the v1 layout: any drift in size or field offsets is a build error
 * on every architecture this library compiles for. */
_Static_assert(sizeof(cyrinx_profile_t) == CYRINX_PROFILE_V1_SIZE, "profile v1 size");
_Static_assert(offsetof(cyrinx_profile_t, struct_size) == 0, "profile v1 layout");
_Static_assert(offsetof(cyrinx_profile_t, abi_version) == 4, "profile v1 layout");
_Static_assert(offsetof(cyrinx_profile_t, id) == 8, "profile v1 layout");
_Static_assert(offsetof(cyrinx_profile_t, classification) == 12, "profile v1 layout");
_Static_assert(offsetof(cyrinx_profile_t, modulation) == 16, "profile v1 layout");
_Static_assert(offsetof(cyrinx_profile_t, code_rate) == 20, "profile v1 layout");
_Static_assert(offsetof(cyrinx_profile_t, low_frequency_hz) == 24, "profile v1 layout");
_Static_assert(offsetof(cyrinx_profile_t, high_frequency_hz) == 32, "profile v1 layout");
_Static_assert(offsetof(cyrinx_profile_t, fft_size) == 40, "profile v1 layout");
_Static_assert(offsetof(cyrinx_profile_t, cyclic_prefix) == 44, "profile v1 layout");
_Static_assert(offsetof(cyrinx_profile_t, sample_rate) == 48, "profile v1 layout");
_Static_assert(offsetof(cyrinx_profile_t, pilot_every) == 52, "profile v1 layout");
_Static_assert(offsetof(cyrinx_profile_t, symbol_count) == 56, "profile v1 layout");
_Static_assert(offsetof(cyrinx_profile_t, block_count) == 60, "profile v1 layout");
_Static_assert(offsetof(cyrinx_profile_t, amplitude) == 64, "profile v1 layout");
_Static_assert(offsetof(cyrinx_profile_t, clip_sigma) == 72, "profile v1 layout");
_Static_assert(offsetof(cyrinx_profile_t, chirp_f0) == 80, "profile v1 layout");
_Static_assert(offsetof(cyrinx_profile_t, chirp_f1) == 88, "profile v1 layout");
_Static_assert(offsetof(cyrinx_profile_t, identity_sha256) == 96, "profile v1 layout");
_Static_assert(sizeof(cyrinx_abi_header_t) == CYRINX_ABI_HEADER_SIZE, "abi header size");

/* Freeze the v2 layout: the v1 fields at their v1 offsets, then the two new
 * fields. Internal code copies the first CYRINX_PROFILE_V1_SIZE bytes of a v2
 * structure into a v1 one, which these assertions make sound. */
#define CYRINX_V2_SAME_OFFSET(field)                                                                         \
    _Static_assert(offsetof(cyrinx_profile_v2_t, field) == offsetof(cyrinx_profile_t, field),                \
                   "profile v2 keeps the v1 offset of " #field)
_Static_assert(sizeof(cyrinx_profile_v2_t) == CYRINX_PROFILE_V2_SIZE, "profile v2 size");
CYRINX_V2_SAME_OFFSET(struct_size);
CYRINX_V2_SAME_OFFSET(abi_version);
CYRINX_V2_SAME_OFFSET(id);
CYRINX_V2_SAME_OFFSET(classification);
CYRINX_V2_SAME_OFFSET(modulation);
CYRINX_V2_SAME_OFFSET(code_rate);
CYRINX_V2_SAME_OFFSET(low_frequency_hz);
CYRINX_V2_SAME_OFFSET(high_frequency_hz);
CYRINX_V2_SAME_OFFSET(fft_size);
CYRINX_V2_SAME_OFFSET(cyclic_prefix);
CYRINX_V2_SAME_OFFSET(sample_rate);
CYRINX_V2_SAME_OFFSET(pilot_every);
CYRINX_V2_SAME_OFFSET(symbol_count);
CYRINX_V2_SAME_OFFSET(block_count);
CYRINX_V2_SAME_OFFSET(amplitude);
CYRINX_V2_SAME_OFFSET(clip_sigma);
CYRINX_V2_SAME_OFFSET(chirp_f0);
CYRINX_V2_SAME_OFFSET(chirp_f1);
CYRINX_V2_SAME_OFFSET(identity_sha256);
_Static_assert(offsetof(cyrinx_profile_v2_t, payload_scrambler) == 128, "profile v2 layout");
_Static_assert(offsetof(cyrinx_profile_v2_t, reserved0) == 132, "profile v2 layout");

const char *cyrinx_internal_code_rate_string(cyrinx_code_rate_t code_rate) {
    switch (code_rate) {
    case CYRINX_CODE_RATE_1_2:
        return "1/2";
    case CYRINX_CODE_RATE_2_3:
        return "2/3";
    case CYRINX_CODE_RATE_3_4:
        return "3/4";
    case CYRINX_CODE_RATE_5_6:
        return "5/6";
    default:
        return NULL;
    }
}

int cyrinx_internal_modulation_bits(cyrinx_modulation_t modulation) {
    switch (modulation) {
    case CYRINX_MODULATION_BPSK:
        return 1;
    case CYRINX_MODULATION_QPSK:
        return 2;
    case CYRINX_MODULATION_16QAM:
        return 4;
    case CYRINX_MODULATION_64QAM:
        return 6;
    case CYRINX_MODULATION_256QAM:
        return 8;
    default:
        return -1;
    }
}

cyrinx_abi_status_t cyrinx_internal_profile_to_bulk_config(const cyrinx_profile_t *profile,
                                                           cyrinx_bulk_config *out_config) {
    const char *rate = cyrinx_internal_code_rate_string((cyrinx_code_rate_t)profile->code_rate);
    int bits = cyrinx_internal_modulation_bits((cyrinx_modulation_t)profile->modulation);
    if (rate == NULL || bits < 0) {
        return CYRINX_STATUS_ERR_INVALID_ARGUMENT;
    }
    if (profile->symbol_count > (uint32_t)INT_MAX || profile->fft_size > (uint32_t)INT_MAX ||
        profile->cyclic_prefix > (uint32_t)INT_MAX || profile->sample_rate > (uint32_t)INT_MAX ||
        profile->pilot_every > (uint32_t)INT_MAX) {
        return CYRINX_STATUS_ERR_INVALID_ARGUMENT;
    }
    memset(out_config, 0, sizeof(*out_config));
    out_config->f_lo = profile->low_frequency_hz;
    out_config->f_hi = profile->high_frequency_hz;
    out_config->pilot_every = (int)profile->pilot_every;
    out_config->bits_per_bin = bits;
    out_config->rate = rate;
    out_config->n_sym = (int)profile->symbol_count;
    out_config->nfft = (int)profile->fft_size;
    out_config->cp = (int)profile->cyclic_prefix;
    out_config->sr = (int)profile->sample_rate;
    out_config->amp = profile->amplitude;
    out_config->clip_sigma = profile->clip_sigma;
    out_config->chirp_f0 = profile->chirp_f0;
    out_config->chirp_f1 = profile->chirp_f1;
    return CYRINX_STATUS_OK;
}

/* ---------------- Canonical identity serialization ---------------- */

static void write_u32_be(uint8_t *buf, uint32_t value) {
    buf[0] = (uint8_t)(value >> 24);
    buf[1] = (uint8_t)(value >> 16);
    buf[2] = (uint8_t)(value >> 8);
    buf[3] = (uint8_t)value;
}

static void write_double_be(uint8_t *buf, double value) {
    union {
        double d;
        uint64_t u;
    } bits;
    bits.d = value;
    for (int i = 0; i < 8; ++i) {
        buf[i] = (uint8_t)(bits.u >> (56 - 8 * i));
    }
}

static void write_identity_fields(const cyrinx_profile_t *profile, const char *domain, uint8_t *out);

static bool identity_doubles_finite(const cyrinx_profile_t *profile) {
    return isfinite(profile->low_frequency_hz) && isfinite(profile->high_frequency_hz) &&
           isfinite(profile->amplitude) && isfinite(profile->clip_sigma) && isfinite(profile->chirp_f0) &&
           isfinite(profile->chirp_f1);
}

cyrinx_abi_status_t cyrinx_profile_serialize_identity_v1(const cyrinx_profile_t *profile, uint8_t *out,
                                                         size_t out_cap) {
    if (profile == NULL || out == NULL) {
        return CYRINX_STATUS_ERR_INVALID_ARGUMENT;
    }
    /* A v2 structure reached through this pointer type: its identity is not
     * the v1 one whenever it is scrambled, so it is never serialized here. */
    if (profile->abi_version == CYRINX_PROFILE_V2_ABI_VERSION) {
        return CYRINX_STATUS_ERR_INVALID_ARGUMENT;
    }
    if (out_cap < CYRINX_PROFILE_IDENTITY_V1_SERIALIZED_SIZE) {
        return CYRINX_STATUS_ERR_BUFFER_TOO_SMALL;
    }
    if (!identity_doubles_finite(profile)) {
        return CYRINX_STATUS_ERR_INVALID_ARGUMENT;
    }
    write_identity_fields(profile, CYRINX_PROFILE_IDENTITY_V1_DOMAIN, out);
    return CYRINX_STATUS_OK;
}

/* The 26-byte domain, then the 76 bytes of identity fields. */
static void write_identity_fields(const cyrinx_profile_t *profile, const char *domain, uint8_t *out) {
    size_t offset = 0;
    memcpy(out, domain, 26);
    offset += 26;
    write_double_be(out + offset, profile->low_frequency_hz);
    offset += 8;
    write_double_be(out + offset, profile->high_frequency_hz);
    offset += 8;
    write_u32_be(out + offset, profile->fft_size);
    offset += 4;
    write_u32_be(out + offset, profile->cyclic_prefix);
    offset += 4;
    write_u32_be(out + offset, profile->sample_rate);
    offset += 4;
    write_u32_be(out + offset, profile->pilot_every);
    offset += 4;
    write_u32_be(out + offset, profile->symbol_count);
    offset += 4;
    write_u32_be(out + offset, profile->modulation);
    offset += 4;
    write_u32_be(out + offset, profile->code_rate);
    offset += 4;
    write_double_be(out + offset, profile->amplitude);
    offset += 8;
    write_double_be(out + offset, profile->clip_sigma);
    offset += 8;
    write_double_be(out + offset, profile->chirp_f0);
    offset += 8;
    write_double_be(out + offset, profile->chirp_f1);
    offset += 8;
    /* offset == CYRINX_PROFILE_IDENTITY_V1_SERIALIZED_SIZE, pinned by tests. */
    (void)offset;
}

cyrinx_abi_status_t cyrinx_profile_compute_identity(const cyrinx_profile_t *profile, uint8_t *out_digest32) {
    if (profile == NULL || out_digest32 == NULL) {
        return CYRINX_STATUS_ERR_INVALID_ARGUMENT;
    }
    uint8_t serialized[CYRINX_PROFILE_IDENTITY_V1_SERIALIZED_SIZE];
    cyrinx_abi_status_t status =
        cyrinx_profile_serialize_identity_v1(profile, serialized, sizeof(serialized));
    if (status != CYRINX_STATUS_OK) {
        return status;
    }
    cyrinx_sha256(serialized, sizeof(serialized), out_digest32);
    return CYRINX_STATUS_OK;
}

/* ---------------- v2 identity ---------------- */

void cyrinx_internal_profile_v1_view(const cyrinx_profile_v2_t *profile, cyrinx_profile_t *out_view) {
    memcpy(out_view, profile, CYRINX_PROFILE_V1_SIZE);
    out_view->struct_size = CYRINX_PROFILE_V1_SIZE;
    out_view->abi_version = CYRINX_PROFILE_ABI_VERSION;
}

cyrinx_abi_status_t cyrinx_profile_v2_serialize_identity(const cyrinx_profile_v2_t *profile, uint8_t *out,
                                                         size_t out_cap, size_t *out_len) {
    if (profile == NULL || out == NULL || out_len == NULL) {
        return CYRINX_STATUS_ERR_INVALID_ARGUMENT;
    }
    if (!cyrinx_abi_accepts(profile, CYRINX_PROFILE_V2_SIZE, CYRINX_PROFILE_V2_ABI_VERSION) ||
        !cyrinx_internal_payload_scrambler_supported(profile->payload_scrambler)) {
        return CYRINX_STATUS_ERR_INVALID_ARGUMENT;
    }
    bool scrambled = profile->payload_scrambler != CYRINX_PAYLOAD_SCRAMBLER_NONE;
    size_t needed =
        scrambled ? CYRINX_PROFILE_IDENTITY_V2_SERIALIZED_SIZE : CYRINX_PROFILE_IDENTITY_V1_SERIALIZED_SIZE;
    if (out_cap < needed) {
        return CYRINX_STATUS_ERR_BUFFER_TOO_SMALL;
    }
    cyrinx_profile_t view;
    cyrinx_internal_profile_v1_view(profile, &view);
    if (!identity_doubles_finite(&view)) {
        return CYRINX_STATUS_ERR_INVALID_ARGUMENT;
    }
    if (scrambled) {
        write_identity_fields(&view, CYRINX_PROFILE_IDENTITY_V2_DOMAIN, out);
        write_u32_be(out + CYRINX_PROFILE_IDENTITY_V1_SERIALIZED_SIZE, profile->payload_scrambler);
    } else {
        write_identity_fields(&view, CYRINX_PROFILE_IDENTITY_V1_DOMAIN, out);
    }
    *out_len = needed;
    return CYRINX_STATUS_OK;
}

cyrinx_abi_status_t cyrinx_profile_v2_compute_identity(const cyrinx_profile_v2_t *profile,
                                                       uint8_t *out_digest32) {
    if (profile == NULL || out_digest32 == NULL) {
        return CYRINX_STATUS_ERR_INVALID_ARGUMENT;
    }
    uint8_t serialized[CYRINX_PROFILE_IDENTITY_V2_SERIALIZED_SIZE];
    size_t length = 0;
    cyrinx_abi_status_t status =
        cyrinx_profile_v2_serialize_identity(profile, serialized, sizeof(serialized), &length);
    if (status != CYRINX_STATUS_OK) {
        return status;
    }
    cyrinx_sha256(serialized, length, out_digest32);
    return CYRINX_STATUS_OK;
}

/* ---------------- Registry ----------------
 * Parameter values mirror Tests/Fixtures/profiles/cyrinx_profiles_v1.json and
 * cyrinx_profiles_v2.json, the canonical registry sources;
 * the registry test suites prove the table agrees with both field-for-field
 * and digest-for-digest. block_count and identity_sha256 are derived at
 * lookup, never hand-maintained here.
 *
 * One table backs both views. The unscrambled rows come first and are the v1
 * view; the v2 view is the whole table. Each row's scrambler mode selects its
 * identity scheme. */

typedef struct {
    uint32_t id;
    uint32_t classification;
    uint32_t modulation;
    uint32_t code_rate;
    double low_frequency_hz;
    double high_frequency_hz;
    uint32_t fft_size;
    uint32_t cyclic_prefix;
    uint32_t sample_rate;
    uint32_t pilot_every;
    uint32_t symbol_count;
    double amplitude;
    double clip_sigma;
    double chirp_f0;
    double chirp_f1;
    uint32_t payload_scrambler;
} registry_row;

#define UNSCRAMBLED CYRINX_PAYLOAD_SCRAMBLER_NONE
#define SCRAMBLED CYRINX_PAYLOAD_SCRAMBLER_FRAMED_BITS_V1
#define CYRINX_REGISTRY_V1_COUNT 5u

static const registry_row g_registry[] = {
    /* 1. Cyrinx 1.x compatibility control geometry (CP768/p8/16-QAM r3/4). */
    {1, CYRINX_CLASSIFICATION_COMPATIBILITY, CYRINX_MODULATION_16QAM, CYRINX_CODE_RATE_3_4, 1100.0, 23000.0,
     2048, 768, 48000, 8, 64, 0.5, 3.3, 2000.0, 16000.0, UNSCRAMBLED},
    /* 2. Moto G 2026 near-field high-goodput route (16-QAM r3/4, CP 240). */
    {2, CYRINX_CLASSIFICATION_QUALIFIED, CYRINX_MODULATION_16QAM, CYRINX_CODE_RATE_3_4, 1100.0, 23000.0, 2048,
     240, 48000, 8, 64, 0.13, 3.3, 2000.0, 16000.0, UNSCRAMBLED},
    /* 3. Pixel 7a near-field flagship route (64-QAM r2/3, CP 96, pilots/16). */
    {3, CYRINX_CLASSIFICATION_QUALIFIED, CYRINX_MODULATION_64QAM, CYRINX_CODE_RATE_2_3, 1100.0, 23000.0, 2048,
     96, 48000, 16, 64, 0.18, 3.3, 2000.0, 16000.0, UNSCRAMBLED},
    /* 4. Pixel 7a peak-goodput research profile (pilots/64, 96 symbols). */
    {4, CYRINX_CLASSIFICATION_EXPERIMENTAL, CYRINX_MODULATION_64QAM, CYRINX_CODE_RATE_2_3, 1100.0, 23000.0,
     2048, 96, 48000, 64, 96, 0.18, 3.3, 2000.0, 16000.0, UNSCRAMBLED},
    /* 5. Cyrinx 2.x experimental rate-5/6 profile. */
    {5, CYRINX_CLASSIFICATION_EXPERIMENTAL, CYRINX_MODULATION_64QAM, CYRINX_CODE_RATE_5_6, 1100.0, 23000.0,
     2048, 96, 48000, 16, 64, 0.13, 3.3, 2000.0, 16000.0, UNSCRAMBLED},
    /* 6 to 10. Profiles 1 to 5 with the payload scrambler on. All experimental:
     * none has its own over-the-air qualification. */
    {6, CYRINX_CLASSIFICATION_EXPERIMENTAL, CYRINX_MODULATION_16QAM, CYRINX_CODE_RATE_3_4, 1100.0, 23000.0,
     2048, 768, 48000, 8, 64, 0.5, 3.3, 2000.0, 16000.0, SCRAMBLED},
    {7, CYRINX_CLASSIFICATION_EXPERIMENTAL, CYRINX_MODULATION_16QAM, CYRINX_CODE_RATE_3_4, 1100.0, 23000.0,
     2048, 240, 48000, 8, 64, 0.13, 3.3, 2000.0, 16000.0, SCRAMBLED},
    {8, CYRINX_CLASSIFICATION_EXPERIMENTAL, CYRINX_MODULATION_64QAM, CYRINX_CODE_RATE_2_3, 1100.0, 23000.0,
     2048, 96, 48000, 16, 64, 0.18, 3.3, 2000.0, 16000.0, SCRAMBLED},
    {9, CYRINX_CLASSIFICATION_EXPERIMENTAL, CYRINX_MODULATION_64QAM, CYRINX_CODE_RATE_2_3, 1100.0, 23000.0,
     2048, 96, 48000, 64, 96, 0.18, 3.3, 2000.0, 16000.0, SCRAMBLED},
    {10, CYRINX_CLASSIFICATION_EXPERIMENTAL, CYRINX_MODULATION_64QAM, CYRINX_CODE_RATE_5_6, 1100.0, 23000.0,
     2048, 96, 48000, 16, 64, 0.13, 3.3, 2000.0, 16000.0, SCRAMBLED}};

#define CYRINX_REGISTRY_COUNT (sizeof(g_registry) / sizeof(g_registry[0]))
_Static_assert(CYRINX_REGISTRY_COUNT == 2 * CYRINX_REGISTRY_V1_COUNT,
               "every v1 profile has a scrambled twin");

size_t cyrinx_profile_get_count(void) {
    return CYRINX_REGISTRY_V1_COUNT;
}

size_t cyrinx_profile_v2_get_count(void) {
    return CYRINX_REGISTRY_COUNT;
}

static cyrinx_abi_status_t materialize_row(const registry_row *row, cyrinx_profile_v2_t *out_full) {
    memset(out_full, 0, sizeof(*out_full));
    out_full->struct_size = CYRINX_PROFILE_V2_SIZE;
    out_full->abi_version = CYRINX_PROFILE_V2_ABI_VERSION;
    out_full->id = row->id;
    out_full->classification = row->classification;
    out_full->modulation = row->modulation;
    out_full->code_rate = row->code_rate;
    out_full->low_frequency_hz = row->low_frequency_hz;
    out_full->high_frequency_hz = row->high_frequency_hz;
    out_full->fft_size = row->fft_size;
    out_full->cyclic_prefix = row->cyclic_prefix;
    out_full->sample_rate = row->sample_rate;
    out_full->pilot_every = row->pilot_every;
    out_full->symbol_count = row->symbol_count;
    out_full->amplitude = row->amplitude;
    out_full->clip_sigma = row->clip_sigma;
    out_full->chirp_f0 = row->chirp_f0;
    out_full->chirp_f1 = row->chirp_f1;
    out_full->payload_scrambler = row->payload_scrambler;

    cyrinx_profile_t view;
    cyrinx_internal_profile_v1_view(out_full, &view);
    cyrinx_bulk_config cfg;
    cyrinx_abi_status_t status = cyrinx_internal_profile_to_bulk_config(&view, &cfg);
    if (status != CYRINX_STATUS_OK) {
        return status;
    }

    cyrinx_bulk_geometry geometry;
    if (cyrinx_bulk_compute_geometry(&cfg, &geometry) != 0 || geometry.n_blocks < 0) {
        return CYRINX_STATUS_ERR_INTERNAL;
    }
    out_full->block_count = (uint32_t)geometry.n_blocks;

    return cyrinx_profile_v2_compute_identity(out_full, out_full->identity_sha256);
}

/* Copy the version's bytes into the caller's accepted structure, keeping the
 * caller's own header and any bytes beyond the version's size. */
static void copy_to_caller(const void *full, size_t version_size, void *out_profile) {
    cyrinx_abi_header_t header = *(const cyrinx_abi_header_t *)out_profile;
    memcpy(out_profile, full, version_size);
    *(cyrinx_abi_header_t *)out_profile = header;
}

cyrinx_abi_status_t cyrinx_profile_get_by_index(size_t index, cyrinx_profile_t *out_profile) {
    if (out_profile == NULL) {
        return CYRINX_STATUS_ERR_INVALID_ARGUMENT;
    }
    if (!cyrinx_abi_accepts(out_profile, CYRINX_PROFILE_V1_SIZE, CYRINX_PROFILE_ABI_VERSION)) {
        return CYRINX_STATUS_ERR_INVALID_ARGUMENT;
    }
    if (index >= CYRINX_REGISTRY_V1_COUNT) {
        return CYRINX_STATUS_ERR_NOT_FOUND;
    }
    cyrinx_profile_v2_t full;
    cyrinx_abi_status_t status = materialize_row(&g_registry[index], &full);
    if (status != CYRINX_STATUS_OK) {
        return status;
    }
    copy_to_caller(&full, CYRINX_PROFILE_V1_SIZE, out_profile);
    return CYRINX_STATUS_OK;
}

cyrinx_abi_status_t cyrinx_profile_get_by_id(uint32_t id, cyrinx_profile_t *out_profile) {
    if (out_profile == NULL) {
        return CYRINX_STATUS_ERR_INVALID_ARGUMENT;
    }
    if (!cyrinx_abi_accepts(out_profile, CYRINX_PROFILE_V1_SIZE, CYRINX_PROFILE_ABI_VERSION)) {
        return CYRINX_STATUS_ERR_INVALID_ARGUMENT;
    }
    /* Only the v1 view: a scrambled ID is not found here. */
    for (size_t i = 0; i < CYRINX_REGISTRY_V1_COUNT; ++i) {
        if (g_registry[i].id == id) {
            return cyrinx_profile_get_by_index(i, out_profile);
        }
    }
    return CYRINX_STATUS_ERR_NOT_FOUND;
}

cyrinx_abi_status_t cyrinx_profile_v2_get_by_index(size_t index, cyrinx_profile_v2_t *out_profile) {
    if (out_profile == NULL) {
        return CYRINX_STATUS_ERR_INVALID_ARGUMENT;
    }
    if (!cyrinx_abi_accepts(out_profile, CYRINX_PROFILE_V2_SIZE, CYRINX_PROFILE_V2_ABI_VERSION)) {
        return CYRINX_STATUS_ERR_INVALID_ARGUMENT;
    }
    if (index >= CYRINX_REGISTRY_COUNT) {
        return CYRINX_STATUS_ERR_NOT_FOUND;
    }
    cyrinx_profile_v2_t full;
    cyrinx_abi_status_t status = materialize_row(&g_registry[index], &full);
    if (status != CYRINX_STATUS_OK) {
        return status;
    }
    copy_to_caller(&full, CYRINX_PROFILE_V2_SIZE, out_profile);
    return CYRINX_STATUS_OK;
}

cyrinx_abi_status_t cyrinx_profile_v2_get_by_id(uint32_t id, cyrinx_profile_v2_t *out_profile) {
    if (out_profile == NULL) {
        return CYRINX_STATUS_ERR_INVALID_ARGUMENT;
    }
    if (!cyrinx_abi_accepts(out_profile, CYRINX_PROFILE_V2_SIZE, CYRINX_PROFILE_V2_ABI_VERSION)) {
        return CYRINX_STATUS_ERR_INVALID_ARGUMENT;
    }
    for (size_t i = 0; i < CYRINX_REGISTRY_COUNT; ++i) {
        if (g_registry[i].id == id) {
            return cyrinx_profile_v2_get_by_index(i, out_profile);
        }
    }
    return CYRINX_STATUS_ERR_NOT_FOUND;
}

/* Everything validation checks except the ABI prefix and the identity digest,
 * which differ between the two versions. */
static cyrinx_abi_status_t validate_parameters(const cyrinx_profile_t *profile) {
    if (profile->classification != CYRINX_CLASSIFICATION_COMPATIBILITY &&
        profile->classification != CYRINX_CLASSIFICATION_QUALIFIED &&
        profile->classification != CYRINX_CLASSIFICATION_EXPERIMENTAL &&
        profile->classification != CYRINX_CLASSIFICATION_INTERNAL_TEST_FIXTURE) {
        return CYRINX_STATUS_ERR_INVALID_ARGUMENT;
    }

    if (!identity_doubles_finite(profile)) {
        return CYRINX_STATUS_ERR_INVALID_ARGUMENT;
    }
    if (profile->low_frequency_hz <= 0.0 || profile->high_frequency_hz <= profile->low_frequency_hz ||
        profile->sample_rate == 0 || profile->high_frequency_hz > (double)profile->sample_rate / 2.0 ||
        profile->fft_size == 0 || profile->fft_size % 2 != 0 || profile->cyclic_prefix > profile->fft_size ||
        profile->pilot_every == 0 || profile->symbol_count == 0 || profile->amplitude <= 0.0 ||
        profile->amplitude > 1.0 || profile->clip_sigma <= 0.0) {
        return CYRINX_STATUS_ERR_INVALID_ARGUMENT;
    }

    cyrinx_bulk_config cfg;
    cyrinx_abi_status_t status = cyrinx_internal_profile_to_bulk_config(profile, &cfg);
    if (status != CYRINX_STATUS_OK) {
        return status;
    }
    cyrinx_bulk_geometry geometry;
    if (cyrinx_bulk_compute_geometry(&cfg, &geometry) != 0) {
        return CYRINX_STATUS_ERR_INVALID_ARGUMENT;
    }
    if (geometry.n_blocks < 0 || profile->block_count != (uint32_t)geometry.n_blocks) {
        return CYRINX_STATUS_ERR_INVALID_ARGUMENT;
    }
    return CYRINX_STATUS_OK;
}

cyrinx_abi_status_t cyrinx_profile_validate(const cyrinx_profile_t *profile) {
    if (profile == NULL) {
        return CYRINX_STATUS_ERR_INVALID_ARGUMENT;
    }
    if (!cyrinx_abi_accepts(profile, CYRINX_PROFILE_V1_SIZE, CYRINX_PROFILE_ABI_VERSION)) {
        return CYRINX_STATUS_ERR_INVALID_ARGUMENT;
    }
    cyrinx_abi_status_t status = validate_parameters(profile);
    if (status != CYRINX_STATUS_OK) {
        return status;
    }

    uint8_t digest[32];
    status = cyrinx_profile_compute_identity(profile, digest);
    if (status != CYRINX_STATUS_OK) {
        return status;
    }
    if (memcmp(profile->identity_sha256, digest, sizeof(digest)) != 0) {
        return CYRINX_STATUS_ERR_INVALID_ARGUMENT;
    }
    return CYRINX_STATUS_OK;
}

cyrinx_abi_status_t cyrinx_profile_v2_validate(const cyrinx_profile_v2_t *profile) {
    if (profile == NULL) {
        return CYRINX_STATUS_ERR_INVALID_ARGUMENT;
    }
    if (!cyrinx_abi_accepts(profile, CYRINX_PROFILE_V2_SIZE, CYRINX_PROFILE_V2_ABI_VERSION)) {
        return CYRINX_STATUS_ERR_INVALID_ARGUMENT;
    }
    if (profile->reserved0 != 0 || !cyrinx_internal_payload_scrambler_supported(profile->payload_scrambler)) {
        return CYRINX_STATUS_ERR_INVALID_ARGUMENT;
    }
    cyrinx_profile_t view;
    cyrinx_internal_profile_v1_view(profile, &view);
    cyrinx_abi_status_t status = validate_parameters(&view);
    if (status != CYRINX_STATUS_OK) {
        return status;
    }

    uint8_t digest[32];
    status = cyrinx_profile_v2_compute_identity(profile, digest);
    if (status != CYRINX_STATUS_OK) {
        return status;
    }
    if (memcmp(profile->identity_sha256, digest, sizeof(digest)) != 0) {
        return CYRINX_STATUS_ERR_INVALID_ARGUMENT;
    }
    return CYRINX_STATUS_OK;
}
