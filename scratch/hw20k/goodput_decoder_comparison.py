#!/usr/bin/env python3
"""Compare legacy and current decoders on one predeclared retained-capture campaign.

The comparator is deliberately offline: it reads two content-addressed
``goodput_reanalysis.py`` v2 reports and their common source campaign, verifies
their bindings, and scores only the candidate profile named by the campaign's
``post_capture_decoder_confirmation`` contract.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence


SCHEMA = "cyrinx.goodput-decoder-comparison.v1"
REANALYSIS_SCHEMA = "cyrinx.goodput-reanalysis.v2"
SOURCE_SCHEMA = "cyrinx.goodput-ab-campaign.v1"
PILOT_SELECT_POLICY = "pilot-select01-v1"
EXACT_POSITION_SCORE = "ordered CRC-valid mask AND same-position byte identity"
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


def canonical_json_bytes(value: Any) -> bytes:
    """Return the repository's finite, compact, sorted-key JSON encoding."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


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


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key!r}")
        result[key] = value
    return result


def _reject_nonfinite_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number is not permitted: {value}")


def _load_json_snapshot(path: Path, *, label: str) -> tuple[dict[str, Any], Path, str]:
    resolved = path.expanduser().resolve(strict=True)
    if not resolved.is_file():
        raise ValueError(f"{label} is not a file: {resolved}")
    payload = resolved.read_bytes()
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not valid UTF-8 JSON: {resolved}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain one JSON object")
    return value, resolved, sha256_bytes(payload)


def _mapping(value: Any, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _list(value: Any, *, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    return value


def _string(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a nonempty string")
    return value


def _integer(value: Any, *, label: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{label} must be an integer >= {minimum}")
    return value


def _boolean(value: Any, *, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be a boolean")
    return value


def _sha256(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _same_json(left: Any, right: Any) -> bool:
    return canonical_json_bytes(left) == canonical_json_bytes(right)


def _verify_reanalysis_self_hash(report: dict[str, Any], *, label: str) -> str:
    if report.get("schema") != REANALYSIS_SCHEMA:
        raise ValueError(f"{label} must use {REANALYSIS_SCHEMA}")
    recorded = _sha256(report.get("report_sha256"), label=f"{label} report_sha256")
    unhashed = dict(report)
    unhashed.pop("report_sha256")
    actual = sha256_bytes(canonical_json_bytes(unhashed))
    if actual != recorded:
        raise ValueError(
            f"{label} report self-hash mismatch: recorded={recorded}, actual={actual}"
        )
    return recorded


def _verify_source_manifest(
    manifest: dict[str, Any],
    *,
    manifest_file_sha256: str,
) -> dict[str, Any]:
    if manifest.get("schema") != SOURCE_SCHEMA:
        raise ValueError(f"source manifest must use {SOURCE_SCHEMA}")
    if manifest.get("mode") != "execute":
        raise ValueError("source manifest must be an executed campaign")
    execution_id = _string(manifest.get("execution_id"), label="source execution_id")
    plan = _mapping(manifest.get("plan"), label="source plan")
    plan_sha256 = _sha256(manifest.get("plan_sha256"), label="source plan_sha256")
    actual_plan_sha256 = sha256_bytes(canonical_json_bytes(plan))
    if actual_plan_sha256 != plan_sha256:
        raise ValueError(
            "source plan hash mismatch: "
            f"recorded={plan_sha256}, actual={actual_plan_sha256}"
        )

    profiles = _list(plan.get("profiles"), label="source plan profiles")
    profile_by_id: dict[str, Mapping[str, Any]] = {}
    for index, profile_value in enumerate(profiles):
        profile = _mapping(profile_value, label=f"source profile {index}")
        profile_id = _string(profile.get("profile_id"), label=f"source profile {index} ID")
        if profile_id in profile_by_id:
            raise ValueError(f"source profile ID is duplicated: {profile_id}")
        profile_by_id[profile_id] = profile

    runs = _list(plan.get("runs"), label="source plan runs")
    if not runs:
        raise ValueError("source plan must contain runs")
    run_by_id: dict[str, Mapping[str, Any]] = {}
    for index, run_value in enumerate(runs):
        run = _mapping(run_value, label=f"source run {index}")
        run_id = _string(run.get("run_id"), label=f"source run {index} ID")
        if run_id in run_by_id:
            raise ValueError(f"source run ID is duplicated: {run_id}")
        profile_id = _string(run.get("profile_id"), label=f"{run_id} profile_id")
        if profile_id not in profile_by_id:
            raise ValueError(f"{run_id} references an unknown profile")
        _integer(run.get("pair_index"), label=f"{run_id} pair_index")
        _integer(run.get("within_pair_order"), label=f"{run_id} within_pair_order")
        run_by_id[run_id] = run

    results = _list(manifest.get("results"), label="source results")
    result_by_id: dict[str, Mapping[str, Any]] = {}
    for index, result_value in enumerate(results):
        result = _mapping(result_value, label=f"source result {index}")
        run_id = _string(result.get("run_id"), label=f"source result {index} run_id")
        if run_id in result_by_id:
            raise ValueError(f"source result ID is duplicated: {run_id}")
        if result.get("status") != "complete":
            raise ValueError(f"source result is not complete: {run_id}")
        result_by_id[run_id] = result
    if set(result_by_id) != set(run_by_id):
        raise ValueError("source manifest run/result identities differ")
    summary = _mapping(manifest.get("summary"), label="source summary")
    if _integer(summary.get("complete_runs"), label="source complete_runs") != len(runs):
        raise ValueError("source summary complete-run count differs from the plan")
    if _integer(summary.get("failed_runs"), label="source failed_runs") != 0:
        raise ValueError("source summary records failed runs")

    schedule = _mapping(plan.get("schedule"), label="source schedule")
    scheduled_slots = _integer(schedule.get("frames"), label="source schedule frames", minimum=1)
    measurement = _mapping(
        plan.get("measurement_contract"), label="source measurement_contract"
    )
    build_intent = _mapping(
        measurement.get("decoder_build_intent"), label="source decoder_build_intent"
    )
    implementation_intent = _mapping(
        measurement.get("campaign_implementation_intent"),
        label="source campaign_implementation_intent",
    )
    primary_receiver = _string(
        measurement.get("primary_receiver"), label="source primary receiver"
    )
    confirmation = _mapping(
        measurement.get("post_capture_decoder_confirmation"),
        label="post_capture_decoder_confirmation",
    )
    candidate_profile_id = _string(
        confirmation.get("profile_id"), label="confirmation profile_id"
    )
    if candidate_profile_id not in profile_by_id:
        raise ValueError("confirmation profile_id is absent from source profiles")
    if confirmation.get("score") != EXACT_POSITION_SCORE:
        raise ValueError("confirmation score is not exact-position CRC/byte identity")
    if _boolean(
        confirmation.get("no_early_stopping"), label="confirmation no_early_stopping"
    ) is not True:
        raise ValueError("confirmation must prohibit early stopping")
    if _boolean(
        confirmation.get("all_scheduled_slots_remain_in_denominator"),
        label="confirmation all_scheduled_slots_remain_in_denominator",
    ) is not True:
        raise ValueError("confirmation must retain all scheduled slots")

    gates = _mapping(confirmation.get("predeclared_gates"), label="confirmation gates")
    if _boolean(
        gates.get("current_aggregate_verified_blocks_strictly_above_legacy"),
        label="confirmation aggregate gate",
    ) is not True:
        raise ValueError("confirmation must require strict aggregate improvement")
    maximum_regressions = _integer(
        gates.get("maximum_run_regressions"),
        label="confirmation maximum_run_regressions",
    )
    if maximum_regressions > 1:
        raise ValueError("comparison supports at most one predeclared run regression")

    candidate_runs = [
        run for run in run_by_id.values() if run.get("profile_id") == candidate_profile_id
    ]
    declared_runs = _integer(confirmation.get("runs"), label="confirmation runs", minimum=1)
    if len(candidate_runs) != declared_runs:
        raise ValueError("candidate run count differs from the confirmation contract")
    pairs = _integer(plan.get("pairs"), label="source plan pairs", minimum=1)
    if declared_runs != pairs:
        raise ValueError("confirmation run count differs from source pair count")
    pair_indexes = [_integer(run.get("pair_index"), label="candidate pair_index") for run in candidate_runs]
    if sorted(pair_indexes) != list(range(pairs)):
        raise ValueError("candidate profile must occur exactly once in every source pair")

    legacy_sha256 = _sha256(
        confirmation.get("legacy_decoder_sha256"), label="legacy decoder SHA-256"
    )
    current_receiver_contract = _mapping(
        confirmation.get("current_receiver_contract_v1"),
        label="confirmation current_receiver_contract_v1",
    )
    execution_provenance = _mapping(
        manifest.get("execution_provenance"), label="source execution_provenance"
    )
    source_hashes = _mapping(
        execution_provenance.get("source_hashes"), label="source execution source_hashes"
    )
    campaign_decoder = _mapping(
        source_hashes.get("c_bulk_dylib"), label="source campaign decoder hash record"
    )
    current_sha256 = _sha256(
        campaign_decoder.get("sha256"), label="source campaign decoder SHA-256"
    )
    current_byte_count = _integer(
        campaign_decoder.get("byte_count"),
        label="source campaign decoder byte_count",
        minimum=1,
    )
    if current_sha256 == legacy_sha256:
        raise ValueError("current and legacy decoder SHA-256 roles are not distinct")

    decoder_binding = _mapping(
        execution_provenance.get("decoder_binding"), label="source decoder_binding"
    )
    if _boolean(
        decoder_binding.get("matches_plan_build_intent"),
        label="decoder_binding matches_plan_build_intent",
    ) is not True:
        raise ValueError("source decoder binding does not assert the plan build intent")
    if _boolean(
        decoder_binding.get("built_from_retained_snapshots"),
        label="decoder_binding built_from_retained_snapshots",
    ) is not True:
        raise ValueError("source decoder was not built from retained input snapshots")
    for field in (
        "expected_sha256",
        "source_sha256_from_single_read",
        "before_import_sha256",
        "after_load_sha256",
        "before_playback_sha256",
        "after_campaign_sha256",
    ):
        observed = _sha256(decoder_binding.get(field), label=f"decoder_binding {field}")
        if observed != current_sha256:
            raise ValueError(f"decoder_binding {field} differs from the campaign decoder")
    for field in ("content_addressed", "unchanged_through_campaign"):
        if _boolean(decoder_binding.get(field), label=f"decoder_binding {field}") is not True:
            raise ValueError(f"decoder_binding {field} must be true")

    build_attestation = _mapping(
        decoder_binding.get("build_attestation"), label="source decoder build_attestation"
    )
    build_identity_fields = (
        "recipe_id",
        "canonical_input_manifest",
        "input_manifest_sha256",
    )
    for field in build_identity_fields:
        if not _same_json(build_attestation.get(field), build_intent.get(field)):
            raise ValueError(
                f"source build attestation {field} differs from decoder_build_intent"
            )
    recipe_id = _string(build_intent.get("recipe_id"), label="decoder build recipe_id")
    canonical_inputs = _list(
        build_intent.get("canonical_input_manifest"),
        label="decoder canonical input manifest",
    )
    if not canonical_inputs:
        raise ValueError("decoder canonical input manifest must not be empty")
    validated_canonical_inputs = []
    for position, record_value in enumerate(canonical_inputs):
        record = _mapping(record_value, label=f"decoder canonical input {position}")
        validated_canonical_inputs.append(
            {
                "relative_path": _string(
                    record.get("relative_path"),
                    label=f"decoder canonical input {position} relative_path",
                ),
                "byte_count": _integer(
                    record.get("byte_count"),
                    label=f"decoder canonical input {position} byte_count",
                    minimum=1,
                ),
                "sha256": _sha256(
                    record.get("sha256"),
                    label=f"decoder canonical input {position} SHA-256",
                ),
            }
        )
    if not _same_json(canonical_inputs, validated_canonical_inputs):
        raise ValueError("decoder canonical input manifest has unexpected fields or types")
    input_manifest_sha256 = sha256_bytes(canonical_json_bytes(validated_canonical_inputs))
    if _sha256(
        build_intent.get("input_manifest_sha256"),
        label="decoder build-intent input_manifest_sha256",
    ) != input_manifest_sha256:
        raise ValueError("decoder build-intent input manifest hash is not canonical")
    if _sha256(
        build_attestation.get("input_manifest_sha256"),
        label="decoder build-attestation input_manifest_sha256",
    ) != input_manifest_sha256:
        raise ValueError("decoder build-attestation input manifest hash is not canonical")

    retained_inputs = _list(
        build_attestation.get("input_manifest"), label="decoder retained input manifest"
    )
    stripped_retained_inputs = []
    for position, record_value in enumerate(retained_inputs):
        record = _mapping(record_value, label=f"decoder retained input {position}")
        _string(
            record.get("snapshot_path"),
            label=f"decoder retained input {position} snapshot_path",
        )
        stripped_retained_inputs.append(
            {
                "relative_path": record.get("relative_path"),
                "byte_count": record.get("byte_count"),
                "sha256": record.get("sha256"),
            }
        )
    if not _same_json(stripped_retained_inputs, validated_canonical_inputs):
        raise ValueError("decoder retained inputs differ from the canonical input manifest")
    if _sha256(
        build_attestation.get("output_sha256"),
        label="decoder build-attestation output SHA-256",
    ) != current_sha256:
        raise ValueError("decoder build output differs from the current campaign decoder")
    if _integer(
        build_attestation.get("output_byte_count"),
        label="decoder build-attestation output byte_count",
        minimum=1,
    ) != current_byte_count:
        raise ValueError("decoder build output byte count differs from campaign provenance")
    compiler = _mapping(
        build_attestation.get("compiler"), label="decoder build compiler"
    )
    normalized_argv = _list(
        build_attestation.get("normalized_argv"), label="decoder normalized build argv"
    )
    if not normalized_argv or any(not isinstance(value, str) for value in normalized_argv):
        raise ValueError("decoder normalized build argv must contain strings")
    canonical_attestation = {
        "recipe_id": recipe_id,
        "canonical_input_manifest": validated_canonical_inputs,
        "compiler": compiler,
        "normalized_argv": normalized_argv,
        "output_byte_count": current_byte_count,
        "output_sha256": current_sha256,
    }
    if _sha256(
        build_attestation.get("attestation_sha256"),
        label="decoder build attestation_sha256",
    ) != sha256_bytes(canonical_json_bytes(canonical_attestation)):
        raise ValueError("decoder build attestation hash is not canonical")

    if _boolean(
        execution_provenance.get("matches_plan_implementation_intent"),
        label="execution matches_plan_implementation_intent",
    ) is not True:
        raise ValueError("source execution does not assert the plan implementation intent")
    required_implementation_labels = {
        "campaign_runner",
        "goodput_referee",
        "c_binding",
        "hil_harness",
        "audio_transaction_runner",
    }
    if set(implementation_intent) != required_implementation_labels:
        raise ValueError("campaign implementation intent has unexpected component labels")
    for component in sorted(required_implementation_labels):
        expected_record = _mapping(
            implementation_intent.get(component),
            label=f"implementation intent {component}",
        )
        validated_record = {
            "path": _string(
                expected_record.get("path"), label=f"implementation intent {component} path"
            ),
            "byte_count": _integer(
                expected_record.get("byte_count"),
                label=f"implementation intent {component} byte_count",
                minimum=1,
            ),
            "sha256": _sha256(
                expected_record.get("sha256"),
                label=f"implementation intent {component} SHA-256",
            ),
        }
        if not _same_json(expected_record, validated_record):
            raise ValueError(
                f"campaign implementation intent {component} has unexpected fields"
            )
        if not _same_json(source_hashes.get(component), validated_record):
            raise ValueError(
                f"execution source hash {component} differs from implementation intent"
            )

    preflight = _mapping(
        execution_provenance.get("decoder_preflight"), label="source decoder_preflight"
    )
    loaded_receiver_contract = _mapping(
        preflight.get("loaded_binary_receiver_contract"),
        label="source loaded receiver contract",
    )
    if not _same_json(loaded_receiver_contract, current_receiver_contract):
        raise ValueError("source loaded receiver contract differs from the predeclared contract")

    return {
        "manifest_file_sha256": manifest_file_sha256,
        "execution_id": execution_id,
        "plan": plan,
        "plan_sha256": plan_sha256,
        "profile_by_id": profile_by_id,
        "run_by_id": run_by_id,
        "result_by_id": result_by_id,
        "candidate_profile_id": candidate_profile_id,
        "candidate_runs": candidate_runs,
        "scheduled_slots": scheduled_slots,
        "primary_receiver": primary_receiver,
        "confirmation": confirmation,
        "maximum_regressions": maximum_regressions,
        "legacy_sha256": legacy_sha256,
        "current_sha256": current_sha256,
        "decoder_build_intent_sha256": sha256_bytes(canonical_json_bytes(build_intent)),
        "campaign_implementation_intent_sha256": sha256_bytes(
            canonical_json_bytes(implementation_intent)
        ),
    }


def _verify_report_binding(
    report: dict[str, Any],
    *,
    label: str,
    role: str,
    context: Mapping[str, Any],
) -> dict[str, Any]:
    report_sha256 = _verify_reanalysis_self_hash(report, label=label)
    source = _mapping(report.get("source_manifest"), label=f"{label} source_manifest")
    expected_source_fields = {
        "sha256": context["manifest_file_sha256"],
        "plan_sha256": context["plan_sha256"],
        "execution_id": context["execution_id"],
    }
    for field, expected in expected_source_fields.items():
        if source.get(field) != expected:
            raise ValueError(f"{label} source manifest {field} differs from the supplied campaign")

    decoder = _mapping(report.get("decoder"), label=f"{label} decoder")
    before = _sha256(
        decoder.get("sha256_before_decode"), label=f"{label} decoder before SHA-256"
    )
    after = _sha256(
        decoder.get("sha256_after_decode"), label=f"{label} decoder after SHA-256"
    )
    if before != after:
        raise ValueError(f"{label} decoder changed during reanalysis")
    expected_decoder = context[f"{role}_sha256"]
    if before != expected_decoder:
        raise ValueError(
            f"{label} decoder does not have the predeclared {role} SHA-256 role"
        )
    if _boolean(
        decoder.get("ordered_block_validity_api"),
        label=f"{label} ordered block-validity API",
    ) is not True:
        raise ValueError(f"{label} decoder lacks ordered block-validity support")
    if context["primary_receiver"] == PILOT_SELECT_POLICY and _boolean(
        decoder.get("automatic_diversity_policy_v1_api"),
        label=f"{label} automatic-diversity API",
    ) is not True:
        raise ValueError(f"{label} decoder lacks the predeclared primary receiver API")

    source_hashes = _mapping(report.get("source_hashes"), label=f"{label} source_hashes")
    decoder_hash_record = _mapping(
        source_hashes.get("decoder_dylib"), label=f"{label} decoder hash record"
    )
    if decoder_hash_record.get("role") != "authoritative_executed_decoder":
        raise ValueError(f"{label} decoder hash record has the wrong provenance role")
    if _sha256(
        decoder_hash_record.get("sha256"), label=f"{label} decoder source hash"
    ) != before:
        raise ValueError(f"{label} decoder source hash differs from decoder identity")

    recorded_campaign_decoder = _mapping(
        source.get("recorded_campaign_decoder"),
        label=f"{label} recorded campaign decoder",
    )
    if _sha256(
        recorded_campaign_decoder.get("recorded_sha256"),
        label=f"{label} recorded campaign decoder SHA-256",
    ) != context["current_sha256"]:
        raise ValueError(f"{label} does not bind the current campaign decoder")

    report_runs = _list(report.get("runs"), label=f"{label} runs")
    run_by_id: dict[str, Mapping[str, Any]] = {}
    for index, run_value in enumerate(report_runs):
        run = _mapping(run_value, label=f"{label} run {index}")
        run_id = _string(run.get("run_id"), label=f"{label} run {index} ID")
        if run_id in run_by_id:
            raise ValueError(f"{label} run ID is duplicated: {run_id}")
        run_by_id[run_id] = run
    if set(run_by_id) != set(context["run_by_id"]):
        raise ValueError(f"{label} run identities differ from the source plan")
    for run_id, source_run in context["run_by_id"].items():
        report_run = run_by_id[run_id]
        for field in ("profile_id", "pair_index", "within_pair_order"):
            if report_run.get(field) != source_run.get(field):
                raise ValueError(f"{label} {run_id} differs from the source plan on {field}")

    classification = _mapping(report.get("classification"), label=f"{label} classification")
    dataset_role = _string(
        classification.get("dataset_role"), label=f"{label} dataset role"
    )
    return {
        "report_sha256": report_sha256,
        "decoder_sha256": before,
        "run_by_id": run_by_id,
        "dataset_role": dataset_role,
    }


def _boolean_mask(value: Any, *, length: int, label: str) -> list[bool]:
    mask = _list(value, label=label)
    if len(mask) != length or any(not isinstance(item, bool) for item in mask):
        raise ValueError(f"{label} must contain exactly {length} booleans")
    return mask


def _score_candidate_run(
    report_run: Mapping[str, Any],
    *,
    source_result: Mapping[str, Any],
    source_profile: Mapping[str, Any],
    primary_receiver: str,
    scheduled_slots: int,
    label: str,
) -> dict[str, Any]:
    source_result_artifact = _mapping(
        source_result.get("result_artifact"), label=f"{label} source result artifact"
    )
    report_source_result = _mapping(
        report_run.get("source_result"), label=f"{label} report source_result"
    )
    if _sha256(
        report_source_result.get("sha256"), label=f"{label} report source-result SHA-256"
    ) != _sha256(
        source_result_artifact.get("sha256"), label=f"{label} source result SHA-256"
    ):
        raise ValueError(f"{label} result artifact differs from the source campaign")

    source_artifacts = _mapping(
        source_result.get("artifacts"), label=f"{label} source artifacts"
    )
    source_capture = _mapping(
        source_artifacts.get("capture"), label=f"{label} source capture"
    )
    report_capture = _mapping(report_run.get("capture"), label=f"{label} report capture")
    for field in ("sha256", "frames", "channels"):
        if report_capture.get(field) != source_capture.get(field):
            raise ValueError(f"{label} capture {field} differs from the source campaign")
    source_pcm16_values = _integer(
        source_capture.get("pcm16_values"), label=f"{label} source capture PCM16 values"
    )
    if _integer(
        report_capture.get("byte_count"), label=f"{label} report capture byte_count"
    ) != source_pcm16_values * 2:
        raise ValueError(f"{label} capture byte count differs from the source campaign")

    source_expected_values = _list(
        source_artifacts.get("expected_payloads"), label=f"{label} source expected payloads"
    )
    report_expected_values = _list(
        report_run.get("expected_payloads"), label=f"{label} report expected payloads"
    )

    def payload_records_by_frame(
        values: list[Any], *, records_label: str
    ) -> dict[int, Mapping[str, Any]]:
        records: dict[int, Mapping[str, Any]] = {}
        for position, value in enumerate(values):
            record = _mapping(value, label=f"{records_label} {position}")
            frame_index = _integer(
                record.get("frame_index"), label=f"{records_label} frame_index"
            )
            if frame_index in records:
                raise ValueError(f"{records_label} contains duplicate frame indexes")
            records[frame_index] = record
        return records

    source_expected = payload_records_by_frame(
        source_expected_values, records_label=f"{label} source expected payload"
    )
    report_expected = payload_records_by_frame(
        report_expected_values, records_label=f"{label} report expected payload"
    )
    if set(source_expected) != set(range(scheduled_slots)) or set(report_expected) != set(
        range(scheduled_slots)
    ):
        raise ValueError(f"{label} expected payloads do not cover every scheduled slot")
    for frame_index in range(scheduled_slots):
        for field in ("sha256", "byte_count"):
            if report_expected[frame_index].get(field) != source_expected[frame_index].get(field):
                raise ValueError(
                    f"{label} expected payload frame {frame_index} {field} differs from source"
                )

    expected_profile = {
        field: source_profile.get(field)
        for field in ("config", "geometry", "amplitude", "clip_sigma")
    }
    report_profile = _mapping(report_run.get("profile"), label=f"{label} profile")
    if not _same_json(report_profile, expected_profile):
        raise ValueError(f"{label} profile differs from the source candidate profile")
    geometry = _mapping(report_profile.get("geometry"), label=f"{label} geometry")
    blocks_per_slot = _integer(
        geometry.get("crc_blocks"), label=f"{label} blocks per slot", minimum=1
    )
    expected_denominator = scheduled_slots * blocks_per_slot

    if report_run.get("primary_receiver") != primary_receiver:
        raise ValueError(f"{label} primary receiver differs from the source contract")
    summaries = _mapping(report_run.get("policy_summaries"), label=f"{label} summaries")
    summary = _mapping(summaries.get(primary_receiver), label=f"{label} primary summary")
    primary_result = _mapping(report_run.get("primary_result"), label=f"{label} primary_result")
    if not _same_json(summary, primary_result):
        raise ValueError(f"{label} primary_result differs from its policy summary")
    if summary.get("verification_rule") != EXACT_POSITION_SCORE:
        raise ValueError(f"{label} does not use the predeclared exact-position score")
    if _integer(summary.get("scheduled_slots"), label=f"{label} scheduled slots") != scheduled_slots:
        raise ValueError(f"{label} scheduled-slot denominator differs from the source plan")
    if _integer(
        summary.get("total_scheduled_blocks"), label=f"{label} block denominator"
    ) != expected_denominator:
        raise ValueError(f"{label} scheduled-block denominator differs from its geometry")

    timing_reuse = _mapping(report_run.get("timing_reuse"), label=f"{label} timing_reuse")
    if _integer(
        timing_reuse.get("scheduled_slots"), label=f"{label} timing scheduled slots"
    ) != scheduled_slots:
        raise ValueError(f"{label} timing denominator differs from the source schedule")
    if timing_reuse.get("recorded_detections_only") is not True:
        raise ValueError(f"{label} did not reuse only recorded timing detections")
    source_timing = _mapping(source_result.get("timing"), label=f"{label} source timing")
    if _integer(
        source_timing.get("scheduled_slots"), label=f"{label} source timing scheduled slots"
    ) != scheduled_slots:
        raise ValueError(f"{label} source timing denominator differs from the plan")
    source_detection_values = _list(
        source_timing.get("detections"), label=f"{label} source timing detections"
    )
    source_detections: dict[int, Mapping[str, Any]] = {}
    for position, detection_value in enumerate(source_detection_values):
        detection = _mapping(detection_value, label=f"{label} source detection {position}")
        frame_index = _integer(
            detection.get("frame_index"), label=f"{label} source detection frame_index"
        )
        if frame_index not in range(scheduled_slots) or frame_index in source_detections:
            raise ValueError(f"{label} source timing detections have invalid frame indexes")
        source_detections[frame_index] = detection
    if _integer(
        source_timing.get("detected_slots"), label=f"{label} source detected slots"
    ) != len(source_detections):
        raise ValueError(f"{label} source detected-slot count differs from its detections")
    if _integer(
        timing_reuse.get("recorded_detected_slots"),
        label=f"{label} report recorded detected slots",
    ) != len(source_detections):
        raise ValueError(f"{label} report detected-slot count differs from source timing")

    receiver_frames = _mapping(
        report_run.get("receiver_frames"), label=f"{label} receiver_frames"
    )
    frames = _list(receiver_frames.get(primary_receiver), label=f"{label} primary frames")
    if len(frames) != scheduled_slots:
        raise ValueError(f"{label} primary frames do not cover every scheduled slot")
    frame_by_index: dict[int, Mapping[str, Any]] = {}
    for position, frame_value in enumerate(frames):
        frame = _mapping(frame_value, label=f"{label} frame {position}")
        frame_index = _integer(frame.get("frame_index"), label=f"{label} frame_index")
        if frame_index in frame_by_index:
            raise ValueError(f"{label} contains duplicate frame indexes")
        frame_by_index[frame_index] = frame
    if set(frame_by_index) != set(range(scheduled_slots)):
        raise ValueError(f"{label} frames do not cover the scheduled indexes")

    frame_scores = []
    total_verified = 0
    total_crc = 0
    total_identity = 0
    for frame_index in range(scheduled_slots):
        frame = frame_by_index[frame_index]
        if not _same_json(frame.get("recorded_detection"), source_detections.get(frame_index)):
            raise ValueError(
                f"{label} frame {frame_index} recorded detection differs from source timing"
            )
        if _integer(frame.get("blocks_total"), label=f"{label} frame blocks_total") != blocks_per_slot:
            raise ValueError(f"{label} frame denominator differs from profile geometry")
        crc_mask = _boolean_mask(
            frame.get("crc_valid_mask"),
            length=blocks_per_slot,
            label=f"{label} frame {frame_index} CRC mask",
        )
        identity_mask = _boolean_mask(
            frame.get("byte_identity_mask"),
            length=blocks_per_slot,
            label=f"{label} frame {frame_index} byte-identity mask",
        )
        intersection_mask = _boolean_mask(
            frame.get("verified_intersection_mask"),
            length=blocks_per_slot,
            label=f"{label} frame {frame_index} intersection mask",
        )
        expected_intersection = [
            crc_valid and byte_identical
            for crc_valid, byte_identical in zip(crc_mask, identity_mask, strict=True)
        ]
        if intersection_mask != expected_intersection:
            raise ValueError(f"{label} frame {frame_index} intersection mask is not exact-position")
        crc_count = sum(crc_mask)
        identity_count = sum(identity_mask)
        verified_count = sum(intersection_mask)
        recorded_counts = {
            "crc_valid_blocks": crc_count,
            "byte_identical_blocks": identity_count,
            "verified_blocks": verified_count,
        }
        for field, expected in recorded_counts.items():
            if _integer(frame.get(field), label=f"{label} frame {frame_index} {field}") != expected:
                raise ValueError(f"{label} frame {frame_index} {field} differs from its mask")
        total_crc += crc_count
        total_identity += identity_count
        total_verified += verified_count
        frame_scores.append(
            {
                "frame_index": frame_index,
                "verified_blocks": verified_count,
                "total_scheduled_blocks": blocks_per_slot,
            }
        )

    expected_summary_counts = {
        "crc_valid_blocks": total_crc,
        "byte_identical_blocks": total_identity,
        "verified_blocks": total_verified,
    }
    for field, expected in expected_summary_counts.items():
        if _integer(summary.get(field), label=f"{label} summary {field}") != expected:
            raise ValueError(f"{label} summary {field} differs from its ordered frame masks")
    metrics = _mapping(summary.get("metrics"), label=f"{label} metrics")
    if _integer(metrics.get("verified_blocks"), label=f"{label} metric verified_blocks") != total_verified:
        raise ValueError(f"{label} metric verified-block count differs from exact-position score")
    if _integer(metrics.get("total_blocks"), label=f"{label} metric total_blocks") != expected_denominator:
        raise ValueError(f"{label} metric denominator differs from the schedule")
    return {
        "verified_blocks": total_verified,
        "total_scheduled_blocks": expected_denominator,
        "scheduled_slots": scheduled_slots,
        "blocks_per_slot": blocks_per_slot,
        "frame_scores": frame_scores,
    }


def _verify_same_replay_inputs(
    legacy_run: Mapping[str, Any],
    current_run: Mapping[str, Any],
    *,
    run_id: str,
    primary_receiver: str,
    scheduled_slots: int,
) -> None:
    for field in ("source_result", "capture", "expected_payloads", "profile", "timing_reuse"):
        if not _same_json(legacy_run.get(field), current_run.get(field)):
            raise ValueError(f"{run_id} legacy/current reports differ on retained {field}")
    legacy_frames = _mapping(
        legacy_run.get("receiver_frames"), label=f"legacy {run_id} receiver_frames"
    ).get(primary_receiver)
    current_frames = _mapping(
        current_run.get("receiver_frames"), label=f"current {run_id} receiver_frames"
    ).get(primary_receiver)
    legacy_list = _list(legacy_frames, label=f"legacy {run_id} primary frames")
    current_list = _list(current_frames, label=f"current {run_id} primary frames")
    if len(legacy_list) != scheduled_slots or len(current_list) != scheduled_slots:
        raise ValueError(f"{run_id} replay frame count differs from the source schedule")
    legacy_by_index = {
        _integer(_mapping(frame, label="legacy frame").get("frame_index"), label="legacy frame_index"):
        _mapping(frame, label="legacy frame")
        for frame in legacy_list
    }
    current_by_index = {
        _integer(_mapping(frame, label="current frame").get("frame_index"), label="current frame_index"):
        _mapping(frame, label="current frame")
        for frame in current_list
    }
    if set(legacy_by_index) != set(current_by_index):
        raise ValueError(f"{run_id} replay frame identities differ")
    for frame_index in sorted(legacy_by_index):
        for field in ("recorded_detection", "decode_window"):
            if not _same_json(
                legacy_by_index[frame_index].get(field),
                current_by_index[frame_index].get(field),
            ):
                raise ValueError(
                    f"{run_id} frame {frame_index} legacy/current reports differ on {field}"
                )


def _outcome(delta: int) -> str:
    if delta > 0:
        return "improvement"
    if delta < 0:
        return "regression"
    return "tie"


def _finalize_content_hash(report: dict[str, Any]) -> dict[str, Any]:
    integrity = _mapping(report.get("integrity"), label="output integrity")
    if "content_sha256" in integrity:
        raise ValueError("content hash was already finalized")
    content_sha256 = sha256_bytes(canonical_json_bytes(report))
    report["integrity"] = {**integrity, "content_sha256": content_sha256}
    return report


def build_comparison(
    source_manifest_path: Path,
    legacy_report_path: Path,
    current_report_path: Path,
) -> dict[str, Any]:
    manifest, manifest_path, manifest_file_sha256 = _load_json_snapshot(
        source_manifest_path, label="source manifest"
    )
    legacy_report, legacy_path, legacy_file_sha256 = _load_json_snapshot(
        legacy_report_path, label="legacy report"
    )
    current_report, current_path, current_file_sha256 = _load_json_snapshot(
        current_report_path, label="current report"
    )
    if legacy_path == current_path:
        raise ValueError("legacy and current reports must be distinct files")

    context = _verify_source_manifest(
        manifest, manifest_file_sha256=manifest_file_sha256
    )
    legacy = _verify_report_binding(
        legacy_report,
        label="legacy report",
        role="legacy",
        context=context,
    )
    current = _verify_report_binding(
        current_report,
        label="current report",
        role="current",
        context=context,
    )
    if legacy["dataset_role"] != current["dataset_role"]:
        raise ValueError("legacy/current reanalysis dataset roles differ")

    candidate_profile_id = context["candidate_profile_id"]
    source_profile = context["profile_by_id"][candidate_profile_id]
    candidate_runs = sorted(
        context["candidate_runs"],
        key=lambda run: (int(run["pair_index"]), int(run["within_pair_order"]), str(run["run_id"])),
    )
    run_deltas = []
    outcome_counts = {"improvements": 0, "ties": 0, "regressions": 0}
    aggregate_legacy = 0
    aggregate_current = 0
    aggregate_denominator = 0
    aggregate_slots = 0
    for source_run in candidate_runs:
        run_id = str(source_run["run_id"])
        legacy_run = legacy["run_by_id"][run_id]
        current_run = current["run_by_id"][run_id]
        _verify_same_replay_inputs(
            legacy_run,
            current_run,
            run_id=run_id,
            primary_receiver=context["primary_receiver"],
            scheduled_slots=context["scheduled_slots"],
        )
        legacy_score = _score_candidate_run(
            legacy_run,
            source_result=context["result_by_id"][run_id],
            source_profile=source_profile,
            primary_receiver=context["primary_receiver"],
            scheduled_slots=context["scheduled_slots"],
            label=f"legacy {run_id}",
        )
        current_score = _score_candidate_run(
            current_run,
            source_result=context["result_by_id"][run_id],
            source_profile=source_profile,
            primary_receiver=context["primary_receiver"],
            scheduled_slots=context["scheduled_slots"],
            label=f"current {run_id}",
        )
        if legacy_score["total_scheduled_blocks"] != current_score["total_scheduled_blocks"]:
            raise ValueError(f"{run_id} legacy/current scheduled-block denominators differ")
        frame_deltas = []
        for legacy_frame, current_frame in zip(
            legacy_score["frame_scores"], current_score["frame_scores"], strict=True
        ):
            if legacy_frame["frame_index"] != current_frame["frame_index"]:
                raise ValueError(f"{run_id} legacy/current frame ordering differs")
            frame_deltas.append(
                {
                    "frame_index": legacy_frame["frame_index"],
                    "legacy_verified_blocks": legacy_frame["verified_blocks"],
                    "current_verified_blocks": current_frame["verified_blocks"],
                    "delta_verified_blocks": (
                        current_frame["verified_blocks"] - legacy_frame["verified_blocks"]
                    ),
                    "total_scheduled_blocks": legacy_frame["total_scheduled_blocks"],
                }
            )
        delta = current_score["verified_blocks"] - legacy_score["verified_blocks"]
        outcome = _outcome(delta)
        outcome_counts[f"{outcome}s"] += 1
        denominator = legacy_score["total_scheduled_blocks"]
        run_deltas.append(
            {
                "run_id": run_id,
                "pair_index": source_run["pair_index"],
                "within_pair_order": source_run["within_pair_order"],
                "profile_id": candidate_profile_id,
                "primary_receiver": context["primary_receiver"],
                "scheduled_slots": legacy_score["scheduled_slots"],
                "total_scheduled_blocks": denominator,
                "legacy_verified_blocks": legacy_score["verified_blocks"],
                "current_verified_blocks": current_score["verified_blocks"],
                "delta_verified_blocks": delta,
                "outcome": outcome,
                "frame_deltas": frame_deltas,
            }
        )
        aggregate_legacy += legacy_score["verified_blocks"]
        aggregate_current += current_score["verified_blocks"]
        aggregate_denominator += denominator
        aggregate_slots += legacy_score["scheduled_slots"]

    aggregate_delta = aggregate_current - aggregate_legacy
    strict_improvement_passed = aggregate_current > aggregate_legacy
    regression_gate_passed = (
        outcome_counts["regressions"] <= context["maximum_regressions"]
        and outcome_counts["regressions"] <= 1
    )
    all_gates_passed = strict_improvement_passed and regression_gate_passed
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "integrity": {
            "canonical_json": (
                "UTF-8, lexicographically sorted object keys, compact separators, "
                "finite JSON numbers"
            ),
            "content_sha256_scope": (
                "canonical entire document before integrity.content_sha256 is inserted"
            ),
            "schema_sha256_scope": "canonical JSON encoding of the schema identifier",
            "schema_sha256": sha256_bytes(canonical_json_bytes(SCHEMA)),
        },
        "source_campaign": {
            "path": str(manifest_path),
            "file_sha256": manifest_file_sha256,
            "plan_sha256": context["plan_sha256"],
            "execution_id": context["execution_id"],
            "provenance_gate_verification": {
                "decoder_binding_matches_plan_build_intent_asserted": True,
                "decoder_build_identity_independently_recomputed": True,
                "decoder_build_intent_sha256": context["decoder_build_intent_sha256"],
                "execution_matches_plan_implementation_intent_asserted": True,
                "campaign_implementation_intent_independently_recomputed": True,
                "campaign_implementation_intent_sha256": (
                    context["campaign_implementation_intent_sha256"]
                ),
            },
        },
        "inputs": {
            "legacy_report": {
                "role": "predeclared_legacy_decoder",
                "path": str(legacy_path),
                "file_sha256": legacy_file_sha256,
                "report_sha256": legacy["report_sha256"],
                "decoder_sha256": legacy["decoder_sha256"],
            },
            "current_report": {
                "role": "source_campaign_current_decoder",
                "path": str(current_path),
                "file_sha256": current_file_sha256,
                "report_sha256": current["report_sha256"],
                "decoder_sha256": current["decoder_sha256"],
            },
        },
        "predeclared_contract": dict(context["confirmation"]),
        "comparison_scope": {
            "dataset_role": current["dataset_role"],
            "profile_id": candidate_profile_id,
            "primary_receiver": context["primary_receiver"],
            "score": EXACT_POSITION_SCORE,
            "candidate_runs": len(candidate_runs),
            "noncandidate_profiles_compared": False,
            "recorded_timing_capture_and_decode_margin_identical": True,
            "all_scheduled_slots_retained": True,
        },
        "run_deltas": run_deltas,
        "aggregate": {
            "runs": len(run_deltas),
            "scheduled_slots": aggregate_slots,
            "total_scheduled_blocks": aggregate_denominator,
            "legacy_verified_blocks": aggregate_legacy,
            "current_verified_blocks": aggregate_current,
            "delta_verified_blocks": aggregate_delta,
            "legacy_block_success_rate": aggregate_legacy / aggregate_denominator,
            "current_block_success_rate": aggregate_current / aggregate_denominator,
            "run_outcomes": outcome_counts,
        },
        "predeclared_gate_results": {
            "current_aggregate_verified_blocks_strictly_above_legacy": {
                "legacy_verified_blocks": aggregate_legacy,
                "current_verified_blocks": aggregate_current,
                "passed": strict_improvement_passed,
            },
            "maximum_run_regressions": {
                "maximum": context["maximum_regressions"],
                "observed": outcome_counts["regressions"],
                "passed": regression_gate_passed,
            },
            "all_passed": all_gates_passed,
        },
    }
    return _finalize_content_hash(report)


def verify_content_hash(report: Mapping[str, Any]) -> bool:
    """Verify a comparison document's schema and canonical content hash."""
    if report.get("schema") != SCHEMA:
        return False
    integrity_value = report.get("integrity")
    if not isinstance(integrity_value, Mapping):
        return False
    if integrity_value.get("schema_sha256") != sha256_bytes(canonical_json_bytes(SCHEMA)):
        return False
    recorded = integrity_value.get("content_sha256")
    if not isinstance(recorded, str) or SHA256_PATTERN.fullmatch(recorded) is None:
        return False
    unhashed = dict(report)
    unhashed["integrity"] = dict(integrity_value)
    unhashed["integrity"].pop("content_sha256", None)
    try:
        actual = sha256_bytes(canonical_json_bytes(unhashed))
    except (TypeError, ValueError):
        return False
    return actual == recorded


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_manifest", type=Path, help="completed source campaign manifest")
    parser.add_argument("legacy_report", type=Path, help="v2 report using the legacy decoder")
    parser.add_argument("current_report", type=Path, help="v2 report using the current decoder")
    parser.add_argument("--out", type=Path, required=True, help="new comparison JSON; never overwritten")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    output_path = args.out.expanduser().resolve()
    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite comparison artifact: {output_path}")
    report = build_comparison(
        args.source_manifest,
        args.legacy_report,
        args.current_report,
    )
    payload = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8") as handle:
        handle.write(payload)
    return 0 if report["predeclared_gate_results"]["all_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
