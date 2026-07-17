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
import modem as M

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
MFSK_PAYLOAD = 32


def coherent_payload_goodput_bps(geometry, verified, total, span_s):
    """Return verified user-payload bits per active frame second."""
    if total <= 0 or not 0 <= verified <= total or span_s <= 0:
        raise ValueError("invalid goodput inputs")
    return geometry.payload_bytes * 8 * (verified / total) / span_s


def coherent_decode_rep(cfg, st, mic, pl):
    """One rep's coherent decode: library clib decode on the selected mic
    first; if that is imperfect, escalate to the LIBRARY two-mic MRC
    (cyrinx_bulk_demodulate2 via clib.decode2 — A2; validated against the
    Python reference modem.demodulate_frame(rx2=...) by xcompat_validate.py)
    and keep the better result. The whole path is library-native. Returns
    per-rep provenance so the JSONL shows exactly what single-mic delivered
    vs what MRC rescued."""
    total = clib.geometry(cfg).n_blocks
    mono = st[:, mic] if st.ndim > 1 else st
    d = clib.decode(cfg, np.ascontiguousarray(mono, dtype=np.float32))
    if d is not None and d["blocks_total"] != total:
        raise RuntimeError("decoder block total does not match scheduled geometry")
    clib_ok = _ordered_verified(d, pl)
    ver, path, rescued = clib_ok, "clib", 0
    if clib_ok < total and st.ndim > 1 and st.shape[1] >= 2:
        r = clib.decode2(cfg, st[:, 0], st[:, 1])
        if r is not None and r["blocks_total"] != total:
            raise RuntimeError("MRC decoder block total does not match scheduled geometry")
        mrc_ok = _ordered_verified(r, pl)
        if mrc_ok > clib_ok:
            ver, path, rescued = mrc_ok, "clib_mrc", mrc_ok - clib_ok
    return {"ver": ver, "total": total, "clib_ok": clib_ok,
            "path": path, "rescued": rescued}


def _ordered_verified(d, pl, blk=256):
    """Blocks CRC-valid AND byte-identical at their ordered position — the
    goodput definition the measured results use. (Byte-compare against the
    known transmitted payload per CRC block, capped by the decoder's CRC
    count; the old `payload == pl` shortcut gave partial decodes 0 credit.)"""
    return clib.ordered_verified_blocks(d, pl, blk)


def run(label, reps=3):
    H.mac_set_output_volume(100)
    H.mac_set_input_volume(22)

    def send(w):
        _, p = H.mac_to_android(w, out_name="adapt.pcm")
        return H.load_pcm16(p, channels=2)        # stereo -> the sounder/decode pick the mic

    print(f"=== adaptive link @ {label} ===")
    rec, _ = S.sound_channel_evm(send)
    mrc_note = (f" (mrcEVM={rec['probe_evm_mrc']} -> MRC tier)" if rec.get("via_mrc")
                else "")
    print(f"  sounded: probeEVM={rec['probe_evm']} ds15={rec['delay_spread_ms_15']}ms "
          f"-> recommend {rec['tier']} ({rec['mcs']} r{rec['rate']}){mrc_note}")

    if rec["noncoherent"]:
        # Coherent OFDM infeasible -> the non-coherent floor.
        payload = bytes((i * 53 + 7) & 0xFF for i in range(MFSK_PAYLOAD))
        okc = 0
        for _ in range(reps):
            # pass stereo: mfsk.demodulate does decode-based mic selection
            dec, crc = mfsk.demodulate(send(mfsk.modulate(payload)), MFSK_PAYLOAD)
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
        ver = tot = clib_ver = rescued = 0
        paths = []
        for _ in range(reps):
            pl = bytes((i * 31 + 7) & 0xFF for i in range(g.payload_bytes))
            st = np.asarray(send(clib.encode(cfg, pl)), dtype=np.float32)
            rep = coherent_decode_rep(cfg, st, mic, pl)
            ver += rep["ver"]
            tot += rep["total"]
            clib_ver += rep["clib_ok"]
            rescued += rep["rescued"]
            paths.append(rep["path"])
        span = len(clib.encode(cfg, pl)) / cfg.sr
        frac = ver / tot if tot else 0.0
        # Goodput counts verified user payload only.  ``info_bits`` also
        # includes each block's CRC plus random frame fill and therefore
        # overstates application delivery.
        gp_bps = coherent_payload_goodput_bps(g, ver, tot, span)
        mode = f"{rec['mcs']} r{rec['rate']}"
        print(f"  OFDM {mode}: {clib_ver}/{tot} clib + {rescued}/{tot} "
              f"MRC-rescued ({frac*100:.0f}%) -> {gp_bps/1000:.1f} kbps")
        result = {"mode": mode, "verified": ver, "total": tot,
                  "clib_verified": clib_ver, "mrc_rescued_blocks": rescued,
                  "decode_path": paths,
                  "goodput_kbps": round(gp_bps / 1000, 1)}
        delivered = gp_bps

    print(f"  => {label}: {mode}, {'LINKED' if delivered > 0 else 'FAILED TO LINK'} "
          f"({delivered:.0f} bps)")
    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, "adaptive_demo.jsonl"), "a") as f:
        f.write(json.dumps({"label": label, "recommend": rec["tier"],
                            "probe_evm": rec["probe_evm"],
                            "probe_evm_mrc": rec.get("probe_evm_mrc"),
                            "via_mrc": rec.get("via_mrc", False),
                            "delay_spread_ms_15": rec["delay_spread_ms_15"],
                            **result, "delivered_bps": round(delivered, 1)}) + "\n")
    return result


def _selftest():
    """Offline check of the clib-first / MRC-escalation decode path — no
    hardware, no sound. Synthesizes stereo captures from clib.encode digitally
    (A1 step 3, docs/A1_AUTO_MRC.md)."""
    import xcompat_validate as XV
    rng = np.random.default_rng(7)
    cfg = clib.make_cfg(bits_per_bin=4, rate="3/4", n_sym=16)
    g = clib.geometry(cfg)
    pl = bytes((i * 31 + 7) & 0xFF for i in range(g.payload_bytes))
    wave = clib.encode(cfg, pl).astype(np.float64)
    rx = np.concatenate([np.zeros(4000), wave, np.zeros(3000)])
    rms = float(np.sqrt(np.mean(wave ** 2)))
    sig = 0.1 * rms

    span = len(wave) / cfg.sr
    payload_bps = coherent_payload_goodput_bps(g, g.n_blocks, g.n_blocks, span)
    info_bps = g.info_bits / span
    assert payload_bps < info_bps
    assert coherent_payload_goodput_bps(g, 1, 2, span) == payload_bps / 2

    mask_payload = bytes((i * 17 + 3) & 0xFF for i in range(2 * 256))
    partial = {"payload": mask_payload, "blocks_ok": 1, "block_valid": [False, True]}
    assert _ordered_verified(partial, mask_payload) == 1
    corrupted = bytearray(mask_payload)
    corrupted[300] ^= 1
    partial["payload"] = bytes(corrupted)
    assert _ordered_verified(partial, mask_payload) == 0

    def stereo(m0, m1):
        return np.stack([m0, m1], axis=1).astype(np.float32)

    # 1: clean stereo -> clib decodes on the selected mic, MRC never invoked
    st = stereo(rx + rng.normal(0, sig, len(rx)),
                rx + rng.normal(0, sig, len(rx)))
    r = coherent_decode_rep(cfg, st, 0, pl)
    assert (r["path"], r["ver"], r["rescued"]) == ("clib", g.n_blocks, 0), r
    print(f"  clean stereo  -> path={r['path']} {r['ver']}/{r['total']} OK")

    # 2: selected mic notched hard (clib fails), other mic complementary
    #    -> escalation rescues via the LIBRARY MRC (cyrinx_bulk_demodulate2)
    m0 = XV.notch(rx, cfg.sr, XV.NOTCH_MIC0) + rng.normal(0, sig, len(rx))
    m1 = XV.notch(rx, cfg.sr, XV.NOTCH_MIC1) + rng.normal(0, sig, len(rx))
    r = coherent_decode_rep(cfg, stereo(m0, m1), 0, pl)
    assert r["path"] == "clib_mrc" and r["clib_ok"] < g.n_blocks \
        and r["ver"] == g.n_blocks and r["rescued"] == r["ver"] - r["clib_ok"], r
    print(f"  notched mic0  -> path={r['path']} clib {r['clib_ok']}/{r['total']}"
          f" + {r['rescued']} rescued = {r['ver']}/{r['total']} OK")

    # 3: mono capture (no second mic) -> degrades to clib-only, no crash
    r = coherent_decode_rep(cfg, m0.astype(np.float32), 0, pl)
    assert r["path"] == "clib" and r["rescued"] == 0, r
    print(f"  mono capture  -> path={r['path']} {r['ver']}/{r['total']} "
          f"(no escalation) OK")
    print("SELFTEST PASS")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    if sys.argv[1] == "selftest":
        _selftest()
        sys.exit(0)
    run(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 3)
