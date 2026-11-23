/* A consumer compiled against the profile header as it was before the v2 type
 * existed, linked against the present library.
 *
 * snapshot/cyrinx holds cyrinx_profiles.h and cyrinx_base.h exactly as they
 * were at commit 5704913, the last one before the scrambled profiles. This
 * file includes only those, so the structure size, the constants and the
 * declarations it uses are the old ones. It shows that code built before the
 * change still enumerates the five profiles it knew, with the same contents.
 */
#include "v1_header_consumer.h"

#include "snapshot/cyrinx/cyrinx_profiles.h"

#include <string.h>

size_t cyrinx_v1_header_consumer_count(void) {
    return cyrinx_profile_get_count();
}

uint32_t cyrinx_v1_header_consumer_profile_size(void) {
    return (uint32_t)sizeof(cyrinx_profile_t);
}

int32_t cyrinx_v1_header_consumer_profile_at(size_t index, uint32_t *out_id, uint8_t *out_identity32) {
    cyrinx_profile_t profile;
    memset(&profile, 0, sizeof(profile));
    profile.struct_size = CYRINX_PROFILE_V1_SIZE;
    profile.abi_version = CYRINX_PROFILE_ABI_VERSION;
    cyrinx_abi_status_t status = cyrinx_profile_get_by_index(index, &profile);
    if (status != CYRINX_STATUS_OK) {
        return status;
    }
    status = cyrinx_profile_validate(&profile);
    if (status != CYRINX_STATUS_OK) {
        return status;
    }
    *out_id = profile.id;
    memcpy(out_identity32, profile.identity_sha256, 32);
    return CYRINX_STATUS_OK;
}

int32_t cyrinx_v1_header_consumer_lookup(uint32_t id) {
    cyrinx_profile_t profile;
    memset(&profile, 0, sizeof(profile));
    profile.struct_size = CYRINX_PROFILE_V1_SIZE;
    profile.abi_version = CYRINX_PROFILE_ABI_VERSION;
    return cyrinx_profile_get_by_id(id, &profile);
}
