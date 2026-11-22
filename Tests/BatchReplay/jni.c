#include "Profiles.h"
#include <jni.h>
#include <stdlib.h>

static jbyteArray copy_bytes(JNIEnv *env, const uint8_t *data, jsize count) {
    jbyteArray array = (*env)->NewByteArray(env, count);
    if (array)
        (*env)->SetByteArrayRegion(env, array, 0, count, (const jbyte *)data);
    return array;
}

JNIEXPORT jobject JNICALL Java_Replay_decode(JNIEnv *env, jobject self, jint key, jfloatArray samples,
                                             jint channels) {
    (void)self;
    cyrinx_profile_t profile;
    if (!samples || (channels != 1 && channels != 2) || !replay_profile(key, &profile)) {
        (*env)->ThrowNew(env, (*env)->FindClass(env, "java/lang/IllegalArgumentException"),
                         "invalid replay input");
        return NULL;
    }
    jsize count = (*env)->GetArrayLength(env, samples);
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
