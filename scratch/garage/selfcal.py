#!/usr/bin/env python3
"""Self-calibration and environmental sampling for one endpoint (ADR 0006).

Phases 1 and 2 of session initiation, both purely local: measure this device's
own speaker against its own microphone, and record the room-tone reference the
delay-spread readout requires at the same gain.

Neither device is made the system default, and playback level is set by the
sweep amplitude rather than by driving the machine's volume to 100 -- the
session fixes one comfortable level and holds it.

Usage:
    .venv/bin/python3 scratch/garage/selfcal.py [--amp 0.5] [--label desk]
"""

import argparse
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hw20k")
)

import acquire  # noqa: E402
import delay_spread as ds  # noqa: E402
import freqresp as F  # noqa: E402

ROOM_TONE_S = 8.0
"""Longer than the 6.0 s inverse filter, so NoiseReference has a fully
overlapped interior to average over."""

TAIL_S = 1.0
"""Recording retained after the sweep ends. This is what bounds the observation
horizon: a tap at lag L needs the whole sweep convolved with it inside the
recording, so the horizon can be at most TAIL_S."""


def run(amp, label, horizon_ms, outdir):
    x, inv = F.make_ess(amp=amp)
    sr = F.SR

    print(f"room tone: {ROOM_TONE_S:.0f} s on {acquire.MAC_MICROPHONE}")
    tone = acquire.record(ROOM_TONE_S, sr, acquire.MAC_MICROPHONE)[:, 0]

    print(f"sweep: {len(x) / sr:.0f} s at amp {amp} on {acquire.MAC_SPEAKERS}")
    captured = acquire.play_and_record(
        x, sr, acquire.MAC_SPEAKERS, acquire.MAC_MICROPHONE, tail_s=TAIL_S
    )[:, 0]

    tone_rms = float(np.sqrt(np.mean(tone**2)))
    cap_rms = float(np.sqrt(np.mean(captured**2)))
    cap_peak = float(np.max(np.abs(captured)))
    print(f"  room tone rms {tone_rms:.5f}   capture rms {cap_rms:.5f} peak {cap_peak:.3f}")
    if cap_peak >= 0.99:
        print("  WARNING: capture is clipping; lower --amp or the device volume")
    if cap_rms <= tone_rms * 1.5:
        print("  WARNING: capture is not meaningfully above room tone")

    acq = acquire.deconvolve(captured, inv, sr, horizon_ms=horizon_ms)
    noise = ds.NoiseReference.from_pcm(tone, inv, sr)
    out = ds.measure(acq, noise)

    print(f"\nhorizon {out.horizon_ms:.1f} ms ({out.support}); "
          f"noise power {out.noise_power:.3e}; truncated={out.truncated}")
    for note in out.notes:
        print(f"  note: {note}")
    for db in ds.THRESHOLDS_DB:
        r = out.at(db)
        if r.usable:
            lo, hi = r.ms_interval
            print(f"  {db:6.0f} dB  {r.ms:8.3f} ms   interval [{lo:.3f}, {hi:.3f}]")
        else:
            print(f"  {db:6.0f} dB  {r.status:<22} {r.detail}")

    os.makedirs(outdir, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    base = os.path.join(outdir, f"selfcal-{label}-{stamp}")
    np.savez_compressed(base + ".npz", capture=captured.astype(np.float32),
                        room_tone=tone.astype(np.float32))
    meta = {
        "label": label, "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "sr": sr, "sweep_amp": amp, "sweep_s": len(x) / sr, "tail_s": TAIL_S,
        "room_tone_s": ROOM_TONE_S,
        "output_device": acquire.MAC_SPEAKERS, "input_device": acquire.MAC_MICROPHONE,
        "capture_rms": cap_rms, "capture_peak": cap_peak, "room_tone_rms": tone_rms,
        "horizon_ms": out.horizon_ms, "support": out.support,
        "noise_power": out.noise_power, "truncated": out.truncated,
        "truncation_ms": out.truncation_ms,
        "readings": {
            f"{int(db)}dB": (
                {"ms": out.at(db).ms, "interval": out.at(db).ms_interval}
                if out.at(db).usable else
                {"status": out.at(db).status, "detail": out.at(db).detail}
            ) for db in ds.THRESHOLDS_DB
        },
    }
    with open(base + ".json", "w") as fh:
        json.dump(meta, fh, indent=2)
    print(f"\nretained: {base}.npz / .json")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--amp", type=float, default=F.AMP)
    ap.add_argument("--label", default="mac-selfcal")
    ap.add_argument("--horizon-ms", type=float, default=acquire.DEFAULT_HORIZON_MS)
    ap.add_argument("--outdir", default="artifacts/garage")
    a = ap.parse_args()
    run(a.amp, a.label, a.horizon_ms, a.outdir)
