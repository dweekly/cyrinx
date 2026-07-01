#!/usr/bin/env python3
"""Cross-compat spike (A1 step 1, docs/A1_AUTO_MRC.md): prove that frames
produced by the library C codec (clib.encode) decode through the Python
reference RX (modem.demodulate_frame), mono and two-mic MRC, across the MCS/CP
grid the adaptive loop can emit. This gates wiring MRC escalation into
adaptive.py — if any cell fails, diagnose the divergence, do not fall back
silently.

Cases per grid cell (all digital, no hardware, no sound):
  (a) mono:      clib frame + silence -> demodulate_frame, every block must be
                 CRC-valid AND byte-identical at its ordered position
  (b) MRC/AWGN:  two copies with independent AWGN -> demodulate_frame(rx2=...)
                 must fully decode
  (c) null-fill: complementary deep spectral notches per mic, wide enough that
                 mic0 alone fails -> MRC must still fully decode

Run: .venv/bin/python3 scratch/hw20k/xcompat_validate.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import clib
import modem as M

# (bits_per_bin, rate) x (nfft, cp): loop default geometry + the adaptive
# long-CP shape sounder.adaptive_geometry emits for reverberant cells.
MCS_GRID = [(4, "3/4"), (4, "1/2"), (2, "1/2")]
GEO_GRID = [(2048, 768), (4096, 3072)]
# One cell at the loop's exact production shape (n_sym=64); the rest run
# n_sym=16 to keep the pure-Python Viterbi fast. Geometry is n_sym-linear.
FULL_NSYM_CELL = (4, "3/4", 2048, 768)

# Complementary brick-wall notch bands (Hz). Each mic loses large chunks of
# the 1.1-23 kHz data band; together they cover it. Chirp (2-16 kHz) keeps
# enough support on mic0 (the sync channel) to lock.
NOTCH_MIC0 = [(3000, 8000), (12000, 18000)]
NOTCH_MIC1 = [(1100, 3000), (8000, 12000), (18000, 23000)]


def ordered_blocks_ok(res, payload):
    """Blocks that are CRC-valid AND byte-identical at their ordered position."""
    if not res.get("ok"):
        return 0
    return sum(1 for i, okb, data in res["blocks"]
               if okb and data == payload[i * M.CRC_BLOCK:(i + 1) * M.CRC_BLOCK])


def notch(x, sr, bands):
    """Zero the given (lo, hi) Hz bands — a deterministic deep per-mic null
    pattern for the MRC null-fill case."""
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1.0 / sr)
    for lo, hi in bands:
        X[(f >= lo) & (f <= hi)] = 0.0
    return np.fft.irfft(X, len(x))


def run_cell(bpb, rate, nfft, cp, rng):
    n_sym = 64 if (bpb, rate, nfft, cp) == FULL_NSYM_CELL else 16
    cfg = clib.make_cfg(bits_per_bin=bpb, rate=rate, n_sym=n_sym,
                        nfft=nfft, cp=cp)
    g = clib.geometry(cfg)
    mcfg = clib.modem_cfg_from_clib(cfg)  # asserts geometry parity
    payload = bytes((i * 31 + 7) & 0xFF for i in range(g.payload_bytes))
    wave = clib.encode(cfg, payload).astype(np.float64)
    rx = np.concatenate([np.zeros(4000), wave, np.zeros(3000)])
    rms = float(np.sqrt(np.mean(wave ** 2)))

    # (a) mono, clean
    ra = M.demodulate_frame(mcfg, rx)
    a_ok = ordered_blocks_ok(ra, payload) == g.n_blocks

    # (b) two-mic MRC under independent AWGN (~20 dB SNR — plumbing test)
    sig = 0.1 * rms
    rb = M.demodulate_frame(mcfg, rx + rng.normal(0, sig, len(rx)),
                            rx2=rx + rng.normal(0, sig, len(rx)))
    b_ok = ordered_blocks_ok(rb, payload) == g.n_blocks

    # (c) null-fill: mic0 alone must fail, MRC must fully decode
    m0 = notch(rx, cfg.sr, NOTCH_MIC0) + rng.normal(0, sig, len(rx))
    m1 = notch(rx, cfg.sr, NOTCH_MIC1) + rng.normal(0, sig, len(rx))
    c_mono = ordered_blocks_ok(M.demodulate_frame(mcfg, m0), payload)
    c_mrc = ordered_blocks_ok(M.demodulate_frame(mcfg, m0, rx2=m1), payload)
    c_ok = c_mono < g.n_blocks and c_mrc == g.n_blocks

    tag = "PASS" if (a_ok and b_ok and c_ok) else "FAIL"
    print(f"  bpb={bpb} r{rate} nfft={nfft} cp={cp} n_sym={n_sym} "
          f"({g.n_blocks} blocks): mono={'OK' if a_ok else 'FAIL'} "
          f"mrc_awgn={'OK' if b_ok else 'FAIL'} "
          f"nullfill mic0={c_mono}/{g.n_blocks} mrc={c_mrc}/{g.n_blocks} "
          f"-> {tag}")
    return a_ok and b_ok and c_ok


def main():
    rng = np.random.default_rng(0xA1)
    print("clib.encode -> modem.demodulate_frame cross-compat "
          "(gates A1 step 2):")
    ok = True
    for nfft, cp in GEO_GRID:
        for bpb, rate in MCS_GRID:
            ok = run_cell(bpb, rate, nfft, cp, rng) and ok
    print("XCOMPAT PASS" if ok else "XCOMPAT FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
