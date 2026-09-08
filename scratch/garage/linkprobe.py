#!/usr/bin/env python3
"""Attempt one conservative link on each directed link, and report what decoded.

The delay-spread readout says whether a position's late energy exceeds the guard
budget. This asks the other question: does a conservative coherent link actually
carry anything there. The two together are what stage 1G needs -- the gate is a
spending decision about a position that keeps *failing*, so a measurement of the
channel is not a substitute for trying it.

Payload is independently seeded per frame and verified at its scheduled block
positions, so a decode that lands the wrong payload in the right slot does not
count. The expected bytes are never available to the receiver.

Usage:
    ANDROID_SERIAL=<serial> .venv/bin/python3 scratch/garage/linkprobe.py --label desk-1ft
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
import clib  # noqa: E402
import crosscal  # noqa: E402
import freqresp as F  # noqa: E402
import geometries as geo  # noqa: E402
import harness as H  # noqa: E402

N_FRAMES = 2
"""The plan's normal research burst: two data frames with a declared gap."""

GAP_S = 0.25
"""Declared inter-frame gap, counted in the schedule rather than assumed away."""

SEARCH_SLACK = 12000
"""Extra samples beyond one frame in each search window. Enough to absorb
acquisition latency and a little drift, small enough that a second frame's chirp
cannot fall inside the same window."""

SEARCH_STEP = 4000
"""Search stride. Must be smaller than SEARCH_SLACK so that some window starts in
the interval just before each frame's chirp; otherwise a present frame can fall
between two windows and score zero."""

TAIL_S = 0.4
"""Trailing silence. NEGATIVE_FINDINGS entry 5: the macOS output chain tapers the
last ~10 ms when a stream stops, which kills the final OFDM symbol, so the
transmit contract appends silence."""


def build(cfg, seed):
    """Frames with independently seeded payloads, plus the schedule they occupy."""
    geom = clib.geometry(cfg)
    rng = np.random.default_rng(seed)
    payloads = [
        rng.integers(0, 256, geom.payload_bytes, dtype=np.uint8).tobytes()
        for _ in range(N_FRAMES)
    ]
    gap = np.zeros(int(GAP_S * F.SR))
    pieces = []
    for i, payload in enumerate(payloads):
        if i:
            pieces.append(gap)
        pieces.append(np.asarray(clib.encode(cfg, payload), dtype=np.float64))
    pieces.append(np.zeros(int(TAIL_S * F.SR)))
    return np.concatenate(pieces), payloads, geom


def acquisition_evidence(rx, wave):
    """Did the signal arrive, and did it arrive as something acquirable?

    NEGATIVE_FINDINGS entry 13's decisive observation was that chirp sync locked
    cleanly -- matched-filter peak/mean 104 -- in a geometry that then decoded
    zero blocks. That is what separates "the channel is too dispersive" from "no
    signal reached the receiver", so it is measured here rather than assumed.
    """
    if len(rx) < len(wave):
        return {"peak_over_mean": None, "detail": "capture shorter than the transmission"}
    mf = np.abs(np.correlate(rx, wave[: min(len(wave), 4 * F.SR)], mode="valid"))
    if not len(mf) or mf.mean() <= 0:
        return {"peak_over_mean": None, "detail": "no correlation support"}
    return {
        "peak_over_mean": float(mf.max() / mf.mean()),
        "peak_at_s": float(int(np.argmax(mf)) / F.SR),
    }


def score(cfg, rx, payloads, geom):
    """Ordered verified blocks across the capture, per frame, payload-independent."""
    # The C receiver acquires the *strongest* chirp inside the buffer it is
    # given, so a window spanning two frames scores the stronger one and hides
    # the other entirely -- the positive control decoded 26/52 that way while
    # both frames were in fact perfect. The window therefore holds at most one
    # frame, and the step is fine enough that some window starts just before each
    # frame's chirp.
    window = geom.frame_samples + SEARCH_SLACK
    step = SEARCH_STEP
    best = [0] * len(payloads)
    best_evm = None
    for start in range(0, max(1, len(rx) - geom.frame_samples), step):
        decoded = clib.decode(cfg, rx[start : start + window])
        if decoded is None:
            continue
        evm = decoded.get("evm")
        if evm is not None and np.isfinite(evm) and (best_evm is None or evm < best_evm):
            best_evm = float(evm)
        for i, payload in enumerate(payloads):
            best[i] = max(best[i], clib.ordered_verified_blocks(decoded, payload))
    return sum(best), best, best_evm


def probe(name, cfg, wave, payloads, geom, send):
    rx = send(wave)
    peak = float(np.max(np.abs(rx))) if len(rx) else 0.0
    verified, per_frame, best_evm = score(cfg, rx, payloads, geom)
    evidence = acquisition_evidence(rx, wave)
    scheduled = N_FRAMES * geom.n_blocks
    airtime = len(wave) / F.SR
    print(f"\n{name}")
    print(f"  capture {len(rx)} samples, peak {peak:.4f}"
          + ("   WARNING: clipping" if peak >= 0.99 else ""))
    pom = evidence.get("peak_over_mean")
    print(f"  matched-filter peak/mean {pom:.1f}" % () if False else
          f"  matched-filter peak/mean {pom if pom is None else round(pom, 1)}"
          f"   best EVM {best_evm if best_evm is None else round(best_evm, 3)}")
    print(f"  verified {verified}/{scheduled} ordered blocks {per_frame}")
    print(f"  scheduled airtime {airtime:.2f} s -> "
          f"{verified * 256 * 8 / airtime:.0f} bps of verified payload")
    return {
        "verified": verified, "scheduled": scheduled, "per_frame": per_frame,
        "capture_peak": peak, "airtime_s": airtime,
        "bps": verified * 256 * 8 / airtime,
        "best_evm": best_evm, "acquisition": evidence,
    }, rx


def self_loop(a):
    """Positive control: the Mac's own speaker to its own microphone.

    An all-fail map cannot be distinguished from broken tooling without a cell
    that is expected to pass. This path measures 0.9 ms of strong-tap spread
    against the 16 ms budget, so the same code, the same codec and the same
    scoring must carry it. If this fails, nothing else in the run means anything.
    """
    link = geo.conservative_link(geo.MACBOOK_PRO_M4, geo.MACBOOK_PRO_M4, amp=a.amp)
    cfg = link.to_clib_cfg(clib)
    wave, payloads, geom = build(cfg, seed=21)
    print(f"\n{link.label}: {link.f_lo_hz:.0f}-{link.f_hi_hz:.0f} Hz, "
          f"{geom.n_blocks} blocks/frame")

    def send(w):
        return acquire.play_and_record(
            w, F.SR, acquire.MAC_SPEAKERS, acquire.MAC_MICROPHONE, tail_s=1.0
        )[:, 0]

    return probe("Mac -> Mac, conservative QPSK r1/2 (positive control)",
                 cfg, wave, payloads, geom, send)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="linkprobe")
    ap.add_argument("--amp", type=float, default=0.5)
    ap.add_argument("--outdir", default="artifacts/garage")
    ap.add_argument("--control-only", action="store_true",
                    help="run just the Mac self-loop control; needs no phone")
    a = ap.parse_args()

    if a.control_only:
        self_loop(a)
        return

    serial = H.validated_android_serial()
    level, readback = crosscal.media_volume()
    print(f"phone: {serial}\nmedia volume: {readback}")
    token = crosscal.run_token()
    results = {}

    down = geo.conservative_link(geo.MACBOOK_PRO_M4, geo.PIXEL_7A, amp=a.amp).at_amplitude(a.amp)
    cfg = down.to_clib_cfg(clib)
    wave, payloads, geom = build(cfg, seed=11)
    print(f"\n{down.label}: {down.f_lo_hz:.0f}-{down.f_hi_hz:.0f} Hz, "
          f"{geom.n_blocks} blocks/frame, {geom.frame_samples / F.SR:.2f} s/frame")

    def send_down(w):
        name = f"lp_m2p_{token}.pcm"
        dur = len(w) / F.SR + 0.7 + 1.0 + 8.0
        H.android_record_start(dur, channels=2, source="unprocessed", out_name=name)
        time.sleep(0.7)
        acquire.play_and_record(w, F.SR, acquire.MAC_SPEAKERS, acquire.MAC_MICROPHONE, tail_s=1.0)
        H.android_record_finish(dur, out_name=name)
        return crosscal._pull_capture(name)

    results["mac_to_phone"], rx_down = probe("Mac -> Pixel, conservative QPSK r1/2", cfg,
                                             wave, payloads, geom, send_down)

    up = geo.conservative_link(geo.PIXEL_7A, geo.MACBOOK_PRO_M4, amp=a.amp).at_amplitude(a.amp)
    cfg_up = up.to_clib_cfg(clib)
    wave_up, payloads_up, geom_up = build(cfg_up, seed=12)
    print(f"\n{up.label}: {up.f_lo_hz:.0f}-{up.f_hi_hz:.0f} Hz, "
          f"{geom_up.n_blocks} blocks/frame")

    def send_up(w):
        H.android_prepare(media_volume=None)
        return np.asarray(
            H.android_to_mac(w.astype(np.float32), channels=1, post_s=1.0), dtype=np.float64
        ).reshape(-1)

    results["phone_to_mac"], rx_up = probe("Pixel -> Mac, conservative QPSK r1/2", cfg_up,
                                           wave_up, payloads_up, geom_up, send_up)

    results["mac_self_loop"], _ = self_loop(a)

    os.makedirs(a.outdir, exist_ok=True)
    base = os.path.join(a.outdir, f"linkprobe-{a.label}-{time.strftime('%Y%m%dT%H%M%S')}")
    np.savez_compressed(base + ".npz", mac_to_phone=rx_down.astype(np.float32),
                        phone_to_mac=rx_up.astype(np.float32))
    with open(base + ".json", "w") as fh:
        json.dump({
            "label": a.label, "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "serial": serial, "media_volume": level, "sweep_amp": a.amp,
            "n_frames": N_FRAMES, "gap_s": GAP_S, "results": results,
        }, fh, indent=2)
    print(f"\nretained: {base}.json")


if __name__ == "__main__":
    main()
