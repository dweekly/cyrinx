#!/usr/bin/env python3
"""Measure the 2x2 path matrix: each Mac speaker to each phone microphone.

One stereo buffer plays a sweep on the left speaker, then the right, then both
together, then the left again, while the phone records both microphones. The
repeated left sweep gives the clock offset between the devices, which is
removed before anything else: without that the four responses cannot be
compared in phase. The "both together" sweep is a check: it must equal the sum
of the left and right responses, or the phase comparison is not trustworthy.

From the four responses this reports, per frequency bin and summarised:

  - what the receiver gets from the left speaker, the right, both playing the
    same signal (today's default if the output is stereo), and both with the
    phase aligned per frequency for that microphone;
  - what two independent streams could carry against one, as Shannon bounds at
    a stated signal-to-noise ratio. Bounds, not decoder results.

Usage: speaker_matrix.py --label NAME            (plays audio, records on the phone)
       speaker_matrix.py --replay FILE.npz       (analysis only)
"""

import argparse
import json
import os
import sys
import time

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "hw20k"))
sys.path.insert(0, _HERE)

SR = 48000
SWEEP_S = 1.0
GAP_S = 0.6
LEAD_S = 0.8
F_LO, F_HI = 300.0, 23000.0
IR_S = 0.25
"""Each response is truncated to this length: longer than the measured echo."""
SEGMENTS = ("left", "right", "both", "left_again")
NFFT = 16384


def sweep(amp):
    t = np.arange(int(SWEEP_S * SR)) / SR
    w = np.sin(2 * np.pi * (F_LO * t + 0.5 * (F_HI - F_LO) * t * t / SWEEP_S))
    ramp = int(0.01 * SR)
    env = np.ones(len(w))
    env[:ramp] = 0.5 - 0.5 * np.cos(np.pi * np.arange(ramp) / ramp)
    env[-ramp:] = env[:ramp][::-1]
    return amp * w * env


def segment_start(index):
    return int((LEAD_S + index * (SWEEP_S + GAP_S)) * SR)


def stereo_program(amp):
    total = segment_start(len(SEGMENTS)) + int(0.5 * SR)
    out = np.zeros((total, 2), dtype=np.float32)
    s = sweep(amp)
    for index, name in enumerate(SEGMENTS):
        start = segment_start(index)
        if name in ("left", "both", "left_again"):
            out[start:start + len(s), 0] = s
        if name in ("right", "both"):
            out[start:start + len(s), 1] = s
    return out


def matched(rx):
    ref = sweep(1.0)
    n = 1 << int(np.ceil(np.log2(len(rx) + len(ref))))
    spec = np.fft.fft(rx, n) * np.conj(np.fft.fft(ref, n))
    spec[n // 2 + 1:] = 0.0
    spec[1:n // 2] *= 2.0
    return np.fft.ifft(spec)[:len(rx)]


def first_arrival(capture):
    """Sample where the first sweep arrives, from both microphones together."""
    env = sum(np.abs(matched(capture[:, m])) for m in range(capture.shape[1]))
    # The first sweep need not be the loudest; take the earliest strong peak.
    strong = np.nonzero(env > 0.3 * env.max())[0][0]
    return strong + int(np.argmax(env[strong:strong + int(0.05 * SR)]))


def repeat_error(rx, first):
    r = responses(rx, first)
    return float(np.sum(np.abs(r["left_again"] - r["left"]) ** 2) / np.sum(np.abs(r["left"]) ** 2))


def clock_offset_ppm(capture, resample, search=(-60.0, 60.0)):
    """The correction that makes the repeated left sweep agree with the first.

    Searched rather than read off two correlation peaks: with a long echo and a
    weak direct sound the peaks are not the same path twice.
    """
    first = first_arrival(capture)
    mic = int(np.argmax([np.max(np.abs(matched(capture[:, m]))) for m in range(capture.shape[1])]))
    best, step = 0.0, 4.0
    lo, hi = search
    candidates = np.arange(lo, hi + step, step)
    for _ in range(4):
        errors = [repeat_error(np.asarray(resample(capture[:, mic], p), float), first) for p in candidates]
        best = float(candidates[int(np.argmin(errors))])
        step /= 4.0
        candidates = best + step * np.arange(-4, 5)
    return best, first


def responses(rx, first):
    """Complex frequency response of each segment on one microphone, on a common time base."""
    corr = matched(rx)
    out = {}
    pre = int(0.002 * SR)
    for index, name in enumerate(SEGMENTS):
        start = first + segment_start(index) - segment_start(0) - pre
        ir = np.real(corr[start:start + int(IR_S * SR)])
        out[name] = np.fft.rfft(ir, NFFT)
    return out


def analyse(capture, resample, snr_db=16.0):
    """capture: (frames, 2) phone recording. Returns a summary dict and per-bin arrays."""
    ppm, first = clock_offset_ppm(capture, resample)
    fixed = np.stack([np.asarray(resample(capture[:, m], ppm), float) for m in range(2)], axis=1)
    freqs = np.fft.rfftfreq(NFFT, 1 / SR)
    band = (freqs >= F_LO + 200) & (freqs <= F_HI - 200)
    H = np.zeros((2, 2, band.sum()), dtype=complex)  # [mic, speaker, bin]
    check = {}
    summary = {"clock_offset_ppm": ppm, "microphones": {}}
    for mic in range(2):
        r = responses(fixed[:, mic], first)
        left, right, both, again = (r[k][band] for k in SEGMENTS)
        H[mic, 0], H[mic, 1] = left, right
        check[mic] = (float(np.sum(np.abs(both - (left + right)) ** 2) / np.sum(np.abs(both) ** 2)),
                      float(np.sum(np.abs(again - left) ** 2) / np.sum(np.abs(left) ** 2)))
        p_left, p_right = np.abs(left) ** 2, np.abs(right) ** 2
        p_same = np.abs(left + right) ** 2
        p_aligned = (np.abs(left) + np.abs(right)) ** 2
        better = np.maximum(p_left, p_right)
        db = lambda x: float(10 * np.log10(np.mean(x) / np.mean(p_left)))  # noqa: E731
        summary["microphones"][mic] = {
            "both_vs_sum_error": check[mic][0], "left_repeat_error": check[mic][1],
            "mean_power_db_re_left": {"left": 0.0, "right": db(p_right), "same_signal": db(p_same),
                                      "phase_aligned": db(p_aligned)},
            "median_db_same_signal_vs_better_speaker": float(np.median(10 * np.log10(p_same / better))),
            "bins_where_same_signal_is_6db_below_better_speaker": float(np.mean(p_same < better / 4)),
            "bins_where_same_signal_is_10db_below_left": float(np.mean(p_same < p_left / 10)),
            "bins_10db_below_median": {
                "left": float(np.mean(p_left < np.median(p_left) / 10)),
                "right": float(np.mean(p_right < np.median(p_right) / 10)),
                "same_signal": float(np.mean(p_same < np.median(p_same) / 10)),
                "phase_aligned": float(np.mean(p_aligned < np.median(p_aligned) / 10))},
        }
    # Shannon bounds. Noise is set so the median left-speaker, first-microphone bin
    # has the stated SNR at full single-speaker amplitude.
    noise = np.median(np.abs(H[0, 0]) ** 2) / 10 ** (snr_db / 10)
    cap = {}
    for name, w in (("left only", np.array([1, 0])), ("right only", np.array([0, 1])),
                    ("both, same signal", np.array([1, 1]))):
        eff = np.einsum("msb,s->mb", H, w)
        for label, rows in (("mic 0", [0]), ("mic 1", [1]), ("both mics", [0, 1])):
            cap[f"{name} / {label}"] = float(np.mean(np.log2(1 + np.sum(np.abs(eff[rows]) ** 2, axis=0) / noise)))
    Hb = np.moveaxis(H, 2, 0)  # [bin, mic, speaker]
    sv = np.linalg.svd(Hb, compute_uv=False)
    # Every arrangement gets the same budget: each speaker at full amplitude.
    # One stream, phase aligned: the right speaker's phase is turned, per bin,
    # to make the two speakers add best across both microphones.
    left, right = H[:, 0, :], H[:, 1, :]
    aligned = (np.sum(np.abs(left) ** 2, axis=0) + np.sum(np.abs(right) ** 2, axis=0)
               + 2 * np.abs(np.sum(np.conj(left) * right, axis=0)))
    cap["both, phase aligned per bin for both mics / both mics"] = float(np.mean(np.log2(1 + aligned / noise)))
    # Two streams: each speaker carries its own, at full amplitude.
    cap["two streams, one per speaker / both mics"] = float(np.mean(np.sum(np.log2(1 + sv ** 2 / noise), axis=1)))
    summary["shannon_bits_per_bin_at_snr_db"] = {"snr_db": snr_db, **cap}
    summary["median_condition_number_db"] = float(np.median(20 * np.log10(sv[:, 0] / sv[:, 1])))
    return summary, {"freqs": freqs[band], "H": H}


def show(summary):
    print(f"clock offset {summary['clock_offset_ppm']:+.2f} ppm (the value that makes the repeated left sweep agree)")
    for mic, m in summary["microphones"].items():
        p = m["mean_power_db_re_left"]
        print(f"phone mic {mic}: check both=left+right error {m['both_vs_sum_error']:.3f}, "
              f"left repeat error {m['left_repeat_error']:.3f}")
        print(f"   mean power re left: right {p['right']:+.1f} dB, same signal {p['same_signal']:+.1f} dB, "
              f"phase aligned {p['phase_aligned']:+.1f} dB")
        d = m["bins_10db_below_median"]
        print(f"   bins more than 10 dB below that source's median: left {d['left']:.1%}, right {d['right']:.1%}, "
              f"same signal {d['same_signal']:.1%}, phase aligned {d['phase_aligned']:.1%}")
        print(f"   same signal against the better single speaker: median "
              f"{m['median_db_same_signal_vs_better_speaker']:+.1f} dB; "
              f"{m['bins_where_same_signal_is_6db_below_better_speaker']:.1%} of bins 6 dB or more below it")
    c = summary["shannon_bits_per_bin_at_snr_db"]
    print(f"Shannon bounds, bits per bin, noise set for {c['snr_db']} dB median (left, mic 0):")
    for k, v in c.items():
        if k != "snr_db":
            print(f"   {k:<56} {v:5.2f}")
    print(f"median ratio of the two singular values: {summary['median_condition_number_db']:.1f} dB")


def main():
    import clock_offset

    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default=None)
    ap.add_argument("--replay", default=None)
    ap.add_argument("--amp", type=float, default=0.25)
    ap.add_argument("--snr-db", type=float, default=16.0)
    ap.add_argument("--outdir", default=None)
    a = ap.parse_args()
    if a.replay:
        capture = np.load(a.replay)["phone"].astype(float)
    else:
        import sounddevice as sd

        import acquire
        import crosscal
        import harness as H
        import retain

        if not a.label:
            ap.error("--label is required for a live run")
        outdir = retain.session_dir(a.outdir)
        H.validated_android_serial()
        program = stereo_program(a.amp)
        name = f"sm_{crosscal.run_token()}.pcm"
        dur = len(program) / SR + 0.7 + 1.0 + 8.0
        H.android_record_start(dur, channels=2, source="unprocessed", out_name=name)
        time.sleep(0.7)
        sd.play(program, samplerate=SR, device=acquire._device_index(acquire.MAC_SPEAKERS, "output"),
                blocking=True)
        H.android_record_finish(dur, out_name=name)
        capture = crosscal._pull_capture(name, all_channels=True)
        base = os.path.join(outdir, f"speakermatrix-{a.label}-{time.strftime('%Y%m%dT%H%M%S')}")
        np.savez_compressed(base + ".npz", phone=capture.astype(np.float32))
        print(f"retained: {base}.npz")
    summary, _ = analyse(capture, clock_offset.resample, a.snr_db)
    show(summary)
    if not a.replay:
        with open(base + ".json", "w") as fh:
            json.dump(summary, fh, indent=1)


if __name__ == "__main__":
    main()
