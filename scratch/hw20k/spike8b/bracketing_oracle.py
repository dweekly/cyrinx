#!/usr/bin/env python3
"""Offline, expected-symbol-aided oracle for ROADMAP Spike 8b.

This module contains no hardware, ADB, playback, or recording imports. Every
candidate is explicitly non-claimable because the terminal estimate consumes
known payload symbols.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import math
from pathlib import Path
import platform
import subprocess
import sys
import time
from typing import Any, Iterable, Mapping, Sequence
import zlib

import numpy as np

HW20K = Path(__file__).resolve().parent.parent
if str(HW20K) not in sys.path:
    sys.path.insert(0, str(HW20K))

import modem  # noqa: E402


SCHEMA = "cyrinx.spike8b-bracketing-oracle.v1"
INVENTORY_SCHEMA = "cyrinx.spike8b-retained-inventory.v1"
LABEL = "non-claimable-terminal-oracle"
EXPECTED_MANIFEST_SHA256 = "0f81dc4dce591c47e7fb1aeefeb635ac49de2ce01fbf0fd7805326446668af9e"
EXPECTED_PLAN_SHA256 = "122957926f1954e20bef3de938a4d0c7a94bb8d50b475c0622a919624d05616d"
PROFILE_ID = "candidate-cp96-p16-b6-r23"
EXPECTED_CONFIG = {
    "bits_per_bin": 6,
    "code_rate": "2/3",
    "cp_samples": 96,
    "data_symbols": 128,
    "f_hi_hz": 23000.0,
    "f_lo_hz": 1100.0,
    "nfft": 2048,
    "pilot_every": 16,
    "sample_rate_hz": 48000,
}
TERMINAL_SYMBOLS = 8
METHODS = ("start-only", "complex-linear", "logmag-unwrapped-phase", "significant-tap")
VARIANTS = ("full", "late-half")
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
PREREGISTRATION = Path(__file__).resolve().parent / "PREREGISTRATION.md"


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha256_bytes(value: bytes | bytearray | memoryview) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def resolve_recorded_path(recorded: str, manifest_path: Path) -> Path:
    path = Path(recorded).expanduser()
    if path.is_absolute():
        return path.resolve(strict=True)
    candidates = []
    for root in (REPOSITORY_ROOT, *manifest_path.parents):
        candidate = root / path
        if candidate.is_file():
            candidates.append(candidate.resolve())
    unique = sorted(set(candidates))
    if not unique:
        raise FileNotFoundError(f"recorded artifact is absent: {recorded}")
    if len(unique) != 1:
        raise ValueError(f"recorded artifact is ambiguous: {recorded}: {unique}")
    return unique[0]


def verify_record(record: Mapping[str, Any], manifest_path: Path, label: str) -> Path:
    recorded = record.get("path")
    expected_hash = record.get("sha256")
    if not isinstance(recorded, str) or not isinstance(expected_hash, str):
        raise ValueError(f"{label}: incomplete artifact binding")
    path = resolve_recorded_path(recorded, manifest_path)
    observed_hash = sha256_file(path)
    if observed_hash != expected_hash:
        raise ValueError(f"{label}: SHA-256 mismatch {observed_hash} != {expected_hash}")
    byte_count = record.get("byte_count")
    if byte_count is not None and int(byte_count) != path.stat().st_size:
        raise ValueError(f"{label}: byte-count mismatch")
    return path


def profile_config(manifest: Mapping[str, Any], profile_id: str) -> dict[str, Any]:
    profiles = manifest.get("plan", {}).get("profiles", [])
    matches = [profile for profile in profiles if profile.get("profile_id") == profile_id]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one profile {profile_id}")
    return dict(matches[0])


def make_config() -> modem.Config:
    bits_per_bin = {bin_index: 6 for bin_index in range(47, 982)}
    config = modem.Config(
        f_lo=1100.0,
        f_hi=23000.0,
        pilot_every=16,
        bits_per_bin=bits_per_bin,
        rate="2/3",
        n_sym=128,
        amp=0.18,
        clip_sigma=3.3,
        nfft=2048,
        cp=96,
        sr=48000,
        track_alpha=0.0,
    )
    expected_geometry = (935, 59, 876, 5256, 215, 55040, 284864)
    actual_geometry = (
        len(config.used),
        len(config.pilot_idx),
        len(config.data_bins),
        config.bits_per_sym,
        config.n_blocks,
        config.payload_bytes,
        config.frame_samples,
    )
    if actual_geometry != expected_geometry:
        raise AssertionError(f"oracle geometry changed: {actual_geometry} != {expected_geometry}")
    return config


def _artifact_status(record: Any, manifest_path: Path, *, verify_hash: bool) -> dict[str, Any]:
    if not isinstance(record, Mapping) or not isinstance(record.get("path"), str):
        return {"present": False, "verified": False, "reason": "missing binding"}
    try:
        path = resolve_recorded_path(str(record["path"]), manifest_path)
    except (FileNotFoundError, ValueError) as error:
        return {"present": False, "verified": False, "reason": str(error)}
    observed = sha256_file(path) if verify_hash else None
    expected = record.get("sha256")
    return {
        "path": str(path),
        "present": True,
        "byte_count": path.stat().st_size,
        "recorded_sha256": expected,
        "observed_sha256": observed,
        "verified": observed == expected if verify_hash else None,
    }


def inventory_manifests(paths: Iterable[Path], *, verify_hash: bool) -> dict[str, Any]:
    records = []
    for manifest_path in sorted({path.resolve() for path in paths}):
        manifest = load_json(manifest_path)
        if manifest.get("mode") != "execute" or manifest.get("schema") != "cyrinx.goodput-ab-campaign.v1":
            continue
        schedule = manifest.get("plan", {}).get("schedule", {})
        profiles = {
            profile.get("profile_id"): profile
            for profile in manifest.get("plan", {}).get("profiles", [])
            if isinstance(profile, Mapping)
        }
        eligible_profiles = {
            profile_id
            for profile_id, profile in profiles.items()
            if profile.get("config", {}).get("data_symbols") == 128
            and float(schedule.get("inter_frame_gap_s", -1)) == 0.0
        }
        if not eligible_profiles:
            continue
        manifest_hash = sha256_file(manifest_path)
        runs = {run.get("run_id"): run for run in manifest.get("plan", {}).get("runs", [])}
        result_records = []
        for result in manifest.get("results", []):
            if result.get("profile_id") not in eligible_profiles:
                continue
            run = runs.get(result.get("run_id"), {})
            payload_records = result.get("artifacts", {}).get("expected_payloads", [])
            payload_status = [
                _artifact_status(record, manifest_path, verify_hash=verify_hash)
                for record in payload_records
            ]
            plan_payloads = {int(item["frame_index"]): item for item in run.get("payloads", [])}
            payload_plan_bound = all(
                int(record.get("frame_index", -1)) in plan_payloads
                and record.get("sha256")
                == plan_payloads[int(record.get("frame_index", -1))].get("sha256")
                and int(record.get("byte_count", -1))
                == int(plan_payloads[int(record.get("frame_index", -1))].get("byte_count", -2))
                for record in payload_records
            )
            result_records.append(
                {
                    "run_id": result.get("run_id"),
                    "profile_id": result.get("profile_id"),
                    "status": result.get("status"),
                    "capture": _artifact_status(
                        result.get("artifacts", {}).get("capture"),
                        manifest_path,
                        verify_hash=verify_hash,
                    ),
                    "transmit": _artifact_status(
                        result.get("artifacts", {}).get("transmit"),
                        manifest_path,
                        verify_hash=verify_hash,
                    ),
                    "expected_payload_count": len(payload_records),
                    "expected_payloads": payload_status,
                    "payloads_bound_to_plan": payload_plan_bound,
                    "known_symbol_provenance": (
                        "verified expected bytes -> deterministic C/Python-parity encoder"
                    ),
                }
            )
        records.append(
            {
                "manifest": str(manifest_path),
                "manifest_sha256": manifest_hash,
                "ledger_bound_source": manifest_hash == EXPECTED_MANIFEST_SHA256,
                "plan_sha256": manifest.get("plan_sha256"),
                "schedule": schedule,
                "eligible_profiles": [profiles[profile_id] for profile_id in sorted(eligible_profiles)],
                "completed_results": len(result_records),
                "results": result_records,
            }
        )
    return {
        "schema": INVENTORY_SCHEMA,
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "verify_hash": verify_hash,
        "manifests": records,
        "summary": {
            "manifest_count": len(records),
            "ledger_bound_manifest_count": sum(record["ledger_bound_source"] for record in records),
            "capture_count": sum(len(record["results"]) for record in records),
            "payload_file_count": sum(
                result["expected_payload_count"]
                for record in records
                for result in record["results"]
            ),
        },
    }


class FastViterbi:
    def __init__(self, library: Path):
        self.library_path = library.resolve(strict=True)
        self.library_sha256 = sha256_file(self.library_path)
        self.library = ctypes.CDLL(str(self.library_path))
        self.function = self.library.spike8b_viterbi
        pointer_double = ctypes.POINTER(ctypes.c_double)
        self.function.argtypes = [
            pointer_double,
            pointer_double,
            ctypes.c_size_t,
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_uint8),
        ]
        self.function.restype = ctypes.c_int

    def decode(self, llr0: np.ndarray, llr1: np.ndarray, information_bits: int) -> np.ndarray:
        first = np.ascontiguousarray(llr0, dtype=np.float64)
        second = np.ascontiguousarray(llr1, dtype=np.float64)
        if first.shape != second.shape or first.ndim != 1:
            raise ValueError("LLR streams must be equal one-dimensional arrays")
        output = np.empty(information_bits, dtype=np.uint8)
        status = self.function(
            first.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
            second.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
            len(first),
            information_bits,
            output.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
        )
        if status != 0:
            raise RuntimeError(f"fast Viterbi failed: {status}")
        return output


def build_viterbi(output: Path) -> Path:
    source = Path(__file__).resolve().parent / "viterbi.c"
    output.parent.mkdir(parents=True, exist_ok=True)
    command = ["cc", "-O3", "-dynamiclib", str(source), "-o", str(output)]
    subprocess.run(command, check=True)
    return output


def _fine_base(config: modem.Config, signal: np.ndarray, detected_sample: int) -> int:
    coarse = detected_sample + modem.CHIRP_LEN + modem.GUARD
    reference = modem.ofdm_mod_symbol(config, modem.sync_symbol_freq(config, 0))
    low = max(0, coarse - 400)
    stop = min(len(signal), coarse + 400 + config.sym)
    segment = signal[low:stop]
    correlation = np.correlate(segment, reference, mode="valid")
    if len(correlation) == 0:
        raise ValueError("capture is too short for fine synchronization")
    return low + int(np.argmax(np.abs(correlation))) - 24


def _fft_at(config: modem.Config, signal: np.ndarray, position: int) -> np.ndarray:
    body = signal[position + config.cp : position + config.sym]
    if len(body) != config.nfft:
        raise ValueError("capture is too short for an OFDM symbol")
    return np.fft.rfft(body, n=config.nfft)[config.used]


def _front_end(
    config: modem.Config, capture: np.ndarray, detected_sample: int
) -> dict[str, Any]:
    base = _fine_base(config, capture[:, 0], detected_sample)
    channels = capture.shape[1]
    if channels != 2:
        raise ValueError("Spike 8b source requires two retained microphone channels")
    channel_start = []
    noise = []
    for channel in range(channels):
        estimates = []
        for sync_index in range(2):
            received = _fft_at(config, capture[:, channel], base + sync_index * config.sym)
            estimates.append(received / modem.sync_symbol_freq(config, sync_index))
        channel_start.append((estimates[0] + estimates[1]) / 2)
        raw_noise = np.abs(estimates[0] - estimates[1]) ** 2 / 2
        noise.append(np.convolve(raw_noise, np.ones(9) / 9, mode="same") + 1e-12)
    channel_start_array = np.asarray(channel_start)
    noise_array = np.asarray(noise)
    shared = np.maximum.reduce(noise_array)
    noise_array = np.maximum(noise_array, shared[None, :] * modem.MRC_NV_RELATIVE_FLOOR)
    snr = np.sum(np.abs(channel_start_array) ** 2 / noise_array, axis=0)
    pilot_positions = np.arange(0, len(config.used), 16, dtype=int)
    if not np.array_equal(config.used[pilot_positions], config.pilot_idx):
        raise AssertionError("pilot position reconstruction changed")
    pilot_weights = modem.pilot_phase_weights(snr[pilot_positions])

    received_symbols = np.empty(
        (channels, config.n_sym, len(config.used)), dtype=np.complex128
    )
    corrections = np.empty((config.n_sym, len(config.used)), dtype=np.complex128)
    phase_records = []
    denominator = np.sum(np.abs(channel_start_array) ** 2 / noise_array, axis=0) + 1e-12
    for symbol_index in range(config.n_sym):
        for channel in range(channels):
            received_symbols[channel, symbol_index] = _fft_at(
                config,
                capture[:, channel],
                base + (2 + symbol_index) * config.sym,
            )
        numerator = np.sum(
            np.conj(channel_start_array)
            * received_symbols[:, symbol_index]
            / noise_array,
            axis=0,
        )
        equalized = numerator / denominator
        errors = equalized[pilot_positions] * np.conj(config.pilots)
        slope, common_phase = modem.fit_pilot_phase(
            errors,
            config.pilot_idx.astype(float),
            pilot_weights,
        )
        corrections[symbol_index] = np.exp(
            -1j * (common_phase + slope * (config.used - config.pilot_idx[0]))
        )
        phase_records.append({"slope_rad_per_bin": slope, "common_phase_rad": common_phase})
    return {
        "base": base,
        "H_start": channel_start_array,
        "noise": noise_array,
        "snr": snr,
        "pilot_positions": pilot_positions,
        "pilot_weights": pilot_weights,
        "received": received_symbols,
        "corrections": corrections,
        "phase_records": phase_records,
    }


def _terminal_estimate(
    config: modem.Config, front: Mapping[str, Any], known: np.ndarray
) -> tuple[np.ndarray, list[dict[str, float]]]:
    received = front["received"][:, -TERMINAL_SYMBOLS:]
    corrections = front["corrections"][-TERMINAL_SYMBOLS:]
    observations = received * corrections[None, :, :] / known[-TERMINAL_SYMBOLS:][None, :, :]
    terminal = np.median(observations.real, axis=1) + 1j * np.median(
        observations.imag, axis=1
    )
    start = front["H_start"]
    pilots = front["pilot_positions"]
    alignments = []
    for channel in range(2):
        ratio = terminal[channel, pilots] / np.where(
            np.abs(start[channel, pilots]) > 1e-12,
            start[channel, pilots],
            1e-12 + 0j,
        )
        slope, common_phase = modem.fit_pilot_phase(
            ratio,
            config.pilot_idx.astype(float),
            front["pilot_weights"],
        )
        alignment = np.exp(
            -1j * (common_phase + slope * (config.used - config.pilot_idx[0]))
        )
        terminal[channel] *= alignment
        alignments.append(
            {"removed_slope_rad_per_bin": slope, "removed_common_phase_rad": common_phase}
        )
    return terminal, alignments


def _interpolation_alpha(symbols: int, variant: str) -> np.ndarray:
    if variant == "full":
        return np.linspace(0.0, 1.0, symbols)
    if variant == "late-half":
        alpha = np.zeros(symbols)
        alpha[symbols // 2 :] = np.linspace(0.0, 1.0, symbols - symbols // 2)
        return alpha
    raise ValueError(f"unknown variant: {variant}")


def _significant_tap_endpoints(
    config: modem.Config, start: np.ndarray, terminal: np.ndarray
) -> tuple[np.ndarray, np.ndarray, list[int]]:
    start_taps = []
    terminal_taps = []
    counts = []
    for channel in range(2):
        start_spectrum = np.zeros(config.nfft // 2 + 1, dtype=np.complex128)
        terminal_spectrum = np.zeros_like(start_spectrum)
        start_spectrum[config.used] = start[channel]
        terminal_spectrum[config.used] = terminal[channel]
        first = np.fft.irfft(start_spectrum, n=config.nfft)
        last = np.fft.irfft(terminal_spectrum, n=config.nfft)
        peak = int(np.argmax(np.abs(first) ** 2 + np.abs(last) ** 2))
        window = (peak + np.arange(-24, config.cp - 24)) % config.nfft
        threshold_first = np.max(np.abs(first)) * 0.1
        threshold_last = np.max(np.abs(last)) * 0.1
        keep = np.zeros(config.nfft, dtype=bool)
        keep[window] = (np.abs(first[window]) >= threshold_first) | (
            np.abs(last[window]) >= threshold_last
        )
        first_filtered = np.where(keep, first, 0.0)
        last_filtered = np.where(keep, last, 0.0)
        start_taps.append(first_filtered)
        terminal_taps.append(last_filtered)
        counts.append(int(np.sum(keep)))
    return np.asarray(start_taps), np.asarray(terminal_taps), counts


def _channel_series(
    config: modem.Config,
    start: np.ndarray,
    terminal: np.ndarray,
    method: str,
    variant: str,
) -> tuple[np.ndarray, dict[str, Any]]:
    alpha = _interpolation_alpha(config.n_sym, variant)[:, None, None]
    if method == "start-only":
        return np.broadcast_to(start[None, :, :], (config.n_sym, *start.shape)), {}
    if method == "complex-linear":
        return start[None, :, :] * (1.0 - alpha) + terminal[None, :, :] * alpha, {}
    if method == "logmag-unwrapped-phase":
        start_magnitude = np.abs(start)
        terminal_magnitude = np.abs(terminal)
        floors = np.max(start_magnitude, axis=1, keepdims=True) * (10.0 ** (-30.0 / 20.0))
        start_safe = np.maximum(start_magnitude, floors)
        terminal_safe = np.maximum(terminal_magnitude, floors)
        log_ratio = np.log(terminal_safe / start_safe)
        safe_complex = np.where(
            start_magnitude > floors,
            start,
            floors * np.exp(1j * np.angle(start)),
        )
        phase_ratio = np.unwrap(np.angle(terminal / safe_complex), axis=1)
        series = start[None, :, :] * np.exp(
            alpha * (log_ratio[None, :, :] + 1j * phase_ratio[None, :, :])
        )
        series[0] = start
        series[-1] = terminal
        return series, {"relative_floor_db": -30.0}
    if method == "significant-tap":
        start_taps, terminal_taps, tap_counts = _significant_tap_endpoints(
            config, start, terminal
        )
        series = np.empty((config.n_sym, 2, len(config.used)), dtype=np.complex128)
        for symbol_index, value in enumerate(alpha[:, 0, 0]):
            taps = start_taps * (1.0 - value) + terminal_taps * value
            for channel in range(2):
                spectrum = np.fft.rfft(taps[channel], n=config.nfft)
                series[symbol_index, channel] = spectrum[config.used]
        if variant == "late-half":
            series[: config.n_sym // 2] = start[None, :, :]
        return series, {
            "threshold_db_below_endpoint_peak": -20.0,
            "cp_window_samples": config.cp,
            "retained_taps_per_channel": tap_counts,
        }
    raise ValueError(f"unknown method: {method}")


def _local_pilot_evm(pilot_residual: np.ndarray, used_count: int) -> np.ndarray:
    padded = np.pad(pilot_residual, (5, 5), mode="edge")
    smoothed = np.convolve(padded, np.ones(11) / 11, mode="valid")
    pilot_positions = np.arange(0, used_count, 16)
    used_positions = np.arange(used_count)
    return np.interp(used_positions, pilot_positions, smoothed, right=smoothed[-1])


def _depuncture_and_decode(
    config: modem.Config, llr_stream: np.ndarray, viterbi: FastViterbi
) -> np.ndarray:
    permutation = modem.DetRng(0x1EAF).permutation(len(llr_stream))
    llr = llr_stream[permutation]
    full_count = (config.info_bits + 6) * 2
    full = modem.depuncture_llr(llr, config.pattern, full_count)
    return viterbi.decode(full[0::2], full[1::2], config.info_bits)


def _verify_blocks(config: modem.Config, bits: np.ndarray, expected: bytes) -> dict[str, Any]:
    packed = np.packbits(bits[: config.n_blocks * (modem.CRC_BLOCK + 4) * 8]).tobytes()
    crc_mask = []
    identity_mask = []
    verified_mask = []
    for block_index in range(config.n_blocks):
        coded_start = block_index * (modem.CRC_BLOCK + 4)
        coded = packed[coded_start : coded_start + modem.CRC_BLOCK + 4]
        payload = coded[: modem.CRC_BLOCK]
        crc_valid = zlib.crc32(payload).to_bytes(4, "big") == coded[modem.CRC_BLOCK :]
        expected_start = block_index * modem.CRC_BLOCK
        byte_identical = payload == expected[expected_start : expected_start + modem.CRC_BLOCK]
        crc_mask.append(crc_valid)
        identity_mask.append(byte_identical)
        verified_mask.append(crc_valid and byte_identical)
    return {
        "crc_valid_mask": crc_mask,
        "byte_identity_mask": identity_mask,
        "verified_mask": verified_mask,
        "crc_valid_blocks": sum(crc_mask),
        "byte_identical_blocks": sum(identity_mask),
        "verified_blocks": sum(verified_mask),
        "total_blocks": config.n_blocks,
    }


def _evaluate_equalizer(
    config: modem.Config,
    front: Mapping[str, Any],
    known: np.ndarray,
    channels: np.ndarray,
    expected: bytes,
    viterbi: FastViterbi,
) -> dict[str, Any]:
    received = front["received"]
    noise = front["noise"]
    snr = front["snr"]
    corrections = front["corrections"]
    pilot_positions = front["pilot_positions"]
    data_positions = np.asarray(
        [position for position in range(len(config.used)) if position % 16 != 0], dtype=int
    )
    llr_symbols = []
    evm_sse = np.zeros(4)
    evm_count = np.zeros(4, dtype=int)
    pilot_evm_by_symbol = []
    for symbol_index in range(config.n_sym):
        channel = channels[symbol_index]
        denominator = np.sum(np.abs(channel) ** 2 / noise, axis=0) + 1e-12
        numerator = np.sum(
            np.conj(channel) * received[:, symbol_index] / noise,
            axis=0,
        )
        equalized = numerator / denominator * corrections[symbol_index]
        pilot_error = equalized[pilot_positions] * np.conj(config.pilots) - 1.0
        pilot_evm2 = np.minimum(np.abs(pilot_error) ** 2, 1e9)
        global_evm2 = float(np.mean(pilot_evm2))
        local_evm2 = _local_pilot_evm(pilot_evm2, len(config.used))
        n0 = (
            1.0 / np.maximum(snr[data_positions], 0.1)
            + 0.25 * global_evm2
            + 0.75 * local_evm2[data_positions]
        )
        llr_symbols.append(
            modem.qam_llr(equalized[data_positions], 6, n0).reshape(-1)
        )
        payload_error = equalized[data_positions] - known[symbol_index, data_positions]
        quarter = min(symbol_index // (config.n_sym // 4), 3)
        evm_sse[quarter] += float(np.sum(np.abs(payload_error) ** 2))
        evm_count[quarter] += len(payload_error)
        pilot_evm_by_symbol.append(math.sqrt(global_evm2))
    bits = _depuncture_and_decode(config, np.concatenate(llr_symbols), viterbi)
    block_result = _verify_blocks(config, bits, expected)
    quarter_evm = np.sqrt(evm_sse / evm_count)
    return {
        **block_result,
        "payload_evm_by_quarter": quarter_evm.tolist(),
        "payload_evm_sse_by_quarter": evm_sse.tolist(),
        "payload_evm_samples_by_quarter": evm_count.tolist(),
        "mean_pilot_evm": float(np.mean(pilot_evm_by_symbol)),
    }


def _load_bound_run(
    manifest_path: Path,
    run: Mapping[str, Any],
    result: Mapping[str, Any],
    config: modem.Config,
) -> tuple[np.ndarray, list[bytes], list[np.ndarray], dict[str, Any]]:
    capture_record = result.get("artifacts", {}).get("capture")
    capture_path = verify_record(capture_record, manifest_path, f"{run['run_id']} capture")
    raw = np.fromfile(capture_path, dtype="<i2")
    if raw.size % 2 != 0:
        raise ValueError("stereo capture has an odd sample count")
    capture = raw.reshape(-1, 2).astype(np.float32) / 32768.0
    if len(capture) != int(capture_record["frames"]):
        raise ValueError("capture frame count differs from binding")

    plan_payloads = {int(record["frame_index"]): record for record in run["payloads"]}
    payload_records = {
        int(record["frame_index"]): record
        for record in result.get("artifacts", {}).get("expected_payloads", [])
    }
    payloads = []
    known_symbols = []
    regenerated_frames = []
    for frame_index in range(5):
        record = payload_records[frame_index]
        if record["sha256"] != plan_payloads[frame_index]["sha256"]:
            raise ValueError("expected payload is not bound to the frozen plan")
        payload_path = verify_record(record, manifest_path, f"{run['run_id']} payload {frame_index}")
        payload = payload_path.read_bytes()
        if len(payload) != config.payload_bytes:
            raise ValueError("payload byte count differs from fixed geometry")
        taps: dict[str, Any] = {}
        regenerated_frames.append(modem.modulate_frame(config, payload, taps=taps))
        known_symbols.append(taps["data_freq"])
        payloads.append(payload)

    transmit_record = result.get("artifacts", {}).get("transmit")
    transmit_path = verify_record(transmit_record, manifest_path, f"{run['run_id']} transmit")
    transmit = np.fromfile(transmit_path, dtype="<f4")
    if transmit.size % 2 != 0:
        raise ValueError("transmit artifact has an odd stereo sample count")
    transmit = transmit.reshape(-1, 2)
    if np.count_nonzero(transmit[:, 1]) != 0:
        raise ValueError("source waveform unexpectedly drives the second speaker channel")
    regeneration_records = []
    for frame_index, start in enumerate(run["scheduled_frame_starts_tx_samples"]):
        regenerated = regenerated_frames[frame_index]
        observed = transmit[int(start) : int(start) + len(regenerated), 0]
        difference = np.abs(regenerated - observed)
        mismatch_count = int(np.count_nonzero(difference))
        maximum_difference = float(np.max(difference))
        if maximum_difference > 1e-7:
            raise ValueError(
                f"{run['run_id']} frame {frame_index} regeneration differs from transmit PCM: "
                f"maximum={maximum_difference}"
            )
        regeneration_records.append(
            {
                "frame_index": frame_index,
                "bit_identical_float32": mismatch_count == 0,
                "different_float32_samples": mismatch_count,
                "maximum_absolute_difference": maximum_difference,
            }
        )
    provenance = {
        "capture_path": str(capture_path),
        "capture_sha256": capture_record["sha256"],
        "capture_frames": len(capture),
        "transmit_path": str(transmit_path),
        "transmit_sha256": transmit_record["sha256"],
        "payload_sha256": [payload_records[index]["sha256"] for index in range(5)],
        "transmitted_wave_regeneration": regeneration_records,
        "regeneration_acceptance_tolerance": 1e-7,
    }
    return capture, payloads, known_symbols, provenance


def _source_mrc_records(result: Mapping[str, Any]) -> dict[int, Mapping[str, Any]]:
    records = result.get("decoded_diagnostics", {}).get("mrc01", [])
    automatic = result.get("decoded_diagnostics", {}).get("pilot-select01-v1", [])
    if len(records) != 5 or len(automatic) != 5:
        raise ValueError("source result lacks five MRC/automatic diagnostics")
    for source, selected in zip(records, automatic, strict=True):
        diagnostics = selected.get("automatic_diversity")
        if diagnostics.get("selected_receiver") != "mrc01":
            raise ValueError("source policy did not freeze MRC for every eligible frame")
        if selected.get("block_valid") != source.get("block_valid"):
            raise ValueError("source automatic output differs from selected MRC")
    return {int(record["frame_index"]): record for record in records}


def _gain_db(reference: float, candidate: float) -> float:
    return 20.0 * math.log10(max(reference, 1e-15) / max(candidate, 1e-15))


def _compact_masks(frame_results: Sequence[dict[str, Any]]) -> None:
    """Encode ordered masks compactly after all aggregate comparisons finish."""
    for frame in frame_results:
        for evaluation in frame["evaluations"].values():
            for name in ("crc_valid_mask", "byte_identity_mask", "verified_mask"):
                mask = np.asarray(evaluation.pop(name), dtype=np.uint8)
                evaluation[f"{name}_hex"] = np.packbits(mask, bitorder="big").tobytes().hex()
            evaluation["mask_encoding"] = {
                "bits": int(evaluation["total_blocks"]),
                "bit_order": "big within each byte",
                "padding": "zero bits after ordered block denominator",
            }


def _accounting(config: modem.Config, run: Mapping[str, Any], capture_frames: Sequence[int]) -> dict[str, Any]:
    frames = 5
    payload_bits = frames * config.payload_bytes * 8
    scheduled_samples = int(run["scheduled_span_samples"])
    trailing_samples = int(run["trailing_pad_samples"])
    gross_samples = scheduled_samples + trailing_samples
    terminal_counterfactuals = []
    for trainers in (0, 1, 2):
        samples = scheduled_samples + frames * trainers * config.sym
        terminal_counterfactuals.append(
            {
                "appended_trainers_per_frame": trainers,
                "payload_bits": payload_bits,
                "scheduled_samples": samples,
                "scheduled_seconds": samples / config.sr,
                "error_free_ceiling_bps": payload_bits * config.sr / samples,
            }
        )
    replacement_counterfactuals = []
    for trainers in (1, 2):
        data_symbols = config.n_sym - trainers
        coded_capacity = config.bits_per_sym * data_symbols
        info_bits = math.floor(coded_capacity * config.rate) - 6
        blocks = info_bits // ((modem.CRC_BLOCK + 4) * 8)
        replacement_payload_bits = frames * blocks * modem.CRC_BLOCK * 8
        replacement_counterfactuals.append(
            {
                "replaced_data_symbols_per_frame": trainers,
                "remaining_data_symbols": data_symbols,
                "crc_blocks_per_frame": blocks,
                "payload_bits": replacement_payload_bits,
                "scheduled_samples": scheduled_samples,
                "scheduled_seconds": scheduled_samples / config.sr,
                "error_free_ceiling_bps": replacement_payload_bits * config.sr / scheduled_samples,
            }
        )
    return {
        "payload_bits": payload_bits,
        "scheduled": {
            "samples": scheduled_samples,
            "seconds": scheduled_samples / config.sr,
            "source_verified_goodput_denominator": True,
        },
        "gross": {
            "samples": gross_samples,
            "seconds": gross_samples / config.sr,
            "definition": "scheduled span plus manifest trailing padding",
        },
        "acquisition": {
            "capture_samples_by_run": list(capture_frames),
            "capture_seconds_by_run": [value / config.sr for value in capture_frames],
            "definition": "complete retained capture; not the campaign gross denominator",
        },
        "session": {
            "available": False,
            "reason": "retained burst captures exclude discovery, sounding, buffering, and decode wall time",
        },
        "appended_terminal_training": terminal_counterfactuals,
        "replacement_terminal_training": replacement_counterfactuals,
    }


def analyze(manifest_path: Path, viterbi_library: Path) -> dict[str, Any]:
    if sha256_file(manifest_path) != EXPECTED_MANIFEST_SHA256:
        raise ValueError("manifest is not the ledger-bound Spike 8b source")
    manifest = load_json(manifest_path)
    if manifest.get("mode") != "execute" or manifest.get("plan_sha256") != EXPECTED_PLAN_SHA256:
        raise ValueError("manifest mode/plan binding differs from preregistration")
    if sha256_bytes(canonical_json_bytes(manifest["plan"])) != EXPECTED_PLAN_SHA256:
        raise ValueError("canonical plan hash does not match the recorded hash")
    schedule = manifest["plan"]["schedule"]
    if int(schedule["frames"]) != 5 or float(schedule["inter_frame_gap_s"]) != 0.0:
        raise ValueError("source schedule is not five-frame zero-gap")
    profile = profile_config(manifest, PROFILE_ID)
    if profile.get("config") != EXPECTED_CONFIG:
        raise ValueError(f"source profile differs from preregistration: {profile.get('config')}")
    config = make_config()
    viterbi = FastViterbi(viterbi_library)
    runs_by_id = {run["run_id"]: run for run in manifest["plan"]["runs"]}
    candidate_results = sorted(
        [result for result in manifest["results"] if result.get("profile_id") == PROFILE_ID],
        key=lambda result: result["run_id"],
    )
    if len(candidate_results) != 8 or any(result.get("status") != "complete" for result in candidate_results):
        raise ValueError("source must contain eight complete candidate runs")

    frame_results = []
    provenance_records = []
    capture_frames = []
    source_mask_mismatches = 0
    for run_number, result in enumerate(candidate_results, start=1):
        run = runs_by_id[result["run_id"]]
        print(f"[{run_number}/{len(candidate_results)}] {run['run_id']}", flush=True)
        capture, payloads, known_frames, provenance = _load_bound_run(
            manifest_path, run, result, config
        )
        provenance_records.append({"run_id": run["run_id"], **provenance})
        capture_frames.append(len(capture))
        source_records = _source_mrc_records(result)
        detections = {int(item["frame_index"]): item for item in result["timing"]["detections"]}
        for frame_index in range(5):
            front = _front_end(config, capture, int(detections[frame_index]["detected_sample"]))
            known = known_frames[frame_index]
            terminal, alignments = _terminal_estimate(config, front, known)
            evaluations: dict[str, Any] = {}
            baseline_channels, baseline_parameters = _channel_series(
                config, front["H_start"], terminal, "start-only", "full"
            )
            baseline = _evaluate_equalizer(
                config, front, known, baseline_channels, payloads[frame_index], viterbi
            )
            source_mask = [bool(value) for value in source_records[frame_index]["block_valid"]]
            mismatches = sum(
                observed != expected
                for observed, expected in zip(baseline["verified_mask"], source_mask, strict=True)
            )
            source_mask_mismatches += mismatches
            evaluations["start-only/full"] = {
                **baseline,
                "parameters": baseline_parameters,
                "source_c_mask_mismatches": mismatches,
                "source_c_valid_blocks": sum(source_mask),
            }
            for method in METHODS[1:]:
                for variant in VARIANTS:
                    channel_series, parameters = _channel_series(
                        config,
                        front["H_start"],
                        terminal,
                        method,
                        variant,
                    )
                    evaluation = _evaluate_equalizer(
                        config,
                        front,
                        known,
                        channel_series,
                        payloads[frame_index],
                        viterbi,
                    )
                    evaluations[f"{method}/{variant}"] = {
                        **evaluation,
                        "parameters": parameters,
                    }
            frame_results.append(
                {
                    "run_id": run["run_id"],
                    "run_number": run_number,
                    "frame_index": frame_index,
                    "detected_sample": int(detections[frame_index]["detected_sample"]),
                    "fine_base_sample": int(front["base"]),
                    "terminal_alignment": alignments,
                    "evaluations": evaluations,
                }
            )

    aggregate: dict[str, Any] = {}
    baseline_key = "start-only/full"
    baseline_invalid = sum(
        evaluation is False
        for frame in frame_results
        for evaluation in frame["evaluations"][baseline_key]["verified_mask"]
    )
    baseline_valid = 8 * 5 * config.n_blocks - baseline_invalid
    baseline_sse = sum(
        frame["evaluations"][baseline_key]["payload_evm_sse_by_quarter"][3]
        for frame in frame_results
    )
    baseline_count = sum(
        frame["evaluations"][baseline_key]["payload_evm_samples_by_quarter"][3]
        for frame in frame_results
    )
    baseline_late_evm = math.sqrt(baseline_sse / baseline_count)
    scheduled_samples_total = sum(
        int(runs_by_id[result["run_id"]]["scheduled_span_samples"])
        for result in candidate_results
    )
    gross_samples_total = sum(
        int(runs_by_id[result["run_id"]]["scheduled_span_samples"])
        + int(runs_by_id[result["run_id"]]["trailing_pad_samples"])
        for result in candidate_results
    )
    for key in [baseline_key] + [
        f"{method}/{variant}" for method in METHODS[1:] for variant in VARIANTS
    ]:
        recovered = 0
        regressed = 0
        valid = 0
        late_sse = 0.0
        late_count = 0
        middle_sse = 0.0
        middle_count = 0
        first_sse = 0.0
        first_count = 0
        per_frame_late_gain = []
        for frame in frame_results:
            base = frame["evaluations"][baseline_key]
            candidate = frame["evaluations"][key]
            recovered += sum(
                not old and new
                for old, new in zip(base["verified_mask"], candidate["verified_mask"], strict=True)
            )
            regressed += sum(
                old and not new
                for old, new in zip(base["verified_mask"], candidate["verified_mask"], strict=True)
            )
            valid += candidate["verified_blocks"]
            candidate_sse = candidate["payload_evm_sse_by_quarter"]
            candidate_count = candidate["payload_evm_samples_by_quarter"]
            late_sse += candidate_sse[3]
            late_count += candidate_count[3]
            middle_sse += candidate_sse[1] + candidate_sse[2]
            middle_count += candidate_count[1] + candidate_count[2]
            first_sse += candidate_sse[0] + candidate_sse[1]
            first_count += candidate_count[0] + candidate_count[1]
            per_frame_late_gain.append(
                _gain_db(
                    base["payload_evm_by_quarter"][3],
                    candidate["payload_evm_by_quarter"][3],
                )
            )
        late_evm = math.sqrt(late_sse / late_count)
        middle_evm = math.sqrt(middle_sse / middle_count)
        first_evm = math.sqrt(first_sse / first_count)
        baseline_middle = math.sqrt(
            sum(
                sum(frame["evaluations"][baseline_key]["payload_evm_sse_by_quarter"][1:3])
                for frame in frame_results
            )
            / sum(
                sum(frame["evaluations"][baseline_key]["payload_evm_samples_by_quarter"][1:3])
                for frame in frame_results
            )
        )
        baseline_first = math.sqrt(
            sum(
                sum(frame["evaluations"][baseline_key]["payload_evm_sse_by_quarter"][0:2])
                for frame in frame_results
            )
            / sum(
                sum(frame["evaluations"][baseline_key]["payload_evm_samples_by_quarter"][0:2])
                for frame in frame_results
            )
        )
        aggregate[key] = {
            "verified_blocks": valid,
            "total_blocks": 8 * 5 * config.n_blocks,
            "scheduled_goodput_bps": (
                valid * modem.CRC_BLOCK * 8 * config.sr / scheduled_samples_total
            ),
            "gross_goodput_bps": (
                valid * modem.CRC_BLOCK * 8 * config.sr / gross_samples_total
            ),
            "recovered_start_only_invalid_blocks": recovered,
            "regressed_start_only_valid_blocks": regressed,
            "net_recovered_blocks": recovered - regressed,
            "start_only_invalid_block_denominator": baseline_invalid,
            "lost_block_recovery_fraction": recovered / baseline_invalid if baseline_invalid else None,
            "late_quartile_payload_evm": late_evm,
            "aggregate_late_quartile_evm_gain_db": _gain_db(baseline_late_evm, late_evm),
            "median_frame_late_quartile_evm_gain_db": float(np.median(per_frame_late_gain)),
            "middle_half_payload_evm": middle_evm,
            "middle_half_evm_gain_db": _gain_db(baseline_middle, middle_evm),
            "first_half_payload_evm": first_evm,
            "first_half_evm_gain_db": _gain_db(baseline_first, first_evm),
        }
    qualifying = []
    for key, value in aggregate.items():
        if key == baseline_key:
            continue
        if key.endswith("/late-half") and (
            value["lost_block_recovery_fraction"] >= 0.5
            or value["aggregate_late_quartile_evm_gain_db"] >= 3.0
        ):
            qualifying.append(key)
    full_candidates = [
        (key, value) for key, value in aggregate.items() if key.endswith("/full") and key != baseline_key
    ]
    best_full_key, best_full = max(
        full_candidates,
        key=lambda item: (
            item[1]["aggregate_late_quartile_evm_gain_db"],
            item[1]["net_recovered_blocks"],
        ),
    )
    stop_reasons = []
    if not qualifying:
        stop_reasons.append("no late-half intervention cleared 50% block recovery or 3 dB late-EVM gain")
    if best_full["first_half_evm_gain_db"] < -0.5:
        stop_reasons.append("best full interpolation regressed first-half EVM by more than 0.5 dB")
    if best_full["middle_half_evm_gain_db"] <= 0.0:
        stop_reasons.append("best full interpolation did not improve midpoint EVM")
    if source_mask_mismatches:
        stop_reasons.append(
            f"Python start-only block map differs from source C in {source_mask_mismatches} positions"
        )
    proceed = bool(qualifying) and not stop_reasons
    accounting = _accounting(
        config,
        runs_by_id[candidate_results[0]["run_id"]],
        capture_frames,
    )
    _compact_masks(frame_results)
    return {
        "schema": SCHEMA,
        "label": LABEL,
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "preregistration": {
            "path": str(PREREGISTRATION.relative_to(REPOSITORY_ROOT)),
            "sha256": sha256_file(PREREGISTRATION),
        },
        "source": {
            "manifest": str(manifest_path.resolve()),
            "manifest_sha256": EXPECTED_MANIFEST_SHA256,
            "plan_sha256": EXPECTED_PLAN_SHA256,
            "profile_id": PROFILE_ID,
            "profile_config": EXPECTED_CONFIG,
            "candidate_runs": 8,
            "frames": 40,
            "source_receiver": "mrc01 selected by frozen pilot-select01-v1 in 40/40 frames",
        },
        "runtime": {
            "python": sys.version,
            "platform": platform.platform(),
            "numpy": np.__version__,
            "analysis_source_sha256": sha256_file(Path(__file__)),
            "viterbi_source_sha256": sha256_file(Path(__file__).resolve().parent / "viterbi.c"),
            "fast_viterbi_sha256": viterbi.library_sha256,
        },
        "leakage_statement": (
            "H_end and payload EVM consume verified expected symbols; all candidates are "
            "non-claimable oracle diagnostics and cannot be promoted as receivers"
        ),
        "front_end": {
            "timing": "recorded chirp detection plus known-sync fine correlation",
            "phase_tracking": "start-only pilot slope/CPE frozen and reused for every candidate",
            "receiver_selection": "source MRC choice frozen before terminal oracle",
            "decision_directed_tracking": False,
            "source_c_mask_mismatches": source_mask_mismatches,
        },
        "provenance": provenance_records,
        "aggregate": aggregate,
        "decision": {
            "oracle_proceed_threshold": (
                ">=50% recovered start-only-invalid blocks for a late-half-only intervention "
                "OR >=3 dB aggregate late-quartile payload-EVM gain"
            ),
            "qualifying_late_half_candidates": qualifying,
            "best_full_candidate": best_full_key,
            "proceed_to_new_ota_terminal_training": proceed,
            "stop_reasons": stop_reasons,
            "claimable_receiver_result": False,
        },
        "accounting": accounting,
        "frames": frame_results,
    }


def acquisition_manifest(result: Mapping[str, Any]) -> dict[str, Any]:
    best = result["decision"]["best_full_candidate"]
    proceed = bool(result["decision"]["proceed_to_new_ota_terminal_training"])
    return {
        "schema": "cyrinx.spike8b-ota-acquisition-plan.v1",
        "status": "oracle-gate-passed-not-executed" if proceed else "blocked-by-offline-oracle-gate",
        "authorization": (
            "requires a separate bench owner and new preregistration"
            if proceed
            else "not authorized: preregistered offline proceed threshold failed"
        ),
        "source_oracle_manifest_sha256": result["source"]["manifest_sha256"],
        "source_oracle_result_sha256": sha256_bytes(canonical_json_bytes(result)),
        "candidate": {
            "estimator": best,
            "terminal_trainers_per_frame": [1, 2],
            "data_symbols": [64, 128],
            "cp_samples": 96,
            "pilot_every": 16,
            "bits_per_bin": 6,
            "code_rate": "2/3",
            "inter_frame_gap_samples": 0,
        },
        "screen": {
            "pairs": 2,
            "order": "balanced",
            "control": "start-only, same waveform with terminal trainer ignored",
            "primary_metric": "strict ordered scheduled goodput with terminal training in denominator",
            "stop": "<25% recovery of control-lost blocks, first-half regression, or no midpoint prediction",
        },
        "note": "This executable plan is intentionally not a playback command.",
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    inventory = subparsers.add_parser("inventory")
    inventory.add_argument("--search-root", type=Path, required=True)
    inventory.add_argument("--verify-hashes", action="store_true")
    inventory.add_argument("--out", type=Path, required=True)
    build = subparsers.add_parser("build-viterbi")
    build.add_argument("--out", type=Path, required=True)
    analysis = subparsers.add_parser("analyze")
    analysis.add_argument("--manifest", type=Path, required=True)
    analysis.add_argument("--viterbi-library", type=Path, required=True)
    analysis.add_argument("--out", type=Path, required=True)
    analysis.add_argument("--acquisition-out", type=Path, required=True)
    return parser.parse_args(argv)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "inventory":
        paths = args.search_root.glob("*sym128*.manifest.json")
        output = inventory_manifests(paths, verify_hash=args.verify_hashes)
        write_json(args.out, output)
        print(json.dumps(output["summary"], sort_keys=True))
        return 0
    if args.command == "build-viterbi":
        print(build_viterbi(args.out))
        return 0
    if args.command == "analyze":
        output = analyze(args.manifest, args.viterbi_library)
        write_json(args.out, output)
        write_json(args.acquisition_out, acquisition_manifest(output))
        print(json.dumps(output["decision"], sort_keys=True))
        return 0
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
