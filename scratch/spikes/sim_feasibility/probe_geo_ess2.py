import sys, numpy as np
from scipy.signal import fftconvolve
sys.path.insert(0, "/Users/dew/dev/cyrinx/scratch/hw20k")
import freqresp as F
SR=48000
raw = np.fromfile("/Users/dew/dev/cyrinx/scratch/hw20k/data/geo_ess.pcm", dtype="<i2").astype(np.float64)/32768
def ess_char(f0=100.0, f1=23900.0, dur_s=4.0, amp=0.5):
    n=int(dur_s*SR); t=np.arange(n)/SR; R=np.log(f1/f0)
    w=np.sin(2*np.pi*f0*dur_s/R*(np.exp(t*R/dur_s)-1)); r=int(0.01*SR)
    env=np.ones(n); env[:r]=np.linspace(0,1,r); env[-r:]=np.linspace(1,0,r)
    s=amp*w*env; return s[::-1]*np.exp(-t*R/dur_s)
for layout, x in [("mono", raw), ("stereo ch0", raw[0::2]), ("stereo ch1", raw[1::2])]:
  for name, inv in [("char4s", ess_char()), ("freq6s", F.make_ess()[1])]:
    full = fftconvolve(x-x.mean(), inv); a=np.abs(full); pk=int(a.argmax())
    # top 5 peaks separated by >50 ms
    order=np.argsort(a)[::-1]; peaks=[]
    for i in order:
        if all(abs(i-p)>SR//20 for p in peaks): peaks.append(int(i))
        if len(peaks)==4: break
    rel=[(round((p-pk)/SR*1000,1), round(float(a[p]/a[pk]),3)) for p in peaks]
    for horizon in (0.12, 0.5):
        seg=full[pk-240: pk+int(horizon*SR)]
        print(layout, name, f"pk/med {a[pk]/np.median(a):.0f} h={horizon}s", {k: round(v,1) for k,v in F.delay_spread(seg,240).items()}, "peaks(ms,rel)", rel if horizon==0.12 else "")
