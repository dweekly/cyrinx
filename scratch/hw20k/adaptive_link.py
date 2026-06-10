#!/usr/bin/env python3
"""Adaptive sound-then-link: sound the channel, build the recommended config,
transmit + ordered-verify. The same code adapts to the measured environment.

Usage: adaptive_link.py <position_label>
Sounds Mac->iPhone, prints the verdict, and (unless 'reposition') links with the
recommended profile and reports verified goodput. Appends to data/adaptive.jsonl.
"""
import json
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H
import ios_harness as I
import modem as M
import sounder as S

SR = 48000
F_LO, F_HI = 1100.0, 23000.0
SEED_BASE = 1000


def send_m2i(wave, name="adapt.pcm"):
    _, path = I.mac_to_ios(np.asarray(wave, np.float32), out_name=name, sr=SR)
    return np.fromfile(path, dtype="<i2").astype(np.float32) / 32768.0


def link_with(rec, n_frames=5):
    """Build a Config from the recommendation and run an ordered-verified link."""
    base = M.Config(F_LO, F_HI, nfft=rec["nfft"], cp=rec["cp"], sr=SR)
    bpb = {b: rec["bits_uniform"] for b in base.data_idx}
    cfg = M.Config(F_LO, F_HI, rate=rec["rate"], n_sym=64, amp=0.7,
                   cp=rec["cp"], nfft=rec["nfft"], sr=SR, track_alpha=0.35,
                   bits_per_bin=bpb)
    payloads = [M.DetRng(SEED_BASE + i).bytes(cfg.payload_bytes) for i in range(n_frames)]
    waves = [M.modulate_frame(cfg, p) for p in payloads]
    gap = np.zeros(int(0.25 * SR), dtype=np.float32)
    parts = []
    for i, w in enumerate(waves):
        parts.append(w)
        if i < n_frames - 1:
            parts.append(gap)
    parts.append(np.zeros(SR // 3, dtype=np.float32))
    rx = send_m2i(np.concatenate(parts), name="adapt_link.pcm")
    mf = np.abs(np.correlate(rx, cfg.chirp_wave, mode="valid"))
    thr = mf.max() * 0.4
    m = mf.copy(); starts = []
    for _ in range(n_frames + 4):
        k = int(np.argmax(m))
        if m[k] < thr:
            break
        starts.append(k); m[max(0, k - cfg.frame_samples // 2): k + cfg.frame_samples // 2] = 0
    starts.sort(); ver = 0; dec = []
    for s0 in starts:
        r = M.demodulate_frame(cfg, rx, start_hint=s0)
        if r.get("ok"):
            v = sum(1 for (j, okb, d) in r["blocks"]
                    if okb and any(d == p[j*M.CRC_BLOCK:(j+1)*M.CRC_BLOCK] for p in payloads))
            ver += v; dec.append(s0)
    span = ((max(dec) + cfg.frame_samples - min(dec)) / SR) if dec else None
    gp = (ver * M.CRC_BLOCK * 8 / span / 1000) if span else 0.0
    return ver, n_frames * cfg.n_blocks, round(gp, 2)


def main(label):
    H.mac_set_output_volume(100)
    rec, an = S.sound_channel(send_m2i)
    print(f"[{label}] SOUND: median SNR {rec['median_snr_db']} dB, "
          f"delay-spread(-15dB) {rec['delay_spread_ms_15']} ms, "
          f"usable {rec['usable_bin_frac']*100:.0f}%  -> TIER {rec['tier']} "
          f"({rec['mcs']} r{rec['rate']}, CP {rec['cp_ms']} ms)"
          + ("  [advise closer/quieter spot]" if rec['advise_reposition'] else ""))
    out = {"position": label, "sounding": rec}
    # Always attempt the link at the chosen (most robust feasible) tier.
    ver, tot, gp = link_with(rec)
    out["link"] = {"verified": ver, "total": tot, "goodput_kbps": gp, "tier": rec["tier"]}
    print(f"[{label}] LINK ({rec['tier']}): {ver}/{tot} blocks, {gp} kbps")
    with open(os.path.join(H.DATA, "adaptive.jsonl"), "a") as fh:
        fh.write(json.dumps(out) + "\n")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "unlabeled")
