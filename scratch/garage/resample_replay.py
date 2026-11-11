#!/usr/bin/env python3
"""Replay retained ladder captures through the C decoder after correcting clock offset.

The two devices' audio clocks differ by a fixed fraction (parts per million), so
a multi-second frame slides against the receiver's sample grid. This resamples
each retained capture by a candidate offset and decodes it again with the
unmodified C codec, scoring ordered verified blocks exactly as the live run did.
It is an offline replay of a real capture: the audio is hardware, the correction
is applied afterwards.

Usage: resample_replay.py <ladder-*.jsonl> --ppm 0,28 [--guard-lib PATH]
"""

import argparse
import json
import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "hw20k"))
sys.path.insert(0, _HERE)

import clib  # noqa: E402
import linkprobe  # noqa: E402


def resample(rx, ppm):
    """Read rx at times scaled by (1 + ppm * 1e-6), by band-limited (sinc) interpolation."""
    if ppm == 0:
        return rx
    n = len(rx)
    t = np.arange(n) * (1.0 + ppm * 1e-6)
    base = np.floor(t).astype(np.int64)
    frac = t - base
    half = 16
    out = np.zeros(n)
    for k in range(-half + 1, half + 1):
        idx = base + k
        ok = (idx >= 0) & (idx < n)
        x = frac - k
        w = np.sinc(x) * (0.5 + 0.5 * np.cos(np.pi * x / half))
        out[ok] += rx[idx[ok]] * w[ok]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("jsonl")
    ap.add_argument("--ppm", default="0,28")
    a = ap.parse_args()
    ppms = [float(p) for p in a.ppm.split(",")]
    captures = np.load(a.jsonl[:-6] + ".npz")
    rows = [json.loads(line) for line in open(a.jsonl) if "error" not in line]
    print(f"{'cell':<22}{'live':>9}" + "".join(f"{p:>+10.1f} ppm" for p in ppms))
    results = []
    for row in rows:
        cfg = clib.make_cfg(bits_per_bin=row["bits_per_bin"], rate=row["rate"], n_sym=row["n_sym"],
                            f_lo=row["f_lo"], f_hi=row["f_hi"], nfft=row["nfft"], cp=row["cp"],
                            amp=row["amp"], pilot_every=row["pilot_every"])
        _, payloads, geom = linkprobe.build(cfg, seed=row["seed"])
        rx = np.asarray(captures[row["name"]], float)
        cells = []
        for ppm in ppms:
            verified, per_frame, evm = linkprobe.score(cfg, resample(rx, ppm), payloads, geom)
            cells.append({"ppm": ppm, "verified": verified, "per_frame": per_frame, "best_evm": evm})
        results.append({"cell": row["name"], "scheduled": row["scheduled"], "live": row["verified"],
                        "airtime_s": row["airtime_s"], "replays": cells})
        print(f"{row['name']:<22}{row['verified']:>5}/{row['scheduled']:<3}"
              + "".join(f"{c['verified']:>6}/{row['scheduled']:<3}{c['best_evm'] or float('nan'):5.2f}" for c in cells),
              flush=True)
    with open(a.jsonl[:-6] + ".resample.json", "w") as fh:
        json.dump(results, fh, indent=1)


if __name__ == "__main__":
    main()
