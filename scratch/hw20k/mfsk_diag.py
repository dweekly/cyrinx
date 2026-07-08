#!/usr/bin/env python3
"""OTA diagnostic for the RS-coded MFSK floor (A3) — why does a cell fail?

Plays one MFSK frame (same known payload adaptive.py uses), saves the stereo
capture to data/, then decodes with full instrumentation per mic:
  - chirp correlation: top peaks + chosen offset (reflection-lock suspect)
  - raw nibble error rate vs the known TX stream (channel viability: RS(15,11)
    corrects (2*errors + erasures) <= 4 per 15, so raw SER above ~20-25%
    is beyond ANY floor code at this geometry)
  - detector confidence distribution vs ERASE_CONF (erasure calibration)
  - per-codeword: errors / erasures / RS success (code-budget accounting)
  - timing-offset sweep: raw SER at +/- offsets around the detected chirp
    (a minimum away from 0 => sync locked onto a reflection)

Usage:  mfsk_diag.py <label>            # OTA via harness (Pixel on adb)
        mfsk_diag.py replay <capture.npy>  # re-analyze a saved capture
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H
import mfsk

PAYLOAD = bytes((i * 53 + 7) & 0xFF for i in range(32))  # == adaptive.py's


def tx_stream():
    return mfsk._rs_stream(mfsk._frame_nibbles(PAYLOAD))


def raw_nibbles(rx, base):
    """Hard tone decisions + confidences for every stream position, given a
    frame base sample (mirrors mfsk.demodulate's detection loop)."""
    sr = mfsk.SR
    freqs = mfsk._tone_freqs()
    stream = tx_stream()
    n_syms = len(stream) // mfsk.N_BLOCKS
    slen = int(mfsk.T_SYM * sr)
    skip = int(mfsk.GUARD_SKIP * sr)
    wlen = slen - skip
    basis = np.exp(-2j * np.pi * np.outer(freqs, np.arange(wlen) / sr))
    nibs, confs = [], []
    for s in range(n_syms):
        a = base + s * slen + skip
        win = rx[a:a + wlen]
        if len(win) < wlen:
            win = np.pad(win, (0, wlen - len(win)))
        E = np.abs(basis @ win) ** 2
        for b in range(mfsk.N_BLOCKS):
            cands = E[b::mfsk.N_BLOCKS][:mfsk.TONES_PER_BLOCK]
            order = np.argsort(cands)
            nibs.append(int(order[-1]))
            confs.append(float(cands[order[-1]] / (cands[order[-2]] + 1e-30)))
    return nibs, confs


def analyze(rx, mic_name):
    sr = mfsk.SR
    stream = tx_stream()
    start = mfsk._find_chirp(rx, sr)
    clen = len(mfsk._chirp(mfsk.CHIRP_F0, mfsk.CHIRP_F1, mfsk.CHIRP_DUR, sr))
    base0 = start + clen + int(mfsk.GAP * sr)

    # chirp correlation landscape: top peaks within +/-0.5 s of the max
    ch = mfsk._chirp(mfsk.CHIRP_F0, mfsk.CHIRP_F1, mfsk.CHIRP_DUR, sr)
    corr = np.abs(np.correlate(rx, ch, "valid"))
    top = np.argsort(corr)[-5:][::-1]
    print(f"  [{mic_name}] chirp@{start} ({start/sr:.3f}s); top corr peaks "
          f"(samples rel chosen): {[int(t - start) for t in top]}")

    # timing sweep: raw SER vs base offset
    best = (1.0, 0)
    print(f"  [{mic_name}] timing sweep (offset_ms -> raw SER):")
    row = []
    for off_ms in range(-60, 61, 15):
        base = base0 + int(off_ms * sr / 1000)
        nibs, confs = raw_nibbles(rx, base)
        ser = float(np.mean([n != t for n, t in zip(nibs, stream)]))
        row.append(f"{off_ms:+d}:{ser:.2f}")
        if ser < best[0]:
            best = (ser, off_ms, nibs, confs)
    print(f"    {'  '.join(row)}")

    ser, off = best[0], best[1]
    nibs, confs = best[2], best[3]
    confs_np = np.array(confs)
    n_erase_flagged = int((confs_np < mfsk.ERASE_CONF).sum())
    print(f"  [{mic_name}] best offset {off:+d} ms: raw SER {ser:.1%} "
          f"({int(ser * len(stream))}/{len(stream)} nibbles wrong)")
    print(f"  [{mic_name}] confidence: median {np.median(confs_np):.2f}, "
          f"{n_erase_flagged}/{len(confs)} below ERASE_CONF={mfsk.ERASE_CONF}")

    # per-codeword budget at the best offset
    n_cw = len(stream) // mfsk.RS_N
    ok_cw = 0
    detail = []
    for c in range(n_cw):
        pos = [p * n_cw + c for p in range(mfsk.RS_N)]
        errs = sum(1 for p in pos if nibs[p] != stream[p])
        flagged = sum(1 for p in pos if confs[p] < mfsk.ERASE_CONF)
        # RS(15,11) corrects 2e + s <= 4 (e = unflagged errors, s = erasures)
        ok = errs * 2 <= mfsk.RS_NSYM or errs <= mfsk.RS_NSYM  # rough bound
        detail.append(f"cw{c}: {errs} err, {flagged} low-conf")
        ok_cw += int(errs * 2 <= mfsk.RS_NSYM)
    print(f"  [{mic_name}] {'; '.join(detail)}")
    print(f"  [{mic_name}] codewords within RS error budget (no erasure help): "
          f"{ok_cw}/{n_cw}")

    # what does the shipping decoder say on this mic?
    dec, crc = mfsk.demodulate(rx, len(PAYLOAD))
    print(f"  [{mic_name}] mfsk.demodulate: crc_ok={crc} "
          f"payload_match={dec == PAYLOAD}")
    return ser


def main():
    if sys.argv[1] == "replay":
        st = np.load(sys.argv[2])
        label = os.path.basename(sys.argv[2])
    else:
        label = sys.argv[1]
        H.mac_set_output_volume(100)
        H.mac_set_input_volume(22)
        _, p = H.mac_to_android(mfsk.modulate(PAYLOAD), out_name="mfskdiag.pcm")
        st = H.load_pcm16(p, channels=2)
        out = os.path.join(H.DATA, f"mfsk_diag_{label}.npy")
        np.save(out, st)
        print(f"capture saved: {out}  shape={st.shape} "
              f"rms={float(np.sqrt((st**2).mean())):.4f}")
    print(f"=== MFSK floor diagnostic @ {label} ===")
    for mic in range(st.shape[1] if st.ndim > 1 else 1):
        rx = st[:, mic] if st.ndim > 1 else st
        analyze(np.asarray(rx, dtype=np.float64), f"mic{mic}")


if __name__ == "__main__":
    main()
