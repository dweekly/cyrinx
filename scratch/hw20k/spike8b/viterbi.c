#include <math.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#define SPIKE8B_STATES 64
#define SPIKE8B_G0 0171
#define SPIKE8B_G1 0133

static uint8_t parity7(unsigned value) {
    value ^= value >> 4;
    value ^= value >> 2;
    value ^= value >> 1;
    return (uint8_t)(value & 1U);
}

int spike8b_viterbi(const double *llr0, const double *llr1, size_t steps, size_t information_bits,
                    uint8_t *decoded) {
    if (llr0 == NULL || llr1 == NULL || decoded == NULL || information_bits > steps ||
        steps > SIZE_MAX / SPIKE8B_STATES) {
        return -1;
    }

    uint8_t *back = malloc(steps * SPIKE8B_STATES);
    if (back == NULL) {
        return -2;
    }

    double metric[SPIKE8B_STATES];
    double next_metric[SPIKE8B_STATES];
    for (size_t state = 0; state < SPIKE8B_STATES; ++state) {
        metric[state] = state == 0 ? 0.0 : -1e300;
    }

    for (size_t index = 0; index < steps; ++index) {
        for (size_t state = 0; state < SPIKE8B_STATES; ++state) {
            next_metric[state] = -1e300;
        }
        for (unsigned state = 0; state < SPIKE8B_STATES; ++state) {
            for (unsigned bit = 0; bit < 2; ++bit) {
                unsigned reg = (bit << 6) | state;
                unsigned destination = reg >> 1;
                uint8_t coded0 = parity7(reg & SPIKE8B_G0);
                uint8_t coded1 = parity7(reg & SPIKE8B_G1);
                double branch0 = coded0 == 0 ? 0.5 * llr0[index] : -0.5 * llr0[index];
                double branch1 = coded1 == 0 ? 0.5 * llr1[index] : -0.5 * llr1[index];
                double candidate = metric[state] + branch0 + branch1;
                if (candidate >= next_metric[destination]) {
                    next_metric[destination] = candidate;
                    back[index * SPIKE8B_STATES + destination] = (uint8_t)((state << 1) | bit);
                }
            }
        }
        memcpy(metric, next_metric, sizeof(metric));
    }

    unsigned state = 0;
    for (size_t index = steps; index-- > 0;) {
        uint8_t transition = back[index * SPIKE8B_STATES + state];
        unsigned predecessor = transition >> 1;
        uint8_t bit = transition & 1U;
        if (index < information_bits) {
            decoded[index] = bit;
        }
        state = predecessor;
    }

    free(back);
    return 0;
}
