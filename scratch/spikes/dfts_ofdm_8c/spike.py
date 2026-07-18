#!/usr/bin/env python3
"""Offline Spike 8c localized DFT-spread OFDM experiment.

This file has no hardware imports. Its deciding configuration is read from the
committed preregistration.json beside it. The candidate is localized
DFT-spread OFDM, not an SC-FDE implementation.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import platform
import sys
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "scratch" / "hw20k"))
import modem as M  # noqa: E402

PREREG_PATH = HERE / "preregistration.json"
RESULTS_DIR = HERE / "results"
SCHEMA = "cyrinx.spike-8c-dfts-ofdm-results.v1"


@dataclass(frozen=True)
class Geometry:
    nfft: int
    cp: int
    used: np.ndarray
    pilot_positions: np.ndarray
    data_positions: np.ndarray
    data_symbols: int
    bits_per_slot: int
    peak: float
    clip_sigma: float

    @property
    def symbol_samples(self) -> int:
        return self.nfft + self.cp

    @property
    def bit_capacity(self) -> int:
        return len(self.data_positions) * self.bits_per_slot * self.data_symbols


@dataclass
class ArmResult:
    raw_crest_db: float
    limited_crest_db: float
    whole_body_crest_db: float
    mean: float
    rms: float
    mean_square: float
    evm_rms: float
    hard_probe_bit_errors: int
    hard_probe_bits: int
    punctured_llr: np.ndarray
    first_symbol: np.ndarray
    tx_seconds: float
    rx_seconds: float


def load_preregistration() -> dict[str, Any]:
    document = json.loads(PREREG_PATH.read_text())
    if document["schema"] != "cyrinx.spike-8c-dfts-ofdm-preregistration.v1":
        raise ValueError("unexpected preregistration schema")
    if document["status"] != "frozen-before-candidate-outcome-inspection":
        raise ValueError("preregistration is not frozen")
    return document


def geometry(document: dict[str, Any]) -> Geometry:
    phy = document["phy"]
    used = np.arange(phy["bin_lo"], phy["bin_hi"] + 1, dtype=np.int64)
    pilot_positions = np.arange(0, len(used), phy["pilot_every"], dtype=np.int64)
    mask = np.ones(len(used), dtype=bool)
    mask[pilot_positions] = False
    data_positions = np.flatnonzero(mask)
    result = Geometry(
        nfft=phy["nfft"],
        cp=phy["cp_samples"],
        used=used,
        pilot_positions=pilot_positions,
        data_positions=data_positions,
        data_symbols=phy["data_symbols"],
        bits_per_slot=phy["bits_per_data_slot"],
        peak=document["normalization"]["peak"],
        clip_sigma=3.3,
    )
    if len(used) != phy["used_bins"]:
        raise ValueError("used-bin count disagrees with preregistration")
    if len(pilot_positions) != phy["pilot_slots"]:
        raise ValueError("pilot count disagrees with preregistration")
    if len(data_positions) != phy["data_slots"]:
        raise ValueError("data count disagrees with preregistration")
    if result.bit_capacity != phy["coded_capacity_bits"]:
        raise ValueError("coded capacity disagrees with preregistration")
    return result


def qam64_lookup() -> np.ndarray:
    labels = np.arange(64, dtype=np.uint8)
    bits = ((labels[:, None] >> np.arange(5, -1, -1)) & 1).astype(np.uint8)
    return M.qam_map(bits, 6)


def frame_seed(master_seed: int, cell_id: str, local_index: int) -> int:
    domain = (
        f"cyrinx-spike-8c-v1\0{master_seed}\0{cell_id}\0{local_index}"
    ).encode("ascii")
    return int.from_bytes(hashlib.sha256(domain).digest()[:8], "big")


def vectorized_conv_encode(bits: np.ndarray) -> np.ndarray:
    """Exact vector form of modem.conv_encode, including six zero tail bits."""
    source = np.concatenate([np.asarray(bits, dtype=np.uint8), np.zeros(6, np.uint8)])
    outputs = []
    for generator in (M.G0, M.G1):
        parity = np.zeros(len(source), dtype=np.uint8)
        for delay in range(7):
            if (generator >> (6 - delay)) & 1:
                if delay == 0:
                    parity ^= source
                else:
                    parity[delay:] ^= source[:-delay]
        outputs.append(parity)
    encoded = np.empty(2 * len(source), dtype=np.uint8)
    encoded[0::2] = outputs[0]
    encoded[1::2] = outputs[1]
    return encoded


def coded_probe(rng: np.random.Generator) -> tuple[bytes, np.ndarray]:
    payload = rng.integers(0, 256, M.CRC_BLOCK, dtype=np.uint8).tobytes()
    stream = payload + zlib.crc32(payload).to_bytes(4, "big")
    info = np.unpackbits(np.frombuffer(stream, dtype=np.uint8))
    encoded = vectorized_conv_encode(info)
    punctured = M.puncture(encoded, M.PUNCTURE["2/3"][0])
    return stream, punctured


def insert_probe(
    labels: np.ndarray,
    punctured: np.ndarray,
    seed: int,
    bit_capacity: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    positions = (seed % bit_capacity + np.arange(len(punctured), dtype=np.int64) * 65537) % bit_capacity
    if len(np.unique(positions)) != len(positions):
        raise ValueError("coded-probe affine placement collided")
    label_indices = positions // 6
    bit_indices = positions % 6
    flat = labels.reshape(-1)
    for bit_index in range(6):
        selected = np.flatnonzero(bit_indices == bit_index)
        indices = label_indices[selected]
        shift = 5 - bit_index
        mask = np.uint8(1 << shift)
        flat[indices] &= np.uint8(0xFF ^ int(mask))
        flat[indices] |= punctured[selected].astype(np.uint8) << np.uint8(shift)
    return positions, label_indices, bit_indices


def modulate_payload(
    logical: np.ndarray,
    geom: Geometry,
    candidate: bool,
) -> tuple[np.ndarray, np.ndarray, float, float, float]:
    started = time.perf_counter()
    physical = np.fft.fft(logical, axis=1, norm="ortho") if candidate else logical
    spectrum = np.zeros((geom.data_symbols, geom.nfft // 2 + 1), dtype=np.complex128)
    spectrum[:, geom.used] = physical
    body = np.fft.irfft(spectrum, n=geom.nfft, axis=1)
    with_cp = np.concatenate([body[:, -geom.cp :], body], axis=1).reshape(-1)
    raw_rms = float(np.sqrt(np.mean(np.square(with_cp))))
    raw_crest = 20.0 * math.log10(float(np.max(np.abs(with_cp))) / raw_rms)
    limited = np.clip(with_cp, -geom.clip_sigma * raw_rms, geom.clip_sigma * raw_rms)
    normalization_scale = geom.peak / float(np.max(np.abs(limited)))
    normalized = limited * normalization_scale
    limited_rms = float(np.sqrt(np.mean(np.square(normalized))))
    limited_crest = 20.0 * math.log10(geom.peak / limited_rms)
    return normalized, normalized[: geom.symbol_samples], normalization_scale, raw_crest, limited_crest


def sparse_channel(samples: np.ndarray, taps: list[list[float]]) -> np.ndarray:
    maximum_delay = max(int(delay) for delay, _ in taps)
    output = np.zeros(len(samples) + maximum_delay, dtype=np.float64)
    for delay_value, gain_value in taps:
        delay = int(delay_value)
        output[delay : delay + len(samples)] += float(gain_value) * samples
    return output


def raw_sync(geom: Geometry) -> tuple[np.ndarray, np.ndarray, float]:
    frequency = np.stack([M.sync_symbol_freq(_modem_config(geom), index) for index in range(2)])
    spectrum = np.zeros((2, geom.nfft // 2 + 1), dtype=np.complex128)
    spectrum[:, geom.used] = frequency
    body = np.fft.irfft(spectrum, n=geom.nfft, axis=1)
    symbols = np.concatenate([body[:, -geom.cp :], body], axis=1).reshape(-1)
    scale = geom.peak / float(np.max(np.abs(symbols)))
    return symbols * scale, frequency, scale


def _modem_config(geom: Geometry) -> M.Config:
    return M.Config(
        f_lo=float(geom.used[0]) * 48000.0 / geom.nfft,
        f_hi=float(geom.used[-1]) * 48000.0 / geom.nfft,
        pilot_every=16,
        bits_per_bin={int(bin_index): 6 for bin_index in geom.used if bin_index not in set(geom.used[geom.pilot_positions])},
        rate="2/3",
        n_sym=geom.data_symbols,
        amp=geom.peak,
        clip_sigma=geom.clip_sigma,
        nfft=geom.nfft,
        cp=geom.cp,
        sr=48000,
    )


def extract_ffts(received: np.ndarray, geom: Geometry) -> np.ndarray:
    total_symbols = 2 + geom.data_symbols
    blocks = np.empty((total_symbols, geom.nfft), dtype=np.float64)
    for symbol_index in range(total_symbols):
        start = symbol_index * geom.symbol_samples + geom.cp
        blocks[symbol_index] = received[start : start + geom.nfft]
    return np.fft.rfft(blocks, n=geom.nfft, axis=1)


def demodulate(
    received: np.ndarray,
    geom: Geometry,
    candidate: bool,
    sync_frequency: np.ndarray,
    sync_scale: float,
    payload_scale: float,
    pilots: np.ndarray,
    transmitted_data: np.ndarray,
    label_indices: np.ndarray,
    bit_indices: np.ndarray,
    punctured_bits: np.ndarray,
) -> tuple[float, int, np.ndarray, float]:
    started = time.perf_counter()
    spectra = extract_ffts(received, geom)
    sync_observed = spectra[:2, geom.used]
    sync_expected = sync_frequency * sync_scale
    estimates = sync_observed / sync_expected
    channel = np.mean(estimates, axis=0)
    denominator = np.square(np.abs(channel))
    floor = max(1e-12, float(np.median(denominator)) * 1e-6)
    equalized_physical = (
        spectra[2:, geom.used] * np.conj(channel)[None, :] / (denominator[None, :] + floor)
    ) / payload_scale
    recovered = (
        np.fft.ifft(equalized_physical, axis=1, norm="ortho")
        if candidate
        else equalized_physical
    )

    corrected = np.empty_like(recovered)
    pilot_noise = np.empty(geom.data_symbols, dtype=np.float64)
    for symbol_index in range(geom.data_symbols):
        pilot_observed = recovered[symbol_index, geom.pilot_positions]
        correction = np.mean(pilot_observed * np.conj(pilots))
        if not np.isfinite(correction) or abs(correction) < 1e-12:
            raise FloatingPointError("invalid pilot correction")
        corrected[symbol_index] = recovered[symbol_index] / correction
        residual = corrected[symbol_index, geom.pilot_positions] - pilots
        pilot_noise[symbol_index] = max(1e-6, float(np.mean(np.square(np.abs(residual)))))

    recovered_data = corrected[:, geom.data_positions]
    error = recovered_data - transmitted_data
    evm_rms = float(np.sqrt(np.mean(np.square(np.abs(error)))))

    flat_data = recovered_data.reshape(-1)
    selected = flat_data[label_indices]
    selected_noise = pilot_noise[label_indices // len(geom.data_positions)]
    all_llrs = M.qam_llr(selected, 6, selected_noise)
    punctured_llr = all_llrs[np.arange(len(bit_indices)), bit_indices]
    hard = (punctured_llr < 0).astype(np.uint8)
    hard_errors = int(np.count_nonzero(hard != punctured_bits))
    return evm_rms, hard_errors, punctured_llr.astype(np.float32), time.perf_counter() - started


def crest_db(samples: np.ndarray) -> float:
    rms = float(np.sqrt(np.mean(np.square(samples))))
    return 20.0 * math.log10(float(np.max(np.abs(samples))) / rms)


def run_frame(
    document: dict[str, Any],
    geom: Geometry,
    cell: dict[str, Any],
    local_index: int,
    sync: np.ndarray,
    sync_frequency: np.ndarray,
    sync_scale: float,
    pilots: np.ndarray,
    lookup: np.ndarray,
) -> tuple[bytes, ArmResult, ArmResult]:
    seed = frame_seed(document["randomization"]["master_seed"], cell["id"], local_index)
    rng = np.random.default_rng(seed)
    stream, punctured = coded_probe(rng)
    labels = rng.integers(
        0,
        64,
        size=(geom.data_symbols, len(geom.data_positions)),
        dtype=np.uint8,
    )
    _, label_indices, bit_indices = insert_probe(labels, punctured, seed, geom.bit_capacity)
    data_values = lookup[labels]
    logical = np.empty((geom.data_symbols, len(geom.used)), dtype=np.complex128)
    logical[:, geom.pilot_positions] = pilots
    logical[:, geom.data_positions] = data_values

    arm_tx: dict[str, tuple[np.ndarray, np.ndarray, float, float, float, float]] = {}
    for arm_name, candidate in (("incumbent", False), ("candidate", True)):
        started = time.perf_counter()
        payload, first_symbol, scale, raw_cf, limited_cf = modulate_payload(logical, geom, candidate)
        tx_seconds = time.perf_counter() - started
        complete = np.concatenate([sync, payload])
        arm_tx[arm_name] = (payload, first_symbol, scale, raw_cf, limited_cf, tx_seconds)

    taps = cell["taps"]
    incumbent_full = sparse_channel(np.concatenate([sync, arm_tx["incumbent"][0]]), taps)
    candidate_full = sparse_channel(np.concatenate([sync, arm_tx["candidate"][0]]), taps)
    incumbent_payload_channel = sparse_channel(arm_tx["incumbent"][0], taps)
    noise_rms = float(np.sqrt(np.mean(np.square(incumbent_payload_channel)))) / (
        10.0 ** (float(cell["snr_db"]) / 20.0)
    )
    noise = rng.normal(0.0, noise_rms, len(incumbent_full))

    results: dict[str, ArmResult] = {}
    for arm_name, candidate, channelled in (
        ("incumbent", False, incumbent_full),
        ("candidate", True, candidate_full),
    ):
        payload, first_symbol, scale, raw_cf, limited_cf, tx_seconds = arm_tx[arm_name]
        received = channelled + noise
        evm, hard_errors, llr, rx_seconds = demodulate(
            received,
            geom,
            candidate,
            sync_frequency,
            sync_scale,
            scale,
            pilots,
            data_values,
            label_indices,
            bit_indices,
            punctured,
        )
        complete = np.concatenate([sync, payload])
        results[arm_name] = ArmResult(
            raw_crest_db=raw_cf,
            limited_crest_db=limited_cf,
            whole_body_crest_db=crest_db(complete),
            mean=float(np.mean(payload)),
            rms=float(np.sqrt(np.mean(np.square(payload)))),
            mean_square=float(np.mean(np.square(payload))),
            evm_rms=evm,
            hard_probe_bit_errors=hard_errors,
            hard_probe_bits=len(punctured),
            punctured_llr=llr,
            first_symbol=first_symbol,
            tx_seconds=tx_seconds,
            rx_seconds=rx_seconds,
        )
    return stream, results["incumbent"], results["candidate"]


def batch_viterbi_crc(llrs: np.ndarray, streams: list[bytes]) -> np.ndarray:
    """Decode the frozen one-block probes in one vectorized batch."""
    pattern = np.resize(np.asarray(M.PUNCTURE["2/3"][0], dtype=bool), 4172)
    if int(np.count_nonzero(pattern)) != llrs.shape[1]:
        raise ValueError("unexpected punctured probe length")
    batch = len(llrs)
    full = np.zeros((batch, 4172), dtype=np.float32)
    full[:, pattern] = llrs
    llr0 = full[:, 0::2]
    llr1 = full[:, 1::2]

    predecessors = np.empty((M.NSTATES, 2), dtype=np.int64)
    predecessor_bits = np.empty((M.NSTATES, 2), dtype=np.uint8)
    output0 = np.empty((M.NSTATES, 2), dtype=np.uint8)
    output1 = np.empty((M.NSTATES, 2), dtype=np.uint8)
    counts = np.zeros(M.NSTATES, dtype=np.int64)
    for state in range(M.NSTATES):
        for bit in (0, 1):
            next_state = int(M._NEXT[state, bit])
            slot = counts[next_state]
            predecessors[next_state, slot] = state
            predecessor_bits[next_state, slot] = bit
            output0[next_state, slot] = M._OUT0[state, bit]
            output1[next_state, slot] = M._OUT1[state, bit]
            counts[next_state] += 1
    if not np.all(counts == 2):
        raise AssertionError("trellis predecessor construction failed")

    metrics = np.full((batch, M.NSTATES), -1e30, dtype=np.float32)
    metrics[:, 0] = 0.0
    back = np.empty((llr0.shape[1], batch, M.NSTATES), dtype=np.uint8)
    state_axis = np.arange(M.NSTATES)[None, :]
    for step in range(llr0.shape[1]):
        previous = metrics[:, predecessors]
        branch = (
            np.where(output0[None, :, :] == 0, 0.5 * llr0[:, step, None, None], -0.5 * llr0[:, step, None, None])
            + np.where(output1[None, :, :] == 0, 0.5 * llr1[:, step, None, None], -0.5 * llr1[:, step, None, None])
        )
        candidates = previous + branch
        choices = np.argmax(candidates, axis=2).astype(np.uint8)
        back[step] = choices
        metrics = candidates[np.arange(batch)[:, None], state_axis, choices]

    decoded = np.empty((batch, llr0.shape[1]), dtype=np.uint8)
    states = np.zeros(batch, dtype=np.int64)
    rows = np.arange(batch)
    for step in range(llr0.shape[1] - 1, -1, -1):
        choices = back[step, rows, states]
        decoded[:, step] = predecessor_bits[states, choices]
        states = predecessors[states, choices]

    packed = np.packbits(decoded[:, :2080], axis=1)
    recovered = np.zeros(batch, dtype=bool)
    for index in range(batch):
        block = packed[index].tobytes()
        recovered[index] = (
            block == streams[index]
            and zlib.crc32(block[: M.CRC_BLOCK]).to_bytes(4, "big") == block[M.CRC_BLOCK :]
        )
    return recovered


def quantiles(values: np.ndarray) -> dict[str, float]:
    return {
        "median": float(np.quantile(values, 0.5)),
        "p90": float(np.quantile(values, 0.9)),
        "p99": float(np.quantile(values, 0.99)),
        "p99_9": float(np.quantile(values, 0.999)),
    }


def spectral_metrics(power: np.ndarray, sample_rate: int = 48000) -> dict[str, float]:
    frequencies = np.fft.rfftfreq(8192, 1.0 / sample_rate)
    normalized = power / max(float(np.sum(power)), 1e-300)
    in_band = (frequencies >= 1100.0) & (frequencies <= 23000.0)
    out_band = ~in_band
    guard = (frequencies < 1000.0) | (frequencies > 23100.0)
    ratio = float(np.sum(normalized[out_band])) / max(float(np.sum(normalized[in_band])), 1e-300)
    return {
        "out_of_band_to_in_band_db": 10.0 * math.log10(max(ratio, 1e-300)),
        "guard_peak_normalized_db": 10.0 * math.log10(max(float(np.max(normalized[guard])), 1e-300)),
    }


def stratified_bootstrap_lower(
    differences: dict[str, np.ndarray], seed: int, replicates: int = 20000
) -> tuple[float, float, float]:
    rng = np.random.default_rng(seed)
    identifiers = sorted(differences)
    output = np.empty(replicates, dtype=np.float64)
    chunk = 100
    for start in range(0, replicates, chunk):
        count = min(chunk, replicates - start)
        cell_means = np.empty((count, len(identifiers)), dtype=np.float64)
        for cell_index, identifier in enumerate(identifiers):
            values = differences[identifier]
            indices = rng.integers(0, len(values), size=(count, len(values)))
            cell_means[:, cell_index] = np.mean(values[indices], axis=1)
        output[start : start + count] = np.mean(cell_means, axis=1)
    return tuple(float(value) for value in np.quantile(output, [0.025, 0.5, 0.975]))


def sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_campaign(document: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    geom = geometry(document)
    lookup = qam64_lookup()
    pilots = M.pilot_symbols(geom.used[geom.pilot_positions])
    sync, sync_frequency, sync_scale = raw_sync(geom)
    frames_per_cell = int(document["sample_count_and_split"]["frames_per_cell"])
    held_out_start = 200

    all_rows: list[dict[str, Any]] = []
    cell_summaries: list[dict[str, Any]] = []
    spectral_power = {
        "incumbent": np.zeros(8192 // 2 + 1, dtype=np.float64),
        "candidate": np.zeros(8192 // 2 + 1, dtype=np.float64),
    }
    campaign_started = time.perf_counter()
    print(f"frozen campaign start: {len(document['cells'])} cells x {frames_per_cell} = {len(document['cells']) * frames_per_cell} frames", flush=True)

    for cell_index, cell in enumerate(document["cells"]):
        cell_started = time.perf_counter()
        streams: list[bytes] = []
        llrs = {"incumbent": [], "candidate": []}
        frame_records: list[dict[str, Any]] = []
        for local_index in range(frames_per_cell):
            stream, incumbent, candidate = run_frame(
                document,
                geom,
                cell,
                local_index,
                sync,
                sync_frequency,
                sync_scale,
                pilots,
                lookup,
            )
            streams.append(stream)
            llrs["incumbent"].append(incumbent.punctured_llr)
            llrs["candidate"].append(candidate.punctured_llr)
            split = "training" if local_index < held_out_start else "held_out"
            row: dict[str, Any] = {"cell": cell["id"], "local_index": local_index, "split": split}
            for name, arm in (("incumbent", incumbent), ("candidate", candidate)):
                row[f"{name}_raw_crest_db"] = arm.raw_crest_db
                row[f"{name}_limited_crest_db"] = arm.limited_crest_db
                row[f"{name}_whole_body_crest_db"] = arm.whole_body_crest_db
                row[f"{name}_mean"] = arm.mean
                row[f"{name}_rms"] = arm.rms
                row[f"{name}_mean_square"] = arm.mean_square
                row[f"{name}_evm_rms"] = arm.evm_rms
                row[f"{name}_hard_errors"] = arm.hard_probe_bit_errors
                row[f"{name}_hard_bits"] = arm.hard_probe_bits
                row[f"{name}_tx_seconds"] = arm.tx_seconds
                row[f"{name}_rx_seconds"] = arm.rx_seconds
                if split == "held_out":
                    spectrum = np.fft.rfft(arm.first_symbol, n=8192)
                    spectral_power[name] += np.square(np.abs(spectrum))
            frame_records.append(row)
            if (local_index + 1) % 100 == 0:
                print(f"cell {cell_index + 1}/10 {cell['id']}: generated {local_index + 1}/{frames_per_cell}", flush=True)

        decoded = {
            name: batch_viterbi_crc(np.stack(values), streams)
            for name, values in llrs.items()
        }
        for row, incumbent_ok, candidate_ok in zip(
            frame_records, decoded["incumbent"], decoded["candidate"]
        ):
            row["incumbent_crc_ok"] = bool(incumbent_ok)
            row["candidate_crc_ok"] = bool(candidate_ok)
        all_rows.extend(frame_records)

        held = [row for row in frame_records if row["split"] == "held_out"]
        summary = {
            "cell": cell["id"],
            "snr_db": cell["snr_db"],
            "provenance": cell["provenance"],
            "frames": frames_per_cell,
            "held_out_frames": len(held),
            "incumbent_crc_recovery": float(np.mean([row["incumbent_crc_ok"] for row in held])),
            "candidate_crc_recovery": float(np.mean([row["candidate_crc_ok"] for row in held])),
            "incumbent_evm_median": float(np.median([row["incumbent_evm_rms"] for row in held])),
            "candidate_evm_median": float(np.median([row["candidate_evm_rms"] for row in held])),
            "incumbent_raw_crest_p99_9_db": float(np.quantile([row["incumbent_raw_crest_db"] for row in held], 0.999)),
            "candidate_raw_crest_p99_9_db": float(np.quantile([row["candidate_raw_crest_db"] for row in held], 0.999)),
            "wall_seconds": time.perf_counter() - cell_started,
        }
        summary["crc_difference"] = summary["candidate_crc_recovery"] - summary["incumbent_crc_recovery"]
        cell_summaries.append(summary)
        print(
            f"cell complete {cell['id']}: CRC {summary['incumbent_crc_recovery']:.4f} -> {summary['candidate_crc_recovery']:.4f}; "
            f"EVM median {summary['incumbent_evm_median']:.4f} -> {summary['candidate_evm_median']:.4f}",
            flush=True,
        )

    held_rows = [row for row in all_rows if row["split"] == "held_out"]
    training_rows = [row for row in all_rows if row["split"] == "training"]

    def values(rows: list[dict[str, Any]], arm: str, metric: str) -> np.ndarray:
        return np.asarray([row[f"{arm}_{metric}"] for row in rows], dtype=np.float64)

    overall: dict[str, Any] = {}
    for split_name, rows in (("training", training_rows), ("held_out", held_rows)):
        overall[split_name] = {}
        for arm in ("incumbent", "candidate"):
            crc = np.asarray([row[f"{arm}_crc_ok"] for row in rows], dtype=bool)
            hard_errors = sum(int(row[f"{arm}_hard_errors"]) for row in rows)
            hard_bits = sum(int(row[f"{arm}_hard_bits"]) for row in rows)
            overall[split_name][arm] = {
                "frames": len(rows),
                "raw_crest_db": quantiles(values(rows, arm, "raw_crest_db")),
                "limited_crest_db": quantiles(values(rows, arm, "limited_crest_db")),
                "whole_body_crest_db": quantiles(values(rows, arm, "whole_body_crest_db")),
                "evm_rms": quantiles(values(rows, arm, "evm_rms")),
                "crc_recovery": float(np.mean(crc)),
                "hard_probe_ber": hard_errors / hard_bits,
                "mean": float(np.mean(values(rows, arm, "mean"))),
                "rms": float(np.mean(values(rows, arm, "rms"))),
                "mean_square": float(np.mean(values(rows, arm, "mean_square"))),
                "tx_seconds": float(np.sum(values(rows, arm, "tx_seconds"))),
                "rx_seconds": float(np.sum(values(rows, arm, "rx_seconds"))),
            }

    reduction = (
        overall["held_out"]["incumbent"]["raw_crest_db"]["p99_9"]
        - overall["held_out"]["candidate"]["raw_crest_db"]["p99_9"]
    )
    rir_identifiers = [cell["id"] for cell in document["cells"] if cell["id"].split("-snr")[0] != "awgn"]
    differences: dict[str, np.ndarray] = {}
    for identifier in rir_identifiers:
        rows = [row for row in held_rows if row["cell"] == identifier]
        differences[identifier] = np.asarray(
            [int(row["candidate_crc_ok"]) - int(row["incumbent_crc_ok"]) for row in rows],
            dtype=np.float64,
        )
    bootstrap = stratified_bootstrap_lower(
        differences,
        int(document["randomization"]["bootstrap_seed"]),
    )

    spectral = {name: spectral_metrics(power) for name, power in spectral_power.items()}
    spectral_oob_delta = (
        spectral["candidate"]["out_of_band_to_in_band_db"]
        - spectral["incumbent"]["out_of_band_to_in_band_db"]
    )
    spectral_peak_delta = (
        spectral["candidate"]["guard_peak_normalized_db"]
        - spectral["incumbent"]["guard_peak_normalized_db"]
    )
    individual_rir_min = min(
        summary["crc_difference"] for summary in cell_summaries if summary["cell"] in rir_identifiers
    )

    gates = {
        "crest_factor": {"value_db": reduction, "threshold_db": 3.0, "pass": reduction >= 3.0},
        "coded_rir": {
            "bootstrap_95_interval_candidate_minus_incumbent": list(bootstrap),
            "lower_threshold": -0.005,
            "minimum_cell_point_difference": individual_rir_min,
            "minimum_cell_threshold": -0.02,
            "pass": bootstrap[0] >= -0.005 and individual_rir_min >= -0.02,
        },
        "spectral": {
            "oob_delta_db": spectral_oob_delta,
            "oob_maximum_delta_db": 0.25,
            "guard_peak_delta_db": spectral_peak_delta,
            "guard_peak_maximum_delta_db": 1.0,
            "pass": spectral_oob_delta <= 0.25 and spectral_peak_delta <= 1.0,
        },
        "integrity": {
            "frames_completed": len(all_rows),
            "required_frames": 10000,
            "all_metrics_finite": bool(
                all(
                    math.isfinite(float(row[key]))
                    for row in all_rows
                    for key in row
                    if isinstance(row[key], (float, np.floating))
                )
            ),
        },
    }
    gates["integrity"]["pass"] = (
        gates["integrity"]["frames_completed"] == gates["integrity"]["required_frames"]
        and gates["integrity"]["all_metrics_finite"]
    )
    ota_permitted = all(gate["pass"] for gate in gates.values())

    result = {
        "schema": SCHEMA,
        "preregistration_sha256": sha256_path(PREREG_PATH),
        "implementation_sha256": sha256_path(Path(__file__)),
        "python": sys.version,
        "numpy": np.__version__,
        "platform": platform.platform(),
        "frames_completed": len(all_rows),
        "campaign_wall_seconds": time.perf_counter() - campaign_started,
        "overall": overall,
        "cells": cell_summaries,
        "spectral": spectral,
        "gates": gates,
        "ota_permitted_by_frozen_gate": ota_permitted,
        "library_integration_permitted": False,
        "limits": document["known_unmeasured_limits"],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "summary.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    write_cells_csv(cell_summaries, output_dir / "cells.csv")
    write_report(result, output_dir / "REPORT.md")
    print(f"campaign complete: OTA permitted by frozen gate = {ota_permitted}", flush=True)
    return result


def write_cells_csv(cells: list[dict[str, Any]], path: Path) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(cells[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(cells)


def write_report(result: dict[str, Any], path: Path) -> None:
    held = result["overall"]["held_out"]
    gates = result["gates"]
    decision = "PERMITTED for a separately preregistered bounded OTA screen" if result["ota_permitted_by_frozen_gate"] else "NOT PERMITTED"
    text = f"""# Spike 8c localized DFT-spread OFDM result

This was an offline, preregistered screen. It did not use a speaker,
microphone, Android device, ADB, or live audio.

## Decision

OTA is **{decision}** under the frozen gate. Library integration remains
unjustified regardless of this screen because no OTA or on-device protection
behavior was measured.

## Held-out result

- Frames: {held['incumbent']['frames']} held out ({result['frames_completed']} total).
- Pre-limiter payload crest-factor q99.9: {held['incumbent']['raw_crest_db']['p99_9']:.4f} dB conventional OFDM versus {held['candidate']['raw_crest_db']['p99_9']:.4f} dB localized DFT-spread OFDM; reduction {gates['crest_factor']['value_db']:.4f} dB (gate >= 3.0 dB: {gates['crest_factor']['pass']}).
- Coded-RIR paired bootstrap 95% interval, candidate minus incumbent: [{gates['coded_rir']['bootstrap_95_interval_candidate_minus_incumbent'][0]:.6f}, {gates['coded_rir']['bootstrap_95_interval_candidate_minus_incumbent'][2]:.6f}]; worst RIR-cell point difference {gates['coded_rir']['minimum_cell_point_difference']:.6f} (gate: {gates['coded_rir']['pass']}).
- Spectral out-of-band delta: {gates['spectral']['oob_delta_db']:.4f} dB; guard-peak delta {gates['spectral']['guard_peak_delta_db']:.4f} dB (gate: {gates['spectral']['pass']}).
- CRC-probe recovery: {held['incumbent']['crc_recovery']:.6f} conventional versus {held['candidate']['crc_recovery']:.6f} candidate.
- Median EVM RMS: {held['incumbent']['evm_rms']['median']:.6f} conventional versus {held['candidate']['evm_rms']['median']:.6f} candidate.
- Equal-peak RMS: {held['incumbent']['rms']:.8f} conventional versus {held['candidate']['rms']:.8f} candidate; mean-square {held['incumbent']['mean_square']:.10f} versus {held['candidate']['mean_square']:.10f}.
- Accumulated Python TX time: {held['incumbent']['tx_seconds']:.3f} s conventional versus {held['candidate']['tx_seconds']:.3f} s candidate. Accumulated RX time: {held['incumbent']['rx_seconds']:.3f} s versus {held['candidate']['rx_seconds']:.3f} s. These are prototype timings, not mobile real-time estimates.

## Interpretation and limits

The candidate used one frozen unitary 935-point localized DFT and logical comb
pilots. No pilot or mapping change was made after preregistration. The CRC
metric is one rate-2/3 convolutionally coded, CRC-protected 256-byte probe per
full 64-symbol frame; it is not the production 107-block frame decoder or a
goodput measurement.

There was no measured room impulse response in the tracked checkout. The two
"golden" responses are deterministic modem fixtures and the other responses
are declared synthetic models. Symbol timing was known, and CFO, SRO,
acquisition, clock drift, speaker/microphone distortion, OS protection DSP,
THD, intermodulation, clipping, and acoustic exposure were not measured.
Consequently, even a passing offline result would only justify designing a
bounded OTA screen, not integration or a throughput claim.

## Reproduction

```bash
.venv/bin/python scratch/spikes/dfts_ofdm_8c/test_spike.py
.venv/bin/python scratch/spikes/dfts_ofdm_8c/spike.py run
```

Preregistration SHA-256: `{result['preregistration_sha256']}`

Implementation SHA-256: `{result['implementation_sha256']}`

Campaign wall time: {result['campaign_wall_seconds']:.3f} s.
"""
    path.write_text(text)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("describe")
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--output-dir", type=Path, default=RESULTS_DIR)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    document = load_preregistration()
    if args.command == "describe":
        print(json.dumps({"preregistration_sha256": sha256_path(PREREG_PATH), "geometry": document["phy"]}, indent=2))
        return 0
    run_campaign(document, args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
