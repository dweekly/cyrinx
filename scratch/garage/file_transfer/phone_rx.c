/* One controlled bench session per working directory. Read stereo microphone
 * PCM on stdin and acknowledge only CRC-valid, indexed blocks found in it. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include "clock_offset.c"

enum {
    BLOCK = 256,
    HEADER = 18,
    CHUNK = BLOCK - HEADER,
    FILE_SIZE = 65536,
    CHUNKS = (FILE_SIZE + CHUNK - 1) / CHUNK,
    MASK_BYTES = (CHUNKS + 7) / 8
};
struct state {
    uint8_t token[8], mask[MASK_BYTES], initialized;
};
static void fail(const char *s) {
    perror(s);
    exit(2);
}
static unsigned read32(const uint8_t *p) {
    return ((unsigned)p[0] << 24) | ((unsigned)p[1] << 16) | ((unsigned)p[2] << 8) | p[3];
}
int main(int argc, char **argv) {
    if (argc != 3)
        return 2;
    int bits = atoi(argv[1]), mono = atoi(argv[2]);
    if ((bits != 2 && bits != 4 && bits != 6) || (mono < -1 || mono > 1))
        return 2;
    cyrinx_bulk_config down = {300, 23000, 8, bits, "1/2", 6, 16384, 8192, 48000, .5, 3.3, 2000, 16000};
    cyrinx_bulk_config up = {300, 11000, 8, 2, "1/2", 3, 16384, 8192, 48000, .5, 3.3, 2000, 16000};
    cyrinx_bulk_geometry g, a;
    if (cyrinx_bulk_compute_geometry(&down, &g) || cyrinx_bulk_compute_geometry(&up, &a))
        return 2;
    const size_t max = 48000 * 20;
    int16_t *pcm = malloc(2 * max * sizeof(*pcm));
    float *mic[2] = {malloc(max * sizeof(float)), malloc(max * sizeof(float))};
    if (!pcm || !mic[0] || !mic[1])
        fail("PCM allocation");
    size_t n = fread(pcm, 2 * sizeof(*pcm), max, stdin);
    if (n < (size_t)g.frame_samples || n == max || ferror(stdin))
        return 2;
    for (size_t i = 0; i < n; i++)
        for (int m = 0; m < 2; m++)
            mic[m][i] = pcm[2 * i + m] / 32768.f;
    size_t active = 0, same = 0;
    for (size_t i = 0; i < n; i++) {
        if (abs((int)pcm[2 * i]) < 64 && abs((int)pcm[2 * i + 1]) < 64)
            continue;
        active++;
        same += pcm[2 * i] == pcm[2 * i + 1];
    }
    if (mono == -1)
        mono = active >= 4800 && (double)same / active >= 0.999;
    free(pcm);
    uint8_t *out = malloc(g.payload_bytes), *valid = malloc(g.n_blocks);
    uint8_t *best = calloc(1, g.payload_bytes), *mask = calloc(1, g.n_blocks);
    if (!out || !valid || !best || !mask)
        fail("decoder allocation");
    cyrinx_research_set_pilot_fit(1);
    double best_evm = INFINITY;
    for (int method = 0; method < (mono ? 1 : 4); method++) {
        double ppm = 0, evm = INFINITY;
        int m = method % 2, ok = 0, total = 0;
        long rc =
            method < 2
                ? cyrinx_research_demodulate_clock_corrected(&down, mic[m], n, 150, 2, out, g.payload_bytes,
                                                             &ok, &total, &evm, valid, g.n_blocks, &ppm)
                : cyrinx_research_demodulate2_clock_corrected(&down, mic[m], mic[1 - m], n, 150, 2, out,
                                                              g.payload_bytes, &ok, &total, &evm, valid,
                                                              g.n_blocks, &ppm);
        if (rc == g.payload_bytes && evm < best_evm) {
            memcpy(best, out, g.payload_bytes);
            memcpy(mask, valid, g.n_blocks);
            best_evm = evm;
        }
    }
    FILE *audit = fopen("channel-log.txt", "a");
    if (!audit)
        fail("audit");
    fprintf(audit, "active=%zu identical=%zu mono=%d evm=%.6g\n", active, same, mono, best_evm);
    fclose(audit);
    struct state st = {0};
    FILE *f = fopen("state", "rb");
    if (f) {
        if (fread(&st, sizeof(st), 1, f) != 1)
            fail("state");
        fclose(f);
    }
    f = fopen("received.bin", st.initialized ? "r+b" : "w+b");
    if (!f)
        fail("file open");
    for (int i = 0; i < g.n_blocks; i++) {
        uint8_t *b = best + BLOCK * i;
        if (!mask[i] || memcmp(b, "DT01", 4) || read32(b + 14) != FILE_SIZE)
            continue;
        unsigned idx = ((unsigned)b[12] << 8) | b[13];
        if (idx >= CHUNKS)
            continue;
        if (!st.initialized) {
            memcpy(st.token, b + 4, 8);
            st.initialized = 1;
        }
        if (memcmp(st.token, b + 4, 8))
            continue;
        size_t pos = idx * CHUNK, len = FILE_SIZE - pos;
        if (len > CHUNK)
            len = CHUNK;
        if (fseek(f, pos, SEEK_SET) || fwrite(b + HEADER, 1, len, f) != len)
            fail("file write");
        st.mask[idx / 8] |= (uint8_t)(1u << (idx % 8));
    }
    if (fclose(f))
        fail("file close");
    f = fopen("state", "wb");
    if (!f || fwrite(&st, sizeof(st), 1, f) != 1 || fclose(f))
        fail("state save");
    uint8_t *ack = calloc(1, a.payload_bytes);
    float *wave = malloc(a.frame_samples * sizeof(float));
    if (!ack || !wave)
        fail("ACK allocation");
    /* Whiten unused control bytes before writing the repeated ACK records. */
    uint32_t prng = 0x97f4a13dU;
    for (int i = 0; i < a.payload_bytes; i++) {
        prng ^= prng << 13;
        prng ^= prng >> 17;
        prng ^= prng << 5;
        ack[i] = (uint8_t)prng;
    }
    for (int i = 0; i < a.n_blocks; i++) {
        uint8_t *b = ack + BLOCK * i;
        memcpy(b, "AK01", 4);
        memcpy(b + 4, st.token, 8);
        memcpy(b + 12, st.mask, MASK_BYTES);
    }
    if (cyrinx_bulk_modulate(&up, ack, a.payload_bytes, wave, a.frame_samples, NULL) != a.frame_samples)
        return 2;
    f = fopen("ack.pcm", "wb");
    if (!f)
        fail("ACK open");
    for (int i = 0; i < a.frame_samples + 12000; i++) {
        int16_t v = i < a.frame_samples ? (int16_t)lrintf(wave[i] * 32767) : 0;
        if (fwrite(&v, sizeof(v), 1, f) != 1)
            fail("ACK write");
    }
    if (fclose(f))
        fail("ACK close");
    return 0;
}
