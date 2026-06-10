#!/usr/bin/env python3
"""Hardware-in-the-loop acoustic harness: Mac <-> Pixel 7a.

The Android app (Apps/HIL/android) exposes two dumb primitives via adb:
  - rec_pcm:  record PCM16LE from the mic array to the app files dir
  - play_pcm: play a PCM16LE file pushed to /data/local/tmp at max media volume

All DSP/modem work happens here on the Mac so we can iterate on the *real*
over-the-air channel without rebuilding the app each cycle.

Conventions: float32 numpy arrays in [-1, 1], 48 kHz unless stated.
"""

import subprocess
import time
import sys
import os
import numpy as np

SR = 48000
PKG = "com.dweekly.cyrinxhil"
ACT = f"{PKG}/.MainActivity"
TAG = "CyrinxHILAndroid"
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
MAC_MIC = "MacBook Pro Microphone"
MAC_SPK = "MacBook Pro Speakers"


def sh(cmd, **kw):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, **kw)


def adb(args, **kw):
    return sh(f"adb {args}", **kw)


# ---------------- Android control ----------------

def android_prepare():
    """Wake device, keep screen on, bring app to front, max media volume.

    The mic returns silence (zeros) if the activity is not foreground/visible,
    so this must run before every capture. Battery saver at low charge can
    re-doze the screen, hence `stayon true` + explicit wakeup each time.
    """
    adb("shell svc power stayon true")
    adb("shell input keyevent KEYCODE_WAKEUP")
    adb("shell wm dismiss-keyguard")
    # An expanded notification shade makes the activity non-top -> mic capture
    # gets silenced (zeros) by the audio policy. Always collapse it.
    adb("shell cmd statusbar collapse")
    adb(f"shell am start -n {ACT}")
    # media volume max (stream 3); harmless if already max
    adb("shell media volume --stream 3 --set 25")
    time.sleep(0.8)
    wake = adb("shell dumpsys power").stdout
    if "mWakefulness=Awake" not in wake:
        raise RuntimeError("device failed to wake (screen off -> mic will be silenced)")


def logcat_clear():
    adb("logcat -c")


def logcat_wait(pattern, timeout_s, poll_s=0.5):
    """Wait until a logcat line containing pattern appears; return the line."""
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        out = adb(f"logcat -d -s {TAG}").stdout
        for line in out.splitlines():
            if pattern in line:
                return line
        time.sleep(poll_s)
    raise TimeoutError(f"logcat_wait: no '{pattern}' within {timeout_s}s. Tail:\n" +
                       "\n".join(adb(f'logcat -d -s {TAG}').stdout.splitlines()[-12:]))


def android_record_start(duration_s, channels=2, source="unprocessed", out_name="cap.pcm"):
    android_prepare()
    logcat_clear()
    adb(f"shell am start -n {ACT} --es cmd rec_pcm --ef duration_sec {duration_s} "
        f"--ei sample_rate_hz {SR} --ei channels {channels} --es source {source} "
        f"--es out_name {out_name}")
    line = logcat_wait("rec_pcm begin", 10)
    return line


def android_record_finish(duration_s, out_name="cap.pcm", local_path=None):
    line = logcat_wait("rec_pcm done", duration_s + 25)
    local_path = local_path or os.path.join(DATA, out_name)
    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    r = sh(f"adb exec-out run-as {PKG} cat files/{out_name} > '{local_path}'")
    if r.returncode != 0:
        raise RuntimeError(f"pull failed: {r.stderr}")
    return line, local_path


def android_record(duration_s, channels=2, source="unprocessed", out_name="cap.pcm"):
    android_record_start(duration_s, channels, source, out_name)
    return android_record_finish(duration_s, out_name)


def android_play(wave, channels=1, block=True):
    """wave: float32 mono [-1,1] or (n,2) stereo. Pushes and plays on the phone."""
    pcm = np.clip(wave, -1, 1)
    pcm16 = (pcm * 32767).astype("<i2")
    tmp = "/tmp/cyrinx_tx.pcm"
    pcm16.tofile(tmp)
    adb(f"push {tmp} /data/local/tmp/tx.pcm")
    android_prepare()
    logcat_clear()
    adb(f"shell am start -n {ACT} --es cmd play_pcm --ei sample_rate_hz {SR} "
        f"--ei channels {channels}")
    logcat_wait("play_pcm begin", 10)
    if block:
        n_frames = len(pcm16) // channels if pcm16.ndim == 1 else len(pcm16)
        logcat_wait("play_pcm done", n_frames / SR + 20)


# ---------------- Mac audio ----------------

def mac_set_output_volume(pct=100):
    sh(f"osascript -e 'set volume output volume {pct}'")


def mac_set_input_volume(pct):
    sh(f"osascript -e 'set volume input volume {pct}'")


def _sd():
    import sounddevice as sd
    sd.default.samplerate = SR
    devs = sd.query_devices()
    for i, d in enumerate(devs):
        if d["name"] == MAC_MIC:
            sd.default.device = (i, sd.default.device[1])
        if d["name"] == MAC_SPK:
            sd.default.device = (sd.default.device[0], i)
    return sd


def mac_record(duration_s):
    sd = _sd()
    x = sd.rec(int(duration_s * SR), channels=1, dtype="float32")
    sd.wait()
    return x[:, 0]


def mac_play(wave, block=True):
    sd = _sd()
    sd.play(np.asarray(wave, dtype=np.float32))
    if block:
        sd.wait()


def mac_play_and_record(wave, extra_s=1.0):
    """Full duplex on the Mac: plays wave, records simultaneously (loopback)."""
    sd = _sd()
    w = np.asarray(wave, dtype=np.float32)
    n = len(w) + int(extra_s * SR)
    rec = sd.playrec(w, channels=1, dtype="float32",
                     blocking=True, output_mapping=None)
    return rec[:, 0] if rec is not None else None


# ---------------- Cross-device captures ----------------

def mac_to_android(wave, channels_out=2, rec_channels=2, source="unprocessed",
                   pre_s=0.7, post_s=0.7, out_name="m2a.pcm"):
    """Play `wave` from Mac speakers while the phone records. Returns phone capture path."""
    dur = len(wave) / SR + pre_s + post_s + 8.0
    android_record_start(dur, channels=rec_channels, source=source, out_name=out_name)
    time.sleep(pre_s)
    mac_play(wave, block=True)
    line, path = android_record_finish(dur, out_name=out_name)
    return line, path


def android_to_mac(wave, channels=1, pre_s=0.7, post_s=0.7):
    """Play `wave` from phone speaker while the Mac records. Returns float32 mono capture."""
    import threading
    dur = (len(wave) if np.ndim(wave) == 1 else wave.shape[0]) / SR + pre_s + post_s + 2.5
    result = {}

    def rec():
        result["x"] = mac_record(dur)

    t = threading.Thread(target=rec)
    t.start()
    time.sleep(pre_s + 0.3)
    android_play(wave, channels=channels, block=True)
    t.join()
    return result["x"]


# ---------------- Helpers ----------------

def load_pcm16(path, channels=2):
    x = np.fromfile(path, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        x = x[: len(x) // channels * channels].reshape(-1, channels)
    return x


def save_wav(path, x):
    import wave as wavemod
    x = np.asarray(x)
    if x.ndim == 1:
        x = x[:, None]
    w = wavemod.open(path, "wb")
    w.setnchannels(x.shape[1])
    w.setsampwidth(2)
    w.setframerate(SR)
    w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())
    w.close()


def chirp(f0, f1, dur_s, amp=0.5, sr=SR):
    t = np.arange(int(dur_s * sr)) / sr
    ph = 2 * np.pi * (f0 * t + 0.5 * (f1 - f0) * t * t / dur_s)
    w = amp * np.sin(ph)
    # 10 ms raised-cosine ramps to avoid clicks
    r = int(0.010 * sr)
    env = np.ones(len(w))
    env[:r] = 0.5 - 0.5 * np.cos(np.pi * np.arange(r) / r)
    env[-r:] = env[:r][::-1]
    return (w * env).astype(np.float32)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "smoke"
    if cmd == "prepare":
        android_prepare()
        print("android prepared")
    elif cmd == "smoke":
        android_prepare()
        print("1) Android ambient record 2s ...")
        line, path = android_record(2.0, out_name="smoke_ambient.pcm")
        print("   ", line.strip())
        x = load_pcm16(path)
        print(f"    pulled {x.shape}, rms={np.sqrt((x**2).mean()):.6f}")
        print("2) Mac ambient record 2s ...")
        m = mac_record(2.0)
        print(f"    rms={np.sqrt((m**2).mean()):.6f}")
        print("3) Mac plays 1 kHz tone, Android records ...")
        tone = chirp(1000, 1000, 1.0, amp=0.4)
        mac_set_output_volume(100)
        line, path = mac_to_android(tone, out_name="smoke_m2a.pcm")
        x = load_pcm16(path)
        print(f"    capture rms={np.sqrt((x**2).mean()):.6f} peak={np.abs(x).max():.4f}")
        print("4) Android plays 1 kHz tone, Mac records ...")
        m = android_to_mac(tone)
        print(f"    capture rms={np.sqrt((m**2).mean()):.6f} peak={np.abs(m).max():.4f}")
        print("smoke ok")
