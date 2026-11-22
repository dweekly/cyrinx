"""Why do zero-heavy payloads fail to decode in a clean loopback? (NEGATIVE_FINDINGS entry 26)

Method. Everything is a loopback through the shipped C codec with no audio hardware. Two builds
of the same source are used: the shipped one, and one whose only change is the upper limit on
clip_sigma in the config check, so the transmitter's amplitude clip can be switched off. For
each payload pattern the script records

  - how concentrated the data constellation is (share of data bins holding the commonest point),
  - the peak-to-RMS ratio of the multicarrier section before clipping,
  - the share of samples the shipped clip flattens, and
  - blocks recovered with the shipped clip and with the clip off.

It then sweeps the clip threshold for the all-zero payload. If clipping is the cause, recovery
returns once the clip is off and the sweep shows a threshold. If recovery stays at zero with the
clip off, the cause is elsewhere and the clip hypothesis is rejected.
"""
import argparse
import ctypes
import json
from pathlib import Path
import subprocess
import sys
import tempfile

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scratch/hw20k"))
import clib  # noqa: E402

CORE = ROOT / "Sources/CCyrinx"
PREAMBLE = 4096 + 2048  # chirp and guard precede the multicarrier section
CLIP_OFF = 1e9


def build(folder, relaxed):
    source = CORE / "cyrinx_bulk.c"
    if relaxed:
        text = source.read_text()
        limit = "cfg->clip_sigma > 10.0 ||"
        if text.count(limit) != 1:
            raise RuntimeError("could not find the clip_sigma limit to relax")
        source = folder / "cyrinx_bulk_relaxed.c"
        source.write_text(text.replace(limit, "cfg->clip_sigma > 1e12 ||"))
    library = folder / ("relaxed.dylib" if relaxed else "shipped.dylib")
    subprocess.run(["clang", "-std=c11", "-O2", "-dynamiclib", "-Dkiss_fft_scalar=double",
                    "-I", CORE / "include", "-I", CORE / "kissfft", "-I", CORE, source,
                    CORE / "cyrinx_fft.c", CORE / "kissfft/kiss_fft.c", CORE / "kissfft/kiss_fftr.c",
                    "-o", library, "-lm"], check=True)
    return clib.BulkCodec(library)


def config(profile, clip_sigma=None):
    cfg = clib.make_cfg(bits_per_bin=[1, 2, 4, 6, 8][profile["modulation"]],
                        rate=["1/2", "2/3", "3/4", "5/6"][profile["codeRate"]],
                        n_sym=profile["symbolCount"], f_lo=profile["lowFrequencyHz"],
                        f_hi=profile["highFrequencyHz"], nfft=profile["fftSize"], cp=profile["cyclicPrefix"],
                        sr=profile["sampleRate"], amp=profile["amplitude"],
                        clip_sigma=profile["clipSigma"] if clip_sigma is None else clip_sigma,
                        pilot_every=profile["pilotEvery"])
    cfg.chirp_f0, cfg.chirp_f1 = profile["chirpF0"], profile["chirpF1"]
    return cfg


def constellation_share(codec, cfg, payload, pilot_every):
    """Share of data bins that carry the single most common constellation point."""
    g = codec.geometry(cfg)
    wave = (ctypes.c_float * g.frame_samples)()
    freq = (ctypes.c_double * (cfg.n_sym * g.n_used * 2))()
    data = (ctypes.c_uint8 * len(payload)).from_buffer_copy(payload)
    modulate = codec._lib.cyrinx_bulk_modulate
    modulate.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
                         ctypes.c_size_t, ctypes.c_void_p]
    modulate.restype = ctypes.c_long
    if modulate(ctypes.byref(cfg), data, len(payload), wave, g.frame_samples, freq) < 0:
        raise RuntimeError("modulate failed")
    points = np.frombuffer(freq, dtype=np.float64).reshape(cfg.n_sym, g.n_used, 2)
    points = points[:, np.arange(g.n_used) % pilot_every != 0].reshape(-1, 2)
    _, counts = np.unique(np.round(points, 9), axis=0, return_counts=True)
    return float(counts.max() / counts.sum())


def recovered(codec, cfg, payload, wave=None):
    wave = codec.encode(cfg, payload) if wave is None else wave
    decoded = codec.decode(cfg, wave)
    if decoded is None:
        return dict(valid=0, verified=0, evm=None)
    mask = np.asarray(decoded["block_valid"], dtype=bool)
    got = np.frombuffer(decoded["payload"], dtype=np.uint8).reshape(-1, 256)
    sent = np.frombuffer(payload, dtype=np.uint8).reshape(-1, 256)
    return dict(valid=int(mask.sum()), verified=int((mask & np.all(got == sent, axis=1)).sum()),
                evm=round(float(decoded["evm"]), 4))


def patterns(length):
    dense = np.frombuffer(np.random.default_rng(112223).bytes(length), dtype=np.uint8)
    short = np.zeros(length, dtype=np.uint8)
    short[:128] = dense[:128]
    sparse = np.zeros(length, dtype=np.uint8).reshape(-1, 256)  # Eight data bytes in every block.
    sparse[:, :8] = dense.reshape(-1, 256)[:, :8]
    return {"zero": np.zeros(length, dtype=np.uint8), "0xff": np.full(length, 0xFF, dtype=np.uint8),
            "short-128": short, "sparse-8": sparse.ravel(), "0x71": np.full(length, 0x71, dtype=np.uint8),
            "random": dense}


def measure(shipped, relaxed, profile):
    rows = []
    cfg, off = config(profile), config(profile, CLIP_OFF)
    blocks = shipped.geometry(cfg).n_blocks
    for name, data in patterns(shipped.geometry(cfg).payload_bytes).items():
        payload = data.tobytes()
        unclipped = relaxed.encode(off, payload)
        clipped = shipped.encode(cfg, payload)
        body = unclipped[PREAMBLE:].astype(float)
        flat = np.abs(clipped[PREAMBLE:])
        rows.append(dict(
            profile=profile["id"], pattern=name, blocks=blocks,
            commonest_point_share=round(constellation_share(shipped, cfg, payload, profile["pilotEvery"]), 4),
            peak_to_rms=round(float(np.abs(body).max() / body.std()), 2),
            clipped_sample_share=round(float(np.mean(flat >= flat.max() * (1 - 1e-6))), 5),
            clip_on=recovered(shipped, cfg, payload),
            clip_off=recovered(relaxed, off, payload, unclipped),
            # The same receiver decodes both, so a difference between the two is the transmitter's clip.
            clip_off_decoded_by_shipped_receiver=recovered(shipped, cfg, payload, unclipped)))
    return rows


def sweep(relaxed, profile, thresholds):
    payload = bytes(relaxed.geometry(config(profile)).payload_bytes)
    return [dict(profile=profile["id"], clip_sigma=sigma, **recovered(relaxed, config(profile, sigma), payload))
            for sigma in thresholds]


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    profiles = json.loads((ROOT / "Tests/Fixtures/profiles/cyrinx_profiles_v1.json").read_text())["profiles"]
    with tempfile.TemporaryDirectory(prefix="cyrinx-zero-payload-") as tmp:
        shipped, relaxed = build(Path(tmp), False), build(Path(tmp), True)
        rows = [row for profile in profiles for row in measure(shipped, relaxed, profile)]
        thresholds = [2.0, 3.3, 5.0, 7.0, 10.0, 15.0, 20.0, 30.0, 50.0, CLIP_OFF]
        sweeps = [row for profile in profiles for row in sweep(relaxed, profile, thresholds)]
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(dict(patterns=rows, zero_payload_clip_sweep=sweeps), indent=2) + "\n")
    print("profile pattern    common  peak/rms  clipped  clip-on  clip-off  of")
    for r in rows:
        print(f"{r['profile']:>7} {r['pattern']:<10} {r['commonest_point_share']:>6.3f} {r['peak_to_rms']:>9.1f} "
              f"{r['clipped_sample_share']:>8.4f} {r['clip_on']['verified']:>8} {r['clip_off']['verified']:>9} "
              f"{r['blocks']:>4}")
    print("zero payload, blocks recovered by clip threshold (sigma):")
    for profile in profiles:
        line = [f"{s['clip_sigma']:g}:{s['verified']}" for s in sweeps if s["profile"] == profile["id"]]
        print(f"  profile {profile['id']}: " + "  ".join(line))


if __name__ == "__main__":
    main()
