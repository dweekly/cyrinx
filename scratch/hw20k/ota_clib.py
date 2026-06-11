#!/usr/bin/env python3
"""Library-native OTA validation (docs/PUBLICATION.md PR 1.10): the SAME C codec
that ships in the library (via libcyrinxbulk.dylib / clib.py) encodes a frame on
the Mac, plays it over the speaker, the Pixel records it, and the C codec decodes
the capture. Reports byte-verified goodput — the honest library-native number,
not the Python reference modem.

Usage: ota_clib.py [n_runs] [bits_per_bin] [rate]
"""
import sys
import time

import numpy as np

import clib
import harness as H

SR = 48000
CRC_BLOCK = 256


def run_once(cfg, tag, run_i):
    g = clib.geometry(cfg)
    payload = bytes((i * 131 + 7 + run_i) & 0xFF for i in range(g.payload_bytes))
    wave = clib.encode(cfg, payload)
    # trailing silence so the macOS output taper doesn't kill the last symbol
    wave = np.concatenate([wave, np.zeros(int(0.3 * SR), np.float32)])
    _, path = H.mac_to_android(wave, out_name="ota_clib.pcm")
    pcm = np.fromfile(path, dtype="<i2")
    if pcm.size % 2 == 0:
        pcm = pcm.reshape(-1, 2)[:, 0]  # channel 0 of the stereo capture
    rx = pcm.astype(np.float32) / 32768.0
    r = clib.decode(cfg, rx)
    if r is None:
        return {"ok": 0, "total": g.n_blocks, "match": False, "gp_kbps": 0.0, "evm": 9.99}
    span = g.frame_samples / SR
    verified = r["blocks_ok"]  # whole-block CRC-valid count
    match = r["payload"] == payload
    gp = verified * CRC_BLOCK * 8 / span / 1000.0
    return {"ok": verified, "total": r["blocks_total"], "match": match,
            "gp_kbps": gp, "evm": r["evm"], "peak": float(np.abs(rx).max())}


def main(n=1, bpb=4, rate="3/4"):
    H.mac_set_output_volume(100)
    cfg = clib.make_cfg(bits_per_bin=bpb, rate=rate, n_sym=64)
    g = clib.geometry(cfg)
    print(f"library-native OTA  Mac->Pixel  {bpb} bits/bin r{rate}  "
          f"{g.payload_bytes} B/frame, {g.frame_samples/SR:.2f}s airtime")
    gps = []
    for i in range(n):
        r = run_once(cfg, "m2p", i)
        gps.append(r["gp_kbps"])
        print(f"  [{i+1}/{n}] blocks {r['ok']}/{r['total']} match={r['match']} "
              f"peak={r.get('peak',0):.3f} evm={r['evm']:.3f} "
              f"goodput={r['gp_kbps']:.1f} kbps")
    if n > 1:
        a = np.array(gps)
        print(f"  goodput kbps min/med/max = {a.min():.1f}/{np.median(a):.1f}/{a.max():.1f}")


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    bpb = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    rate = sys.argv[3] if len(sys.argv) > 3 else "3/4"
    main(n, bpb, rate)
