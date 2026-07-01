#!/usr/bin/env python3
"""Reed-Solomon over GF(16) for the MFSK floor modem (A3, ROADMAP Track A).

One RS code symbol = one 4-bit nibble = exactly one 16-FSK tone decision, so a
wrong tone pick corrupts exactly one code symbol (nibble-aligned). Codewords
are up to n=15 nibbles; the floor uses RS(15,11) (nsym=4 parity nibbles),
correcting e errors + f erasures with 2e + f <= 4. Erasures come free from the
energy detector's confidence (best/second-best tone energy), which repetition
voting could not exploit.

Errors-and-erasures decoder: syndromes -> Forney syndromes (erasures folded
in) -> Berlekamp-Massey -> Chien search -> Forney magnitudes. The decode path
mirrors the standard reedsolo formulation (same polynomial conventions,
descending-order coefficient lists), specialized to GF(2^4), primitive
polynomial x^4 + x + 1, generator element 2, fcr=0.

Run: .venv/bin/python3 scratch/hw20k/rs16.py   (exhaustive selftest)
"""
import sys

FIELD = 16
ORDER = 15                      # multiplicative order of GF(16)*
PRIM = 0x13                     # x^4 + x + 1

EXP = [0] * (2 * ORDER)         # doubled so products index without % ORDER
LOG = [0] * FIELD
_x = 1
for _i in range(ORDER):
    EXP[_i] = _x
    LOG[_x] = _i
    _x <<= 1
    if _x & 0x10:
        _x ^= PRIM
for _i in range(ORDER, 2 * ORDER):
    EXP[_i] = EXP[_i - ORDER]


def gf_mul(a, b):
    if a == 0 or b == 0:
        return 0
    return EXP[LOG[a] + LOG[b]]


def gf_div(a, b):
    if b == 0:
        raise ZeroDivisionError()
    if a == 0:
        return 0
    return EXP[(LOG[a] - LOG[b]) % ORDER]


def gf_pow(a, n):
    return EXP[(LOG[a] * n) % ORDER]


def gf_inv(a):
    return EXP[(ORDER - LOG[a]) % ORDER]


# polynomial helpers; coefficient lists are DESCENDING (p[0] = highest degree)

def poly_scale(p, x):
    return [gf_mul(c, x) for c in p]


def poly_add(p, q):
    r = [0] * max(len(p), len(q))
    r[len(r) - len(p):] = p
    for i, c in enumerate(q):
        r[i + len(r) - len(q)] ^= c
    return r


def poly_mul(p, q):
    r = [0] * (len(p) + len(q) - 1)
    for j, qj in enumerate(q):
        if qj:
            for i, pi in enumerate(p):
                r[i + j] ^= gf_mul(pi, qj)
    return r


def poly_div(dividend, divisor):
    out = list(dividend)
    for i in range(len(dividend) - (len(divisor) - 1)):
        coef = out[i]
        if coef:
            for j in range(1, len(divisor)):
                if divisor[j]:
                    out[i + j] ^= gf_mul(divisor[j], coef)
    sep = -(len(divisor) - 1)
    return out[:sep], out[sep:]


def poly_eval(p, x):
    y = p[0]
    for c in p[1:]:
        y = gf_mul(y, x) ^ c
    return y


def _generator_poly(nsym):
    g = [1]
    for i in range(nsym):
        g = poly_mul(g, [1, gf_pow(2, i)])
    return g


def encode(msg, nsym=4):
    """Systematic encode: msg (list of nibbles) -> msg + nsym parity nibbles.
    len(msg) + nsym must be <= 15."""
    assert len(msg) + nsym <= ORDER, "codeword exceeds n=15"
    gen = _generator_poly(nsym)
    _, rem = poly_div(list(msg) + [0] * nsym, gen)
    return list(msg) + rem


def _syndromes(cw, nsym):
    # leading 0 = math convenience (mirrors reedsolo), stripped by callers
    return [0] + [poly_eval(cw, gf_pow(2, i)) for i in range(nsym)]


def _forney_syndromes(synd, erase_pos, n):
    fsynd = list(synd[1:])
    for p in erase_pos:
        x = gf_pow(2, n - 1 - p)
        for j in range(len(fsynd) - 1):
            fsynd[j] = gf_mul(fsynd[j], x) ^ fsynd[j + 1]
    return fsynd


def _berlekamp_massey(fsynd, nsym, erase_count):
    err_loc, old_loc = [1], [1]
    for i in range(nsym - erase_count):
        delta = fsynd[i]
        for j in range(1, len(err_loc)):
            delta ^= gf_mul(err_loc[-(j + 1)], fsynd[i - j])
        old_loc.append(0)
        if delta:
            if len(old_loc) > len(err_loc):
                new_loc = poly_scale(old_loc, delta)
                old_loc = poly_scale(err_loc, gf_inv(delta))
                err_loc = new_loc
            err_loc = poly_add(err_loc, poly_scale(old_loc, delta))
    while err_loc and err_loc[0] == 0:
        err_loc.pop(0)
    errs = len(err_loc) - 1
    if 2 * errs + erase_count > nsym:
        return None
    return err_loc


def _find_errors(err_loc, n):
    """err_loc in ASCENDING order (reversed); roots -> error positions."""
    errs = len(err_loc) - 1
    pos = [n - 1 - i for i in range(n)
           if poly_eval(err_loc, gf_pow(2, i)) == 0]
    return pos if len(pos) == errs else None


def _errata_locator(coef_pos):
    loc = [1]
    for cp in coef_pos:
        loc = poly_mul(loc, poly_add([1], [gf_pow(2, cp), 0]))
    return loc


def _error_evaluator(synd_rev, err_loc, nsym):
    _, rem = poly_div(poly_mul(synd_rev, err_loc), [1] + [0] * (nsym + 1))
    return rem


def _correct_errata(cw, synd, errata_pos):
    n = len(cw)
    coef_pos = [n - 1 - p for p in errata_pos]
    err_loc = _errata_locator(coef_pos)
    err_eval = _error_evaluator(synd[::-1], err_loc, len(err_loc) - 1)[::-1]
    X = [gf_pow(2, -(ORDER - cp)) for cp in coef_pos]
    E = [0] * n
    for i, Xi in enumerate(X):
        Xi_inv = gf_inv(Xi)
        prime = 1
        for j, Xj in enumerate(X):
            if j != i:
                prime = gf_mul(prime, 1 ^ gf_mul(Xi_inv, Xj))
        if prime == 0:
            return None
        y = gf_mul(Xi, poly_eval(err_eval[::-1], Xi_inv))   # fcr=0 -> Xi^1
        E[errata_pos[i]] = gf_div(y, prime)
    return poly_add(cw, E)


def decode(cw, nsym=4, erase_pos=None):
    """Errors-and-erasures decode of a length-n codeword (list of nibbles).
    Returns the corrected codeword, or None if the errata exceed the budget
    (2*errors + erasures > nsym). Slice [:-nsym] for the message."""
    erase_pos = list(erase_pos or [])
    if len(erase_pos) > nsym:
        return None
    synd = _syndromes(cw, nsym)
    if max(synd) == 0:
        return list(cw)
    fsynd = _forney_syndromes(synd, erase_pos, len(cw))
    err_loc = _berlekamp_massey(fsynd, nsym, len(erase_pos))
    if err_loc is None:
        return None
    err_pos = _find_errors(err_loc[::-1], len(cw)) if len(err_loc) > 1 else []
    if err_pos is None:
        return None
    out = _correct_errata(cw, synd, erase_pos + err_pos)
    if out is None or max(_syndromes(out, nsym)) != 0:
        return None
    return out


def _selftest():
    import random
    rng = random.Random(0xA3)
    nsym, k = 4, 11
    trials, fails = 0, 0
    for e in range(0, nsym // 2 + 1):               # hard errors
        for f in range(0, nsym - 2 * e + 1):        # erasures, 2e+f <= nsym
            cell_fails = 0
            for _ in range(400):
                msg = [rng.randrange(16) for _ in range(k)]
                cw = encode(msg, nsym)
                pos = rng.sample(range(len(cw)), e + f)
                bad = list(cw)
                for p in pos:
                    bad[p] ^= rng.randrange(1, 16)
                got = decode(bad, nsym, erase_pos=pos[e:])
                trials += 1
                if got is None or got[:k] != msg:
                    cell_fails += 1
            fails += cell_fails
            print(f"  e={e} f={f}: {'OK' if cell_fails == 0 else f'FAIL ({cell_fails}/400)'}")
    # beyond-budget sanity: the decoder should reject (or miscorrect — the
    # frame CRC-32 is the backstop); it must never crash
    beyond_none = 0
    for _ in range(400):
        msg = [rng.randrange(16) for _ in range(k)]
        bad = encode(msg, nsym)
        for p in rng.sample(range(15), 3):          # 3 errors > t=2
            bad[p] ^= rng.randrange(1, 16)
        got = decode(bad, nsym)
        beyond_none += int(got is None or got[:k] != msg)
    print(f"  3-error overload: {beyond_none}/400 rejected or miscorrected "
          f"(caught by frame CRC)")
    print(f"  SELFTEST {'PASS' if fails == 0 else 'FAIL'} ({trials} in-budget trials)")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(_selftest())
