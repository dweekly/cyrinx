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

#endif /* CYRINX_PROFILES_INTERNAL_H */
