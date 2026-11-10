#!/usr/bin/env python3
"""SIMULATED. Feasibility spike: pass one conservative Cyrinx bulk frame through
synthetic and measured impulse responses plus additive noise, decode with the
library C codec, and count ordered verified blocks.

Every number this prints is a simulation result, not a hardware measurement.

Run (from anywhere; nothing is written inside the repo):
  CYRINX_BULK_CODEC_PATH=<scratchpad>/libcyrinxbulk.dylib PYTHONDONTWRITEBYTECODE=1 \
    /Users/dew/dev/cyrinx/.venv/bin/python -I spike_reverb.py <out.jsonl>
"""
import json
import sys
import time

import numpy as np
from scipy.signal import fftconvolve

import os
REPO = os.environ.get("CYRINX_REPO", "/Users/dew/dev/cyrinx")
sys.path.insert(0, f"{REPO}/scratch/hw20k")
sys.path.insert(0, f"{REPO}/scratch/garage")
import clib  # noqa: E402
import freqresp as F  # noqa: E402
import geometries as geo  # noqa: E402

SR = 48000
OUT = sys.argv[1] if len(sys.argv) > 1 else "/dev/stdout"


def schroeder_10(ir, peak_idx):
    return F.delay_spread(ir, peak_idx)


def polack_ir(t60_s, drr_db, seed, length_s=0.6, onset_ms=1.0):
    """Direct tap + exponentially decaying Gaussian tail (Polack's model).
    t60_s sets the decay rate; drr_db the direct-to-reverberant energy ratio."""
    rng = np.random.default_rng(seed)
    n = int(length_s * SR)
    t = np.arange(n) / SR
    tail = rng.standard_normal(n) * np.exp(-6.9078 * t / t60_s)  # 60 dB amplitude^2 decay at t60
    tail[: int(onset_ms * 1e-3 * SR)] = 0.0
    tail /= np.sqrt(np.sum(tail**2))
    h = tail * 10 ** (-drr_db / 20)
    h[0] += 1.0
    return h / np.sqrt(np.sum(h**2)), 0


def fit_polack(target_ms, drr_db, seed):
    """Bisection on T60 so the Schroeder -10 dB crossing hits target_ms."""
    lo, hi = 0.005, 5.0
    for _ in range(40):
        mid = np.sqrt(lo * hi)
        h, p = polack_ir(mid, drr_db, seed)
        if schroeder_10(h, p)["-10dB"] < target_ms:
            lo = mid
        else:
            hi = mid
    return polack_ir(np.sqrt(lo * hi), drr_db, seed) + (np.sqrt(lo * hi),)


def run_channel(cfg, geom, wave, payload, ir, snr_db, seed, sro_ppm=0.0):
    rng = np.random.default_rng(seed)
    y = fftconvolve(wave, ir)
    if sro_ppm:
        # receiver clock offset: resample by (1 + ppm) via linear interpolation
        n_out = int(len(y) / (1 + sro_ppm * 1e-6))
        y = np.interp(np.arange(n_out) * (1 + sro_ppm * 1e-6), np.arange(len(y)), y)
    sig_p = np.mean(y[: len(wave)] ** 2)
    noise = rng.standard_normal(len(y) + 12000) * np.sqrt(sig_p / 10 ** (snr_db / 10))
    rx = noise.copy()
    rx[6000 : 6000 + len(y)] += y
    t0 = time.perf_counter()
    d = clib.decode(cfg, rx.astype(np.float32))
    dt = time.perf_counter() - t0
    blocks = clib.ordered_verified_blocks(d, payload) if d else 0
    evm = float(d["evm"]) if d and d.get("evm") is not None else None
    return blocks, evm, dt


def main():
    print(f"codec: {clib.loaded_library_path()}  -- ALL RESULTS BELOW ARE SIMULATED", file=sys.stderr)
    g = geo.conservative_link(geo.MACBOOK_PRO_M4, geo.PIXEL_7A, 0.5)
    cfg = g.to_clib_cfg(clib)
    geom = clib.geometry(cfg)
    payload = np.random.default_rng(7).integers(0, 256, geom.payload_bytes, dtype=np.uint8).tobytes()
    wave = np.asarray(clib.encode(cfg, payload), dtype=np.float64)
    print(f"geometry {g.label}: band {g.f_lo_hz:.0f}-{g.f_hi_hz:.0f} Hz, QPSK r1/2, CP {g.cyclic_prefix}, "
          f"{geom.n_blocks} blocks/frame, frame {geom.frame_samples} samples "
          f"({geom.frame_samples / SR:.2f} s)", file=sys.stderr)

    channels = []
    # identity
    channels.append(("identity (digital loopback)", np.array([1.0]), 0, None))
    # measured short channel: the retained MacBook self-calibration IR
    fx = np.load(f"{REPO}/scratch/garage/fixtures/mac_selfcal_ir.npz")
    channels.append(("measured: mac_selfcal_ir.npz (Mac spk->Mac mic)", fx["ir"].astype(np.float64),
                     int(fx["peak_idx"]), None))
    # candidate reconstruction of the NF-13 desk IR (provenance unverified, see report)
    try:
        desk = np.load(sys.argv[2]) if len(sys.argv) > 2 else None
    except FileNotFoundError:
        desk = None
    if desk is not None:
        channels.append(("measured?: geo_ess.pcm reconstruction (NF-13 desk, 120 ms crop)", desk, 240, None))
    # synthetic Polack channels at controlled -10 dB spreads
    for target in (1.0, 2.0, 8.0, 16.0, 24.0, 35.8, 48.3, 82.3):
        for drr in (0.0, 6.0):
            h, p, t60 = fit_polack(target, drr, seed=11)
            channels.append((f"synthetic Polack ds10={target} ms DRR={drr} dB (T60={t60*1000:.0f} ms)", h, p, t60))

    with open(OUT, "w") as out:
        for name, ir, pk, t60 in channels:
            ds = schroeder_10(ir, pk) if len(ir) > 1 else {"-10dB": 0.0, "-15dB": 0.0, "-20dB": 0.0}
            for snr in (20.0, 30.0):
                for sro in (0.0, 20.0):
                    b, evm, dt = run_channel(cfg, geom, wave, payload, ir, snr, seed=3, sro_ppm=sro)
                    rec = {"class": "SIMULATED", "channel": name,
                           "ds_ms": {k: round(v, 2) for k, v in ds.items()},
                           "snr_db": snr, "sro_ppm": sro, "blocks": b, "of": geom.n_blocks,
                           "evm": None if evm is None else round(evm, 3), "decode_s": round(dt, 3)}
                    out.write(json.dumps(rec) + "\n")
                    out.flush()
                    print(f"[SIM] {name:72s} ds10={ds['-10dB']:6.1f} snr={snr:4.0f} sro={sro:3.0f}ppm "
                          f"-> {b:2d}/{geom.n_blocks} evm={evm if evm is None else round(evm, 3)} "
                          f"({dt*1000:.0f} ms)", file=sys.stderr)


if __name__ == "__main__":
    main()
