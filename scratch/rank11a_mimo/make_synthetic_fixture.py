#!/usr/bin/env python3
"""Generate the deterministic full-rank fixture for matrix_analysis.py."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


def _pairs(array: np.ndarray) -> list:
    return np.stack((array.real, array.imag), axis=-1).tolist()


def seal_allocation(allocation: dict) -> None:
    """Replace the allocation hash with its canonical JSON SHA-256."""

    allocation.pop("allocation_sha256", None)
    canonical = json.dumps(allocation, sort_keys=True, separators=(",", ":")).encode("utf-8")
    allocation["allocation_sha256"] = hashlib.sha256(canonical).hexdigest()


def build_fixture() -> dict:
    rng = np.random.default_rng(110_202_607_18)
    active_bins = list(range(96, 106))
    bin_axis = np.arange(len(active_bins), dtype=np.float64)
    base = np.empty((len(active_bins), 2, 2), dtype=np.complex128)
    base[:, 0, 0] = np.exp(-1j * 0.030 * bin_axis)
    base[:, 0, 1] = 0.22 * np.exp(1j * (0.15 + 0.021 * bin_axis))
    base[:, 1, 0] = 0.18 * np.exp(1j * (-0.30 + 0.017 * bin_axis))
    base[:, 1, 1] = 0.88 * np.exp(1j * (0.40 - 0.025 * bin_axis))

    repeats = []
    for repeat in range(5):
        relative = repeat - 2
        common_phase = 0.009 * relative + 0.0015 * relative * bin_axis
        residual = rng.normal(0.0, 0.0025, base.shape)
        amplitude = 1.0 + rng.normal(0.0, 0.004, base.shape)
        repeats.append(base * amplitude * np.exp(1j * (common_phase[:, None, None] + residual)))

    noise = np.empty((len(active_bins), 2, 2), dtype=np.complex128)
    for index in range(len(active_bins)):
        cross = 0.0006 * np.exp(1j * (0.1 + index * 0.03))
        noise[index] = [[0.0040, cross], [np.conj(cross), 0.0050]]

    observations = []
    for bin_index in active_bins:
        for mode in range(2):
            bits = rng.integers(0, 2, size=(8, 2), dtype=np.int8)
            signed = 1.0 - 2.0 * bits
            strength = 7.0 if mode == 0 else 5.0
            llrs = signed * strength + rng.normal(0.0, 0.65, size=bits.shape)
            observations.append(
                {
                    "bin": bin_index,
                    "mode": mode,
                    "bits": bits.tolist(),
                    "llrs": llrs.tolist(),
                }
            )

    frozen_allocation = {
        "allocation_id": "synthetic-calibration-all-bins-qpsk-r12-v1",
        "source": "calibration_repeats_only",
        "frozen_before_held_out": True,
        "power_policy": {
            "one_mode_bin_total_power_fraction": 1.0,
            "two_mode_bin_power_fractions": [0.5, 0.5],
            "sum_digital_sample_power_matches_control": True,
        },
        "mode1": {
            "active_bins": active_bins.copy(),
            "bits_per_subcarrier": 2,
            "code_rate": 0.5,
        },
        "mode2": {
            "active_bins": active_bins.copy(),
            "bits_per_subcarrier": 2,
            "code_rate": 0.5,
        },
    }
    seal_allocation(frozen_allocation)

    return {
        "schema": "cyrinx.rank11a.matrix-dataset.v2",
        "evidence_class": "synthetic",
        "geometry": "deterministic_full_rank_fixture",
        "provenance": {
            "independent_tx_ports": True,
            "independent_rx_ports": True,
            "common_phase_reference": True,
            "continuous_capture_clock": True,
            "route_identity_stable": True,
            "probe_timing_documented": True,
            "fixed_total_power_documented": True,
            "per_speaker_peaks_documented": True,
            "noise_interval_present": True,
            "timing_cfo_sro_removed": True,
            "randomized_probe_documented": True,
            "repeat_count": 5,
            "acquisition_mode": "phase_continuous_stereo",
            "device_route_identity": "synthetic-known-2tx-2rx",
            "channel_map": "tx0,tx1 -> rx0,rx1",
            "probe_waveform_hash": "synthetic-seed-11020260718",
            "analysis_amendment_sha256": (
                "a87f849a6da576844da1202f152f76596a4c0ff872394fdd7ab1df743b7f9f37"
            ),
            "geometry_label": "deterministic full-rank fixture",
            "environment_label": "complex AWGN with correlated receiver noise",
        },
        "active_bins": active_bins,
        "channel_repeats": _pairs(np.asarray(repeats)),
        "noise_covariance": _pairs(noise),
        "power": {
            "control_sum_digital_sample_power": 0.008,
            "mimo_sum_digital_sample_power": 0.008,
            "per_active_bin_total_symbol_power": 0.10,
            "per_bin_symbol_power_domain": "known normalized X[k] used by Y[k] = H[k] X[k] + N[k]",
            "control_single_speaker_sample_peak": 0.18,
            "mimo_per_speaker_sample_peaks": [0.127, 0.127],
        },
        "data_split": {"calibration_repeats": 2, "held_out_repeats": 3},
        "gmi_observations": observations,
        "scheduling": {
            "frame": {
                "sample_rate_hz": 48000,
                "nfft": 2048,
                "cyclic_prefix_samples": 240,
                "payload_bins": len(active_bins),
                "bits_per_subcarrier": 2,
                "code_rate": 0.5,
                "data_symbols_per_frame": 64,
                "preamble_samples_per_frame": 8192,
                "gap_samples_per_frame": 12000,
                "control_samples_per_frame": 2048,
            },
            "frozen_allocation": frozen_allocation,
            "control_recovery": 0.99,
            "mimo_residual_recovery": 0.99,
            "recovery_accounting": {
                "control_source": "explicit_synthetic_model",
                "mimo_residual_source": "explicit_synthetic_model",
                "application_order": "after per-mode per-bin held-out GMI masking",
            },
            "control_setup_s": 0.15,
            "mimo_setup_s": 0.60,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.write_text(
        json.dumps(build_fixture(), indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
