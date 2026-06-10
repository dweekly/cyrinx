#!/usr/bin/env python3
"""Geometry-robustness sweep: one labeled position per invocation.

Mac left speaker -> iPhone mic, 16-QAM r3/4 (the headline profile), ordered-
verified goodput. Run once per physical position; the operator repositions the
phone between invocations. Appends one record to data/geometry.jsonl so the
paper can show goodput is not a palm-rest-contact-only artifact.

Usage: geometry_sweep.py <position_label> [n_frames]
e.g.   geometry_sweep.py contact_palmrest
       geometry_sweep.py desk_beside
       geometry_sweep.py foam_isolated
       geometry_sweep.py faceup_palmrest / facedown / edge_on / rot180 / right_palmrest
"""
import json
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H
import ios_harness as I
import modem as M

SR = 48000
N_SYM = 64
SEED_BASE = 1000
F_LO, F_HI = 1100.0, 23000.0   # Mac->iPhone band


def make_cfg():
    base = M.Config(F_LO, F_HI, nfft=2048, cp=768, sr=SR)
    return M.Config(F_LO, F_HI, rate="3/4", n_sym=N_SYM, amp=0.7, cp=768,
                    nfft=2048, sr=SR, track_alpha=0.35,
                    bits_per_bin={b: 4 for b in base.data_idx})


def run(label, n_frames=5):
    cfg = make_cfg()
    payloads = [M.DetRng(SEED_BASE + i).bytes(cfg.payload_bytes) for i in range(n_frames)]
    waves = [M.modulate_frame(cfg, p) for p in payloads]
    gap = np.zeros(int(0.25 * SR), dtype=np.float32)
    parts = []
    for i, w in enumerate(waves):
        parts.append(w)
        if i < n_frames - 1:
            parts.append(gap)
    parts.append(np.zeros(SR // 3, dtype=np.float32))
    tx = np.concatenate(parts)

    H.mac_set_output_volume(100)
    I.mac_to_ios(tx, out_name="geo.pcm")
    d = I.ios_bulk_decode(out_name="geo.pcm", channels=1, f_lo=F_LO, f_hi=F_HI,
                          n_sym=N_SYM, n_payloads=n_frames, seed_base=SEED_BASE)
    verified = int(d.get("verified_ordered_blocks", d.get("verified", 0)))
    span = float(d.get("span_s", 0)) or None
    total = n_frames * cfg.n_blocks
    evms = [f.get("evm") for f in d.get("frames", []) if isinstance(f, dict) and f.get("evm")]
    rec = {
        "ts": __import__("time").strftime("%Y-%m-%dT%H:%M:%S"),
        "position": label, "device": "iphone17pm", "direction": "m2i",
        "n_frames": n_frames, "verified_ordered_blocks": verified,
        "total_blocks": total,
        "evm_mean": round(float(np.mean(evms)), 4) if evms else None,
        "goodput_kbps": round(verified * M.CRC_BLOCK * 8 / span / 1000, 2) if span else 0.0,
        "span_s": round(span, 2) if span else None,
    }
    with open(os.path.join(H.DATA, "geometry.jsonl"), "a") as fh:
        fh.write(json.dumps(rec) + "\n")
    print(f"[{label}] verified={verified}/{total} blocks  "
          f"goodput={rec['goodput_kbps']} kbps  evm_mean={rec['evm_mean']}")
    return rec


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: geometry_sweep.py <position_label> [n_frames]")
    run(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 5)
