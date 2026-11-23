#!/usr/bin/env python3
"""Why a scrambler air-screen run recovered nothing: three measurements on its recordings.

scrambler_air_screen.py decodes with the batch receiver. When even its dense control frames fail,
the run says nothing about the scrambler, and this script says what the recordings do show:

  1. Level. Each frame's level as transmitted and as received, relative to the first control
     frame, and the control's signal-to-noise ratio. An unscrambled short record is transmitted
     several decibels below a dense frame, because its tall peaks set the scale; a scrambled one
     is not.
  2. Echoes. How late, and how strong, the strongest arrival after the first chirp is, against
     the profile's cyclic prefix.
  3. A second receiver. The research receiver, with each of its two pilot fits and its search
     for a sampling-clock offset. It knows nothing of the scrambler, so its CRC verdict means
     nothing on a scrambled frame; blocks are scored by byte agreement with what was sent, after
     removing the scrambler mask from the payload bytes.

Usage:
  scrambler_air_diagnose.py DIRECTORY
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import numpy as np
from scipy.signal import fftconvolve

ROOT = Path(__file__).resolve().parents[2]
PREAMBLE = 4096 + 2048  # chirp and guard before the multicarrier section


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    folder = Path(sys.argv[1])
    with tempfile.TemporaryDirectory(prefix="cyrinx-air-diagnose-") as build:
        library = Path(build) / "libcyrinxclock.dylib"
        subprocess.run(["bash", str(ROOT / "scratch/garage/build_clock_dylib.sh"), str(library)], check=True,
                       capture_output=True)
        # clib reads its library path at import, so both variables are set first.
        os.environ["CYRINX_CLOCK_CODEC_PATH"] = os.environ["CYRINX_BULK_CODEC_PATH"] = str(library)
        sys.path[:0] = [str(ROOT / "scratch/garage"), str(ROOT / "scratch/hw20k")]
        import clib
        import clock_offset
        import modem
        import scrambler_air_screen as screen

        trial = json.loads((folder / "trial.json").read_text())
        candidate = trial["candidate_profile"]
        profiles, data, wave, facts = screen.transmission(screen.Codec(build), candidate, trial["schedule"])
        if facts["tx_float32_sha256"] != trial["tx_float32_sha256"]:
            raise SystemExit("this checkout no longer makes the transmission that was recorded")
        frame, step, blocks = facts["frame_samples"], facts["frame_samples"] + screen.GAP, facts["blocks"]
        frames = [tuple(row) for row in facts["schedule"]]
        profile = profiles[candidate]
        cfg = clib.make_cfg(bits_per_bin=[1, 2, 4, 6, 8][profile.modulation],
                            rate=["1/2", "2/3", "3/4", "5/6"][profile.code_rate], n_sym=profile.symbol_count,
                            f_lo=profile.low_frequency_hz, f_hi=profile.high_frequency_hz, nfft=profile.fft_size,
                            cp=profile.cyclic_prefix, sr=profile.sample_rate, amp=profile.amplitude,
                            clip_sigma=profile.clip_sigma, pilot_every=profile.pilot_every)
        # The scrambler mask at payload positions: 260 framed bytes per block, of which 256 are payload.
        mask = np.packbits(modem.prbs_bits(blocks * 260 * 8, seed=modem.SCRAMBLER_V1_SEED)).reshape(
            blocks, 260)[:, :256].reshape(-1)

        def decibels(value, reference):
            return round(float(20 * np.log10(value / reference)), 1)

        sent = [np.sqrt(np.mean(wave[slot * step + PREAMBLE:slot * step + frame].astype(float) ** 2))
                for slot in range(len(frames))]
        print(f"candidate profile {candidate}, cyclic prefix {profile.cyclic_prefix} samples")
        print("frames:", ", ".join(f"{slot}: {label}" for slot, (label, _, _) in enumerate(frames)))
        print("transmitted level, dB re frame 0:", [decibels(v, sent[0]) for v in sent])
        for serial in trial["phones"]:
            model = trial["phones"][serial].get("model", serial)
            raw = np.fromfile(folder / f"{serial}.pcm", "<i2").reshape(-1, 2).astype(float)
            matched = np.abs(fftconvolve(raw[:, 0], wave[:4096].astype(float)[::-1], mode="valid"))
            room = len(raw) - len(wave)
            if room <= 0:
                print(f"{model}: the capture is shorter than the transmission; skipped")
                continue
            start = int(np.argmax(sum(matched[slot * step:slot * step + room] for slot in range(len(frames)))))
            noise = raw[-3 * screen.SR:, 0].std()
            received = [np.sqrt(max(raw[start + slot * step + PREAMBLE:start + slot * step + frame, 0].var()
                                    - noise ** 2, 1e-9)) for slot in range(len(frames))]
            print(f"{model}: received level, dB re frame 0: {[decibels(v, received[0]) for v in received]}; "
                  f"frame 0 is {decibels(received[0], noise)} dB above the noise after the transmission")
            near = matched[max(0, start - 2000):start + 2000]
            first = max(0, start - 2000) + int(np.argmax(near))
            later = matched[first + 100:first + 4000]
            print(f"{model}: strongest arrival more than 100 samples after the first chirp's peak: "
                  f"{int(np.argmax(later)) + 100} samples later, {decibels(later.max(), matched[first])} dB")
            for fit, label in ((clock_offset.FIT_SHIPPED, "shipped pilot fit"),
                               (clock_offset.FIT_COHERENT, "coherent pilot fit")):
                clock_offset.set_pilot_fit(fit)
                cells = []
                for slot, (_, identifier, name) in enumerate(frames):
                    at = start + slot * step
                    window = raw[max(0, at - screen.LEAD):at + frame + screen.TAIL, 0] / 32768
                    decoded = clock_offset.decode(cfg, window)
                    if not decoded:
                        cells.append("no result")
                        continue
                    got = np.frombuffer(decoded["payload"], np.uint8)
                    if identifier == candidate:
                        got = got ^ mask
                    expected = np.frombuffer(data[name] + bytes(blocks * 256 - len(data[name])), np.uint8)
                    right = int((got.reshape(blocks, 256) == expected.reshape(blocks, 256)).all(axis=1).sum())
                    cells.append(f"{right}/{blocks} (EVM {decoded['evm']:.2f})")
                print(f"{model}: research receiver, {label}: " + ", ".join(cells))
    return 0


if __name__ == "__main__":
    sys.exit(main())
