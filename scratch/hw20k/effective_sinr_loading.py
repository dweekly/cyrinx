#!/usr/bin/env python3
"""Offline-only effective-SINR/GMI loading spike.

This research tool audits whether retained captures identify alternate loading
policies, estimates payload-independent per-bin allocations from known probes,
and emits the minimum machine-readable acquisition plan needed when they do
not. It deliberately has no hardware, ADB, or audio imports.
"""

from __future__ import annotations

import argparse
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np


POLICY_IDS = (
    "P0-uniform-incumbent",
    "P1-raw-psd",
    "P2-pilot-gmi",
    "P3-covariance-gmi",
)
RANK5_CELL_IDS = (
    "F0-forward-vol30-lr",
    "R1-reverse-vol25-lr",
    "S-M-mac-self-vol30-lr",
    "R2-reverse-vol25-rl",
)
FORBIDDEN_SAME_FRAME_FIELDS = frozenset(
    {
        "block_valid",
        "blocks_ok",
        "crc",
        "decoded_bytes",
        "evm_probe",
        "expected_bytes",
        "payload",
        "payload_exact",
        "strict_ordered_verified_blocks",
    }
)


def canonical_json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps(value, indent=2, sort_keys=True).encode() + b"\n")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_record(path: Path, *, semantically_inspected: bool) -> dict[str, object]:
    return {
        "path": str(path.resolve()),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "semantically_inspected": semantically_inspected,
    }


def _qam_constellation(bits_per_symbol: int) -> tuple[np.ndarray, np.ndarray]:
    if bits_per_symbol == 2:
        axis = ((0, -1.0), (1, 1.0))
    elif bits_per_symbol == 4:
        axis = ((0, 0, -3.0), (0, 1, -1.0), (1, 1, 1.0), (1, 0, 3.0))
    else:
        raise ValueError("only QPSK and 16-QAM are preregistered")
    axis_bits = bits_per_symbol // 2
    points: list[complex] = []
    labels: list[tuple[int, ...]] = []
    for i_value in axis:
        for q_value in axis:
            labels.append(tuple(int(value) for value in i_value[:axis_bits] + q_value[:axis_bits]))
            points.append(complex(i_value[-1], q_value[-1]))
    constellation = np.asarray(points, dtype=np.complex128)
    constellation /= math.sqrt(float(np.mean(np.abs(constellation) ** 2)))
    return constellation, np.asarray(labels, dtype=np.int8)


def _logsumexp(values: np.ndarray, axis: int) -> np.ndarray:
    maximum = np.max(values, axis=axis, keepdims=True)
    return np.squeeze(maximum, axis=axis) + np.log(
        np.sum(np.exp(values - maximum), axis=axis)
    )


def _bicm_gmi_at_snr(snr_linear: float, bits_per_symbol: int, order: int = 10) -> float:
    constellation, labels = _qam_constellation(bits_per_symbol)
    nodes, weights = np.polynomial.hermite.hermgauss(order)
    noise = (nodes[:, None] + 1j * nodes[None, :]).reshape(-1) / math.sqrt(snr_linear)
    probability = (weights[:, None] * weights[None, :]).reshape(-1) / math.pi
    gmi = 0.0
    for symbol_index, transmitted in enumerate(constellation):
        received = transmitted + noise
        metric = -snr_linear * np.abs(received[:, None] - constellation[None, :]) ** 2
        for bit_index in range(bits_per_symbol):
            zero = _logsumexp(metric[:, labels[:, bit_index] == 0], axis=1)
            one = _logsumexp(metric[:, labels[:, bit_index] == 1], axis=1)
            signed_llr = (1 - 2 * int(labels[symbol_index, bit_index])) * (zero - one)
            loss = np.logaddexp(0.0, -signed_llr) / math.log(2.0)
            gmi += (1.0 - float(np.sum(probability * loss))) / len(constellation)
    return float(np.clip(gmi, 0.0, bits_per_symbol))


@lru_cache(maxsize=2)
def _gmi_table(bits_per_symbol: int) -> tuple[np.ndarray, np.ndarray]:
    snr_db = np.arange(-20.0, 40.0001, 0.25)
    values = np.asarray(
        [_bicm_gmi_at_snr(10.0 ** (value / 10.0), bits_per_symbol) for value in snr_db]
    )
    values = np.maximum.accumulate(values)
    return snr_db, values


def bicm_gmi_awgn(snr_db: np.ndarray | Sequence[float] | float, bits: int) -> np.ndarray:
    """Return an AWGN BICM-GMI lookup, in bits per complex symbol."""

    grid, values = _gmi_table(bits)
    requested = np.asarray(snr_db, dtype=np.float64)
    return np.interp(requested, grid, values, left=values[0], right=values[-1])


def bicm_gmi_from_known_symbols(
    transmitted_indices: np.ndarray,
    equalized_received: np.ndarray,
    noise_variance: np.ndarray | Sequence[float] | float,
    bits_per_symbol: int,
) -> np.ndarray:
    """Estimate per-bin BICM-GMI from a held-out known randomized QAM probe.

    Channel/equalizer and noise variance must be fitted on earlier probe
    symbols. The returned known-probe statistic may select only a later burst.
    """

    constellation, labels = _qam_constellation(bits_per_symbol)
    indices = np.asarray(transmitted_indices, dtype=np.int64)
    received = np.asarray(equalized_received, dtype=np.complex128)
    if indices.shape != received.shape or indices.ndim != 2:
        raise ValueError("known symbol indices and received samples need shape [symbols, bins]")
    if np.any(indices < 0) or np.any(indices >= len(constellation)):
        raise ValueError("known symbol index is outside the constellation")
    variance = np.asarray(noise_variance, dtype=np.float64)
    if variance.ndim == 0:
        variance = np.full(received.shape[1], float(variance))
    if variance.shape != (received.shape[1],):
        raise ValueError("noise variance must be scalar or one value per bin")
    if np.any(~np.isfinite(received)) or np.any(~np.isfinite(variance)) or np.any(variance <= 0.0):
        raise ValueError("received samples and positive noise variance must be finite")
    result = np.zeros(received.shape[1], dtype=np.float64)
    for bin_index in range(received.shape[1]):
        metric = -(
            np.abs(received[:, bin_index, None] - constellation[None, :]) ** 2
            / variance[bin_index]
        )
        for bit_index in range(bits_per_symbol):
            zero = _logsumexp(metric[:, labels[:, bit_index] == 0], axis=1)
            one = _logsumexp(metric[:, labels[:, bit_index] == 1], axis=1)
            actual = labels[indices[:, bin_index], bit_index]
            signed_llr = (1 - 2 * actual) * (zero - one)
            result[bin_index] += 1.0 - float(
                np.mean(np.logaddexp(0.0, -signed_llr) / math.log(2.0))
            )
    return np.clip(result, 0.0, bits_per_symbol)


def raw_psd_allocation(snr_db: Sequence[float] | np.ndarray) -> np.ndarray:
    snr = np.asarray(snr_db, dtype=np.float64)
    if np.any(~np.isfinite(snr)):
        raise ValueError("SNR contains non-finite values")
    return np.where(snr >= 13.0, 4, np.where(snr >= 6.0, 2, 0)).astype(np.int8)


def pilot_gmi_allocation(
    snr_db: Sequence[float] | np.ndarray,
    *,
    calibration_offset_bits: float = -0.50,
) -> np.ndarray:
    snr = np.asarray(snr_db, dtype=np.float64)
    if np.any(~np.isfinite(snr)):
        raise ValueError("SNR contains non-finite values")
    qpsk = bicm_gmi_awgn(snr, 2) + calibration_offset_bits
    qam16 = bicm_gmi_awgn(snr, 4) + calibration_offset_bits
    return np.where(qam16 >= 2.40, 4, np.where(qpsk >= 1.20, 2, 0)).astype(np.int8)


def maximum_sinr(
    channel: np.ndarray,
    noise_covariance: np.ndarray,
    *,
    signal_power: float = 1.0,
    regularization_fraction: float = 1e-6,
) -> np.ndarray:
    """Compute h^H R^-1 h for one independently verified microphone set."""

    h_value = np.asarray(channel, dtype=np.complex128)
    covariance = np.asarray(noise_covariance, dtype=np.complex128)
    if h_value.ndim != 2:
        raise ValueError("channel must have shape [bins, microphones]")
    if covariance.shape != (h_value.shape[0], h_value.shape[1], h_value.shape[1]):
        raise ValueError("noise covariance shape does not match channel")
    if not math.isfinite(signal_power) or signal_power <= 0.0:
        raise ValueError("signal_power must be finite and positive")
    result = np.empty(h_value.shape[0], dtype=np.float64)
    for index, (vector, matrix) in enumerate(zip(h_value, covariance)):
        if np.any(~np.isfinite(vector)) or np.any(~np.isfinite(matrix)):
            raise ValueError("channel or covariance contains non-finite values")
        hermitian = (matrix + matrix.conj().T) / 2.0
        scale = max(float(np.trace(hermitian).real) / len(vector), np.finfo(float).tiny)
        if float(np.min(np.linalg.eigvalsh(hermitian))) < -1e-9 * scale:
            raise ValueError("noise covariance is not positive semidefinite")
        regularized = hermitian + regularization_fraction * scale * np.eye(len(vector))
        try:
            solved = np.linalg.solve(regularized, vector)
        except np.linalg.LinAlgError as error:
            raise ValueError("noise covariance is singular after regularization") from error
        value = float(np.vdot(vector, solved).real * signal_power)
        if not math.isfinite(value) or value < 0.0:
            raise ValueError("noise covariance is not positive semidefinite")
        result[index] = value
    return result


def _ensure_payload_independent(mapping: Mapping[str, Any]) -> None:
    forbidden = FORBIDDEN_SAME_FRAME_FIELDS.intersection(mapping)
    if forbidden:
        raise ValueError(f"same-frame outcome fields are forbidden: {sorted(forbidden)}")


def estimate_scalar_sounder(
    sounder: Mapping[str, Any],
    *,
    raw_psd_snr_db: Sequence[float] | np.ndarray | None = None,
) -> dict[str, Any]:
    """Estimate available allocations from causally prior measurements only."""

    _ensure_payload_independent(sounder)
    snr = np.asarray(sounder["snr_db"], dtype=np.float64)
    if snr.ndim != 1 or len(snr) == 0:
        raise ValueError("sounder SNR must be one nonempty vector")
    allocations: dict[str, np.ndarray] = {
        "P0-uniform-incumbent": np.full(len(snr), 4, dtype=np.int8),
        "P2-pilot-gmi": pilot_gmi_allocation(snr),
    }
    if raw_psd_snr_db is not None:
        raw_snr = np.asarray(raw_psd_snr_db, dtype=np.float64)
        if raw_snr.shape != snr.shape:
            raise ValueError("raw-PSD SNR shape does not match sounder bins")
        allocations["P1-raw-psd"] = raw_psd_allocation(raw_snr)
    result: dict[str, Any] = {}
    for policy, values in allocations.items():
        counts = {str(bits): int(np.sum(values == bits)) for bits in (0, 2, 4)}
        result[policy] = {
            "allocation_counts": counts,
            "allocated_coded_bits_per_symbol": int(np.sum(values)),
            "active_fraction": float(np.mean(values > 0)),
            "allocation_sha256": hashlib.sha256(values.tobytes()).hexdigest(),
        }
    if raw_psd_snr_db is None:
        result["P1-raw-psd"] = {
            "status": "unavailable",
            "reason": "retained passive analysis has coarse bands, not aligned per-bin PSD",
        }
    result["P3-covariance-gmi"] = {
        **result["P2-pilot-gmi"],
        "fallback": "P2: retained corpus lacks per-bin noise covariance",
    }
    return result


def _rank5_profile(acquisition: Mapping[str, Any]) -> tuple[object, ...]:
    probe = acquisition["program"]["evm_probe"]
    return (
        probe["wave_sha256"],
        probe["modulation"],
        probe["fec"],
        probe["nfft"],
        probe["cp"],
        probe["symbols"],
    )


def audit_rank5_corpus(
    preregistration_path: Path,
    rank5_root: Path,
) -> dict[str, Any]:
    preregistration = load_json(preregistration_path)
    requested_cells = [item["id"] for item in preregistration["corpus"]["cells"]]
    profiles: set[tuple[object, ...]] = set()
    payload_hashes: set[str] = set()
    provenance: list[dict[str, object]] = [
        artifact_record(preregistration_path, semantically_inspected=True),
        artifact_record(Path(__file__), semantically_inspected=True),
    ]
    estimator_results: list[dict[str, Any]] = []
    for cell_id in requested_cells:
        cell = rank5_root / cell_id
        acquisition_path = cell / "acquisition.json"
        analysis_path = cell / "analysis.json"
        if not acquisition_path.is_file() or not analysis_path.is_file():
            raise FileNotFoundError(f"selected whole-run cell is incomplete: {cell}")
        acquisition = load_json(acquisition_path)
        analysis = load_json(analysis_path)
        profiles.add(_rank5_profile(acquisition))
        payload_hashes.add(str(acquisition["program"]["evm_probe"]["payload_sha256"]))
        for path in analysis["paths"]:
            for receiver in path["receivers"]:
                estimator_results.append(
                    {
                        "cell": cell_id,
                        "speaker": int(path["speaker"]),
                        "receiver": int(receiver["receiver"]),
                        "policies": estimate_scalar_sounder(receiver["sounder"]),
                    }
                )
        for retained in sorted(cell.iterdir()):
            if retained.is_file():
                provenance.append(
                    artifact_record(
                        retained,
                        semantically_inspected=retained.suffix == ".json",
                    )
                )
    for name in ("preregistration.json", "waveform-manifest.json"):
        retained = rank5_root / name
        provenance.append(artifact_record(retained, semantically_inspected=True))
    passive = rank5_root / "P0-passive" / "passive-analysis.json"
    provenance.append(artifact_record(passive, semantically_inspected=True))
    unique_profiles = len(profiles)
    unique_payloads = len(payload_hashes)
    failed_requirements = [
        "Only one uniform waveform/profile was physically transmitted; P1/P2/P3 outcomes are absent.",
        (
            "The same payload hash was reused across all selected data probes; "
            "bursts are not independently randomized."
        ),
        "No allocation descriptor/profile-ID airtime was transmitted or measured.",
        "No complete outcome-blind device/geometry holdout exists for this spike.",
        "The retained passive artifact has broadband covariance, not per-bin noise covariance for P3.",
    ]
    return {
        "schema": "cyrinx.spike8d.offline-audit.v1",
        "preregistration_sha256": sha256_file(preregistration_path),
        "decision": "formal-stop-not-counterfactually-identifiable",
        "counterfactual_identifiable": False,
        "ota_permitted": False,
        "confidence": {
            "identifiability_stop": "high",
            "policy_performance": "not estimable from this corpus",
            "basis": "content-addressed acquisition manifests and physical waveform hashes",
        },
        "quantitative_result": {
            "selected_whole_runs": len(requested_cells),
            "required_policy_count": len(POLICY_IDS),
            "unique_physically_transmitted_profile_count": unique_profiles,
            "physically_observed_alternate_policy_count": max(0, unique_profiles - 1),
            "unique_payload_hash_count": unique_payloads,
            "p10_net_goodput_delta_percent": None,
            "recovery_percent_by_alternate_policy": None,
            "oracle_regret_percent": None,
            "catastrophic_optimistic_selections": None,
        },
        "failed_requirements": failed_requirements,
        "estimator": {
            "status": "descriptive-only-unqualified",
            "calibration_offset_bits": -0.50,
            "same_frame_outcome_fields_consumed": [],
            "paths": estimator_results,
            "warning": (
                "Allocation counts are generated from repeated known-pilot SNR. They are not "
                "counterfactual decodes, delivered goodput, or a qualified rate predictor."
            ),
        },
        "provenance": sorted(provenance, key=lambda value: str(value["path"])),
        "raw_pcm_semantically_inspected": False,
    }


def _payload_seed(campaign_seed: int, cell: str, round_index: int, policy: str) -> int:
    value = f"{campaign_seed}:{cell}:{round_index}:{policy}".encode()
    return int.from_bytes(hashlib.sha256(value).digest()[:8], "big")


def build_acquisition_manifest(
    preregistration_path: Path,
    rank5_preregistration_path: Path,
) -> dict[str, Any]:
    preregistration = load_json(preregistration_path)
    rank5_preregistration = load_json(rank5_preregistration_path)
    campaign_seed = 2026071808
    orders = (
        POLICY_IDS,
        tuple(reversed(POLICY_IDS)),
        (POLICY_IDS[2], POLICY_IDS[0], POLICY_IDS[3], POLICY_IDS[1]),
        (POLICY_IDS[1], POLICY_IDS[3], POLICY_IDS[0], POLICY_IDS[2]),
    )
    cells = (
        {
            "id": "C0-optimal-calibration",
            "split": "training",
            "geometry": "Pixel 7a face-up over MacBook function key in marked optimal position",
        },
        {
            "id": "C1-lateral-20mm-holdout",
            "split": "outcome-blind-holdout",
            "geometry": "Pixel 7a face-up, translated 20 mm laterally from the marked optimal position",
        },
    )
    jobs: list[dict[str, Any]] = []
    for cell in cells:
        for round_index, order in enumerate(orders):
            barrier = f"{cell['id']}-round-{round_index:02d}-decode-barrier"
            for position, policy in enumerate(order):
                seed = _payload_seed(campaign_seed, cell["id"], round_index, policy)
                seed_text = str(seed)
                jobs.append(
                    {
                        "job_id": f"{cell['id']}-r{round_index:02d}-p{position}-{policy}",
                        "cell_id": cell["id"],
                        "split": cell["split"],
                        "round_index": round_index,
                        "within_round_position": position,
                        "policy_id": policy,
                        "payload_seed_hex": f"{seed:016x}",
                        "payload_seed_decimal": seed_text,
                        "payload_seed_commitment_sha256": hashlib.sha256(
                            seed_text.encode()
                        ).hexdigest(),
                        "selection_inputs": [
                            "route signature",
                            "same-cell passive noise recorded before the round",
                            "eight known QPSK sounder symbols ending before payload",
                            (
                                "32 randomized known 16-QAM probe symbols ending before payload; "
                                "first 16 fit H/noise and last 16 estimate bitwise GMI"
                            ),
                        ],
                        "selection_record_must_precede_payload": True,
                        "outcome_decode_barrier": barrier,
                    }
                )
    return {
        "schema": "cyrinx.spike8d.ota-acquisition-plan.v1",
        "purpose": "minimum causal identifiability screen; not final 7/8 qualification",
        "generated_from": {
            "spike8d_preregistration": artifact_record(
                preregistration_path, semantically_inspected=True
            ),
            "rank5_preregistration": artifact_record(
                rank5_preregistration_path, semantically_inspected=True
            ),
            "planner_source": artifact_record(Path(__file__), semantically_inspected=True),
        },
        "campaign_seed": campaign_seed,
        "direction": "MacBook-built-in-speaker-0-to-Pixel-7a-UNPROCESSED-mic-0",
        "cells": cells,
        "rounds_per_cell": len(orders),
        "policies": preregistration["policies"],
        "jobs": jobs,
        "physical": {
            "sample_rate_hz": 48000,
            "nfft": 2048,
            "cp_samples": 768,
            "active_band_hz": [1100, 23000],
            "known_sounder_symbols_per_round": 8,
            "known_randomized_qam_symbols_per_round": 32,
            "qam_probe_split": "first 16 fit H/noise; last 16 estimate bitwise GMI",
            "passive_noise_seconds_per_cell": 5.0,
            "per_bin_noise_estimator": (
                "2048-sample Hann windows, 50% overlap, mean complex cross-spectrum; "
                "record scalar PSD and full microphone covariance"
            ),
            "data_symbols_per_candidate": 64,
            "fec": "existing constraint-length-7 rate-1/2 convolutional code",
            "digital_sample_peak": 0.18,
            "mac_output_volume_percent": 30,
            "inter_candidate_gap_seconds": 0.25,
            "pre_roll_seconds": 0.70,
            "post_roll_seconds": 0.80,
            "mac_output": "MacBook Pro built-in speaker 0 only",
            "pixel_input": "48 kHz stereo UNPROCESSED retained; mic 0 primary, mic 1 diagnostic",
            "elgato_forbidden": True,
            "calibrated_spl": False,
        },
        "signalling": preregistration["allocation_signalling"],
        "execution_contract": {
            "policy_selection_record": (
                "canonical JSON plus SHA-256 written after the sounder and before each payload"
            ),
            "decode_barrier": (
                "do not decode or inspect any candidate outcome until all four candidates in "
                "the round have been captured"
            ),
            "independent_payloads": True,
            "position_balance": "each policy appears once in each ordinal position per cell",
            "route_checks": "before and after every round; abort on any change",
            "wall_clock_accounting": "include passive setup, sounder, descriptors, gaps, and padding",
            "retention": "raw TX/RX PCM, expected payload, route log, selection record, and timestamps",
        },
        "execution_state": {
            "ready": False,
            "ota_permitted": False,
            "blocked_on": [
                "deterministic mixed 0/2/4-bit bin-loading oracle and decoder do not yet exist",
                "allocation descriptor/profile-ID encoder and airtime accounting are not validated",
                "runner must enforce pre-payload selection commits and round decode barriers",
                "the delegated Spike 8d task is offline-only",
            ],
        },
        "promotion_limits": {
            "screen_gate": preregistration["gates"]["offline_replay_promotion_to_ota_screen"],
            "final_gate": preregistration["gates"]["final_promotion"],
            "minimum_plan_cannot_clear_final_gate": True,
        },
        "rank5_source_commit": rank5_preregistration["code_and_config"]["repository_head"],
    }


def validate_acquisition_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    if manifest.get("schema") != "cyrinx.spike8d.ota-acquisition-plan.v1":
        raise ValueError("unexpected acquisition-plan schema")
    jobs = list(manifest["jobs"])
    if len(jobs) != 32:
        raise ValueError("minimum plan must contain 32 candidate jobs")
    ids = [str(job["job_id"]) for job in jobs]
    seeds = [int(str(job["payload_seed_hex"]), 16) for job in jobs]
    if len(set(ids)) != len(ids) or len(set(seeds)) != len(seeds):
        raise ValueError("job IDs and payload seeds must be unique")
    for cell in manifest["cells"]:
        cell_jobs = [job for job in jobs if job["cell_id"] == cell["id"]]
        for policy in POLICY_IDS:
            positions = sorted(
                int(job["within_round_position"])
                for job in cell_jobs
                if job["policy_id"] == policy
            )
            if positions != [0, 1, 2, 3]:
                raise ValueError(f"policy order is not position-balanced: {cell['id']} {policy}")
        for round_index in range(4):
            round_jobs = [job for job in cell_jobs if job["round_index"] == round_index]
            barriers = {str(job["outcome_decode_barrier"]) for job in round_jobs}
            if len(round_jobs) != 4 or len(barriers) != 1:
                raise ValueError("each round needs four candidates behind one decode barrier")
    return {
        "valid": True,
        "jobs": len(jobs),
        "cells": len(manifest["cells"]),
        "ota_permitted": bool(manifest["execution_state"]["ota_permitted"]),
        "ready": bool(manifest["execution_state"]["ready"]),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    audit = commands.add_parser("audit", help="audit retained Rank 5 captures")
    audit.add_argument("--preregistration", type=Path, required=True)
    audit.add_argument("--rank5-root", type=Path, required=True)
    audit.add_argument("--out", type=Path, required=True)
    emit = commands.add_parser("emit-acquisition", help="emit the minimum causal OTA plan")
    emit.add_argument("--preregistration", type=Path, required=True)
    emit.add_argument("--rank5-preregistration", type=Path, required=True)
    emit.add_argument("--out", type=Path, required=True)
    validate = commands.add_parser("validate-acquisition", help="validate a generated plan")
    validate.add_argument("manifest", type=Path)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "audit":
        result = audit_rank5_corpus(args.preregistration, args.rank5_root)
        write_json(args.out, result)
    elif args.command == "emit-acquisition":
        result = build_acquisition_manifest(
            args.preregistration,
            args.rank5_preregistration,
        )
        validate_acquisition_manifest(result)
        write_json(args.out, result)
    else:
        result = validate_acquisition_manifest(load_json(args.manifest))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
