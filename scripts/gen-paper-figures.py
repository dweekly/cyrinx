#!/usr/bin/env python3
"""Generate whitepaper figures from REAL modem output (not illustrations).

Currently: docs/whitepaper/cyrinx-frame-anatomy.pdf — the spectrogram of an
actual 16-QAM r3/4 bulk-PHY frame (modem.modulate_frame) with the frame
regions annotated. This is the paper's Figure 1: a reader who has never seen
an OFDM acoustic frame should understand the whole transmit structure from
this one figure.

Run: .venv/bin/python3 scripts/gen-paper-figures.py
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

OUT = os.path.join(REPO, "docs", "whitepaper", "cyrinx-frame-anatomy.pdf")
SR = 48000


def main():
    base = M.Config(nfft=2048, cp=768)
    cfg = M.Config(nfft=2048, cp=768, rate="3/4", n_sym=8,
                   bits_per_bin={b: 4 for b in base.data_idx})
    payload = M.DetRng(0xCAFE).bytes(cfg.payload_bytes)
    wave = M.modulate_frame(cfg, payload).astype(float)

    fig, ax = plt.subplots(figsize=(7.2, 3.0), dpi=200)
    ax.specgram(wave, NFFT=512, Fs=SR, noverlap=384, cmap="magma",
                vmin=-110, vmax=-10)
    ax.set_ylim(0, 24000)
    ax.set_xlabel("time (s)")
    ax.set_ylabel("frequency (Hz)")
    ax.set_yticks([0, 6000, 12000, 18000, 24000])
    ax.set_yticklabels(["0", "6k", "12k", "18k", "24k"])

    # region boundaries from the real geometry
    t_chirp = M.CHIRP_LEN / SR
    t_guard = (M.CHIRP_LEN + M.GUARD) / SR
    t_sync = (M.CHIRP_LEN + M.GUARD + 2 * cfg.sym) / SR
    t_end = len(wave) / SR
    for x in (t_chirp, t_guard, t_sync):
        ax.axvline(x, color="white", lw=0.7, ls=(0, (2, 2)), alpha=0.8)

    def label(x0, x1, text):
        ax.annotate(text, xy=((x0 + x1) / 2, 24000), xytext=(0, 4),
                    textcoords="offset points", ha="center", va="bottom",
                    fontsize=7.5, color="black")

    label(0, t_chirp, "chirp\n(sync)")
    label(t_chirp, t_guard, "guard")
    label(t_guard, t_sync, "sync ×2\n(channel est.)")
    label(t_sync, t_end, "OFDM payload symbols (16-QAM r3/4, pilots every 8th bin)")
    ax.set_title("")
    fig.subplots_adjust(left=0.09, right=0.99, top=0.82, bottom=0.17)
    fig.savefig(OUT)
    print(f"wrote {OUT} ({t_end:.2f}s frame, {cfg.payload_bytes} B payload)")


if __name__ == "__main__":
    main()
