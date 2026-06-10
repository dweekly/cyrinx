#!/usr/bin/env python3
"""Adaptive channel sounding -> recommended link configuration.

The core value proposition of the library: before committing to a modulation,
sound the actual channel and pick the best feasible config for THIS environment
rather than a fixed palm-rest-tuned profile (which fails at, e.g., a reverberant
desk geometry, see data/geometry.jsonl).

A sounding burst = chirp preamble + GUARD + S known full-band QPSK OFDM symbols.
From the capture the receiver estimates:
  - delay spread (Schroeder EDC on the channel impulse response),
  - per-bin SNR (variance across the repeated known symbols),
  - sample-clock offset (pilot/symbol phase ramp over the burst).
It then recommends:
  - CP length (cover the -15 dB delay-spread point, clamped to a practical cap),
  - per-bin bit loading (off/QPSK/16/64-QAM by SNR with margin),
  - code rate (by median usable SNR),
  - a FEASIBILITY verdict (fast / robust-only / reposition).

This module exposes sound_channel(send_fn, capture) -> recommendation dict.
`send_fn(wave)` plays a mono waveform and returns the receiver's mono capture
(any OTA path: mac_to_ios, mac_to_android, ios_to_mac, ...).
"""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import modem as M

SOUND_NSYM = 8          # known QPSK symbols for SNR + clock estimation
# practical CP ceiling: beyond this, OFDM overhead is not worth it and a
# different waveform (single-carrier DFE / OTFS) or repositioning is indicated.
CP_CAP_MS = 32.0


def build_sounding(sr, nfft, cp, f_lo, f_hi, chirp_f0, chirp_f1, amp=0.7):
    cfg = M.Config(f_lo, f_hi, nfft=nfft, cp=cp, sr=sr, chirp_f0=chirp_f0,
                   chirp_f1=chirp_f1, n_sym=SOUND_NSYM, amp=amp)
    # all sounding symbols use the known full-band sync sequence #0
    sym = M.ofdm_mod_symbol(cfg, M.sync_symbol_freq(cfg, 0))
    body = np.tile(sym, SOUND_NSYM)
    body = body / np.abs(body).max() * amp
    wave = np.concatenate([cfg.chirp_wave * amp, np.zeros(M.GUARD), body,
                           np.zeros(sr // 3)]).astype(np.float32)
    return cfg, wave


def analyze(cfg, rx, sr):
    X = M.sync_symbol_freq(cfg, 0)
    start, q = M.find_chirp(rx, chirp=cfg.chirp_wave)
    base = start + len(cfg.chirp_wave) + M.GUARD
    ref = M.ofdm_mod_symbol(cfg, X)
    lo = max(0, base - 400)
    mf = np.correlate(rx[lo: base + 400 + cfg.sym], ref, mode="valid")
    base = lo + int(np.argmax(np.abs(mf)))

    def H_at(i):
        seg = rx[base + i * cfg.sym + cfg.cp: base + i * cfg.sym + cfg.cp + cfg.nfft]
        if len(seg) < cfg.nfft:
            return None
        return np.fft.rfft(seg)[cfg.used] / X

    Hs = [H_at(i) for i in range(SOUND_NSYM)]
    Hs = np.stack([h for h in Hs if h is not None])
    Hm = Hs.mean(axis=0)
    # per-bin SNR: signal = |Hm|^2, noise = variance across symbols
    nv = np.var(Hs, axis=0) + 1e-12
    snr_lin = (np.abs(Hm) ** 2) / nv
    snr_db = 10 * np.log10(np.maximum(snr_lin, 1e-3))

    # delay spread from the channel impulse response (full-band irfft of Hm)
    Hfull = np.zeros(cfg.nfft // 2 + 1, dtype=complex)
    Hfull[cfg.used] = Hm
    h = np.fft.irfft(Hfull, cfg.nfft)
    e = np.abs(h) ** 2
    pk = int(np.argmax(e))
    roll = np.roll(e, -pk + 16)        # align main tap near index 16
    edc = np.cumsum(roll[::-1])[::-1]
    edc = edc / edc.max()
    edc_db = 10 * np.log10(edc + 1e-12)
    def t_at(db):
        idx = np.where(edc_db < db)[0]
        return ((idx[0] - 16) / sr * 1000) if len(idx) else (cfg.nfft / sr * 1000)
    ds = {"-10dB": t_at(-10), "-15dB": t_at(-15), "-20dB": t_at(-20)}

    # clock offset: phase ramp of the per-symbol channel vs symbol index,
    # at the strongest bin (proxy; full impl would use comb pilots)
    k = int(np.argmax(np.abs(Hm)))
    ph = np.unwrap(np.angle(Hs[:, k]))
    slope = np.polyfit(np.arange(len(ph)), ph, 1)[0]  # rad per symbol
    ppm = slope / (2 * np.pi) / cfg.sym * 1e6          # crude
    return {
        "chirp_q": float(q), "snr_db": snr_db, "freqs": cfg.used * cfg.bin_hz,
        "delay_spread_ms": ds, "clock_ppm_est": float(ppm),
        "median_snr_db": float(np.median(snr_db)),
        "usable_bins": int((snr_db > 6).sum()), "total_bins": len(cfg.used),
    }


LOAD = [(27.0, 6), (20.0, 4), (12.0, 2), (6.0, 1)]   # SNR-margin -> bits


def recommend(an, sr, margin_db=4.0):
    ds15 = an["delay_spread_ms"]["-15dB"]
    med = an["median_snr_db"]
    usable_frac = an["usable_bins"] / max(1, an["total_bins"])
    # CP to cover the -15 dB delay spread, +25% headroom, clamped
    cp_ms = min(CP_CAP_MS, max(5.0, ds15 * 1.25))
    cp = int(cp_ms / 1000 * sr)
    nfft = 2048 if cp <= 1024 else 4096
    # feasibility
    if ds15 > CP_CAP_MS or usable_frac < 0.3 or med < 4:
        verdict = "reposition"
        mcs, rate = "robust", "1/2"
    elif med >= 22 and ds15 < 12 and usable_frac > 0.7:
        verdict = "fast"
        mcs, rate = "16-QAM", "3/4"
    else:
        verdict = "robust-only"
        mcs, rate = "QPSK", "1/2"
    # per-bin loading by SNR
    bits = {}
    for f, s in zip(an["freqs"], an["snr_db"]):
        b = 0
        for th, nb in LOAD:
            if s - margin_db >= th:
                b = nb; break
        bits[int(round(f / (sr / nfft)))] = b
    return {
        "verdict": verdict, "mcs": mcs, "rate": rate,
        "cp": cp, "cp_ms": round(cp_ms, 1), "nfft": nfft,
        "median_snr_db": round(med, 1),
        "delay_spread_ms_15": round(ds15, 1),
        "usable_bin_frac": round(usable_frac, 2),
        "clock_ppm_est": round(an["clock_ppm_est"], 1),
    }


def sound_channel(send_fn, sr=48000, nfft=2048, cp=768, f_lo=1100.0, f_hi=23000.0,
                  chirp_f0=2000.0, chirp_f1=16000.0, amp=0.7):
    """Play a sounding burst via send_fn, analyze the capture, return recommendation."""
    cfg, wave = build_sounding(sr, nfft, cp, f_lo, f_hi, chirp_f0, chirp_f1, amp)
    rx = np.asarray(send_fn(wave), dtype=float)
    an = analyze(cfg, rx, sr)
    rec = recommend(an, sr)
    return rec, an


if __name__ == "__main__":
    # Demo: sound the Mac->iPhone channel at the current physical position.
    import harness as H
    import ios_harness as I
    H.mac_set_output_volume(100)

    def send(wave):
        _, path = I.mac_to_ios(wave, out_name="sound.pcm", sr=48000)
        return np.fromfile(path, dtype="<i2").astype(np.float32) / 32768.0

    rec, an = sound_channel(send)
    print("=== channel sounding (Mac -> iPhone, current position) ===")
    print(f"  chirp_q={an['chirp_q']:.2f}  median SNR={rec['median_snr_db']} dB  "
          f"usable bins={rec['usable_bin_frac']*100:.0f}%")
    print(f"  delay spread -15dB={rec['delay_spread_ms_15']} ms  "
          f"clock~{rec['clock_ppm_est']} ppm")
    print(f"  --> VERDICT: {rec['verdict']}  (recommend {rec['mcs']} r{rec['rate']}, "
          f"CP {rec['cp']} = {rec['cp_ms']} ms, NFFT {rec['nfft']})")
