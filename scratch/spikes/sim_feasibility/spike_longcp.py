"""SIMULATED. NF-13 also measured NFFT 4096 / CP 2048 (43 ms) decoding zero at the desk
(ds10 35.8 ms). Does the simulator agree, or does it say a long guard rescues it?"""
import os, sys, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import spike_reverb as R
clib = R.clib
desk = np.load(sys.argv[1])
for nfft, cp, nsym in ((2048, 768, 64), (4096, 2048, 32)):
    cfg = clib.make_cfg(bits_per_bin=2, rate="1/2", n_sym=nsym, f_lo=300.0, f_hi=23000.0, nfft=nfft, cp=cp, amp=0.5)
    g = clib.geometry(cfg)
    payload = np.random.default_rng(7).integers(0, 256, g.payload_bytes, dtype=np.uint8).tobytes()
    wave = np.asarray(clib.encode(cfg, payload), dtype=np.float64)
    chans = [("desk candidate IR (ds10 38.3)", desk)]
    for t in (24.0, 35.8, 48.3):
        for drr in (0.0, 6.0):
            h, p, t60 = R.fit_polack(t, drr, seed=11); chans.append((f"Polack ds10={t} DRR={drr}", h))
    for name, ir in chans:
        b, evm, dt = R.run_channel(cfg, g, wave, payload, ir, 30.0, seed=3)
        print(f"[SIM] NFFT {nfft} CP {cp} ({cp/48:.1f} ms) {name:32s} -> {b}/{g.n_blocks} evm={evm:.3f}")
