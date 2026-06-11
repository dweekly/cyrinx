#!/usr/bin/env python3
"""Render the whitepaper frequency-response figure grid from the JSON files
written by freqresp.py (data/freqresp/<path>_<env>.json).

Two figures:
  data/freqresp/freqresp_by_path.png
      one panel per OTA path (mac2pixel, pixel2mac, mac2iphone, iphone2mac);
      within a panel, one |H(f)| curve per environment -> shows how placement
      moves the channel for a fixed device pair + direction.
  data/freqresp/freqresp_by_direction.png
      downlink (mac2*) vs uplink (*2mac) overlaid per device, default env only
      -> shows the up/down asymmetry that bounds the return path.

SNR(f), when present, is drawn on a secondary axis. Magnitude is relative dB
(re in-band median); the relative-calibration caveat is printed on each figure.

Usage:  plot_freqresp.py            # renders both figures from data/freqresp/
"""
import glob
import json
import os
import sys

import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt   # noqa: E402

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "freqresp")
PATHS = ["mac2pixel", "pixel2mac", "mac2iphone", "iphone2mac"]
PATH_TITLE = {
    "mac2pixel": "Mac → Pixel 7a (downlink)",
    "pixel2mac": "Pixel 7a → Mac (uplink)",
    "mac2iphone": "Mac → iPhone 17 PM (downlink)",
    "iphone2mac": "iPhone 17 PM → Mac (uplink)",
}
CAVEAT = ("Relative magnitude (dB re in-band median); drive + mic gain pinned. "
          "Not absolute SPL — cross-device magnitude not directly comparable.")


def _load():
    recs = {}
    for fn in sorted(glob.glob(os.path.join(DATA, "*.json"))):
        with open(fn) as f:
            r = json.load(f)
        recs.setdefault(r["path"], []).append(r)
    return recs


def _smooth_db(freqs, mag_db, frac_oct=1.0 / 6):
    """Fractional-octave smoothing for legible publication curves."""
    f = np.asarray(freqs)
    m = np.asarray(mag_db)
    out = np.empty_like(m)
    for i, fc in enumerate(f):
        if fc <= 0:
            out[i] = m[i]
            continue
        lo, hi = fc * 2 ** (-frac_oct), fc * 2 ** (frac_oct)
        sel = (f >= lo) & (f <= hi)
        out[i] = m[sel].mean() if sel.any() else m[i]
    return out


def fig_by_path(recs):
    present = [p for p in PATHS if p in recs]
    if not present:
        return None
    n = len(present)
    ncol = 2 if n > 1 else 1
    nrow = int(np.ceil(n / ncol))
    fig, axes = plt.subplots(nrow, ncol, figsize=(6.2 * ncol, 3.6 * nrow),
                             squeeze=False)
    for ax in axes.flat:
        ax.set_visible(False)
    for k, path in enumerate(present):
        ax = axes.flat[k]
        ax.set_visible(True)
        for r in sorted(recs[path], key=lambda r: r["env"]):
            f = np.asarray(r["freqs_hz"])
            m = _smooth_db(f, r["H_mag_db"])
            sel = (f >= 50) & (f <= 23500)
            ax.semilogx(f[sel], m[sel], lw=1.4, label=r["env"])
        ax.set_title(PATH_TITLE.get(path, path), fontsize=10)
        ax.set_xlim(50, 24000)
        ax.set_ylim(-45, 15)
        ax.grid(True, which="both", alpha=0.25)
        ax.axvline(18000, color="0.6", ls=":", lw=0.8)   # audible/ultrasonic edge
        ax.set_xlabel("frequency (Hz)")
        ax.set_ylabel("|H(f)| (dB, rel.)")
        ax.legend(fontsize=7, title="environment", loc="lower left")
    fig.suptitle("Channel magnitude response by device pairing and direction",
                 fontsize=12)
    fig.text(0.5, 0.005, CAVEAT, ha="center", fontsize=7, color="0.35")
    fig.tight_layout(rect=(0, 0.03, 1, 0.97))
    out = os.path.join(DATA, "freqresp_by_path.png")
    fig.savefig(out, dpi=160)
    plt.close(fig)
    print(f"  wrote {out}")
    return out


def fig_by_direction(recs, env="default"):
    """Downlink vs uplink per device, for one environment."""
    pairs = [("Pixel 7a", "mac2pixel", "pixel2mac"),
             ("iPhone 17 PM", "mac2iphone", "iphone2mac")]
    pairs = [(d, dn, up) for (d, dn, up) in pairs if dn in recs or up in recs]
    if not pairs:
        return None
    fig, axes = plt.subplots(1, len(pairs), figsize=(6.2 * len(pairs), 3.8),
                             squeeze=False)

    def pick(path):
        for r in recs.get(path, []):
            if r["env"] == env:
                return r
        return recs.get(path, [None])[0] if recs.get(path) else None

    for k, (dev, dn, up) in enumerate(pairs):
        ax = axes[0][k]
        for path, lbl in [(dn, "downlink"), (up, "uplink")]:
            r = pick(path)
            if r is None:
                continue
            f = np.asarray(r["freqs_hz"])
            m = _smooth_db(f, r["H_mag_db"])
            sel = (f >= 50) & (f <= 23500)
            ax.semilogx(f[sel], m[sel], lw=1.5, label=f"{lbl} ({r['env']})")
        ax.set_title(dev, fontsize=10)
        ax.set_xlim(50, 24000)
        ax.set_ylim(-45, 15)
        ax.grid(True, which="both", alpha=0.25)
        ax.axvline(18000, color="0.6", ls=":", lw=0.8)
        ax.set_xlabel("frequency (Hz)")
        ax.set_ylabel("|H(f)| (dB, rel.)")
        ax.legend(fontsize=8, loc="lower left")
    fig.suptitle("Up/down asymmetry: the uplink is bounded by the mobile speaker",
                 fontsize=12)
    fig.text(0.5, 0.005, CAVEAT, ha="center", fontsize=7, color="0.35")
    fig.tight_layout(rect=(0, 0.03, 1, 0.96))
    out = os.path.join(DATA, "freqresp_by_direction.png")
    fig.savefig(out, dpi=160)
    plt.close(fig)
    print(f"  wrote {out}")
    return out


if __name__ == "__main__":
    recs = _load()
    if not recs:
        print(f"no data in {DATA} — run freqresp.py over some paths first")
        sys.exit(1)
    print("=== rendering frequency-response figure grid ===")
    for p, rs in recs.items():
        print(f"  {p}: {len(rs)} env(s) -> {[r['env'] for r in rs]}")
    fig_by_path(recs)
    fig_by_direction(recs)
