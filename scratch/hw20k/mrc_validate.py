#!/usr/bin/env python3
"""Validate two-mic maximal-ratio combining over the air (ROADMAP #15).

The MRC demod already lives in modem.demodulate_frame(cfg, rx, rx2=...): it
combines the two mic captures per subcarrier (conj(H)-weighted), filling the
frequency nulls that sink either mic alone in a reverberant field. This driver
plays a frame, captures stereo, and compares decode on mic0 / mic1 / MRC.

Measured at a shadowed reverberant spot (edge_below_laptop, 2026-06-12) where
BOTH single mics decoded 0 blocks:
  QPSK r1/2 32ms-CP: mic0 0/11 (EVM 1.36), mic1 0/11 (EVM 1.63), MRC 8/11 (EVM 0.69)
MRC decodes where neither mic alone can -- selection < combining, decisively.

Usage:  mrc_validate.py <label>
"""
import sys
import numpy as np

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import harness as H
import modem as M


def _cfg(bpb, rate, cp, nfft, n_sym=24, f_lo=2000.0, f_hi=15000.0):
    di = M.Config(f_lo, f_hi, cp=cp, nfft=nfft).data_idx
    return M.Config(f_lo, f_hi, rate=rate, n_sym=n_sym, amp=0.6, cp=cp, nfft=nfft,
                    bits_per_bin={b: bpb for b in di})


def run(label):
    H.mac_set_output_volume(100)
    H.mac_set_input_volume(22)
    print(f"=== two-mic MRC vs single mic @ {label} ===")
    for bpb, rate, cp, nfft, name in [(2, "1/2", 1536, 4096, "QPSK r1/2 32ms-CP"),
                                      (4, "1/2", 2304, 4096, "16QAM r1/2 48ms-CP")]:
        cfg = _cfg(bpb, rate, cp, nfft)
        pl = bytes((i * 31 + 7) & 0xFF for i in range(cfg.payload_bytes))
        _, p = H.mac_to_android(M.modulate_frame(cfg, pl), out_name="mrc.pcm")
        st = H.load_pcm16(p, channels=2)

        def dec(a, b=None):
            r = M.demodulate_frame(
                cfg, np.asarray(st[:, a], float),
                rx2=(np.asarray(st[:, b], float) if b is not None else None))
            return r.get("blocks_ok", 0), r.get("evm_rms", r.get("evm", 9.9))

        o0, e0 = dec(0)
        o1, e1 = dec(1)
        om, em = dec(0, 1)
        nb = cfg.n_blocks
        print(f"  {name} ({nb} blk): mic0 {o0}/{nb} EVM{e0:.2f} | "
              f"mic1 {o1}/{nb} EVM{e1:.2f} | MRC {om}/{nb} EVM{em:.2f}")


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "default")
