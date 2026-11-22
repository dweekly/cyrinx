"""Reconstruct the burst-ACK file trials from both microphones, entirely offline."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))
import clock_offset
import clib

CHUNKS = 276
FILE_BYTES = 65536


def require(condition, message):
    if not condition:
        raise ValueError(message)


def build(folder):
    core = ROOT / "Sources/CCyrinx"
    for name in ["split", "phone_rx"]:
        subprocess.run(["clang", "-std=c11", "-O2", "-Dkiss_fft_scalar=double", "-I", HERE.parent,
                        "-I", core / "include", "-I", core / "kissfft", HERE / (name + ".c"),
                        core / "cyrinx_fft.c", core / "kissfft/kiss_fft.c", core / "kissfft/kiss_fftr.c",
                        "-lm", "-o", folder / name], check=True)
    lib = folder / "clock.dylib"
    subprocess.run(["bash", HERE.parent / "build_clock_dylib.sh", lib], check=True)
    os.environ["CYRINX_CLOCK_CODEC_PATH"] = os.environ["CYRINX_BULK_CODEC_PATH"] = str(lib)
    # clib fixed its library path when it was imported above, before this build existed. Point it
    # here so a checkout with no locally built scratch/hw20k library runs, and so the pilot fit
    # set below applies to the codec that clib calls.
    require(clib._DEFAULT_CODEC is None, "clib loaded a codec before the research build")
    clib._DEFAULT_CODEC_PATH = str(lib)
    require(Path(clib.loaded_library_path()) == lib.resolve(), "clib loaded a different codec")
    clock_offset.set_pilot_fit(clock_offset.FIT_COHERENT)


def ack_bitmap(result, token):
    bitmaps = set()
    if result:
        for i, valid in enumerate(result["block_valid"]):
            block = result["payload"][i * 256:(i + 1) * 256]
            if valid and block[:12] == b"AK01" + token:
                bitmaps.add(block[12:12 + (CHUNKS + 7) // 8])
    if len(bitmaps) != 1:
        return None
    bitmap = bitmaps.pop()
    return {i for i in range(CHUNKS) if bitmap[i // 8] & (1 << (i % 8))}


def aligned_ack(samples, chirp, frame_samples):
    from scipy.signal import fftconvolve

    peak = int(np.argmax(np.abs(fftconvolve(samples, chirp[::-1], mode="valid"))))
    start, stop = peak - 3072, peak + frame_samples + 8000
    return samples[start:stop] if start >= 0 and stop <= len(samples) else None


def replay(trial, tools, cfg):
    ledger = json.loads((trial / "transfer.json").read_text())
    policy = ledger.get("ack_policy", "single")
    require(policy in ("single", "repeat-mrc-v1"), f"{trial}: unknown ACK policy")
    numbers = [turn["turn"] for turn in ledger["turns"]]
    # A missing or repeated turn would let recordings that were never adjacent be combined as
    # consecutive failed replies.
    require(numbers == list(range(1, len(numbers) + 1)), f"{trial}: turns are not numbered 1 to {len(numbers)}")
    # The policy decides whether the live acknowledgment method is checked, so a ledger must not
    # carry that record under a policy that skips it, or lack it under one that needs it.
    for turn in ledger["turns"]:
        recorded_method = {"ack_method", "combine_attempted"} & turn.keys()
        require(len(recorded_method) == (2 if policy == "repeat-mrc-v1" else 0),
                f"{trial} turn {turn['turn']}: acknowledgment record does not fit policy {policy}")
    token = bytes.fromhex(ledger["token"])
    source = (trial / "sent.bin").read_bytes()
    digest = hashlib.sha256(source).hexdigest()
    require(len(source) == FILE_BYTES and digest == ledger["source_sha256"],
            f"{trial}: source file digest mismatch")
    known = set()
    # Only the preamble is known to this receiver. The source file and the
    # reconstructed bitmap below are reserved for independent verification.
    geometry = clib.geometry(cfg)
    chirp = clib.encode(cfg, bytes(geometry.payload_bytes))[:4096].astype(float)
    previous_failure = None
    combined_attempts = []
    changed_controls = []
    prior_window = prior_bitmap = None
    frame_count = tries = acks = gaps = 0
    with tempfile.TemporaryDirectory(prefix="cyrinx-file-") as temp:
        work = Path(temp)
        for turn in ledger["turns"]:
            label = f"{trial.name} turn {turn['turn']}"
            frames, bits = turn["frames"], turn["bits"]
            slots = 39 if bits == 4 else 19
            expected = [i for i in range(CHUNKS) if i not in known][:slots * ledger["batch"]]
            require(bits == (4 if ledger["phone"] == "pixel" else 2) and expected == turn["indices"]
                    and frames == (len(expected) + slots - 1) // slots, f"{label}: unexpected schedule")
            raw = trial / f"cy_{ledger['token']}_{turn['turn']}.pcm"
            with raw.open("rb") as file:
                subprocess.run([tools / "split", str(frames)], stdin=file, cwd=work, check=True)
            for i in range(frames):
                with (work / f"window-{i}.pcm").open("rb") as file:
                    subprocess.run([tools / "phone_rx", str(bits), "-1"], cwd=work, stdin=file, check=True)
            generated = np.fromfile(work / "ack.pcm", "<i2").astype("f4") / 32768
            local = ack_bitmap(clock_offset.decode(cfg, generated), token)
            capture = np.load(trial / f"ack-{turn['turn']}.npy", allow_pickle=False).astype(float)
            recorded = ack_bitmap(clock_offset.decode(cfg, capture), token)
            current = aligned_ack(capture, chirp, geometry.frame_samples)
            # Challenge the combining assumption with adjacent recordings where
            # the independently reconstructed phone state actually changed.
            if (prior_window is not None and current is not None and prior_bitmap is not None
                    and local is not None and local != prior_bitmap):
                test_result = clock_offset.decode2(cfg, prior_window, current)
                control = ack_bitmap(test_result, token)
                changed_controls.append(dict(turn=turn["turn"], decoded=control is not None,
                                             invented_chunks=sorted(control - local) if control is not None else [],
                                             equals_current=control == local, equals_previous=control == prior_bitmap))
            prior_window, prior_bitmap = current, local
            method = "single" if recorded is not None else "miss"
            attempted = False
            if recorded is None:
                if current is not None and previous_failure is not None and previous_failure[1] == expected:
                    attempted = True
                    decoded = clock_offset.decode2(cfg, previous_failure[0], current)
                    candidate = ack_bitmap(decoded, token)
                    accepted = candidate is not None and known <= candidate <= known.union(expected)
                    combined_attempts.append(dict(turn=turn["turn"], elapsed_s=turn["elapsed"],
                                                  accepted=accepted, matches_receiver=accepted and candidate == local,
                                                  confirms_file=accepted and len(candidate) == CHUNKS))
                    if accepted and policy == "repeat-mrc-v1":
                        recorded, method = candidate, "combined"
                previous_failure = (current, expected) if recorded is None and current is not None else None
            else:
                previous_failure = None
            if policy == "repeat-mrc-v1":
                require(method == turn["ack_method"] and attempted == turn["combine_attempted"],
                        f"{label}: live ACK method differs from replay")
            require((recorded is not None) == turn["ack_ok"], f"{label}: ACK availability changed")
            prior = len(known)
            if recorded is not None:
                require(recorded == local and known <= recorded, f"{label}: acoustic bitmap mismatch")
                known = recorded
                acks += 1
            require(len(known) == turn["received"] and len(known) - prior == turn["new"],
                    f"{label}: sender progress mismatch")
            frame_count += frames
            gaps += frames - 1
            tries += len(expected)
        reconstructed = (work / "received.bin").read_bytes()
        require(hashlib.sha256(reconstructed).hexdigest() == ledger["receiver_sha256"],
                f"{trial}: host replay differs from the handset file")
        require((reconstructed == source) == ledger["hash_match"]
                and (len(known) == CHUNKS) == ledger["complete_ack"], f"{trial}: completion status mismatch")
        for index in known:
            block = slice(index * 238, min((index + 1) * 238, len(source)))
            require(reconstructed[block] == source[block], f"{trial}: acknowledged chunk {index} is wrong")
    elapsed = ledger["elapsed_s"]
    turns = len(ledger["turns"])
    complete = ledger["complete_ack"] and ledger["hash_match"]
    rate = 8 * len(source) / elapsed / 1000 if complete else None
    require((rate is None and ledger["kbps"] is None)
            or (complete and abs(rate - ledger["kbps"]) < 1e-9), f"{trial}: timing arithmetic mismatch")
    return dict(phone=ledger["phone"], batch=ledger["batch"], file_sha256=digest, seconds=elapsed,
                file_kbps=rate, frames=frame_count, turns=turns, acoustic_acks=acks, sent_chunks=tries,
                waveform_seconds=frame_count * 4.224 + turns * 2.688, interframe_seconds=gaps * 0.25, combined_ack_replay=combined_attempts, changed_bitmap_controls=changed_controls)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--batches", nargs="+", type=int, choices=(1, 2, 4), default=[1, 2, 4])
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    rows = []
    with tempfile.TemporaryDirectory(prefix="cyrinx-rx-") as temp:
        tools = Path(temp)
        build(tools)
        cfg = clib.make_cfg(f_lo=300, f_hi=11000, bits_per_bin=2, rate="1/2", nfft=16384, cp=8192, n_sym=3)
        for phone in ("pixel", "moto"):
            for batch in args.batches:
                trial = args.archive / f"{phone}-{batch}"
                row = replay(trial, tools, cfg)
                require((row["phone"], row["batch"]) == (phone, batch), f"{trial}: mislabeled ledger")
                rows.append(row)
                print(trial.name, row["file_kbps"], flush=True)
    require(len({r["file_sha256"] for r in rows}) == 1, "source file differs across trials")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(rows, indent=2) + "\n")


if __name__ == "__main__":
    main()
