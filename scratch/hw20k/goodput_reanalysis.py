#!/usr/bin/env python3
"""Content-addressed offline reanalysis of completed goodput captures.

This tool never imports the HIL harness or audio transaction code. It reuses
the recorded slot detections and decode margin from a completed campaign, then
decodes the retained stereo PCM through one explicitly selected C dylib.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

import clib
import goodput_bench as G


SCHEMA = "cyrinx.goodput-reanalysis.v2"
SOURCE_SCHEMA = "cyrinx.goodput-ab-campaign.v1"
DATASET_ROLES = ("development", "held-out")
PILOT_SELECT_POLICY = "pilot-select01-v1"
RECEIVER_POLICIES = ("mic0", "mic1", "mrc01", PILOT_SELECT_POLICY)
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
        "utf-8"
    )


def sha256_bytes(value: bytes | bytearray | memoryview) -> str:
    digest = hashlib.sha256()
    digest.update(value)
    return digest.hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _resolve_recorded_path(recorded: Any, manifest_path: Path) -> Path:
    if not isinstance(recorded, str) or not recorded:
        raise ValueError("artifact record requires a nonempty path")
    path = Path(recorded).expanduser()
    if path.is_absolute():
        return path.resolve(strict=True)
    candidates = [REPOSITORY_ROOT / path, manifest_path.parent / path]
    existing = {candidate.resolve() for candidate in candidates if candidate.is_file()}
    if not existing:
        raise ValueError(f"recorded artifact path is missing: {recorded}")
    if len(existing) != 1:
        raise ValueError(f"recorded artifact path is ambiguous: {recorded}")
    return existing.pop()


def _verify_artifact(
    record: Mapping[str, Any],
    manifest_path: Path,
    *,
    label: str,
) -> Path:
    path = _resolve_recorded_path(record.get("path"), manifest_path)
    recorded_hash = record.get("sha256")
    if not isinstance(recorded_hash, str) or len(recorded_hash) != 64:
        raise ValueError(f"{label} lacks a valid recorded SHA-256")
    actual_hash = sha256_file(path)
    if actual_hash != recorded_hash:
        raise ValueError(
            f"{label} hash mismatch: recorded={recorded_hash}, actual={actual_hash}"
        )
    byte_count = record.get("byte_count")
    if byte_count is not None and int(byte_count) != path.stat().st_size:
        raise ValueError(f"{label} byte-count mismatch")
    return path


def _verify_completed_manifest(manifest: dict[str, Any]) -> None:
    if manifest.get("schema") != SOURCE_SCHEMA:
        raise ValueError(f"source manifest must use {SOURCE_SCHEMA}")
    if manifest.get("mode") != "execute":
        raise ValueError("source manifest is not an executed campaign")
    plan = manifest.get("plan")
    if not isinstance(plan, dict):
        raise ValueError("source manifest lacks a plan object")
    actual_plan_hash = sha256_bytes(canonical_json_bytes(plan))
    if actual_plan_hash != manifest.get("plan_sha256"):
        raise ValueError(
            "source plan hash mismatch: "
            f"recorded={manifest.get('plan_sha256')}, actual={actual_plan_hash}"
        )
    runs = plan.get("runs")
    results = manifest.get("results")
    if not isinstance(runs, list) or not isinstance(results, list) or not runs:
        raise ValueError("source manifest requires nonempty plan runs and results")
    if len(results) != len(runs):
        raise ValueError("source manifest is incomplete: result/run count differs")
    if any(not isinstance(result, dict) or result.get("status") != "complete" for result in results):
        raise ValueError("source manifest contains a failed or incomplete result")
    if {run.get("run_id") for run in runs} != {result.get("run_id") for result in results}:
        raise ValueError("source manifest run/result identities differ")
    summary = manifest.get("summary")
    if not isinstance(summary, dict):
        raise ValueError("completed source manifest lacks a summary")
    if int(summary.get("complete_runs", -1)) != len(runs) or int(
        summary.get("failed_runs", -1)
    ) != 0:
        raise ValueError("source summary does not describe a complete campaign")


def _load_bound_result(
    manifest_result: dict[str, Any], manifest_path: Path
) -> tuple[dict[str, Any], Path, str]:
    artifact_record = manifest_result.get("result_artifact")
    if not isinstance(artifact_record, dict):
        raise ValueError(f"{manifest_result.get('run_id')} lacks result_artifact binding")
    result_path = _verify_artifact(
        artifact_record,
        manifest_path,
        label=f"{manifest_result.get('run_id')} result",
    )
    result = _load_json(result_path)
    embedded = dict(manifest_result)
    embedded.pop("result_artifact", None)
    if canonical_json_bytes(embedded) != canonical_json_bytes(result):
        raise ValueError(f"{manifest_result.get('run_id')} manifest/result content mismatch")
    return result, result_path, artifact_record["sha256"]


def _profile_context(
    profile_document: dict[str, Any], codec: clib.BulkCodec
) -> tuple[G.PHYConfig, G.Geometry, Any]:
    config_document = profile_document.get("config")
    if not isinstance(config_document, dict):
        raise ValueError("profile lacks config object")
    try:
        config = G.PHYConfig(**config_document)
    except TypeError as error:
        raise ValueError(f"profile config fields differ from the referee: {error}") from error
    geometry = G.compute_geometry(config)
    if profile_document.get("geometry") != asdict(geometry):
        raise ValueError(f"{profile_document.get('profile_id')} recorded geometry mismatch")
    amplitude = float(profile_document["amplitude"])
    clip_sigma = float(profile_document["clip_sigma"])
    if not math.isfinite(amplitude) or not math.isfinite(clip_sigma):
        raise ValueError("profile amplitude and clip sigma must be finite")
    c_config = clib.make_cfg(
        bits_per_bin=config.bits_per_bin,
        rate=config.code_rate,
        n_sym=config.data_symbols,
        f_lo=config.f_lo_hz,
        f_hi=config.f_hi_hz,
        nfft=config.nfft,
        cp=config.cp_samples,
        sr=config.sample_rate_hz,
        amp=amplitude,
        clip_sigma=clip_sigma,
        pilot_every=config.pilot_every,
    )
    c_geometry = codec.geometry(c_config)
    checks = {
        "bin_lo": (c_geometry.bin_lo, geometry.bin_lo),
        "bin_hi": (c_geometry.bin_hi, geometry.bin_hi),
        "n_blocks": (c_geometry.n_blocks, geometry.crc_blocks),
        "payload_bytes": (c_geometry.payload_bytes, geometry.payload_bytes),
        "frame_samples": (c_geometry.frame_samples, geometry.frame_samples),
    }
    mismatches = [
        f"{name}: dylib={got}, manifest={expected}"
        for name, (got, expected) in checks.items()
        if got != expected
    ]
    if mismatches:
        raise ValueError("explicit decoder geometry mismatch: " + "; ".join(mismatches))
    return config, geometry, c_config


def _load_capture(
    result: dict[str, Any], manifest_path: Path
) -> tuple[np.ndarray, dict[str, Any]]:
    record = result.get("artifacts", {}).get("capture")
    if not isinstance(record, dict):
        raise ValueError(f"{result.get('run_id')} lacks capture artifact metadata")
    path = _verify_artifact(record, manifest_path, label=f"{result.get('run_id')} capture")
    if int(record.get("channels", -1)) != 2:
        raise ValueError("goodput reanalysis requires the recorded stereo capture")
    raw = np.fromfile(path, dtype="<i2")
    if len(raw) % 2:
        raise ValueError("capture has an odd PCM16 value count")
    capture = raw.reshape(-1, 2).astype(np.float32) / 32768.0
    if int(record.get("frames", -1)) != len(capture):
        raise ValueError("capture frame count differs from its artifact record")
    if int(record.get("pcm16_values", -1)) != raw.size:
        raise ValueError("capture PCM16 value count differs from its artifact record")
    return capture, {
        "path": str(path),
        "sha256": record["sha256"],
        "byte_count": path.stat().st_size,
        "frames": len(capture),
        "channels": 2,
    }


def _load_expected_payloads(
    result: dict[str, Any],
    run_plan: dict[str, Any],
    manifest_path: Path,
    *,
    frames: int,
    payload_bytes: int,
) -> tuple[dict[int, bytes], list[dict[str, Any]]]:
    records = result.get("artifacts", {}).get("expected_payloads")
    plan_records = run_plan.get("payloads")
    if not isinstance(records, list) or not isinstance(plan_records, list):
        raise ValueError("run lacks expected-payload artifact records")
    by_frame = {int(record["frame_index"]): record for record in records}
    plan_by_frame = {int(record["frame_index"]): record for record in plan_records}
    expected_frames = set(range(frames))
    if set(by_frame) != expected_frames or set(plan_by_frame) != expected_frames:
        raise ValueError("expected payload records do not cover each scheduled frame exactly once")
    payloads: dict[int, bytes] = {}
    verified_records = []
    for frame_index in range(frames):
        record = by_frame[frame_index]
        plan_record = plan_by_frame[frame_index]
        for field in ("sha256", "byte_count"):
            if record.get(field) != plan_record.get(field):
                raise ValueError(
                    f"frame {frame_index} expected payload differs from the hashed run plan"
                )
        path = _verify_artifact(
            record,
            manifest_path,
            label=f"{result.get('run_id')} expected frame {frame_index}",
        )
        payload = path.read_bytes()
        if len(payload) != payload_bytes or int(record["byte_count"]) != payload_bytes:
            raise ValueError(f"frame {frame_index} expected payload geometry mismatch")
        payloads[frame_index] = payload
        verified_records.append(
            {
                "frame_index": frame_index,
                "path": str(path),
                "sha256": record["sha256"],
                "byte_count": len(payload),
            }
        )
    return payloads, verified_records


def _recorded_detections(
    result: dict[str, Any], *, frames: int
) -> dict[int, dict[str, Any]]:
    timing = result.get("timing")
    if not isinstance(timing, dict) or not isinstance(timing.get("detections"), list):
        raise ValueError("completed result lacks recorded timing detections")
    detections = timing["detections"]
    if int(timing.get("detected_slots", -1)) != len(detections):
        raise ValueError("recorded detected-slot count is inconsistent")
    if int(timing.get("scheduled_slots", -1)) != frames:
        raise ValueError("recorded scheduled-slot count differs from the plan")
    by_frame: dict[int, dict[str, Any]] = {}
    for detection in detections:
        if not isinstance(detection, dict):
            raise ValueError("timing detection must be an object")
        frame_index = int(detection["frame_index"])
        detected_sample = int(detection["detected_sample"])
        if frame_index not in range(frames) or frame_index in by_frame:
            raise ValueError("recorded timing detections have duplicate/out-of-range frame IDs")
        if detected_sample < 0:
            raise ValueError("recorded detection sample must be nonnegative")
        by_frame[frame_index] = detection
    return by_frame


def _decode_policy(
    codec: clib.BulkCodec,
    policy: str,
    c_config: Any,
    capture: np.ndarray,
    start: int,
    stop: int,
) -> Mapping[str, Any] | None:
    if policy == "mic0":
        return codec.decode(c_config, capture[start:stop, 0])
    if policy == "mic1":
        return codec.decode(c_config, capture[start:stop, 1])
    if policy == "mrc01":
        return codec.decode2(c_config, capture[start:stop, 0], capture[start:stop, 1])
    if policy == PILOT_SELECT_POLICY:
        return codec.decode2_auto_v1(
            c_config,
            capture[start:stop, 0],
            capture[start:stop, 1],
        )
    raise ValueError(f"unknown receiver policy: {policy}")


def _assert_auto_v1_selected_output_parity(
    decoded_by_policy: Mapping[str, Mapping[str, Any] | None],
    *,
    frame_index: int,
) -> None:
    automatic = decoded_by_policy[PILOT_SELECT_POLICY]
    if automatic is None:
        if any(decoded_by_policy[policy] is not None for policy in ("mic0", "mrc01")):
            raise ValueError(
                f"frame {frame_index}: auto-v1 failed while a selectable receiver decoded"
            )
        return
    diagnostics = automatic.get("automatic_diversity")
    if not isinstance(diagnostics, Mapping):
        raise ValueError(f"frame {frame_index}: auto-v1 diagnostics are missing")
    frozen = {
        "abi_version": clib.AUTO_V1_DIAGNOSTICS_ABI_VERSION,
        "policy_version": clib.AUTO_V1_POLICY_VERSION,
        "maximum_mrc_to_primary_pilot_rms_ratio": (
            clib.AUTO_V1_MAX_MRC_TO_PRIMARY_PILOT_RMS_RATIO
        ),
        "payload_or_crc_used_for_selection": False,
        "data_bins_used_for_selection": False,
    }
    for field, expected in frozen.items():
        if diagnostics.get(field) != expected:
            raise ValueError(f"frame {frame_index}: auto-v1 {field} is not frozen")
    selected = diagnostics.get("selected_receiver")
    if selected not in ("mic0", "mrc01"):
        raise ValueError(f"frame {frame_index}: auto-v1 selected receiver is invalid")
    reference = decoded_by_policy[str(selected)]
    if reference is None:
        raise ValueError(f"frame {frame_index}: selected receiver decode is unavailable")
    for field in ("payload", "block_valid", "blocks_ok", "blocks_total", "evm"):
        if automatic.get(field) != reference.get(field):
            raise ValueError(
                f"frame {frame_index}: auto-v1 {field} differs from selected receiver"
            )


def _intersection_record(
    decoded: Mapping[str, Any] | None,
    expected: bytes,
    *,
    blocks: int,
) -> dict[str, Any]:
    if decoded is None:
        false_mask = [False] * blocks
        return {
            "decode_status": "decoder-returned-no-frame",
            "crc_valid_mask": false_mask,
            "byte_identity_mask": false_mask,
            "verified_intersection_mask": false_mask,
            "crc_valid_blocks": 0,
            "byte_identical_blocks": 0,
            "verified_blocks": 0,
            "blocks_total": blocks,
            "evm": None,
            "automatic_diversity": None,
            "decoded_payload_sha256": None,
            "decoded_payload_byte_count": 0,
        }
    payload = decoded.get("payload")
    mask = decoded.get("block_valid")
    if not isinstance(payload, bytes) or len(payload) != len(expected):
        raise ValueError("decoder returned an unexpected payload length")
    if not isinstance(mask, list) or len(mask) != blocks:
        raise ValueError("explicit decoder did not return an ordered CRC-validity mask")
    if int(decoded.get("blocks_total", -1)) != blocks:
        raise ValueError("decoder block denominator differs from manifest geometry")
    crc_mask = [bool(value) for value in mask]
    if sum(crc_mask) != int(decoded.get("blocks_ok", -1)):
        raise ValueError("decoder CRC count differs from its ordered validity mask")
    identity_mask = []
    for block_index in range(blocks):
        start = block_index * G.CRC_BLOCK_BYTES
        stop = start + G.CRC_BLOCK_BYTES
        identity_mask.append(payload[start:stop] == expected[start:stop])
    intersection = [
        crc_valid and byte_identical
        for crc_valid, byte_identical in zip(crc_mask, identity_mask, strict=True)
    ]
    evm = float(decoded["evm"])
    if not math.isfinite(evm) or evm < 0:
        raise ValueError("decoder EVM must be finite and nonnegative")
    return {
        "decode_status": "decoded",
        "crc_valid_mask": crc_mask,
        "byte_identity_mask": identity_mask,
        "verified_intersection_mask": intersection,
        "crc_valid_blocks": sum(crc_mask),
        "byte_identical_blocks": sum(identity_mask),
        "verified_blocks": sum(intersection),
        "blocks_total": blocks,
        "evm": evm,
        "automatic_diversity": decoded.get("automatic_diversity"),
        "decoded_payload_sha256": sha256_bytes(payload),
        "decoded_payload_byte_count": len(payload),
    }


def _missing_slot_record(*, frame_index: int, blocks: int) -> dict[str, Any]:
    false_mask = [False] * blocks
    return {
        "frame_index": frame_index,
        "recorded_detection": None,
        "decode_window": None,
        "decode_status": "missing-recorded-slot",
        "crc_valid_mask": false_mask,
        "byte_identity_mask": false_mask,
        "verified_intersection_mask": false_mask,
        "crc_valid_blocks": 0,
        "byte_identical_blocks": 0,
        "verified_blocks": 0,
        "blocks_total": blocks,
        "evm": None,
        "automatic_diversity": None,
        "decoded_payload_sha256": None,
        "decoded_payload_byte_count": 0,
    }


def _automatic_diversity_slot_accounting(
    records: Sequence[Mapping[str, Any]],
    *,
    scheduled_slots: int,
) -> dict[str, Any]:
    """Partition every scheduled reanalysis slot by selector outcome."""
    receivers = {"mic0": 0, "mrc01": 0}
    reasons = {
        reason: 0
        for reason in (
            "mrc_improved",
            "primary_margin_not_met",
            "insufficient_pilots",
            "nonfinite_score",
            "resource_failure",
            "second_unavailable",
        )
    }
    outcomes = {
        "diagnostics_observed": 0,
        "missing_timing_detection": 0,
        "decoder_returned_no_frame": 0,
        "failed_run_unresolved": 0,
        "run_not_yet_attempted": 0,
    }
    if len(records) != scheduled_slots:
        raise ValueError("reanalysis automatic-diversity records do not cover schedule")
    for record in records:
        diagnostics = record.get("automatic_diversity")
        if isinstance(diagnostics, Mapping):
            receiver = str(diagnostics.get("selected_receiver"))
            reason = str(diagnostics.get("selection_reason"))
            if receiver not in receivers or reason not in reasons:
                raise ValueError("reanalysis automatic-diversity diagnostics are invalid")
            receivers[receiver] += 1
            reasons[reason] += 1
            outcomes["diagnostics_observed"] += 1
            continue
        status = record.get("decode_status")
        if status == "missing-recorded-slot":
            outcomes["missing_timing_detection"] += 1
        elif status == "decoder-returned-no-frame":
            outcomes["decoder_returned_no_frame"] += 1
        else:
            raise ValueError("decoded automatic-diversity frame lacks diagnostics")
    observed = outcomes["diagnostics_observed"]
    if sum(outcomes.values()) != scheduled_slots:
        raise AssertionError("reanalysis selector outcomes do not cover schedule")
    if sum(receivers.values()) != observed or sum(reasons.values()) != observed:
        raise AssertionError("reanalysis selector diagnostic totals disagree")
    missing_or_failed = scheduled_slots - observed
    receivers["missing_or_failed"] = missing_or_failed
    reasons["missing_or_failed"] = missing_or_failed
    return {
        "scheduled_slots": scheduled_slots,
        "observed_diagnostics": observed,
        "selected_receiver": receivers,
        "selection_reason": reasons,
        "slot_outcomes": outcomes,
    }


def _analyze_run(
    *,
    codec: clib.BulkCodec,
    manifest_path: Path,
    plan: dict[str, Any],
    run_plan: dict[str, Any],
    profile_document: dict[str, Any],
    manifest_result: dict[str, Any],
    schedule: G.BurstSchedule,
) -> dict[str, Any]:
    result, result_path, result_sha256 = _load_bound_result(manifest_result, manifest_path)
    identity_fields = ("run_id", "profile_id", "pair_index", "within_pair_order")
    for field in identity_fields:
        if result.get(field) != run_plan.get(field):
            raise ValueError(f"{run_plan.get('run_id')} result differs on {field}")
    config, geometry, c_config = _profile_context(profile_document, codec)
    if geometry.frame_samples != int(run_plan["scheduled_span_samples"]) and schedule.frames == 1:
        raise ValueError("one-frame scheduled span differs from reconstructed geometry")
    capture, capture_record = _load_capture(result, manifest_path)
    expected, expected_records = _load_expected_payloads(
        result,
        run_plan,
        manifest_path,
        frames=schedule.frames,
        payload_bytes=geometry.payload_bytes,
    )
    detections = _recorded_detections(result, frames=schedule.frames)
    margin = round(
        float(plan["capture_policy"]["decode_margin_ms"]) * config.sample_rate_hz / 1000
    )
    if margin < 0:
        raise ValueError("recorded decode margin must be nonnegative")

    policy_frames: dict[str, list[dict[str, Any]]] = {
        policy: [] for policy in RECEIVER_POLICIES
    }
    for frame_index in range(schedule.frames):
        detection = detections.get(frame_index)
        if detection is None:
            for policy in RECEIVER_POLICIES:
                policy_frames[policy].append(
                    _missing_slot_record(frame_index=frame_index, blocks=geometry.crc_blocks)
                )
            continue
        detected_sample = int(detection["detected_sample"])
        start = max(0, detected_sample - margin)
        stop = min(len(capture), detected_sample + geometry.frame_samples + margin)
        if stop <= start:
            raise ValueError("recorded timing produced an empty decode window")
        decoded_by_policy = {
            policy: _decode_policy(codec, policy, c_config, capture, start, stop)
            for policy in RECEIVER_POLICIES
        }
        _assert_auto_v1_selected_output_parity(
            decoded_by_policy, frame_index=frame_index
        )
        for policy, decoded in decoded_by_policy.items():
            record = _intersection_record(
                decoded,
                expected[frame_index],
                blocks=geometry.crc_blocks,
            )
            policy_frames[policy].append(
                {
                    "frame_index": frame_index,
                    "recorded_detection": detection,
                    "decode_window": {
                        "start_sample": start,
                        "stop_sample_exclusive": stop,
                        "sample_count": stop - start,
                        "margin_samples": margin,
                    },
                    **record,
                }
            )

    policy_summaries = {}
    for policy, records in policy_frames.items():
        verified_blocks = sum(record["verified_blocks"] for record in records)
        crc_valid_blocks = sum(record["crc_valid_blocks"] for record in records)
        identity_blocks = sum(record["byte_identical_blocks"] for record in records)
        metrics = G.metric_summary(geometry, schedule, verified_blocks)
        evms = [record["evm"] for record in records if record["evm"] is not None]
        automatic_accounting = (
            _automatic_diversity_slot_accounting(
                records, scheduled_slots=schedule.frames
            )
            if policy == PILOT_SELECT_POLICY
            else None
        )
        policy_summaries[policy] = {
            "verification_rule": "ordered CRC-valid mask AND same-position byte identity",
            "verified_blocks": verified_blocks,
            "crc_valid_blocks": crc_valid_blocks,
            "byte_identical_blocks": identity_blocks,
            "total_scheduled_blocks": schedule.frames * geometry.crc_blocks,
            "decoded_slots": len(evms),
            "scheduled_slots": schedule.frames,
            "mean_evm_diagnostic": None if not evms else sum(evms) / len(evms),
            "automatic_diversity_selected_receiver_counts": (
                {} if automatic_accounting is None else automatic_accounting["selected_receiver"]
            ),
            "automatic_diversity_selection_reason_counts": (
                {} if automatic_accounting is None else automatic_accounting["selection_reason"]
            ),
            "automatic_diversity_slot_accounting": automatic_accounting,
            "metrics": metrics,
        }

    primary_receiver = plan["measurement_contract"]["primary_receiver"]
    if result.get("primary_receiver") != primary_receiver:
        raise ValueError("completed result primary receiver differs from the source plan")
    return {
        "run_id": run_plan["run_id"],
        "profile_id": run_plan["profile_id"],
        "pair_index": run_plan["pair_index"],
        "within_pair_order": run_plan["within_pair_order"],
        "source_result": {
            "path": str(result_path),
            "sha256": result_sha256,
        },
        "capture": capture_record,
        "expected_payloads": expected_records,
        "profile": {
            "config": asdict(config),
            "geometry": asdict(geometry),
            "amplitude": profile_document["amplitude"],
            "clip_sigma": profile_document["clip_sigma"],
        },
        "timing_reuse": {
            "source_policy": result["timing"].get("policy"),
            "recorded_acquisition_anchor": result["timing"].get("acquisition_anchor"),
            "recorded_detections_only": True,
            "global_reacquisition_performed": False,
            "payload_or_crc_used_for_slot_selection": False,
            "decoder_within_window_sync_note": (
                "the C decoder retains its normal chirp/fine synchronization inside each "
                "fixed recorded window; it does not choose or move scheduled slots"
            ),
            "decode_margin_ms": plan["capture_policy"]["decode_margin_ms"],
            "decode_margin_samples": margin,
            "recorded_detected_slots": len(detections),
            "scheduled_slots": schedule.frames,
        },
        "receiver_frames": policy_frames,
        "policy_summaries": policy_summaries,
        "primary_receiver": primary_receiver,
        "primary_result": policy_summaries[primary_receiver],
    }


def _source_hashes(decoder_path: Path) -> dict[str, dict[str, Any]]:
    paths = {
        "reanalysis": (
            Path(__file__).resolve(),
            "executed_or_imported_reanalysis_component",
        ),
        "c_binding": (
            Path(clib.__file__).resolve(),
            "executed_or_imported_reanalysis_component",
        ),
        "goodput_referee": (
            Path(G.__file__).resolve(),
            "executed_or_imported_reanalysis_component",
        ),
        "c_bulk_source": (
            REPOSITORY_ROOT / "Sources/CCyrinx/cyrinx_bulk.c",
            "repository_context_only_not_proven_to_build_selected_decoder",
        ),
        "c_bulk_header": (
            REPOSITORY_ROOT / "Sources/CCyrinx/include/cyrinx/cyrinx_bulk.h",
            "repository_context_only_not_proven_to_build_selected_decoder",
        ),
        "decoder_dylib": (
            decoder_path,
            "authoritative_executed_decoder",
        ),
    }
    records = {}
    for label, (path, role) in paths.items():
        path = path.resolve(strict=True)
        if not path.is_file():
            raise ValueError(f"reanalysis provenance input is not a file: {label}={path}")
        records[label] = {
            "path": str(path),
            "sha256": sha256_file(path),
            "byte_count": path.stat().st_size,
            "role": role,
        }
    return records


def build_reanalysis(
    manifest_path: Path,
    decoder_dylib: Path,
    *,
    dataset_role: str,
    label_note: str = "",
) -> dict[str, Any]:
    if dataset_role not in DATASET_ROLES:
        raise ValueError(f"dataset_role must be one of {DATASET_ROLES}")
    manifest_path = manifest_path.expanduser().resolve(strict=True)
    decoder_path = decoder_dylib.expanduser().resolve(strict=True)
    if not manifest_path.is_file() or not decoder_path.is_file():
        raise ValueError("manifest and decoder dylib must be files")
    manifest_hash = sha256_file(manifest_path)
    decoder_hash_before = sha256_file(decoder_path)
    manifest = _load_json(manifest_path)
    _verify_completed_manifest(manifest)
    codec = clib.BulkCodec(decoder_path)
    if not codec.has_block_validity_api:
        raise ValueError("explicit decoder lacks required ordered block-validity APIs")
    if not codec.has_auto_v1_api:
        raise ValueError("explicit decoder lacks required automatic-diversity policy-v1 APIs")
    if codec.library_path != decoder_path:
        raise ValueError("explicit decoder loaded a different binary path")

    plan = manifest["plan"]
    schedule = G.BurstSchedule(**plan["schedule"])
    schedule.validate()
    profiles = plan.get("profiles")
    if not isinstance(profiles, list) or len(profiles) != 2:
        raise ValueError("source plan must contain exactly two profiles")
    profile_by_id = {profile.get("profile_id"): profile for profile in profiles}
    if len(profile_by_id) != len(profiles):
        raise ValueError("source profiles require unique IDs")
    run_by_id = {run.get("run_id"): run for run in plan["runs"]}
    result_by_id = {result.get("run_id"): result for result in manifest["results"]}

    run_reports = []
    for run in plan["runs"]:
        profile_id = run.get("profile_id")
        if profile_id not in profile_by_id:
            raise ValueError(f"run references unknown profile: {profile_id}")
        run_reports.append(
            _analyze_run(
                codec=codec,
                manifest_path=manifest_path,
                plan=plan,
                run_plan=run_by_id[run["run_id"]],
                profile_document=profile_by_id[profile_id],
                manifest_result=result_by_id[run["run_id"]],
                schedule=schedule,
            )
        )

    decoder_hash_after = sha256_file(decoder_path)
    if decoder_hash_after != decoder_hash_before:
        raise RuntimeError("explicit decoder dylib changed during reanalysis")
    source_hashes = _source_hashes(decoder_path)
    if source_hashes["decoder_dylib"]["sha256"] != decoder_hash_before:
        raise RuntimeError("decoder provenance hash changed after decode")

    original_decoder = (
        manifest.get("execution_provenance", {})
        .get("source_hashes", {})
        .get("c_bulk_dylib")
    )
    recorded_campaign_decoder = None
    if isinstance(original_decoder, Mapping):
        recorded_campaign_decoder = {
            "recorded_path": original_decoder.get("path"),
            "recorded_sha256": original_decoder.get("sha256"),
            "recorded_byte_count": original_decoder.get("byte_count"),
            "path_semantics": (
                "historical campaign metadata copied without resolving or re-verifying the "
                "recorded path; decoder.path identifies the binary executed by this reanalysis"
            ),
        }
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "classification": {
            "dataset_role": dataset_role,
            "label_supplied_by_cli": True,
            "label_note": label_note,
            "claim_scope": (
                "development-only; excluded from held-out validation and headline evidence"
                if dataset_role == "development"
                else "caller-labelled held-out; validity still requires a decoder frozen before capture"
            ),
        },
        "source_manifest": {
            "path": str(manifest_path),
            "sha256": manifest_hash,
            "plan_sha256": manifest["plan_sha256"],
            "execution_id": manifest.get("execution_id"),
            "measurement_class": plan["measurement_contract"]["measurement_class"],
            "recorded_campaign_decoder": recorded_campaign_decoder,
        },
        "decoder": {
            "selection": "explicit CLI path; no search or environment override",
            "path": str(decoder_path),
            "sha256_before_decode": decoder_hash_before,
            "sha256_after_decode": decoder_hash_after,
            "ordered_block_validity_api": codec.has_block_validity_api,
            "automatic_diversity_policy_v1_api": codec.has_auto_v1_api,
        },
        "source_hashes": source_hashes,
        "timing_policy": {
            "recorded_slot_detections_reused": True,
            "recorded_decode_margin_reused": True,
            "new_global_acquisition": False,
            "payload_aware_slot_selection": False,
        },
        "runs": run_reports,
        "limitations": [
            "development captures used to select decoder logic cannot become held-out by relabelling",
            "the held-out label is declarative and does not prove decoder-freeze chronology",
            (
                "repository C source/header hashes are contextual and do not prove that they built "
                "the selected decoder dylib; source-to-binary binding requires separate evidence"
            ),
            (
                "the recorded campaign decoder path is historical metadata and is not treated as "
                "a live or verified path by this reanalysis"
            ),
            "EVM is diagnostic and never substitutes for ordered CRC and byte-identity verification",
            (
                "mic1, raw MRC, and pilot-select01-v1 are diagnostic unless the source plan "
                "predeclared that exact receiver policy as primary"
            ),
        ],
    }
    report["report_sha256"] = sha256_bytes(canonical_json_bytes(report))
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="completed execute-mode goodput manifest")
    parser.add_argument(
        "--decoder-dylib",
        type=Path,
        required=True,
        help="exact libcyrinxbulk dylib to load; its content hash is retained",
    )
    parser.add_argument(
        "--dataset-role",
        choices=DATASET_ROLES,
        required=True,
        help="explicit development or held-out classification",
    )
    parser.add_argument("--label-note", default="", help="optional classification context")
    parser.add_argument("--out", type=Path, required=True, help="new output JSON; never overwritten")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    output_path = args.out.expanduser().resolve()
    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite reanalysis artifact: {output_path}")
    report = build_reanalysis(
        args.manifest,
        args.decoder_dylib,
        dataset_role=args.dataset_role,
        label_note=args.label_note,
    )
    payload = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(payload, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
