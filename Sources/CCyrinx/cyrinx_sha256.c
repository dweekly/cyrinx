/* Portable FIPS 180-4 SHA-256. See cyrinx/cyrinx_base.h.
 *
 * Straightforward single-shot implementation: fixed 64-entry round-constant
 * table, 64-byte block schedule, big-endian length padding. No allocation,
 * no global mutable state. Validated against the NIST reference vectors in
 * CyrinxABIBaseTests.
 */
#include "cyrinx/cyrinx_base.h"

#include <string.h>

static const uint32_t kSha256RoundConstants[64] = {
    0x428a2f98u, 0x71374491u, 0xb5c0fbcfu, 0xe9b5dba5u, 0x3956c25bu, 0x59f111f1u, 0x923f82a4u, 0xab1c5ed5u,
    0xd807aa98u, 0x12835b01u, 0x243185beu, 0x550c7dc3u, 0x72be5d74u, 0x80deb1feu, 0x9bdc06a7u, 0xc19bf174u,
    0xe49b69c1u, 0xefbe4786u, 0x0fc19dc6u, 0x240ca1ccu, 0x2de92c6fu, 0x4a7484aau, 0x5cb0a9dcu, 0x76f988dau,
    0x983e5152u, 0xa831c66du, 0xb00327c8u, 0xbf597fc7u, 0xc6e00bf3u, 0xd5a79147u, 0x06ca6351u, 0x14292967u,
    0x27b70a85u, 0x2e1b2138u, 0x4d2c6dfcu, 0x53380d13u, 0x650a7354u, 0x766a0abbu, 0x81c2c92eu, 0x92722c85u,
    0xa2bfe8a1u, 0xa81a664bu, 0xc24b8b70u, 0xc76c51a3u, 0xd192e819u, 0xd6990624u, 0xf40e3585u, 0x106aa070u,
    0x19a4c116u, 0x1e376c08u, 0x2748774cu, 0x34b0bcb5u, 0x391c0cb3u, 0x4ed8aa4au, 0x5b9cca4fu, 0x682e6ff3u,
    0x748f82eeu, 0x78a5636fu, 0x84c87814u, 0x8cc70208u, 0x90befffau, 0xa4506cebu, 0xbef9a3f7u, 0xc67178f2u};

static uint32_t sha256_rotr(uint32_t value, unsigned bits) {
    return (value >> bits) | (value << (32u - bits));
}

static void sha256_compress(uint32_t state[8], const uint8_t block[64]) {
    uint32_t w[64];
    for (int t = 0; t < 16; ++t) {
        w[t] = ((uint32_t)block[t * 4] << 24) | ((uint32_t)block[t * 4 + 1] << 16) |
               ((uint32_t)block[t * 4 + 2] << 8) | (uint32_t)block[t * 4 + 3];
    }
    for (int t = 16; t < 64; ++t) {
        uint32_t s0 = sha256_rotr(w[t - 15], 7) ^ sha256_rotr(w[t - 15], 18) ^ (w[t - 15] >> 3);
        uint32_t s1 = sha256_rotr(w[t - 2], 17) ^ sha256_rotr(w[t - 2], 19) ^ (w[t - 2] >> 10);
        w[t] = w[t - 16] + s0 + w[t - 7] + s1;
    }

    uint32_t a = state[0], b = state[1], c = state[2], d = state[3];
    uint32_t e = state[4], f = state[5], g = state[6], h = state[7];

    for (int t = 0; t < 64; ++t) {
        uint32_t big_s1 = sha256_rotr(e, 6) ^ sha256_rotr(e, 11) ^ sha256_rotr(e, 25);
        uint32_t ch = (e & f) ^ (~e & g);
        uint32_t temp1 = h + big_s1 + ch + kSha256RoundConstants[t] + w[t];
        uint32_t big_s0 = sha256_rotr(a, 2) ^ sha256_rotr(a, 13) ^ sha256_rotr(a, 22);
        uint32_t maj = (a & b) ^ (a & c) ^ (b & c);
        uint32_t temp2 = big_s0 + maj;
        h = g;
        g = f;
        f = e;
        e = d + temp1;
        d = c;
        c = b;
        b = a;
        a = temp1 + temp2;
    }

    state[0] += a;
    state[1] += b;
    state[2] += c;
    state[3] += d;
    state[4] += e;
    state[5] += f;
    state[6] += g;
    state[7] += h;
}

void cyrinx_sha256(const uint8_t *data, size_t len, uint8_t *out_digest32) {
    uint32_t state[8] = {0x6a09e667u, 0xbb67ae85u, 0x3c6ef372u, 0xa54ff53au,
                         0x510e527fu, 0x9b05688cu, 0x1f83d9abu, 0x5be0cd19u};

    size_t offset = 0;
    while (len - offset >= 64) {
        sha256_compress(state, data + offset);
        offset += 64;
    }

    /* Final padding: 0x80, zeros, then the bit length as a big-endian u64. */
    uint8_t tail[128];
    size_t rem = len - offset;
    memset(tail, 0, sizeof(tail));
    if (rem > 0) {
        memcpy(tail, data + offset, rem);
    }
    tail[rem] = 0x80;
    size_t tail_blocks = (rem + 1 + 8 > 64) ? 2 : 1;
    uint64_t bit_len = (uint64_t)len * 8u;
    for (int i = 0; i < 8; ++i) {
        tail[tail_blocks * 64 - 8 + i] = (uint8_t)(bit_len >> (56 - 8 * i));
    }
    sha256_compress(state, tail);
    if (tail_blocks == 2) {
        sha256_compress(state, tail + 64);
    }

    for (int i = 0; i < 8; ++i) {
        out_digest32[i * 4] = (uint8_t)(state[i] >> 24);
        out_digest32[i * 4 + 1] = (uint8_t)(state[i] >> 16);
        out_digest32[i * 4 + 2] = (uint8_t)(state[i] >> 8);
        out_digest32[i * 4 + 3] = (uint8_t)state[i];
    }
}
