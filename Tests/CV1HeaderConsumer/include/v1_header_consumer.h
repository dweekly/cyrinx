/* Test support: what a consumer built against the pre-v2 profile header sees
 * when it runs against the present library. See consumer.c. */
#ifndef CYRINX_V1_HEADER_CONSUMER_H
#define CYRINX_V1_HEADER_CONSUMER_H

#include <stddef.h>
#include <stdint.h>

/* cyrinx_profile_get_count() as the old header declares it. */
size_t cyrinx_v1_header_consumer_count(void);

/* sizeof(cyrinx_profile_t) in the old header. */
uint32_t cyrinx_v1_header_consumer_profile_size(void);

/* Look up and validate the profile at `index` through the old declarations.
 * Returns the library's status; on success writes the ID and the 32-byte
 * identity digest. */
int32_t cyrinx_v1_header_consumer_profile_at(size_t index, uint32_t *out_id, uint8_t *out_identity32);

/* Status of a lookup by ID through the old declarations. */
int32_t cyrinx_v1_header_consumer_lookup(uint32_t id);

#endif /* CYRINX_V1_HEADER_CONSUMER_H */
