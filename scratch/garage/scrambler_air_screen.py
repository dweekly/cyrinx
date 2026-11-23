#!/usr/bin/env python3
"""Over-the-air screen of one scrambled profile: a short record, sent through its batch encoder.

docs/SCRAMBLER_PLAN.md asks for this screen. The Mac's speakers play eight frames while one phone
records; the run is repeated for each phone. The candidate is one of the scrambled profiles 6 to
10, and its parent is the unscrambled profile five below it. The frames are, in order:

  0  dense random payload on the candidate profile        (control)
  1  dense random payload on the parent profile           (control)
  2  record A on the parent profile                        (the paired legacy transmission)
  3  record A on the candidate profile
  4  record B on the candidate profile
  5  record B on the parent profile
  6  dense random payload on the parent profile           (control)
  7  dense random payload on the candidate profile        (control)

A record is 128 bytes handed to cyrinx_batch_encode_v2 as a 128-byte payload, which fills the
rest of the frame with zeros. Each capture is cut into frames at the transmitter's schedule and
decoded by cyrinx_batch_decode_v2 with the profile that frame was sent with.

The run has three possible outcomes, and only the first is a pass:

  passed       at least two phones; on every phone every dense control decodes in full and
               both candidate record frames return a CRC-valid first block holding the record
  failed       the dense controls decode, and a candidate record frame is lost
  no answer    a dense control is lost, or fewer than two phones were recorded. The controls
               say whether the placement carries the geometry at all, scrambled and not; when
               they fail, the run says nothing about the scrambler

It is a screen: one placement, one volume, two records. It does not qualify a profile.

Usage:
  scrambler_air_screen.py record NEW_DIRECTORY --candidate ID --serial SERIAL --serial SERIAL
  scrambler_air_screen.py replay DIRECTORY     # re-score a retained run; fails if scores change

A replay rebuilds the frames the retained run recorded, so runs made with an earlier,
six-frame schedule still replay.
"""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "Sources/CCyrinx"
SR, GAP, LEAD, TAIL = 48000, 12000, 3072, 8000
SOURCES = ("cyrinx_batch.c", "cyrinx_profiles.c", "cyrinx_sha256.c", "cyrinx_bulk.c", "cyrinx_fft.c",
           "kissfft/kiss_fft.c", "kissfft/kiss_fftr.c")


def schedule(candidate):
    """The frames of a new run as (label, profile ID, payload name)."""
    parent = candidate - 5
    return (("dense control, scrambled", candidate, "dense-0"), ("dense control, unscrambled", parent, "dense-2"),
            ("record A, unscrambled", parent, "record-A"), ("record A, scrambled", candidate, "record-A"),
            ("record B, scrambled", candidate, "record-B"), ("record B, unscrambled", parent, "record-B"),
            ("dense control, unscrambled", parent, "dense-3"), ("dense control, scrambled", candidate, "dense-1"))


class Profile(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint32) for name in (
        "struct_size", "abi_version", "id", "classification", "modulation", "code_rate")] + [
        ("low_frequency_hz", ctypes.c_double), ("high_frequency_hz", ctypes.c_double)] + [
        (name, ctypes.c_uint32) for name in (
            "fft_size", "cyclic_prefix", "sample_rate", "pilot_every", "symbol_count", "block_count")] + [
        (name, ctypes.c_double) for name in ("amplitude", "clip_sigma", "chirp_f0", "chirp_f1")] + [
        ("identity_sha256", ctypes.c_uint8 * 32), ("payload_scrambler", ctypes.c_uint32),
        ("reserved0", ctypes.c_uint32)]


class View(ctypes.Structure):
    _fields_ = [("samples", ctypes.POINTER(ctypes.c_float)), ("sample_count", ctypes.c_uint64),
                ("first_sample", ctypes.c_uint64), ("stride", ctypes.c_uint32), ("role", ctypes.c_uint32)]


class Input(ctypes.Structure):
    _fields_ = [("struct_size", ctypes.c_uint32), ("abi_version", ctypes.c_uint32),
                ("channel_count", ctypes.c_uint32), ("reserved0", ctypes.c_uint32), ("channels", View * 2)]


class Result(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint32) for name in (
        "struct_size", "abi_version", "profile_id", "selected_receiver", "selection_reason", "blocks_total",
        "blocks_ok", "block_validity_required")] + [(name, ctypes.c_uint64) for name in (
            "payload_bytes_required", "logical_frame_samples", "logical_samples_decoded",
            "payload_bytes_produced", "samples_produced")] + [
        ("clipping_evidence", ctypes.c_uint32), ("nonfinite_evidence", ctypes.c_uint32),
        ("evm_rms", ctypes.c_double), ("primary_holdout_pilot_rms", ctypes.c_double),
        ("mrc_holdout_pilot_rms", ctypes.c_double), ("profile_identity_sha256", ctypes.c_uint8 * 32)]


assert (ctypes.sizeof(Profile), ctypes.sizeof(View), ctypes.sizeof(Input), ctypes.sizeof(Result)) == (136, 32, 80, 136)


class Codec:
    """The C batch codec, built from this checkout's sources."""

    def __init__(self, folder):
        library = Path(folder) / "libcyrinxbatch.dylib"
        subprocess.run(["clang", "-std=c11", "-O2", "-dynamiclib", "-Dkiss_fft_scalar=double", "-I",
                        str(CORE / "include"), "-I", str(CORE / "kissfft"), *[str(CORE / s) for s in SOURCES],
                        "-lm", "-o", str(library)], check=True)
        self.c = ctypes.CDLL(str(library))

    def profile(self, identifier):
        profile = Profile(struct_size=136, abi_version=2)
        if self.c.cyrinx_profile_v2_get_by_id(ctypes.c_uint32(identifier), ctypes.byref(profile)) != 0:
            raise ValueError(f"profile {identifier} is not registered")
        return profile

    def sizes(self, profile):
        result, byte, sample = Result(struct_size=136, abi_version=1), ctypes.c_uint8(), ctypes.c_float()
        status = self.c.cyrinx_batch_encode_v2(ctypes.byref(profile), ctypes.byref(byte), ctypes.c_size_t(0),
                                               ctypes.byref(sample), ctypes.c_size_t(0), ctypes.byref(result))
        if status != -3:
            raise ValueError(f"capacity query returned {status}")
        return int(result.logical_frame_samples), int(result.payload_bytes_required), int(result.blocks_total)

    def encode(self, profile, payload):
        frame, _, _ = self.sizes(profile)
        samples, result = (ctypes.c_float * frame)(), Result(struct_size=136, abi_version=1)
        data = (ctypes.c_uint8 * len(payload)).from_buffer_copy(payload)
        status = self.c.cyrinx_batch_encode_v2(ctypes.byref(profile), data, ctypes.c_size_t(len(payload)), samples,
                                               ctypes.c_size_t(frame), ctypes.byref(result))
        if status != 0 or result.samples_produced != frame:
            raise ValueError(f"encode returned {status}")
        return np.frombuffer(samples, dtype=np.float32).copy()

    def decode(self, profile, capture, channels):
        """`capture` is float32, interleaved when `channels` is 2. Returns the decoder's result."""
        _, capacity, blocks = self.sizes(profile)
        capture = np.ascontiguousarray(capture, dtype=np.float32)
        base = capture.ctypes.data_as(ctypes.POINTER(ctypes.c_float))
        batch = Input(struct_size=80, abi_version=1, channel_count=channels)
        for index in range(channels):
            batch.channels[index] = View(base, len(capture), index, channels, index)
        payload, mask = (ctypes.c_uint8 * capacity)(), (ctypes.c_uint8 * blocks)()
        result = Result(struct_size=136, abi_version=1)
        status = self.c.cyrinx_batch_decode_v2(ctypes.byref(profile), ctypes.byref(batch), payload,
                                               ctypes.c_size_t(capacity), mask, ctypes.c_size_t(blocks),
                                               ctypes.byref(result))
        return dict(status=int(status), mask=bytes(mask), payload=bytes(payload), receiver=int(result.selected_receiver),
                    reason=int(result.selection_reason), evm_rms=float(result.evm_rms),
                    clipping=int(result.clipping_evidence), nonfinite=int(result.nonfinite_evidence))


def payloads(capacity):
    """What each frame carries, as handed to the encoder, by name."""
    # Drawn in this order, so the four payloads of the first, six-frame schedule keep their bytes.
    rng = np.random.default_rng(20261122)
    return {"dense-0": rng.bytes(capacity), "dense-1": rng.bytes(capacity),
            "record-A": rng.bytes(128), "record-B": rng.bytes(128),
            "dense-2": rng.bytes(capacity), "dense-3": rng.bytes(capacity)}


def transmission(codec, candidate, frames=None):
    """The frames with their gaps, and the facts of the trial.

    `frames` is the schedule to build; a replay passes the one its run recorded.
    """
    if candidate not in range(6, 11):
        raise ValueError("the candidate is one of the scrambled profiles 6 to 10")
    parent = candidate - 5
    profiles = {identifier: codec.profile(identifier) for identifier in (candidate, parent)}
    if not profiles[candidate].payload_scrambler or profiles[parent].payload_scrambler:
        raise ValueError("the candidate must be scrambled and its parent unscrambled")
    frame, capacity, blocks = codec.sizes(profiles[candidate])
    if codec.sizes(profiles[parent]) != (frame, capacity, blocks):
        raise ValueError("the candidate and its parent do not share a geometry")
    data = payloads(capacity)
    frames = [tuple(row) for row in (frames or schedule(candidate))]
    if any(identifier not in profiles or name not in data for _, identifier, name in frames):
        raise ValueError("the schedule names a profile or payload this trial does not have")
    waves = [np.pad(codec.encode(profiles[identifier], data[name]), (0, GAP)) for _, identifier, name in frames]
    wave = np.concatenate(waves).astype("<f4")
    facts = dict(candidate_profile=candidate, parent_profile=parent, sample_rate=SR, frame_samples=frame, gap=GAP,
                 blocks=blocks, schedule=[list(row) for row in frames],
                 identities={str(i): bytes(p.identity_sha256).hex() for i, p in profiles.items()},
                 tx_float32_sha256=hashlib.sha256(wave.tobytes()).hexdigest())
    return profiles, data, wave, facts


def score(codec, profiles, data, wave, facts, pcm_path):
    """Cut one phone's capture at the transmitter's schedule and decode each frame."""
    from scipy.signal import fftconvolve

    raw = np.fromfile(pcm_path, "<i2").reshape(-1, 2)
    frame, step, capacity = facts["frame_samples"], facts["frame_samples"] + GAP, facts["blocks"] * 256
    room = len(raw) - len(wave)
    if room <= 0:
        raise ValueError(f"{pcm_path}: the capture is shorter than the transmission")
    # One alignment for the whole capture: where the six chirps, at their scheduled spacing, add up.
    chirp = wave[:4096].astype(float)
    matched = np.abs(fftconvolve(raw[:, 0].astype(float), chirp[::-1], mode="valid"))
    frames = [tuple(row) for row in facts["schedule"]]
    start = int(np.argmax(sum(matched[slot * step:slot * step + room] for slot in range(len(frames)))))
    rows = []
    for slot, (label, identifier, name) in enumerate(frames):
        at = start + slot * step
        window = raw[max(0, at - LEAD):at + frame + TAIL]
        # A phone that reports one microphone on both channels gives the decoder one channel.
        active = np.max(np.abs(window.astype("i4")), axis=1) >= 64
        mono = bool(active.sum() >= 4800 and np.mean(window[active, 0] == window[active, 1]) >= 0.999)
        capture = (window[:, 0] if mono else window.reshape(-1)).astype(np.float32) / 32768
        decoded = codec.decode(profiles[identifier], capture, 1 if mono else 2)
        expected = data[name] + bytes(capacity - len(data[name]))
        valid = [bool(v) for v in decoded["mask"]] if decoded["status"] == 0 else []
        right = [valid[i] and decoded["payload"][i * 256:(i + 1) * 256] == expected[i * 256:(i + 1) * 256]
                 for i in range(len(valid))]
        rows.append(dict(slot=slot, frame=label, profile=identifier, payload=name, status=decoded["status"],
                         channels=1 if mono else 2, receiver=decoded["receiver"], reason=decoded["reason"],
                         blocks_valid=sum(valid), blocks_verified=sum(right), blocks_total=facts["blocks"],
                         wrong_valid_blocks=sum(valid) - sum(right),
                         first_block_holds_the_payload=bool(right and right[0]),
                         evm_rms=None if not np.isfinite(decoded["evm_rms"]) else round(decoded["evm_rms"], 6),
                         clipping=decoded["clipping"]))
    return rows


def summarize(scores, candidate):
    """What a run established. See the module description for the three outcomes."""
    rows = [row for phone in scores.values() for row in phone]
    records = [row for row in rows if row["profile"] == candidate and row["payload"].startswith("record")]
    controls = [row for row in rows if row["payload"].startswith("dense")]
    recovered = bool(records) and all(row["first_block_holds_the_payload"] for row in records)
    carried = bool(controls) and all(row["blocks_verified"] == row["blocks_total"] for row in controls)
    # The plan asks for recovery on each of the two phones, so one phone alone is never a pass.
    enough = len(scores) >= 2
    outcome = "no answer" if not (carried and enough) else "passed" if recovered else "failed"
    return dict(passed=outcome == "passed", outcome=outcome, records_recovered=recovered,
                controls_decoded=carried, phones=len(scores))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mode", choices=["record", "replay"])
    parser.add_argument("folder", type=Path)
    parser.add_argument("--serial", action="append", default=[], help="a USB Android device running the HIL app")
    parser.add_argument("--candidate", type=int, help="record: the scrambled profile to screen, 6 to 10")
    args = parser.parse_args()
    if args.mode == "record" and args.candidate is None:
        raise SystemExit("record needs --candidate")
    # A replay screens the candidate, and rebuilds the frames, that its run recorded.
    recorded = None if args.mode == "record" else json.loads((args.folder / "trial.json").read_text())
    candidate = args.candidate if recorded is None else recorded["candidate_profile"]
    with tempfile.TemporaryDirectory(prefix="cyrinx-air-screen-") as build:
        codec = Codec(build)
        profiles, data, wave, facts = transmission(codec, candidate, recorded and recorded["schedule"])
        if args.mode == "record":
            if not args.serial or len(set(args.serial)) != len(args.serial):
                raise SystemExit("record needs each phone's --serial once")
            if ROOT in args.folder.resolve().parents:
                raise SystemExit("the recording directory must be outside the checkout")
            sys.path.insert(0, str(ROOT / "scratch/hw20k"))
            import harness

            args.folder.mkdir(parents=True, exist_ok=False)
            facts["mac_output"] = harness.mac_get_output_state()
            facts["revision"] = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], check=True,
                                               capture_output=True, text=True).stdout.strip()
            facts["phones"] = {}
            with harness.exclusive_android_audio_lock("scrambler-air-screen"):
                for serial in args.serial:
                    os.environ[harness.ANDROID_SERIAL_ENV] = serial
                    facts["phones"][serial] = harness.collect_android_target_provenance()
                    # A name of its own for every recording: the harness finds a recording's start
                    # and end by its name in the phone's log, and an earlier run's lines would match.
                    line, path = harness.mac_to_android(wave, out_name=f"airscreen_{time.time_ns()}.pcm")
                    shutil.move(path, args.folder / f"{serial}.pcm")
                    (args.folder / f"{serial}.record.txt").write_text(line + "\n")
                    frames = (args.folder / f"{serial}.pcm").stat().st_size // 4
                    if frames < len(wave) + SR:
                        raise SystemExit(f"{serial}: the capture holds {frames} frames, too few for the "
                                         f"{len(wave)}-sample transmission and its margins")
                    print(f"recorded {serial}")
            (args.folder / "trial.json").write_text(json.dumps(facts, indent=2) + "\n")
            shutil.copyfile(__file__, args.folder / Path(__file__).name)
        trial = json.loads((args.folder / "trial.json").read_text())
        for key in ("tx_float32_sha256", "identities", "frame_samples", "schedule"):
            if trial[key] != facts[key]:
                raise SystemExit(f"this checkout no longer makes the transmission that was recorded: {key} differs")
        scores = {serial: score(codec, profiles, data, wave, facts, args.folder / f"{serial}.pcm")
                  for serial in trial["phones"]}
    summary = dict(summarize(scores, candidate), scores=scores)
    ledger = args.folder / "scores.json"
    if args.mode == "record":
        ledger.write_text(json.dumps(summary, indent=2) + "\n")
    else:
        # The verdict and every frame's scores must be the retained ones. A ledger written before
        # a summary field existed is not failed for lacking it.
        retained = json.loads(ledger.read_text())
        if retained["scores"] != summary["scores"] or retained["passed"] != summary["passed"]:
            raise SystemExit("replay gave different scores from the retained ones")
    for serial, rows in scores.items():
        model = trial["phones"][serial].get("model", serial)
        for row in rows:
            print(f"{model:<14} {row['frame']:<22} profile {row['profile']:>2}  valid {row['blocks_valid']:>3}/"
                  f"{row['blocks_total']}  verified {row['blocks_verified']:>3}  record in block 0: "
                  f"{row['first_block_holds_the_payload']}")
    if summary["outcome"] == "no answer":
        reason = ("fewer than two phones were recorded" if summary["phones"] < 2
                  else "a dense control did not decode in full, so this placement does not carry the geometry")
        print(f"screen gave no answer: {reason}")
    else:
        print("screen passed" if summary["passed"] else "screen FAILED: the controls decoded and a record was lost")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
