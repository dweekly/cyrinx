#!/usr/bin/env python3
"""Legacy near-ultrasonic-band (>=18.5 kHz) OTA modem diagnostics at 96 kHz.

Usage: ultra_test.py {m2a|a2m} {qam16-34|qam64-34|qam64-56|adapt} [amp] [cp] [n_sym]

`adapt` bit-loads each bin from the per-bin SNR saved by the previous run of
the same direction (data/ultra_snr_<dir>.json), thresholds in LOAD_THRESH.
Decoded frames are attributed to a unique best-matching payload at the same
within-frame block position, but not to chronological scheduled slots. Reported
rates therefore remain diagnostic rather than flagship evidence. The script
also reports digital audible-band leakage of the TX waveform (power below
16 kHz relative to in-band power).
"""
import json
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H
import modem as M

SR = 96000
DATA = H.DATA
# inaudible band per direction (measured: a2m dies above ~21 kHz)
BANDS = {"m2a": (18500.0, 23900.0), "a2m": (18500.0, 21200.0)}
CHIRPS = {"m2a": (18600.0, 23400.0), "a2m": (18600.0, 21100.0)}

LOAD_THRESH = [(32.0, 8), (25.0, 6), (18.0, 4), (11.0, 2), (6.0, 1)]


def bits_for_snr(snr_db, margin_db=2.0):
    for th, b in LOAD_THRESH:
        if snr_db - margin_db >= th:
            return b
    return 0


def make_cfg(direction, profile, amp, cp, n_sym):
    f_lo, f_hi = BANDS[direction]
    c0, c1 = CHIRPS[direction]
    # receive band-pass spans both directions' chirps (down to 18.5k);
    # keep the low edge a bit under the chirp f0 to avoid edge ripple
    kw = dict(nfft=2048, cp=cp, sr=SR, chirp_f0=c0, chirp_f1=c1, amp=amp,
              n_sym=n_sym, track_alpha=0.35, rx_bandpass=(17500.0, min(f_hi + 800, 47000)))
    base = M.Config(f_lo, f_hi, **kw)
    if profile == "qam16-34":
        return M.Config(f_lo, f_hi, rate="3/4",
                        bits_per_bin={b: 4 for b in base.data_idx}, **kw)
    if profile == "qam64-34":
        return M.Config(f_lo, f_hi, rate="3/4",
                        bits_per_bin={b: 6 for b in base.data_idx}, **kw)
    if profile == "qam64-56":
        return M.Config(f_lo, f_hi, rate="5/6",
                        bits_per_bin={b: 6 for b in base.data_idx}, **kw)
    if profile == "adapt":
        with open(os.path.join(DATA, f"ultra_snr_{direction}.json")) as fh:
            snr = {int(k): v for k, v in json.load(fh)["snr_bin_db"].items()}
        load = {b: bits_for_snr(snr.get(b, -99)) for b in base.data_idx}
        return M.Config(f_lo, f_hi, rate="3/4", bits_per_bin=load, **kw)
    raise SystemExit(f"unknown profile {profile}")


def audible_leakage_db(tx):
    X = np.abs(np.fft.rfft(tx)) ** 2
    f = np.fft.rfftfreq(len(tx), 1 / SR)
    aud = X[f < 16000].sum()
    inband = X[(f >= 18000)].sum()
    return 10 * np.log10(aud / max(inband, 1e-20))


def run(direction, profile, amp=0.9, cp=512, n_sym=96, n_frames=3, gap_s=0.25):
    cfg = make_cfg(direction, profile, amp, cp, n_sym)
    print(f"[ultra {direction}/{profile}] {cfg.describe()}")
    payloads = [M.DetRng(1000 + i).bytes(cfg.payload_bytes) for i in range(n_frames)]
    waves = [M.modulate_frame(cfg, p) for p in payloads]
    gap = np.zeros(int(gap_s * SR), dtype=np.float32)
    parts = []
    for i, w in enumerate(waves):
        parts.append(w)
        parts.append(gap)
    parts.append(np.zeros(SR // 3, dtype=np.float32))
    tx = np.concatenate(parts)
    print(f"  TX audible leakage (<16 kHz vs in-band, digital): {audible_leakage_db(tx):.1f} dB")

    H.mac_set_output_volume(100)
    if direction == "m2a":
        st = np.zeros((len(tx), 2), dtype=np.float32)
        st[:, 0] = tx
        _, p = H.mac_to_android(st, out_name=f"ultra_{profile}.pcm", sr=SR)
        cap = H.load_pcm16(p)
        rx_list = [("mic0", cap[:, 0].astype(float))]
    else:
        H.mac_set_input_volume(40)
        rx = H.android_to_mac(tx, sr=SR)
        rx_list = [("macmic", rx.astype(float))]

    for name, rx in rx_list:
        rxf = M.bandpass(rx, 17500.0, min(BANDS[direction][1] + 800, 47000), SR)
        mf = np.abs(np.correlate(rxf, cfg.chirp_wave, mode="valid"))
        thr = mf.max() * 0.4
        m = mf.copy()
        starts = []
        for _ in range(n_frames + 4):
            k = int(np.argmax(m))
            if m[k] < thr:
                break
            starts.append(k)
            m[max(0, k - cfg.frame_samples // 2): k + cfg.frame_samples // 2] = 0
        starts.sort()
        verified = 0
        snr_acc = []
        seen_payloads = set()
        for s0 in starts:
            res = M.demodulate_frame(cfg, rx, start_hint=s0)
            if res.get("ok"):
                v, payload_index = max(
                    (sum(1 for block_index, valid, data in res["blocks"]
                         if valid
                         and data
                         == payload[block_index * M.CRC_BLOCK:(block_index + 1) * M.CRC_BLOCK]),
                     payload_index)
                    for payload_index, payload in enumerate(payloads))
                if payload_index in seen_payloads:
                    v = 0
                else:
                    seen_payloads.add(payload_index)
                verified += v
                snr_acc.append(res["snr_bin_db"])
                print(f"  [{name}] frame@{s0/SR:.2f}s: blocks {res['blocks_ok']}/"
                      f"{res['blocks_total']} verified={v} evm={res['evm_rms']:.3f}")
            else:
                print(f"  [{name}] frame@{s0/SR:.2f}s: FAILED {res.get('err')}")
        active_span = n_frames * cfg.frame_samples / SR + (n_frames - 1) * gap_s
        emitted_span = n_frames * cfg.frame_samples / SR + n_frames * gap_s + 1 / 3
        gp = verified * M.CRC_BLOCK * 8 / active_span
        gross_gp = verified * M.CRC_BLOCK * 8 / emitted_span
        print(
            f"  [{name}] TOTAL verified={verified} blocks "
            f"active_span={active_span:.2f}s "
            f"legacy_content_attributed_payload_rate={gp/1000:.2f} kbps "
            f"emitted_span={emitted_span:.2f}s gross_rate={gross_gp/1000:.2f} kbps "
            f"peak={np.abs(rx).max():.3f}"
        )
        if snr_acc:
            snr_mean = np.mean(np.stack(snr_acc), axis=0)
            with open(os.path.join(DATA, f"ultra_snr_{direction}.json"), "w") as fh:
                json.dump({"snr_bin_db": {int(b): float(s) for b, s in
                                          zip(cfg.used, snr_mean)}}, fh)
        return gp
    return 0.0


if __name__ == "__main__":
    d = sys.argv[1]
    prof = sys.argv[2] if len(sys.argv) > 2 else "qam16-34"
    amp = float(sys.argv[3]) if len(sys.argv) > 3 else 0.9
    cp = int(sys.argv[4]) if len(sys.argv) > 4 else 512
    n_sym = int(sys.argv[5]) if len(sys.argv) > 5 else 96
    run(d, prof, amp, cp, n_sym)
