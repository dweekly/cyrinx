/* Minimal C-side loader for the bulk-PHY golden vectors (docs/PUBLICATION.md
 * PR 1.1). This is a TEST-SUPPORT target — it is not part of the shipping
 * Cyrinx/CCyrinx API. It proves the portable-C side can locate, size, and read
 * the golden artifacts described by the generated golden_manifest.h, so the C
 * DSP ports (PRs 1.2/1.3) have a working artifact loader to assert against.
 */
#ifndef CYRINX_GOLDEN_LOADER_H
#define CYRINX_GOLDEN_LOADER_H

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Read `dir`/`relpath` fully into a freshly malloc'd buffer. On success returns
 * 0, sets *out (caller frees via cyrinx_golden_free) and *len. Returns -1 on
 * any error (open/seek/read/alloc). */
int cyrinx_golden_read(const char *dir, const char *relpath,
                       unsigned char **out, size_t *len);

void cyrinx_golden_free(unsigned char *p);

/* Walk the generated manifest; for every artifact, check the file under `dir`
 * exists and is exactly the recorded byte length. Returns the number of
 * mismatches (0 = all good). *checked (if non-NULL) receives the number of
 * artifacts examined. */
int cyrinx_golden_verify_sizes(const char *dir, size_t *checked);

/* Manifest accessors (over the generated golden_manifest.h). */
size_t cyrinx_golden_case_count(void);
const char *cyrinx_golden_case_name(size_t i); /* NULL if out of range */
double cyrinx_golden_float_abs_tol(void);

#ifdef __cplusplus
}
#endif

#endif /* CYRINX_GOLDEN_LOADER_H */
