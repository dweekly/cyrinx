#ifndef CYRINX_BASE_H
#define CYRINX_BASE_H

#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/*
 * ============================================================================
 * Base Types & Return Status Codes
 * ============================================================================
 */

/* Monotonic timestamp in microseconds since an epoch. */
typedef uint64_t cyrinx_timestamp_us_t;

/* Core status codes returned by the C API. */
typedef enum {
    /* Operation completed successfully. */
    CYRINX_OK = 0,
    /* One or more input arguments were invalid. */
    CYRINX_ERR_INVALID_ARGUMENT = -1,
    /* Session was not started before calling a runtime API. */
    CYRINX_ERR_NOT_RUNNING = -2,
    /* Caller-provided output buffer is too small for the requested data. */
    CYRINX_ERR_BUFFER_TOO_SMALL = -3,
    /* Operation timed out (for example, reliable send ACK timeout). */
    CYRINX_ERR_TIMEOUT = -4,
    /* Frame failed integrity checks (header CRC or payload CRC). */
    CYRINX_ERR_CRC = -5,
    /* Session is busy and cannot accept this request yet. */
    CYRINX_ERR_BUSY = -6,
    /* Feature or mode is not supported by this build/backend. */
    CYRINX_ERR_UNSUPPORTED = -7,
    /* API was called in the wrong state for the current operation. */
    CYRINX_ERR_STATE = -8,
    /* Unexpected internal failure. */
    CYRINX_ERR_INTERNAL = -9
} cyrinx_status_t;

/*
 * ============================================================================
 * Allocator Abstractions
 * ============================================================================
 */

/*
 * Opaque memory allocator definition matching standard malloc/realloc/free
 * signatures but with a user-supplied context pointer.
 */
typedef struct cyrinx_allocator {
    void *(*malloc)(size_t size, void *context);
    void *(*realloc)(void *ptr, size_t size, void *context);
    void (*free)(void *ptr, void *context);
    void *context;
} cyrinx_allocator_t;

/*
 * ============================================================================
 * Clock Abstractions
 * ============================================================================
 */

/* Callback function type to query the monotonic clock in microseconds. */
typedef cyrinx_timestamp_us_t (*cyrinx_clock_callback_t)(void *context);

/*
 * Monotonic clock wrapper containing a read callback and user context.
 */
typedef struct cyrinx_clock {
    cyrinx_clock_callback_t read;
    void *context;
} cyrinx_clock_t;

/*
 * ============================================================================
 * Buffer Views
 * ============================================================================
 */

/* Non-const memory buffer view. */
typedef struct cyrinx_buf {
    uint8_t *data;
    size_t size;
} cyrinx_buf_t;

/* Const memory buffer view. */
typedef struct cyrinx_const_buf {
    const uint8_t *data;
    size_t size;
} cyrinx_const_buf_t;

/*
 * ============================================================================
 * Versioned C ABI Layout Conventions (Cyrinx 3.0+)
 * ============================================================================
 */

/*
 * The standard header required at the start of all versioned C structs
 * introduced in Cyrinx 3.0+.
 */
typedef struct cyrinx_abi_header {
    size_t struct_size;
    uint32_t abi_version;
} cyrinx_abi_header_t;

/**
 * Validates a versioned structure's size and ABI version.
 *
 * This function accepts a pointer to a struct that starts with size_t and uint32_t.
 * It returns true if the struct's size is greater than or equal to the minimum expected
 * size for the target struct type, and its ABI version matches the expected ABI version exactly.
 *
 * @param struct_ptr Pointer to the structure to validate.
 * @param min_expected_size The sizeof the expected struct type definition.
 * @param expected_abi The expected ABI version constant.
 * @return true if valid, false otherwise.
 */
static inline bool cyrinx_validate_abi(const void *struct_ptr, size_t min_expected_size,
                                       uint32_t expected_abi) {
    if (struct_ptr == NULL) {
        return false;
    }
    const cyrinx_abi_header_t *hdr = (const cyrinx_abi_header_t *)struct_ptr;
    return (hdr->struct_size >= min_expected_size && hdr->abi_version == expected_abi);
}

/**
 * Macro wrapper for validating ABI version and structure size.
 *
 * Usage:
 *   if (!CYRINX_VALIDATE_ABI(config, cyrinx_some_config_t, CYRINX_SOME_ABI_VERSION)) {
 *       return CYRINX_ERR_INVALID_ARGUMENT;
 *   }
 */
#define CYRINX_VALIDATE_ABI(struct_ptr, struct_type, expected_abi)                                           \
    cyrinx_validate_abi((struct_ptr), sizeof(struct_type), (expected_abi))

/*
 * ============================================================================
 * Object Lifecycle Management Conventions & Thread Affinity
 * ============================================================================
 *
 * CCyrinx 3.0+ components must implement standard lifecycle patterns and suffixes:
 *
 * 1. create: Allocates and initializes resources.
 *    Signature: cyrinx_status_t cyrinx_<module>_create(const cyrinx_<module>_config_t *config,
 *                                                      cyrinx_<module>_t **out_instance);
 *
 * 2. reset: Resets state without freeing/reallocating memory.
 *    Signature: cyrinx_status_t cyrinx_<module>_reset(cyrinx_<module>_t *instance);
 *
 * 3. process: Runs processing/transform loops.
 *    Signature: cyrinx_status_t cyrinx_<module>_process(cyrinx_<module>_t *instance,
 *                                                       const cyrinx_const_buf_t *input,
 *                                                       cyrinx_buf_t *output);
 *
 * 4. snapshot: Retrieves telemetry or current state configuration.
 *    Signature: cyrinx_status_t cyrinx_<module>_snapshot(const cyrinx_<module>_t *instance,
 *                                                        cyrinx_<module>_metrics_t *out_metrics);
 *
 * 5. destroy: Releases all resources and deallocates memory.
 *    Signature: void cyrinx_<module>_destroy(cyrinx_<module>_t *instance);
 *
 * Thread Affinity Definitions:
 * - CYRINX_THREAD_AFFINITY_CONTROL: For slow-path/allocating actions (e.g. create, destroy).
 *   Must not be run inside high-priority real-time audio threads.
 * - CYRINX_THREAD_AFFINITY_REALTIME: For fast-path/lock-free operations (e.g. process, reset).
 *   Safe for execution in real-time streaming pipelines.
 *
 * Concurrency Model:
 * - Thread-compatible, but NOT thread-safe: Individual object instances cannot be invoked
 *   simultaneously from multiple threads without external synchronization.
 */

#define CYRINX_THREAD_AFFINITY_CONTROL
#define CYRINX_THREAD_AFFINITY_REALTIME

/*
 * ============================================================================
 * Test Support Structures & Definitions
 * ============================================================================
 */

/* Dummy versioned struct for validating layout and ABI checks in unit tests. */
typedef struct cyrinx_test_versioned_struct {
    size_t struct_size;
    uint32_t abi_version;
    int data;
} cyrinx_test_versioned_struct_t;

#define CYRINX_TEST_ABI_VERSION 0x01020304

#ifdef __cplusplus
}
#endif

#endif /* CYRINX_BASE_H */
