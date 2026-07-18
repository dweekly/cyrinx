#!/usr/bin/env python3
"""Rank-5 bench spike: phase-continuous sequential acoustic path matrices.

This is research instrumentation, not a shipping sounder.  It composes the
existing route-checked Android HIL capture primitive, tone/linearity program,
OFDM pilot sounder, portable-C EVM probe, and Mac audio endpoint guards.  Raw
captures and per-request route logs are retained for offline reanalysis.

Every active matrix is rendered in one phase-continuous stereo buffer.  The
left and right output columns are time-division sounded while every receiver
channel remains continuously captured.  This preserves the evidence needed to
assess whether a later coherent 2x2 experiment is warranted; it does not by
itself establish coherent MIMO feasibility.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from typing import Any, Iterator, Sequence

import numpy as np


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import clib
import freqresp
import harness as H
import modem as M
import sounder
import tone_check_hardware as T


SR = 48_000
DIGITAL_PEAK = 0.18
F_LO = 1_100.0
F_HI = 23_000.0
NFFT = 2_048
CP = 768
SOUNDER_SYMBOLS = 8
EVM_SYMBOLS = 64
PRE_ROLL_S = 0.70
POST_ROLL_S = 0.80
BETWEEN_BLOCK_S = 0.65
BETWEEN_PROBES_S = 0.40
ANDROID_SOURCE = "unprocessed"
ANDROID_INPUT_CHANNELS = 2
PIXEL_MEDIA_MAX = 25
PIXEL_SELF_MEDIA_VOLUME = 15
PIXEL_REVERSE_MEDIA_VOLUME = 25
EXPECTED_OUTPUT_UID = "BuiltInSpeakerDevice"
EXPECTED_OUTPUT_NAME = "MacBook Pro Speakers"
EXPECTED_INPUT_UID = "BuiltInMicrophoneDevice"
EXPECTED_INPUT_NAME = "MacBook Pro Microphone"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_array(value: np.ndarray) -> str:
    array = np.ascontiguousarray(value)
    return hashlib.sha256(array.view(np.uint8)).hexdigest()


def rms(value: np.ndarray) -> float:
    array = np.asarray(value, dtype=np.float64)
    return float(np.sqrt(np.mean(array * array)))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def artifact(path: Path, *, frames: int | None = None, channels: int | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }
    if frames is not None:
        result["frames"] = frames
    if channels is not None:
        result["channels"] = channels
    return result


@dataclasses.dataclass(frozen=True)
class Segment:
    speaker: int
    probe: str
    start_sample: int
    end_sample: int

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True)
class MatrixProgram:
    stereo: np.ndarray
    order: tuple[int, int]
    segments: tuple[Segment, ...]
    tone: T.ToneProgram
    sounder_cfg: M.Config
    sounder_wave: np.ndarray
    evm_cfg: Any
    evm_payload: bytes
    evm_wave: np.ndarray


def normalized_peak(value: np.ndarray, peak: float = DIGITAL_PEAK) -> np.ndarray:
    array = np.asarray(value, dtype=np.float32)
    observed = float(np.max(np.abs(array)))
    if not math.isfinite(observed) or observed <= 0:
        raise ValueError("probe has no finite nonzero peak")
    return np.ascontiguousarray(array * (peak / observed), dtype=np.float32)


def build_program(order: str) -> MatrixProgram:
    if order not in ("lr", "rl"):
        raise ValueError("order must be lr or rl")
    output_order = (0, 1) if order == "lr" else (1, 0)
    tone = T.build_tone_program(DIGITAL_PEAK)
    sound_cfg, sound_wave = sounder.build_sounding(
        SR,
        NFFT,
        CP,
        F_LO,
        F_HI,
        2_000.0,
        16_000.0,
        amp=DIGITAL_PEAK,
    )
    sound_wave = normalized_peak(sound_wave)
    evm_cfg = clib.make_cfg(
        bits_per_bin=4,
        rate="1/2",
        n_sym=EVM_SYMBOLS,
        f_lo=F_LO,
        f_hi=F_HI,
        nfft=NFFT,
        cp=CP,
        sr=SR,
    )
    geometry = clib.geometry(evm_cfg)
    evm_payload = bytes((index * 31 + 7) & 0xFF for index in range(geometry.payload_bytes))
    evm_wave = normalized_peak(clib.encode(evm_cfg, evm_payload))

    stereo_parts: list[np.ndarray] = []
    segments: list[Segment] = []
    cursor = 0

    def silence(seconds: float) -> None:
        nonlocal cursor
        count = round(seconds * SR)
        stereo_parts.append(np.zeros((count, 2), dtype=np.float32))
        cursor += count

    def append_probe(speaker: int, label: str, mono: np.ndarray) -> None:
        nonlocal cursor
        value = np.zeros((len(mono), 2), dtype=np.float32)
        value[:, speaker] = mono
        start = cursor
        stereo_parts.append(value)
        cursor += len(value)
        segments.append(Segment(speaker, label, start, cursor))

    silence(PRE_ROLL_S)
    for block_index, speaker in enumerate(output_order):
        append_probe(speaker, "tone-linearity", tone.mono)
        silence(BETWEEN_PROBES_S)
        append_probe(speaker, "repeated-qpsk-sounder", sound_wave)
        silence(BETWEEN_PROBES_S)
        append_probe(speaker, "random-16qam-r12-evm", evm_wave)
        if block_index == 0:
            silence(BETWEEN_BLOCK_S)
    silence(POST_ROLL_S)
    stereo = np.concatenate(stereo_parts, axis=0)
    if float(np.max(np.abs(stereo))) > DIGITAL_PEAK + 1e-6:
        raise AssertionError("matrix program exceeds preregistered peak")
    if any(np.any(stereo[s.start_sample : s.end_sample, 1 - s.speaker]) for s in segments):
        raise AssertionError("inactive matrix output is not digital zero")
    return MatrixProgram(
        stereo=np.ascontiguousarray(stereo),
        order=output_order,
        segments=tuple(segments),
        tone=tone,
        sounder_cfg=sound_cfg,
        sounder_wave=sound_wave,
        evm_cfg=evm_cfg,
        evm_payload=evm_payload,
        evm_wave=evm_wave,
    )


def program_manifest(program: MatrixProgram) -> dict[str, Any]:
    return {
        "sample_rate_hz": SR,
        "output_channels": 2,
        "speaker_order": list(program.order),
        "frames": len(program.stereo),
        "duration_s": len(program.stereo) / SR,
        "digital_peak": float(np.max(np.abs(program.stereo))),
        "digital_rms_all_channels": rms(program.stereo),
        "digital_rms_per_channel": [rms(program.stereo[:, index]) for index in range(2)],
        "stereo_f32le_sha256": sha256_array(program.stereo.astype("<f4")),
        "segments": [segment.to_dict() for segment in program.segments],
        "tone_program": program.tone.manifest(),
        "sounder": {
            "nfft": NFFT,
            "cp": CP,
            "symbols": SOUNDER_SYMBOLS,
            "f_lo_hz": F_LO,
            "f_hi_hz": F_HI,
            "wave_sha256": sha256_array(program.sounder_wave.astype("<f4")),
        },
        "evm_probe": {
            "nfft": NFFT,
            "cp": CP,
            "symbols": EVM_SYMBOLS,
            "modulation": "16-QAM",
            "fec": "convolutional-r1/2",
            "payload_bytes": len(program.evm_payload),
            "payload_sha256": hashlib.sha256(program.evm_payload).hexdigest(),
            "wave_sha256": sha256_array(program.evm_wave.astype("<f4")),
        },
    }


def load_expected(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def require_route_start(request: str, expected_route: dict[str, Any]) -> dict[str, Any]:
    log = H.android_request_log(request)
    return H.validate_android_record_route_start_log(
        log,
        request,
        expected_source=ANDROID_SOURCE,
        expected_sample_rate_hz=SR,
        expected_channels=ANDROID_INPUT_CHANNELS,
        expected_route_signature=expected_route,
        require_distinct_direct_channel_mappings=True,
    )


def require_route_complete(request: str, expected_route: dict[str, Any]) -> tuple[dict[str, Any], str]:
    log = H.android_request_log(request)
    route = H.validate_android_record_route_log(
        log,
        request,
        expected_source=ANDROID_SOURCE,
        expected_sample_rate_hz=SR,
        expected_channels=ANDROID_INPUT_CHANNELS,
        expected_route_signature=expected_route,
        require_distinct_direct_channel_mappings=True,
    )
    return route, log


def select_mac_builtin_input() -> dict[str, str]:
    completed = subprocess.run(
        [T.SWITCH_AUDIO_SOURCE, "-t", "input", "-u", EXPECTED_INPUT_UID],
        check=True,
        capture_output=True,
        text=True,
    )
    current = subprocess.run(
        [T.SWITCH_AUDIO_SOURCE, "-c", "-t", "input", "-f", "json"],
        check=True,
        capture_output=True,
        text=True,
    )
    value = json.loads(current.stdout)
    if value.get("uid") != EXPECTED_INPUT_UID or value.get("name") != EXPECTED_INPUT_NAME:
        raise RuntimeError(f"failed to select built-in Mac input: {value}")
    return {"selection_stdout": completed.stdout.strip(), "name": value["name"], "uid": value["uid"]}


def current_mac_input() -> dict[str, str]:
    current = subprocess.run(
        [T.SWITCH_AUDIO_SOURCE, "-c", "-t", "input", "-f", "json"],
        check=True,
        capture_output=True,
        text=True,
    )
    value = json.loads(current.stdout)
    return {"name": str(value["name"]), "uid": str(value["uid"])}


@contextlib.contextmanager
def temporary_mac_builtin_input() -> Iterator[dict[str, Any]]:
    prior = current_mac_input()
    selected = select_mac_builtin_input()
    try:
        yield {"prior": prior, "selected": selected}
    finally:
        subprocess.run(
            [T.SWITCH_AUDIO_SOURCE, "-t", "input", "-u", prior["uid"]],
            check=True,
            capture_output=True,
            text=True,
        )
        restored = current_mac_input()
        if restored != prior:
            raise RuntimeError(f"Mac input restoration mismatch: {restored} != {prior}")


def pixel_media_volume_get() -> int:
    result = H.adb(["shell", "cmd", "media_session", "volume", "--stream", "3", "--get"])
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    match = re.search(r"volume is (\d+) in range \[0\.\.(\d+)\]", result.stdout)
    if match is None or int(match.group(2)) != PIXEL_MEDIA_MAX:
        raise RuntimeError(f"cannot parse Pixel media volume: {result.stdout!r}")
    return int(match.group(1))


def pixel_media_volume_set(value: int) -> int:
    if not 0 <= value <= PIXEL_MEDIA_MAX:
        raise ValueError("Pixel media volume outside reviewed range")
    result = H.adb(
        ["shell", "cmd", "media_session", "volume", "--stream", "3", "--set", str(value)]
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    realized = pixel_media_volume_get()
    if realized != value:
        raise RuntimeError(f"Pixel media volume realization mismatch: {realized} != {value}")
    return realized


@contextlib.contextmanager
def temporary_pixel_media_volume(value: int) -> Iterator[dict[str, int]]:
    prior = pixel_media_volume_get()
    realized = pixel_media_volume_set(value)
    state = {"prior": prior, "requested": value, "realized": realized}
    try:
        yield state
    finally:
        pixel_media_volume_set(prior)
        state["restored"] = pixel_media_volume_get()


def cmd_manifest(args: argparse.Namespace) -> None:
    values = {
        "lr": program_manifest(build_program("lr")),
        "rl": program_manifest(build_program("rl")),
    }
    write_json(args.out, values)
    print(json.dumps(values, indent=2, sort_keys=True))


def cmd_passive(args: argparse.Namespace) -> None:
    root = args.artifact_dir
    root.mkdir(parents=True, exist_ok=True)
    expected_target = load_expected(args.target)["android_target"]
    apk = load_expected(args.target)["hil_apk"]
    expected_target = {
        **expected_target,
        "package": H.PKG,
        "installed_package_apk_sha256": apk["installed_apk_sha256"],
    }
    expected_route = load_expected(args.route)
    H.assert_android_target(expected_target)
    started_wall = time.time_ns()
    pixel_path = root / "pixel-room-tone-stereo-s16le.pcm"
    route_log_path = root / "pixel-room-tone-route.logcat.txt"
    if args.resume_pixel:
        if not pixel_path.is_file() or not route_log_path.is_file():
            raise RuntimeError("--resume-pixel requires the retained Pixel PCM and route log")
        log = route_log_path.read_text(encoding="utf-8")
        requests = re.findall(r"rec_pcm begin: request=([^ ]+) ", log)
        if len(requests) != 1:
            raise RuntimeError("cannot recover one request ID from retained passive route log")
        request = requests[0]
        route = H.validate_android_record_route_log(
            log,
            request,
            expected_source=ANDROID_SOURCE,
            expected_sample_rate_hz=SR,
            expected_channels=ANDROID_INPUT_CHANNELS,
            expected_route_signature=expected_route,
            require_distinct_direct_channel_mappings=True,
        )
        start_route = route
    else:
        request = f"rank5-passive-{time.time_ns()}.pcm"
        H.android_record_start(
            args.duration,
            channels=2,
            source=ANDROID_SOURCE,
            out_name=request,
            sr=SR,
        )
        start_route = require_route_start(request, expected_route)
        H.android_record_finish(
            args.duration,
            out_name=request,
            local_path=str(pixel_path),
        )
        route, log = require_route_complete(request, expected_route)
        route_log_path.write_text(log, encoding="utf-8")
    pixel = H.load_pcm16(str(pixel_path), channels=2)
    with temporary_mac_builtin_input() as input_state:
        mac = H.mac_record(args.duration, sr=SR)
    mac_path = root / "mac-room-tone-mono-f32le.pcm"
    np.ascontiguousarray(mac, dtype="<f4").tofile(mac_path)
    analysis = {
        "schema": "cyrinx.rank5-passive.v1",
        "wall_clock_start_ns": started_wall,
        "duration_s": args.duration,
        "environment": {
            "hvac_state": "unknown",
            "statement": "No HVAC state was asserted; spectra are retained as the measured environmental evidence.",
        },
        "pixel": {
            "artifact": artifact(pixel_path, frames=len(pixel), channels=2),
            "route": route,
            "start_route": start_route,
            "rms": [rms(pixel[:, index]) for index in range(2)],
            "peak": [float(np.max(np.abs(pixel[:, index]))) for index in range(2)],
            "room_tone": [freqresp.analyze_room_tone(pixel[:, index], SR) for index in range(2)],
            "covariance": np.cov(pixel.T).tolist(),
            "correlation": float(np.corrcoef(pixel.T)[0, 1]),
            "exact_sample_equal_fraction": float(np.mean(pixel[:, 0] == pixel[:, 1])),
            "route_log": artifact(route_log_path),
        },
        "mac": {
            "artifact": artifact(mac_path, frames=len(mac), channels=1),
            "input_transaction": input_state,
            "rms": rms(mac),
            "peak": float(np.max(np.abs(mac))),
            "room_tone": freqresp.analyze_room_tone(mac, SR),
        },
    }
    write_json(root / "passive-analysis.json", analysis)
    print(json.dumps(analysis, indent=2, sort_keys=True))


def cmd_forward(args: argparse.Namespace) -> None:
    root = args.artifact_dir
    root.mkdir(parents=True, exist_ok=False)
    program = build_program(args.order)
    target_doc = load_expected(args.target)
    expected_target = {
        **target_doc["android_target"],
        "package": H.PKG,
        "installed_package_apk_sha256": target_doc["hil_apk"]["installed_apk_sha256"],
    }
    expected_route = load_expected(args.route)
    H.assert_android_target(expected_target)
    tx_path = root / "tx-stereo-f32le.pcm"
    np.ascontiguousarray(program.stereo, dtype="<f4").tofile(tx_path)
    request = f"rank5-{args.label}-{time.time_ns()}.pcm"
    duration = len(program.stereo) / SR + PRE_ROLL_S + POST_ROLL_S + 1.5
    started_wall = time.time_ns()
    volume_controller = T.MacVolumeController()
    endpoint_inspector = T.PortAudioOutputInspector()
    route_controller = T.MacDefaultOutputController()
    with H.exclusive_android_audio_lock(args.label), T.temporary_mac_volume(
        args.mac_volume,
        controller=volume_controller,
        endpoint_inspector=endpoint_inspector,
        route_controller=route_controller,
    ) as volume:
        H.android_record_start(
            duration,
            channels=2,
            source=ANDROID_SOURCE,
            out_name=request,
            sr=SR,
        )
        start_route = require_route_start(request, expected_route)
        time.sleep(PRE_ROLL_S)
        state_before = T._assert_playback_state(
            route_controller=route_controller,
            endpoint_inspector=endpoint_inspector,
            volume_controller=volume_controller,
            expected_volume=volume.requested,
        )
        playback_start_ns = time.time_ns()
        H.mac_play(program.stereo, block=True, sr=SR)
        playback_end_ns = time.time_ns()
        state_after = T._assert_playback_state(
            route_controller=route_controller,
            endpoint_inspector=endpoint_inspector,
            volume_controller=volume_controller,
            expected_volume=volume.requested,
        )
        _, capture_text = H.android_record_finish(
            duration,
            out_name=request,
            local_path=str(root / "rx-pixel-stereo-s16le.pcm"),
        )
    route, log = require_route_complete(request, expected_route)
    log_path = root / "android-route.logcat.txt"
    log_path.write_text(log, encoding="utf-8")
    capture_path = Path(capture_text)
    raw = np.fromfile(capture_path, dtype="<i2")
    if len(raw) % 2:
        raise RuntimeError("Pixel stereo capture has an odd sample count")
    capture = raw.reshape(-1, 2).astype(np.float32) / 32768.0
    metadata = {
        "schema": "cyrinx.rank5-forward-acquisition.v1",
        "label": args.label,
        "wall_clock_start_ns": started_wall,
        "wall_clock_playback_start_ns": playback_start_ns,
        "wall_clock_playback_end_ns": playback_end_ns,
        "geometry": args.geometry,
        "environment": {"hvac_state": "unknown"},
        "target": H.collect_android_target_provenance(),
        "route": route,
        "route_start": start_route,
        "mac_output_volume_percent": args.mac_volume,
        "mac_output_before": state_before,
        "mac_output_after": state_after,
        "volume_restored": dataclasses.asdict(volume.restored) if volume.restored else None,
        "program": program_manifest(program),
        "tx_artifact": artifact(tx_path, frames=len(program.stereo), channels=2),
        "capture_artifact": artifact(capture_path, frames=len(capture), channels=2),
        "route_log": artifact(log_path),
        "capture_peak": [float(np.max(np.abs(capture[:, index]))) for index in range(2)],
        "capture_rms": [rms(capture[:, index]) for index in range(2)],
        "capture_full_scale_count": int(np.sum(np.abs(raw) >= 32766)),
    }
    write_json(root / "acquisition.json", metadata)
    print(json.dumps(metadata, indent=2, sort_keys=True))


def cmd_reverse(args: argparse.Namespace) -> None:
    root = args.artifact_dir
    root.mkdir(parents=True, exist_ok=False)
    program = build_program(args.order)
    tx_path = root / "tx-pixel-stereo-f32le.pcm"
    np.ascontiguousarray(program.stereo, dtype="<f4").tofile(tx_path)
    started_wall = time.time_ns()
    with H.exclusive_android_audio_lock(args.label), temporary_mac_builtin_input() as input_state:
        with temporary_pixel_media_volume(args.pixel_volume) as volume_state:
            H.android_prepare()
            playback_start_ns = time.time_ns()
            capture = H.android_to_mac(program.stereo, channels=2, sr=SR)
            playback_end_ns = time.time_ns()
    capture_path = root / "rx-mac-mono-f32le.pcm"
    np.ascontiguousarray(capture, dtype="<f4").tofile(capture_path)
    metadata = {
        "schema": "cyrinx.rank5-reverse-acquisition.v1",
        "label": args.label,
        "wall_clock_start_ns": started_wall,
        "wall_clock_playback_start_ns": playback_start_ns,
        "wall_clock_playback_end_ns": playback_end_ns,
        "geometry": args.geometry,
        "environment": {"hvac_state": "unknown"},
        "target": H.collect_android_target_provenance(),
        "pixel_media_volume": volume_state,
        "mac_input": input_state,
        "program": program_manifest(program),
        "tx_artifact": artifact(tx_path, frames=len(program.stereo), channels=2),
        "capture_artifact": artifact(capture_path, frames=len(capture), channels=1),
        "capture_peak": float(np.max(np.abs(capture))),
        "capture_rms": rms(capture),
        "limitation": (
            "The current play_pcm primitive does not expose a parsed realized-output "
            "route; this acquisition is descriptive until that provenance gap is closed."
        ),
    }
    write_json(root / "acquisition.json", metadata)
    print(json.dumps(metadata, indent=2, sort_keys=True))


def cmd_pixel_self(args: argparse.Namespace) -> None:
    root = args.artifact_dir
    root.mkdir(parents=True, exist_ok=False)
    program = build_program(args.order)
    tx_path = root / "tx-pixel-stereo-f32le.pcm"
    np.ascontiguousarray(program.stereo, dtype="<f4").tofile(tx_path)
    started_wall = time.time_ns()
    out_name = f"rank5-self-{time.time_ns()}.pcm"
    with H.exclusive_android_audio_lock(args.label), temporary_pixel_media_volume(
        args.pixel_volume
    ) as volume_state:
        _, _, capture_text, log = H.android_playrec(
            program.stereo,
            out_name,
            source=ANDROID_SOURCE,
            input_channels=2,
            pre_roll_ms=700,
            post_roll_ms=800,
            sr=SR,
            local_path=str(root / "rx-pixel-self-stereo-s16le.pcm"),
        )
    log_path = root / "android-playrec-route.logcat.txt"
    log_path.write_text(log, encoding="utf-8")
    capture_path = Path(capture_text)
    raw = np.fromfile(capture_path, dtype="<i2")
    if len(raw) % 2:
        raise RuntimeError("Pixel self capture has an odd sample count")
    capture = raw.reshape(-1, 2).astype(np.float32) / 32768.0
    metadata = {
        "schema": "cyrinx.rank5-pixel-self-acquisition.v1",
        "label": args.label,
        "wall_clock_start_ns": started_wall,
        "geometry": args.geometry,
        "environment": {"hvac_state": "unknown"},
        "target": H.collect_android_target_provenance(),
        "pixel_media_volume": volume_state,
        "requested_source": ANDROID_SOURCE,
        "program": program_manifest(program),
        "tx_artifact": artifact(tx_path, frames=len(program.stereo), channels=2),
        "capture_artifact": artifact(capture_path, frames=len(capture), channels=2),
        "route_log": artifact(log_path),
        "capture_peak": [float(np.max(np.abs(capture[:, index]))) for index in range(2)],
        "capture_rms": [rms(capture[:, index]) for index in range(2)],
        "capture_full_scale_count": int(np.sum(np.abs(raw) >= 32766)),
    }
    write_json(root / "acquisition.json", metadata)
    print(json.dumps(metadata, indent=2, sort_keys=True))


def cmd_mac_self(args: argparse.Namespace) -> None:
    root = args.artifact_dir
    root.mkdir(parents=True, exist_ok=False)
    program = build_program(args.order)
    tx_path = root / "tx-mac-stereo-f32le.pcm"
    np.ascontiguousarray(program.stereo, dtype="<f4").tofile(tx_path)
    volume_controller = T.MacVolumeController()
    endpoint_inspector = T.PortAudioOutputInspector()
    route_controller = T.MacDefaultOutputController()
    started_wall = time.time_ns()
    with temporary_mac_builtin_input() as input_state, T.temporary_mac_volume(
        args.mac_volume,
        controller=volume_controller,
        endpoint_inspector=endpoint_inspector,
        route_controller=route_controller,
    ) as volume:
        playback_start_ns = time.time_ns()
        capture = H.mac_play_and_record(program.stereo, extra_s=1.0)
        playback_end_ns = time.time_ns()
    capture_path = root / "rx-mac-self-mono-f32le.pcm"
    np.ascontiguousarray(capture, dtype="<f4").tofile(capture_path)
    metadata = {
        "schema": "cyrinx.rank5-mac-self-acquisition.v1",
        "label": args.label,
        "wall_clock_start_ns": started_wall,
        "wall_clock_playback_start_ns": playback_start_ns,
        "wall_clock_playback_end_ns": playback_end_ns,
        "mac_output_volume_percent": args.mac_volume,
        "mac_input": input_state,
        "program": program_manifest(program),
        "tx_artifact": artifact(tx_path, frames=len(program.stereo), channels=2),
        "capture_artifact": artifact(capture_path, frames=len(capture), channels=1),
        "capture_peak": float(np.max(np.abs(capture))),
        "capture_rms": rms(capture),
        "volume_restored": dataclasses.asdict(volume.restored) if volume.restored else None,
    }
    write_json(root / "acquisition.json", metadata)
    print(json.dumps(metadata, indent=2, sort_keys=True))


def marker_pairs(
    capture_channel: np.ndarray,
    program: MatrixProgram,
) -> list[tuple[int, int, float]]:
    """Locate the two repeated-marker pairs without using payload outcomes."""

    score = T.normalized_correlation(capture_channel, program.tone.sync_marker)
    candidates = T._nonmaximum_candidates(
        score,
        count=64,
        radius=len(program.tone.sync_marker) // 2,
    )
    expected = program.tone.sync_separation_samples
    tolerance = round(0.025 * SR)
    possible: list[tuple[float, int, int]] = []
    for start in candidates:
        expected_end = start + expected
        lo = max(0, expected_end - tolerance)
        hi = min(len(score), expected_end + tolerance + 1)
        if lo >= hi:
            continue
        end = lo + int(np.argmax(score[lo:hi]))
        pair_score = min(float(score[start]), float(score[end]))
        if pair_score >= T.MINIMUM_SYNC_SCORE:
            possible.append((pair_score, start, end))
    # Prefer the strongest non-overlapping pair in each chronological block.
    possible.sort(reverse=True)
    selected: list[tuple[int, int, float]] = []
    for pair_score, start, end in possible:
        if any(abs(start - prior[0]) < len(program.tone.mono) // 2 for prior in selected):
            continue
        selected.append((start, end, pair_score))
        if len(selected) == 2:
            break
    selected.sort()
    if len(selected) != 2:
        raise RuntimeError(f"located {len(selected)} marker pairs; expected two")
    return selected


def affine_from_markers(
    program: MatrixProgram,
    pairs: list[tuple[int, int, float]],
) -> tuple[float, float, list[dict[str, float]]]:
    tone_segments = [segment for segment in program.segments if segment.probe == "tone-linearity"]
    if len(tone_segments) != 2:
        raise AssertionError("program must contain two tone blocks")
    scheduled: list[float] = []
    observed: list[float] = []
    points: list[dict[str, float]] = []
    for segment, pair in zip(tone_segments, pairs):
        for label, local, actual in (
            ("start", program.tone.start_sync_sample, pair[0]),
            ("end", program.tone.end_sync_sample, pair[1]),
        ):
            schedule = float(segment.start_sample + local)
            scheduled.append(schedule)
            observed.append(float(actual))
            points.append(
                {
                    "speaker": float(segment.speaker),
                    "marker": 0.0 if label == "start" else 1.0,
                    "scheduled_sample": schedule,
                    "observed_sample": float(actual),
                }
            )
    scale, offset = np.polyfit(np.asarray(scheduled), np.asarray(observed), 1)
    residual = np.asarray(observed) - (offset + scale * np.asarray(scheduled))
    for point, error in zip(points, residual):
        point["fit_residual_samples"] = float(error)
    return float(offset), float(scale), points


def mapped_slice(
    capture: np.ndarray,
    offset: float,
    scale: float,
    start_sample: int,
    end_sample: int,
    margin_s: float = 0.20,
) -> tuple[np.ndarray, int]:
    margin = round(margin_s * SR)
    lo = max(0, math.floor(offset + scale * start_sample) - margin)
    hi = min(len(capture), math.ceil(offset + scale * end_sample) + margin)
    if hi - lo < end_sample - start_sample:
        raise RuntimeError("mapped probe slice is unexpectedly short")
    return np.asarray(capture[lo:hi]), lo


def tone_analysis_from_matrix_clock(
    tone: T.ToneProgram,
    capture: np.ndarray,
    matrix_segment: Segment,
    offset: float,
    scale: float,
    marker_pairs_for_block: list[tuple[int, int, float]],
) -> dict[str, Any]:
    """Analyze a nested tone program using the full-matrix affine clock fit."""

    value = np.asarray(capture, dtype=np.float64)
    receivers = []
    alignments = []
    for rx in range(value.shape[1]):
        start_marker, end_marker, pair_score = marker_pairs_for_block[rx]
        alignment = T.AlignmentEstimate(
            valid=True,
            capture_origin_sample=round(offset + scale * matrix_segment.start_sample),
            start_marker_sample=int(start_marker),
            end_marker_sample=int(end_marker),
            sample_scale=float(scale),
            clock_error_ppm=float((scale - 1.0) * 1e6),
            start_score=float(pair_score),
            end_score=float(pair_score),
            peak_to_sidelobe_db=0.0,
            uncertainty_samples=1,
            reason="full-matrix affine schedule from four payload-independent markers",
        )
        alignments.append(alignment)
        segments = [T._analyze_segment(tone, value[:, rx], alignment, segment) for segment in tone.segments]
        thd_values = [
            item["thd"]["conservative_upper_bound_dbc"]
            for item in segments
            if item.get("thd") is not None
        ]
        imd_values = [
            item["imd"]["conservative_upper_bound_dbc"]
            for item in segments
            if item.get("imd") is not None
        ]
        receivers.append(
            {
                "rx_index": rx,
                "alignment": alignment.to_dict(),
                "schedule_alignment": alignment.to_dict(),
                "own_marker_detection_valid": False,
                "timing_method": "full-matrix-affine-marker-fit",
                "absolute_peak": float(np.max(np.abs(value[:, rx]))),
                "clipped_sample_fraction": float(np.mean(np.abs(value[:, rx]) >= 32766.0 / 32768.0)),
                "segments": segments,
                "noise": T._noise_summary(tone, value[:, rx], alignment),
                "maximum_thd_dbc": max(thd_values),
                "maximum_imd_dbc": max(imd_values),
                "maximum_absolute_within_stage_gain_jump_db": max(
                    abs(item["early_to_late_gain_change_db"]) for item in segments
                ),
                "minimum_fundamental_snr_db": min(
                    item["minimum_fundamental_snr_db"] for item in segments
                ),
            }
        )
    result = {
        "schema": "cyrinx.rank5-tone-matrix-clock.v1",
        "tx_index": matrix_segment.speaker,
        "receivers": receivers,
        "duplicate_channel_diagnostics": (
            T.duplicate_channel_diagnostics(tone, value, alignments)
            if value.shape[1] == 2
            else None
        ),
    }
    return result


def complex_sounder_analysis(cfg: M.Config, capture: np.ndarray) -> dict[str, Any]:
    value = np.asarray(capture, dtype=np.float64)
    known = M.sync_symbol_freq(cfg, 0)
    chirp_start, chirp_score = M.find_chirp(value, chirp=cfg.chirp_wave)
    base = chirp_start + len(cfg.chirp_wave) + M.GUARD
    reference = M.ofdm_mod_symbol(cfg, known)
    lo = max(0, base - 400)
    hi = min(len(value), base + 400 + cfg.sym)
    matched = np.correlate(value[lo:hi], reference, mode="valid")
    base = lo + int(np.argmax(np.abs(matched)))
    channels = []
    for symbol in range(SOUNDER_SYMBOLS):
        start = base + symbol * cfg.sym + cfg.cp
        segment = value[start : start + cfg.nfft]
        if len(segment) == cfg.nfft:
            channels.append(np.fft.rfft(segment)[cfg.used] / known)
    if len(channels) < SOUNDER_SYMBOLS - 1:
        raise RuntimeError("sounder has too few complete symbols")
    matrix = np.stack(channels)
    mean = np.mean(matrix, axis=0)
    variance = np.var(matrix, axis=0) + 1e-18
    snr_linear = np.abs(mean) ** 2 / variance
    snr_db = 10 * np.log10(np.maximum(snr_linear, 1e-12))
    phases = np.unwrap(np.angle(matrix), axis=0)
    strongest = int(np.argmax(np.abs(mean)))
    slope = np.polyfit(np.arange(len(phases)), phases[:, strongest], 1)[0]
    common = np.arange(len(phases))[:, None] * slope
    residual_phase = np.angle(np.exp(1j * (phases - common - phases[0:1])))
    per_bin_phase_std = np.std(residual_phase, axis=0)
    operational = sounder.analyze(cfg, value, SR)
    symbol_rate = SR / (cfg.nfft + cfg.cp)
    descriptive_capacity = float(np.sum(np.log2(1.0 + snr_linear)) * symbol_rate)
    return {
        "chirp_score": float(chirp_score),
        "chirp_start_in_slice": int(chirp_start),
        "frequencies_hz": (cfg.used * cfg.bin_hz).astype(float).tolist(),
        "H_real": mean.real.astype(float).tolist(),
        "H_imag": mean.imag.astype(float).tolist(),
        "H_magnitude_db": (20 * np.log10(np.maximum(np.abs(mean), 1e-18))).tolist(),
        "H_phase_rad": np.angle(mean).tolist(),
        "snr_db": snr_db.tolist(),
        "median_snr_db": float(np.median(snr_db)),
        "bins_snr_ge_12db": int(np.sum(snr_db >= 12.0)),
        "bins_total": int(len(snr_db)),
        "delay_spread_ms": operational["delay_spread_ms"],
        "clock_ppm_estimator": operational["clock_ppm_est"],
        "per_bin_phase_std_rad": per_bin_phase_std.tolist(),
        "phase_std_median_deg_snr20": (
            float(np.degrees(np.median(per_bin_phase_std[snr_db >= 20.0])))
            if np.any(snr_db >= 20.0)
            else None
        ),
        "descriptive_repeated_pilot_capacity_bps": descriptive_capacity,
        "capacity_warning": (
            "Repeated-pilot variance bound; not Shannon capacity, GMI, delivered "
            "goodput, or an adaptation-qualified predictor."
        ),
    }


def decode_probe(cfg: Any, payload: bytes, capture: np.ndarray) -> dict[str, Any]:
    decoded = clib.decode(cfg, np.asarray(capture, dtype=np.float32))
    if decoded is None:
        return {
            "synced": False,
            "blocks_ok": 0,
            "blocks_total": clib.geometry(cfg).n_blocks,
            "strict_ordered_verified_blocks": 0,
            "evm": None,
        }
    verified = clib.ordered_verified_blocks(decoded, payload)
    return {
        "synced": True,
        "blocks_ok": int(decoded["blocks_ok"]),
        "blocks_total": int(decoded["blocks_total"]),
        "block_valid": decoded["block_valid"],
        "strict_ordered_verified_blocks": int(verified),
        "strict_ordered_verified_bytes": int(verified * 256),
        "payload_exact": bool(decoded["payload"] == payload),
        "evm": float(decoded["evm"]),
    }


def analyze_acquisition(root: Path) -> dict[str, Any]:
    metadata = load_expected(root / "acquisition.json")
    program_order = metadata["program"]["speaker_order"]
    order = "lr" if program_order == [0, 1] else "rl"
    program = build_program(order)
    capture_record = metadata["capture_artifact"]
    capture_path = Path(capture_record["path"])
    channels = int(capture_record["channels"])
    if capture_path.name.endswith("s16le.pcm"):
        raw = np.fromfile(capture_path, dtype="<i2")
        capture = raw[: len(raw) // channels * channels].reshape(-1, channels).astype(np.float32) / 32768.0
    else:
        raw_f = np.fromfile(capture_path, dtype="<f4")
        capture = raw_f[: len(raw_f) // channels * channels].reshape(-1, channels)

    per_receiver_pairs = []
    pair_sources = []
    for rx in range(channels):
        try:
            per_receiver_pairs.append(marker_pairs(capture[:, rx], program))
            pair_sources.append("independent-repeated-marker")
        except RuntimeError:
            if rx == 0:
                raise
            # Interleaved channels share one ADC clock. Preserve the weak own-
            # marker result as a negative finding, but use receiver 0 only for
            # schedule coordinates; no payload or CRC outcome enters this path.
            per_receiver_pairs.append(per_receiver_pairs[0])
            pair_sources.append("shared-interleaved-capture-clock-from-rx0")
    affine = [affine_from_markers(program, pairs) for pairs in per_receiver_pairs]
    # Interleaved receiver channels share one capture clock. Use receiver 0's
    # schedule mapping for sample-aligned MRC slices, retaining each receiver's
    # independent fit as a diagnostic.
    global_offset, global_scale, _ = affine[0]
    paths: list[dict[str, Any]] = []
    tone_segments = [segment for segment in program.segments if segment.probe == "tone-linearity"]
    sound_segments = [segment for segment in program.segments if segment.probe == "repeated-qpsk-sounder"]
    evm_segments = [segment for segment in program.segments if segment.probe == "random-16qam-r12-evm"]
    for block, (tone_segment, sound_segment, evm_segment) in enumerate(
        zip(tone_segments, sound_segments, evm_segments)
    ):
        pair_starts = [per_receiver_pairs[rx][block][0] for rx in range(channels)]
        pair_ends = [per_receiver_pairs[rx][block][1] for rx in range(channels)]
        tone_lo = max(0, min(pair_starts) - program.tone.start_sync_sample - round(0.1 * SR))
        tone_hi = min(
            len(capture),
            max(pair_ends) + (len(program.tone.mono) - program.tone.end_sync_sample) + round(0.1 * SR),
        )
        tone_analysis = None
        if channels == 2:
            tone_analysis = tone_analysis_from_matrix_clock(
                program.tone,
                capture,
                tone_segment,
                global_offset,
                global_scale,
                [per_receiver_pairs[rx][block] for rx in range(channels)],
            )
        sound_capture, sound_origin = mapped_slice(
            capture,
            global_offset,
            global_scale,
            sound_segment.start_sample,
            sound_segment.end_sample,
        )
        evm_capture, evm_origin = mapped_slice(
            capture,
            global_offset,
            global_scale,
            evm_segment.start_sample,
            evm_segment.end_sample,
        )
        receivers = []
        for rx in range(channels):
            receivers.append(
                {
                    "receiver": rx,
                    "sounder": complex_sounder_analysis(program.sounder_cfg, sound_capture[:, rx]),
                    "evm_probe": decode_probe(program.evm_cfg, program.evm_payload, evm_capture[:, rx]),
                }
            )
        mrc = None
        if channels == 2:
            decoded = clib.decode2(program.evm_cfg, evm_capture[:, 0], evm_capture[:, 1])
            if decoded is not None:
                verified = clib.ordered_verified_blocks(decoded, program.evm_payload)
                mrc = {
                    "blocks_ok": int(decoded["blocks_ok"]),
                    "blocks_total": int(decoded["blocks_total"]),
                    "block_valid": decoded["block_valid"],
                    "strict_ordered_verified_blocks": int(verified),
                    "strict_ordered_verified_bytes": int(verified * 256),
                    "payload_exact": bool(decoded["payload"] == program.evm_payload),
                    "evm": float(decoded["evm"]),
                }
        paths.append(
            {
                "speaker": tone_segment.speaker,
                "tone_capture_slice": [tone_lo, tone_hi],
                "sounder_capture_origin": sound_origin,
                "evm_capture_origin": evm_origin,
                "tone": tone_analysis,
                "receivers": receivers,
                "mrc_evm_probe": mrc,
            }
        )
    safety_reasons: list[str] = []
    for path in paths:
        tone_result = path["tone"]
        if tone_result is None:
            continue
        for receiver in tone_result["receivers"]:
            label = f"speaker{path['speaker']}/rx{receiver['rx_index']}"
            schedule = receiver["schedule_alignment"]
            if not schedule["valid"]:
                safety_reasons.append(f"{label}: no valid shared-clock schedule alignment")
                continue
            if receiver["absolute_peak"] >= T.ABSOLUTE_SAMPLE_PEAK_LIMIT:
                safety_reasons.append(f"{label}: absolute peak reaches limit")
            if receiver["clipped_sample_fraction"] > T.CLIPPED_SAMPLE_FRACTION_LIMIT:
                safety_reasons.append(f"{label}: clipped fraction exceeds limit")
            if receiver["maximum_thd_dbc"] > T.MAXIMUM_THD_DB:
                safety_reasons.append(f"{label}: conservative THD exceeds {T.MAXIMUM_THD_DB} dBc")
            if receiver["maximum_imd_dbc"] > T.MAXIMUM_IMD_DB:
                safety_reasons.append(f"{label}: conservative IMD exceeds {T.MAXIMUM_IMD_DB} dBc")
            if receiver["maximum_absolute_within_stage_gain_jump_db"] > T.WITHIN_STAGE_GAIN_JUMP_LIMIT_DB:
                safety_reasons.append(f"{label}: within-stage gain jump exceeds limit")
            if receiver["noise"]["post_signal_noise_increase_db"] > T.POST_SIGNAL_NOISE_INCREASE_LIMIT_DB:
                safety_reasons.append(f"{label}: post-signal noise increase exceeds limit")
            if receiver["minimum_fundamental_snr_db"] < T.MINIMUM_FUNDAMENTAL_SNR_DB:
                safety_reasons.append(f"{label}: minimum tone SNR is below {T.MINIMUM_FUNDAMENTAL_SNR_DB} dB")
    if int(metadata.get("capture_full_scale_count", 0)) != 0:
        safety_reasons.append("capture contains full-scale PCM samples")
    peak = metadata["capture_peak"]
    peak_values = peak if isinstance(peak, list) else [peak]
    if max(float(value) for value in peak_values) >= T.ABSOLUTE_SAMPLE_PEAK_LIMIT:
        safety_reasons.append("capture reaches absolute peak limit")
    result = {
        "schema": "cyrinx.rank5-matrix-analysis.v1",
        "acquisition_sha256": sha256_file(root / "acquisition.json"),
        "analysis_script_sha256": sha256_file(Path(__file__)),
        "capture_sha256": capture_record["sha256"],
        "channels": channels,
        "marker_pairs_by_receiver": per_receiver_pairs,
        "marker_pair_source_by_receiver": pair_sources,
        "sample_mapping_by_receiver": [
            {
                "offset_samples": item[0],
                "sample_scale": item[1],
                "clock_error_ppm": (item[1] - 1.0) * 1e6,
                "points": item[2],
            }
            for item in affine
        ],
        "paths": paths,
        "next_level_gate": {"passed": not safety_reasons, "reasons": safety_reasons},
        "goodput_semantics": (
            "Probe block recovery is strict and ordered within one known EVM frame. "
            "Capacity figures are descriptive repeated-pilot arithmetic. Neither is "
            "setup-inclusive application goodput."
        ),
    }
    return result


def cmd_analyze(args: argparse.Namespace) -> None:
    result = analyze_acquisition(args.artifact_dir)
    write_json(args.artifact_dir / "analysis.json", result)
    summary = {
        "schema": result["schema"],
        "capture_sha256": result["capture_sha256"],
        "clock_error_ppm": [item["clock_error_ppm"] for item in result["sample_mapping_by_receiver"]],
        "next_level_gate": result["next_level_gate"],
        "paths": [
            {
                "speaker": path["speaker"],
                "tone_gate": (
                    {
                        "minimum_snr_db": min(
                            receiver["minimum_fundamental_snr_db"]
                            for receiver in path["tone"]["receivers"]
                        ),
                        "maximum_thd_dbc": max(
                            receiver["maximum_thd_dbc"]
                            for receiver in path["tone"]["receivers"]
                        ),
                        "maximum_imd_dbc": max(
                            receiver["maximum_imd_dbc"]
                            for receiver in path["tone"]["receivers"]
                        ),
                    }
                    if path["tone"]
                    else None
                ),
                "receivers": [
                    {
                        "receiver": receiver["receiver"],
                        "median_snr_db": receiver["sounder"]["median_snr_db"],
                        "delay_spread_ms": receiver["sounder"]["delay_spread_ms"],
                        "evm_probe": receiver["evm_probe"],
                    }
                    for receiver in path["receivers"]
                ],
                "mrc_evm_probe": path["mrc_evm_probe"],
            }
            for path in result["paths"]
        ],
    }
    print(json.dumps(summary, indent=2, sort_keys=True))


def cmd_compare(args: argparse.Namespace) -> None:
    calibration = load_expected(args.calibration / "analysis.json")
    heldout = load_expected(args.heldout / "analysis.json")

    def indexed(value: dict[str, Any]) -> dict[tuple[int, int], dict[str, Any]]:
        result = {}
        for path in value["paths"]:
            for receiver in path["receivers"]:
                result[(int(path["speaker"]), int(receiver["receiver"]))] = receiver
        return result

    first = indexed(calibration)
    second = indexed(heldout)
    comparisons = []
    for key in sorted(first.keys() & second.keys()):
        first_receiver = first[key]
        second_receiver = second[key]
        first_sound = first_receiver["sounder"]
        second_sound = second_receiver["sounder"]
        h1 = np.asarray(first_sound["H_real"]) + 1j * np.asarray(first_sound["H_imag"])
        h2 = np.asarray(second_sound["H_real"]) + 1j * np.asarray(second_sound["H_imag"])
        snr1 = np.asarray(first_sound["snr_db"])
        snr2 = np.asarray(second_sound["snr_db"])
        freqs = np.asarray(first_sound["frequencies_hz"])
        active = (snr1 >= args.minimum_snr_db) & (snr2 >= args.minimum_snr_db)
        active_count = int(np.sum(active))
        if active_count:
            magnitude_change = np.abs(
                20 * np.log10(np.maximum(np.abs(h2[active]), 1e-18))
                - 20 * np.log10(np.maximum(np.abs(h1[active]), 1e-18))
            )
            phase_delta = np.unwrap(np.angle(h2[active] * np.conj(h1[active])))
            if active_count >= 2:
                slope, intercept = np.polyfit(freqs[active], phase_delta, 1)
                phase_residual = phase_delta - (slope * freqs[active] + intercept)
            else:
                phase_residual = phase_delta - phase_delta[0]
            normalized_coherence = float(
                abs(np.vdot(h1[active], h2[active]))
                / max(
                    math.sqrt(float(np.vdot(h1[active], h1[active]).real * np.vdot(h2[active], h2[active]).real)),
                    np.finfo(float).tiny,
                )
            )
            metrics = {
                "magnitude_change_median_db": float(np.median(magnitude_change)),
                "magnitude_change_p90_db": float(np.quantile(magnitude_change, 0.90)),
                "phase_residual_std_deg_after_affine_removal": float(np.degrees(np.std(phase_residual))),
                "complex_vector_coherence_without_delay_removal": normalized_coherence,
            }
        else:
            metrics = {
                "magnitude_change_median_db": None,
                "magnitude_change_p90_db": None,
                "phase_residual_std_deg_after_affine_removal": None,
                "complex_vector_coherence_without_delay_removal": None,
            }
        first_ds = float(first_sound["delay_spread_ms"]["-15dB"])
        second_ds = float(second_sound["delay_spread_ms"]["-15dB"])
        comparisons.append(
            {
                "speaker": key[0],
                "receiver": key[1],
                "active_bin_rule_snr_db": args.minimum_snr_db,
                "active_bins_both_repeats": active_count,
                "occupied_bins": int(len(active)),
                "active_fraction": active_count / len(active),
                **metrics,
                "delay_spread_calibration_ms_15": first_ds,
                "delay_spread_heldout_ms_15": second_ds,
                "delay_spread_absolute_change_ms": abs(second_ds - first_ds),
                "calibration_probe": first_receiver["evm_probe"],
                "heldout_probe": second_receiver["evm_probe"],
            }
        )
    result = {
        "schema": "cyrinx.rank5-repeat-comparison.v1",
        "calibration_analysis_sha256": sha256_file(args.calibration / "analysis.json"),
        "heldout_analysis_sha256": sha256_file(args.heldout / "analysis.json"),
        "minimum_snr_db": args.minimum_snr_db,
        "comparisons": comparisons,
        "phase_warning": (
            "Affine phase removal is descriptive; this sequential probe does not "
            "measure simultaneous two-stream separation."
        ),
    }
    write_json(args.out, result)
    print(json.dumps(result, indent=2, sort_keys=True))


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = value.add_subparsers(dest="command", required=True)

    manifest = sub.add_parser("manifest")
    manifest.add_argument("--out", type=Path, required=True)
    manifest.set_defaults(func=cmd_manifest)

    passive = sub.add_parser("passive")
    passive.add_argument("--artifact-dir", type=Path, required=True)
    passive.add_argument("--target", type=Path, required=True)
    passive.add_argument("--route", type=Path, required=True)
    passive.add_argument("--duration", type=float, default=10.0)
    passive.add_argument(
        "--resume-pixel",
        action="store_true",
        help="finish the Mac half after a retained Pixel capture; never reacquire Pixel",
    )
    passive.set_defaults(func=cmd_passive)

    def common_active(command: argparse.ArgumentParser) -> None:
        command.add_argument("--artifact-dir", type=Path, required=True)
        command.add_argument("--label", required=True)
        command.add_argument("--order", choices=("lr", "rl"), required=True)
        command.add_argument("--geometry", required=True)

    forward = sub.add_parser("forward")
    common_active(forward)
    forward.add_argument("--target", type=Path, required=True)
    forward.add_argument("--route", type=Path, required=True)
    forward.add_argument("--mac-volume", type=int, required=True)
    forward.set_defaults(func=cmd_forward)

    reverse = sub.add_parser("reverse")
    common_active(reverse)
    reverse.add_argument("--pixel-volume", type=int, default=PIXEL_REVERSE_MEDIA_VOLUME)
    reverse.set_defaults(func=cmd_reverse)

    pixel_self = sub.add_parser("pixel-self")
    common_active(pixel_self)
    pixel_self.add_argument("--pixel-volume", type=int, default=PIXEL_SELF_MEDIA_VOLUME)
    pixel_self.set_defaults(func=cmd_pixel_self)

    mac_self = sub.add_parser("mac-self")
    common_active(mac_self)
    mac_self.add_argument("--mac-volume", type=int, required=True)
    mac_self.set_defaults(func=cmd_mac_self)

    analyze = sub.add_parser("analyze")
    analyze.add_argument("--artifact-dir", type=Path, required=True)
    analyze.set_defaults(func=cmd_analyze)

    compare = sub.add_parser("compare")
    compare.add_argument("--calibration", type=Path, required=True)
    compare.add_argument("--heldout", type=Path, required=True)
    compare.add_argument("--minimum-snr-db", type=float, default=12.0)
    compare.add_argument("--out", type=Path, required=True)
    compare.set_defaults(func=cmd_compare)
    return value


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
