#!/usr/bin/env python3
"""Replay retained ladder captures through the research decoder, with nothing hinted.

resample_replay.py and ladder.py --ppm were told which clock offsets to try.
This gives the decoder nothing. Each retained capture is scored four ways,
exactly as the live run scored it (ordered verified blocks):

  live      as transmitted, shipped decoder (read from the ladder record)
  hinted    resampled by an offset chosen from a supplied list (from the record)
  fit       no clock correction; coherent pilot timing fit only
  blind     clock offset estimated from the frame's own training symbols and
            removed; shipped pilot fit
  both      blind clock correction and the coherent pilot fit

Usage: blind_replay.py <ladder-*.jsonl> [more.jsonl ...] [--json OUT]
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
import clock_offset  # noqa: E402
import linkprobe  # noqa: E402
import resample_replay  # noqa: E402


def score(cfg, rx, payloads, geom, correct_clock=True):
    """linkprobe.score through the research dylib: (verified, per_frame, best_evm, ppm per frame)."""
    window = geom.frame_samples + linkprobe.SEARCH_SLACK
    best = [0] * len(payloads)
    ppm = [None] * len(payloads)
    best_evm = None
    for start in range(0, max(1, len(rx) - geom.frame_samples), linkprobe.SEARCH_STEP):
        segment = rx[start:start + window]
        decoded = clock_offset.decode(cfg, segment) if correct_clock else clib.decode(cfg, segment)
        if decoded is None:
            continue
        evm = decoded["evm"]
        if np.isfinite(evm) and (best_evm is None or evm < best_evm):
            best_evm = float(evm)
        for i, payload in enumerate(payloads):
            count = clib.ordered_verified_blocks(decoded, payload)
            if count > best[i]:
                best[i], ppm[i] = count, decoded.get("ppm")
    return sum(best), best, best_evm, ppm


MODES = (("fit", False, clock_offset.FIT_COHERENT), ("blind", True, clock_offset.FIT_SHIPPED),
         ("both", True, clock_offset.FIT_COHERENT))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("jsonl", nargs="+")
    ap.add_argument("--json", default=None, help="write every row's result here")
    a = ap.parse_args()
    if os.path.realpath(clib.loaded_library_path()) != os.path.realpath(clock_offset.library_path()):
        raise SystemExit(f"set {clib.CODEC_PATH_ENV} to the research dylib "
                         f"({clock_offset.library_path()}): the fit switch must reach the decoder")
    out = []
    totals = {}
    for path in a.jsonl:
        captures = np.load(path[:-6] + ".npz")
        rows = [json.loads(line) for line in open(path)]
        print(f"\n{os.path.basename(path)}")
        print(f"{'cell':<28}{'live':>9}{'hinted':>9}{'fit':>9}{'blind':>9}{'both':>9}"
              "  evm(both)  ppm per decoded frame (both)")
        for row in rows:
            if "error" in row:
                continue
            cfg = clib.make_cfg(bits_per_bin=row["bits_per_bin"], rate=row["rate"], n_sym=row["n_sym"],
                                f_lo=row["f_lo"], f_hi=row["f_hi"], nfft=row["nfft"], cp=row["cp"],
                                amp=row["amp"], pilot_every=row["pilot_every"])
            _, payloads, geom = linkprobe.build(cfg, seed=row["seed"])
            key = resample_replay.capture_key(row)
            rx = np.asarray(captures[key], float)
            sched = row["scheduled"]
            hinted = row.get("corrected", {}).get("verified")
            result = {"file": os.path.basename(path), "cell": key, "name": row["name"],
                      "scheduled": sched, "airtime_s": row["airtime_s"], "live": row["verified"],
                      "hinted": hinted}
            try:
                for mode, correct_clock, fit in MODES:
                    clock_offset.set_pilot_fit(fit)
                    verified, per_frame, evm, ppm = score(cfg, rx, payloads, geom, correct_clock)
                    result[mode] = {"verified": verified, "per_frame": per_frame, "best_evm": evm,
                                    "ppm": ppm}
            finally:
                clock_offset.set_pilot_fit(clock_offset.FIT_SHIPPED)
            both = result["both"]
            shown = " ".join("  -  " if p is None else f"{p:+5.1f}" for p in both["ppm"])
            evm = both["best_evm"]
            print(f"{key:<28}{row['verified']:>5}/{sched:<3}"
                  + (f"{hinted:>5}/{sched:<3}" if hinted is not None else f"{'-':>9}")
                  + "".join(f"{result[m]['verified']:>5}/{sched:<3}" for m, _, _ in MODES)
                  + f"  {evm if evm is None else round(evm, 3)!s:<9}  {shown}", flush=True)
            out.append(result)
            t = totals.setdefault((os.path.basename(path), row["name"]),
                                  {"sched": 0, "live": 0, "hinted": 0, "has_hint": hinted is not None,
                                   **{m: 0 for m, _, _ in MODES}})
            t["sched"] += sched
            t["live"] += row["verified"]
            t["hinted"] += hinted or 0
            for mode, _, _ in MODES:
                t[mode] += result[mode]["verified"]
            if a.json:
                with open(a.json, "w") as fh:
                    json.dump(out, fh, indent=1)
    print("\ntotals per cell (verified blocks of scheduled)")
    for (name, cell), t in totals.items():
        print(f"  {name[:48]:<50}{cell:<26}/{t['sched']:<4} live {t['live']:<4} "
              f"hinted {t['hinted'] if t['has_hint'] else '-':<4} "
              + " ".join(f"{m} {t[m]:<4}" for m, _, _ in MODES))


if __name__ == "__main__":
    main()
