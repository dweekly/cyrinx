/* Cyrinx profile registry. See cyrinx/cyrinx_profiles.h. */
#include "cyrinx/cyrinx_profiles.h"
#include "cyrinx/cyrinx_bulk.h"
#include <string.h>

static const char *code_rate_to_str(cyrinx_code_rate_t cr) {
    switch (cr) {
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

static int modulation_to_bits(cyrinx_modulation_t mod) {
    switch (mod) {
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

static uint64_t cyrinx_fnv1a_64(const uint8_t *data, size_t len) {
    uint64_t hash = 14695981039346656037ULL;
    for (size_t i = 0; i < len; ++i) {
        hash ^= data[i];
        hash *= 1099511628211ULL;
    }
    return hash;
}

static void serialize_profile_params(const cyrinx_profile_t *profile, uint8_t *buf) {
    size_t offset = 0;

    memcpy(buf + offset, &profile->low_frequency_hz, 8);
    offset += 8;
    memcpy(buf + offset, &profile->high_frequency_hz, 8);
    offset += 8;

    uint32_t fft_size = profile->fft_size;
    memcpy(buf + offset, &fft_size, 4);
    offset += 4;

    uint32_t cyclic_prefix = profile->cyclic_prefix;
    memcpy(buf + offset, &cyclic_prefix, 4);
    offset += 4;

    uint32_t sample_rate = profile->sample_rate;
    memcpy(buf + offset, &sample_rate, 4);
    offset += 4;

    uint32_t pilot_every = profile->pilot_every;
    memcpy(buf + offset, &pilot_every, 4);
    offset += 4;

    uint32_t symbol_count = profile->symbol_count;
    memcpy(buf + offset, &symbol_count, 4);
    offset += 4;

    uint32_t modulation = profile->modulation;
    memcpy(buf + offset, &modulation, 4);
    offset += 4;

    uint32_t code_rate = profile->code_rate;
    memcpy(buf + offset, &code_rate, 4);
    offset += 4;

    memcpy(buf + offset, &profile->amplitude, 8);
    offset += 8;
    memcpy(buf + offset, &profile->clip_sigma, 8);
    offset += 8;
    memcpy(buf + offset, &profile->chirp_f0, 8);
    offset += 8;
    memcpy(buf + offset, &profile->chirp_f1, 8);
    offset += 8;
}

int cyrinx_profile_compute_hash(const cyrinx_profile_t *profile, uint8_t *out_hash) {
    if (profile == NULL || out_hash == NULL) {
        return CYRINX_ERR_INVALID_ARGUMENT;
    }
    uint8_t buf[76];
    serialize_profile_params(profile, buf);
    uint64_t hash_val = cyrinx_fnv1a_64(buf, sizeof(buf));

    memset(out_hash, 0, 32);
    out_hash[0] = (uint8_t)((hash_val >> 56) & 0xFF);
    out_hash[1] = (uint8_t)((hash_val >> 48) & 0xFF);
    out_hash[2] = (uint8_t)((hash_val >> 40) & 0xFF);
    out_hash[3] = (uint8_t)((hash_val >> 32) & 0xFF);
    out_hash[4] = (uint8_t)((hash_val >> 24) & 0xFF);
    out_hash[5] = (uint8_t)((hash_val >> 16) & 0xFF);
    out_hash[6] = (uint8_t)((hash_val >> 8) & 0xFF);
    out_hash[7] = (uint8_t)(hash_val & 0xFF);

    return CYRINX_OK;
}

static const cyrinx_profile_t g_profiles[] = {
    /* 1. Stable Cyrinx 1.x Control Profile */
    {.struct_size = sizeof(cyrinx_profile_t),
     .abi_version = CYRINX_PROFILE_ABI_VERSION,
     .id = 1,
     .classification = CYRINX_CLASSIFICATION_COMPATIBILITY,
     .modulation = CYRINX_MODULATION_16QAM,
     .code_rate = CYRINX_CODE_RATE_3_4,
     .low_frequency_hz = 1100.0,
     .high_frequency_hz = 23000.0,
     .fft_size = 2048,
     .cyclic_prefix = 768,
     .sample_rate = 48000,
     .pilot_every = 8,
     .symbol_count = 64,
     .amplitude = 0.5,
     .clip_sigma = 3.3,
     .chirp_f0 = 2000.0,
     .chirp_f1 = 16000.0},
    /* 2. Moto G 2026 Near-Field High-Goodput Profile */
    {.struct_size = sizeof(cyrinx_profile_t),
     .abi_version = CYRINX_PROFILE_ABI_VERSION,
     .id = 2,
     .classification = CYRINX_CLASSIFICATION_QUALIFIED,
     .modulation = CYRINX_MODULATION_16QAM,
     .code_rate = CYRINX_CODE_RATE_3_4,
     .low_frequency_hz = 1100.0,
     .high_frequency_hz = 23000.0,
     .fft_size = 2048,
     .cyclic_prefix = 240,
     .sample_rate = 48000,
     .pilot_every = 8,
     .symbol_count = 64,
     .amplitude = 0.13,
     .clip_sigma = 3.3,
     .chirp_f0 = 2000.0,
     .chirp_f1 = 16000.0},
    /* 3. Pixel 7a Near-Field Flagship Profile */
    {.struct_size = sizeof(cyrinx_profile_t),
     .abi_version = CYRINX_PROFILE_ABI_VERSION,
     .id = 3,
     .classification = CYRINX_CLASSIFICATION_QUALIFIED,
     .modulation = CYRINX_MODULATION_64QAM,
     .code_rate = CYRINX_CODE_RATE_2_3,
     .low_frequency_hz = 1100.0,
     .high_frequency_hz = 23000.0,
     .fft_size = 2048,
     .cyclic_prefix = 96,
     .sample_rate = 48000,
     .pilot_every = 16,
     .symbol_count = 64,
     .amplitude = 0.18,
     .clip_sigma = 3.3,
     .chirp_f0 = 2000.0,
     .chirp_f1 = 16000.0},
    /* 4. Pixel 7a Near-Field Peak-Goodput Research Profile */
    {.struct_size = sizeof(cyrinx_profile_t),
     .abi_version = CYRINX_PROFILE_ABI_VERSION,
     .id = 4,
     .classification = CYRINX_CLASSIFICATION_EXPERIMENTAL,
     .modulation = CYRINX_MODULATION_64QAM,
     .code_rate = CYRINX_CODE_RATE_2_3,
     .low_frequency_hz = 1100.0,
     .high_frequency_hz = 23000.0,
     .fft_size = 2048,
     .cyclic_prefix = 96,
     .sample_rate = 48000,
     .pilot_every = 64,
     .symbol_count = 96,
     .amplitude = 0.18,
     .clip_sigma = 3.3,
     .chirp_f0 = 2000.0,
     .chirp_f1 = 16000.0},
    /* 5. Cyrinx 2.x Experimental Profile */
    {.struct_size = sizeof(cyrinx_profile_t),
     .abi_version = CYRINX_PROFILE_ABI_VERSION,
     .id = 5,
     .classification = CYRINX_CLASSIFICATION_EXPERIMENTAL,
     .modulation = CYRINX_MODULATION_64QAM,
     .code_rate = CYRINX_CODE_RATE_5_6,
     .low_frequency_hz = 1100.0,
     .high_frequency_hz = 23000.0,
     .fft_size = 2048,
     .cyclic_prefix = 96,
     .sample_rate = 48000,
     .pilot_every = 16,
     .symbol_count = 64,
     .amplitude = 0.13,
     .clip_sigma = 3.3,
     .chirp_f0 = 2000.0,
     .chirp_f1 = 16000.0}};

size_t cyrinx_profile_get_count(void) {
    return sizeof(g_profiles) / sizeof(g_profiles[0]);
}

int cyrinx_profile_get_by_index(size_t index, cyrinx_profile_t *out_profile) {
    if (out_profile == NULL) {
        return CYRINX_ERR_INVALID_ARGUMENT;
    }
    size_t count = sizeof(g_profiles) / sizeof(g_profiles[0]);
    if (index >= count) {
        return CYRINX_ERR_INVALID_ARGUMENT;
    }

    cyrinx_profile_t p = g_profiles[index];

    /* Calculate block count using geometry engine */
    cyrinx_bulk_config cfg = {0};
    cfg.f_lo = p.low_frequency_hz;
    cfg.f_hi = p.high_frequency_hz;
    cfg.pilot_every = (int)p.pilot_every;
    cfg.bits_per_bin = modulation_to_bits((cyrinx_modulation_t)p.modulation);
    cfg.rate = code_rate_to_str((cyrinx_code_rate_t)p.code_rate);
    cfg.n_sym = (int)p.symbol_count;
    cfg.nfft = (int)p.fft_size;
    cfg.cp = (int)p.cyclic_prefix;
    cfg.sr = (int)p.sample_rate;
    cfg.amp = p.amplitude;
    cfg.clip_sigma = p.clip_sigma;
    cfg.chirp_f0 = p.chirp_f0;
    cfg.chirp_f1 = p.chirp_f1;

    cyrinx_bulk_geometry geom;
    if (cyrinx_bulk_compute_geometry(&cfg, &geom) == 0) {
        p.block_count = (uint32_t)geom.n_blocks;
    } else {
        p.block_count = 0;
    }

    /* Compute canonical content hash */
    cyrinx_profile_compute_hash(&p, p.hash);

    *out_profile = p;
    return CYRINX_OK;
}

int cyrinx_profile_get_by_id(uint32_t id, cyrinx_profile_t *out_profile) {
    if (out_profile == NULL) {
        return CYRINX_ERR_INVALID_ARGUMENT;
    }
    size_t count = sizeof(g_profiles) / sizeof(g_profiles[0]);
    for (size_t i = 0; i < count; ++i) {
        if (g_profiles[i].id == id) {
            return cyrinx_profile_get_by_index(i, out_profile);
        }
    }
    return CYRINX_ERR_INVALID_ARGUMENT;
}

int cyrinx_profile_validate(const cyrinx_profile_t *profile) {
    if (profile == NULL) {
        return CYRINX_ERR_INVALID_ARGUMENT;
    }

    if (!CYRINX_VALIDATE_ABI(profile, cyrinx_profile_t, CYRINX_PROFILE_ABI_VERSION)) {
        return CYRINX_ERR_INVALID_ARGUMENT;
    }

    if (profile->modulation != CYRINX_MODULATION_BPSK && profile->modulation != CYRINX_MODULATION_QPSK &&
        profile->modulation != CYRINX_MODULATION_16QAM && profile->modulation != CYRINX_MODULATION_64QAM &&
        profile->modulation != CYRINX_MODULATION_256QAM) {
        return CYRINX_ERR_INVALID_ARGUMENT;
    }

    if (profile->code_rate != CYRINX_CODE_RATE_1_2 && profile->code_rate != CYRINX_CODE_RATE_2_3 &&
        profile->code_rate != CYRINX_CODE_RATE_3_4 && profile->code_rate != CYRINX_CODE_RATE_5_6) {
        return CYRINX_ERR_INVALID_ARGUMENT;
    }

    if (profile->classification != CYRINX_CLASSIFICATION_COMPATIBILITY &&
        profile->classification != CYRINX_CLASSIFICATION_QUALIFIED &&
        profile->classification != CYRINX_CLASSIFICATION_EXPERIMENTAL &&
        profile->classification != CYRINX_CLASSIFICATION_INTERNAL_TEST_FIXTURE) {
        return CYRINX_ERR_INVALID_ARGUMENT;
    }

    if (profile->low_frequency_hz <= 0.0 || profile->high_frequency_hz <= profile->low_frequency_hz ||
        profile->sample_rate == 0 || profile->fft_size == 0 || profile->fft_size % 2 != 0 ||
        profile->cyclic_prefix > profile->fft_size || profile->pilot_every == 0 ||
        profile->symbol_count == 0 || profile->amplitude <= 0.0 || profile->amplitude > 1.0 ||
        profile->clip_sigma <= 0.0) {
        return CYRINX_ERR_INVALID_ARGUMENT;
    }

    cyrinx_bulk_config cfg = {0};
    cfg.f_lo = profile->low_frequency_hz;
    cfg.f_hi = profile->high_frequency_hz;
    cfg.pilot_every = (int)profile->pilot_every;
    cfg.bits_per_bin = modulation_to_bits((cyrinx_modulation_t)profile->modulation);
    cfg.rate = code_rate_to_str((cyrinx_code_rate_t)profile->code_rate);
    cfg.n_sym = (int)profile->symbol_count;
    cfg.nfft = (int)profile->fft_size;
    cfg.cp = (int)profile->cyclic_prefix;
    cfg.sr = (int)profile->sample_rate;
    cfg.amp = profile->amplitude;
    cfg.clip_sigma = profile->clip_sigma;
    cfg.chirp_f0 = profile->chirp_f0;
    cfg.chirp_f1 = profile->chirp_f1;

    cyrinx_bulk_geometry geom;
    if (cyrinx_bulk_compute_geometry(&cfg, &geom) != 0) {
        return CYRINX_ERR_INVALID_ARGUMENT;
    }

    if (profile->block_count != (uint32_t)geom.n_blocks) {
        return CYRINX_ERR_INVALID_ARGUMENT;
    }

    uint8_t computed_hash[32];
    int status = cyrinx_profile_compute_hash(profile, computed_hash);
    if (status != CYRINX_OK) {
        return status;
    }

    if (memcmp(profile->hash, computed_hash, 32) != 0) {
        return CYRINX_ERR_INVALID_ARGUMENT;
    }

    return CYRINX_OK;
}
