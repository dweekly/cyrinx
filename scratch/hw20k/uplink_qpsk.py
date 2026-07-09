#!/usr/bin/env python3
"""Android->Mac uplink at QPSK r1/2 with a speaker-settle preroll (A5 spike).

Written for the Moto G 2026 cross-hardware run (2026-07-09), where the
Pixel-tuned 16-QAM r3/4 uplink profile (final_measurement.measure_a2m)
failed with a residual EVM floor of ~0.35 and a consistently-garbage first
frame. Three Moto-specific mechanisms, all verified at the bench:

1. Dolby DAX processes the media stream (com.dolby.daxservice effect chain
   on session 0) -- multiband compression/EQ wrecks OFDM. Disable first:
   `adb shell pm disable-user --user 0 com.dolby.daxservice`
   (reversible with `pm enable`). EVM improved 0.42 -> ~0.35.
2. Speaker-protection DSP adapts over the first seconds of playback: frame 1
   read EVM 2.5-2.8 while frames 2-5 read ~0.35. The 3 s low-level noise
   preroll here lets it settle; with it, ALL frames verify.
3. The speaker is hot: Mac input 22/100 (the bench standard) clipped
   (peak 1.03); this script uses 15/100.

Measured result (Moto G 2026 -> MacBook Pro M4, facedown_port_fnkey cell,
Wi-Fi adb): 90/90 blocks verified across 5 frames, 8,777 bps goodput,
frame EVMs 0.31-0.57 at QPSK r1/2, band 609-16992 Hz.

Usage: ANDROID_SERIAL=<serial> .venv/bin/python3 scratch/hw20k/uplink_qpsk.py <label>
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import final_measurement as F
import harness as H
import modem as M

MAC_INPUT_VOL = 15   # bench standard is 22; hot phone speakers clip at that
SETTLE_S = 3.0       # speaker-protection DSP settle preroll
SETTLE_AMP = 0.07


def main():
    label = sys.argv[1] if len(sys.argv) > 1 else "unlabeled"
    di = M.Config(600, 17000, cp=768, nfft=2048).data_idx
    cfg = M.Config(600, 17000, rate="1/2", n_sym=F.N_SYM, amp=0.7, cp=768,
                   nfft=2048, bits_per_bin={b: 2 for b in di})
    print(f"=== a2m QPSK r1/2 @ {label} ===")
    print("  profile:", cfg.describe())
    tx, payloads = F.build_tx(cfg)
    settle = (np.random.default_rng(2).standard_normal(int(SETTLE_S * H.SR))
              * SETTLE_AMP).astype(np.float32)
    tx2 = np.concatenate([settle, np.zeros(H.SR // 4, dtype=np.float32), tx])
    H.mac_set_input_volume(MAC_INPUT_VOL)
    rx = H.android_to_mac(tx2)
    np.save(os.path.join(H.DATA, f"a2m_qpsk_{label}.npy"), rx)

    mf = np.abs(np.correlate(rx, M.CHIRP, mode="valid"))
    thr = mf.max() * 0.4
    m = mf.copy()
    starts = []
    for _ in range(F.N_FRAMES + 4):
        k = int(np.argmax(m))
        if m[k] < thr:
            break
        starts.append(k)
        m[max(0, k - cfg.frame_samples // 2): k + cfg.frame_samples // 2] = 0
    starts.sort()
    verified, decoded = 0, []
    for s in starts:
        d = M.demodulate_frame(cfg, rx[s:s + cfg.frame_samples + 4000])
        if d is None:
            continue
        best = max(
            sum(1 for j in range(cfg.n_blocks)
                if d["blocks"][j] and
                d["payload"][j * M.CRC_BLOCK:(j + 1) * M.CRC_BLOCK]
                == pl[j * M.CRC_BLOCK:(j + 1) * M.CRC_BLOCK])
            for pl in payloads)
        if best:
            verified += best
            decoded.append(s)
            print(f"  frame@{s/H.SR:.2f}s: {best}/{cfg.n_blocks} "
                  f"evm={d['evm_rms']:.3f}")
    if decoded:
        span = (max(decoded) + cfg.frame_samples - min(decoded)) / H.SR
        gp = verified * M.CRC_BLOCK * 8 / span
        print(f"  a2m QPSK TOTAL: verified={verified} blocks "
              f"span={span:.2f}s goodput={gp:.0f} bps")
    else:
        print("  a2m QPSK: no blocks verified")


if __name__ == "__main__":
    main()
