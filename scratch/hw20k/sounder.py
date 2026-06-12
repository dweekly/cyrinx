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

# MCS ladder, fastest -> most robust. The sounder ALWAYS picks the most
# aggressive tier the measured channel clears; it never refuses to transmit.
# "reposition" is advisory only, attached when even the floor tier is marginal.
#   tier: (label, mcs, rate, bits/bin, min median SNR dB, max delay-spread ms)
LADDER = [
    ("fast",   "16-QAM", "3/4", 4, 22.0, 12.0),
    ("medium", "16-QAM", "1/2", 4, 16.0, 18.0),
    ("qpsk",   "QPSK",   "1/2", 2,  9.0, 24.0),
    ("bpsk",   "BPSK",   "1/2", 1,  4.0, CP_CAP_MS),   # coherent OFDM floor
]
# Below the coherent-OFDM floor: when the delay spread exceeds the practical CP
# cap (the reverberant-desk regime), cyclic-prefix OFDM cannot equalize the
# channel at ANY MCS (measured: BPSK+32ms CP -> 0/125 at 36 ms spread). The
# correct fallback is a NON-COHERENT multi-tone FSK / DTMF waveform, which
# detects per-bin energy over symbols longer than the delay spread and is immune
# to both phase incoherence and ISI. Measured to carry ~267 bps where OFDM gave
# 0 (data/desk_noncoherent.json). This tier trades rate for the ability to link
# at all.
NONCOHERENT_FLOOR = ("mfsk", "MT-FSK", "n/a", 0)


def recommend(an, sr, margin_db=4.0):
    ds15 = an["delay_spread_ms"]["-15dB"]
    med = an["median_snr_db"]
    usable_frac = an["usable_bins"] / max(1, an["total_bins"])
    # Pick the fastest tier whose SNR and delay-spread gates are both cleared.
    # If nothing in the coherent-OFDM ladder clears (delay spread beyond the CP
    # cap, or SNR below the BPSK floor), drop to the non-coherent MFSK floor
    # rather than emit a coherent profile that cannot decode.
    chosen = None
    for tier in LADDER:
        _, _, _, _, snr_min, ds_max = tier
        if med >= snr_min and ds15 <= ds_max:
            chosen = tier
            break
    noncoherent = chosen is None
    if noncoherent:
        label, mcs, rate, bits_uniform = NONCOHERENT_FLOOR
    else:
        label, mcs, rate, bits_uniform, _, _ = chosen
    # CP to cover the -15 dB delay spread (+25% headroom). For robust tiers let
    # CP run to the full cap; only the fast tier keeps CP tight for efficiency.
    cp_ms = min(CP_CAP_MS, max(5.0, ds15 * 1.25))
    cp = int(cp_ms / 1000 * sr)
    nfft = 2048 if cp <= 1024 else 4096
    # advisory only: a better physical spot would help, but we still link
    advise_reposition = bool(noncoherent and (med < 6 or ds15 > CP_CAP_MS))  # was: label=="robust" and (med < 6 or ds15 > CP_CAP_MS))
    # Per-bin loading by SNR. Thresholds CALIBRATED against measured FEC reach
    # (not a flat margin): the rate-1/2 conv code carries QPSK down to ~5 dB
    # per-bin SNR, so the QPSK floor matches uniform-QPSK's reach while strong
    # bins upgrade to 16/64-QAM. Measured to beat uniform QPSK (16.15 vs
    # 14.74 kbps) at the 10.5 dB direct-contact channel. 1-bit (BPSK) loading is
    # intentionally NOT used here: it is buggy in mixed maps (PAPR/normalization,
    # see task), so the floor is QPSK. (data/perbin_gain.json)
    bits = {}
    if noncoherent:
        for f in an["freqs"]:
            bits[int(round(f / (sr / nfft)))] = 0
    else:
        for f, s in zip(an["freqs"], an["snr_db"]):
            b = 2 if s >= 5.0 else 0
            if s >= 15.0:
                b = 4
            if s >= 23.0:
                b = 6
            bits[int(round(f / (sr / nfft)))] = b
    return {
        "tier": label, "mcs": mcs, "rate": rate, "bits_uniform": bits_uniform,
        "bits_per_bin": bits,   # measured per-subcarrier loading (water-filling-lite)
        "noncoherent": noncoherent,
        "cp": cp, "cp_ms": round(cp_ms, 1), "nfft": nfft,
        "advise_reposition": advise_reposition,
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


# ======================================================================
# EVM-probe sounder (the corrected MCS-selection algorithm)
# ----------------------------------------------------------------------
# The repeated-pilot SNR estimate above is unreliable for MCS choice: its
# variance is pessimistic (the -10..-25 ppm clock drift between independently
# clocked devices rotates the channel between symbols and is counted as noise),
# and detrending the phase over-corrects because identical low-PAPR pilots never
# excite the channel-estimation error and PAPR-driven loudspeaker nonlinearity
# that random data incurs. Measured at the palm-rest cell: it reported 10.5 dB ->
# "QPSK" while the channel carried 16-QAM r3/4 (EVM 0.157).
#
# The fix: probe with a DATA-REPRESENTATIVE frame (a known random-QAM frame, the
# real codec, the sized CP) and read its EVM through the actual receiver. EVM is
# ~constellation-independent (a channel property), so one probe predicts every
# tier; pick the densest constellation whose EVM threshold it clears. Delay
# spread beyond the CP cap still routes to the non-coherent floor.
#
# EVM->MCS thresholds, calibrated from over-the-air measurements (2026-06-11/12):
#   EVM 0.157 -> 16-QAM r3/4 decoded (39.3 kbps); EVM ~0.17 -> 64-QAM FAILED;
#   EVM ~0.25 -> 16-QAM r1/2 decoded but r3/4 failed; EVM ~0.27 -> QPSK r1/2;
#   EVM ~1.2 (reverberant) -> nothing. (64-QAM, needing EVM < ~0.08, omitted
#   until a sub-0.1 cell is measured.) The sweep tightens these.
EVM_LADDER = [
    (0.20, "fast",   "16-QAM", "3/4", 4),
    (0.28, "medium", "16-QAM", "1/2", 4),
    (0.45, "qpsk",   "QPSK",   "1/2", 2),
]


def evm_probe(send_fn, sr=48000, nfft=2048, cp=768, f_lo=1100.0, f_hi=23000.0,
              n_sym=64):
    """Send one known 16-QAM r1/2 frame through the library codec at the given
    geometry and return its measured EVM (effective-SINR proxy). Uses n_sym=64
    so the probe accumulates the same clock-drift/ICI a real data frame does.
    Returns inf if the receiver cannot sync (treated as non-coherent)."""
    import clib
    cfg = clib.make_cfg(bits_per_bin=4, rate="1/2", n_sym=n_sym,
                        f_lo=f_lo, f_hi=f_hi, nfft=nfft, cp=cp, sr=sr)
    g = clib.geometry(cfg)
    payload = bytes((i * 31 + 7) & 0xFF for i in range(g.payload_bytes))
    wave = clib.encode(cfg, payload)
    d = clib.decode(cfg, np.asarray(send_fn(wave), dtype=np.float32))
    return float("inf") if d is None else float(d["evm"])


def recommend_evm(evm, ds15, sr):
    """Pick MCS from the probe EVM (densest tier it clears) and size the CP from
    the delay spread. Delay spread beyond the CP cap, or EVM past the QPSK
    threshold, routes to the non-coherent floor."""
    cp_ms = min(CP_CAP_MS, max(5.0, ds15 * 1.25))
    cp = int(cp_ms / 1000 * sr)
    nfft = 2048 if cp <= 1024 else 4096
    chosen = None
    if ds15 <= CP_CAP_MS:
        for thr, label, mcs, rate, bpb in EVM_LADDER:
            if evm <= thr:
                chosen = (label, mcs, rate, bpb)
                break
    noncoherent = chosen is None
    if noncoherent:
        label, mcs, rate, bpb = (*NONCOHERENT_FLOOR[:3], 0)
    else:
        label, mcs, rate, bpb = chosen
    return {
        "tier": label, "mcs": mcs, "rate": rate, "bits_uniform": bpb,
        "noncoherent": noncoherent, "cp": cp, "cp_ms": round(cp_ms, 1),
        "nfft": nfft, "advise_reposition": noncoherent,
        "probe_evm": (None if evm == float("inf") else round(evm, 3)),
        "delay_spread_ms_15": round(ds15, 1),
    }


def sound_channel_evm(send_fn, sr=48000, f_lo=1100.0, f_hi=23000.0):
    """Corrected sounder: a pilot burst sizes the CP from the delay spread, then
    an EVM probe at that CP selects the MCS. Returns (recommendation, analysis)."""
    cfg_s, wave_s = build_sounding(sr, 2048, 768, f_lo, f_hi, 2000.0, 16000.0)
    an = analyze(cfg_s, np.asarray(send_fn(wave_s), dtype=float), sr)
    ds15 = an["delay_spread_ms"]["-15dB"]
    cp_ms = min(CP_CAP_MS, max(5.0, ds15 * 1.25))
    cp = int(cp_ms / 1000 * sr)
    nfft = 2048 if cp <= 1024 else 4096
    evm = evm_probe(send_fn, sr=sr, nfft=nfft, cp=cp, f_lo=f_lo, f_hi=f_hi)
    return recommend_evm(evm, ds15, sr), an


def _selftest():
    """Offline regression net for the EVM->MCS selection, anchored on the
    over-the-air calibration points (no hardware)."""
    cases = [
        (0.157, 3.0, "fast"),     # 16-QAM r3/4 decoded (39.3 kbps)
        (0.17, 3.0, "fast"),      # 64-QAM failed; 16-QAM r3/4 is the right call
        (0.252, 3.0, "medium"),   # 16-QAM r1/2 decoded, r3/4 failed
        (0.40, 3.0, "qpsk"),      # QPSK r1/2 regime
        (0.98, 42.0, "mfsk"),     # reverberant: EVM + delay spread -> floor
        (0.15, 42.0, "mfsk"),     # low EVM but delay spread > CP cap -> floor
    ]
    ok = True
    for evm, ds, want in cases:
        got = recommend_evm(evm, ds, 48000)["tier"]
        ok &= got == want
        print(f"  EVM={evm:5.3f} ds={ds:4.1f}ms -> {got:6s} (expect {want}) "
              f"{'OK' if got == want else 'FAIL'}")
    print("SELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    import sys as _sys
    if len(_sys.argv) > 1 and _sys.argv[1] == "selftest":
        _sys.exit(_selftest())
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
    rep = "  (also: a closer/less-reverberant spot would help)" if rec['advise_reposition'] else ""
    print(f"  --> TIER: {rec['tier']}  ({rec['mcs']} r{rec['rate']}, "
          f"CP {rec['cp']} = {rec['cp_ms']} ms, NFFT {rec['nfft']}){rep}")
