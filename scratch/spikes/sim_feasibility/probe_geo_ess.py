"""Try to recover a measured IR from the retained NF-13 desk capture geo_ess.pcm.
Format and sweep parameters are not recorded; try int16 mono with two known sweep generators."""
import sys, numpy as np
from scipy.signal import fftconvolve
sys.path.insert(0, "/Users/dew/dev/cyrinx/scratch/hw20k")
import freqresp as F
SR = 48000
raw = np.fromfile("/Users/dew/dev/cyrinx/scratch/hw20k/data/geo_ess.pcm", dtype="<i2").astype(np.float64) / 32768
print("int16-mono samples", len(raw), "dur s", len(raw)/SR, "peak", np.abs(raw).max(), "rms", raw.std())
def ess_char(f0=100.0, f1=23900.0, dur_s=4.0, amp=0.5):
    n = int(dur_s*SR); t = np.arange(n)/SR; R = np.log(f1/f0)
    w = np.sin(2*np.pi*f0*dur_s/R*(np.exp(t*R/dur_s)-1)); r = int(0.01*SR)
    env = np.ones(n); env[:r] = np.linspace(0,1,r); env[-r:] = np.linspace(1,0,r)
    sweep = amp*w*env; inv = sweep[::-1]*np.exp(-t*R/dur_s); return sweep, inv
cands = {"characterize.ess(4s,100-23900)": ess_char()[1], "freqresp.make_ess(6s,60-23500)": F.make_ess()[1]}
for name, inv in cands.items():
    full = fftconvolve(raw - raw.mean(), inv)
    a = np.abs(full); pk = int(a.argmax())
    pm = a[pk] / np.median(a)
    seg = full[pk-int(0.005*SR): pk+int(0.5*SR)]
    ds = F.delay_spread(seg, int(0.005*SR))
    print(f"{name}: peak/median {pm:.0f}  delay spread from main tap {ds}")
    if name.startswith("characterize"):
        np.save(sys.argv[1] if len(sys.argv) > 1 else "/dev/null", seg)
