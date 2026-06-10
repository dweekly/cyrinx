#!/usr/bin/env python3
"""Wideband OFDM acoustic modem (prototype) for the Mac <-> Pixel 7a link.

Design point (from measured channel, see NOTES.md):
  48 kHz, FFT 1024 (46.875 Hz bins), CP 256 -> 37.5 OFDM symbols/s.
  Comb pilots every 8th used bin; per-symbol pilot phase/timing tracking.
  Per-bin adaptive QAM (0/1/2/4/6/8 bits) + K=7 convolutional FEC with
  puncturing + frame-wide interleaving. CRC32 per 256-byte payload block.

Goodput definition (honest): CRC-valid payload bits / airtime, where airtime
runs from the first preamble sample to the last data sample of the frame.
"""

import numpy as np
import zlib

SR = 48000
NFFT = 1024
CP = 256
SYM = NFFT + CP
BIN_HZ = SR / NFFT

CHIRP_LEN = 4096
CHIRP_F0, CHIRP_F1 = 2000.0, 16000.0
GUARD = 2048             # silence after chirp so its reverb tail decays
                         # before the channel-estimation symbols
CRC_BLOCK = 256          # payload bytes per CRC32 block (4-byte CRC appended)

# Convolutional code K=7 (171, 133), MSB = newest bit.
G0, G1 = 0o171, 0o133
NSTATES = 64

PUNCTURE = {
    "1/2": ([1, 1], 1 / 2),
    "2/3": ([1, 1, 0, 1], 2 / 3),            # per 2 info bits: keep c0a c1a c1b
    "3/4": ([1, 1, 0, 1, 1, 0], 3 / 4),      # standard 3/4 pattern
    "5/6": ([1, 1, 0, 1, 1, 0, 0, 1, 1, 0], 5 / 6),
}


def _parity_table():
    t = np.zeros(128, dtype=np.int8)
    for v in range(128):
        t[v] = bin(v).count("1") & 1
    return t

_PAR = _parity_table()

# Precomputed trellis: for prev state s and input bit b
_PREV = np.arange(NSTATES)
_NEXT = np.zeros((NSTATES, 2), dtype=np.int64)
_OUT0 = np.zeros((NSTATES, 2), dtype=np.int8)
_OUT1 = np.zeros((NSTATES, 2), dtype=np.int8)
for s in range(NSTATES):
    for b in (0, 1):
        reg = (b << 6) | s
        _NEXT[s, b] = reg >> 1
        _OUT0[s, b] = _PAR[reg & G0]
        _OUT1[s, b] = _PAR[reg & G1]


def conv_encode(bits):
    """Rate-1/2 encode with 6 zero tail bits. bits: uint8 array."""
    bits = np.concatenate([bits, np.zeros(6, dtype=np.uint8)])
    out = np.empty(2 * len(bits), dtype=np.uint8)
    s = 0
    for i, b in enumerate(bits):
        reg = (int(b) << 6) | s
        out[2 * i] = _PAR[reg & G0]
        out[2 * i + 1] = _PAR[reg & G1]
        s = reg >> 1
    return out


def viterbi_decode(llr0, llr1, n_info):
    """Soft Viterbi. llr0/llr1: per-step LLRs (log P0/P1) for the two coded
    bits (0 = erasure). Returns decoded info bits (without the 6 tail bits)."""
    n = len(llr0)
    metrics = np.full(NSTATES, -1e12)
    metrics[0] = 0.0
    back = np.zeros((n, NSTATES), dtype=np.uint8)
    # branch metric for coded bit c with llr l: c==0 -> +l/2 ; c==1 -> -l/2
    for i in range(n):
        l0, l1 = llr0[i], llr1[i]
        # candidate metric for transition (s, b) -> next
        bm = np.where(_OUT0 == 0, 0.5 * l0, -0.5 * l0) + \
             np.where(_OUT1 == 0, 0.5 * l1, -0.5 * l1)   # (64, 2)
        cand = metrics[:, None] + bm                      # (64, 2)
        flat = cand.ravel()
        nxt = _NEXT.ravel()
        new = np.full(NSTATES, -1e12)
        arg = np.zeros(NSTATES, dtype=np.int64)
        order = np.argsort(flat)  # ascending; later (larger) wins
        np.put(new, nxt[order], flat[order])
        np.put(arg, nxt[order], order)
        back[i] = arg
        metrics = new
    # traceback from state 0 (zero-terminated)
    s = 0
    bits = np.zeros(n, dtype=np.uint8)
    for i in range(n - 1, -1, -1):
        o = back[i, s]
        ps, b = o // 2, o % 2
        bits[i] = b
        s = ps
    return bits[:n_info]


def puncture(coded, pattern):
    pat = np.array(pattern, dtype=bool)
    n = len(coded)
    keep = np.resize(pat, n)
    return coded[keep]


def depuncture_llr(llr, pattern, n_coded):
    pat = np.resize(np.array(pattern, dtype=bool), n_coded)
    full = np.zeros(n_coded)
    full[pat] = llr[: pat.sum()]
    return full


# ---------------- QAM ----------------

def _gray_levels(nbits_axis):
    """Gray-coded PAM levels for one axis; returns (levels, bit_patterns)."""
    L = 1 << nbits_axis
    lv = np.arange(L) * 2 - (L - 1)          # -(L-1) .. (L-1)
    gray = np.arange(L) ^ (np.arange(L) >> 1)
    # map: index by gray code value -> level position
    order = np.argsort(gray)
    return lv.astype(float), order


_QAM_NORM = {1: 1.0, 2: np.sqrt(2), 4: np.sqrt(10), 6: np.sqrt(42), 8: np.sqrt(170)}


def qam_map(bits, nbits):
    """bits: (n, nbits) -> complex symbols, unit average power, Gray mapping."""
    if nbits == 1:
        return (1 - 2 * bits[:, 0]).astype(complex)
    na = nbits // 2
    lv, order = _gray_levels(na)
    w = (1 << np.arange(na - 1, -1, -1))
    gi = (bits[:, :na] * w).sum(axis=1)
    gq = (bits[:, na:] * w).sum(axis=1)
    i = lv[order[gi]]
    q = lv[order[gq]]
    return (i + 1j * q) / _QAM_NORM[nbits]


def qam_llr(z, nbits, n0):
    """Max-log LLRs (log P0/P1) for one complex symbol array z with noise var n0.
    Returns (n, nbits)."""
    n = len(z)
    if nbits == 1:
        return (4.0 * z.real / np.maximum(n0, 1e-9))[:, None]
    na = nbits // 2
    lv, order = _gray_levels(na)
    norm = _QAM_NORM[nbits]
    lv_n = lv / norm
    # per-axis: distances to every level
    out = np.empty((n, nbits))
    for axis, y in ((0, z.real), (1, z.imag)):
        d2 = (y[:, None] - lv_n[None, :]) ** 2      # (n, L)
        # bits of each level position: gray index g such that order[g] = pos
        g_of_pos = np.empty(len(lv), dtype=int)
        g_of_pos[order] = np.arange(len(lv))
        for bit in range(na):
            mask1 = ((g_of_pos >> (na - 1 - bit)) & 1).astype(bool)
            d0 = d2[:, ~mask1].min(axis=1)
            d1 = d2[:, mask1].min(axis=1)
            out[:, axis * na + bit] = (d1 - d0) / np.maximum(n0, 1e-9)
    return out


# ---------------- PRBS / pilots ----------------

def prbs_bits(n, seed=0xC0FFEE):
    rng = np.random.default_rng(seed)
    return rng.integers(0, 2, n, dtype=np.uint8)


def pilot_symbols(pilot_bins, seed=0xBEEF):
    rng = np.random.default_rng(seed)
    ph = rng.integers(0, 4, len(pilot_bins))
    return np.exp(1j * (np.pi / 4 + np.pi / 2 * ph))


# ---------------- Modem config ----------------

class Config:
    def __init__(self, f_lo=1100.0, f_hi=23000.0, pilot_every=8,
                 bits_per_bin=None, rate="1/2", n_sym=64, amp=0.5,
                 clip_sigma=3.3, nfft=NFFT, cp=CP):
        self.nfft = nfft
        self.cp = cp
        self.sym = nfft + cp
        self.bin_hz = SR / nfft
        self.bin_lo = int(np.ceil(f_lo / self.bin_hz))
        self.bin_hi = int(np.floor(f_hi / self.bin_hz))
        self.used = np.arange(self.bin_lo, self.bin_hi + 1)
        self.pilot_idx = self.used[::pilot_every]
        self.data_idx = np.array(sorted(set(self.used) - set(self.pilot_idx)))
        self.rate_name = rate
        self.pattern, self.rate = PUNCTURE[rate]
        self.n_sym = n_sym
        self.amp = amp
        self.clip_sigma = clip_sigma
        if bits_per_bin is None:
            bits_per_bin = {b: 2 for b in self.data_idx}
        self.bits_per_bin = bits_per_bin  # dict bin -> 0/1/2/4/6/8
        self.data_bins = [b for b in self.data_idx if bits_per_bin.get(b, 0) > 0]
        self.bits_per_sym = int(sum(bits_per_bin.get(b, 0) for b in self.data_bins))
        self.pilots = pilot_symbols(self.pilot_idx)
        # frame capacity
        coded = self.bits_per_sym * n_sym
        info = int(np.floor(coded * self.rate)) - 6
        blk_bits = (CRC_BLOCK + 4) * 8
        self.n_blocks = info // blk_bits
        self.payload_bytes = self.n_blocks * CRC_BLOCK
        self.info_bits = info
        self.frame_samples = CHIRP_LEN + GUARD + (2 + n_sym) * self.sym
        self.airtime_s = self.frame_samples / SR

    def describe(self):
        return (f"nfft {self.nfft} cp {self.cp}, band {self.bin_lo*self.bin_hz:.0f}-{self.bin_hi*self.bin_hz:.0f} Hz, "
                f"{len(self.data_bins)} data bins + {len(self.pilot_idx)} pilots, "
                f"{self.bits_per_sym} bits/sym, rate {self.rate_name}, {self.n_sym} syms, "
                f"{self.payload_bytes} B payload, airtime {self.airtime_s:.2f}s, "
                f"PHY goodput cap {self.payload_bytes*8/self.airtime_s/1000:.1f} kbps")


def make_chirp():
    t = np.arange(CHIRP_LEN) / SR
    T = CHIRP_LEN / SR
    ph = 2 * np.pi * (CHIRP_F0 * t + 0.5 * (CHIRP_F1 - CHIRP_F0) * t * t / T)
    w = np.sin(ph)
    r = 128
    env = np.ones(CHIRP_LEN)
    env[:r] = 0.5 - 0.5 * np.cos(np.pi * np.arange(r) / r)
    env[-r:] = env[:r][::-1]
    return (w * env).astype(np.float64)

CHIRP = make_chirp()


def sync_symbol_freq(cfg, which=0):
    """Known full-band QPSK symbol for channel estimation."""
    rng = np.random.default_rng(0x5EED + which)
    ph = rng.integers(0, 4, len(cfg.used))
    return np.exp(1j * (np.pi / 4 + np.pi / 2 * ph))


def ofdm_mod_symbol(cfg, freq_vals_on_used):
    spec = np.zeros(cfg.nfft // 2 + 1, dtype=complex)
    spec[cfg.used] = freq_vals_on_used
    x = np.fft.irfft(spec, cfg.nfft)
    return np.concatenate([x[-cfg.cp:], x])


# ---------------- TX ----------------

def modulate_frame(cfg, payload_bytes, frame_seed=1):
    """payload_bytes: exactly cfg.payload_bytes. Returns float32 waveform."""
    assert len(payload_bytes) == cfg.payload_bytes
    # blocks + CRC
    blocks = []
    for i in range(cfg.n_blocks):
        blk = payload_bytes[i * CRC_BLOCK:(i + 1) * CRC_BLOCK]
        crc = zlib.crc32(blk).to_bytes(4, "big")
        blocks.append(blk + crc)
    stream = b"".join(blocks)
    bits = np.unpackbits(np.frombuffer(stream, dtype=np.uint8))
    # pad info bits up to cfg.info_bits
    pad = cfg.info_bits - len(bits)
    bits = np.concatenate([bits, prbs_bits(pad, seed=7)]) if pad else bits
    coded = conv_encode(bits)
    coded = puncture(coded, cfg.pattern)
    # fill to symbol capacity with PRBS (receiver ignores)
    cap = cfg.bits_per_sym * cfg.n_sym
    if len(coded) < cap:
        coded = np.concatenate([coded, prbs_bits(cap - len(coded), seed=8)])
    # frame-wide interleave
    rng = np.random.default_rng(0x1EAF)
    perm = rng.permutation(cap)
    inter = np.empty(cap, dtype=np.uint8)
    inter[perm] = coded[:cap]

    syms = []
    # 2 sync symbols
    for w in range(2):
        syms.append(ofdm_mod_symbol(cfg, sync_symbol_freq(cfg, w)))
    # data symbols
    pos = 0
    used_set = {b: i for i, b in enumerate(cfg.used)}
    for s in range(cfg.n_sym):
        fv = np.zeros(len(cfg.used), dtype=complex)
        fv[[used_set[b] for b in cfg.pilot_idx]] = cfg.pilots
        for b in cfg.data_bins:
            nb = cfg.bits_per_bin[b]
            chunk = inter[pos:pos + nb]
            pos += nb
            fv[used_set[b]] = qam_map(chunk[None, :], nb)[0]
        syms.append(ofdm_mod_symbol(cfg, fv))
    x = np.concatenate(syms)
    # normalize + soft clip to tame OFDM PAPR
    sigma = x.std()
    x = np.clip(x, -cfg.clip_sigma * sigma, cfg.clip_sigma * sigma)
    x = x / np.abs(x).max() * cfg.amp
    wave = np.concatenate([CHIRP * cfg.amp, np.zeros(GUARD), x])
    return wave.astype(np.float32)


# ---------------- RX ----------------

def find_chirp(rx, search_from=0):
    """Matched filter; returns sample index where chirp starts."""
    mf = np.correlate(rx[search_from:], CHIRP, mode="valid")
    pk = int(np.argmax(np.abs(mf)))
    return search_from + pk, np.abs(mf[pk]) / (np.linalg.norm(CHIRP) *
           np.linalg.norm(rx[search_from + pk: search_from + pk + CHIRP_LEN]) + 1e-12)


def demodulate_frame(cfg, rx, start_hint=None, fine_window=400, diag=None):
    """Returns dict with blocks_ok, payload, evm, etc. rx: float array."""
    if start_hint is None:
        start, q = find_chirp(rx)
    else:
        start, q = start_hint, 1.0
    base = start + CHIRP_LEN + GUARD

    # fine alignment on first sync symbol via cross-correlation
    ref = ofdm_mod_symbol(cfg, sync_symbol_freq(cfg, 0))
    lo = max(0, base - fine_window)
    seg = rx[lo: base + fine_window + cfg.sym]
    mf = np.correlate(seg, ref, mode="valid")
    off = int(np.argmax(np.abs(mf)))
    base = lo + off

    def fft_at(pos):
        w = rx[pos + cfg.cp: pos + cfg.sym]
        if len(w) < cfg.nfft:
            return None
        return np.fft.rfft(w[:cfg.nfft])

    def estimate_H(b):
        Hs = []
        for w in range(2):
            Y = fft_at(b + w * cfg.sym)
            if Y is None:
                return None
            X = sync_symbol_freq(cfg, w)
            Hs.append(Y[cfg.used] / X)
        return Hs

    # Bias the window a little early so multipath pre-cursors (taps arriving
    # before the strongest one the xcorr locks to) stay inside the CP. The CP
    # is sized with margin, so starting early only trades guard headroom.
    base -= 24
    Hs = estimate_H(base)
    if Hs is None:
        return {"ok": False, "err": "short capture"}

    H = (Hs[0] + Hs[1]) / 2
    # noise variance per bin from the two estimates
    nv = np.abs(Hs[0] - Hs[1]) ** 2 / 2
    # Do NOT smooth H across bins: with multipath/bulk delay its phase rotates
    # multiple cycles across a few bins and smoothing destroys the estimate.
    # Only the (real, positive) noise variance is smoothed.
    H_s = H
    k = np.ones(9) / 9
    nv_s = np.convolve(nv, k, mode="same") + 1e-12
    snr_bin = (np.abs(H_s) ** 2) / nv_s

    used_set = {b: i for i, b in enumerate(cfg.used)}
    pil_pos = np.array([used_set[b] for b in cfg.pilot_idx])
    dat_pos = np.array([used_set[b] for b in cfg.data_bins])
    pil_bin = cfg.pilot_idx.astype(float)

    llr_stream = np.empty(cfg.bits_per_sym * cfg.n_sym)
    pos = 0
    evms = []
    for s in range(cfg.n_sym):
        Y = fft_at(base + (2 + s) * cfg.sym)
        if Y is None:
            return {"ok": False, "err": f"short capture at sym {s}"}
        Z = Y[cfg.used] / H_s
        # pilot phase tracking: CPE + timing slope, fitted iteratively so a
        # large ramp doesn't bias the angle-of-sum estimator under ISI noise
        e = Z[pil_pos] * np.conj(cfg.pilots)
        step = pil_bin[1] - pil_bin[0]
        slope_tot, ph0 = 0.0, 0.0
        ew = e.copy()
        for _ in range(3):
            d = ew[1:] * np.conj(ew[:-1])
            slope = np.angle(np.sum(d)) / step
            slope_tot += slope
            ew = ew * np.exp(-1j * slope * (pil_bin - pil_bin[0]))
        ph0 = np.angle(np.sum(ew))
        corr = np.exp(-1j * (ph0 + slope_tot * (cfg.used - pil_bin[0])))
        Z = Z * corr
        # EVM on pilots after correction; also serves as this symbol's noise
        # floor so a corrupted symbol (burst, dropout, stream-end fade) gets
        # weak LLRs and degrades into erasures instead of poisoning Viterbi.
        ep = Z[pil_pos] * np.conj(cfg.pilots)
        evm2 = float(np.mean(np.abs(ep - 1) ** 2))
        evms.append(float(np.sqrt(evm2)))
        # demap data bins
        for b, p in zip(cfg.data_bins, dat_pos):
            nb = cfg.bits_per_bin[b]
            n0 = 1.0 / max(snr_bin[p], 0.1) + evm2
            llr_stream[pos:pos + nb] = qam_llr(Z[p:p+1], nb, n0)[0]
            pos += nb

    # deinterleave
    rng = np.random.default_rng(0x1EAF)
    perm = rng.permutation(len(llr_stream))
    llr = llr_stream[perm]
    # depuncture + viterbi
    n_coded_used = int(np.ceil((cfg.info_bits + 6) * 2 *
                       len(cfg.pattern) / (2 * sum(cfg.pattern)) / len(cfg.pattern)) * len(cfg.pattern))
    n_coded_full = (cfg.info_bits + 6) * 2
    llr_full = depuncture_llr(llr, cfg.pattern, n_coded_full)
    bits = viterbi_decode(llr_full[0::2], llr_full[1::2], cfg.info_bits)

    # CRC check per block
    by = np.packbits(bits[: cfg.n_blocks * (CRC_BLOCK + 4) * 8]).tobytes()
    ok = 0
    good = bytearray()
    for i in range(cfg.n_blocks):
        blk = by[i * (CRC_BLOCK + 4):(i + 1) * (CRC_BLOCK + 4)]
        if zlib.crc32(blk[:CRC_BLOCK]).to_bytes(4, "big") == blk[CRC_BLOCK:]:
            ok += 1
            good += blk[:CRC_BLOCK]
    res = {
        "ok": True, "start": start, "chirp_q": q,
        "blocks_ok": ok, "blocks_total": cfg.n_blocks,
        "payload": bytes(good),
        "evm_rms": float(np.mean(evms)),
        "snr_bin_db": 10 * np.log10(np.maximum(snr_bin, 1e-6)),
        "goodput_bps": ok * CRC_BLOCK * 8 / cfg.airtime_s,
    }
    if diag is not None:
        diag["H"] = H_s
        diag["evms"] = evms
    return res


# ---------------- self test ----------------

def _selftest():
    cfg = Config(rate="1/2", n_sym=24, bits_per_bin=None)
    print("cfg:", cfg.describe())
    payload = np.random.default_rng(3).integers(0, 256, cfg.payload_bytes,
                                                dtype=np.uint8).tobytes()
    wave = modulate_frame(cfg, payload)
    # digital loopback + noise + delay
    rx = np.concatenate([np.zeros(3000), wave.astype(float), np.zeros(2000)])
    rng = np.random.default_rng(4)
    rx = rx + rng.normal(0, 0.002, len(rx))
    res = demodulate_frame(cfg, rx)
    print(f"selftest: blocks {res['blocks_ok']}/{res['blocks_total']} "
          f"evm={res['evm_rms']:.3f} goodput={res['goodput_bps']/1000:.1f} kbps")
    assert res["blocks_ok"] == res["blocks_total"], "digital loopback failed"
    assert res["payload"] == payload
    # 16-QAM ladder check
    cfg2 = Config(rate="3/4", n_sym=24, bits_per_bin={b: 4 for b in Config().data_idx})
    print("cfg2:", cfg2.describe())
    payload2 = np.random.default_rng(5).integers(0, 256, cfg2.payload_bytes,
                                                 dtype=np.uint8).tobytes()
    wave2 = modulate_frame(cfg2, payload2)
    rx2 = np.concatenate([np.zeros(1234), wave2.astype(float)])
    rx2 = rx2 + rng.normal(0, 0.002, len(rx2))
    res2 = demodulate_frame(cfg2, rx2)
    print(f"selftest2: blocks {res2['blocks_ok']}/{res2['blocks_total']} "
          f"evm={res2['evm_rms']:.3f} goodput={res2['goodput_bps']/1000:.1f} kbps")
    assert res2["blocks_ok"] == res2["blocks_total"]
    assert res2["payload"] == payload2
    print("modem selftest OK")


if __name__ == "__main__":
    _selftest()
