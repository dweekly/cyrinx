#!/usr/bin/env python3
"""Is the clock-offset failure a slide between symbols, or something within a symbol?

A clock offset does two things to a long OFDM frame. Between symbols it slides
each one a fraction of a sample against the last, which is a phase ramp across
frequency. Within a symbol it stretches the waveform, which leaks energy between
neighbouring bins. Resampling the capture removes both. This experiment removes
only the first, in the frequency domain, in a patched copy of the Python
reference receiver: it measures the ramp between the two training symbols,
undoes it before averaging them, and pre-rotates each data symbol by the
predicted slide. Comparing "as captured", "slide fix" and "resampled" on the
same retained frame separates the two effects.

The patch is applied to the source of scratch/hw20k/modem.py at import time, so
this file fails loudly if that receiver changes shape.

Usage: slide_experiment.py <ladder-*.jsonl> <ppm> <cell> [<cell> ...]
A cell from a repeated run is named cell#run, e.g. qpsk-full-g85#2.
"""

import json
import os
import sys
import types

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_HW = os.path.join(os.path.dirname(_HERE), "hw20k")
sys.path.insert(0, _HW)
sys.path.insert(0, _HERE)

import clib  # noqa: E402
import resample_replay  # noqa: E402

PATCHES = [
    ('''        H_list.append((Hs[0] + Hs[1]) / 2)
        nvk = np.convolve(np.abs(Hs[0] - Hs[1]) ** 2 / 2, np.ones(9) / 9,''',
     '''        if SLIDE_FIX:
            f_used = cfg.used * (cfg.sr / cfg.nfft)
            cross = Hs[1] * np.conj(Hs[0])
            taus = np.linspace(-2.0, 2.0, 801) / cfg.sr
            score = [abs(np.sum(cross * np.exp(2j * np.pi * f_used * t))) for t in taus]
            SLIDE_STATE["tau"] = taus[int(np.argmax(score))]
            SLIDE_STATE["f"] = f_used
            Hs = [Hs[0], Hs[1] * np.exp(2j * np.pi * f_used * SLIDE_STATE["tau"])]
        H_list.append((Hs[0] + Hs[1]) / 2)
        nvk = np.convolve(np.abs(Hs[0] - Hs[1]) ** 2 / 2, np.ones(9) / 9,'''),
    ('''            Ys.append(Y[cfg.used])
        if len(chans) == 1:''',
     '''            Yu = Y[cfg.used]
            if SLIDE_FIX:
                Yu = Yu * np.exp(2j * np.pi * SLIDE_STATE["f"] * SLIDE_STATE["tau"] * (2 + s))
            Ys.append(Yu)
        if len(chans) == 1:'''),
    ("def demodulate_frame(", "SLIDE_FIX = False\nSLIDE_STATE = {}\n\n\ndef demodulate_frame("),
]


def patched_modem():
    source = open(os.path.join(_HW, "modem.py")).read()
    for old, new in PATCHES:
        if source.count(old) != 1:
            raise SystemExit("modem.py no longer matches this experiment's patch points")
        source = source.replace(old, new)
    module = types.ModuleType("modem_slide")
    module.__file__ = os.path.join(_HW, "modem.py")
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    return module


def main(path, ppm, names):
    M = patched_modem()
    rows = {resample_replay.capture_key(row): row for row in map(json.loads, open(path))}
    captures = np.load(path[:-6] + ".npz")
    for name in names:
        row = rows[name]
        cfg = clib.make_cfg(bits_per_bin=row["bits_per_bin"], rate=row["rate"], n_sym=row["n_sym"],
                            f_lo=row["f_lo"], f_hi=row["f_hi"], nfft=row["nfft"], cp=row["cp"],
                            amp=row["amp"], pilot_every=row["pilot_every"])
        m = clib.modem_cfg_from_clib(cfg)
        geom = clib.geometry(cfg)
        rx = np.asarray(captures[name], float)
        start, _ = M.find_chirp(rx[: geom.frame_samples + 100000], chirp=m.chirp_wave)
        expected = ppm * 1e-6 * (row["nfft"] + row["cp"])
        for label, fix, applied in (("as captured", False, 0.0), ("slide fix", True, 0.0),
                                    ("resampled", False, ppm)):
            M.SLIDE_FIX = fix
            x = resample_replay.resample(rx, applied)
            res = M.demodulate_frame(m, x[max(0, start - 2000): start + geom.frame_samples + 4000])
            slide = f"{M.SLIDE_STATE['tau'] * cfg.sr:+.3f}" if fix else "      "
            print(f"{name:<18} {label:<12} blocks {res.get('blocks_ok')}/{res.get('blocks_total')}"
                  f"  evm {res.get('evm_rms', float('nan')):.3f}  measured slide/symbol {slide}"
                  f"  (expected {expected:+.3f} samples)  {res.get('err', '')}")


if __name__ == "__main__":
    main(sys.argv[1], float(sys.argv[2]), sys.argv[3:])
