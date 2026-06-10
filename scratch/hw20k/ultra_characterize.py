#!/usr/bin/env python3
"""Ultrasonic-band channel characterization at 96 kHz, both directions.

Measures per-bin SNR 16-46 kHz via probe-on vs probe-off PSD, plus the
audible-band leakage of the band-limited probe (how much energy lands below
16 kHz at the receiver, which is what a human could hear).
"""
import json
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H

SR = 96000
NFFT = 2048  # 46.875 Hz bins at 96k


def welch(x):
    from scipy.signal import welch as w
    return w(x, fs=SR, nperseg=NFFT, noverlap=NFFT // 2, window="hann")


def multitone(dur_s, amp, f_lo, f_hi, seed=2):
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


def core(x, margin=SR // 2):
    e = np.convolve(x ** 2, np.ones(SR // 10) / (SR // 10), mode="same")
    m = e > e.max() * 0.1
    i0, i1 = np.argmax(m), len(m) - np.argmax(m[::-1])
    return x[i0 + margin: i1 - margin]


def report(name, rx_on, rx_off, results):
    f, p_on = welch(rx_on)
    _, p_off = welch(rx_off)
    snr = 10 * np.log10(np.maximum((p_on - p_off) / np.maximum(p_off, 1e-20), 1e-3))
    results[name] = {"freqs": f.tolist(), "snr_db": snr.tolist()}
    for lo, hi in [(16000, 18500), (18500, 21000), (21000, 23500), (23500, 26000),
                   (26000, 30000), (30000, 36000), (36000, 46000)]:
        m = (f >= lo) & (f < hi)
        print(f"    {lo/1000:5.1f}-{hi/1000:4.1f} kHz: median SNR {np.median(snr[m]):6.1f} dB")
    # audible leakage at receiver: power below 16 kHz, probe-on vs probe-off
    aud = f < 16000
    leak = 10 * np.log10(p_on[aud].sum() / max(p_off[aud].sum(), 1e-20))
    print(f"    audible-band (<16 kHz) rx power delta probe-on vs off: {leak:+.1f} dB")
    return snr


def main():
    results = {"sr": SR}
    probe = multitone(6.0, 0.6, 16000, 46000)
    H.mac_set_output_volume(100)
    H.mac_set_input_volume(70)   # ultrasonic-only TX is weak at the mic; raise gain

    print("== noise floors @96k ==")
    _, p = H.android_record(6.0, out_name="u_noise_a.pcm", sr=SR)
    a_noise = H.load_pcm16(p)
    print(f"  pixel ambient rms ch0={a_noise[:,0].std():.6f}")
    m_noise = H.mac_record(6.0, sr=SR)
    print(f"  mac ambient rms {m_noise.std():.6f}")

    print("== Mac left speaker -> Pixel, 16-46 kHz multitone ==")
    st = np.zeros((len(probe), 2), dtype=np.float32)
    st[:, 0] = probe
    _, p = H.mac_to_android(st, out_name="u_m2a.pcm", sr=SR)
    m2a = H.load_pcm16(p)
    c = core(m2a[:, 0])
    print(f"  rx rms={c.std():.5f} peak={np.abs(c).max():.4f} core={len(c)/SR:.1f}s")
    report("m2a_mic0", c, a_noise[SR:, 0], results)

    print("== Pixel -> Mac, 16-46 kHz multitone ==")
    a2m = H.android_to_mac(probe, sr=SR)
    c = core(a2m)
    print(f"  rx rms={c.std():.5f} peak={np.abs(c).max():.4f} core={len(c)/SR:.1f}s")
    report("a2m", c, m_noise[SR:], results)

    with open(os.path.join(H.DATA, "ultra_channel.json"), "w") as fh:
        json.dump(results, fh)
    print("saved data/ultra_channel.json")


if __name__ == "__main__":
    main()
