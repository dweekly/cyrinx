#!/usr/bin/env python3
"""How much usable signal a retained ladder capture holds, bin by bin.

For each cell the transmitted waveform is regenerated from its recorded seed, so
every transmitted sample is known. The first frame is located by its chirp, and
for every OFDM symbol the transmitted and received FFT windows are compared. A
single complex gain per bin is fitted across all symbols of the frame (a static
channel known perfectly: a "genie"), and what is left over is counted as
interference plus noise. The result is a per-bin signal-to-interference-and-
noise ratio that no receiver with a static channel estimate could beat on that
capture, so it separates "the information is not there" from "the receiver
failed to use it". It is an upper bound for that receiver class, not a decode.

The clock-rate difference between the two devices is fitted first (one number
per frame, in parts per million) and removed; without that step a static gain
cannot describe a multi-second frame at high frequencies and the tool reports
nonsense. Also reported: the same ratio with the gain fitted separately on the
first and second half of the frame, which rises if the channel drifts within a
frame.

Usage: genie_sinr.py <ladder-*.jsonl> [--guard SAMPLES]
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
import freqresp as F  # noqa: E402
import linkprobe  # noqa: E402
import resample_replay  # noqa: E402

CHIRP = 4096
BANDS = [(300, 2000), (2000, 8000), (8000, 12000), (12000, 16000), (16000, 20000), (20000, 23000)]


def fit(X, Y):
    """Per-bin least-squares gain and SINR. X, Y: (symbols, bins)."""
    num = (Y * X.conj()).sum(axis=0)
    den = (np.abs(X) ** 2).sum(axis=0)
    H = num / den
    resid = Y - H * X
    n = X.shape[0]
    noise = (np.abs(resid) ** 2).sum(axis=0) / max(n - 1, 1) * n
    return (np.abs(H) ** 2 * den) / np.maximum(noise, 1e-300)


def analyse(row, rx, guard):
    cfg = clib.make_cfg(bits_per_bin=row["bits_per_bin"], rate=row["rate"], n_sym=row["n_sym"],
                        f_lo=row["f_lo"], f_hi=row["f_hi"], nfft=row["nfft"], cp=row["cp"],
                        amp=row["amp"], pilot_every=row["pilot_every"])
    wave, _, geom = linkprobe.build(cfg, seed=row["seed"])
    nfft, cp = row["nfft"], row["cp"]
    sym = nfft + cp
    n_all = row["n_sym"] + 2  # two sync symbols lead the data symbols
    assert geom.frame_samples == CHIRP + guard + n_all * sym, "frame layout differs from this tool's model"
    mf = np.abs(np.correlate(rx[: len(rx) - geom.frame_samples], wave[:CHIRP], mode="valid"))
    start = int(np.argmax(mf[: len(mf)]))
    # the first frame is the earlier of the two chirp peaks
    second = start - (geom.frame_samples + int(linkprobe.GAP_S * F.SR))
    if second >= 0 and mf[max(0, second - 200): second + 200].max() > 0.5 * mf[start]:
        start = max(0, second - 200) + int(np.argmax(mf[max(0, second - 200): second + 200]))
    freqs = np.fft.rfftfreq(nfft, 1.0 / F.SR)
    used = (freqs >= row["f_lo"]) & (freqs <= row["f_hi"])
    body = CHIRP + guard
    X = np.stack([np.fft.rfft(wave[body + k * sym + cp: body + k * sym + sym])[used]
                  for k in range(n_all)])
    f_used = freqs[used]
    k_idx = np.arange(n_all)[:, None]

    def windows(early):
        off = start + body - early
        return np.stack([np.fft.rfft(rx[off + k * sym + cp: off + k * sym + sym])[used]
                         for k in range(n_all)])

    def derotate(Y, ppm):
        # A clock-rate difference of ppm slides symbol k by ppm * 1e-6 * k * sym samples,
        # which is a phase ramp across frequency that grows with k. Undo it so that one
        # gain per bin can describe the whole frame.
        tau = ppm * 1e-6 * k_idx * sym / F.SR
        return Y * np.exp(2j * np.pi * f_used[None, :] * tau)

    def score_of(sinr):
        return float(np.mean(np.log2(1 + sinr)))

    best = None
    for early in range(0, cp + 1, max(1, cp // 16)):
        if start + body - early < 0:
            continue
        Y = windows(early)
        for ppm in np.arange(-60.0, 60.5, 2.0):
            sc = score_of(fit(X, derotate(Y, ppm)))
            if best is None or sc > best[0]:
                best = (sc, early, ppm)
    _, early, ppm0 = best
    Y = windows(early)
    ppm = max(np.arange(ppm0 - 2.0, ppm0 + 2.05, 0.1), key=lambda p: score_of(fit(X, derotate(Y, p))))
    Yd = derotate(Y, ppm)
    sinr = fit(X, Yd)
    half = n_all // 2
    sinr_split = (fit(X[:half], Yd[:half]) + fit(X[half:], Yd[half:])) / 2
    best = (None, early, sinr, sinr_split)
    _, early, sinr, sinr_split = best
    out = {"cell": resample_replay.capture_key(row), "verified": row["verified"], "scheduled": row["scheduled"],
           "decoder_evm": row["best_evm"], "window_early_samples": early, "clock_offset_ppm": float(ppm),
           "mean_sinr_db": float(10 * np.log10(sinr.mean())),
           "median_sinr_db": float(10 * np.log10(np.median(sinr))),
           "bits_per_bin_bound": float(np.mean(np.log2(1 + sinr))),
           "bound_bps": float(np.sum(np.log2(1 + sinr)) / (sym / F.SR)),
           "split_gain_db": float(10 * np.log10(np.median(sinr_split) / np.median(sinr))),
           "bands": {}}
    for lo, hi in BANDS:
        m = (f_used >= lo) & (f_used < hi)
        if m.sum():
            out["bands"][f"{lo}-{hi}"] = float(10 * np.log10(np.median(sinr[m])))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("jsonl")
    ap.add_argument("--guard", type=int, default=2048, help="post-chirp guard the codec was built with")
    a = ap.parse_args()
    captures = np.load(a.jsonl[:-6] + ".npz")
    rows = [json.loads(line) for line in open(a.jsonl)]
    print(f"{'cell':<22}{'blocks':>8}{'EVM':>7}{'med dB':>8}{'b/bin':>7}{'bound bps':>11}{'split':>7}{'ppm':>7}   median SINR dB by band")
    results = []
    for row in rows:
        if "error" in row:
            continue
        r = analyse(row, np.asarray(captures[resample_replay.capture_key(row)], float), a.guard)
        results.append(r)
        bands = "  ".join(f"{k.split('-')[0]}:{v:5.1f}" for k, v in r["bands"].items())
        print(f"{r['cell']:<22}{r['verified']:>4}/{r['scheduled']:<3}{r['decoder_evm']:>7.2f}{r['median_sinr_db']:>8.1f}"
              f"{r['bits_per_bin_bound']:>7.2f}{r['bound_bps']:>11.0f}{r['split_gain_db']:>7.1f}{r['clock_offset_ppm']:>7.1f}   {bands}")
    with open(a.jsonl[:-6] + ".genie.json", "w") as fh:
        json.dump(results, fh, indent=1)


if __name__ == "__main__":
    main()
