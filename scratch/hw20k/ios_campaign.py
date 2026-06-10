#!/usr/bin/env python3
"""Reliability campaign for the Mac <-> iPhone 17 Pro Max link.

Mirrors campaign.py (the Pixel version) but drives the iPhone HIL app. Repeats
the headline 16-QAM r3/4 profile N times across varied volume settings, with
ORDERED-stream verification, and emits a unified per-run metrics record so the
paper can report a goodput distribution and a Wilson CI on block-failure rate
instead of a single 5-frame demo.

  m2i: Mac left speaker -> iPhone, decoded ON the iPhone (BulkDemod.swift).
  i2m: iPhone speaker -> Mac, decoded on the Mac (modem.py), band 0.6-11 kHz.

Usage: ios_campaign.py <n_runs_per_direction> [tag]
Appends one JSON line per run to data/ios_campaign.jsonl.
"""
import json
import os
import sys
import time
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H
import ios_harness as I
import modem as M

SR = 48000
N_FRAMES = 5
GAP_S = 0.25
PAD_S = 1.0 / 3.0
N_SYM = 64
SEED_BASE = 1000
BANDS = {"m2i": (1100.0, 23000.0), "i2m": (600.0, 11000.0)}
# (mac_out_vol, mac_in_vol) — iPhone media volume is fixed at max by the app
VOLUMES = [(100, 22), (100, 22), (85, 30), (100, 15), (70, 40), (100, 25)]


def make_cfg(direction):
    f_lo, f_hi = BANDS[direction]
    base = M.Config(f_lo, f_hi, nfft=2048, cp=768, sr=SR)
    return M.Config(f_lo, f_hi, rate="3/4", n_sym=N_SYM, amp=0.7, cp=768,
                    nfft=2048, sr=SR, track_alpha=0.35,
                    bits_per_bin={b: 4 for b in base.data_idx})


def build_tx(cfg):
    payloads = [M.DetRng(SEED_BASE + i).bytes(cfg.payload_bytes) for i in range(N_FRAMES)]
    waves = [M.modulate_frame(cfg, p) for p in payloads]
    gap = np.zeros(int(GAP_S * SR), dtype=np.float32)
    parts = []
    for i, w in enumerate(waves):
        parts.append(w)
        if i < N_FRAMES - 1:
            parts.append(gap)
    parts.append(np.zeros(int(PAD_S * SR), dtype=np.float32))
    return np.concatenate(parts), payloads


def verify_mac_side(cfg, rx, payloads):
    """Ordered verification on the Mac (i2m)."""
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
    verified, crc_ok, decoded, frames, seen = 0, 0, [], [], set()
    for s0 in starts:
        res = M.demodulate_frame(cfg, rx, start_hint=s0)
        if not res.get("ok"):
            frames.append({"start": s0, "ok": False})
            continue
        best_pi, best_n = None, -1
        for pi, pl in enumerate(payloads):
            n = sum(1 for (j, okb, data) in res["blocks"]
                    if okb and data == pl[j * M.CRC_BLOCK:(j + 1) * M.CRC_BLOCK])
            if n > best_n:
                best_pi, best_n = pi, n
        if best_pi not in seen:
            seen.add(best_pi)
            verified += best_n
        crc_ok += res["blocks_ok"]
        decoded.append(s0)
        frames.append({"start": s0, "ok": True, "frame_id": best_pi,
                       "blocks_ok": res["blocks_ok"], "verified_ordered": best_n,
                       "evm": round(res["evm_rms"], 4)})
    span = ((max(decoded) + cfg.frame_samples - min(decoded)) / SR) if decoded else None
    return verified, crc_ok, span, frames


def run_once(direction, vols, tag):
    cfg = make_cfg(direction)
    mac_out, mac_in = vols
    tx, payloads = build_tx(cfg)
    H.mac_set_output_volume(mac_out)
    t0 = time.time()
    if direction == "m2i":
        res, _ = I.mac_to_ios(tx, out_name="camp_m2i.pcm")
        # decode on-device for honest receiver-side verification
        d = I.ios_bulk_decode(out_name="camp_m2i.pcm", channels=1,
                              f_lo=BANDS["m2i"][0], f_hi=BANDS["m2i"][1],
                              n_sym=N_SYM, n_payloads=N_FRAMES, seed_base=SEED_BASE)
        verified = int(d.get("verified_ordered_blocks", d.get("verified", 0)))
        crc_ok = int(d.get("crc_ok_blocks", d.get("blocks_ok", 0)))
        span = float(d.get("span_s", 0)) or None
        frames = d.get("frames", [])
        rx_peak = float(d.get("rx_peak", 0))
    else:
        H.mac_set_input_volume(mac_in)
        rx = I.ios_to_mac(tx, sr=SR).astype(float)
        verified, crc_ok, span, frames = verify_mac_side(cfg, rx, payloads)
        rx_peak = float(np.abs(rx).max())
    wall_s = time.time() - t0

    rec = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "tag": tag, "device": "iphone17pm",
        "direction": direction, "profile": "qam16-34", "nfft": 2048, "cp": 768, "sr": SR,
        "n_frames": N_FRAMES, "blocks_per_frame": cfg.n_blocks,
        "mac_out_vol": mac_out, "mac_in_vol": mac_in, "band": BANDS[direction],
        "rx_peak": round(rx_peak, 4), "frames": frames,
        "crc_ok_blocks": crc_ok, "verified_ordered_blocks": verified,
        "total_blocks": N_FRAMES * cfg.n_blocks, "wall_s": round(wall_s, 1),
    }
    if span:
        rec["span_s"] = round(span, 3)
        rec["goodput_span_bps"] = round(verified * M.CRC_BLOCK * 8 / span)
        rec["goodput_gross_bps"] = round(verified * M.CRC_BLOCK * 8 / (span + PAD_S))
    with open(os.path.join(H.DATA, "ios_campaign.jsonl"), "a") as fh:
        fh.write(json.dumps(rec) + "\n")
    return rec


def summarize():
    path = os.path.join(H.DATA, "ios_campaign.jsonl")
    recs = [json.loads(l) for l in open(path)]
    for d in ("m2i", "i2m"):
        rs = [r for r in recs if r["direction"] == d and "goodput_span_bps" in r]
        if not rs:
            continue
        gp = np.array([r["goodput_span_bps"] for r in rs]) / 1000
        ver = sum(r["verified_ordered_blocks"] for r in rs)
        tot = sum(r["total_blocks"] for r in rs)
        f = tot - ver
        ph = f / tot
        z = 1.96
        den = 1 + z * z / tot
        ctr = (ph + z * z / (2 * tot)) / den
        hw = z * np.sqrt(ph * (1 - ph) / tot + z * z / (4 * tot * tot)) / den
        print(f"{d}: runs={len(rs)} goodput kbps min/med/max="
              f"{gp.min():.2f}/{np.median(gp):.2f}/{gp.max():.2f} "
              f"mean={gp.mean():.2f}±{gp.std():.2f}  blocks {ver}/{tot}  "
              f"block-fail {ph:.4f} (95% CI {max(0,ctr-hw):.4f}-{ctr+hw:.4f})")


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    tag = sys.argv[2] if len(sys.argv) > 2 else "default"
    for i in range(n):
        vols = VOLUMES[i % len(VOLUMES)]
        for d in ("m2i", "i2m"):
            try:
                r = run_once(d, vols, tag)
                print(f"[{i+1}/{n}] {d} vols={vols}: "
                      f"{r['verified_ordered_blocks']}/{r['total_blocks']} "
                      f"goodput={r.get('goodput_span_bps', 0)/1000:.2f} kbps "
                      f"peak={r['rx_peak']:.3f}")
            except Exception as e:
                print(f"[{i+1}/{n}] {d} FAILED: {e}")
    print("--- summary ---")
    summarize()
