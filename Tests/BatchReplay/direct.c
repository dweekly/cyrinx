#include "Profiles.h"
#include <stdio.h>
#include <stdlib.h>

int main(int argc, char **argv) {
    cyrinx_profile_t profile;
    if (argc != 4 || !replay_profile(atoi(argv[1]), &profile))
        return 1;
    uint32_t channels = (uint32_t)atoi(argv[3]);
    if (channels != 1 && channels != 2)
        return 1;
    FILE *file = fopen(argv[2], "rb");
    if (!file || fseek(file, 0, SEEK_END))
        return 1;
    long size = ftell(file);
    if (size <= 0 || (size_t)size % (sizeof(float) * channels) || fseek(file, 0, SEEK_SET))
        return 1;
    float *samples = malloc((size_t)size);
    if (!samples || fread(samples, 1, (size_t)size, file) != (size_t)size)
        return 1;
    fclose(file);
    cyrinx_batch_input_t input = {
        .struct_size = sizeof(input),
        .abi_version = CYRINX_BATCH_INPUT_ABI_VERSION,
        .channel_count = channels,
        .channels = {{samples, (size_t)size / sizeof(float), 0, channels, CYRINX_BATCH_ROLE_PRIMARY},
                     {samples, (size_t)size / sizeof(float), 1, channels, CYRINX_BATCH_ROLE_SECONDARY}}};
    cyrinx_batch_result_t result = {.struct_size = sizeof(result),
                                    .abi_version = CYRINX_BATCH_RESULT_ABI_VERSION};
    size_t blocks = profile.block_count, capacity = blocks * 256;
    uint8_t *payload = calloc(capacity, 1), *mask = calloc(blocks, 1);
    int status = cyrinx_batch_decode(&profile, &input, payload, capacity, mask, blocks, &result);
    free(samples);
    if (status != CYRINX_STATUS_OK || result.blocks_total != blocks ||
        result.payload_bytes_produced != capacity) {
        fprintf(stderr, "batch decode status %d, blocks %u\n", status, result.blocks_total);
        return 1;
    }
    bool ok = fwrite(result.profile_identity_sha256, 1, 32, stdout) == 32 &&
              fwrite(&result.selected_receiver, sizeof(uint32_t), 1, stdout) == 1 &&
              fwrite(&result.selection_reason, sizeof(uint32_t), 1, stdout) == 1 &&
              fwrite(mask, 1, blocks, stdout) == blocks && fwrite(payload, 1, capacity, stdout) == capacity;
    free(mask);
    free(payload);
    return ok ? 0 : 1;
}
