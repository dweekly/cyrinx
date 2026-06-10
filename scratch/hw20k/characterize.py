#!/usr/bin/env python3
"""Channel characterization for the Mac <-> Pixel 7a acoustic link.

Measures (real hardware, real air):
  1. Ambient noise PSD on both receivers.
  2. Per-bin SNR both directions via probe-on vs probe-off PSD (no sync needed).
  3. Impulse response / delay spread via exponential sine sweep (ESS).
  4. Sample clock offset (ppm) between the two devices via a long pure tone.

Outputs JSON + PNG plots into scratch/hw20k/data/.
"""

import json
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H

SR = H.SR
DATA = H.DATA
NFFT = 1024  # matches the modem FFT; 46.875 Hz/bin at 48 kHz


def welch_psd(x, nfft=NFFT):
    from scipy.signal import welch
    f, p = welch(x, fs=SR, nperseg=nfft, noverlap=nfft // 2, window="hann")
    return f, p


def multitone_probe(dur_s=6.0, amp=0.5, f_lo=300.0, f_hi=23500.0, seed=1):
    """Random-phase multitone covering all FFT bins in [f_lo, f_hi].
    Crest-factor-reduced by random phases; amplitude-normalized to `amp` peak."""
    rng = np.random.default_rng(seed)
    n = int(dur_s * SR)
    spec = np.zeros(n // 2 + 1, dtype=complex)
    freqs = np.fft.rfftfreq(n, 1 / SR)
    sel = (freqs >= f_lo) & (freqs <= f_hi)
    spec[sel] = np.exp(2j * np.pi * rng.random(sel.sum()))
    x = np.fft.irfft(spec, n)
    x = x / np.abs(x).max() * amp
    r = int(0.01 * SR)
    env = np.ones(n)
    env[:r] = 0.5 - 0.5 * np.cos(np.pi * np.arange(r) / r)
    env[-r:] = env[:r][::-1]
    return (x * env).astype(np.float32)


def ess(f0=100.0, f1=23900.0, dur_s=4.0, amp=0.5):
    """Exponential sine sweep + its inverse filter (Farina method)."""
    n = int(dur_s * SR)
    t = np.arange(n) / SR
    R = np.log(f1 / f0)
    w = np.sin(2 * np.pi * f0 * dur_s / R * (np.exp(t * R / dur_s) - 1))
    r = int(0.01 * SR)
    env = np.ones(n)
    env[:r] = 0.5 - 0.5 * np.cos(np.pi * np.arange(r) / r)
    env[-r:] = env[:r][::-1]
    sweep = (amp * w * env).astype(np.float32)
    inv = sweep[::-1] * np.exp(-t * R / dur_s)
    return sweep, inv.astype(np.float32)


def snr_per_bin(probe_psd, noise_psd):
    snr = (probe_psd - noise_psd) / np.maximum(noise_psd, 1e-20)
    return 10 * np.log10(np.maximum(snr, 1e-3))


def analyze_direction(name, rx_probe, rx_noise, results):
    f, p_on = welch_psd(rx_probe)
    _, p_off = welch_psd(rx_noise)
    snr_db = snr_per_bin(p_on, p_off)
    results[name] = {
        "freqs_hz": f.tolist(),
        "snr_db": snr_db.tolist(),
        "noise_psd_db": (10 * np.log10(np.maximum(p_off, 1e-20))).tolist(),
    }
    # capacity estimate (Shannon, per-bin, capped at 8 bits like 256-QAM)
    df = f[1] - f[0]
    bits = np.minimum(np.log2(1 + 10 ** (np.maximum(snr_db, 0) / 10)), 8.0)
    cap = float((bits * df)[(f > 200) & (f < 24000)].sum())
    results[name]["shannon_capacity_bps"] = cap
    print(f"  {name}: usable Shannon capacity ~{cap/1000:.1f} kbps")
    for lo, hi in [(300, 2000), (2000, 6000), (6000, 10000), (10000, 14000),
                   (14000, 18000), (18000, 21000), (21000, 24000)]:
        m = (f >= lo) & (f < hi)
        print(f"    {lo/1000:5.1f}-{hi/1000:4.1f} kHz: median SNR {np.median(snr_db[m]):6.1f} dB")
    return f, snr_db


def main():
    os.makedirs(DATA, exist_ok=True)
    results = {"sample_rate": SR, "nfft": NFFT}
    H.mac_set_output_volume(100)

    probe = multitone_probe(dur_s=6.0, amp=0.6)
    H.save_wav(os.path.join(DATA, "probe_multitone.wav"), probe)

    print("== 1. Ambient noise floors (6 s each) ==")
    _, p = H.android_record(6.0, out_name="noise_android.pcm")
    and_noise = H.load_pcm16(p)
    print(f"  android ambient rms ch0={np.sqrt((and_noise[:,0]**2).mean()):.6f} "
          f"ch1={np.sqrt((and_noise[:,1]**2).mean()):.6f}")
    mac_noise = H.mac_record(6.0)
    print(f"  mac ambient rms {np.sqrt((mac_noise**2).mean()):.6f}")

    print("== 2. Multitone probe Mac -> Android ==")
    line, p = H.mac_to_android(probe, out_name="probe_m2a.pcm")
    m2a = H.load_pcm16(p)
    print(f"  m2a rms ch0={np.sqrt((m2a[:,0]**2).mean()):.5f} ch1={np.sqrt((m2a[:,1]**2).mean()):.5f} "
          f"peak={np.abs(m2a).max():.4f}")
    # trim to the loud middle portion to exclude leading/trailing silence
    e = np.convolve(m2a[:, 0] ** 2, np.ones(4800) / 4800, mode="same")
    core = e > e.max() * 0.1
    i0, i1 = np.argmax(core), len(core) - np.argmax(core[::-1])
    m2a_core = m2a[i0 + SR // 2: i1 - SR // 2]
    print(f"  core {len(m2a_core)/SR:.1f}s")
    f, snr_m2a = analyze_direction("mac_to_android_ch0", m2a_core[:, 0], and_noise[SR:, 0], results)
    _, snr_m2a1 = analyze_direction("mac_to_android_ch1", m2a_core[:, 1], and_noise[SR:, 1], results)

    print("== 3. Multitone probe Android -> Mac ==")
    a2m = H.android_to_mac(probe)
    H.save_wav(os.path.join(DATA, "probe_a2m_rx.wav"), a2m)
    e = np.convolve(a2m ** 2, np.ones(4800) / 4800, mode="same")
    core = e > e.max() * 0.1
    i0, i1 = np.argmax(core), len(core) - np.argmax(core[::-1])
    a2m_core = a2m[i0 + SR // 2: i1 - SR // 2]
    print(f"  a2m rms={np.sqrt((a2m_core**2).mean()):.5f} peak={np.abs(a2m_core).max():.4f} core {len(a2m_core)/SR:.1f}s")
    _, snr_a2m = analyze_direction("android_to_mac", a2m_core, mac_noise[SR:], results)

    print("== 4. ESS impulse response, both directions ==")
    sweep, inv = ess()
    _, p = H.mac_to_android(sweep, out_name="ess_m2a.pcm")
    ess_m2a = H.load_pcm16(p)[:, 0]
    ir = np.convolve(ess_m2a, inv, mode="valid")
    pk = np.argmax(np.abs(ir))
    win = np.abs(ir[pk - 480: pk + 4800]) ** 2
    win = win / win.max()
    # delay spread: last sample above -30 dB of peak after the peak
    above = np.where(win[480:] > 1e-3)[0]
    ds_m2a = (above[-1] / SR * 1000) if len(above) else 0.0
    results["delay_spread_ms_m2a_30db"] = float(ds_m2a)
    print(f"  m2a delay spread (-30 dB) ~{ds_m2a:.2f} ms")

    rx = H.android_to_mac(sweep)
    ir2 = np.convolve(rx, inv, mode="valid")
    pk2 = np.argmax(np.abs(ir2))
    win2 = np.abs(ir2[pk2 - 480: pk2 + 4800]) ** 2
    win2 = win2 / win2.max()
    above2 = np.where(win2[480:] > 1e-3)[0]
    ds_a2m = (above2[-1] / SR * 1000) if len(above2) else 0.0
    results["delay_spread_ms_a2m_30db"] = float(ds_a2m)
    print(f"  a2m delay spread (-30 dB) ~{ds_a2m:.2f} ms")

    print("== 5. Sample clock offset (10 kHz tone, 8 s, Mac -> Android) ==")
    tone = H.chirp(10000, 10000, 8.0, amp=0.4)
    _, p = H.mac_to_android(tone, out_name="cko_m2a.pcm")
    cko = H.load_pcm16(p)[:, 0]
    # strongest 6-second window
    e = np.convolve(cko ** 2, np.ones(SR) / SR, mode="same")
    c = int(np.argmax(e))
    lo, hi = max(0, c - 3 * SR), min(len(cko), c + 3 * SR)
    seg = cko[lo:hi]
    # quadratic-interpolated FFT peak
    w = np.hanning(len(seg))
    X = np.fft.rfft(seg * w)
    k = np.argmax(np.abs(X))
    a, b, g = np.abs(X[k - 1]), np.abs(X[k]), np.abs(X[k + 1])
    d = 0.5 * (a - g) / (a - 2 * b + g)
    f_meas = (k + d) * SR / len(seg)
    ppm = (f_meas - 10000.0) / 10000.0 * 1e6
    results["clock_offset_ppm_m2a"] = float(ppm)
    print(f"  measured tone {f_meas:.3f} Hz -> clock offset {ppm:+.1f} ppm")

    with open(os.path.join(DATA, "channel.json"), "w") as fh:
        json.dump(results, fh)

    # plots
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(2, 1, figsize=(12, 8), sharex=True)
    ax[0].plot(f / 1000, snr_m2a, label="Mac->Android mic0")
    ax[0].plot(f / 1000, snr_m2a1, label="Mac->Android mic1", alpha=0.6)
    ax[0].plot(f / 1000, snr_a2m, label="Android->Mac")
    ax[0].set_ylabel("SNR (dB)"); ax[0].legend(); ax[0].grid(True)
    ax[0].axhline(10, color="gray", ls=":"); ax[0].axhline(20, color="gray", ls=":")
    ax[1].plot(f / 1000, results["mac_to_android_ch0"]["noise_psd_db"], label="Android noise PSD")
    ax[1].plot(f / 1000, results["android_to_mac"]["noise_psd_db"], label="Mac noise PSD")
    ax[1].set_xlabel("Frequency (kHz)"); ax[1].set_ylabel("Noise PSD (dB)"); ax[1].legend(); ax[1].grid(True)
    fig.savefig(os.path.join(DATA, "channel_snr.png"), dpi=110, bbox_inches="tight")
    print(f"saved {DATA}/channel.json and channel_snr.png")


if __name__ == "__main__":
    main()
