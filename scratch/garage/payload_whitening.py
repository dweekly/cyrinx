"""Paired digital loopbacks of application data with and without reversible XOR whitening."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scratch/hw20k"))
import clib


def compare(codec, profile):
    cfg = clib.make_cfg(bits_per_bin=[1, 2, 4, 6, 8][profile["modulation"]],
                        rate=["1/2", "2/3", "3/4", "5/6"][profile["codeRate"]],
                        n_sym=profile["symbolCount"], f_lo=profile["lowFrequencyHz"],
                        f_hi=profile["highFrequencyHz"], nfft=profile["fftSize"], cp=profile["cyclicPrefix"],
                        sr=profile["sampleRate"], amp=profile["amplitude"], clip_sigma=profile["clipSigma"],
                        pilot_every=profile["pilotEvery"])
    cfg.chirp_f0, cfg.chirp_f1 = profile["chirpF0"], profile["chirpF1"]
    geometry = codec.geometry(cfg)
    if geometry.n_blocks != profile["blockCount"]:
        raise ValueError("registry geometry mismatch")
    length = geometry.payload_bytes
    dense = np.frombuffer(np.random.default_rng(112223).bytes(length), dtype=np.uint8)
    short = np.zeros_like(dense)
    short[:128] = dense[:128]
    sparse = np.zeros_like(dense).reshape(-1, 256)
    sparse[:, :8] = dense.reshape(-1, 256)[:, :8]
    pad = np.frombuffer(np.random.default_rng(20261122).bytes(length), dtype=np.uint8)
    rows = []
    for name, data in [("zero", np.zeros_like(dense)), ("short", short), ("sparse", sparse.ravel()),
                       ("constant-0x71", np.full_like(dense, 0x71)), ("random", dense)]:
        for whiten in [False, True]:
            wire = np.bitwise_xor(data, pad) if whiten else data
            decoded = codec.decode(cfg, codec.encode(cfg, wire.tobytes()))
            if decoded is None:
                raise ValueError("decoder failed to produce a result")
            recovered = np.frombuffer(decoded["payload"], dtype=np.uint8)
            if whiten:
                recovered = np.bitwise_xor(recovered, pad)
            mask = np.asarray(decoded["block_valid"], dtype=bool)
            correct = np.all((recovered == data).reshape(-1, 256), axis=1)
            rows.append(dict(profile=profile["id"], pattern=name, whitened=whiten, total=len(mask),
                             verified=int(np.count_nonzero(mask & correct)),
                             wrong_crc_valid=int(np.count_nonzero(mask & ~correct))))
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    core = ROOT / "Sources/CCyrinx"
    profiles = json.loads((ROOT / "Tests/Fixtures/profiles/cyrinx_profiles_v1.json").read_text())["profiles"]
    with tempfile.TemporaryDirectory(prefix="cyrinx-whitening-") as tmp:
        library = Path(tmp) / "bulk.dylib"
        subprocess.run(["clang", "-std=c11", "-O2", "-dynamiclib", "-Dkiss_fft_scalar=double",
                        "-I", core / "include", "-I", core / "kissfft", core / "cyrinx_bulk.c",
                        core / "cyrinx_fft.c", core / "kissfft/kiss_fft.c", core / "kissfft/kiss_fftr.c",
                        "-o", library, "-lm"], check=True)
        codec = clib.BulkCodec(library)
        rows = [row for p in profiles for row in compare(codec, p)]
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(rows, indent=2) + "\n")
    for pattern in dict.fromkeys(r["pattern"] for r in rows):
        rates = [sum(r["verified"] for r in rows if r["pattern"] == pattern and r["whitened"] == w)
                 for w in (False, True)]
        print(pattern, *rates)
    if any(r["wrong_crc_valid"] for r in rows):
        raise ValueError("a CRC-valid block differed from the input")


if __name__ == "__main__":
    main()
