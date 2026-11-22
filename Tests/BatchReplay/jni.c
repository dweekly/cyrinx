#include "Profiles.h"
#include <jni.h>
#include <stdlib.h>

static jbyteArray copy_bytes(JNIEnv *env, const uint8_t *data, jsize count) {
    jbyteArray array = (*env)->NewByteArray(env, count);
    if (array)
        (*env)->SetByteArrayRegion(env, array, 0, count, (const jbyte *)data);
    return array;
}

static bool lookup_registered(jint id, cyrinx_profile_t *profile) {
    profile->struct_size = CYRINX_PROFILE_V1_SIZE;
    profile->abi_version = CYRINX_PROFILE_ABI_VERSION;
    return cyrinx_profile_get_by_id((uint32_t)id, profile) == CYRINX_STATUS_OK;
}

JNIEXPORT jintArray JNICALL Java_Replay_profileIDs(JNIEnv *env, jobject self) {
    (void)self;
    jsize count = (jsize)cyrinx_profile_get_count();
    jintArray ids = (*env)->NewIntArray(env, count);
    if (!ids)
        return NULL;
    for (jsize i = 0; i < count; i++) {
        cyrinx_profile_t profile = {.struct_size = CYRINX_PROFILE_V1_SIZE,
                                    .abi_version = CYRINX_PROFILE_ABI_VERSION};
        if (cyrinx_profile_get_by_index((size_t)i, &profile) != CYRINX_STATUS_OK) {
            (*env)->ThrowNew(env, (*env)->FindClass(env, "java/lang/IllegalStateException"),
                             "registry lookup");
            return NULL;
        }
        jint id = (jint)profile.id;
        (*env)->SetIntArrayRegion(env, ids, i, 1, &id);
        if ((*env)->ExceptionCheck(env))
            return NULL;
    }
    return ids;
}

JNIEXPORT jfloatArray JNICALL Java_Replay_encode(JNIEnv *env, jobject self, jint id, jbyteArray bytes) {
    (void)self;
    cyrinx_profile_t profile = {0};
    if (!bytes || !lookup_registered(id, &profile)) {
        (*env)->ThrowNew(env, (*env)->FindClass(env, "java/lang/IllegalArgumentException"),
                         "unknown profile or null payload");
        return NULL;
    }
    jsize count = (*env)->GetArrayLength(env, bytes);
    uint8_t byte = 0;
    float sample = 0;
    cyrinx_batch_result_t result = {.struct_size = sizeof(result),
                                    .abi_version = CYRINX_BATCH_RESULT_ABI_VERSION};
    if (cyrinx_batch_encode(&profile, &byte, 0, &sample, 0, &result) != CYRINX_STATUS_ERR_BUFFER_TOO_SMALL ||
        (uint64_t)count > result.payload_bytes_required || result.logical_frame_samples > INT32_MAX) {
        (*env)->ThrowNew(env, (*env)->FindClass(env, "java/lang/IllegalArgumentException"),
                         "invalid encode capacity");
        return NULL;
    }
    jsize frames = (jsize)result.logical_frame_samples;
    jfloatArray output = (*env)->NewFloatArray(env, frames);
    if (!output)
        return NULL;
    jfloat *samples = (*env)->GetFloatArrayElements(env, output, NULL);
    if (!samples)
        return NULL;
    jbyte *input = count ? (*env)->GetByteArrayElements(env, bytes, NULL) : NULL;
    if (count && !input) {
        (*env)->ReleaseFloatArrayElements(env, output, samples, JNI_ABORT);
        return NULL;
    }
    int status = cyrinx_batch_encode(&profile, count ? (uint8_t *)input : &byte, (size_t)count, samples,
                                     (size_t)frames, &result);
    if (input)
        (*env)->ReleaseByteArrayElements(env, bytes, input, JNI_ABORT);
    (*env)->ReleaseFloatArrayElements(env, output, samples, status ? JNI_ABORT : 0);
    if (status) {
        (*env)->ThrowNew(env, (*env)->FindClass(env, "java/lang/IllegalStateException"),
                         "batch encode failed");
        return NULL;
    }
    return output;
}

JNIEXPORT jobject JNICALL Java_Replay_decode(JNIEnv *env, jobject self, jint key, jfloatArray samples,
                                             jint channels, jboolean fixture) {
    (void)self;
    cyrinx_profile_t profile = {0};
    if (!samples || (channels != 1 && channels != 2) ||
        !(fixture ? replay_profile(key, &profile) : lookup_registered(key, &profile))) {
        (*env)->ThrowNew(env, (*env)->FindClass(env, "java/lang/IllegalArgumentException"),
                         "invalid replay input");
        return NULL;
    }
    jsize count = (*env)->GetArrayLength(env, samples);
    if (!count || count % channels) {
        (*env)->ThrowNew(env, (*env)->FindClass(env, "java/lang/IllegalArgumentException"),
                         "invalid channel layout");
        return NULL;
    }
    jfloat *data = (*env)->GetFloatArrayElements(env, samples, NULL);
    if (!data)
        return NULL;
    cyrinx_batch_input_t input = {
        .struct_size = sizeof(input),
        .abi_version = CYRINX_BATCH_INPUT_ABI_VERSION,
        .channel_count = (uint32_t)channels,
        .channels = {{data, (uint64_t)count, 0, (uint32_t)channels, CYRINX_BATCH_ROLE_PRIMARY},
                     {data, (uint64_t)count, 1, (uint32_t)channels, CYRINX_BATCH_ROLE_SECONDARY}}};
    cyrinx_batch_result_t result = {.struct_size = sizeof(result),
                                    .abi_version = CYRINX_BATCH_RESULT_ABI_VERSION};
    jsize blocks = (jsize)profile.block_count, capacity = blocks * 256;
    uint8_t *payload = calloc((size_t)capacity, 1), *mask = calloc((size_t)blocks, 1);
    int status =
        cyrinx_batch_decode(&profile, &input, payload, (size_t)capacity, mask, (size_t)blocks, &result);
    (*env)->ReleaseFloatArrayElements(env, samples, data, JNI_ABORT);
    jobject decoded = NULL;
    if (!payload || !mask) {
        (*env)->ThrowNew(env, (*env)->FindClass(env, "java/lang/OutOfMemoryError"), "replay output");
    } else if (status || result.blocks_total != (uint32_t)blocks ||
               result.payload_bytes_produced != (uint64_t)capacity) {
        (*env)->ThrowNew(env, (*env)->FindClass(env, "java/lang/IllegalStateException"),
                         "batch decode failed");
    } else {
        jclass cls = (*env)->FindClass(env, "Decoded");
        jmethodID ctor = cls ? (*env)->GetMethodID(env, cls, "<init>", "([B[B[BII)V") : NULL;
        jbyteArray id = ctor ? copy_bytes(env, result.profile_identity_sha256, 32) : NULL;
        jbyteArray valid = id && !(*env)->ExceptionCheck(env) ? copy_bytes(env, mask, blocks) : NULL;
        jbyteArray bytes = valid && !(*env)->ExceptionCheck(env) ? copy_bytes(env, payload, capacity) : NULL;
        if (bytes && !(*env)->ExceptionCheck(env))
            decoded = (*env)->NewObject(env, cls, ctor, id, valid, bytes, (jint)result.selected_receiver,
                                        (jint)result.selection_reason);
    }
    free(mask);
    free(payload);
    return decoded;
}
