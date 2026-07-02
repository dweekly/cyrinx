#!/usr/bin/env python3
"""Generate site/assets: the OpenGraph card (1200x630) and apple-touch-icon
(180x180) from a REAL cyrinx bulk-PHY frame — the spectrogram on the card is
modem.modulate_frame output, not an illustration.

Run: .venv/bin/pip install matplotlib && .venv/bin/python3 scripts/gen-site-assets.py
"""
import os
import sys

import numpy as np

REPO = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(REPO, "scratch", "hw20k"))
import modem as M  # noqa: E402

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyBboxPatch  # noqa: E402

OUT = os.path.join(REPO, "site", "assets")
PANEL = "#12101A"
PAPER = "#F6F5F2"
AMBER = "#E8890C"


def frame_wave():
    cfg = M.Config(nfft=2048, cp=768, rate="3/4", n_sym=8,
                   bits_per_bin={b: 4 for b in M.Config(nfft=2048, cp=768).data_idx})
    payload = M.DetRng(0xCAFE).bytes(cfg.payload_bytes)
    return M.modulate_frame(cfg, payload), cfg


def spectrogram(ax, wave, sr=48000):
    ax.specgram(wave, NFFT=512, Fs=sr, noverlap=384, cmap="magma",
                vmin=-110, vmax=-10)
    ax.set_ylim(0, 24000)
    ax.axis("off")


def og_card():
    wave, cfg = frame_wave()
    fig = plt.figure(figsize=(12, 6.3), dpi=100)
    fig.patch.set_facecolor(PANEL)
    # spectrogram band across the lower half
    ax = fig.add_axes([0.0, 0.0, 1.0, 0.52])
    ax.set_facecolor(PANEL)
    spectrogram(ax, wave)
    # text
    fig.text(0.045, 0.90, "CYRINX", color=AMBER, fontsize=30,
             fontfamily="monospace", fontweight="bold", va="top")
    fig.text(0.045, 0.79, "Data over sound, measured.", color=PAPER,
             fontsize=44, fontweight="bold", va="top")
    fig.text(0.045, 0.645, "36.6 kbps laptop → phone, byte-verified, over the air.",
             color="#C9C4D4", fontsize=22, va="top")
    fig.text(0.955, 0.575, "one transmitted frame ↓", color="#8b86a0",
             fontsize=13, fontfamily="monospace", ha="right", va="top")
    os.makedirs(OUT, exist_ok=True)
    fig.savefig(os.path.join(OUT, "og-card.png"), dpi=100, facecolor=PANEL)
    plt.close(fig)


def touch_icon():
    fig = plt.figure(figsize=(1.8, 1.8), dpi=100)
    fig.patch.set_alpha(0.0)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 64)
    ax.set_ylim(0, 64)
    ax.axis("off")
    ax.add_patch(FancyBboxPatch((2, 2), 60, 60,
                                boxstyle="round,pad=0,rounding_size=12",
                                facecolor=PANEL, edgecolor="none"))
    t = np.linspace(0, 1, 100)
    ax.plot(8 + 48 * t, 14 + 40 * t**2, color="#DE4968", lw=5,
            solid_capstyle="round")
    for x, h in [(37, 7), (44, 12), (51, 18)]:
        ax.add_patch(FancyBboxPatch((x, 12), 4, h,
                                    boxstyle="round,pad=0,rounding_size=2",
                                    facecolor="#FE9F6D", edgecolor="none"))
    fig.savefig(os.path.join(OUT, "apple-touch-icon.png"), dpi=100,
                transparent=True)
    plt.close(fig)


if __name__ == "__main__":
    og_card()
    touch_icon()
    print(f"wrote {OUT}/og-card.png and apple-touch-icon.png")
