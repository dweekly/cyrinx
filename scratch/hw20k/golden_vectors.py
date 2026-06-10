#!/usr/bin/env python3
"""Emit canonical golden vectors for the bulk PHY — the cross-implementation
contract (docs/PUBLICATION.md PR 1.1).

The Python modem (modem.py) is the *semantic oracle*. The portable-C core (and
the Swift/Kotlin bindings) must reproduce these stage outputs at the TIERED
tolerance recorded in the manifest:

  - "exact"  : byte-for-byte identical. The deterministic integer pipeline —
               DetRng, CRC, conv-encode, puncture, interleave permutation,
               interleaved bits, and the final decoded payload. A different FFT
               cannot excuse a mismatch here.
  - "float"  : within a documented absolute tolerance. FFT/IFFT-derived values
               (QAM constellation points, OFDM time samples, the TX waveform).
               Different FFT backends differ by ~ULPs, so we assert closeness,
               not equality.

Each artifact is a little-endian binary blob; the manifest records its dtype,
shape, SHA-256, and tolerance. Bit arrays are one uint8 per bit (0/1).
Complex arrays are interleaved float64 (re, im). The fixtures are intentionally
SMALL (a single CRC block, few OFDM symbols) — every code path is exercised
identically by a tiny frame, and small blobs stay friendly to a public repo.

Usage:
  golden_vectors.py emit     # (re)generate fixtures under Tests/Fixtures/golden
  golden_vectors.py verify   # recompute and check against the committed manifest
"""
import hashlib
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import modem as M

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.normpath(os.path.join(HERE, "..", "..", "Tests", "Fixtures", "golden"))

# Absolute tolerance for FFT-derived float artifacts, against the float32
# reference stored here. A correct *double-precision* C FFT of length<=2048
# agrees with the numpy reference to ~1e-9; float32 storage quantizes to ~1e-7;
# 1e-5 sits comfortably above both while still catching a real structural bug
# (wrong scaling, wrong bin, sign error). A single-precision backend (e.g. a
# vDSP fast path) may legitimately need a looser per-backend tolerance — that is
# the consumer test's call, documented where it loosens this default.
FLOAT_ABS_TOL = 1e-5


def _cfg_qpsk():
    # Real bulk-PHY parameters (NFFT 2048, CP 768, default QPSK r1/2), but the
    # smallest n_sym that still carries exactly one 256-byte CRC block.
    cfg = M.Config(nfft=2048, cp=768, rate="1/2", n_sym=4)
    assert cfg.n_blocks >= 1, "config too small to carry a CRC block"
    return cfg


def _cfg_qam16():
    base = M.Config(nfft=2048, cp=768)
    cfg = M.Config(nfft=2048, cp=768, rate="3/4", n_sym=4,
                   bits_per_bin={b: 4 for b in base.data_idx})
    assert cfg.n_blocks >= 1
    return cfg


CASES = [
    ("qpsk_r12", _cfg_qpsk, 0x1234),
    ("qam16_r34", _cfg_qam16, 0x5678),
]


def _as_le_bytes(arr):
    """Canonical little-endian byte serialization for a numpy array."""
    a = np.ascontiguousarray(arr)
    if np.iscomplexobj(a):
        a = a.astype("<c8")           # interleaved float32 re/im, LE
    elif a.dtype == np.float64:
        a = a.astype("<f4")           # float32 reference (matches the TX wave dtype)
    elif a.dtype == np.int64:
        a = a.astype("<i8")
    elif a.dtype == np.uint8:
        a = a.astype(np.uint8)
    else:
        raise TypeError(f"unsupported dtype {a.dtype}")
    return a.tobytes()


# stage name -> tolerance class
TOL = {
    "payload": "exact", "stream_with_crc": "exact", "info_bits": "exact",
    "pad_info_bits": "exact", "coded_bits": "exact", "punctured_bits": "exact",
    "pad_fill_bits": "exact", "coded_filled_bits": "exact",
    "interleave_perm": "exact", "interleaved_bits": "exact",
    "decoded_payload": "exact",
    "pilots": "float", "sync_freq": "float", "data_freq": "float",
    # The full `wave` validates the IFFT transitively and is the TX contract.
    # ofdm_time_raw (post-IFFT pre-normalization) and ofdm_time_norm (= wave tail)
    # are intentionally NOT checked in — they remain available via modulate_frame's
    # `taps` for local IFFT-isolation debugging without bloating the repo.
    "wave": "float",
}


def _storage_dtype(arr):
    """The on-disk dtype produced by _as_le_bytes (for the manifest reader)."""
    if np.iscomplexobj(arr):
        return "complex64"            # interleaved float32 re/im
    if arr.dtype == np.float64:
        return "float32"
    return str(arr.dtype)


def build_case(name, cfg, seed):
    payload = M.DetRng(seed).bytes(cfg.payload_bytes)
    taps = {}
    wave = M.modulate_frame(cfg, payload, taps=taps)
    # round-trip decode (noiseless digital loopback at a known offset) -> the
    # byte-exact RX target for the receiver port (PR 1.3).
    pre = 3000
    rx = np.concatenate([np.zeros(pre), wave.astype(float), np.zeros(2000)])
    res = M.demodulate_frame(cfg, rx)
    assert res["blocks_ok"] == res["blocks_total"], "loopback decode failed"
    assert res["payload"] == payload, "decoded payload != input"

    arts = {"payload": np.frombuffer(payload, dtype=np.uint8).copy()}
    arts.update({k: v for k, v in taps.items() if k in TOL})
    arts["decoded_payload"] = np.frombuffer(res["payload"], dtype=np.uint8).copy()

    cfgmeta = {
        "nfft": cfg.nfft, "cp": cfg.cp, "sr": cfg.sr, "rate": cfg.rate_name,
        "n_sym": cfg.n_sym, "amp": cfg.amp, "clip_sigma": cfg.clip_sigma,
        "bin_lo": int(cfg.bin_lo), "bin_hi": int(cfg.bin_hi),
        "pilot_idx": cfg.pilot_idx.tolist(), "data_bins": list(map(int, cfg.data_bins)),
        "bits_per_bin": {int(b): int(cfg.bits_per_bin.get(b, 0)) for b in cfg.data_bins},
        "n_blocks": cfg.n_blocks, "payload_bytes": cfg.payload_bytes,
        "info_bits": cfg.info_bits, "bits_per_sym": cfg.bits_per_sym,
        "rx_offset": pre, "seed": seed,
    }
    return arts, cfgmeta


def emit():
    manifest = {"format": 1, "float_abs_tol": FLOAT_ABS_TOL,
                "note": "Golden vectors for the Cyrinx bulk PHY. See golden_vectors.py.",
                "cases": []}
    for name, cfg_fn, seed in CASES:
        cfg = cfg_fn()
        arts, cfgmeta = build_case(name, cfg, seed)
        cdir = os.path.join(OUT, name)
        os.makedirs(cdir, exist_ok=True)
        entry = {"name": name, "config": cfgmeta, "artifacts": []}
        for stage, arr in arts.items():
            blob = _as_le_bytes(arr)
            fn = f"{stage}.bin"
            with open(os.path.join(cdir, fn), "wb") as fh:
                fh.write(blob)
            entry["artifacts"].append({
                "stage": stage, "file": fn, "tolerance": TOL[stage],
                "dtype": _storage_dtype(arr),
                "shape": list(arr.shape), "bytes": len(blob),
                "sha256": hashlib.sha256(blob).hexdigest(),
            })
        manifest["cases"].append(entry)
        print(f"  {name}: {cfg.describe()}")
        print(f"    {len(entry['artifacts'])} artifacts, "
              f"{sum(a['bytes'] for a in entry['artifacts'])/1024:.0f} KB")
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=2)
    print(f"wrote {OUT}/manifest.json ({len(manifest['cases'])} cases)")


def verify():
    mpath = os.path.join(OUT, "manifest.json")
    manifest = json.load(open(mpath))
    bad = 0
    for name, cfg_fn, seed in CASES:
        cfg = cfg_fn()
        arts, _ = build_case(name, cfg, seed)
        entry = next(c for c in manifest["cases"] if c["name"] == name)
        amap = {a["stage"]: a for a in entry["artifacts"]}
        for stage, arr in arts.items():
            want = amap[stage]["sha256"]
            got = hashlib.sha256(_as_le_bytes(arr)).hexdigest()
            if got != want:
                print(f"  MISMATCH {name}/{stage}: {got[:12]} != {want[:12]}")
                bad += 1
        print(f"  {name}: {len(arts)} artifacts checked")
    if bad:
        print(f"FAILED: {bad} mismatches")
        sys.exit(1)
    print("all golden vectors reproduce the committed manifest")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "emit"
    if cmd == "emit":
        emit()
    elif cmd == "verify":
        verify()
    else:
        print(__doc__)
        sys.exit(2)
