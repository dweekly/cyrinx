#!/usr/bin/env python3
"""Offline Rank 5a capacity/predictor spike.

This tool consumes retained Rank 5 PCM without modifying it.  It reconstructs
the portable-C receiver's actual max-log LLR stream in Python, checks the
resulting ordered CRC mask against the retained C result, and only then
calculates bitwise GMI.  Passive-noise and repeated-pilot Shannon arithmetic
remain explicitly descriptive upper bounds.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import sys
from typing import Any
import zlib

import numpy as np


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import modem as M  # noqa: E402


SR = 48_000
NFFT = 2_048
CP = 768
F_LO = 1_100.0
F_HI = 23_000.0
N_SYM = 64
PILOT_EVERY = 8
BITS_PER_BIN = 4
FRAME_SECONDS = 4.0
SCHEDULED_CEILING_BPS = 25_600.0
MARGIN_SAMPLES = round(0.20 * SR)
PREREG_EXPECTED_SHA256 = "ece043f5c94e96643ac3e9235525a594c7d7450942438b3f74781c860d357fbd"
RUN_DIRECTORIES = {
    "F0": "F0-forward-vol30-lr",
    "R1": "R1-reverse-vol25-lr",
    "R2": "R2-reverse-vol25-rl",
    "S-M": "S-M-mac-self-vol30-lr",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def config() -> M.Config:
    initial = M.Config(
        F_LO,
        F_HI,
        pilot_every=PILOT_EVERY,
        rate="1/2",
        n_sym=N_SYM,
        nfft=NFFT,
        cp=CP,
        sr=SR,
    )
    return M.Config(
        F_LO,
        F_HI,
        pilot_every=PILOT_EVERY,
        bits_per_bin={int(bin_index): BITS_PER_BIN for bin_index in initial.data_idx},
        rate="1/2",
        n_sym=N_SYM,
        nfft=NFFT,
        cp=CP,
        sr=SR,
    )


def expected_payload(cfg: M.Config) -> bytes:
    return bytes((index * 31 + 7) & 0xFF for index in range(cfg.payload_bytes))


def transmitted_interleaved_bits(cfg: M.Config, payload: bytes) -> np.ndarray:
    taps: dict[str, Any] = {}
    M.modulate_frame(cfg, payload, taps=taps)
    return np.asarray(taps["interleaved_bits"], dtype=np.uint8)


def raw_inventory(root: Path) -> list[dict[str, Any]]:
    values = []
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        stat = path.stat()
        values.append(
            {
                "path": str(path.relative_to(root)),
                "bytes": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "mode_octal": oct(stat.st_mode & 0o777),
                "sha256": sha256_file(path),
            }
        )
    return values


def assert_inventory_unchanged(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> None:
    if before != after:
        raise RuntimeError("read-only Rank 5 raw-input inventory changed during analysis")


def load_capture(run_root: Path, acquisition: dict[str, Any]) -> np.ndarray:
    record = acquisition["capture_artifact"]
    path = run_root / Path(record["path"]).name
    observed_hash = sha256_file(path)
    if observed_hash != record["sha256"]:
        raise RuntimeError(f"capture hash mismatch for {path}: {observed_hash} != {record['sha256']}")
    channels = int(record["channels"])
    if path.name.endswith("s16le.pcm"):
        raw = np.fromfile(path, dtype="<i2")
        values = raw.astype(np.float32) / 32768.0
    else:
        values = np.fromfile(path, dtype="<f4")
    if len(values) % channels:
        raise RuntimeError(f"capture has incomplete interleaved frame: {path}")
    return np.ascontiguousarray(values.reshape(-1, channels))


def find_path(analysis: dict[str, Any], speaker: int) -> dict[str, Any]:
    matches = [path for path in analysis["paths"] if int(path["speaker"]) == speaker]
    if len(matches) != 1:
        raise RuntimeError(f"expected one analysis path for speaker {speaker}, got {len(matches)}")
    return matches[0]


def find_evm_segment(acquisition: dict[str, Any], speaker: int) -> dict[str, Any]:
    matches = [
        segment
        for segment in acquisition["program"]["segments"]
        if int(segment["speaker"]) == speaker and segment["probe"] == "random-16qam-r12-evm"
    ]
    if len(matches) != 1:
        raise RuntimeError(f"expected one EVM segment for speaker {speaker}, got {len(matches)}")
    return matches[0]


def probe_slice(
    capture: np.ndarray,
    acquisition: dict[str, Any],
    analysis: dict[str, Any],
    speaker: int,
) -> np.ndarray:
    mapping = analysis["sample_mapping_by_receiver"][0]
    offset = float(mapping["offset_samples"])
    scale = float(mapping["sample_scale"])
    segment = find_evm_segment(acquisition, speaker)
    lo = max(0, math.floor(offset + scale * int(segment["start_sample"])) - MARGIN_SAMPLES)
    hi = min(
        len(capture),
        math.ceil(offset + scale * int(segment["end_sample"])) + MARGIN_SAMPLES,
    )
    retained_origin = int(find_path(analysis, speaker)["evm_capture_origin"])
    if lo != retained_origin:
        raise RuntimeError(f"reconstructed EVM origin {lo} != retained origin {retained_origin}")
    return np.ascontiguousarray(capture[lo:hi])


def _local_pilot_noise(pilot_evm2: np.ndarray, pilot_every: int, positions: np.ndarray) -> np.ndarray:
    padded = np.pad(np.asarray(pilot_evm2), (5, 5), mode="edge")
    smooth = np.convolve(padded, np.ones(11) / 11.0, mode="valid")
    left = positions // pilot_every
    result = np.empty(len(positions), dtype=float)
    for index, (position, left_index) in enumerate(zip(positions, left)):
        if left_index >= len(smooth) - 1:
            result[index] = smooth[-1]
        else:
            fraction = (position - left_index * pilot_every) / pilot_every
            result[index] = smooth[left_index] * (1.0 - fraction) + smooth[left_index + 1] * fraction
    return result


def receiver_llrs(cfg: M.Config, rx: np.ndarray, rx2: np.ndarray | None = None) -> dict[str, Any]:
    """Reconstruct portable-C receiver-semantics-v1 LLRs from one probe slice."""

    primary = np.asarray(rx, dtype=np.float64)
    secondary = None if rx2 is None else np.asarray(rx2, dtype=np.float64)
    start, chirp_score = M.find_chirp(primary, chirp=cfg.chirp_wave)
    coarse_base = start + M.CHIRP_LEN + M.GUARD
    reference = M.ofdm_mod_symbol(cfg, M.sync_symbol_freq(cfg, 0))
    lo = max(0, coarse_base - 400)
    hi = min(len(primary), coarse_base + 400 + cfg.sym)
    matched = np.correlate(primary[lo:hi], reference, mode="valid")
    base = lo + int(np.argmax(np.abs(matched))) - 24
    required_stop = base + (2 + cfg.n_sym) * cfg.sym
    if base < 0 or required_stop > len(primary) or (secondary is not None and required_stop > len(secondary)):
        raise RuntimeError("probe slice is too short after receiver synchronization")

    signals = [primary] + ([] if secondary is None else [secondary])

    def fft_at(signal: np.ndarray, symbol_start: int) -> np.ndarray:
        window = signal[symbol_start + cfg.cp : symbol_start + cfg.cp + cfg.nfft]
        if len(window) != cfg.nfft:
            raise RuntimeError("short OFDM FFT window")
        return np.fft.rfft(window)[cfg.used]

    channels = []
    noise_variances = []
    for signal in signals:
        h0 = fft_at(signal, base) / M.sync_symbol_freq(cfg, 0)
        h1 = fft_at(signal, base + cfg.sym) / M.sync_symbol_freq(cfg, 1)
        channels.append((h0 + h1) / 2.0)
        raw_noise = np.abs(h0 - h1) ** 2 / 2.0
        noise_variances.append(np.convolve(raw_noise, np.ones(9) / 9.0, mode="same") + 1e-12)

    use_mrc = len(signals) == 2
    if use_mrc:
        shared = np.maximum(noise_variances[0], noise_variances[1])
        noise_variances = [np.maximum(noise, shared * 1e-4) for noise in noise_variances]
    snr = sum(np.abs(channel) ** 2 / noise for channel, noise in zip(channels, noise_variances))

    used_positions = np.arange(len(cfg.used))
    pilot_positions = used_positions[::PILOT_EVERY]
    data_positions = used_positions[used_positions % PILOT_EVERY != 0]
    pilot_bins = cfg.pilot_idx.astype(float)
    pilot_weights = M.pilot_phase_weights(snr[pilot_positions])
    llr_stream = np.empty(cfg.bits_per_sym * cfg.n_sym, dtype=float)
    llr_position = 0
    symbol_pilot_evm = []
    for symbol in range(cfg.n_sym):
        spectra = [fft_at(signal, base + (2 + symbol) * cfg.sym) for signal in signals]
        if not use_mrc:
            equalized = spectra[0] / channels[0]
        else:
            numerator = sum(
                np.conj(channel) * spectrum / noise
                for channel, spectrum, noise in zip(channels, spectra, noise_variances)
            )
            denominator = sum(
                np.abs(channel) ** 2 / noise
                for channel, noise in zip(channels, noise_variances)
            )
            equalized = numerator / (denominator + 1e-12)
        pilot_error = equalized[pilot_positions] * np.conj(cfg.pilots)
        slope, common_phase = M.fit_pilot_phase(
            pilot_error,
            pilot_bins,
            pilot_weights,
        )
        correction = np.exp(-1j * (common_phase + slope * (cfg.used - pilot_bins[0])))
        equalized *= correction
        corrected_pilots = equalized[pilot_positions] * np.conj(cfg.pilots)
        pilot_evm2 = np.abs(corrected_pilots - 1.0) ** 2
        pilot_evm2 = np.nan_to_num(pilot_evm2, nan=1e9, posinf=1e9, neginf=1e9)
        pilot_evm2 = np.clip(pilot_evm2, 0.0, 1e9)
        global_evm2 = float(np.mean(pilot_evm2))
        symbol_pilot_evm.append(math.sqrt(global_evm2))
        local_evm2 = _local_pilot_noise(pilot_evm2, PILOT_EVERY, data_positions)
        data_noise = (
            1.0 / np.maximum(snr[data_positions], 0.1)
            + 0.25 * global_evm2
            + 0.75 * local_evm2
        )
        data_values = equalized[data_positions]
        symbol_llrs = M.qam_llr(data_values, BITS_PER_BIN, data_noise).reshape(-1)
        stop = llr_position + len(symbol_llrs)
        llr_stream[llr_position:stop] = symbol_llrs
        llr_position = stop
    if llr_position != len(llr_stream):
        raise AssertionError(f"LLR count {llr_position} != configured capacity {len(llr_stream)}")

    permutation = M.DetRng(0x1EAF).permutation(len(llr_stream))
    deinterleaved = llr_stream[permutation]
    full_count = (cfg.info_bits + 6) * 2
    depunctured = M.depuncture_llr(deinterleaved, cfg.pattern, full_count)
    decoded_bits = M.viterbi_decode(depunctured[0::2], depunctured[1::2], cfg.info_bits)
    framed_bytes = np.packbits(
        decoded_bits[: cfg.n_blocks * (M.CRC_BLOCK + 4) * 8]
    ).tobytes()
    block_valid = []
    decoded_payload = bytearray()
    for block in range(cfg.n_blocks):
        framed = framed_bytes[block * (M.CRC_BLOCK + 4) : (block + 1) * (M.CRC_BLOCK + 4)]
        valid = zlib.crc32(framed[: M.CRC_BLOCK]).to_bytes(4, "big") == framed[M.CRC_BLOCK :]
        block_valid.append(bool(valid))
        decoded_payload.extend(framed[: M.CRC_BLOCK])
    return {
        "llrs": llr_stream,
        "block_valid": block_valid,
        "decoded_payload": bytes(decoded_payload),
        "chirp_score": float(chirp_score),
        "sync_start": int(start),
        "fft_base": int(base),
        "sync_snr_db_median": float(10.0 * np.log10(max(float(np.median(snr)), 1e-12))),
        "pilot_evm_mean": float(np.mean(symbol_pilot_evm)),
    }


def strict_mask_and_payload(
    analysis_path: dict[str, Any],
    receiver_index: int | None,
    mrc: bool,
) -> tuple[list[bool], int, int]:
    if mrc:
        probe = analysis_path["mrc_evm_probe"]
    else:
        matches = [
            receiver
            for receiver in analysis_path["receivers"]
            if int(receiver["receiver"]) == int(receiver_index)
        ]
        if len(matches) != 1:
            raise RuntimeError("retained analysis receiver missing")
        probe = matches[0]["evm_probe"]
    mask = [bool(value) for value in probe["block_valid"]]
    return mask, int(probe["strict_ordered_verified_blocks"]), int(probe["strict_ordered_verified_bytes"])


def gmi_for_scale(bits: np.ndarray, llrs: np.ndarray, scale: float) -> float:
    signs = 1.0 - 2.0 * np.asarray(bits, dtype=float)
    penalty = np.logaddexp(0.0, -scale * signs * np.asarray(llrs, dtype=float)) / math.log(2.0)
    return float(1.0 - np.mean(penalty))


def optimized_gmi(bits: np.ndarray, llrs: np.ndarray) -> tuple[float, float, float]:
    """Return (s=1 GMI, optimized GMI, common scale) with deterministic search."""

    at_one = gmi_for_scale(bits, llrs, 1.0)
    left = 0.0
    right = 10.0
    golden = (math.sqrt(5.0) - 1.0) / 2.0
    x1 = right - golden * (right - left)
    x2 = left + golden * (right - left)
    f1 = gmi_for_scale(bits, llrs, x1)
    f2 = gmi_for_scale(bits, llrs, x2)
    for _ in range(42):
        if f1 < f2:
            left = x1
            x1 = x2
            f1 = f2
            x2 = left + golden * (right - left)
            f2 = gmi_for_scale(bits, llrs, x2)
        else:
            right = x2
            x2 = x1
            f2 = f1
            x1 = right - golden * (right - left)
            f1 = gmi_for_scale(bits, llrs, x1)
    scale = x1 if f1 >= f2 else x2
    value = max(0.0, min(1.0, max(f1, f2)))
    return at_one, value, scale


def awgn_gmi_curve(symbol_count: int = 50_000, seed: int = 20_260_718) -> list[dict[str, float]]:
    rng = np.random.default_rng(seed)
    bits = rng.integers(0, 2, size=(symbol_count, BITS_PER_BIN), dtype=np.uint8)
    symbols = M.qam_map(bits, BITS_PER_BIN)
    unit_noise = (
        rng.normal(size=symbol_count) + 1j * rng.normal(size=symbol_count)
    ) / math.sqrt(2.0)
    result = []
    for snr_db in np.arange(-10.0, 40.0 + 0.125, 0.25):
        n0 = 10.0 ** (-float(snr_db) / 10.0)
        received = symbols + math.sqrt(n0) * unit_noise
        llrs = M.qam_llr(received, BITS_PER_BIN, n0).reshape(-1)
        result.append(
            {
                "snr_db": float(snr_db),
                "gmi_per_bit": max(0.0, min(1.0, gmi_for_scale(bits.reshape(-1), llrs, 1.0))),
            }
        )
    return result


def equivalent_sinr(gmi: float, curve: list[dict[str, float]]) -> dict[str, Any]:
    values = np.asarray([point["gmi_per_bit"] for point in curve])
    snrs = np.asarray([point["snr_db"] for point in curve])
    monotonic = np.maximum.accumulate(values)
    if gmi <= monotonic[0]:
        return {"sinr_db": float(snrs[0]), "censoring": "at-or-below-grid"}
    if gmi >= monotonic[-1]:
        return {"sinr_db": float(snrs[-1]), "censoring": "at-or-above-grid"}
    return {"sinr_db": float(np.interp(gmi, monotonic, snrs)), "censoring": "none"}


def passive_fft_statistics(raw_root: Path, cfg: M.Config) -> dict[str, Any]:
    passive_root = raw_root / "P0-passive"
    passive = read_json(passive_root / "passive-analysis.json")

    pixel_path = passive_root / Path(passive["pixel"]["artifact"]["path"]).name
    mac_path = passive_root / Path(passive["mac"]["artifact"]["path"]).name
    if sha256_file(pixel_path) != passive["pixel"]["artifact"]["sha256"]:
        raise RuntimeError("Pixel passive PCM hash mismatch")
    if sha256_file(mac_path) != passive["mac"]["artifact"]["sha256"]:
        raise RuntimeError("Mac passive PCM hash mismatch")

    pixel_raw = np.fromfile(pixel_path, dtype="<i2")
    pixel = pixel_raw.reshape(-1, 2).astype(np.float64) / 32768.0
    mac = np.fromfile(mac_path, dtype="<f4").astype(np.float64)[:, None]

    def spectra(values: np.ndarray) -> np.ndarray:
        centered = values - np.mean(values, axis=0, keepdims=True)
        record_count = len(centered) // cfg.nfft
        records = centered[: record_count * cfg.nfft].reshape(record_count, cfg.nfft, values.shape[1])
        return np.fft.rfft(records, axis=1)[:, cfg.used, :]

    pixel_fft = spectra(pixel)
    mac_fft = spectra(mac)
    pixel_covariance = np.einsum(
        "bkm,bkn->kmn",
        pixel_fft,
        np.conj(pixel_fft),
    ) / pixel_fft.shape[0]
    mac_psd = np.mean(np.abs(mac_fft[:, :, 0]) ** 2, axis=0)
    mono_floor = max(1e-18, 1e-6 * float(np.median(mac_psd)))
    return {
        "pixel_covariance": pixel_covariance,
        "mac_psd": np.maximum(mac_psd, mono_floor),
        "pixel_records": int(pixel_fft.shape[0]),
        "mac_records": int(mac_fft.shape[0]),
        "mono_psd_floor": mono_floor,
        "pixel_pcm_sha256": passive["pixel"]["artifact"]["sha256"],
        "mac_pcm_sha256": passive["mac"]["artifact"]["sha256"],
    }


def stationary_shannon_mono(h: np.ndarray, noise_psd: np.ndarray, cfg: M.Config) -> tuple[float, np.ndarray]:
    floor = max(1e-18, 1e-6 * float(np.median(noise_psd)))
    snr = np.abs(h) ** 2 / np.maximum(noise_psd, floor)
    capacity = float(np.sum(np.log2(1.0 + snr)) * cfg.sr / cfg.sym)
    return capacity, snr


def stationary_shannon_simo(
    channels: np.ndarray,
    noise_covariance: np.ndarray,
    cfg: M.Config,
) -> tuple[float, np.ndarray]:
    snr = np.empty(len(cfg.used), dtype=float)
    for bin_index in range(len(cfg.used)):
        covariance = np.asarray(noise_covariance[bin_index], dtype=complex)
        loading = max(1e-18, 1e-3 * float(np.trace(covariance).real) / channels.shape[0])
        loaded = covariance + loading * np.eye(channels.shape[0])
        h = channels[:, bin_index]
        snr[bin_index] = max(0.0, float(np.vdot(h, np.linalg.solve(loaded, h)).real))
    capacity = float(np.sum(np.log2(1.0 + snr)) * cfg.sr / cfg.sym)
    return capacity, snr


def sounder_channel(analysis_path: dict[str, Any], receiver: int) -> np.ndarray:
    matches = [
        item for item in analysis_path["receivers"] if int(item["receiver"]) == receiver
    ]
    if len(matches) != 1:
        raise RuntimeError("sounder receiver missing")
    sounder = matches[0]["sounder"]
    return np.asarray(sounder["H_real"]) + 1j * np.asarray(sounder["H_imag"])


def repeated_pilot_capacity(analysis_path: dict[str, Any], receiver: int) -> float:
    matches = [
        item for item in analysis_path["receivers"] if int(item["receiver"]) == receiver
    ]
    if len(matches) != 1:
        raise RuntimeError("sounder receiver missing")
    return float(matches[0]["sounder"]["descriptive_repeated_pilot_capacity_bps"])


def analyze_observation(
    *,
    observation_id: str,
    direction: str,
    run: str,
    speaker: int,
    receiver_index: int,
    split: str,
    acquisition: dict[str, Any],
    analysis: dict[str, Any],
    capture: np.ndarray,
    cfg: M.Config,
    tx_bits: np.ndarray,
    passive: dict[str, Any],
    curve: list[dict[str, float]],
    mrc: bool = False,
) -> dict[str, Any]:
    path = find_path(analysis, speaker)
    probe = probe_slice(capture, acquisition, analysis, speaker)
    if mrc:
        receiver = receiver_llrs(cfg, probe[:, 0], probe[:, 1])
        channels = np.stack([sounder_channel(path, 0), sounder_channel(path, 1)])
        shannon_bps, shannon_snr = stationary_shannon_simo(
            channels,
            passive["pixel_covariance"],
            cfg,
        )
        repeated_bps = None
    else:
        receiver = receiver_llrs(cfg, probe[:, receiver_index])
        channel = sounder_channel(path, receiver_index)
        if direction == "Mac-to-Pixel":
            pixel_noise = np.real(passive["pixel_covariance"][:, receiver_index, receiver_index])
            shannon_bps, shannon_snr = stationary_shannon_mono(channel, pixel_noise, cfg)
        else:
            shannon_bps, shannon_snr = stationary_shannon_mono(channel, passive["mac_psd"], cfg)
        repeated_bps = repeated_pilot_capacity(path, receiver_index)

    retained_mask, strict_blocks, strict_bytes = strict_mask_and_payload(
        path,
        None if mrc else receiver_index,
        mrc,
    )
    mask_parity = receiver["block_valid"] == retained_mask
    if not mask_parity:
        raise RuntimeError(f"actual-LLR Python/C block-mask parity failed for {observation_id}")
    payload = expected_payload(cfg)
    for block_index, valid in enumerate(retained_mask):
        if not valid:
            continue
        lo = block_index * M.CRC_BLOCK
        hi = lo + M.CRC_BLOCK
        if receiver["decoded_payload"][lo:hi] != payload[lo:hi]:
            raise RuntimeError(f"CRC-valid block does not match expected position for {observation_id}")

    gmi_s1, gmi, gmi_scale = optimized_gmi(tx_bits, receiver["llrs"])
    effective = equivalent_sinr(gmi, curve)
    configured_rate = cfg.info_bits / (cfg.bits_per_sym * cfg.n_sym)
    gmi_selected = gmi >= configured_rate
    shannon_selected = shannon_bps >= SCHEDULED_CEILING_BPS
    repeated_selected = None if repeated_bps is None else repeated_bps >= SCHEDULED_CEILING_BPS
    recovery = strict_blocks / cfg.n_blocks
    observed_bps = strict_bytes * 8.0 / FRAME_SECONDS
    measurement_ns = int(acquisition["wall_clock_playback_start_ns"])
    frozen = dt.datetime.fromisoformat("2026-07-18T19:28:19+00:00")
    measured = dt.datetime.fromtimestamp(measurement_ns / 1e9, tz=dt.timezone.utc)
    stable_h = run in ("R1", "R2")
    return {
        "id": observation_id,
        "direction": direction,
        "run": run,
        "speaker": speaker,
        "receiver": "mrc01" if mrc else f"mic{receiver_index}",
        "split": split,
        "is_scalar_input_simo": mrc,
        "strict": {
            "ordered_block_mask": retained_mask,
            "blocks_verified": strict_blocks,
            "blocks_total": cfg.n_blocks,
            "recovery_fraction": recovery,
            "bytes_verified": strict_bytes,
            "observed_probe_goodput_bps": observed_bps,
            "python_llr_to_portable_c_mask_parity": mask_parity,
        },
        "protocol": {
            "scheduled_payload_ceiling_bps": SCHEDULED_CEILING_BPS,
            "configured_info_bit_rate": configured_rate,
            "gmi_margin": gmi - configured_rate,
        },
        "predictors": {
            "passive_stationary_shannon_upper_bound_bps": shannon_bps,
            "passive_stationary_shannon_median_snr_db": float(
                10.0 * np.log10(max(float(np.median(shannon_snr)), 1e-12))
            ),
            "passive_stationary_shannon_selects_profile": shannon_selected,
            "repeated_pilot_log2_arithmetic_bps": repeated_bps,
            "repeated_pilot_arithmetic_selects_profile": repeated_selected,
            "actual_llr_gmi_s1_per_bit": gmi_s1,
            "actual_llr_gmi_optimized_per_bit": gmi,
            "actual_llr_gmi_common_scale": gmi_scale,
            "actual_llr_gmi_selects_profile": gmi_selected,
            "effective_sinr_db": effective["sinr_db"],
            "effective_sinr_censoring": effective["censoring"],
        },
        "receiver_diagnostics": {
            "chirp_score": receiver["chirp_score"],
            "sync_start_in_slice": receiver["sync_start"],
            "fft_base_in_slice": receiver["fft_base"],
            "sync_snr_db_median": receiver["sync_snr_db_median"],
            "pilot_evm_mean": receiver["pilot_evm_mean"],
            "llr_abs_median": float(np.median(np.abs(receiver["llrs"]))),
            "llr_abs_p10": float(np.percentile(np.abs(receiver["llrs"]), 10.0)),
        },
        "measurement_inventory": {
            "actual_randomized_known_qam": True,
            "randomized_qam_payload_sha256": "3d33a8692fdab11b792184efbd86a322fa0d2145336c68a381e6db0093db9384",
            "receiver_matched_actual_llrs": mask_parity,
            "llr_provenance": "Offline reconstruction of receiver-semantics-v1 LLRs; the C API does not expose LLRs, so exact ordered block-mask parity is required.",
            "passive_noise_covariance_available": True,
            "passive_noise_covariance_kind": (
                "P0 Pixel full 2x2 per-bin covariance"
                if direction == "Mac-to-Pixel"
                else "P0 Mac scalar per-bin covariance"
            ),
            "active_h_k_available": True,
            "active_h_k_repeat_available": stable_h,
            "stable_h_k_qualified": False,
            "stable_h_k_reason": (
                "R1/R2 repeat exists, but only 33.7% (speaker 0) or 44.2% (speaker 1) of bins cleared the repeat gate and output-route provenance is incomplete."
                if stable_h
                else "No held-out active repeat exists."
            ),
            "strict_ordered_block_mask_available": True,
        },
        "confidence_and_provenance": {
            "confidence": "descriptive-low",
            "measurement_age_at_preregistration_s": max(0.0, (frozen - measured).total_seconds()),
            "active_h_repeat_available": stable_h,
            "active_h_repeat_limitation": (
                "R1/R2 are order-balanced repeats but lack strict realized-output-route provenance"
                if stable_h
                else "only one active matrix exists for this path"
            ),
            "noise_relation": "same-campaign passive capture; not simultaneous with QAM probe",
            "acquisition_sha256": analysis["acquisition_sha256"],
            "capture_sha256": analysis["capture_sha256"],
        },
    }


def average_ranks(values: list[float]) -> np.ndarray:
    array = np.asarray(values, dtype=float)
    order = np.argsort(array, kind="mergesort")
    ranks = np.empty(len(array), dtype=float)
    cursor = 0
    while cursor < len(array):
        stop = cursor + 1
        while stop < len(array) and array[order[stop]] == array[order[cursor]]:
            stop += 1
        ranks[order[cursor:stop]] = (cursor + stop - 1) / 2.0 + 1.0
        cursor = stop
    return ranks


def spearman(values: list[float], outcomes: list[float]) -> float | None:
    if len(values) < 3:
        return None
    left = average_ranks(values)
    right = average_ranks(outcomes)
    if float(np.std(left)) == 0.0 or float(np.std(right)) == 0.0:
        return None
    return float(np.corrcoef(left, right)[0, 1])


def summarize_rule(observations: list[dict[str, Any]], key: str) -> dict[str, Any]:
    usable = [obs for obs in observations if obs["predictors"].get(key) is not None]
    errors = []
    false_positive = []
    catastrophic = []
    false_negative = []
    optimism_over_20 = []
    for observation in usable:
        selected = bool(observation["predictors"][key])
        predicted = SCHEDULED_CEILING_BPS if selected else 0.0
        actual = observation["strict"]["observed_probe_goodput_bps"]
        recovery = observation["strict"]["recovery_fraction"]
        errors.append(abs(predicted - actual))
        if selected and recovery < 0.98:
            false_positive.append(observation["id"])
        if selected and recovery < 0.90:
            catastrophic.append(observation["id"])
        if not selected and recovery >= 0.98:
            false_negative.append(observation["id"])
        if predicted > actual and (
            actual == 0.0 or (predicted - actual) / actual > 0.20
        ):
            optimism_over_20.append(observation["id"])
    return {
        "observations": len(usable),
        "mean_absolute_goodput_error_bps": float(np.mean(errors)) if errors else None,
        "median_absolute_goodput_error_bps": float(np.median(errors)) if errors else None,
        "false_positive_below_98pct": false_positive,
        "catastrophic_optimistic_below_90pct": catastrophic,
        "false_negative_at_or_above_98pct": false_negative,
        "optimistic_by_more_than_20pct_or_zero_actual": optimism_over_20,
    }


def aggregate(observations: list[dict[str, Any]], cfg: M.Config) -> dict[str, Any]:
    mono = [observation for observation in observations if not observation["is_scalar_input_simo"]]
    outcomes = [observation["strict"]["recovery_fraction"] for observation in mono]
    correlation = {
        "passive_stationary_shannon": spearman(
            [observation["predictors"]["passive_stationary_shannon_upper_bound_bps"] for observation in mono],
            outcomes,
        ),
        "repeated_pilot_arithmetic": spearman(
            [observation["predictors"]["repeated_pilot_log2_arithmetic_bps"] for observation in mono],
            outcomes,
        ),
        "actual_llr_gmi": spearman(
            [observation["predictors"]["actual_llr_gmi_optimized_per_bit"] for observation in mono],
            outcomes,
        ),
        "effective_sinr": spearman(
            [observation["predictors"]["effective_sinr_db"] for observation in mono],
            outcomes,
        ),
    }
    heldout = [observation for observation in mono if observation["split"] == "held-out-descriptive"]
    return {
        "schema": "cyrinx.rank5a-capacity-predictor-summary.v1",
        "status": "BENCH-SPIKE_NOT_PROMOTED",
        "observation_counts": {
            "all": len(observations),
            "primary_unique_mono": len(mono),
            "scalar_input_simo_diagnostic": len(observations) - len(mono),
            "heldout_paths": len(heldout),
            "independent_heldout_physical_cells": 1 if heldout else 0,
            "heldout_devices": 1 if heldout else 0,
        },
        "exact_schedule": {
            "occupied_bins": len(cfg.used),
            "pilot_bins": len(cfg.pilot_idx),
            "data_bins": len(cfg.data_bins),
            "bits_per_symbol": cfg.bits_per_sym,
            "capacity_coded_bits": cfg.bits_per_sym * cfg.n_sym,
            "info_bits": cfg.info_bits,
            "configured_info_bit_rate": cfg.info_bits / (cfg.bits_per_sym * cfg.n_sym),
            "payload_bytes": cfg.payload_bytes,
            "frame_samples": cfg.frame_samples,
            "frame_seconds": cfg.airtime_s,
            "scheduled_payload_ceiling_bps": cfg.payload_bytes * 8.0 / cfg.airtime_s,
            "components": "4096-sample chirp + 2048-sample guard + 2 sync and 64 data symbols, each 2048+768 samples; no inter-frame gap",
        },
        "spearman_primary_mono": correlation,
        "selection_rules_primary_mono": {
            "passive_stationary_shannon": summarize_rule(
                mono, "passive_stationary_shannon_selects_profile"
            ),
            "repeated_pilot_arithmetic": summarize_rule(
                mono, "repeated_pilot_arithmetic_selects_profile"
            ),
            "actual_llr_gmi": summarize_rule(mono, "actual_llr_gmi_selects_profile"),
        },
        "heldout_R2_descriptive": {
            "ids": [observation["id"] for observation in heldout],
            "note": "Two speaker paths from one correlated physical run; no model was fitted, and n=2 is too small for a meaningful held-out rank statistic.",
            "selection_rules": {
                "passive_stationary_shannon": summarize_rule(
                    heldout, "passive_stationary_shannon_selects_profile"
                ),
                "repeated_pilot_arithmetic": summarize_rule(
                    heldout, "repeated_pilot_arithmetic_selects_profile"
                ),
                "actual_llr_gmi": summarize_rule(heldout, "actual_llr_gmi_selects_profile"),
            },
        },
        "qualification": {
            "roadmap_gate_evaluable": False,
            "classification": "QUALIFICATION_GAP",
            "reasons": [
                "Only one held-out physical run is available, below the preregistered five-cell/two-device minimum.",
                "F0 and Mac-self have no held-out active repeat, so H[k] stability is not established.",
                "R1/R2 lack a strict realized Pixel output-route signature.",
                "Only one fixed 16-QAM rate-1/2 profile was probed, so within-10%-of-oracle profile choice cannot be scored.",
                "The 50-block outcomes within a frame are not independent physical cells.",
            ],
            "adaptation_promotion_permitted": False,
        },
    }


def observation_plan() -> list[dict[str, Any]]:
    plan = []
    for speaker in (0, 1):
        for receiver in (0, 1):
            plan.append(
                {
                    "observation_id": f"F0-S{speaker}-R{receiver}",
                    "direction": "Mac-to-Pixel",
                    "run": "F0",
                    "speaker": speaker,
                    "receiver_index": receiver,
                    "split": "training-screen-descriptive",
                    "mrc": False,
                }
            )
        plan.append(
            {
                "observation_id": f"F0-S{speaker}-MRC01",
                "direction": "Mac-to-Pixel",
                "run": "F0",
                "speaker": speaker,
                "receiver_index": 0,
                "split": "training-screen-descriptive",
                "mrc": True,
            }
        )
    for run, split in (("R1", "calibration-descriptive"), ("R2", "held-out-descriptive")):
        for speaker in (0, 1):
            plan.append(
                {
                    "observation_id": f"{run}-S{speaker}-R0",
                    "direction": "Pixel-to-Mac",
                    "run": run,
                    "speaker": speaker,
                    "receiver_index": 0,
                    "split": split,
                    "mrc": False,
                }
            )
    for speaker in (0, 1):
        plan.append(
            {
                "observation_id": f"SM-S{speaker}-R0",
                "direction": "Mac-self",
                "run": "S-M",
                "speaker": speaker,
                "receiver_index": 0,
                "split": "descriptive-only",
                "mrc": False,
            }
        )
    return plan


def execute(raw_root: Path, preregistration: Path, out: Path) -> dict[str, Any]:
    prereg_hash = sha256_file(preregistration)
    if prereg_hash != PREREG_EXPECTED_SHA256:
        raise RuntimeError(
            f"preregistration hash {prereg_hash} != frozen {PREREG_EXPECTED_SHA256}"
        )
    inventory_before = raw_inventory(raw_root)
    cfg = config()
    if cfg.frame_samples != 192_000 or cfg.payload_bytes != 12_800:
        raise RuntimeError("probe schedule differs from preregistration")
    payload = expected_payload(cfg)
    if hashlib.sha256(payload).hexdigest() != "3d33a8692fdab11b792184efbd86a322fa0d2145336c68a381e6db0093db9384":
        raise RuntimeError("known randomized-QAM payload hash mismatch")
    tx_bits = transmitted_interleaved_bits(cfg, payload)
    passive = passive_fft_statistics(raw_root, cfg)
    curve = awgn_gmi_curve()

    run_data = {}
    for run, directory in RUN_DIRECTORIES.items():
        root = raw_root / directory
        acquisition = read_json(root / "acquisition.json")
        analysis = read_json(root / "analysis.json")
        if sha256_file(root / "acquisition.json") != analysis["acquisition_sha256"]:
            raise RuntimeError(f"acquisition/analysis hash mismatch for {run}")
        run_data[run] = {
            "acquisition": acquisition,
            "analysis": analysis,
            "capture": load_capture(root, acquisition),
        }

    observations = []
    for item in observation_plan():
        run = run_data[item["run"]]
        observations.append(
            analyze_observation(
                **item,
                acquisition=run["acquisition"],
                analysis=run["analysis"],
                capture=run["capture"],
                cfg=cfg,
                tx_bits=tx_bits,
                passive=passive,
                curve=curve,
            )
        )
    summary = aggregate(observations, cfg)
    inventory_after = raw_inventory(raw_root)
    assert_inventory_unchanged(inventory_before, inventory_after)

    inventory_doc = {
        "schema": "cyrinx.rank5a-raw-input-inventory.v1",
        "root": str(raw_root),
        "read_only_policy": "No writer opened any path under root; before/after size, mtime, mode and SHA-256 inventories are identical.",
        "unchanged_after_analysis": True,
        "files": inventory_before,
    }
    result = {
        "schema": "cyrinx.rank5a-capacity-predictor-results.v1",
        "preregistration_sha256": prereg_hash,
        "analysis_implementation": {
            "repository_base": "27b148a2664d47c0ab3dae13d4d6d4887ef63e5f",
            "script": "scratch/hw20k/rank5a_capacity_predictor.py",
            "script_sha256": sha256_file(Path(__file__)),
            "python_version": sys.version.split()[0],
            "numpy_version": np.__version__,
        },
        "raw_input_inventory_sha256": hashlib.sha256(
            json.dumps(inventory_before, sort_keys=True).encode("utf-8")
        ).hexdigest(),
        "receiver_semantics": {
            "source": "Python reconstruction of portable-C receiver semantics v1",
            "local_pilot_window": 11,
            "global_pilot_evm_weight": 0.25,
            "local_pilot_evm_weight": 0.75,
            "snr_floor": 0.1,
            "actual_llr_gate": "ordered Python block mask must equal retained portable-C block mask for every observation",
            "all_observations_passed_mask_parity": all(
                observation["strict"]["python_llr_to_portable_c_mask_parity"]
                for observation in observations
            ),
        },
        "passive_noise": {
            "pixel_fft_records": passive["pixel_records"],
            "mac_fft_records": passive["mac_records"],
            "pixel_pcm_sha256": passive["pixel_pcm_sha256"],
            "mac_pcm_sha256": passive["mac_pcm_sha256"],
            "warning": "Campaign-stationary digital receiver noise; not simultaneous probe noise, calibrated SPL, or a broadly valid environment model.",
        },
        "awgn_equivalent_curve": {
            "seed": 20_260_718,
            "symbols_per_point": 50_000,
            "snr_min_db": -10.0,
            "snr_max_db": 40.0,
            "snr_step_db": 0.25,
            "points": curve,
        },
        "observations": observations,
        "summary": summary,
    }
    write_json(out / "raw-input-inventory.json", inventory_doc)
    write_json(out / "results.json", result)
    write_json(out / "summary.json", summary)
    return result


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--raw-root", type=Path, required=True)
    value.add_argument("--preregistration", type=Path, required=True)
    value.add_argument("--out", type=Path, required=True)
    return value


def main() -> int:
    args = parser().parse_args()
    result = execute(
        args.raw_root.resolve(strict=True),
        args.preregistration.resolve(strict=True),
        args.out.resolve(),
    )
    print(json.dumps(result["summary"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
