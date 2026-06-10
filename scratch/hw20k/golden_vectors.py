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

  - "input" : not asserted. The receive waveform `rx_wave` fed to the RX port;
              the contract on the RX side is that decoding it reproduces
              `decoded_payload` (exact) and recovers all blocks.

Each artifact is a little-endian binary blob; the manifest records its dtype,
shape, SHA-256, and tolerance. Bit arrays are one uint8 per bit (0/1); complex
arrays are interleaved float32 (re, im); float arrays are float32. The fixtures
are intentionally SMALL (a single CRC block, few OFDM symbols) — every code path
is exercised by a tiny frame, and small blobs stay friendly to a public repo.

Coverage is asserted, not assumed: at least one case must exercise the PRBS
capacity-fill branch (non-empty pad_fill_bits), and every case ships an rx_wave
so the receiver port has a stable receive fixture.

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
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
OUT = os.path.join(REPO, "Tests", "Fixtures", "golden")
# generated C manifest header lives with its C consumer (a test-support target)
C_HEADER = os.path.join(REPO, "Tests", "CGoldenVectors", "golden_manifest.h")

# Absolute tolerance for FFT-derived float artifacts, against the float32
# reference stored here. A correct *double-precision* C FFT of length<=2048
# agrees with the numpy reference to ~1e-9; float32 storage quantizes to ~1e-7;
# 1e-5 sits comfortably above both while still catching a real structural bug
# (wrong scaling, wrong bin, sign error). A single-precision backend (e.g. a
# vDSP fast path) may legitimately need a looser per-backend tolerance — that is
# the consumer test's call, documented where it loosens this default.
FLOAT_ABS_TOL = 1e-5

# A fixed, deterministic multipath channel applied to the TX waveform to produce
# the RX fixture (rx_wave). Echoes WITHIN the CP force the receiver port to
# actually estimate and equalize H — a unity/noiseless channel would let a broken
# channel-estimator still decode. No random noise, so the fixture is exactly
# reproducible and still decodes 100% (the byte-exact RX target). Taps are
# (delay_samples, gain); the main tap is unity at delay 0. Max delay 260 < CP 768.
RX_CHANNEL_TAPS = [(0, 1.0), (37, 0.45), (113, -0.22), (260, 0.12)]
RX_PRE_PAD = 3000     # leading silence before the frame in rx_wave
RX_POST_PAD = 2000    # trailing silence


def _apply_channel(wave, taps):
    span = len(wave) + max(d for d, _ in taps)
    out = np.zeros(span, dtype=float)
    for delay, gain in taps:
        out[delay:delay + len(wave)] += gain * wave
    return out


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


def _cfg_qpsk_r23():
    # Exercises the seed-8 PRBS capacity-fill branch (modem.py): rate 2/3 leaves
    # a non-empty pad_fill after puncturing. The modem sizes the payload to fill
    # capacity, so pad_fill only ever absorbs the floor() rounding remainder —
    # structurally 0 or 1 bit across ALL rate/n_sym combos (verified). This case
    # pins the 1-bit case: a C port that omits the fill writes a different
    # interleaved_bits.bin and fails the exact-stage check.
    cfg = M.Config(nfft=2048, cp=768, rate="2/3", n_sym=4)
    assert cfg.n_blocks >= 1
    return cfg


CASES = [
    ("qpsk_r12", _cfg_qpsk, 0x1234),
    ("qam16_r34", _cfg_qam16, 0x5678),
    ("qpsk_r23", _cfg_qpsk_r23, 0x9ABC),
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
    "rx_wave": "input",   # the receive waveform the RX port decodes (PR 1.3)
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
    # RX fixture: TX through the fixed multipath channel, with leading/trailing
    # silence, at a known offset. Decoding THIS waveform must reproduce the
    # payload byte-for-byte and recover every block (the RX-side contract, 1.3).
    body = _apply_channel(wave.astype(float), RX_CHANNEL_TAPS)
    rx = np.concatenate([np.zeros(RX_PRE_PAD), body, np.zeros(RX_POST_PAD)])
    res = M.demodulate_frame(cfg, rx)
    assert res["blocks_ok"] == res["blocks_total"], \
        f"{name}: rx_wave decode {res['blocks_ok']}/{res['blocks_total']}"
    assert res["payload"] == payload, f"{name}: decoded payload != input"

    arts = {"payload": np.frombuffer(payload, dtype=np.uint8).copy()}
    arts.update({k: v for k, v in taps.items() if k in TOL})
    arts["rx_wave"] = rx.astype(np.float64)   # serialized as float32 like `wave`
    arts["decoded_payload"] = np.frombuffer(res["payload"], dtype=np.uint8).copy()

    cfgmeta = {
        "nfft": cfg.nfft, "cp": cfg.cp, "sr": cfg.sr, "rate": cfg.rate_name,
        "n_sym": cfg.n_sym, "amp": cfg.amp, "clip_sigma": cfg.clip_sigma,
        "bin_lo": int(cfg.bin_lo), "bin_hi": int(cfg.bin_hi),
        "pilot_idx": cfg.pilot_idx.tolist(), "data_bins": list(map(int, cfg.data_bins)),
        "bits_per_bin": {int(b): int(cfg.bits_per_bin.get(b, 0)) for b in cfg.data_bins},
        "n_blocks": cfg.n_blocks, "payload_bytes": cfg.payload_bytes,
        "info_bits": cfg.info_bits, "bits_per_sym": cfg.bits_per_sym,
        "seed": seed,
        # full generating config so a port can reconstruct it without guessing.
        # f_lo/f_hi as exact bin edges recover the same bin_lo/bin_hi via ceil/floor.
        "f_lo": float(cfg.bin_lo * cfg.bin_hz), "f_hi": float(cfg.bin_hi * cfg.bin_hz),
        "pilot_every": int(cfg.pilot_idx[1] - cfg.pilot_idx[0])
        if len(cfg.pilot_idx) > 1 else 8,
        "bits_per_bin_uniform": int(cfg.bits_per_bin[cfg.data_bins[0]]),
        "chirp_f0": float(M.CHIRP_F0), "chirp_f1": float(M.CHIRP_F1),
        # RX-side expectations + reproducibility metadata
        "rx_pre_pad": RX_PRE_PAD, "rx_post_pad": RX_POST_PAD,
        "rx_channel_taps": RX_CHANNEL_TAPS,
        "decode_blocks_ok": int(res["blocks_ok"]),
        "decode_blocks_total": int(res["blocks_total"]),
        "pad_fill_bits_len": int(taps["pad_fill_bits"].size),
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
              f"{sum(a['bytes'] for a in entry['artifacts'])/1024:.0f} KB, "
              f"pad_fill={cfgmeta['pad_fill_bits_len']} bits")
    # coverage guarantee: at least one case must exercise the capacity-fill branch
    fill = [c["config"]["pad_fill_bits_len"] for c in manifest["cases"]]
    assert any(n > 0 for n in fill), \
        "no case exercises the PRBS capacity-fill branch (non-empty pad_fill_bits)"
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=2)
    os.makedirs(os.path.dirname(C_HEADER), exist_ok=True)
    generate_c_header(manifest, C_HEADER)
    print(f"wrote {OUT}/manifest.json + {C_HEADER} "
          f"({len(manifest['cases'])} cases)")


def generate_c_header(manifest, path):
    """Emit a C-consumable view of the manifest so the portable-C port can
    locate and size each artifact without a JSON parser (PR 1.1's C-side rig)."""
    def cid(s):
        return s.replace("-", "_").replace(".", "_")
    lines = [
        "/* Generated by scratch/hw20k/golden_vectors.py — DO NOT EDIT. */",
        "/* C-consumable manifest of the bulk-PHY golden vectors. */",
        "#ifndef CYRINX_GOLDEN_MANIFEST_H",
        "#define CYRINX_GOLDEN_MANIFEST_H",
        "#include <stddef.h>",
        "",
        "typedef struct {",
        "    const char *stage, *file, *dtype, *tolerance, *sha256;",
        "    size_t bytes;",
        "} cyrinx_golden_artifact;",
        "",
        "typedef struct {",
        "    const char *name;",
        "    size_t n_artifacts;",
        "    const cyrinx_golden_artifact *artifacts;",
        "} cyrinx_golden_case;",
        "",
        f"#define CYRINX_GOLDEN_FLOAT_ABS_TOL {manifest['float_abs_tol']:.6g}",
        "",
    ]
    for c in manifest["cases"]:
        lines.append(f"static const cyrinx_golden_artifact "
                     f"cyrinx_golden__{cid(c['name'])}[] = {{")
        for a in c["artifacts"]:
            lines.append(
                f'    {{"{a["stage"]}", "{c["name"]}/{a["file"]}", "{a["dtype"]}", '
                f'"{a["tolerance"]}", "{a["sha256"]}", {a["bytes"]}}},')
        lines.append("};")
        lines.append("")
    lines.append("static const cyrinx_golden_case cyrinx_golden_cases[] = {")
    for c in manifest["cases"]:
        n = len(c["artifacts"])
        lines.append(f'    {{"{c["name"]}", {n}, cyrinx_golden__{cid(c["name"])}}},')
    lines.append("};")
    lines.append("static const size_t cyrinx_golden_ncases = "
                 f"{len(manifest['cases'])};")
    lines.append("")
    lines.append("#endif /* CYRINX_GOLDEN_MANIFEST_H */")
    with open(path, "w") as fh:
        fh.write("\n".join(lines) + "\n")


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
