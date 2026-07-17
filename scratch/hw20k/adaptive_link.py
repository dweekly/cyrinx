#!/usr/bin/env python3
"""Legacy adaptive sound-then-link diagnostic: sound the channel, build the
recommended config, then transmit and content-attribute decoded frames.

Verification is position-exact within a uniquely best-matching payload, not
chronological scheduled-slot binding. Reported goodput covers the selected link
phase and excludes the preceding sounding transaction. Use goodput_campaign.py
for the strict Cyrinx 2.0 referee contract.

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
    """Run a content-attributed link at the recommended configuration."""
    base = M.Config(F_LO, F_HI, nfft=rec["nfft"], cp=rec["cp"], sr=SR)
    # Use the sounder's measured per-bin loading (calibrated, no 1-bit bins),
    # restricted to this Config's data bins. Beats uniform in challenged
    # channels by upgrading strong bins; reduces to ~uniform when SNR is flat.
    bpb = {b: rec["bits_per_bin"].get(b, 0) for b in base.data_idx}
    if not any(bpb.values()):
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
    starts.sort(); ver = 0; dec = []; seen_payloads = set()
    for s0 in starts:
        r = M.demodulate_frame(cfg, rx, start_hint=s0)
        if r.get("ok"):
            v, payload_index = max(
                (sum(1 for (j, okb, d) in r["blocks"]
                     if okb and d == payload[j * M.CRC_BLOCK:(j + 1) * M.CRC_BLOCK]),
                 payload_index)
                for payload_index, payload in enumerate(payloads))
            if payload_index in seen_payloads:
                v = 0
            else:
                seen_payloads.add(payload_index)
            ver += v; dec.append(s0)
    span = n_frames * cfg.frame_samples / SR + (n_frames - 1) * 0.25
    gp = ver * M.CRC_BLOCK * 8 / span / 1000
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
    if rec.get("noncoherent"):
        # Delay spread exceeds the OFDM CP cap: coherent OFDM cannot decode here
        # at any MCS. The sounder selects the non-coherent MT-FSK floor, which
        # was measured to carry ~267 bps at this very position where OFDM gave 0
        # (data/desk_noncoherent.json). The homegrown MFSK floor modem is the
        # implementation follow-up (ROADMAP); ggwave stands in as the reference.
        out["link"] = {"tier": "mfsk", "note": "non-coherent floor; OFDM infeasible "
                       "(delay spread > CP cap). MT-FSK reference ~267 bps measured."}
        print(f"[{label}] LINK (mfsk floor): coherent OFDM infeasible here; "
              f"non-coherent MT-FSK is the floor (~267 bps measured, vs 0 for OFDM).")
    else:
        ver, tot, gp = link_with(rec)
        out["link"] = {
            "verified": ver,
            "total": tot,
            "goodput_kbps": gp,
            "tier": rec["tier"],
            "verification_contract": "unique-best-payload-within-frame-position; not-scheduled-slot",
            "rate_scope": "selected-link-phase; preceding-sounding-excluded",
        }
        print(
            f"[{label}] LINK ({rec['tier']}): {ver}/{tot} blocks, "
            f"{gp} kbps post-sounding PHY payload rate"
        )
    with open(os.path.join(H.DATA, "adaptive.jsonl"), "a") as fh:
        fh.write(json.dumps(out) + "\n")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "unlabeled")
