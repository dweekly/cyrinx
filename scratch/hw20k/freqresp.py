#!/usr/bin/env python3
"""Farina log-swept-sine channel sounding -> transfer function H(f), impulse
response, and per-bin SNR, for the whitepaper's frequency-response figure grid.

Why a dedicated swept-sine (vs. the modem's QPSK-symbol sounder in sounder.py):
the exponential sine sweep (ESS) deconvolution of Farina [1] yields a clean,
high-resolution magnitude/phase transfer function AND separates the linear
impulse response from harmonic-distortion products (which appear as *pre*-echoes
ahead of the linear IR and are simply windowed out). That makes it the right
primitive for publication-grade |H(f)| curves across device pairings and
environments. sounder.py stays the operational, low-latency sounding used for
MCS selection; this module is the measurement instrument.

  [1] A. Farina, "Simultaneous measurement of impulse response and distortion
      with a swept-sine technique," 108th AES Convention, 2000.

Calibration: RELATIVE. We pin the drive amplitude and (caller pins) the mic gain,
and report |H(f)| in dB relative to its own in-band median. That is valid for
within-device cross-environment overlays and for band-edge/shape claims; it is
NOT absolute SPL and cross-device magnitude comparison is only fair if drive and
gain are identical (stated on the figure).

Usage (bench, needs hardware):
  freqresp.py mac2pixel  <env_label>      # Mac speakers -> Pixel mic
  freqresp.py pixel2mac  <env_label>      # Pixel speaker -> Mac mic
  freqresp.py mac2iphone <env_label>      # Mac speakers -> iPhone mic
  freqresp.py iphone2mac <env_label>      # iPhone speaker -> Mac mic
Writes data/freqresp/<path>_<env>.json.

Offline self-test (no hardware):
  freqresp.py selftest
synthesizes the sweep through a known multipath channel + noise and checks that
|H(f)| and the recovered taps match the truth within tolerance.
"""
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SR = 48000
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "freqresp")

# Sweep parameters. f_lo just above DC rumble; f_hi to Nyquist-ish so we can
# see the ultrasonic roll-off and phase collapse > 18 kHz that the paper reports.
SWEEP_F0 = 60.0
SWEEP_F1 = 23500.0
SWEEP_DUR = 6.0          # long sweep -> good low-frequency resolution + SNR
PAD_PRE = 0.7            # silence before sweep (let the OTA path settle)
PAD_POST = 1.0           # silence after sweep (capture the IR tail)
AMP = 0.5                # drive amplitude (pinned for relative calibration)


def make_ess(f0=SWEEP_F0, f1=SWEEP_F1, dur=SWEEP_DUR, sr=SR, amp=AMP):
    """Exponential sine sweep x(t) and its inverse filter f(t) (Farina).

    The inverse filter is the time-reversed sweep with a +6 dB/oct amplitude
    envelope that whitens the 1/f energy density of the ESS, so that
    x * f = delta (band-limited). Convolving the *capture* with f recovers the
    impulse response, with harmonic distortion pushed to negative time.
    """
    n = int(dur * sr)
    t = np.arange(n) / sr
    w0, w1 = 2 * np.pi * f0, 2 * np.pi * f1
    K = dur * w0 / np.log(w1 / w0)
    L = np.log(w1 / w0) / dur
    x = np.sin(K * (np.exp(t * L) - 1.0))
    # 20 ms raised-cosine fades to avoid spectral splatter at the ends
    r = int(0.020 * sr)
    env = np.ones(n)
    env[:r] = 0.5 - 0.5 * np.cos(np.pi * np.arange(r) / r)
    env[-r:] = env[:r][::-1]
    x = (amp * x * env).astype(np.float32)
    # inverse filter: reversed sweep, scaled by exp(-t L) (i.e. +6 dB/oct rising
    # toward high freq in forward time) to flatten the sweep's pink spectrum.
    inv = x[::-1].astype(np.float64) * np.exp(-t * L)
    # normalize so that x (*) inv has unit peak
    conv_peak = np.max(np.abs(np.convolve(x.astype(np.float64), inv, mode="full")))
    inv = inv / conv_peak
    return x, inv.astype(np.float64)


def deconvolve_ir(capture, inv, sr=SR):
    """Recover the linear impulse response from an ESS capture.

    The full convolution puts the linear IR at the index where the sweep ends;
    harmonic-distortion IRs sit at *earlier* indices. We locate the linear IR by
    its energy peak, window a causal slice around it, and return that slice.
    """
    cap = np.asarray(capture, dtype=np.float64)
    cap = cap - cap.mean()
    full = np.convolve(cap, inv, mode="full")
    pk = int(np.argmax(np.abs(full)))
    # causal window: a few ms before the peak (main tap) to ~120 ms after
    # (covers any room/desk reverberant tail; far longer than our delay spreads).
    pre = int(0.005 * sr)
    post = int(0.120 * sr)
    lo = max(0, pk - pre)
    ir = full[lo: pk + post]
    # index of the main tap within the returned slice
    peak_idx = pk - lo
    return ir, peak_idx


def transfer_function(ir, peak_idx, sr=SR, nfft=16384):
    """|H(f)| (dB, relative) and phase from the windowed linear IR."""
    h = np.asarray(ir, dtype=np.float64)
    # Taper the tail so the FFT is not contaminated by the window edge.
    w = np.ones(len(h))
    r = min(len(h) // 4, int(0.010 * sr))
    if r > 0:
        w[-r:] = 0.5 + 0.5 * np.cos(np.pi * np.arange(r) / r)
    h = h * w
    H = np.fft.rfft(h, nfft)
    freqs = np.fft.rfftfreq(nfft, 1.0 / sr)
    mag = np.abs(H)
    return freqs, mag, np.angle(H)


def delay_spread(ir, peak_idx, sr=SR):
    """Schroeder energy-decay delay spread (ms) at -10/-15/-20 dB, measured from
    the main tap. Mirrors the convention in sounder.analyze()."""
    e = np.abs(np.asarray(ir, dtype=np.float64)) ** 2
    e = e[peak_idx:]                       # causal, from the main tap
    edc = np.cumsum(e[::-1])[::-1]
    edc = edc / (edc[0] + 1e-300)
    edc_db = 10 * np.log10(edc + 1e-300)

    def t_at(db):
        idx = np.where(edc_db < db)[0]
        return float(idx[0] / sr * 1000) if len(idx) else float(len(e) / sr * 1000)

    return {"-10dB": t_at(-10), "-15dB": t_at(-15), "-20dB": t_at(-20)}


def _welch(x, sr=SR, nfft=8192):
    """Single-sided Welch PSD (Hann, 50% overlap). Returns (freqs, psd)."""
    x = np.asarray(x, dtype=np.float64)
    x = x - x.mean()
    w = np.hanning(min(nfft, len(x)))
    if len(w) < 2:
        return np.array([0.0]), np.array([0.0])
    step = max(1, len(w) // 2)
    acc = np.zeros(len(w) // 2 + 1)
    k = 0
    for i in range(0, len(x) - len(w) + 1, step):
        acc += np.abs(np.fft.rfft(x[i:i + len(w)] * w)) ** 2
        k += 1
    return np.fft.rfftfreq(len(w), 1.0 / sr), acc / max(1, k)


def snr_from_captures(sweep_capture, noise_capture, grid_freqs, sr=SR):
    """Received SNR(f) in dB = PSD of the sweep capture / PSD of a room-tone
    (silent) capture taken at the SAME receiver gain, interpolated onto the
    H(f) frequency grid. Well-defined and apples-to-apples: both are measured
    receiver-side spectra. Returns None if no noise capture is given (figures
    then omit the SNR overlay)."""
    if noise_capture is None:
        return None
    fs, ps = _welch(sweep_capture, sr)
    _, pn = _welch(noise_capture, sr)
    snr_db = 10 * np.log10(np.maximum(ps, 1e-20) / np.maximum(pn, 1e-20))
    return np.interp(grid_freqs, fs, snr_db)


def analyze_room_tone(noise, sr=SR):
    """Passive ambient ("room tone") analysis: no transmission, just listen.

    Lets an adaptive link pre-screen the channel passively (the user's idea):
    how loud is the band, and is there structured occupancy -- a near-ultrasonic
    jammer, an HVAC tone -- that a transmission would have to fight? Returns the
    broadband level, per-band PSD, and a near-ultrasonic interferer flag (a
    18-22 kHz peak standing clearly above the in-band median)."""
    x = np.asarray(noise, dtype=np.float64)
    freqs, psd = _welch(x, sr)

    def band_db(lo, hi):
        sel = (freqs >= lo) & (freqs < hi)
        return round(float(10 * np.log10(psd[sel].sum() + 1e-20)), 1) if sel.any() else -200.0

    bands = {f"{lo // 1000}-{hi // 1000}kHz": band_db(lo, hi)
             for lo, hi in [(300, 3000), (3000, 8000), (8000, 14000),
                            (14000, 18000), (18000, 22000)]}
    inband = (freqs >= 300) & (freqs <= 22000)
    med = float(np.median(10 * np.log10(psd[inband] + 1e-20))) if inband.any() else -200.0
    us = (freqs >= 18000) & (freqs <= 22000)
    us_peak = float(10 * np.log10(psd[us].max() + 1e-20)) if us.any() else -200.0
    return {
        "rms_inband": float(np.sqrt(np.mean(x ** 2))),
        "band_psd_db": bands,
        "near_ultrasonic_excess_db": round(us_peak - med, 1),
        "near_ultrasonic_interferer": bool(us_peak - med > 12.0),
    }


def measure(send_fn, env_label, path_label, noise_fn=None, sr=SR):
    """Run one frequency-response measurement over an OTA path.

    send_fn(wave)->capture plays a mono float32 waveform and returns the mono
    capture (e.g. a closure over harness.mac_to_android / android_to_mac).
    noise_fn() optionally returns a silent capture of equal-ish length for SNR.
    """
    x, inv = make_ess(sr=sr)
    tx = np.concatenate([np.zeros(int(PAD_PRE * sr), np.float32), x,
                         np.zeros(int(PAD_POST * sr), np.float32)])
    cap = np.asarray(send_fn(tx), dtype=np.float64)
    ir, pk = deconvolve_ir(cap, inv, sr=sr)
    freqs, mag, phase = transfer_function(ir, pk, sr=sr)
    ds = delay_spread(ir, pk, sr=sr)
    noise_cap = noise_fn() if noise_fn is not None else None
    snr = snr_from_captures(cap, noise_cap, freqs, sr=sr)
    room = analyze_room_tone(noise_cap, sr) if noise_cap is not None else None

    # relative magnitude in dB, normalized to the in-band median
    band = (freqs >= 300) & (freqs <= 20000)
    ref = np.median(mag[band]) + 1e-300
    mag_db = 20 * np.log10(np.maximum(mag, 1e-12) / ref)

    rec = {
        "path": path_label, "env": env_label, "sr": sr,
        "sweep": {"f0": SWEEP_F0, "f1": SWEEP_F1, "dur_s": SWEEP_DUR, "amp": AMP},
        "calibration": "relative (drive+gain pinned; dB re in-band median)",
        "freqs_hz": freqs.tolist(),
        "H_mag_db": mag_db.round(3).tolist(),
        "H_phase_rad": phase.round(4).tolist(),
        "snr_db": (snr.round(2).tolist() if snr is not None else None),
        "delay_spread_ms": ds,
        "rx_peak": float(np.abs(cap).max()),
        "room_tone": room,
    }
    os.makedirs(DATA, exist_ok=True)
    out = os.path.join(DATA, f"{path_label}_{env_label}.json")
    with open(out, "w") as f:
        json.dump(rec, f)
    print(f"  wrote {out}")
    print(f"  rx_peak={rec['rx_peak']:.3f}  delay_spread(-15dB)={ds['-15dB']:.2f} ms"
          + (f"  median SNR={np.median(snr[band]):.1f} dB" if snr is not None else ""))
    return rec


# ----------------------------- offline self-test -----------------------------

def _selftest():
    """Synthesize the ESS through a known FIR channel + noise and confirm the
    deconvolution recovers the channel's magnitude response and tap structure.
    No hardware, no audio. This is the regression net for the analysis math."""
    sr = SR
    x, inv = make_ess(sr=sr)
    tx = np.concatenate([np.zeros(int(PAD_PRE * sr), np.float32), x,
                         np.zeros(int(PAD_POST * sr), np.float32)])
    # Truth channel: the golden-vector multipath taps used elsewhere in the repo
    # (Tests/Fixtures/golden rx_channel_taps), as (delay_samples, gain).
    taps = [(0, 1.0), (37, 0.45), (113, -0.22), (260, 0.12)]
    h_true = np.zeros(512)
    for d, g in taps:
        h_true[d] = g
    rx = np.convolve(tx.astype(np.float64), h_true, mode="full")[:len(tx)]
    rng = np.random.default_rng(1)
    rx = rx + rng.normal(0, 1e-3, len(rx))      # ~ -60 dB noise

    ir, pk = deconvolve_ir(rx, inv, sr=sr)
    # 1) recovered taps: peak at lag 0, secondary taps at 37/113/260 samples.
    # The band-limited delta spreads each tap into a sinc, so read the signed
    # local extremum within +-2 samples of the expected delay rather than a
    # single sample, and normalize by the recovered main tap.
    seg = ir[pk: pk + 300]
    main = seg[0]

    def tap_at(d):
        w = seg[max(0, d - 2): d + 3]
        return w[np.argmax(np.abs(w))] / main

    ok_taps = True
    for d, g in taps:
        rec_g = tap_at(d)
        bad = abs(rec_g - g) > 0.06
        ok_taps &= not bad
        print(f"    tap@{d}: recovered {rec_g:+.3f} vs truth {g:+.3f}  "
              f"{'MISMATCH' if bad else 'ok'}")

    # 2) magnitude response matches |FFT(h_true)| within tolerance in-band
    freqs, mag, _ = transfer_function(ir, pk, sr=sr)
    Htrue = np.abs(np.fft.rfft(h_true, 16384))
    ft = np.fft.rfftfreq(16384, 1.0 / sr)
    band = (ft >= 300) & (ft <= 20000)
    a = 20 * np.log10(mag[band] / np.median(mag[band]))
    b = 20 * np.log10(Htrue[band] / np.median(Htrue[band]))
    err = np.abs(a - b)
    p95 = np.percentile(err, 95)
    print(f"    |H(f)| in-band error: median={np.median(err):.2f} dB  p95={p95:.2f} dB")

    # 3) delay spread is finite and on the order of the 260-sample tap (~5.4 ms)
    ds = delay_spread(ir, pk, sr=sr)
    print(f"    delay spread -15dB={ds['-15dB']:.2f} ms (expect a few ms)")

    # 4) room-tone analysis: white noise -> no interferer; white + a 20 kHz tone
    #    -> near-ultrasonic interferer flag fires.
    t = np.arange(2 * sr) / sr
    white = rng.normal(0, 1e-3, len(t))
    rt_clean = analyze_room_tone(white, sr)
    jam = white + 0.02 * np.sin(2 * np.pi * 20000 * t)
    rt_jam = analyze_room_tone(jam, sr)
    print(f"    room-tone: clean interferer={rt_clean['near_ultrasonic_interferer']} "
          f"(expect False); jammed={rt_jam['near_ultrasonic_interferer']} "
          f"(expect True), excess={rt_jam['near_ultrasonic_excess_db']} dB")
    ok_room = (not rt_clean["near_ultrasonic_interferer"]) and rt_jam["near_ultrasonic_interferer"]

    # 5) snr_from_captures returns a finite in-band SNR (sweep capture vs noise)
    snr = snr_from_captures(rx, white, freqs, sr=sr)
    ok_snr = snr is not None and np.isfinite(snr[(freqs >= 300) & (freqs <= 20000)]).all()
    print(f"    snr_from_captures finite in-band: {ok_snr}")

    ok = ok_taps and p95 < 2.0 and 0.5 < ds["-15dB"] < 20.0 and ok_room and ok_snr
    print("  SELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


# ----------------------------------- CLI -------------------------------------

def _send_for(path_label):
    """Build a send_fn closure for the named OTA path (bench only)."""
    import harness as H
    H.mac_set_output_volume(100)
    if path_label == "mac2pixel":
        def send(w):
            _, p = H.mac_to_android(w, out_name="fr_m2p.pcm")
            return H.load_pcm16(p, channels=2)[:, 0]
        return send
    if path_label == "pixel2mac":
        return lambda w: H.android_to_mac(w)
    if path_label in ("mac2iphone", "iphone2mac"):
        import ios_harness as I
        if path_label == "mac2iphone":
            def send(w):
                _, p = I.mac_to_ios(w, out_name="fr_m2i.pcm")
                return np.fromfile(p, dtype="<i2").astype(np.float32) / 32768.0
            return send
        return lambda w: I.ios_to_mac(w)
    raise SystemExit(f"unknown path '{path_label}'")


def _record_for(path_label, dur=2.0):
    """Record-only ambient ("room tone") on the RECEIVER side of the named path
    -- no playback. Used both as the SNR noise floor and the passive sounding."""
    import harness as H
    rx_is_phone = path_label in ("mac2pixel", "mac2iphone")
    if not rx_is_phone:                       # RX is the Mac
        return lambda: H.mac_record(dur)
    if path_label == "mac2pixel":             # RX is the Pixel
        def rec():
            _, p = H.android_record(dur, out_name="rt_pixel.pcm")
            return H.load_pcm16(p, channels=2)[:, 0]
        return rec
    # RX is the iPhone
    import ios_harness as I
    if not hasattr(I, "ios_record"):
        raise SystemExit("iPhone room-tone needs ios_harness.ios_record")
    return lambda: np.asarray(I.ios_record(dur), dtype=np.float64)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "selftest"
    if cmd == "selftest":
        sys.exit(_selftest())
    if cmd == "roomtone":
        path = sys.argv[2]
        env = sys.argv[3] if len(sys.argv) > 3 else "default"
        print(f"=== room tone (passive): {path}  env={env} ===")
        rt = analyze_room_tone(_record_for(path)(), SR)
        os.makedirs(DATA, exist_ok=True)
        out = os.path.join(DATA, f"{path}_roomtone_{env}.json")
        with open(out, "w") as f:
            json.dump({"path": path, "env": env, **rt}, f)
        print(f"  in-band RMS={rt['rms_inband']:.2e}  near-US excess="
              f"{rt['near_ultrasonic_excess_db']} dB  "
              f"interferer={rt['near_ultrasonic_interferer']}")
        print(f"  band PSD (dB): {rt['band_psd_db']}")
        print(f"  wrote {out}")
        sys.exit(0)
    # otherwise cmd is a path label -> full frequency-response measure with the
    # receiver-side room tone captured for the SNR overlay.
    path = cmd
    env = sys.argv[2] if len(sys.argv) > 2 else "default"
    print(f"=== frequency response: {path}  env={env} ===")
    measure(_send_for(path), env, path, noise_fn=_record_for(path))
