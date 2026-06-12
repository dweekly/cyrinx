#!/usr/bin/env python3
"""Measure one downlink cell for the distance/ambient sweep, non-interactively
(no positioning prompt -- the operator positions between invocations). Logs the
through-line: passive room tone, sounder-predicted MCS, and the library-native
achieved ceiling, to data/sweep.jsonl.

Usage:  sweep_cell.py <cell_label> [reps]     e.g.  sweep_cell.py dist_20cm 3
"""
import json
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H
import sounder as S
import clib
import freqresp as FR

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
LADDER = [("qpsk", 2, "1/2"), ("medium", 4, "1/2"), ("fast", 4, "3/4")]
TIER_ORDER = {"qpsk": 0, "medium": 1, "fast": 2}


def run(cell, reps=3):
    H.mac_set_output_volume(100)
    H.mac_set_input_volume(22)

    def send(w):
        _, p = H.mac_to_android(w, out_name="sweep_m2p.pcm")
        return H.load_pcm16(p, channels=2)[:, 0]

    print(f"=== cell '{cell}' (Mac->Pixel), {reps} reps/tier ===")
    # passive room tone (noise floor; the user's pre-transmission screen)
    rt = FR.analyze_room_tone(H.load_pcm16(
        H.android_record(2.0, out_name="sweep_rt.pcm")[1], channels=2)[:, 0])
    print(f"  room tone: in-band RMS={rt['rms_inband']:.2e}  "
          f"near-US interferer={rt['near_ultrasonic_interferer']}")

    rec, an = S.sound_channel(send)
    print(f"  sounder: tier={rec['tier']} medSNR={rec['median_snr_db']}dB "
          f"ds15={rec['delay_spread_ms_15']}ms peakUsable={rec['usable_bin_frac']*100:.0f}%")

    per = {}
    for tier, bpb, rate in LADDER:
        cfg = clib.make_cfg(bits_per_bin=bpb, rate=rate, n_sym=64)
        g = clib.geometry(cfg)
        ver = tot = 0
        evs = []
        for _ in range(reps):
            payload = bytes((i * 31 + 7) & 0xFF for i in range(g.payload_bytes))
            wave = clib.encode(cfg, payload)
            d = clib.decode(cfg, np.asarray(send(wave), dtype=np.float32))
            if d:
                ver += d["blocks_ok"] if d["payload"] == payload else 0
                tot += d["blocks_total"]
                evs.append(d["evm"])
        span = len(wave) / cfg.sr
        frac = ver / tot if tot else 0.0
        per[tier] = {"verified": ver, "total": tot, "verified_frac": round(frac, 3),
                     "evm_med": round(float(np.median(evs)), 3) if evs else None,
                     "goodput_kbps": round(g.info_bits * frac / span / 1000, 1)}
        print(f"  {tier:6s} {1<<bpb:2d}-QAM r{rate}: {ver:3d}/{tot:3d} "
              f"({frac*100:3.0f}%) EVM={per[tier]['evm_med']} "
              f"-> {per[tier]['goodput_kbps']} kbps")

    ach = None
    for t, _, _ in LADDER:
        if per[t]["total"] and per[t]["verified"] / per[t]["total"] >= 0.95:
            if ach is None or TIER_ORDER[t] > TIER_ORDER[ach]:
                ach = t
    best_kbps = max((per[t]["goodput_kbps"] for t in per if per[t]["verified_frac"] >= 0.95),
                    default=0.0)
    pi, ai = TIER_ORDER.get(rec["tier"]), TIER_ORDER.get(ach)
    verdict = ("floor" if rec["noncoherent"] else
               "optimistic" if ai is None or (pi is not None and pi > ai) else
               "exact" if pi == ai else "conservative")
    print(f"  => predicted={rec['tier']} achieved={ach} ({best_kbps} kbps) verdict={verdict}")

    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, "sweep.jsonl"), "a") as f:
        f.write(json.dumps({
            "cell": cell, "path": "mac2pixel", "reps": reps,
            "room_tone": rt,
            "sounder": {"predicted_tier": rec["tier"], "median_snr_db": rec["median_snr_db"],
                        "delay_spread_ms_15": rec["delay_spread_ms_15"],
                        "usable_bin_frac": rec["usable_bin_frac"]},
            "ladder": per, "achieved_tier": ach, "best_goodput_kbps": best_kbps,
            "prediction_verdict": verdict}) + "\n")
    print("  logged to data/sweep.jsonl")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    run(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 3)
