#!/usr/bin/env python3
"""Two-way acoustic ranging between the Mac and the phone, with no shared clock.

Each device plays chirps while both devices record. In its own recording a
device hears its own chirp almost at once and the other device's chirp one
flight time later. Subtracting the gap between the two chirps as heard on the
phone from the same gap as heard on the Mac cancels both devices' unknown start
times and leaves twice the flight time (Peng et al., "BeepBeep", SenSys 2007):

    distance = c/2 * (gap_on_mac - gap_on_phone) + (d_mac_self + d_phone_self)/2

where each d_self is the distance from a device's speaker to its own
microphone. Those two are not measured here; they are stated assumptions and
they bound the accuracy. The Mac plays rising chirps and the phone falling
ones, so the two are told apart by shape and may overlap.

Usage: rangefind.py --label NAME [--runs 3] [--temp-c 20]
"""

import argparse
import json
import os
import sys
import threading
import time

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "hw20k"))
sys.path.insert(0, _HERE)

SR = 48000
CHIRP_S = 0.040
F_LO, F_HI = 3000.0, 16000.0
"""Inside both speakers' usable bands (geometries.py)."""
PERIOD_S = 0.5
MAC_PERIOD_S = 0.37
"""The two devices repeat at different intervals, so if one pair of chirps
happens to collide the next does not."""
MAC_CHIRPS = 40
PHONE_CHIRPS = 8
PHONE_LEAD_S = 1.0
MIN_SEPARATION_S = 0.060
"""A pair is skipped when the two chirps arrive closer than this in either
recording: their correlation peaks then sit in each other's echo tail."""
FIRST_ARRIVAL_FRACTION = 0.5
FIRST_ARRIVAL_LOOKBACK_S = 0.030
"""The direct sound can be weaker than a reflection that follows it: on the
Pixel's first microphone the Mac's direct sound was weaker than a reflection
17 ms later.
The arrival is the first correlation peak, within this window before the
strongest one, that reaches this fraction of it."""

MAX_RANGE_M = 15.0

D_MAC_SELF_M = 0.05
D_PHONE_SELF_M = 0.02
SELF_UNCERTAINTY_M = 0.04
"""Assumed speaker-to-own-microphone distances (MacBook Pro: left speaker grille
to the microphone array beside it; Pixel 7a: bottom speaker to bottom
microphone) and a combined uncertainty. Assumptions, not measurements."""


def chirp(rising):
    t = np.arange(int(CHIRP_S * SR)) / SR
    f0, f1 = (F_LO, F_HI) if rising else (F_HI, F_LO)
    w = np.sin(2 * np.pi * (f0 * t + 0.5 * (f1 - f0) * t * t / CHIRP_S))
    ramp = int(0.004 * SR)
    env = np.ones(len(w))
    env[:ramp] = 0.5 - 0.5 * np.cos(np.pi * np.arange(ramp) / ramp)
    env[-ramp:] = env[:ramp][::-1]
    return w * env


def train(rising, count, lead_s, amp):
    period = MAC_PERIOD_S if rising else PERIOD_S
    out = np.zeros(int((lead_s + count * period + 0.5) * SR))
    c = chirp(rising) * amp
    for i in range(count):
        start = int((lead_s + i * period) * SR)
        out[start:start + len(c)] = c
    return out


def speed_of_sound(temp_c):
    return 331.3 * np.sqrt(1.0 + temp_c / 273.15)


def envelope(rx, rising):
    """Matched-filter envelope of one chirp shape (magnitude of the analytic correlation)."""
    ref = chirp(rising)
    n = 1 << int(np.ceil(np.log2(len(rx) + len(ref))))
    spec = np.fft.fft(rx, n) * np.conj(np.fft.fft(ref, n))
    spec[n // 2 + 1:] = 0.0
    spec[1:n // 2] *= 2.0
    return np.abs(np.fft.ifft(spec))[:len(rx)]


def arrivals(rx, rising, max_count, mask_around=None):
    """First-arrival sample (fractional) of each chirp of one shape in rx.

    A device's own chirp is far louder at its own microphone than the other
    device's, and a loud chirp of the wrong shape still leaves a low smear in
    this correlation. ``mask_around`` (arrivals of the loud shape) blanks those
    stretches so the smear is not mistaken for a quiet chirp.
    """
    mf = envelope(rx, rising)
    work = mf.copy()
    if mask_around is not None:
        blank = int((CHIRP_S + 0.015) * SR)
        for t in mask_around:
            work[max(0, int(t) - blank):int(t) + blank] = 0.0
    floor = 8.0 * np.median(mf)
    period = MAC_PERIOD_S if rising else PERIOD_S
    guard = int(0.4 * period * SR)
    found = []
    for _ in range(max_count):
        peak = int(np.argmax(work))
        if work[peak] < floor or peak < 2 or peak > len(mf) - 2:
            break
        lo = max(1, peak - int(FIRST_ARRIVAL_LOOKBACK_S * SR))
        first = peak
        for i in range(lo, peak):
            if mf[i] >= FIRST_ARRIVAL_FRACTION * mf[peak] and mf[i] >= mf[i - 1] and mf[i] >= mf[i + 1]:
                first = i
                break
        a, b, c = mf[first - 1], mf[first], mf[first + 1]
        denom = a - 2 * b + c
        found.append(first + (0.5 * (a - c) / denom if denom < 0 else 0.0))
        work[max(0, peak - guard):peak + guard] = 0.0
    return np.sort(np.array(found))


def gaps(reference, events):
    """For each event, samples since the last reference arrival before it.

    Keyed by that reference arrival's index and the event's, so the same pair
    of chirps can be matched across the two recordings. Pairs closer than
    MIN_SEPARATION_S to a reference arrival on either side are skipped.
    """
    out = {}
    for j, t in enumerate(events):
        before = np.nonzero(reference < t)[0]
        if len(before) == 0:
            continue
        i = int(before[-1])
        to_next = reference[i + 1] - t if i + 1 < len(reference) else np.inf
        if min(t - reference[i], to_next) < MIN_SEPARATION_S * SR:
            continue
        out[j] = (i, t - reference[i])
    return out


def estimate(mac_rx, phone_rx, temp_c):
    """Distance from one pair of recordings; None when no chirp pair is usable."""
    mac_own = arrivals(mac_rx, True, MAC_CHIRPS)
    mac_other = arrivals(mac_rx, False, PHONE_CHIRPS, mask_around=mac_own)
    phone_own = arrivals(phone_rx, False, PHONE_CHIRPS)
    phone_other = arrivals(phone_rx, True, MAC_CHIRPS, mask_around=phone_own)
    detail = {"mac_heard_own": len(mac_own), "mac_heard_phone": len(mac_other),
              "phone_heard_own": len(phone_own), "phone_heard_mac": len(phone_other), "pairs": 0}
    if len(mac_own) < 2 or len(phone_own) < 2 or len(mac_other) == 0 or len(phone_other) == 0:
        return None, detail
    # The phone's chirps are heard in full on both devices, in the same order,
    # so they give the offset between the two recordings' time axes to within a
    # flight time. That offset then says which Mac chirp heard on the phone is
    # which Mac chirp on the Mac, even though the phone only hears some of them.
    consistent = 0.001 * SR
    c = speed_of_sound(temp_c)

    def distances_for(matched):
        """Distances from one way of matching phone chirps across the recordings."""
        offset = float(np.median([mac_other[k] - phone_own[j] for j, k in matched]))
        # A Mac chirp heard on the phone at t was played near t + offset on the
        # Mac's time axis, early by two flight times. Match on that, then keep
        # only matches that agree with each other.
        candidates = {}
        for t in phone_other:
            i = int(np.argmin(np.abs(mac_own - (t + offset))))
            candidates[i] = (t, mac_own[i] - (t + offset))
        typical = float(np.median([r for _, r in candidates.values()]))
        heard = {i: t for i, (t, r) in candidates.items() if abs(r - typical) < consistent}
        out = []
        for j, k in matched:
            for i, t in heard.items():
                on_mac = mac_other[k] - mac_own[i]
                on_phone = phone_own[j] - t
                # one Mac chirp just before this phone chirp, clear of it in both recordings
                if not (MIN_SEPARATION_S * SR < on_mac < MAC_PERIOD_S * SR
                        and MIN_SEPARATION_S * SR < on_phone < MAC_PERIOD_S * SR):
                    continue
                d = c / 2 * (on_mac - on_phone) / SR + (D_MAC_SELF_M + D_PHONE_SELF_M) / 2
                if -0.5 < d < MAX_RANGE_M:
                    out.append(d)
        return out

    # A phone chirp that collided with a Mac chirp may be missed on the Mac, or
    # detected late (its true peak is blanked). So phone chirps are matched
    # across the recordings by a common time offset. When chirps are missing,
    # an offset wrong by one whole period can match as many; that one puts
    # every distance tens of metres out, so the matching that yields the most
    # plausible distances is the one used.
    distances, seen = [], set()
    for heard_at in mac_other:
        for played_at in phone_own:
            trial = heard_at - played_at
            matched = tuple((j, k) for j, t in enumerate(phone_own)
                            for k in [int(np.argmin(np.abs(mac_other - (t + trial))))]
                            if abs(mac_other[k] - (t + trial)) < consistent)
            if len(matched) < 2 or matched in seen:
                continue
            seen.add(matched)
            found = distances_for(matched)
            if len(found) > len(distances):
                distances = found
    detail["pairs"] = len(distances)
    if not distances:
        return None, detail
    detail["per_pair_m"] = [round(float(d), 4) for d in distances]
    detail["spread_m"] = round(float(np.max(distances) - np.min(distances)), 4)
    return float(np.median(distances)), detail


def main():

    import acquire
    import harness as H
    import retain

    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default=None)
    ap.add_argument("--replay", default=None, help="re-analyse a retained rangefind-*.npz; no audio")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--temp-c", type=float, default=20.0, help="air temperature; sets the speed of sound")
    ap.add_argument("--amp", type=float, default=0.25)
    ap.add_argument("--outdir", default=None)
    a = ap.parse_args()
    if a.replay:
        captures = np.load(a.replay)
        for run in range(sum(1 for k in captures.files if k.startswith("mac#"))):
            phone = captures[f"phone#{run}"]
            for mic in range(phone.shape[1]):
                d, detail = estimate(np.asarray(captures[f"mac#{run}"], float),
                                     phone[:, mic].astype(np.float64), a.temp_c)
                shown = "no usable chirp pair" if d is None else f"{d * 100:.1f} cm ({d / 0.0254:.1f} in)"
                print(f"run {run} phone mic {mic}: {shown}  {detail}")
        return
    if not a.label:
        ap.error("--label is required for a live run")
    outdir = retain.session_dir(a.outdir)
    retain.stage_phone_pulls(H, outdir)
    serial = H.validated_android_serial()
    base = os.path.join(outdir, f"rangefind-{a.label}-{time.strftime('%Y%m%dT%H%M%S')}")
    mac_wave = train(True, MAC_CHIRPS, 0.5, a.amp).astype(np.float32)
    phone_wave = train(False, PHONE_CHIRPS, PHONE_LEAD_S, a.amp).astype(np.float32)
    captures, results = {}, []
    for run in range(a.runs):
        box = {}
        # The built-in devices are named explicitly: the system default may be a display.
        thread = threading.Thread(target=lambda: box.update(mac=acquire.play_and_record(
            mac_wave, SR, acquire.MAC_SPEAKERS, acquire.MAC_MICROPHONE, tail_s=1.0)[:, 0]))
        thread.start()
        time.sleep(1.5)
        name = f"rf_{os.getpid()}_{run}.pcm"
        # playrec_pcm only opens a stereo output track
        _, _, path, _ = H.android_playrec(np.stack([phone_wave, phone_wave], axis=1), name,
                                          input_channels=2)
        thread.join()
        phone = H.load_pcm16(path, channels=2)
        mac = np.asarray(box["mac"], dtype=np.float64)
        captures[f"mac#{run}"] = mac.astype(np.float32)
        captures[f"phone#{run}"] = phone.astype(np.float32)
        row = {"run": run}
        for mic in range(phone.shape[1]):
            d, detail = estimate(mac, phone[:, mic].astype(np.float64), a.temp_c)
            row[f"phone_mic{mic}"] = {"distance_m": d, **detail}
            shown = "no usable chirp pair" if d is None else f"{d * 100:.1f} cm ({d / 0.0254:.1f} in)"
            print(f"run {run} phone mic {mic}: {shown}  {detail}", flush=True)
        results.append(row)
        np.savez_compressed(base + ".npz", **captures)
    with open(base + ".json", "w") as fh:
        json.dump({"label": a.label, "serial": serial, "temp_c": a.temp_c,
                   "speed_of_sound_m_s": speed_of_sound(a.temp_c),
                   "assumed_self_distances_m": {"mac": D_MAC_SELF_M, "phone": D_PHONE_SELF_M,
                                                "uncertainty": SELF_UNCERTAINTY_M},
                   "runs": results}, fh, indent=1)
    print(f"retained: {base}.json / .npz")


if __name__ == "__main__":
    main()
