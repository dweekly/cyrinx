#!/usr/bin/env python3
"""Over-the-air modem trials: Mac <-> Pixel 7a, honest goodput measurement.

Usage:
  ota_test.py macloop [profile]    # Mac speaker -> Mac mic sanity loop
  ota_test.py m2a [profile]        # Mac speaker -> Pixel mics
  ota_test.py a2m [profile]        # Pixel speaker -> Mac mic
  ota_test.py both [profile]       # m2a then a2m
Profiles: qpsk12 qam16-12 qam16-34 qam64-34 qam64-56 adapt
`adapt` loads per-bin SNR from the most recent run of that direction
(data/snr_<dir>.json) and bit-loads each bin with a configurable margin.
"""

import json
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H
import modem as M

DATA = H.DATA
N_SYM = 64

# direction-specific usable bands (measured, NOTES.md)
BANDS = {"m2a": (1100.0, 23000.0), "a2m": (600.0, 17000.0), "macloop": (1100.0, 20000.0)}

# SNR(dB) thresholds for loading 1/2/4/6/8 bits (includes implementation margin)
LOAD_THRESH = [(27.0, 8), (21.0, 6), (15.0, 4), (9.0, 2), (5.0, 1)]


def bits_for_snr(snr_db, margin_db=3.0):
    for th, b in LOAD_THRESH:
        if snr_db - margin_db >= th:
            return b
    return 0


def make_cfg(direction, profile, amp, cp=768, nfft=2048):
    f_lo, f_hi = BANDS[direction]
    kw = dict(cp=cp, nfft=nfft)
    base = M.Config(f_lo=f_lo, f_hi=f_hi, **kw)  # to get data_idx
    if profile == "qpsk12":
        return M.Config(f_lo, f_hi, rate="1/2", n_sym=N_SYM, amp=amp, **kw)
    if profile == "qam16-12":
        return M.Config(f_lo, f_hi, rate="1/2", n_sym=N_SYM, amp=amp,
                        bits_per_bin={b: 4 for b in base.data_idx}, **kw)
    if profile == "qam16-34":
        return M.Config(f_lo, f_hi, rate="3/4", n_sym=N_SYM, amp=amp,
                        bits_per_bin={b: 4 for b in base.data_idx}, **kw)
    if profile == "qam64-34":
        return M.Config(f_lo, f_hi, rate="3/4", n_sym=N_SYM, amp=amp,
                        bits_per_bin={b: 6 for b in base.data_idx}, **kw)
    if profile == "qam64-56":
        return M.Config(f_lo, f_hi, rate="5/6", n_sym=N_SYM, amp=amp,
                        bits_per_bin={b: 6 for b in base.data_idx}, **kw)
    if profile == "adapt":
        path = os.path.join(DATA, f"snr_{direction}.json")
        with open(path) as fh:
            saved = json.load(fh)
        snr = {int(k): v for k, v in saved["snr_bin_db"].items()}
        load = {b: bits_for_snr(snr.get(b, -99)) for b in base.data_idx}
        return M.Config(f_lo, f_hi, rate="3/4", n_sym=N_SYM, amp=amp,
                        bits_per_bin=load, **kw)
    raise SystemExit(f"unknown profile {profile}")


def run_direction(direction, profile, amp=0.7, n_frames=3, gap_s=0.25, cp=768, nfft=2048):
    cfg = make_cfg(direction, profile, amp, cp=cp, nfft=nfft)
    print(f"[{direction}/{profile}] {cfg.describe()}")
    rng = np.random.default_rng(42)
    payloads = [rng.integers(0, 256, cfg.payload_bytes, dtype=np.uint8).tobytes()
                for _ in range(n_frames)]
    waves = [M.modulate_frame(cfg, p) for p in payloads]
    gap = np.zeros(int(gap_s * H.SR), dtype=np.float32)
    tx = np.concatenate([w for pair in zip(waves, [gap] * n_frames) for w in pair])

    if direction == "macloop":
        s = H._sd()
        H.mac_set_input_volume(30)
        pad = np.zeros(2 * H.SR, dtype=np.float32)
        rec = s.playrec(np.concatenate([tx, pad]), channels=1, dtype="float32")
        s.wait()
        rx_list = [("mac", rec[:, 0])]
    elif direction == "m2a":
        H.mac_set_output_volume(100)
        # Left speaker only: the phone sits by the left palm rest; the right
        # speaker arrives 3x weaker with badly decorrelated phase and its sum
        # with the left wrecks the composite EVM (measured 2026-06-09).
        st = np.zeros((len(tx) + H.SR // 3, 2), dtype=np.float32)
        st[: len(tx), 0] = tx        # trailing pad: last symbol must not abut
        _, p = H.mac_to_android(st, out_name=f"ota_{profile}.pcm")  # stream end
        cap = H.load_pcm16(p)
        rx_list = [("mic0", cap[:, 0]), ("mic1", cap[:, 1])]
    elif direction == "a2m":
        H.mac_set_input_volume(40)
        rx = H.android_to_mac(np.concatenate([tx, np.zeros(H.SR // 3, dtype=np.float32)]))
        rx_list = [("macmic", rx)]

    np.save(os.path.join(DATA, f"rx_{direction}_{profile}.npy"), rx_list[0][1])
    np.save(os.path.join(DATA, f"tx_{direction}_{profile}.npy"), tx)

    def find_all_chirps(rx):
        mf = np.abs(np.correlate(rx, M.CHIRP, mode="valid"))
        thr = mf.max() * 0.4
        peaks = []
        m = mf.copy()
        for _ in range(n_frames + 4):
            k = int(np.argmax(m))
            if m[k] < thr:
                break
            peaks.append(k)
            lo = max(0, k - cfg.frame_samples // 2)
            m[lo: k + cfg.frame_samples // 2] = 0
        return sorted(peaks)

    # expected per-block content across all payloads for verification
    exp_blocks = {}
    for pi, pl in enumerate(payloads):
        for j in range(cfg.n_blocks):
            exp_blocks[pl[j*M.CRC_BLOCK:(j+1)*M.CRC_BLOCK]] = (pi, j)

    best = None
    for name, rx in rx_list:
        peak = np.abs(rx).max()
        res_all = []
        snr_acc = []
        starts = find_all_chirps(rx)
        for i, start in enumerate(starts):
            res = M.demodulate_frame(cfg, rx, start_hint=start)
            if res.get("ok"):
                res_all.append(res)
                snr_acc.append(res["snr_bin_db"])
                ok, tot = res["blocks_ok"], res["blocks_total"]
                print(f"  [{name}] frame@{start/H.SR:.2f}s: blocks {ok}/{tot} "
                      f"evm={res['evm_rms']:.3f} goodput={res['goodput_bps']/1000:.2f} kbps")
            else:
                print(f"  [{name}] frame@{start/H.SR:.2f}s: FAILED {res.get('err')}")
        if res_all:
            tot_ok = sum(r["blocks_ok"] for r in res_all)
            tot_blk = sum(r["blocks_total"] for r in res_all)
            # verify decoded blocks byte-for-byte against transmitted content
            verified = 0
            for r in res_all:
                got = r["payload"]
                for g in range(len(got) // M.CRC_BLOCK):
                    if got[g*M.CRC_BLOCK:(g+1)*M.CRC_BLOCK] in exp_blocks:
                        verified += 1
            n_dec = len(res_all)
            airtime = n_dec * cfg.airtime_s + (n_dec - 1) * gap_s
            gp = verified * M.CRC_BLOCK * 8 / airtime
            print(f"  [{name}] TOTAL: {tot_ok}/{tot_blk} CRC-ok, {verified} verified "
                  f"against TX bytes, peak={peak:.3f}, "
                  f"GOODPUT (incl gaps) = {gp/1000:.2f} kbps over {airtime:.1f}s")
            if best is None or gp > best[1]:
                best = (name, gp, snr_acc)
    # save per-bin SNR for adaptive loading next run
    if best and best[2]:
        snr_mean = np.mean(np.stack(best[2]), axis=0)
        out = {"snr_bin_db": {int(b): float(s) for b, s in zip(cfg.used, snr_mean)}}
        with open(os.path.join(DATA, f"snr_{direction}.json"), "w") as fh:
            json.dump(out, fh)
    return best


if __name__ == "__main__":
    direction = sys.argv[1] if len(sys.argv) > 1 else "macloop"
    profile = sys.argv[2] if len(sys.argv) > 2 else "qpsk12"
    amp = float(sys.argv[3]) if len(sys.argv) > 3 else 0.7
    cp = int(sys.argv[4]) if len(sys.argv) > 4 else 768
    nfft = int(sys.argv[5]) if len(sys.argv) > 5 else 2048
    if direction == "both":
        run_direction("m2a", profile, amp, cp=cp, nfft=nfft)
        run_direction("a2m", profile, amp, cp=cp, nfft=nfft)
    else:
        run_direction(direction, profile, amp, cp=cp, nfft=nfft)
