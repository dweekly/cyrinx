#!/usr/bin/env python3
"""Over-the-air ladder: does a longer guard or a narrower band decode at range?

Runs a list of coherent OFDM configurations through the same build, send and
scoring path as linkprobe.py, one directed link at a time, and retains every
capture. Each cell is two frames with independently seeded payloads, scored by
ordered verified blocks, so a cell either carried bytes or it did not.

Results are appended to a .jsonl as each cell finishes, so a partial run is
usable, and captures are saved per cell.

Usage: ladder.py --direction down|up --label NAME [--cells a,b,c] [--amp 0.5]
"""

import argparse
import json
import os
import sys
import time

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "hw20k"))
sys.path.insert(0, _HERE)

import acquire  # noqa: E402
import clib  # noqa: E402
import crosscal  # noqa: E402
import freqresp as F  # noqa: E402
import harness as H  # noqa: E402
import linkprobe  # noqa: E402
import resample_replay  # noqa: E402
import retain  # noqa: E402

TARGET_FRAME_S = 3.5


def cell(name, bits, f_lo, f_hi, nfft, cp, rate="1/2", pilot_every=8):
    sym_s = (nfft + cp) / F.SR
    n_sym = max(4, int(TARGET_FRAME_S / sym_s))
    return dict(name=name, bits_per_bin=bits, rate=rate, f_lo=f_lo, f_hi=f_hi,
                nfft=nfft, cp=cp, n_sym=n_sym, pilot_every=pilot_every)


DOWN = [  # Mac speaker -> phone microphone
    cell("base-qpsk-full-g16", 2, 300, 23000, 2048, 768),
    cell("qpsk-full-g43", 2, 300, 23000, 4096, 2048),
    cell("bpsk-full-g43", 1, 300, 23000, 4096, 2048),
    cell("qpsk-hi16-23-g16", 2, 16000, 23000, 2048, 768),
    cell("qpsk-hi16-23-g43", 2, 16000, 23000, 4096, 2048),
    cell("bpsk-hi16-23-g43", 1, 16000, 23000, 4096, 2048),
    cell("qpsk-hi12-23-g43", 2, 12000, 23000, 4096, 2048),
    cell("qpsk-hi16-23-g85", 2, 16000, 23000, 4096, 4096),
    cell("qpsk-hi8-23-g85", 2, 8000, 23000, 8192, 4096),
    cell("qpsk-full-g85", 2, 300, 23000, 8192, 4096),
    cell("16qam-hi16-23-g85", 4, 16000, 23000, 4096, 4096),
    cell("qpsk-full-g171", 2, 300, 23000, 8192, 8192),
]

DOWN2 = [  # second pass, scored with clock-offset correction
    cell("qpsk-full-g85", 2, 300, 23000, 8192, 4096),
    cell("16qam-full-g85", 4, 300, 23000, 8192, 4096),
    cell("16qam-full-g171", 4, 300, 23000, 8192, 8192),
    cell("16qam-r34-full-g171", 4, 300, 23000, 8192, 8192, rate="3/4"),
    cell("64qam-full-g171", 6, 300, 23000, 8192, 8192),
    cell("qpsk-r34-full-g85", 2, 300, 23000, 8192, 4096, rate="3/4"),
    cell("qpsk-full-g43", 2, 300, 23000, 4096, 2048),
    cell("qpsk-full-g64", 2, 300, 23000, 8192, 3072),
    cell("16qam-hi8-23-g85", 4, 8000, 23000, 8192, 4096),
    cell("16qam-r34-hi16-23-g85", 4, 16000, 23000, 4096, 4096, rate="3/4"),
    cell("64qam-hi16-23-g85", 6, 16000, 23000, 4096, 4096),
    cell("qpsk-full-g85-n16k", 2, 300, 23000, 16384, 4096),
]

UP2 = [
    cell("qpsk-full-g85", 2, 300, 17000, 8192, 4096),
    cell("qpsk-full-g171", 2, 300, 17000, 8192, 8192),
    cell("bpsk-full-g85", 1, 300, 17000, 8192, 4096),
    cell("qpsk-lo-12k-g171", 2, 300, 12000, 8192, 8192),
    cell("qpsk-lo-12k-g85", 2, 300, 12000, 8192, 4096),
    cell("16qam-lo-12k-g171", 4, 300, 12000, 8192, 8192),
    cell("qpsk-full-g171-n16k", 2, 300, 17000, 16384, 8192),
    cell("16qam-full-g171", 4, 300, 17000, 8192, 8192),
]

DOWN3 = [  # third pass: single exploratory runs around the cells that repeated well
    cell("16qam-hi8-23-g171", 4, 8000, 23000, 8192, 8192),
    cell("16qam-full-g171-n16k", 4, 300, 23000, 16384, 8192),
    cell("16qam-hi8-23-g85-n16k", 4, 8000, 23000, 16384, 4096),
    cell("64qam-hi8-23-g85", 6, 8000, 23000, 8192, 4096),
    cell("16qam-r34-hi8-23-g85", 4, 8000, 23000, 8192, 4096, rate="3/4"),
    cell("16qam-hi4-23-g85", 4, 4000, 23000, 8192, 4096),
    cell("16qam-hi12-23-g85", 4, 12000, 23000, 8192, 4096),
    cell("16qam-hi12-23-g43", 4, 12000, 23000, 4096, 2048),
]

UP3 = [
    cell("16qam-full-g171-n16k", 4, 300, 17000, 16384, 8192),
    cell("qpsk-r34-full-g171-n16k", 2, 300, 17000, 16384, 8192, rate="3/4"),
    cell("qpsk-full-g85-n16k", 2, 300, 17000, 16384, 4096),
    cell("qpsk-full-g341-n16k", 2, 300, 17000, 16384, 16384),
    cell("16qam-lo-12k-g171-n16k", 4, 300, 12000, 16384, 8192),
    cell("qpsk-lo-8k-g171-n16k", 2, 300, 8000, 16384, 8192),
]

UP = [  # phone speaker -> Mac microphone
    cell("base-qpsk-full-g16", 2, 300, 17000, 2048, 768),
    cell("qpsk-full-g43", 2, 300, 17000, 4096, 2048),
    cell("bpsk-full-g43", 1, 300, 17000, 4096, 2048),
    cell("qpsk-hi12-17-g43", 2, 12000, 17000, 4096, 2048),
    cell("qpsk-hi12-17-g85", 2, 12000, 17000, 8192, 4096),
    cell("bpsk-hi12-17-g85", 1, 12000, 17000, 8192, 4096),
    cell("qpsk-full-g85", 2, 300, 17000, 8192, 4096),
    cell("qpsk-full-g171", 2, 300, 17000, 8192, 8192),
    cell("bpsk-full-g171", 1, 300, 17000, 8192, 8192),
    cell("qpsk-hi12-17-g171", 2, 12000, 17000, 8192, 8192),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--direction", choices=("down", "up"), required=True)
    ap.add_argument("--set", choices=("first", "second", "third"), default="first")
    ap.add_argument("--repeat", type=int, default=1, help="run the selected cells this many times")
    ap.add_argument("--ppm", default=None,
                    help="comma-separated clock offsets (ppm) to try on each capture; the one with "
                         "the lowest decoder pilot EVM is scored. The choice never sees the payload.")
    ap.add_argument("--label", required=True)
    ap.add_argument("--amp", type=float, default=0.5)
    ap.add_argument("--cells", default=None, help="comma-separated cell names; default all")
    ap.add_argument("--outdir", default=None)
    a = ap.parse_args()
    outdir = retain.session_dir(a.outdir)

    serial = H.validated_android_serial()
    level, readback = crosscal.media_volume()
    cells = {("down", "first"): DOWN, ("up", "first"): UP,
             ("down", "second"): DOWN2, ("up", "second"): UP2,
             ("down", "third"): DOWN3, ("up", "third"): UP3}[(a.direction, a.set)]
    if a.cells:
        wanted = a.cells.split(",")
        cells = [c for c in cells if c["name"] in wanted]
    if a.repeat > 1:
        cells = [{**c, "run": r} for r in range(a.repeat) for c in cells]
    stamp = time.strftime("%Y%m%dT%H%M%S")
    base = os.path.join(outdir, f"ladder-{a.label}-{a.direction}-{stamp}")
    print(f"phone {serial}; media volume {readback}; {len(cells)} cells -> {base}.jsonl")
    captures = {}
    started = time.time()
    for i, c in enumerate(cells):
        cfg = clib.make_cfg(bits_per_bin=c["bits_per_bin"], rate=c["rate"], n_sym=c["n_sym"],
                            f_lo=c["f_lo"], f_hi=c["f_hi"], nfft=c["nfft"], cp=c["cp"],
                            amp=a.amp, pilot_every=c["pilot_every"])
        try:
            wave, payloads, geom = linkprobe.build(cfg, seed=100 + i)
        except Exception as exc:  # a geometry the codec rejects is a result, not a crash
            row = {**c, "direction": a.direction, "error": f"build: {exc}"}
            print(f"[{i + 1}/{len(cells)}] {c['name']}: {row['error']}")
            with open(base + ".jsonl", "a") as fh:
                fh.write(json.dumps(row) + "\n")
            continue
        token = crosscal.run_token()

        def send_down(w):
            name = f"ld_{token}_{i}.pcm"
            dur = len(w) / F.SR + 0.7 + 1.0 + 8.0
            H.android_record_start(dur, channels=2, source="unprocessed", out_name=name)
            time.sleep(0.7)
            acquire.play_and_record(w, F.SR, acquire.MAC_SPEAKERS, acquire.MAC_MICROPHONE,
                                    tail_s=1.0)
            H.android_record_finish(dur, out_name=name)
            return crosscal._pull_capture(name)

        def send_up(w):
            H.android_prepare(media_volume=None)
            return np.asarray(
                H.android_to_mac(w.astype(np.float32), channels=1, post_s=1.0), dtype=np.float64
            ).reshape(-1)

        title = (f"[{i + 1}/{len(cells)}] {c['name']}: {c['f_lo']}-{c['f_hi']} Hz, "
                 f"{c['bits_per_bin']} bit/bin r{c['rate']}, NFFT {c['nfft']} CP {c['cp']} "
                 f"({c['cp'] / F.SR * 1e3:.1f} ms), {c['n_sym']} sym, {geom.n_blocks} blocks/frame")
        result, rx = linkprobe.probe(title, cfg, wave, payloads, geom,
                                     send_down if a.direction == "down" else send_up)
        if a.ppm:
            best = None
            for ppm in [float(p) for p in a.ppm.split(",")]:
                v, per_frame, evm = linkprobe.score(cfg, resample_replay.resample(rx, ppm), payloads, geom)
                if evm is not None and (best is None or evm < best[0]):
                    best = (evm, ppm, v, per_frame)
            if best is not None:
                evm, ppm, v, per_frame = best
                result["corrected"] = {"ppm": ppm, "best_evm": evm, "verified": v, "per_frame": per_frame,
                                       "bps": v * 256 * 8 / result["airtime_s"]}
                print(f"  clock-corrected ({ppm:+.1f} ppm, chosen by pilot EVM {evm:.3f}): "
                      f"{v}/{result['scheduled']} blocks -> {result['corrected']['bps']:.0f} bps")
        row = {**c, "direction": a.direction, "blocks_per_frame": geom.n_blocks,
               "payload_bytes": geom.payload_bytes, "frame_s": geom.frame_samples / F.SR,
               "seed": 100 + i, "amp": a.amp, "media_volume": level, **result}
        with open(base + ".jsonl", "a") as fh:
            fh.write(json.dumps(row) + "\n")
        captures[c["name"] + (f"#{c['run']}" if "run" in c else "")] = rx.astype(np.float32)
        np.savez_compressed(base + ".npz", **captures)
        elapsed = time.time() - started
        print(f"  elapsed {elapsed:.0f} s, about {elapsed / (i + 1) * (len(cells) - i - 1):.0f} s left")
    print(f"\nretained: {base}.jsonl / .npz")


if __name__ == "__main__":
    main()
