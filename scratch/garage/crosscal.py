#!/usr/bin/env python3
"""Mutual channel characterization across two endpoints (ADR 0006 phase 3).

Phase 3 of session initiation: each endpoint transmits a swept sine in turn while
the other records, producing one delay-spread estimate per *directed* link. The
two directions are separate channels -- different speaker, different microphone,
different band -- so they are measured and reported separately.

The Mac side selects its speaker and microphone by name rather than using the
system default, so a session runs at its own level and does not depend on
whatever the machine is routed to. The Android side uses the existing HIL app
through `harness`, which must be foregrounded before every capture or the mic
returns zeros.

Usage:
    ANDROID_SERIAL=<serial> .venv/bin/python3 scratch/garage/crosscal.py \
        --label desk-1ft --amp 0.5 --media-volume 12
"""

import argparse
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hw20k")
)

import acquire  # noqa: E402
import delay_spread as ds  # noqa: E402
import freqresp as F  # noqa: E402
import geometries as geo  # noqa: E402
import harness as H  # noqa: E402

ROOM_TONE_S = 8.0
"""Longer than the 6.0 s inverse filter so NoiseReference has a fully overlapped
interior. This is an acquisition requirement, not a tuning knob."""

TAIL_S = 1.5
"""Recording retained after the sweep ends, which bounds the observation horizon.
The MacBook path measured 154 ms of playback-plus-capture latency and a
cross-device path adds the phone's own buffering, so the tail must cover the
horizon plus that latency."""

HORIZON_MS = 500.0
"""Matches the garage default; past convergence for the paths measured so far."""


def run_token():
    """A per-run token for Android capture filenames.

    `harness.android_record_finish` waits on a logcat line naming the request id,
    and `logcat -d` returns the whole buffer -- so a request id reused from an
    earlier run matches that run's completion line immediately, and the file is
    pulled while the new recording is still being written. A unique id per run
    removes the ambiguity without clearing a buffer other sessions may be using.
    """
    return time.strftime("%H%M%S")


def _pull_capture(out_name, channels=2):
    """Pull an Android capture and refuse an empty or silent one."""
    path = os.path.join(H.DATA, out_name)
    captured = H.load_pcm16(path, channels=channels)
    if len(captured) == 0:
        raise RuntimeError(f"{out_name}: Android capture is empty")
    mono = captured[:, 0].astype(np.float64)
    if float(np.max(np.abs(mono))) == 0.0:
        raise RuntimeError(f"{out_name}: Android capture is all zeros (app not foreground?)")
    return mono


def media_volume(value=None):
    """Read, and optionally set, the phone's media stream level.

    `harness.android_prepare(media_volume=...)` shells out to `media volume`,
    which this Pixel 7a's shell does not provide ("media: inaccessible or not
    found"). `cmd media_session` is the interface that answers here, so the level
    is handled locally rather than by changing shared harness code for one
    device.

    The plan fixes one comfortable level per device at setup and records it, so
    the default path reads rather than writes.
    """
    if value is not None:
        H._checked_adb(
            f"shell cmd media_session volume --stream 3 --set {int(value)}",
            "media-volume set",
        )
    out = H._checked_adb(
        "shell cmd media_session volume --stream 3 --get", "media-volume get"
    ).stdout
    for line in out.splitlines():
        if "volume is" in line:
            parts = line.split()
            return int(parts[parts.index("is") + 1]), line.strip()
    raise RuntimeError(f"could not parse media volume from: {out!r}")


def _report(name, acq, noise):
    out = ds.measure(acq, noise)
    print(f"\n{name}")
    print(f"  capture peak {np.max(np.abs(acq.ir)):.3f}"
          + ("   WARNING: clipping" if np.max(np.abs(acq.ir)) >= 0.99 else ""))
    print(f"  horizon {out.horizon_ms:.1f} ms ({out.support}); "
          f"noise power {out.noise_power:.3e}; truncated={out.truncated}")
    for db in ds.THRESHOLDS_DB:
        r = out.at(db)
        if r.usable:
            lo, hi = r.ms_interval
            verdict = ds.exceeds_guard_budget(r, geo.PRACTICAL_GUARD_BUDGET_MS)
            print(f"  {db:6.0f} dB  {r.ms:8.3f} ms  interval [{lo:.3f}, {hi:.3f}]"
                  f"  beyond {geo.PRACTICAL_GUARD_BUDGET_MS:.0f} ms budget: {verdict}")
        else:
            print(f"  {db:6.0f} dB  {r.status:<22} {r.detail}")
    return out


def mac_to_phone(x, inv, amp, token):
    """Mac speaker -> phone microphone. Room tone recorded on the phone."""
    tone_name = f"ct_tone_{token}.pcm"
    H.android_record(ROOM_TONE_S, channels=2, out_name=tone_name)
    tone = _pull_capture(tone_name)

    sweep_name = f"ct_m2p_{token}.pcm"
    dur = len(x) / F.SR + 0.7 + TAIL_S + 8.0
    H.android_record_start(dur, channels=2, source="unprocessed", out_name=sweep_name)
    time.sleep(0.7)
    acquire.play_and_record(x, F.SR, acquire.MAC_SPEAKERS, acquire.MAC_MICROPHONE, tail_s=TAIL_S)
    H.android_record_finish(dur, out_name=sweep_name)
    captured = _pull_capture(sweep_name)

    acq = acquire.deconvolve(captured, inv, F.SR, horizon_ms=HORIZON_MS)
    return acq, ds.NoiseReference.from_pcm(tone, inv, F.SR), captured, tone


def phone_to_mac(x, inv):
    """Phone speaker -> Mac microphone. Room tone recorded on the Mac."""
    tone = acquire.record(ROOM_TONE_S, F.SR, acquire.MAC_MICROPHONE)[:, 0]

    H.android_prepare(media_volume=None)
    captured = H.android_to_mac(x.astype(np.float32), channels=1, post_s=TAIL_S)
    captured = np.asarray(captured, dtype=np.float64).reshape(-1)

    acq = acquire.deconvolve(captured, inv, F.SR, horizon_ms=HORIZON_MS)
    return acq, ds.NoiseReference.from_pcm(tone, inv, F.SR), captured, tone


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="crosscal")
    ap.add_argument("--amp", type=float, default=F.AMP)
    ap.add_argument("--media-volume", type=int, default=None,
                    help="set the phone media level; omit to use and record its current level")
    ap.add_argument("--outdir", default="artifacts/garage")
    a = ap.parse_args()

    serial = H.validated_android_serial()
    provenance = H.collect_android_target_provenance()
    level, readback = media_volume(a.media_volume)
    print(f"phone: {provenance.get('model', '?')} / {serial}")
    print(f"media volume: {readback}")

    x, inv = F.make_ess(amp=a.amp)
    results, blobs = {}, {}

    token = run_token()
    acq, noise, cap, tone = mac_to_phone(x, inv, a.amp, token)
    results["mac_to_phone"] = _report("Mac speaker -> phone microphone", acq, noise)
    blobs["mac_to_phone"] = (cap, tone)

    acq, noise, cap, tone = phone_to_mac(x, inv)
    results["phone_to_mac"] = _report("phone speaker -> Mac microphone", acq, noise)
    blobs["phone_to_mac"] = (cap, tone)

    os.makedirs(a.outdir, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    base = os.path.join(a.outdir, f"crosscal-{a.label}-{stamp}")
    np.savez_compressed(
        base + ".npz",
        **{f"{k}_{n}": v.astype(np.float32)
           for k, (c, t) in blobs.items() for n, v in (("capture", c), ("tone", t))},
    )
    meta = {
        "label": a.label, "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "sr": F.SR, "sweep_amp": a.amp,
        "media_volume": level, "media_volume_readback": readback,
        "tail_s": TAIL_S, "room_tone_s": ROOM_TONE_S, "horizon_ms": HORIZON_MS,
        "phone": provenance, "serial": serial,
        "directions": {
            name: {
                "support": out.support, "horizon_ms": out.horizon_ms,
                "noise_power": out.noise_power, "truncated": out.truncated,
                "readings": {
                    f"{int(db)}dB": (
                        {"ms": out.at(db).ms, "interval": out.at(db).ms_interval,
                         "beyond_budget": ds.exceeds_guard_budget(
                             out.at(db), geo.PRACTICAL_GUARD_BUDGET_MS)}
                        if out.at(db).usable else
                        {"status": out.at(db).status, "detail": out.at(db).detail}
                    ) for db in ds.THRESHOLDS_DB
                },
            } for name, out in results.items()
        },
    }
    with open(base + ".json", "w") as fh:
        json.dump(meta, fh, indent=2)
    print(f"\nretained: {base}.npz / .json")


if __name__ == "__main__":
    main()
