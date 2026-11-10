#!/usr/bin/env python3
"""SIMULATED. Does a geometric room model of a plausible two-car garage, with the
devices ~30 cm apart on a bench, produce the measured -10 dB strong-tap spreads
(48.3 ms Mac->Pixel, 82.3 ms Pixel->Mac)? Room dimensions and materials are
ASSUMPTIONS (the garage was not measured); this asks whether any plausible
parameter choice lands near the measurement, and how much the answer moves.

Uses the same Schroeder convention as scratch/hw20k/freqresp.delay_spread
(re-implemented here so this venv does not import the repo)."""
import itertools
import time

import numpy as np
import pyroomacoustics as pra

SR = 48000


def ds(ir):
    pk = int(np.argmax(np.abs(ir)))
    e = ir[pk:] ** 2
    edc = np.cumsum(e[::-1])[::-1]
    edc_db = 10 * np.log10(edc / edc[0] + 1e-300)
    out = {}
    for db in (-10, -15, -20):
        idx = np.where(edc_db < db)[0]
        out[db] = idx[0] / SR * 1000 if len(idx) else float("nan")
    return out


rooms = {"garage 6.0x6.0x2.7 m": [6.0, 6.0, 2.7], "garage 6.5x7.0x3.0 m": [6.5, 7.0, 3.0]}
absorptions = {"bare concrete/drywall (energy abs 0.03)": 0.03, "mixed, shelves/car (0.10)": 0.10,
               "furnished room (0.25)": 0.25}
for (rname, dims), (aname, a) in itertools.product(rooms.items(), absorptions.items()):
    for sep in (0.10, 0.30):
        t0 = time.perf_counter()
        room = pra.ShoeBox(dims, fs=SR, materials=pra.Material(a), max_order=60,
                           air_absorption=True, ray_tracing=False)
        src = [1.5, 2.0, 0.95]  # laptop speaker on a 0.9 m bench, 1.5 m from a wall
        room.add_source(src)
        room.add_microphone([src[0] + sep, src[1], 0.92])
        room.compute_rir()
        ir = np.asarray(room.rir[0][0])
        dt = time.perf_counter() - t0
        d = ds(ir)
        rt60 = pra.experimental.measure_rt60(ir, fs=SR)
        print(f"[SIM] {rname:22s} {aname:40s} sep={sep*100:3.0f} cm  RT60={rt60*1000:5.0f} ms  "
              f"ds10={d[-10]:5.1f} ds15={d[-15]:5.1f} ds20={d[-20]:5.1f} ms  ({dt:.1f} s)")
