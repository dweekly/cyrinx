#!/usr/bin/env python3
"""Step-by-step dissection of a saved OTA capture to find where demod breaks."""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import modem as M
import harness as H

direction = sys.argv[1] if len(sys.argv) > 1 else "macloop"
profile = sys.argv[2] if len(sys.argv) > 2 else "qpsk12"
rx = np.load(os.path.join(H.DATA, f"rx_{direction}_{profile}.npy")).astype(float)
tx = np.load(os.path.join(H.DATA, f"tx_{direction}_{profile}.npy")).astype(float)

f_lo, f_hi = {"m2a": (1100.0, 23000.0), "a2m": (600.0, 17000.0),
              "macloop": (1100.0, 20000.0)}[direction]
cfg = M.Config(f_lo, f_hi, rate="1/2", n_sym=64, amp=0.7, cp=int(sys.argv[3]) if len(sys.argv)>3 else 512)

print(f"rx len {len(rx)/M.SR:.2f}s rms={rx.std():.4f} peak={np.abs(rx).max():.3f}")

# 1. chirp sync
start, q = M.find_chirp(rx)
print(f"chirp peak at {start} ({start/M.SR:.3f}s) q={q:.3f}")
mf = np.correlate(rx[max(0,start-2000):start+2000+M.CHIRP_LEN], M.CHIRP, mode="valid")
prof = np.abs(mf)
pk = np.argmax(prof)
print("mf around peak (every 16):", np.array2string(prof[pk-64:pk+64:16]/prof[pk], precision=2))

# 2. fine sync correlation
base = start + M.CHIRP_LEN + M.GUARD
ref = M.ofdm_mod_symbol(cfg, M.sync_symbol_freq(cfg, 0))
lo = max(0, base - 400)
seg = rx[lo: base + 400 + M.SYM]
mf2 = np.correlate(seg, ref, mode="valid")
off = int(np.argmax(np.abs(mf2)))
fine = lo + off
print(f"fine sync: nominal base {base}, found {fine} (delta {fine-base})")
prof2 = np.abs(mf2)
print("fine profile around max (every 4):", np.array2string(prof2[max(0,off-16):off+16:4]/prof2[off], precision=2))
base = fine

def fft_at(pos):
    return np.fft.rfft(rx[pos + M.CP: pos + M.CP + M.NFFT])

# 3. channel estimates from each sync symbol
X0 = M.sync_symbol_freq(cfg, 0)
X1 = M.sync_symbol_freq(cfg, 1)
H0 = fft_at(base)[cfg.used] / X0
H1 = fft_at(base + M.SYM)[cfg.used] / X1
print(f"|H0| median {np.median(np.abs(H0)):.3f}  |H1| median {np.median(np.abs(H1)):.3f}")
rel = np.abs(H1 - H0)**2 / np.maximum(np.abs(H0)**2, 1e-12)
print(f"sync-sym consistency: median |H1-H0|^2/|H0|^2 = {np.median(rel):.4f} "
      f"(=> est SNR {-10*np.log10(np.maximum(np.median(rel)/2,1e-9)):.1f} dB)")

# 4. cross-decode: sync1 demod with H0
Z = fft_at(base + M.SYM)[cfg.used] / np.where(np.abs(H0) > 1e-9, H0, 1e-9)
err = Z * np.conj(X1)
evm = np.sqrt(np.mean(np.abs(err - 1)**2))
print(f"sync1 decoded w/ H0: EVM={evm:.3f}")

# 5. first data symbol pilots with averaged H
Hav = (H0 + H1) / 2
used_set = {b: i for i, b in enumerate(cfg.used)}
pil_pos = np.array([used_set[b] for b in cfg.pilot_idx])
for s in [0, 1, 2, 10, 30, 62]:
    Y = fft_at(base + (2 + s) * M.SYM)
    Z = Y[cfg.used] / np.where(np.abs(Hav) > 1e-9, Hav, 1e-9)
    e = Z[pil_pos] * np.conj(cfg.pilots)
    d = e[1:] * np.conj(e[:-1])
    slope = np.angle(np.sum(d)) / (cfg.pilot_idx[1] - cfg.pilot_idx[0])
    ph0 = np.angle(np.sum(e * np.exp(-1j * slope * (cfg.pilot_idx - cfg.pilot_idx[0]))))
    corr = np.exp(-1j * (ph0 + slope * (cfg.used - cfg.pilot_idx[0])))
    Zc = Z * corr
    ep = Zc[pil_pos] * np.conj(cfg.pilots)
    evm = np.sqrt(np.mean(np.abs(ep - 1)**2))
    print(f"data sym {s:2d}: slope/bin={slope:+.4f} rad cpe={ph0:+.3f} pilotEVM={evm:.3f} "
          f"impliedTimingDrift={slope*M.NFFT/(2*np.pi):+.2f} samples")
