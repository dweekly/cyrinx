"""Candidate reconstruction of the NF-13 desk IR from geo_ess.pcm, ASSUMING int16
stereo layout and freqresp.make_ess() defaults (6 s, 60-23500 Hz). Neither
assumption is recorded with the capture; the file predates freqresp.py."""
import sys, numpy as np
from scipy.signal import fftconvolve
sys.path.insert(0, "/Users/dew/dev/cyrinx/scratch/hw20k")
import freqresp as F
raw = np.fromfile("/Users/dew/dev/cyrinx/scratch/hw20k/data/geo_ess.pcm", dtype="<i2").astype(np.float64)/32768
x = raw[0::2]
full = fftconvolve(x - x.mean(), F.make_ess()[1]); pk = int(np.abs(full).argmax())
seg = full[pk-240: pk+int(0.120*48000)]
seg = seg/np.sqrt(np.sum(seg**2))
print(F.delay_spread(seg, 240)); np.save(sys.argv[1], seg)
