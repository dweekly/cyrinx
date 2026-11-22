#include <stdio.h>
#include <stdint.h>
#include "clock_offset.c"

int main(int argc, char **argv) {
    if (argc != 2)
        return 2;
    int frames = atoi(argv[1]);
    if (frames < 1 || frames > 4)
        return 2;
    const int frame = 6144 + 8 * 24576, gap = 12000, cap = (frame + gap) * frames + 3 * 48000;
    int16_t *pcm = malloc((size_t)cap * 2 * sizeof(*pcm));
    if (!pcm)
        return 2;
    int n = (int)fread(pcm, 2 * sizeof(*pcm), cap, stdin);
    int prefix = n - (frames - 1) * (frame + gap) - frame + 4096;
    if (n == cap || prefix < 4096 || ferror(stdin))
        return 2;
    double chirp[4096], *signal = malloc((size_t)prefix * sizeof(*signal));
    if (!signal)
        return 2;
    cyrinx_make_chirp(chirp, 4096, 48000, 2000, 16000);
    for (int i = 0; i < prefix; i++)
        signal[i] = pcm[2 * i];
    int first = cyrinx_matched_filter(signal, prefix, chirp, 4096, 0);
    for (int j = 0; j < frames; j++) {
        int center = first + j * (frame + gap), begin = center - 3072, end = center + frame + 8000;
        if (begin < 0)
            begin = 0;
        if (end > n)
            end = n;
        if (end - begin < frame)
            return 2;
        char name[32];
        snprintf(name, sizeof(name), "window-%d.pcm", j);
        FILE *out = fopen(name, "wb");
        if (!out || fwrite(pcm + 2 * begin, 2 * sizeof(*pcm), end - begin, out) != (size_t)(end - begin) ||
            fclose(out))
            return 2;
    }
    return 0;
}
