#!/usr/bin/env python3
"""Deterministic offline Rank 11a matrix and capacity analysis.

This research tool consumes an already estimated, phase-referenced per-bin
channel matrix.  It does not acquire audio and it refuses to analyze a data set
whose provenance does not establish independent ports and a common phase
reference.  Synthetic results validate the arithmetic only; they are never OTA
evidence.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Sequence
import json
import math
from pathlib import Path
from typing import Any

import numpy as np


SCHEMA = "cyrinx.rank11a.matrix-dataset.v1"
RESULT_SCHEMA = "cyrinx.rank11a.matrix-analysis.v1"
REQUIRED_PROVENANCE_FLAGS = (
    "independent_tx_ports",
    "independent_rx_ports",
    "common_phase_reference",
    "continuous_capture_clock",
    "route_identity_stable",
    "probe_timing_documented",
    "fixed_total_power_documented",
    "per_speaker_peaks_documented",
    "noise_interval_present",
    "timing_cfo_sro_removed",
)


class AnalysisError(ValueError):
    """Raised when a matrix input violates the frozen analysis contract."""


def _complex_array(value: Any, name: str) -> np.ndarray:
    raw = np.asarray(value, dtype=np.float64)
    if raw.ndim < 1 or raw.shape[-1] != 2:
        raise AnalysisError(f"{name} must end in [real, imaginary]")
    if not np.all(np.isfinite(raw)):
        raise AnalysisError(f"{name} contains a non-finite value")
    return raw[..., 0] + 1j * raw[..., 1]


def _percentile(values: Iterable[float], percentile: float) -> float:
    array = np.asarray(list(values), dtype=np.float64)
    if array.size == 0:
        raise AnalysisError("cannot take a percentile of an empty sequence")
    return float(np.percentile(array, percentile))


def audit_identifiability(dataset: dict[str, Any]) -> list[str]:
    """Return every provenance reason that prevents a coherent matrix claim."""

    reasons: list[str] = []
    if dataset.get("schema") != SCHEMA:
        reasons.append(f"schema must be {SCHEMA}")

    provenance = dataset.get("provenance")
    if not isinstance(provenance, dict):
        return reasons + ["provenance object is absent"]
    for flag in REQUIRED_PROVENANCE_FLAGS:
        if provenance.get(flag) is not True:
            reasons.append(f"provenance.{flag} is not true")

    repeats = provenance.get("repeat_count")
    if not isinstance(repeats, int) or repeats < 5:
        reasons.append("at least five phase-referenced repeats are required")

    acquisition = provenance.get("acquisition_mode")
    allowed = {"phase_continuous_stereo", "documented_equivalent_common_reference"}
    if acquisition not in allowed:
        reasons.append("acquisition_mode does not establish a common column phase")

    required_text = (
        "device_route_identity",
        "channel_map",
        "probe_waveform_hash",
        "geometry_label",
        "environment_label",
    )
    for key in required_text:
        if not isinstance(provenance.get(key), str) or not provenance[key].strip():
            reasons.append(f"provenance.{key} is absent")
    return reasons


def _validate_arrays(dataset: dict[str, Any]) -> tuple[np.ndarray, np.ndarray, list[int]]:
    channels = _complex_array(dataset.get("channel_repeats"), "channel_repeats")
    noise = _complex_array(dataset.get("noise_covariance"), "noise_covariance")
    bins = dataset.get("active_bins")
    if not isinstance(bins, list) or not bins or not all(isinstance(k, int) for k in bins):
        raise AnalysisError("active_bins must be a non-empty integer list")
    if channels.ndim != 4:
        raise AnalysisError("channel_repeats must have shape [repeat, bin, rx, tx, complex]")
    repeats, bin_count, rx_count, tx_count = channels.shape
    if repeats < 5 or rx_count < 2 or tx_count < 2:
        raise AnalysisError("Rank 11a requires >=5 repeats and at least a 2x2 matrix")
    if bin_count != len(bins):
        raise AnalysisError("active_bins length does not match channel_repeats")
    if noise.shape != (bin_count, rx_count, rx_count):
        raise AnalysisError("noise_covariance must have shape [bin, rx, rx, complex]")
    for index, covariance in enumerate(noise):
        if not np.allclose(covariance, covariance.conj().T, atol=1e-9, rtol=1e-7):
            raise AnalysisError(f"noise covariance {index} is not Hermitian")
        if float(np.min(np.linalg.eigvalsh(covariance))) <= 0.0:
            raise AnalysisError(f"noise covariance {index} is not positive definite")
    return channels, noise, bins


def _repeat_stability(channels: np.ndarray, calibration_repeats: int) -> dict[str, float]:
    held_out = channels[calibration_repeats:]
    if held_out.shape[0] < 3:
        raise AnalysisError("at least three held-out whole repeats are required")

    coherence_values: list[float] = []
    phase_sd_values: list[float] = []
    bin_axis = np.arange(channels.shape[1], dtype=np.float64)
    for first in range(held_out.shape[0]):
        for second in range(first + 1, held_out.shape[0]):
            for rx in range(channels.shape[2]):
                for tx in range(channels.shape[3]):
                    a = held_out[first, :, rx, tx]
                    b = held_out[second, :, rx, tx]
                    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
                    if denominator <= 0.0:
                        coherence_values.append(0.0)
                        phase_sd_values.append(float("inf"))
                        continue
                    coherence_values.append(float(abs(np.vdot(a, b)) / denominator))
                    phase_delta = np.unwrap(np.angle(b * np.conj(a)))
                    design = np.column_stack((bin_axis, np.ones_like(bin_axis)))
                    slope, intercept = np.linalg.lstsq(design, phase_delta, rcond=None)[0]
                    residual = phase_delta - (slope * bin_axis + intercept)
                    resultant = float(abs(np.mean(np.exp(1j * residual))))
                    resultant = min(1.0, max(np.finfo(float).tiny, resultant))
                    phase_sd_values.append(math.degrees(math.sqrt(-2.0 * math.log(resultant))))

    magnitudes = np.abs(held_out)
    mean_magnitudes = np.mean(magnitudes, axis=0)
    amplitude_cv = np.std(magnitudes, axis=0, ddof=1) / np.maximum(
        mean_magnitudes, np.finfo(float).tiny
    )
    return {
        "minimum_repeat_coherence": min(coherence_values),
        "median_repeat_coherence": float(np.median(coherence_values)),
        "p90_residual_phase_sd_degrees": _percentile(phase_sd_values, 90.0),
        "p90_amplitude_coefficient_of_variation": float(np.percentile(amplitude_cv, 90.0)),
    }


def _whitened_matrix(channel: np.ndarray, covariance: np.ndarray) -> np.ndarray:
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    inverse_root = eigenvectors @ np.diag(1.0 / np.sqrt(eigenvalues)) @ eigenvectors.conj().T
    return inverse_root @ channel


def _capacity_metrics(
    channel: np.ndarray,
    covariance: np.ndarray,
    per_bin_total_symbol_power: float | Sequence[float],
) -> dict[str, Any]:
    powers = np.asarray(per_bin_total_symbol_power, dtype=np.float64)
    if powers.ndim == 0:
        powers = np.full(channel.shape[0], float(powers))
    if powers.shape != (channel.shape[0],):
        raise AnalysisError("per-bin total symbol power must be scalar or match the active-bin count")
    if not np.all(np.isfinite(powers)) or np.any(powers <= 0.0):
        raise AnalysisError("per-bin total symbol powers must be finite and positive")

    singular_values: list[list[float]] = []
    effective_ranks: list[float] = []
    control_bits_per_use: list[float] = []
    mimo_bits_per_use: list[float] = []
    best_speaker_by_bin: list[int] = []
    tx_count = channel.shape[2]

    for matrix, noise, bin_power in zip(channel, covariance, powers, strict=True):
        whitened = _whitened_matrix(matrix, noise)
        values = np.linalg.svd(whitened, compute_uv=False)
        singular_values.append([float(value) for value in values])
        energies = values**2
        probabilities = energies / max(float(np.sum(energies)), np.finfo(float).tiny)
        entropy = -float(np.sum(probabilities * np.log(np.maximum(probabilities, 1e-300))))
        effective_ranks.append(math.exp(entropy))

        per_speaker = [
            math.log2(1.0 + bin_power * float(np.vdot(column, column).real))
            for column in whitened.T
        ]
        best_speaker_by_bin.append(int(np.argmax(per_speaker)))
        control_bits_per_use.append(max(per_speaker))

        gram = whitened @ whitened.conj().T
        matrix_term = np.eye(gram.shape[0], dtype=np.complex128) + (bin_power / tx_count) * gram
        sign, log_determinant = np.linalg.slogdet(matrix_term)
        if sign.real <= 0.0 or abs(sign.imag) > 1e-8:
            raise AnalysisError("log-det capacity matrix is not positive definite")
        mimo_bits_per_use.append(float(log_determinant / math.log(2.0)))

    return {
        "singular_values_by_bin": singular_values,
        "effective_rank": {
            "median": float(np.median(effective_ranks)),
            "p10": _percentile(effective_ranks, 10.0),
            "minimum": min(effective_ranks),
        },
        "best_speaker_by_bin": best_speaker_by_bin,
        "best_speaker_simo_logdet_bits_per_ofdm_use": sum(control_bits_per_use),
        "equal_power_mimo_logdet_bits_per_ofdm_use": sum(mimo_bits_per_use),
        "logdet_ratio": sum(mimo_bits_per_use) / sum(control_bits_per_use),
    }


def bitwise_gmi(bits: Sequence[Sequence[int]], llrs: Sequence[Sequence[float]]) -> float:
    """Return bit-metric GMI in information bits per modulation symbol.

    LLR uses the conventional log(P(bit=0) / P(bit=1)) sign.
    """

    bit_array = np.asarray(bits, dtype=np.int8)
    llr_array = np.asarray(llrs, dtype=np.float64)
    if bit_array.ndim != 2 or bit_array.shape != llr_array.shape or bit_array.size == 0:
        raise AnalysisError("GMI bits and LLRs must be matching non-empty 2-D arrays")
    if not np.all((bit_array == 0) | (bit_array == 1)):
        raise AnalysisError("GMI bits must be zero or one")
    if not np.all(np.isfinite(llr_array)):
        raise AnalysisError("GMI LLRs must be finite")
    signed = 1.0 - 2.0 * bit_array
    penalty_per_symbol = np.sum(np.logaddexp(0.0, -signed * llr_array), axis=1) / math.log(2.0)
    return float(bit_array.shape[1] - np.mean(penalty_per_symbol))


def _gmi_metrics(dataset: dict[str, Any], bins: list[int]) -> dict[str, Any]:
    observations = dataset.get("gmi_observations")
    if observations is None:
        return {
            "available": False,
            "reason": "actual randomized known bits and demapper LLRs were not retained",
            "mode2_qpsk_rate_half_bin_fraction": None,
        }
    if dataset.get("provenance", {}).get("randomized_probe_documented") is not True:
        return {
            "available": False,
            "reason": "randomized known-probe provenance is not documented",
            "mode2_qpsk_rate_half_bin_fraction": None,
        }
    if not isinstance(observations, list):
        raise AnalysisError("gmi_observations must be a list")

    by_bin_mode: dict[tuple[int, int], float] = {}
    for observation in observations:
        if not isinstance(observation, dict):
            raise AnalysisError("each GMI observation must be an object")
        key = (int(observation["bin"]), int(observation["mode"]))
        if key in by_bin_mode:
            raise AnalysisError(f"duplicate GMI observation for bin/mode {key}")
        by_bin_mode[key] = bitwise_gmi(observation["bits"], observation["llrs"])

    mode2 = {bin_index: by_bin_mode.get((bin_index, 1)) for bin_index in bins}
    if any(value is None for value in mode2.values()):
        return {
            "available": False,
            "reason": "mode-2 GMI observations do not cover every active bin",
            "mode2_qpsk_rate_half_bin_fraction": None,
            "gmi_bits_per_symbol_by_bin": {str(key): value for key, value in mode2.items()},
        }
    values = {key: float(value) for key, value in mode2.items() if value is not None}
    passing = sum(value >= 1.0 for value in values.values())
    return {
        "available": True,
        "threshold_bits_per_qpsk_symbol": 1.0,
        "mode2_qpsk_rate_half_bin_fraction": passing / len(bins),
        "mode2_gmi_bits_per_symbol_by_bin": {str(key): value for key, value in values.items()},
    }


def _scheduled_ceiling(schedule: dict[str, Any], streams: int) -> float:
    required = (
        "sample_rate_hz",
        "nfft",
        "cyclic_prefix_samples",
        "payload_bins",
        "bits_per_subcarrier",
        "code_rate",
        "data_symbols_per_frame",
        "preamble_samples_per_frame",
        "gap_samples_per_frame",
        "control_samples_per_frame",
    )
    missing = [key for key in required if key not in schedule]
    if missing:
        raise AnalysisError(f"schedule is missing {', '.join(missing)}")
    payload_bits = (
        streams
        * float(schedule["payload_bins"])
        * float(schedule["bits_per_subcarrier"])
        * float(schedule["code_rate"])
        * float(schedule["data_symbols_per_frame"])
    )
    frame_samples = (
        float(schedule["preamble_samples_per_frame"])
        + float(schedule["gap_samples_per_frame"])
        + float(schedule["control_samples_per_frame"])
        + float(schedule["data_symbols_per_frame"])
        * (float(schedule["nfft"]) + float(schedule["cyclic_prefix_samples"]))
    )
    if payload_bits <= 0.0 or frame_samples <= 0.0:
        raise AnalysisError("schedule bit and sample counts must be positive")
    return payload_bits * float(schedule["sample_rate_hz"]) / frame_samples


def _session_metrics(dataset: dict[str, Any]) -> dict[str, Any]:
    scheduling = dataset.get("scheduling")
    if not isinstance(scheduling, dict):
        raise AnalysisError("scheduling object is required")
    schedule = scheduling.get("frame")
    if not isinstance(schedule, dict):
        raise AnalysisError("scheduling.frame object is required")

    control_rate = _scheduled_ceiling(schedule, streams=1)
    mimo_rate = _scheduled_ceiling(schedule, streams=int(scheduling.get("mimo_streams", 2)))
    control_recovery = float(scheduling.get("control_recovery", 1.0))
    mimo_recovery = float(scheduling.get("mimo_recovery", 1.0))
    control_setup = float(scheduling.get("control_setup_s", 0.0))
    mimo_setup = float(scheduling.get("mimo_setup_s", 0.0))
    if not (0.0 < control_recovery <= 1.0 and 0.0 < mimo_recovery <= 1.0):
        raise AnalysisError("recovery fractions must be in (0, 1]")
    if control_setup < 0.0 or mimo_setup < 0.0:
        raise AnalysisError("setup times cannot be negative")

    transfers: dict[str, Any] = {}
    for label, byte_count in (("64KiB", 64 * 1024), ("1MiB", 1024 * 1024)):
        bits = 8.0 * byte_count
        control_duration = control_setup + bits / (control_rate * control_recovery)
        mimo_duration = mimo_setup + bits / (mimo_rate * mimo_recovery)
        control_net = bits / control_duration
        mimo_net = bits / mimo_duration
        transfers[label] = {
            "best_speaker_simo_net_bps": control_net,
            "two_mode_mimo_net_bps": mimo_net,
            "ratio": mimo_net / control_net,
            "best_speaker_simo_duration_s": control_duration,
            "two_mode_mimo_duration_s": mimo_duration,
        }
    return {
        "scheduled_ceiling_bps": {
            "best_speaker_simo": control_rate,
            "two_mode_mimo": mimo_rate,
        },
        "setup_s": {
            "best_speaker_simo": control_setup,
            "two_mode_mimo": mimo_setup,
        },
        "transfers": transfers,
    }


def analyze_dataset(dataset: dict[str, Any]) -> dict[str, Any]:
    """Analyze one geometry after enforcing coherent-matrix provenance."""

    reasons = audit_identifiability(dataset)
    base: dict[str, Any] = {
        "schema": RESULT_SCHEMA,
        "evidence_class": dataset.get("evidence_class", "unknown"),
        "geometry": dataset.get("geometry", "unknown"),
        "identifiable_phase_coherent_matrix": not reasons,
        "identifiability_reasons": reasons,
    }
    if reasons:
        return base | {
            "decision": "STOP_NOT_IDENTIFIABLE",
            "promotion_gate_evaluable": False,
        }

    channels, noise, bins = _validate_arrays(dataset)
    calibration_repeats = int(dataset.get("data_split", {}).get("calibration_repeats", 2))
    stability = _repeat_stability(channels, calibration_repeats)
    held_out_channel = np.mean(channels[calibration_repeats:], axis=0)
    power = dataset.get("power", {})
    per_bin_power = power.get("per_active_bin_total_symbol_power", 0.0)
    capacity = _capacity_metrics(held_out_channel, noise, per_bin_power)
    gmi = _gmi_metrics(dataset, bins)
    session = _session_metrics(dataset)

    measured_peaks = [float(value) for value in power.get("mimo_per_speaker_sample_peaks", [])]
    peak_limit = float(power.get("control_single_speaker_sample_peak", -1.0))
    peak_gate = bool(measured_peaks) and peak_limit > 0.0 and max(measured_peaks) <= peak_limit
    control_sample_power = float(power.get("control_sum_digital_sample_power", -1.0))
    mimo_sample_power = float(power.get("mimo_sum_digital_sample_power", -2.0))
    fixed_sum_gate = (
        control_sample_power > 0.0
        and mimo_sample_power > 0.0
        and math.isclose(control_sample_power, mimo_sample_power, rel_tol=1e-6, abs_tol=1e-12)
    )
    gates = {
        "repeat_coherence_at_least_0_95": stability["minimum_repeat_coherence"] >= 0.95,
        "residual_phase_sd_at_most_15_degrees": (
            stability["p90_residual_phase_sd_degrees"] <= 15.0
        ),
        "amplitude_cv_at_most_0_15": (
            stability["p90_amplitude_coefficient_of_variation"] <= 0.15
        ),
        "mode2_qpsk_rate_half_on_at_least_40_percent_bins": (
            gmi["available"] and float(gmi["mode2_qpsk_rate_half_bin_fraction"]) >= 0.40
        ),
        "per_speaker_peak_limit": peak_gate,
        "fixed_sum_digital_sample_power": fixed_sum_gate,
        "one_mib_net_gain_at_least_1_35": session["transfers"]["1MiB"]["ratio"] >= 1.35,
    }
    all_gates = all(gates.values())
    evidence_class = dataset.get("evidence_class")
    if evidence_class == "synthetic":
        decision = "SYNTHETIC_VALIDATION_ONLY"
    elif all_gates:
        decision = "GEOMETRY_PASSES_OFFLINE_GATE"
    else:
        decision = "GEOMETRY_FAILS_OR_INCOMPLETE"

    sample_rate = float(dataset["scheduling"]["frame"]["sample_rate_hz"])
    symbol_samples = float(dataset["scheduling"]["frame"]["nfft"]) + float(
        dataset["scheduling"]["frame"]["cyclic_prefix_samples"]
    )
    capacity["best_speaker_simo_cp_inclusive_bps"] = (
        capacity["best_speaker_simo_logdet_bits_per_ofdm_use"] * sample_rate / symbol_samples
    )
    capacity["equal_power_mimo_cp_inclusive_bps"] = (
        capacity["equal_power_mimo_logdet_bits_per_ofdm_use"] * sample_rate / symbol_samples
    )
    return base | {
        "decision": decision,
        "promotion_gate_evaluable": evidence_class in {"retained_replay", "measured_ota"},
        "stability": stability,
        "capacity": capacity,
        "gmi": gmi,
        "session_accounting": session,
        "gates": gates,
        "all_geometry_gates_pass": all_gates,
        "claim_boundary": (
            "Synthetic matrices validate arithmetic only and cannot satisfy the Rank 11a OTA gate."
            if evidence_class == "synthetic"
            else "This result applies only to the declared retained geometry and provenance."
        ),
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path, help="rank11a matrix dataset JSON")
    parser.add_argument("--output", type=Path, help="optional analysis JSON output")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    result = analyze_dataset(dataset)
    rendered = json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if result["identifiable_phase_coherent_matrix"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
