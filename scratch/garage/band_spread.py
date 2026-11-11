#!/usr/bin/env python3
"""Echo length and guard-window signal-to-echo ratio per frequency band.

Reads retained crosscal captures and repeats the delay-spread readout on
band-limited copies of each capture. Air absorbs high frequencies faster than
low ones, so a full-band strong-tap spread can overstate how long the echo lasts
in the upper band. This asks the retained recordings directly.

Method: the sweep capture and the room tone are band-limited with the same
zero-phase FFT mask, then go through the unchanged `acquire.deconvolve` and
`delay_spread.measure`. For each band it also reports, for several guard
lengths G, the ratio of impulse-response energy inside the best G-long window to
the energy outside it (noise-corrected). That ratio is the signal-to-echo ratio
an OFDM symbol with guard G would see if everything outside the guard acted as
interference, which is an estimate from the impulse response, not a decode.

Usage: band_spread.py <crosscal-*.npz> [...]
"""

import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hw20k"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import acquire  # noqa: E402
import delay_spread as ds  # noqa: E402
import freqresp as F  # noqa: E402

BANDS_HZ = [(300, 23000), (300, 1000), (1000, 2000), (2000, 4000), (4000, 8000),
            (8000, 12000), (12000, 16000), (16000, 20000), (20000, 23000)]
GUARDS_MS = [16.0, 42.7, 85.3, 170.7]
HORIZON_MS = 500.0


def band_limit(x, lo, hi, sr):
    spec = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(len(x), 1.0 / sr)
    spec[(freqs < lo) | (freqs > hi)] = 0.0
    return np.fft.irfft(spec, len(x))


def guard_ratio_db(ir, noise_power, guard_ms, sr):
    """Best-window energy over the rest, with the noise floor removed from both."""
    energy = ir ** 2
    g = int(guard_ms * 1e-3 * sr)
    if g >= len(energy):
        return None
    csum = np.concatenate([[0.0], np.cumsum(energy)])
    inside = (csum[g:] - csum[:-g]).max()
    total = csum[-1]
    inside_c = inside - noise_power * g
    outside_c = (total - inside) - noise_power * (len(energy) - g)
    if inside_c <= 0 or outside_c <= 0:
        return None
    return float(10 * np.log10(inside_c / outside_c))


def analyse(capture, tone, inv, sr):
    rows = []
    for lo, hi in BANDS_HZ:
        cap_b = band_limit(capture, lo, hi, sr)
        tone_b = band_limit(tone, lo, hi, sr)
        acq = acquire.deconvolve(cap_b, inv, sr, horizon_ms=HORIZON_MS)
        noise = ds.NoiseReference.from_pcm(tone_b, inv, sr)
        out = ds.measure(acq, noise)
        tail = acq.tail
        peak_to_noise = float(10 * np.log10(np.max(tail ** 2) / max(out.noise_power, 1e-300)))
        rows.append({
            "band_hz": [lo, hi],
            "spread_ms": {f"{int(db)}dB": (out.at(db).ms if out.at(db).usable else None)
                          for db in ds.THRESHOLDS_DB},
            "ir_peak_to_noise_db": peak_to_noise,
            "guard_ratio_db": {f"{g:g}ms": guard_ratio_db(tail, out.noise_power, g, sr)
                               for g in GUARDS_MS},
        })
    return rows


def show(title, rows):
    print(f"\n{title}")
    print(f"  {'band Hz':>13} {'-10dB':>8} {'-15dB':>8} {'-20dB':>8} {'pk/noise':>9}  "
          + "  ".join(f"SER@{g:g}ms" for g in GUARDS_MS))
    for r in rows:
        s = r["spread_ms"]
        fmt = lambda v, w=8: (f"{v:{w}.1f}" if v is not None else " " * (w - 1) + "-")  # noqa: E731
        print(f"  {r['band_hz'][0]:>6}-{r['band_hz'][1]:<6} {fmt(s['-10dB'])} {fmt(s['-15dB'])} "
              f"{fmt(s['-20dB'])} {r['ir_peak_to_noise_db']:9.1f}  "
              + "  ".join(fmt(r["guard_ratio_db"][f"{g:g}ms"], 10) for g in GUARDS_MS))


def main(paths):
    _, inv = F.make_ess(amp=F.AMP)
    for path in paths:
        z = np.load(path)
        result = {}
        links = [("Mac speaker -> phone mic 0", z["mac_to_phone_capture_all_mics"][:, 0],
                  z["mac_to_phone_tone_all_mics"][:, 0]),
                 ("Mac speaker -> phone mic 1", z["mac_to_phone_capture_all_mics"][:, 1],
                  z["mac_to_phone_tone_all_mics"][:, 1]),
                 ("phone speaker -> Mac mic", z["phone_to_mac_capture"], z["phone_to_mac_tone"])]
        print(f"\n=== {os.path.basename(path)}")
        for title, cap, tone in links:
            rows = analyse(np.asarray(cap, float), np.asarray(tone, float), inv, F.SR)
            show(title, rows)
            result[title] = rows
        out = os.path.splitext(path)[0] + ".bands.json"
        with open(out, "w") as fh:
            json.dump(result, fh, indent=1)
        print(f"\nwrote {out}")


if __name__ == "__main__":
    main(sys.argv[1:])
