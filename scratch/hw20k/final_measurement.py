#!/usr/bin/env python3
"""Definitive bidirectional goodput measurement, decoded by the receiving device.

Mac -> Android: Mac left speaker plays 16QAM-r3/4 frames; the Pixel records
  through its own mic and demodulates ON DEVICE (BulkDemod.kt); the verified
  goodput line comes from the phone's logcat.
Android -> Mac: Pixel speaker plays; the Mac records through its own mic and
  demodulates locally (modem.py).

Future runs use an independently detected first chirp to bind decoded frames to
scheduled slots. Goodput = payload bytes that are CRC32-valid AND byte-identical
to the DetRng PRBS at that slot, divided by the full declared airtime from the
first frame's chirp to the last frame's final data sample (preambles, pilots,
FEC, CRC, missed frames, and inter-frame gaps all included). This hardening does
not retroactively strengthen the verifier used by the 2026-06-09 result.
"""

import os
import sys
import time
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H
import modem as M

N_FRAMES = 5
GAP_S = 0.25
N_SYM = 64
SEED_BASE = 1000
MIN_CHIRP_SCORE = 0.12


def build_tx(cfg):
    payloads = [M.DetRng(SEED_BASE + i).bytes(cfg.payload_bytes) for i in range(N_FRAMES)]
    waves = [M.modulate_frame(cfg, p) for p in payloads]
    gap = np.zeros(int(GAP_S * H.SR), dtype=np.float32)
    parts = []
    for i, w in enumerate(waves):
        parts.append(w)
        if i < N_FRAMES - 1:
            parts.append(gap)
    parts.append(np.zeros(H.SR // 3, dtype=np.float32))   # stream-end pad
    return np.concatenate(parts), payloads


def scheduled_slot_index(detected_start, origin, period, frame_count, tolerance):
    """Map one detected frame start to a declared slot without payload evidence."""
    if period <= 0 or frame_count <= 0 or tolerance < 0:
        raise ValueError("invalid schedule geometry")
    slot = round((detected_start - origin) / period)
    expected_start = origin + slot * period
    if not 0 <= slot < frame_count or abs(detected_start - expected_start) > tolerance:
        return None
    return slot


def ordered_verified_blocks(result, expected_payload):
    """Count unique CRC-valid block positions matching one scheduled payload."""
    if not result.get("ok") or len(expected_payload) % M.CRC_BLOCK:
        return 0
    expected_blocks = len(expected_payload) // M.CRC_BLOCK
    verified = 0
    seen_blocks = set()
    for block_index, valid, data in result.get("blocks", ()):
        if block_index in seen_blocks or not 0 <= block_index < expected_blocks:
            continue
        seen_blocks.add(block_index)
        start = block_index * M.CRC_BLOCK
        if valid and data == expected_payload[start:start + M.CRC_BLOCK]:
            verified += 1
    return verified


def score_scheduled_results(results, payloads, origin, period, tolerance):
    """Score decoded frames against fixed slots; missed/duplicate slots stay charged."""
    seen_slots = set()
    verified = 0
    attributions = []
    for detected_start, result in results:
        slot = scheduled_slot_index(
            detected_start,
            origin,
            period,
            len(payloads),
            tolerance,
        )
        block_count = 0
        if result.get("ok") and slot is not None and slot not in seen_slots:
            seen_slots.add(slot)
            block_count = ordered_verified_blocks(result, payloads[slot])
            verified += block_count
        attributions.append((slot, block_count))
    return verified, attributions


def scheduled_goodput_bps(
    verified_blocks,
    frame_samples,
    frame_count=N_FRAMES,
    gap_s=GAP_S,
    sample_rate=H.SR,
):
    """Score verified payload bytes over the full declared frame schedule."""
    if verified_blocks < 0 or frame_samples <= 0 or frame_count <= 0 or sample_rate <= 0:
        raise ValueError("invalid goodput geometry")
    span = frame_count * frame_samples / sample_rate + (frame_count - 1) * gap_s
    if span <= 0:
        raise ValueError("invalid scheduled span")
    return verified_blocks * M.CRC_BLOCK * 8 / span


def measure_m2a():
    print("=== Mac -> Android (decoded on the Pixel) ===")
    cfg = M.Config(1100, 23000, rate="3/4", n_sym=N_SYM, amp=0.7, cp=768, nfft=2048,
                   bits_per_bin={b: 4 for b in M.Config(1100, 23000, cp=768, nfft=2048).data_idx})
    print("  profile:", cfg.describe())
    tx, _ = build_tx(cfg)
    st = np.zeros((len(tx), 2), dtype=np.float32)
    st[:, 0] = tx                      # left speaker only
    H.mac_set_output_volume(100)
    dur = len(tx) / H.SR + 10.0
    H.android_record_start(dur, channels=2, out_name="final_m2a.pcm")
    time.sleep(0.7)
    H.mac_play(st, block=True)
    _, capture_path = H.android_record_finish(dur, out_name="final_m2a.pcm")
    capture = H.load_pcm16(capture_path, channels=2)
    first_frame_window = min(len(capture), 2 * H.SR)
    origin_candidates = [
        M.find_chirp(capture[:first_frame_window, channel], chirp=cfg.chirp_wave)
        for channel in range(capture.shape[1])
    ]
    schedule_origin, origin_score = max(origin_candidates, key=lambda item: item[1])
    print(f"  independent chirp origin={schedule_origin} score={origin_score:.3f}")
    if origin_score < MIN_CHIRP_SCORE:
        raise RuntimeError(
            f"independent schedule origin chirp score {origin_score:.3f} "
            f"below {MIN_CHIRP_SCORE:.3f}"
        )
    # Payload decode and strict scheduled-slot attribution run on the phone.
    # The pulled capture is used only to locate the first chirp independently
    # of decoded payload identity.
    decode_request_id = f"final-m2a-decode-{os.getpid()}-{time.time_ns()}"
    H.logcat_clear()
    H.adb("shell am start -n com.dweekly.cyrinxhil/.MainActivity "
          "--es cmd bulk_decode "
          f"--es request_id {decode_request_id} "
          "--es path /data/user/0/com.dweekly.cyrinxhil/files/final_m2a.pcm "
          "--ei channels 2 --ef f_lo 1100 --ef f_hi 23000 "
          f"--ei n_sym {N_SYM} --ei n_payloads {N_FRAMES} "
          f"--ei payload_seed_base {SEED_BASE} "
          f"--ei schedule_origin_sample {schedule_origin} "
          f"--ei slot_tolerance_samples 480 --ei gap_samples {int(GAP_S * H.SR)} "
          f"--ei trailing_pad_samples {H.SR // 3}")
    line = H.logcat_wait(
        f"bulk_decode TOTAL: request={decode_request_id} ",
        180,
    )
    if "headline_valid=true" not in line:
        raise RuntimeError(f"on-device headline decode refused: {line}")
    out = H.adb("logcat -d -s CyrinxHILAndroid").stdout
    for ln in out.splitlines():
        if "bulk_decode" in ln:
            print("  " + ln.split("CyrinxHILAndroid: ")[-1].strip())
    return line


def measure_a2m():
    print("=== Android -> Mac (decoded on the Mac) ===")
    cfg = M.Config(600, 17000, rate="3/4", n_sym=N_SYM, amp=0.7, cp=768, nfft=2048,
                   bits_per_bin={b: 4 for b in M.Config(600, 17000, cp=768, nfft=2048).data_idx})
    print("  profile:", cfg.describe())
    tx, payloads = build_tx(cfg)
    H.mac_set_input_volume(22)
    rx = H.android_to_mac(tx)
    np.save(os.path.join(H.DATA, "final_a2m_rx.npy"), rx)

    mf = np.abs(np.correlate(rx, M.CHIRP, mode="valid"))
    thr = mf.max() * 0.4
    m = mf.copy()
    starts = []
    for _ in range(N_FRAMES + 4):
        k = int(np.argmax(m))
        if m[k] < thr:
            break
        starts.append(k)
        m[max(0, k - cfg.frame_samples // 2): k + cfg.frame_samples // 2] = 0
    starts.sort()
    origin_window = min(len(rx), 2 * H.SR)
    origin, origin_score = M.find_chirp(rx[:origin_window], chirp=cfg.chirp_wave)
    print(f"  independent chirp origin={origin} score={origin_score:.3f}")
    if origin_score < MIN_CHIRP_SCORE:
        raise RuntimeError(
            f"independent schedule origin chirp score {origin_score:.3f} "
            f"below {MIN_CHIRP_SCORE:.3f}"
        )
    period = cfg.frame_samples + round(GAP_S * H.SR)
    decoded_results = []
    for s0 in starts:
        res = M.demodulate_frame(cfg, rx, start_hint=s0)
        decoded_results.append((s0, res))
    verified, attributions = score_scheduled_results(
        decoded_results,
        payloads,
        origin,
        period,
        480,
    )
    for (s0, res), (payload_index, block_count) in zip(decoded_results, attributions):
        if res.get("ok"):
            print(f"  frame@{s0/H.SR:.2f}s: payload={payload_index} "
                  f"blocks {res['blocks_ok']}/{res['blocks_total']} "
                  f"ordered={block_count} evm={res['evm_rms']:.3f}")
        else:
            print(f"  frame@{s0/H.SR:.2f}s: FAILED {res.get('err')}")
    span = N_FRAMES * cfg.frame_samples / H.SR + (N_FRAMES - 1) * GAP_S
    gp = scheduled_goodput_bps(verified, cfg.frame_samples)
    print(f"  a2m TOTAL: verified={verified} blocks ({verified*M.CRC_BLOCK} bytes) "
          f"span={span:.2f}s goodput={gp:.0f} bps")
    return gp


if __name__ == "__main__":
    m2a_line = measure_m2a()
    a2m_gp = measure_a2m()
    print()
    print("=== SUMMARY ===")
    print("  m2a (phone-decoded):", m2a_line.split("CyrinxHILAndroid: ")[-1].strip())
    print(f"  a2m (mac-decoded):   goodput={a2m_gp:.0f} bps")
