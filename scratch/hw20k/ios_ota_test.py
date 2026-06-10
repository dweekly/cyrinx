#!/usr/bin/env python3
"""Real over-the-air OTA trials: Mac <-> iPhone 17 Pro Max, honest goodput.

Mirrors ota_test.py but for the iPhone HIL app (ios_harness). The audible-band
profile (the proven-working one from ACOUSTIC_BULK_PHY.md) is the default:

  m2i: Mac LEFT speaker -> iPhone mic, band 1.1-23 kHz, decoded ON the iPhone
       (BulkDemod.swift), so reception is proven on-device with no host-side
       decode ambiguity.
  i2m: iPhone speaker -> Mac mic, band 0.6-17 kHz, decoded on the Mac (modem.py).

Goodput = ordered-verified blocks (CRC-ok AND byte-equal to the transmitted
DetRng payload at the correct ordered position) * 256 * 8 / span, where span
runs from the first frame's chirp to the last frame's final data sample.

Usage: ios_ota_test.py {m2i|i2m|both} [profile] [amp] [n_frames]
"""

import os
import sys

import numpy as np

import harness as H
import ios_harness as I
import modem as M

DATA = H.DATA
N_SYM = 64
CP, NFFT = 768, 2048
# i2m band is narrower than the Pixel's 0.6-17 kHz: the iPhone 17 Pro Max
# speaker rolls off ~18 dB by 10-14 kHz and ~33 dB by 14-17 kHz (measured,
# spectral probe below), so the upper half carries no coherent QAM. 0.6-11 kHz
# is the measured sweet spot (173/175 blocks, EVM ~0.1); pushing to 17 kHz like
# the Pixel collapses the link (EVM 0.3-0.9). Confined to where the iPhone
# transducer actually radiates with phase coherence.
BANDS = {"m2i": (1100.0, 23000.0), "i2m": (600.0, 11000.0)}
SEED_BASE = 1000


def make_cfg(direction, profile, amp):
    f_lo, f_hi = BANDS[direction]
    kw = dict(cp=CP, nfft=NFFT)
    base = M.Config(f_lo=f_lo, f_hi=f_hi, **kw)
    if profile == "qam16-34":
        return M.Config(f_lo, f_hi, rate="3/4", n_sym=N_SYM, amp=amp,
                        bits_per_bin={b: 4 for b in base.data_idx}, **kw)
    if profile == "qpsk12":
        return M.Config(f_lo, f_hi, rate="1/2", n_sym=N_SYM, amp=amp, **kw)
    raise SystemExit(f"unknown profile {profile}")


def build_tx(cfg, n_frames, gap_s=0.25):
    payloads = [M.DetRng(SEED_BASE + i).bytes(cfg.payload_bytes) for i in range(n_frames)]
    waves = [M.modulate_frame(cfg, p) for p in payloads]
    gap = np.zeros(int(gap_s * H.SR), dtype=np.float32)
    tx = np.concatenate([w for pair in zip(waves, [gap] * n_frames) for w in pair])
    return tx, payloads


def m2i(profile="qam16-34", amp=0.7, n_frames=5):
    """Mac LEFT speaker -> iPhone mic, decoded ON the iPhone."""
    cfg = make_cfg("m2i", profile, amp)
    print(f"[m2i/{profile}] {cfg.describe()}")
    tx, _ = build_tx(cfg, n_frames)
    # Trailing in-stream silence so the macOS stream-end fade does not eat the
    # final data symbol (ACOUSTIC_BULK_PHY.md defect #3).
    tx_pad = np.concatenate([tx, np.zeros(H.SR // 3, dtype=np.float32)])
    res, path = I.mac_to_ios(tx_pad, rec_channels=1, sr=H.SR, out_name="m2i.pcm")
    cap = np.fromfile(path, dtype="<i2").astype(np.float32) / 32768.0
    print(f"  iPhone capture: {cap.shape} rms={np.sqrt((cap**2).mean()):.5f} "
          f"peak={np.abs(cap).max():.4f} hw_rate={res.get('hw_rate')}")
    f_lo, f_hi = BANDS["m2i"]
    dres = I.ios_bulk_decode(out_name="m2i.pcm", channels=1, f_lo=f_lo, f_hi=f_hi,
                             n_sym=N_SYM, n_payloads=n_frames, seed_base=SEED_BASE)
    print(f"  [ON-DEVICE] verified={dres['verified']} blocks "
          f"({dres['verified_bytes']} B) span={dres['span_s']:.2f}s "
          f"GOODPUT = {dres['goodput_bps']/1000:.2f} kbps")
    for fr in dres.get("frames", []):
        if fr.get("ok"):
            print(f"    frame@{fr['start']/H.SR:.2f}s blocks={fr['blocks_ok']}/"
                  f"{fr['blocks_total']} verified={fr['verified']} evm={fr['evm']:.3f}")
    return dres


def i2m(profile="qam16-34", amp=0.95, n_frames=5):
    """iPhone speaker -> Mac mic, decoded on the Mac (modem.py)."""
    cfg = make_cfg("i2m", profile, amp)
    print(f"[i2m/{profile}] {cfg.describe()}")
    tx, payloads = build_tx(cfg, n_frames)
    H.mac_set_input_volume(22)
    rx = I.ios_to_mac(np.concatenate([tx, np.zeros(H.SR // 3, dtype=np.float32)]),
                      channels=1, sr=H.SR)
    np.save(os.path.join(DATA, f"rx_i2m_{profile}.npy"), rx)
    print(f"  Mac capture: rms={np.sqrt((rx**2).mean()):.5f} peak={np.abs(rx).max():.4f}")

    # ordered-expected: block j of payload i
    exp = [[payloads[i][j*M.CRC_BLOCK:(j+1)*M.CRC_BLOCK] for j in range(cfg.n_blocks)]
           for i in range(n_frames)]
    mf = np.abs(np.correlate(rx, cfg.chirp_wave, mode="valid"))
    thr = mf.max() * 0.4
    peaks, m = [], mf.copy()
    for _ in range(n_frames + 4):
        k = int(np.argmax(m))
        if m[k] < thr:
            break
        peaks.append(k)
        m[max(0, k - cfg.frame_samples // 2): k + cfg.frame_samples // 2] = 0
    peaks.sort()
    total_verified, used = 0, set()
    ok_starts = []
    for start in peaks:
        res = M.demodulate_frame(cfg, rx, start_hint=start)
        if not res.get("ok"):
            print(f"  frame@{start/H.SR:.2f}s FAILED {res.get('err')}")
            continue
        # ordered attribution against unused payloads
        best_i, best_m = -1, -1
        for i in range(n_frames):
            if i in used:
                continue
            mm = sum(1 for (j, cok, b) in res["blocks"] if cok and b == exp[i][j])
            if mm > best_m:
                best_m, best_i = mm, i
        v = best_m if best_m > 0 else 0
        if v > 0:
            used.add(best_i)
            ok_starts.append(start)
        total_verified += v
        print(f"  frame@{start/H.SR:.2f}s blocks={res['blocks_ok']}/{res['blocks_total']} "
              f"verified={v} (payload#{best_i}) evm={res['evm_rms']:.3f}")
    if ok_starts:
        span = (max(ok_starts) + cfg.frame_samples - min(ok_starts)) / H.SR
        gp = total_verified * M.CRC_BLOCK * 8 / span
        print(f"  [MAC] verified={total_verified} blocks ({total_verified*M.CRC_BLOCK} B) "
              f"span={span:.2f}s GOODPUT = {gp/1000:.2f} kbps")
    else:
        print("  [MAC] no frames verified")
    return total_verified


if __name__ == "__main__":
    direction = sys.argv[1] if len(sys.argv) > 1 else "m2i"
    profile = sys.argv[2] if len(sys.argv) > 2 else "qam16-34"
    amp = float(sys.argv[3]) if len(sys.argv) > 3 else 0.7
    n_frames = int(sys.argv[4]) if len(sys.argv) > 4 else 5
    if direction == "both":
        m2i(profile, amp, n_frames)
        i2m(profile, max(amp, 0.95), n_frames)
    elif direction == "m2i":
        m2i(profile, amp, n_frames)
    elif direction == "i2m":
        i2m(profile, amp, n_frames)
