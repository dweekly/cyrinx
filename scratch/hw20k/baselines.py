#!/usr/bin/env python3
"""Same-hardware baselines: ggwave and minimodem over the IDENTICAL Mac->iPhone
OTA path and geometry as Cyrinx, scored under the same goodput definition
(correctly received payload bytes / audio airtime).

This answers the review's "comparison to prior systems is under-supported":
instead of literature numbers, we run the deployed tools on this exact channel
(iPhone 17 Pro Max on a cloth on the MacBook palm rest, Mac left speaker).

Each tool: generate its TX audio for a known payload, (a) measure its clean
file-to-file rate, then (b) play it Mac left speaker -> iPhone mic, pull the
capture, decode with the tool's own decoder, and report OTA goodput + whether
the payload was recovered byte-exact.

Usage: baselines.py [ggwave|minimodem|all]
Writes data/baselines.json.
"""
import json
import os
import subprocess
import sys
import wave as wavemod
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H
import ios_harness as I

SR = 48000
GG = "/tmp/ggb/bin/ggwave-to-file"
GGD = "/tmp/ggb/bin/ggwave-from-file"
DATA = H.DATA
# 32-byte ASCII payload (ggwave caps at 64 B/msg; keep within one message)
PAYLOAD = b"Cyrinx baseline payload 32 bytes"
assert len(PAYLOAD) == 32


def wav_read(path):
    w = wavemod.open(path)
    n = w.getnframes()
    x = np.frombuffer(w.readframes(n), dtype="<i2").astype(np.float32) / 32768.0
    sr = w.getframerate()
    w.close()
    return x, sr


def wav_write(path, x, sr=SR):
    w = wavemod.open(path, "wb")
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
    w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())
    w.close()


def trim_silence(x, frac=0.02):
    e = np.convolve(x ** 2, np.ones(480) / 480, mode="same")
    m = e > e.max() * frac
    if not m.any():
        return x
    return x[np.argmax(m): len(m) - np.argmax(m[::-1])]


def ota_m2i(x, sr=SR, name="base.pcm"):
    """Play x (mono) Mac left speaker -> iPhone mic; return pulled mono capture."""
    res, path = I.mac_to_ios(np.asarray(x, np.float32), sr=sr, out_name=name)
    cap = np.fromfile(path, dtype="<i2").astype(np.float32) / 32768.0
    return cap


def run_ggwave(results):
    out = {}
    for pid, label in [(1, "Fast"), (2, "Fastest"), (5, "U-Fastest")]:
        wav = f"/tmp/gg_p{pid}.wav"
        subprocess.run([GG, f"-f{wav}", f"-p{pid}", f"-l{len(PAYLOAD)}"],
                       input=PAYLOAD, capture_output=True)
        x, _ = wav_read(wav)
        xt = trim_silence(x)
        dur = len(xt) / SR
        digital_bps = len(PAYLOAD) * 8 / dur
        # OTA
        cap = ota_m2i(x, name=f"gg_p{pid}.pcm")
        wav_write(f"/tmp/gg_p{pid}_rx.wav", cap)
        dec = subprocess.run([GGD, f"/tmp/gg_p{pid}_rx.wav", f"-l{len(PAYLOAD)}"],
                             capture_output=True, text=True)
        ok = False
        for ln in dec.stdout.splitlines():
            if "Decoded message" in ln and "'" in ln:
                if ln.split("'")[1].encode() == PAYLOAD:
                    ok = True
                    break
        ota_bps = (len(PAYLOAD) * 8 / dur) if ok else 0.0
        out[f"p{pid}_{label}"] = {
            "digital_bps": round(digital_bps, 1),
            "msg_airtime_s": round(dur, 3),
            "ota_recovered": ok,
            "ota_goodput_bps": round(ota_bps, 1),
        }
        print(f"  ggwave {label}: digital {digital_bps:.0f} bps, "
              f"airtime {dur:.2f}s, OTA {'OK' if ok else 'FAIL'} "
              f"({ota_bps:.0f} bps)")
    results["ggwave"] = out


def run_minimodem(results):
    out = {}
    for baud in [300, 600, 1200, 2400]:
        wav = f"/tmp/mm_{baud}.wav"
        subprocess.run(["minimodem", "--tx", str(baud), "--file", wav],
                       input=PAYLOAD, capture_output=True)
        x, _ = wav_read(wav)
        xt = trim_silence(x)
        dur = len(xt) / SR
        digital_bps = len(PAYLOAD) * 8 / dur
        cap = ota_m2i(x, name=f"mm_{baud}.pcm")
        wav_write(f"/tmp/mm_{baud}_rx.wav", cap)
        dec = subprocess.run(["minimodem", "--rx", str(baud), "--file",
                              f"/tmp/mm_{baud}_rx.wav"], capture_output=True)
        body = dec.stdout  # bytes; OTA garbage need not be valid UTF-8
        err = dec.stderr.decode("utf-8", "replace")
        ndata = 0
        for ln in err.splitlines():
            if "ndata=" in ln:
                try:
                    ndata = int(ln.split("ndata=")[1].split()[0])
                except Exception:
                    pass
        got = PAYLOAD in body
        ota_bps = (len(PAYLOAD) * 8 / dur) if got else 0.0
        out[f"baud{baud}"] = {
            "digital_bps": round(digital_bps, 1),
            "msg_airtime_s": round(dur, 3),
            "ota_recovered": got, "ota_bytes_seen": ndata,
            "ota_goodput_bps": round(ota_bps, 1),
        }
        print(f"  minimodem {baud}bd: digital {digital_bps:.0f} bps, "
              f"airtime {dur:.2f}s, OTA {'OK' if got else f'FAIL (ndata={ndata})'} "
              f"({ota_bps:.0f} bps)")
    results["minimodem"] = out


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    H.mac_set_output_volume(100)
    results = {"payload_len": len(PAYLOAD), "geometry": "iphone17pm on cloth, Mac palm rest",
               "path": "Mac left speaker -> iPhone mic (m2i)",
               "cyrinx_m2i_kbps": 36.47}
    if which in ("ggwave", "all"):
        print("== ggwave ==")
        run_ggwave(results)
    if which in ("minimodem", "all"):
        print("== minimodem ==")
        run_minimodem(results)
    with open(os.path.join(DATA, "baselines.json"), "w") as fh:
        json.dump(results, fh, indent=2)
    print(f"saved {DATA}/baselines.json")
