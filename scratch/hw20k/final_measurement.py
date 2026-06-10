#!/usr/bin/env python3
"""Definitive bidirectional goodput measurement, decoded by the receiving device.

Mac -> Android: Mac left speaker plays 16QAM-r3/4 frames; the Pixel records
  through its own mic and demodulates ON DEVICE (BulkDemod.kt); the verified
  goodput line comes from the phone's logcat.
Android -> Mac: Pixel speaker plays; the Mac records through its own mic and
  demodulates locally (modem.py).

Goodput = payload bytes that are CRC32-valid AND byte-identical to the
transmitted DetRng PRBS, divided by the airtime span from the first frame's
chirp to the last frame's final data sample (preambles, pilots, FEC, CRC,
and inter-frame gaps all included).
"""

import os
import sys
import time
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H
import modem as M

N_FRAMES = 5
GAP_S = 0.25
N_SYM = 64
SEED_BASE = 1000


def build_tx(cfg):
    payloads = [M.DetRng(SEED_BASE + i).bytes(cfg.payload_bytes) for i in range(N_FRAMES)]
    waves = [M.modulate_frame(cfg, p) for p in payloads]
    gap = np.zeros(int(GAP_S * H.SR), dtype=np.float32)
    parts = []
    for i, w in enumerate(waves):
        parts.append(w)
        if i < N_FRAMES - 1:
            parts.append(gap)
    parts.append(np.zeros(H.SR // 3, dtype=np.float32))   # stream-end pad
    return np.concatenate(parts), payloads


def measure_m2a():
    print("=== Mac -> Android (decoded on the Pixel) ===")
    cfg = M.Config(1100, 23000, rate="3/4", n_sym=N_SYM, amp=0.7, cp=768, nfft=2048,
                   bits_per_bin={b: 4 for b in M.Config(1100, 23000, cp=768, nfft=2048).data_idx})
    print("  profile:", cfg.describe())
    tx, _ = build_tx(cfg)
    st = np.zeros((len(tx), 2), dtype=np.float32)
    st[:, 0] = tx                      # left speaker only
    H.mac_set_output_volume(100)
    dur = len(tx) / H.SR + 10.0
    H.android_record_start(dur, channels=2, out_name="final_m2a.pcm")
    time.sleep(0.7)
    H.mac_play(st, block=True)
    H.logcat_wait("rec_pcm done", dur + 25)
    # decode ON DEVICE (capture never leaves the phone)
    H.logcat_clear()
    H.adb("shell am start -n com.dweekly.cyrinxhil/.MainActivity "
          "--es cmd bulk_decode "
          "--es path /data/user/0/com.dweekly.cyrinxhil/files/final_m2a.pcm "
          "--ei channels 2 --ef f_lo 1100 --ef f_hi 23000 "
          f"--ei n_sym {N_SYM} --ei n_payloads {N_FRAMES} --ei payload_seed_base {SEED_BASE}")
    line = H.logcat_wait("bulk_decode TOTAL", 180)
    out = H.adb("logcat -d -s CyrinxHILAndroid").stdout
    for ln in out.splitlines():
        if "bulk_decode" in ln:
            print("  " + ln.split("CyrinxHILAndroid: ")[-1].strip())
    return line


def measure_a2m():
    print("=== Android -> Mac (decoded on the Mac) ===")
    cfg = M.Config(600, 17000, rate="3/4", n_sym=N_SYM, amp=0.7, cp=768, nfft=2048,
                   bits_per_bin={b: 4 for b in M.Config(600, 17000, cp=768, nfft=2048).data_idx})
    print("  profile:", cfg.describe())
    tx, payloads = build_tx(cfg)
    H.mac_set_input_volume(22)
    rx = H.android_to_mac(tx)
    np.save(os.path.join(H.DATA, "final_a2m_rx.npy"), rx)

    exp_blocks = set()
    for pl in payloads:
        for j in range(cfg.n_blocks):
            exp_blocks.add(pl[j * M.CRC_BLOCK:(j + 1) * M.CRC_BLOCK])

    mf = np.abs(np.correlate(rx, M.CHIRP, mode="valid"))
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
    verified = 0
    decoded = []
    for s0 in starts:
        res = M.demodulate_frame(cfg, rx, start_hint=s0)
        if res.get("ok"):
            v = sum(1 for g in range(len(res["payload"]) // M.CRC_BLOCK)
                    if res["payload"][g * M.CRC_BLOCK:(g + 1) * M.CRC_BLOCK] in exp_blocks)
            verified += v
            decoded.append(s0)
            print(f"  frame@{s0/H.SR:.2f}s: blocks {res['blocks_ok']}/{res['blocks_total']} "
                  f"verified={v} evm={res['evm_rms']:.3f}")
        else:
            print(f"  frame@{s0/H.SR:.2f}s: FAILED {res.get('err')}")
    if decoded:
        span = (max(decoded) + cfg.frame_samples - min(decoded)) / H.SR
        gp = verified * M.CRC_BLOCK * 8 / span
        print(f"  a2m TOTAL: verified={verified} blocks ({verified*M.CRC_BLOCK} bytes) "
              f"span={span:.2f}s goodput={gp:.0f} bps")
        return gp
    return 0.0


if __name__ == "__main__":
    m2a_line = measure_m2a()
    a2m_gp = measure_a2m()
    print()
    print("=== SUMMARY ===")
    print("  m2a (phone-decoded):", m2a_line.split("CyrinxHILAndroid: ")[-1].strip())
    print(f"  a2m (mac-decoded):   goodput={a2m_gp:.0f} bps")
