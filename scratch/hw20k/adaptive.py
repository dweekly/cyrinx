#!/usr/bin/env python3
"""Adaptive closed loop: sound the channel, auto-select coherent OFDM (at the
EVM-probe-recommended MCS) or the non-coherent MFSK floor, transmit, and measure
goodput. This is the integration that ties the library's robustness together --
the sounder routes between the OFDM ladder (BulkPHY / clib) and the MFSK floor
(mfsk.py), so the link adapts to the topology and never refuses to connect.

Run it at each orientation/configuration to build the graceful-degradation
evidence: every cell should deliver nonzero goodput, OFDM where it can and MFSK
where it can't.

Usage:  adaptive.py <label> [reps]
"""
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H
import sounder as S
import clib
import mfsk

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
MFSK_PAYLOAD = 32


def run(label, reps=3):
    H.mac_set_output_volume(100)
    H.mac_set_input_volume(22)

    def send(w):
        _, p = H.mac_to_android(w, out_name="adapt.pcm")
        return H.load_pcm16(p, channels=2)        # stereo -> the sounder/decode pick the mic

    print(f"=== adaptive link @ {label} ===")
    rec, _ = S.sound_channel_evm(send)
    print(f"  sounded: probeEVM={rec['probe_evm']} ds15={rec['delay_spread_ms_15']}ms "
          f"-> recommend {rec['tier']} ({rec['mcs']} r{rec['rate']})")

    if rec["noncoherent"]:
        # Coherent OFDM infeasible -> the non-coherent floor.
        payload = bytes((i * 53 + 7) & 0xFF for i in range(MFSK_PAYLOAD))
        okc = 0
        for _ in range(reps):
            mono, _ = S.best_channel(send(mfsk.modulate(payload)))
            dec, crc = mfsk.demodulate(mono, MFSK_PAYLOAD)
            okc += int(crc and dec == payload)
        gp_bps = mfsk.bitrate(MFSK_PAYLOAD) * (okc / reps)
        mode = "MFSK floor"
        print(f"  MFSK floor: {okc}/{reps} frames -> {gp_bps:.0f} bps")
        result = {"mode": mode, "frames_ok": okc, "frames": reps,
                  "goodput_bps": round(gp_bps, 1)}
        delivered = gp_bps
    else:
        # Coherent OFDM at the recommended MCS / CP.
        cfg = clib.make_cfg(bits_per_bin=rec["bits_uniform"], rate=rec["rate"],
                            n_sym=64, cp=rec["cp"], nfft=rec["nfft"])
        g = clib.geometry(cfg)
        mic = rec.get("mic", 0)
        ver = tot = 0
        for _ in range(reps):
            pl = bytes((i * 31 + 7) & 0xFF for i in range(g.payload_bytes))
            st = np.asarray(send(clib.encode(cfg, pl)), dtype=np.float32)
            mono = st[:, mic] if st.ndim > 1 else st
            d = clib.decode(cfg, np.ascontiguousarray(mono))
            if d:
                ver += d["blocks_ok"] if d["payload"] == pl else 0
                tot += d["blocks_total"]
        span = len(clib.encode(cfg, pl)) / cfg.sr
        frac = ver / tot if tot else 0.0
        gp_bps = g.info_bits * frac / span
        mode = f"{rec['mcs']} r{rec['rate']}"
        print(f"  OFDM {mode}: {ver}/{tot} blocks ({frac*100:.0f}%) -> {gp_bps/1000:.1f} kbps")
        result = {"mode": mode, "verified": ver, "total": tot,
                  "goodput_kbps": round(gp_bps / 1000, 1)}
        delivered = gp_bps

    print(f"  => {label}: {mode}, {'LINKED' if delivered > 0 else 'FAILED TO LINK'} "
          f"({delivered:.0f} bps)")
    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, "adaptive_demo.jsonl"), "a") as f:
        f.write(json.dumps({"label": label, "recommend": rec["tier"],
                            "probe_evm": rec["probe_evm"],
                            "delay_spread_ms_15": rec["delay_spread_ms_15"],
                            **result, "delivered_bps": round(delivered, 1)}) + "\n")
    return result


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    run(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 3)
