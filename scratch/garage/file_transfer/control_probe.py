"""Measure repeated acoustic ACKs with deliberately complementary frequency losses."""
import argparse
import json
import os
from pathlib import Path

import numpy as np
from scipy.signal import fftconvolve

from replay import CHUNKS, ack_bitmap, aligned_ack, build, clib, clock_offset
import tempfile

RATE = 48000
TOKEN = b"ctrl2026"
STALE = b"wrongtok"


def waveform(cfg, geometry, token, indices):
    body = bytearray(np.random.default_rng(112223).bytes(geometry.payload_bytes))
    bitmap = bytearray((CHUNKS + 7) // 8)
    for index in indices:
        bitmap[index // 8] |= 1 << (index % 8)
    header = b"AK01" + token + bitmap
    for slot in range(geometry.n_blocks):
        start = slot * 256
        body[start:start + len(header)] = header
    return clib.encode(cfg, bytes(body)).astype(float)


def impair(wave, side):
    data = wave[6144:]  # Leave the acquisition chirp and its guard intact.
    hz = np.fft.rfftfreq(len(data), 1 / RATE)
    gain = (1 + side * np.tanh(8 * np.sin(2 * np.pi * hz / 1600))) / 2
    return np.concatenate((wave[:6144], np.fft.irfft(np.fft.rfft(data) * gain, len(data))))


def measure(capture, cfg, geometry, chirp, step):
    # Schedule isolates the eight slots; each pair independently reacquires its chirps.
    room = len(capture) - 8 * step + 1
    if room <= 0:
        raise ValueError("truncated control sequence")
    corr = np.abs(fftconvolve(capture, chirp[::-1], mode="valid"))
    first = int(np.argmax(sum(corr[j * step:j * step + room] for j in range(6))))
    windows = [capture[max(0, first + j * step - 5500):first + j * step + geometry.frame_samples + 10000]
               for j in range(8)]
    singles = [ack_bitmap(clock_offset.decode(cfg, w), TOKEN) for w in windows]
    checks = []
    for name, a, b, count in [("unchanged", 1, 2, 254), ("changed", 1, 3, 276),
                              ("stale-session", 4, 5, 0), ("absent", 6, 7, 0)]:
        pair = [aligned_ack(windows[j], chirp, geometry.frame_samples) for j in (a, b)]
        result = None if any(w is None for w in pair) else clock_offset.decode2(cfg, *pair)
        recovered = ack_bitmap(result, TOKEN)
        stale = ack_bitmap(result, STALE)
        checks.append(dict(condition=name, single=[None if singles[j] is None else sorted(singles[j])
                                                 for j in (a, b)],
                           combined=None if recovered is None else sorted(recovered),
                           stale_chunks=None if stale is None else len(stale),
                           unexpected=sorted((recovered or set()) - set(range(count)))))
    return dict(first_sample=first, positive=sorted(singles[0] or []), checks=checks)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--digital", action="store_true")
    mode.add_argument("--record", type=Path)
    mode.add_argument("--replay", type=Path)
    parser.add_argument("--serial", action="append", default=[])
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="cyrinx-control-") as tmp:
        build(Path(tmp))
        cfg = clib.make_cfg(f_lo=300, f_hi=11000, bits_per_bin=2, rate="1/2", nfft=16384, cp=8192, n_sym=3)
        g = clib.geometry(cfg)
        base = waveform(cfg, g, TOKEN, range(254))
        changed = waveform(cfg, g, TOKEN, range(CHUNKS))
        stale = waveform(cfg, g, STALE, range(254))
        frames = [base, impair(base, 1), impair(base, -1), impair(changed, -1),
                  impair(stale, 1), impair(stale, -1), np.zeros_like(base), np.zeros_like(base)]
        step = g.frame_samples + 16000
        wave = np.concatenate([np.pad(w, (0, 16000)) for w in frames])

        def score(x):
            return measure(x, cfg, g, base[:4096], step)

        if args.digital:
            result = score(np.pad(wave, (8000, 16000)))
            same = result["checks"][0]
            if (result["positive"] != list(range(254)) or same["single"] != [None, None]
                    or same["combined"] != list(range(254)) or any(c["unexpected"] for c in result["checks"])
                    or any(c["combined"] is not None for c in result["checks"][2:])):
                raise ValueError("digital control mismatch")
            print("Digital positive, complementary loss, changing bitmap, stale session and silence checks passed.")
        elif args.record:
            import harness
            if not args.serial:
                parser.error("--record needs at least one --serial")
            args.record.mkdir(parents=True, exist_ok=False)
            for serial in args.serial:
                os.environ["ANDROID_SERIAL"] = serial
                # Use the same built-in Mac recording and HIL speaker routes as the file bench.
                capture = harness.android_to_mac(wave, post_s=1.2)
                np.save(args.record / f"{serial}.npy", capture)
                result = score(capture)
                (args.record / f"{serial}.json").write_text(json.dumps(result, indent=2) + "\n")
                print(serial, [len(row["combined"] or []) for row in result["checks"]], flush=True)
        else:
            paths = sorted(args.replay.glob("*.npy"))
            if not paths:
                raise ValueError("no live control recordings")
            for path in paths:
                if score(np.load(path, allow_pickle=False)) != json.loads(path.with_suffix(".json").read_text()):
                    raise ValueError(f"control replay mismatch for {path}")
                print(path.stem, "reproduced")


if __name__ == "__main__":
    main()
