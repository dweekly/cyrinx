#!/usr/bin/env python3
"""Environment sweep driver: at each physical cell (distance / orientation /
surface / ambient), measure the channel, ask the sounder what to do, then run
the *library* codec over the air and record what actually worked.

The through-line of the whole campaign (docs/EXPERIMENTS.md): every cell logs
  (a) the measured channel + frequency response (via freqresp.py),
  (b) the sounder's PREDICTED MCS tier (sounder.recommend),
  (c) the ACHIEVED ceiling tier (highest library-native MCS that decoded clean),
so the sweep doubles as a validation of the adaptive sounder against ground
truth, not just a "does it still work farther away" table.

Per cell, appends one record to data/env_sweep.jsonl. Bench only (needs the
dylib, audio, and the device). The pure decision logic has an offline selftest.

Usage:
  env_sweep.py <device> <direction> <cell_label> [reps]
    device:    pixel | iphone
    direction: down (Mac->phone) | up (phone->Mac)
    cell_label e.g. dist_20cm, orient_edge, surf_glass, noise_office_60dB
  env_sweep.py selftest
"""
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
LOG = os.path.join(DATA, "env_sweep.jsonl")

# Library-native MCS ladder we exercise, robust -> fast, named to match the
# sounder tiers in sounder.py:LADDER. Each entry: (tier, bits_per_bin, rate).
# 64-QAM r3/4 is included as the bit-loading headroom probe (Experiment #6).
MCS_LADDER = [
    ("qpsk",   2, "1/2"),
    ("medium", 4, "1/2"),
    ("fast",   4, "3/4"),
    ("hi",     6, "3/4"),
]
CLEAN_FRAC = 0.95          # a tier "decodes clean" if >= this fraction of blocks verify
TIER_ORDER = {t: i for i, (t, _, _) in enumerate(MCS_LADDER)}


def achieved_tier(per_tier, clean_frac=CLEAN_FRAC):
    """Given {tier: {verified, total}}, return the most aggressive tier whose
    verified-block fraction clears clean_frac, else None."""
    best = None
    for tier, _, _ in MCS_LADDER:
        r = per_tier.get(tier)
        if not r or r["total"] == 0:
            continue
        if r["verified"] / r["total"] >= clean_frac:
            if best is None or TIER_ORDER[tier] > TIER_ORDER[best]:
                best = tier
    return best


def prediction_verdict(predicted, achieved):
    """Compare the sounder's predicted tier to the empirically achieved ceiling.
    EXACT (matched), CONSERVATIVE (sounder under-called; link could go faster),
    OPTIMISTIC (sounder over-called; recommended tier did not decode clean),
    or FLOOR (sounder dropped to the non-coherent floor)."""
    if predicted in ("mfsk", None):
        return "floor"
    if achieved is None:
        return "optimistic"     # nothing decoded clean but sounder picked a tier
    pi, ai = TIER_ORDER.get(predicted), TIER_ORDER.get(achieved)
    if pi is None or ai is None:
        return "unknown"
    if pi == ai:
        return "exact"
    return "conservative" if pi < ai else "optimistic"


def goodput_bps(payload_bits_per_frame, verified_frac, span_s):
    """Verified user-payload goodput over the active message span."""
    if span_s <= 0:
        return 0.0
    return payload_bits_per_frame * verified_frac / span_s


# ------------------------------ bench driver ------------------------------

def _run_cell(device, direction, cell, reps):
    import harness as H
    import sounder as S
    import clib
    import freqresp
    H.mac_set_output_volume(100)

    # Build the OTA send_fn and a frequency-response path label for this cell.
    if device == "pixel" and direction == "down":
        path = "mac2pixel"

        def send(w):
            _, p = H.mac_to_android(w, out_name="es_m2p.pcm")
            return H.load_pcm16(p, channels=2)[:, 0]
    elif device == "pixel" and direction == "up":
        path = "pixel2mac"
        send = lambda w: H.android_to_mac(w)            # noqa: E731
    elif device == "iphone":
        import ios_harness as I
        if direction == "down":
            path = "mac2iphone"

            def send(w):
                _, p = I.mac_to_ios(w, out_name="es_m2i.pcm")
                return np.fromfile(p, dtype="<i2").astype(np.float32) / 32768.0
        else:
            path = "iphone2mac"
            send = lambda w: I.ios_to_mac(w)            # noqa: E731
    else:
        raise SystemExit(f"bad device/direction: {device}/{direction}")

    input(f"\n>>> Position for cell '{cell}' ({path}), then press Enter ...")

    # 1) frequency response (for the figure grid)
    print("  [1/3] frequency-response sounding ...")
    freqresp.measure(send, cell, path)

    # 2) sounder recommendation
    print("  [2/3] channel sounding -> recommendation ...")
    rec, an = S.sound_channel(send)
    print(f"        predicted tier={rec['tier']} ({rec['mcs']} r{rec['rate']}), "
          f"median SNR={rec['median_snr_db']} dB, ds15={rec['delay_spread_ms_15']} ms")

    # 3) library-native MCS ladder, reps each
    print(f"  [3/3] library-native ladder, {reps} reps/tier ...")
    per_tier = {}
    for tier, bpb, rate in MCS_LADDER:
        cfg = clib.make_cfg(bits_per_bin=bpb, rate=rate, n_sym=64)
        g = clib.geometry(cfg)
        verified = total = 0
        evms = []
        t0 = time.time()
        for _ in range(reps):
            payload = bytes((i * 31 + 7) & 0xFF for i in range(g.payload_bytes))
            wave = clib.encode(cfg, payload)
            cap = np.asarray(send(wave), dtype=np.float32)
            d = clib.decode(cfg, cap)
            # Every emitted repetition contributes its full scheduled block
            # count. A sync/decode failure is zero verified blocks, not a slot
            # that disappears from the denominator.
            total += g.n_blocks
            if d is not None:
                # ordered byte-exact verification at block granularity
                ok_blocks = clib.ordered_verified_blocks(d, payload)
                verified += ok_blocks
                evms.append(d["evm"])
        span_s = (len(wave) / cfg.sr)
        frac = verified / total if total else 0.0
        per_tier[tier] = {
            "verified": verified, "total": total,
            "verified_frac": round(frac, 4),
            "evm_med": round(float(np.median(evms)), 4) if evms else None,
            "payload_bits_per_frame": g.payload_bytes * 8,
            "goodput_bps": round(goodput_bps(g.payload_bytes * 8, frac, span_s), 1),
        }
        print(f"        {tier:7s} {('%d-QAM' % (1 << bpb)):7s} r{rate}: "
              f"{verified}/{total} blocks ({frac*100:.0f}%), "
              f"{per_tier[tier]['goodput_bps']/1000:.1f} kbps")

    ach = achieved_tier(per_tier)
    verdict = prediction_verdict(rec["tier"], ach)
    print(f"  => predicted={rec['tier']}  achieved={ach}  verdict={verdict}")

    record = {
        "device": device, "direction": direction, "path": path, "cell": cell,
        "reps": reps,
        "sounding": {
            "predicted_tier": rec["tier"], "mcs": rec["mcs"], "rate": rec["rate"],
            "median_snr_db": rec["median_snr_db"],
            "delay_spread_ms_15": rec["delay_spread_ms_15"],
            "cp": rec["cp"], "nfft": rec["nfft"],
            "advise_reposition": rec["advise_reposition"],
            "noncoherent": rec["noncoherent"],
        },
        "ladder": per_tier,
        "achieved_tier": ach,
        "prediction_verdict": verdict,
    }
    os.makedirs(DATA, exist_ok=True)
    with open(LOG, "a") as f:
        f.write(json.dumps(record) + "\n")
    print(f"  appended {LOG}")
    return record


# ------------------------------ offline test ------------------------------

def _selftest():
    ok = True

    # achieved_tier picks the most aggressive clean tier
    pt = {"qpsk": {"verified": 100, "total": 100},
          "medium": {"verified": 98, "total": 100},
          "fast": {"verified": 60, "total": 100},
          "hi": {"verified": 5, "total": 100}}
    a = achieved_tier(pt)
    ok &= (a == "medium"); print(f"  achieved_tier -> {a} (expect medium)")

    # all clean -> the top tier
    a2 = achieved_tier({t: {"verified": 100, "total": 100} for t, _, _ in MCS_LADDER})
    ok &= (a2 == "hi"); print(f"  achieved_tier(all clean) -> {a2} (expect hi)")

    # nothing clean -> None
    a3 = achieved_tier({"qpsk": {"verified": 10, "total": 100}})
    ok &= (a3 is None); print(f"  achieved_tier(none clean) -> {a3} (expect None)")

    # verdicts
    cases = [
        ("fast", "fast", "exact"),
        ("qpsk", "fast", "conservative"),   # sounder under-called
        ("fast", "qpsk", "optimistic"),     # sounder over-called
        ("fast", None, "optimistic"),
        ("mfsk", None, "floor"),
    ]
    for pred, ach, want in cases:
        got = prediction_verdict(pred, ach)
        ok &= (got == want); print(f"  verdict({pred},{ach}) -> {got} (expect {want})")

    # goodput
    g = goodput_bps(19200 * 8, 1.0, 4.0)
    ok &= abs(g - 38400.0) < 1.0; print(f"  goodput_bps -> {g:.1f} (expect 38400.0)")

    # A no-sync repetition remains in the fixed schedule denominator.
    scheduled = 3 * 75
    recovered = 75 + 0 + 60
    frac = recovered / scheduled
    ok &= scheduled == 225 and abs(frac - 0.6) < 1e-12
    print(f"  fixed schedule with no-sync slot -> {recovered}/{scheduled} (expect 135/225)")

    print("  SELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "selftest":
        sys.exit(_selftest())
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(2)
    device, direction, cell = sys.argv[1], sys.argv[2], sys.argv[3]
    reps = int(sys.argv[4]) if len(sys.argv) > 4 else 8
    _run_cell(device, direction, cell, reps)
