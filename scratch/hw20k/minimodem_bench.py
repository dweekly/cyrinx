#!/usr/bin/env python3
"""minimodem OTA baseline over the Mac->Pixel bench (the "defend minimodem's
honor" run, prompted by K. Mostafa's 2026-07 note that 600+ baud works
air-gapped on SOME hardware pairs).

For each baud rate: minimodem --tx renders a WAV, the harness plays it over
the Mac speakers and captures stereo on the Pixel, and minimodem --rx decodes
each mic channel. Score = byte accuracy of the decoded text vs the sent text
(order-sensitive, like the paper's ordered verification). The paper's
baseline row (measured Mac->iPhone, palm-rest geometry, 2026-06-10) had 300bd
recover 237 bps and 600+ fail; this script re-tests on the Pixel bench at any
placement, e.g. the cleanest contact cell (facedown_port_fnkey).

Usage: minimodem_bench.py <label> [bauds...]   (default bauds: 300 600 1200 2400)
"""
import os
import subprocess
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H

SR = 48000
# ~64 words of pangram-ish ASCII; long enough that partial syncs show up as
# partial accuracy rather than pass/fail noise
TEXT = ("the quick brown fox jumps over the lazy dog 0123456789 " * 12).strip()


def tx_wav(baud, path):
    subprocess.run(
        ["minimodem", "--tx", str(baud), "-R", str(SR), "-f", path],
        input=TEXT.encode(), check=True)


def rx_text(baud, path):
    r = subprocess.run(
        ["minimodem", "--rx", str(baud), "-R", str(SR), "-q", "-f", path],
        capture_output=True, timeout=120)
    return r.stdout.decode(errors="replace")


def accuracy(dec):
    n = sum(1 for a, b in zip(dec, TEXT) if a == b)
    return n / len(TEXT)


def wav_write(path, x):
    import wave
    w = wave.open(path, "wb")
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(SR)
    w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())
    w.close()


def main():
    label = sys.argv[1]
    bauds = [int(b) for b in sys.argv[2:]] or [300, 600, 1200, 2400]
    H.mac_set_output_volume(100)
    print(f"=== minimodem OTA @ {label} ({len(TEXT)} bytes/frame) ===")
    for baud in bauds:
        with tempfile.TemporaryDirectory() as td:
            txp = os.path.join(td, "tx.wav")
            tx_wav(baud, txp)
            import wave
            w = wave.open(txp)
            x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
            w.close()
            wave_f = (x / 32768.0).astype(np.float32)
            _, p = H.mac_to_android(wave_f, out_name="minimodem.pcm")
            st = H.load_pcm16(p, channels=2)
            best = (0.0, -1, "")
            for c in range(st.shape[1]):
                rxp = os.path.join(td, f"rx{c}.wav")
                wav_write(rxp, np.asarray(st[:, c], dtype=np.float64))
                dec = rx_text(baud, rxp)
                acc = accuracy(dec)
                if acc > best[0]:
                    best = (acc, c, dec)
            dur = len(wave_f) / SR
            gp = best[0] * len(TEXT) * 8 / dur
            print(f"  {baud:5d} bd: best mic{best[1]} byte-acc {best[0]*100:5.1f}% "
                  f"-> ~{gp:.0f} bps effective ({dur:.1f} s airtime)")


if __name__ == "__main__":
    main()
