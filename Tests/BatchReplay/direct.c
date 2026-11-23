#include "Packet.h"
#include "Profiles.h"
#include <stdio.h>
#include <stdlib.h>

int main(int argc, char **argv) {
    // A registered profile goes through the v2 type and entry point, a test geometry through
    // the v1 ones, so both are exercised.
    cyrinx_profile_t profile;
    cyrinx_profile_v2_t registered;
    if (argc != 4)
        return 1;
    bool by_id = replay_names_registered(argv[1]);
    int key = 0;
    if (by_id ? !replay_registered(argv[1], &registered)
              : !(replay_key(argv[1], &key) && replay_profile(key, &profile)))
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
    size_t blocks = by_id ? registered.block_count : profile.block_count, capacity = blocks * 256;
    uint8_t *payload = calloc(capacity, 1), *mask = calloc(blocks, 1);
    if (!payload || !mask)
        return 1;
    int status = by_id ? cyrinx_batch_decode_v2(&registered, &input, payload, capacity, mask, blocks, &result)
                       : cyrinx_batch_decode(&profile, &input, payload, capacity, mask, blocks, &result);
    free(samples);
    // A rejected input is a result too: the header carries the status and nothing follows it.
    uint8_t header[REPLAY_HEADER_BYTES];
    replay_header(header, status, &result);
    bool ok = fwrite(header, 1, sizeof header, stdout) == sizeof header;
    if (ok && status == CYRINX_STATUS_OK)
        ok = fwrite(mask, 1, blocks, stdout) == blocks && fwrite(payload, 1, capacity, stdout) == capacity;
    free(mask);
    free(payload);
    return ok ? 0 : 1;
}
