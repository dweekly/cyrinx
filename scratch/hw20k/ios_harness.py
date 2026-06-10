#!/usr/bin/env python3
"""iOS hardware-in-the-loop helpers: Mac <-> iPhone 17 Pro Max over USB.

iOS has no `adb am`, so the app is driven by `xcrun devicectl device process
launch --environment-variables '<JSON>'`: the app reads a CYRINX_* command from
its process environment on launch, runs one primitive (rec_pcm / play_pcm /
bulk_decode), writes any PCM + a result.json into its Documents container, and
we pull those back with `xcrun devicectl device copy from --domain-type
appDataContainer`. See Apps/HIL/iOS/App/HILAutomation.swift.

All modem DSP stays on the Mac and reuses modem.py, identical to the Android
path (harness.py). Mirrors mac_to_android / android_to_mac.
"""

import json
import os
import subprocess
import threading
import time

import numpy as np

import harness as H  # reuse Mac audio + DSP-adjacent helpers
import modem as M

SR = 48000
DEV = "19F2503C-9C10-56B6-B662-5C9F1BA7E3FB"  # iPhone 17 Pro Max (devicectl)
BID = "com.dweekly.cyrinx.hil.ios"
DATA = H.DATA


def sh(cmd, timeout=180):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)


def dc(args, timeout=180):
    return sh(f"xcrun devicectl {args}", timeout=timeout)


# ---------------- iOS device control ----------------

def ios_push(local_path, dest_name):
    """Push a local file into the app's Documents container."""
    r = dc(f"device copy to --device {DEV} --domain-type appDataContainer "
           f"--domain-identifier {BID} --source '{local_path}' "
           f"--destination Documents/{dest_name}")
    if r.returncode != 0:
        raise RuntimeError(f"ios_push failed: {r.stderr or r.stdout}")


def ios_pull(src_name, local_path):
    if os.path.exists(local_path):
        os.remove(local_path)
    r = dc(f"device copy from --device {DEV} --domain-type appDataContainer "
           f"--domain-identifier {BID} --source Documents/{src_name} "
           f"--destination '{local_path}'")
    if r.returncode != 0:
        raise RuntimeError(f"ios_pull failed: {r.stderr or r.stdout}")
    return local_path


def ios_launch(env: dict, timeout=30):
    """Launch the app with a CYRINX_* environment dictionary. Requires the
    iPhone to be awake/unlocked; iOS denies launches on a locked device and
    silences mic capture / audio render when the app is not foreground (the
    analog of Android's top-visibility gotcha)."""
    payload = json.dumps(env)
    r = dc(f"device process launch --device {DEV} --terminate-existing "
           f"--environment-variables '{payload}' {BID}", timeout=timeout)
    if "Launched application" not in (r.stdout + r.stderr):
        if "Locked" in (r.stdout + r.stderr):
            raise RuntimeError("iPhone is locked — wake & unlock it (audio HIL "
                               "needs the app foreground/unlocked).")
        raise RuntimeError(f"ios_launch failed: {r.stderr or r.stdout}")


def ios_wait_result(min_log_contains=None, timeout_s=60, poll_s=1.0):
    """Poll result.json until present and `ok`, returning the parsed dict."""
    t0 = time.time()
    last_err = None
    local = os.path.join(DATA, "ios_result.json")
    while time.time() - t0 < timeout_s:
        try:
            ios_pull("result.json", local)
            with open(local) as fh:
                res = json.load(fh)
            if res.get("ok"):
                if (min_log_contains is None or
                        any(min_log_contains in ln for ln in res.get("log", []))):
                    return res
        except Exception as e:  # noqa: BLE001
            last_err = e
        time.sleep(poll_s)
    raise TimeoutError(f"ios_wait_result timed out ({timeout_s}s); last={last_err}")


def ios_clear_result():
    """Remove a stale result.json so the next poll sees only the fresh one."""
    # devicectl has no delete; push an empty placeholder that is not `ok`.
    tmp = os.path.join(DATA, "_empty_result.json")
    with open(tmp, "w") as fh:
        json.dump({"ok": False, "stale": True}, fh)
    try:
        ios_push(tmp, "result.json")
    except Exception:  # noqa: BLE001
        pass


# ---------------- recording / playback primitives ----------------

def ios_record_start(duration_s, channels=1, sr=SR, out_name="cap.pcm"):
    ios_clear_result()
    ios_launch({
        "CYRINX_CMD": "rec_pcm",
        "CYRINX_DURATION": str(duration_s),
        "CYRINX_SR": str(sr),
        "CYRINX_CH": str(channels),
        "CYRINX_OUT": out_name,
    })


def ios_record_finish(out_name="cap.pcm", local_path=None, timeout_s=120):
    res = ios_wait_result(min_log_contains="rec_pcm done", timeout_s=timeout_s)
    local_path = local_path or os.path.join(DATA, out_name)
    ios_pull(out_name, local_path)
    return res, local_path


def ios_play(wave, channels=1, sr=SR, block=True):
    """Push a PCM16LE waveform and play it out the iPhone speaker."""
    pcm = np.clip(np.asarray(wave), -1, 1)
    pcm16 = (pcm * 32767).astype("<i2")
    tmp = "/tmp/cyrinx_ios_tx.pcm"
    pcm16.tofile(tmp)
    ios_push(tmp, "tx.pcm")
    ios_clear_result()
    ios_launch({
        "CYRINX_CMD": "play_pcm",
        "CYRINX_SR": str(sr),
        "CYRINX_CH": str(channels),
        "CYRINX_IN": "tx.pcm",
    })
    if block:
        n_frames = len(pcm16) // channels if pcm16.ndim == 1 else len(pcm16)
        ios_wait_result(min_log_contains="play_pcm done",
                        timeout_s=int(n_frames / sr) + 60)


def ios_bulk_decode(out_name="cap.pcm", channels=1, f_lo=1100.0, f_hi=23000.0,
                    n_sym=64, n_payloads=5, seed_base=1000, timeout_s=180):
    """Decode an already-captured PCM ON the iPhone and return the result dict."""
    ios_clear_result()
    ios_launch({
        "CYRINX_CMD": "bulk_decode",
        "CYRINX_IN": out_name,
        "CYRINX_CH": str(channels),
        "CYRINX_FLO": str(int(f_lo)),
        "CYRINX_FHI": str(int(f_hi)),
        "CYRINX_NSYM": str(n_sym),
        "CYRINX_NPAYLOADS": str(n_payloads),
        "CYRINX_SEEDBASE": str(seed_base),
    })
    return ios_wait_result(min_log_contains="bulk_decode TOTAL", timeout_s=timeout_s)


# ---------------- cross-device captures (mirror harness.py) ----------------

def mac_to_ios(wave, rec_channels=1, sr=SR, pre_s=0.7, post_s=0.7, out_name="m2i.pcm"):
    """Play `wave` from the Mac LEFT speaker while the iPhone records.

    Left-speaker-only transmit per ACOUSTIC_BULK_PHY.md defect #2: the device
    sits by the left palm rest; the right speaker arrives weaker, phase-
    decorrelated, and wrecks the composite EVM. `wave` is mono; we route it to
    Mac channel 0 only. Returns (result_dict, local_pcm_path)."""
    dur = len(wave) / sr + pre_s + post_s + 8.0
    ios_record_start(dur, channels=rec_channels, sr=sr, out_name=out_name)
    time.sleep(pre_s)
    st = np.zeros((len(wave), 2), dtype=np.float32)
    st[:, 0] = wave  # left only
    H.mac_set_output_volume(100)
    H.mac_play(st, block=True, sr=sr)
    return ios_record_finish(out_name=out_name, local_path=os.path.join(DATA, out_name),
                             timeout_s=int(dur) + 60)


def ios_to_mac(wave, channels=1, sr=SR, pre_s=0.7, post_s=0.7):
    """Play `wave` from the iPhone speaker while the Mac records. Returns mono."""
    dur = (len(wave) if np.ndim(wave) == 1 else wave.shape[0]) / sr + pre_s + post_s + 2.5
    result = {}

    def rec():
        result["x"] = H.mac_record(dur, sr=sr)

    t = threading.Thread(target=rec)
    t.start()
    time.sleep(pre_s + 0.3)
    ios_play(wave, channels=channels, sr=sr, block=True)
    t.join()
    return result["x"]


if __name__ == "__main__":
    # Smoke test: confirm device control + audio primitives respond.
    print("iOS HIL smoke: noop launch ...")
    ios_clear_result()
    ios_launch({"CYRINX_CMD": "noop"})
    res = ios_wait_result(timeout_s=20)
    print("  noop ok:", res.get("ok"), res.get("log", [])[-1:])
    print("iOS records 2 s ambient ...")
    ios_record_start(2.0, channels=1, out_name="smoke_ios.pcm")
    res, path = ios_record_finish(out_name="smoke_ios.pcm")
    x = np.fromfile(path, dtype="<i2").astype(np.float32) / 32768.0
    print(f"  pulled {x.shape} rms={np.sqrt((x**2).mean()):.6f} "
          f"hw_rate={res.get('hw_rate')}")
    print("smoke ok")
