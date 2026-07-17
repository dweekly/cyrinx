#!/usr/bin/env python3
"""Legacy reliability campaign: repeated audible-band runs, both directions,
with content-attributed within-frame verification and a unified metrics record.

Each run: 5 frames of 16-QAM r3/4 (the headline profile). Verification is
position-exact within the best-matching unique payload: a decoded frame is
attributed to the transmitted frame whose payload matches the most CRC-ok
blocks, then duplicate payload attributions are suppressed. This is not strict
chronological scheduled-slot binding. Historical JSON field names containing
``ordered`` are retained for schema compatibility; use goodput_campaign.py for
the strict Cyrinx 2.0 referee contract.

Two goodput accountings are reported:
  goodput_span  — verified bits / (first chirp .. last data sample)   [paper]
  goodput_gross — verified bits / (span + trailing pad)               [incl pad]

Usage: campaign.py <n_runs_per_direction> [tag]
Appends one JSON line per run to data/campaign.jsonl.
"""
import json
import os
import subprocess
import sys
import time
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H
import modem as M

SR = 48000
N_FRAMES = 5
GAP_S = 0.25
PAD_S = 1.0 / 3.0
BANDS = {"m2a": (1100.0, 23000.0), "a2m": (600.0, 17000.0)}

# (mac_out_vol, mac_in_vol, pixel_media_vol 0-25) variations across runs
VOLUMES = [(100, 22, 25), (100, 22, 25), (85, 30, 25), (100, 22, 20),
           (70, 40, 25), (100, 15, 25)]


def make_cfg(direction):
    f_lo, f_hi = BANDS[direction]
    base = M.Config(f_lo, f_hi, nfft=2048, cp=768, sr=SR)
    return M.Config(f_lo, f_hi, rate="3/4", n_sym=64, amp=0.7, cp=768, nfft=2048,
                    sr=SR, track_alpha=0.35,
                    bits_per_bin={b: 4 for b in base.data_idx})


def ordered_verify(res, payloads, n_blocks):
    """Best-content-attribute a frame, then count within-frame matches."""
    best_pi, best_n = None, -1
    for pi, pl in enumerate(payloads):
        n = sum(1 for (j, okb, data) in res["blocks"]
                if okb and data == pl[j * M.CRC_BLOCK:(j + 1) * M.CRC_BLOCK])
        if n > best_n:
            best_pi, best_n = pi, n
    return best_pi, best_n


def run_once(direction, volumes, tag):
    cfg = make_cfg(direction)
    mac_out, mac_in, pix_vol = volumes
    payloads = [M.DetRng(1000 + i).bytes(cfg.payload_bytes) for i in range(N_FRAMES)]
    waves = [M.modulate_frame(cfg, p) for p in payloads]
    gap = np.zeros(int(GAP_S * SR), dtype=np.float32)
    parts = []
    for i, w in enumerate(waves):
        parts.append(w)
        if i < N_FRAMES - 1:
            parts.append(gap)
    parts.append(np.zeros(int(PAD_S * SR), dtype=np.float32))
    tx = np.concatenate(parts)

    H.mac_set_output_volume(mac_out)
    t0 = time.time()
    if direction == "m2a":
        st = np.zeros((len(tx), 2), dtype=np.float32)
        st[:, 0] = tx
        H.adb(f"shell media volume --stream 3 --set {pix_vol}")
        _, p = H.mac_to_android(st, out_name="camp.pcm", sr=SR)
        rx = H.load_pcm16(p)[:, 0].astype(float)
    else:
        H.mac_set_input_volume(mac_in)
        H.adb(f"shell media volume --stream 3 --set {pix_vol}")
        rx = H.android_to_mac(tx, sr=SR).astype(float)
    wall_s = time.time() - t0

    mf = np.abs(np.correlate(rx, cfg.chirp_wave, mode="valid"))
    thr = mf.max() * 0.4
    m = mf.copy()
    starts = []
    for _ in range(N_FRAMES + 4):
        k = int(np.argmax(m))
        if m[k] < thr:
            break
        starts.append(k)
        m[max(0, k - cfg.frame_samples // 2): k + cfg.frame_samples // 2] = 0
    starts.sort()

    frames = []
    verified = 0
    crc_ok = 0
    decoded = []
    seen_pi = set()
    for s0 in starts:
        res = M.demodulate_frame(cfg, rx, start_hint=s0)
        if not res.get("ok"):
            frames.append({"start": s0, "ok": False, "err": res.get("err")})
            continue
        pi, v = ordered_verify(res, payloads, cfg.n_blocks)
        dup = pi in seen_pi
        if not dup:
            seen_pi.add(pi)
            verified += v
        crc_ok += res["blocks_ok"]
        decoded.append(s0)
        frames.append({"start": s0, "ok": True, "frame_id": pi, "dup": dup,
                       "blocks_ok": res["blocks_ok"], "verified_ordered": v,
                       "evm": round(res["evm_rms"], 4)})
    rec = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "tag": tag, "direction": direction,
        "profile": "qam16-34", "nfft": 2048, "cp": 768, "sr": SR,
        "n_frames": N_FRAMES, "blocks_per_frame": cfg.n_blocks,
        "mac_out_vol": mac_out, "mac_in_vol": mac_in, "pixel_vol": pix_vol,
        "rx_peak": round(float(np.abs(rx).max()), 4),
        "frames": frames,
        "crc_ok_blocks": crc_ok,
        "verified_ordered_blocks": verified,
        "verification_contract": "unique-best-payload-within-frame-position; not-scheduled-slot",
        "total_blocks": N_FRAMES * cfg.n_blocks,
        "wall_s": round(wall_s, 1),
    }
    # Score the complete declared schedule even when a boundary frame fails.
    span = N_FRAMES * cfg.frame_samples / SR + (N_FRAMES - 1) * GAP_S
    gross = span + PAD_S
    rec["span_s"] = round(span, 3)
    rec["goodput_span_bps"] = round(verified * M.CRC_BLOCK * 8 / span)
    rec["goodput_gross_bps"] = round(verified * M.CRC_BLOCK * 8 / gross)
    with open(os.path.join(H.DATA, "campaign.jsonl"), "a") as fh:
        fh.write(json.dumps(rec) + "\n")
    return rec


def summarize():
    recs = [json.loads(l) for l in open(os.path.join(H.DATA, "campaign.jsonl"))]
    for d in ("m2a", "a2m"):
        rs = [r for r in recs if r["direction"] == d and "goodput_span_bps" in r]
        if not rs:
            continue
        gp = np.array([r["goodput_span_bps"] for r in rs]) / 1000
        ver = sum(r["verified_ordered_blocks"] for r in rs)
        tot = sum(r["total_blocks"] for r in rs)
        # Wilson 95% CI on block failure rate
        f = tot - ver
        ph = f / tot
        z = 1.96
        den = 1 + z * z / tot
        ctr = (ph + z * z / (2 * tot)) / den
        hw = z * np.sqrt(ph * (1 - ph) / tot + z * z / (4 * tot * tot)) / den
        print(f"{d}: runs={len(rs)} goodput kbps min/med/max = "
              f"{gp.min():.2f}/{np.median(gp):.2f}/{gp.max():.2f}  "
              f"blocks {ver}/{tot} verified  "
              f"block-failure rate {ph:.4f} (95% CI {max(0,ctr-hw):.4f}-{ctr+hw:.4f})")


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    tag = sys.argv[2] if len(sys.argv) > 2 else "default"
    for i in range(n):
        vols = VOLUMES[i % len(VOLUMES)]
        for d in ("m2a", "a2m"):
            try:
                r = run_once(d, vols, tag)
                print(f"[{i+1}/{n}] {d} vols={vols}: "
                      f"{r['verified_ordered_blocks']}/{r['total_blocks']} "
                      f"goodput={r.get('goodput_span_bps', 0)/1000:.2f} kbps")
            except Exception as e:
                print(f"[{i+1}/{n}] {d} FAILED: {e}")
    summarize()
