/* Internal (non-installed) helpers shared by the profile registry and the
 * batch contract: profile-to-bulk-config mapping. Not part of the public
 * ABI surface. */
#ifndef CYRINX_PROFILES_INTERNAL_H
#define CYRINX_PROFILES_INTERNAL_H

#include "cyrinx/cyrinx_bulk.h"
#include "cyrinx/cyrinx_profiles.h"

/* NULL for an out-of-range enum value. */
const char *cyrinx_internal_code_rate_string(cyrinx_code_rate_t code_rate);

/* -1 for an out-of-range enum value. */
int cyrinx_internal_modulation_bits(cyrinx_modulation_t modulation);

/* Map a profile's physical parameters onto the 2.x bulk-PHY config. Returns
 * CYRINX_STATUS_ERR_INVALID_ARGUMENT when an enum value has no mapping or an
 * integer field exceeds the config's int range; the config is unspecified on
 * failure. */
cyrinx_abi_status_t cyrinx_internal_profile_to_bulk_config(const cyrinx_profile_t *profile,
                                                           cyrinx_bulk_config *out_config);

/* The v1 fields of a v2 profile as a v1 structure with a v1 header, for the
 * code that maps parameters onto the bulk PHY. The identity digest is copied
 * as it stands, so for a scrambled profile the view carries the v2 digest and
 * does not pass cyrinx_profile_validate. */
void cyrinx_internal_profile_v1_view(const cyrinx_profile_v2_t *profile, cyrinx_profile_t *out_view);

#endif /* CYRINX_PROFILES_INTERNAL_H */
