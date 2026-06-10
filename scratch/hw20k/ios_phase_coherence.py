#!/usr/bin/env python3
"""iPhone 17 Pro Max speaker phase-coherence vs frequency, 96 kHz.

Mirrors the decisive measurement in docs/ULTRASONIC_BAND.md (which found the
Pixel 7a speaker phase-incoherent above ~18 kHz): play a single tone from the
iPhone speaker, capture on the Mac mic, take the STFT phase of the fundamental,
detrend it, and report the standard deviation (low = coherent). This decides
whether an inaudible (>=18 kHz) OFDM/QAM uplink from the iPhone could work.

Usage: ios_phase_coherence.py
"""

import os
import sys

import numpy as np

import harness as H
import ios_harness as I

SR = 96_000


def tone(freq, dur_s, amp):
    t = np.arange(int(dur_s * SR)) / SR
    w = amp * np.sin(2 * np.pi * freq * t)
    r = int(0.02 * SR)
    env = np.ones(len(w))
    env[:r] = 0.5 - 0.5 * np.cos(np.pi * np.arange(r) / r)
    env[-r:] = env[:r][::-1]
    return (w * env).astype(np.float32)


def phase_std(rx, freq):
    """STFT phase of the fundamental, detrended, std in radians."""
    # trim to the loud core
    e = np.convolve(rx ** 2, np.ones(SR // 20) / (SR // 20), mode="same")
    m = e > e.max() * 0.2
    if not m.any():
        return float("nan"), 0.0
    i0, i1 = np.argmax(m), len(m) - np.argmax(m[::-1])
    seg = rx[i0 + SR // 10: i1 - SR // 10]
    if len(seg) < SR // 2:
        return float("nan"), float(np.sqrt((rx ** 2).mean()))
    win = 2_048
    hop = 512
    phases = []
    k = freq * win / SR
    k0 = int(round(k))
    for s in range(0, len(seg) - win, hop):
        block = seg[s:s + win] * np.hanning(win)
        X = np.fft.rfft(block)
        phases.append(np.angle(X[k0]))
    ph = np.unwrap(np.array(phases))
    # detrend (remove the linear frequency-offset ramp)
    n = np.arange(len(ph))
    a = np.polyfit(n, ph, 1)
    resid = ph - np.polyval(a, n)
    return float(np.std(resid)), float(np.sqrt((seg ** 2).mean()))


def main(n_reps=8):
    import json
    H.mac_set_input_volume(60)
    print(f"iPhone 17 Pro Max speaker phase coherence, {n_reps} reps "
          f"(STFT phase-std, lower=coherent)")
    conds = [(8_000, 0.9), (19_500, 0.9), (19_500, 0.4),
             (22_000, 0.9), (22_000, 0.4)]
    out = {"device": "iphone17pm", "sr": SR, "n_reps": n_reps, "conditions": []}
    print(f"{'tone':>8} {'amp':>5} {'std_med':>9} {'std_min':>9} {'std_max':>9} "
          f"{'rms_med':>10}  interp")
    for freq, amp in conds:
        stds, rmss = [], []
        for _ in range(n_reps):
            rx = I.ios_to_mac(tone(freq, 2.0, amp), channels=1, sr=SR)
            s, r = phase_std(rx, freq)
            if not np.isnan(s):
                stds.append(s); rmss.append(r)
        stds = np.array(stds); rmss = np.array(rmss)
        med = float(np.median(stds))
        interp = ("coherent" if med < 0.3 else
                  "marginal" if med < 1.2 else "INCOHERENT")
        rec = {"freq_hz": freq, "amp": amp, "phase_std_rad": stds.tolist(),
               "std_median": med, "std_min": float(stds.min()),
               "std_max": float(stds.max()), "rms_median": float(np.median(rmss)),
               "interp": interp}
        out["conditions"].append(rec)
        print(f"{freq/1000:6.1f}k {amp:5.2f} {med:9.3f} {stds.min():9.3f} "
              f"{stds.max():9.3f} {np.median(rmss):10.6f}  {interp}")
    with open(os.path.join(H.DATA, "ios_phase_coherence.json"), "w") as fh:
        json.dump(out, fh, indent=2)
    print(f"saved {H.DATA}/ios_phase_coherence.json")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 8)
