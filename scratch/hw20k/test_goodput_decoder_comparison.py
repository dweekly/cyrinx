#!/usr/bin/env python3
"""Deterministic integrity tests for the paired decoder comparator."""

from __future__ import annotations

import ast
import json
from pathlib import Path
import tempfile
import unittest

import goodput_decoder_comparison as comparison


LEGACY_SHA256 = "1" * 64
CURRENT_SHA256 = "2" * 64
PROFILE_ID = "candidate-cp96-p16-b6-r23"
PRIMARY_RECEIVER = comparison.PILOT_SELECT_POLICY
BLOCKS_PER_SLOT = 3
SCHEDULED_SLOTS = 2


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _profile(profile_id: str) -> dict[str, object]:
    return {
        "profile_id": profile_id,
        "config": {
            "sample_rate_hz": 48_000,
            "bits_per_bin": 6,
            "code_rate": "2/3",
            "data_symbols": 64,
        },
        "geometry": {
            "crc_blocks": BLOCKS_PER_SLOT,
            "payload_bytes": BLOCKS_PER_SLOT * 256,
            "frame_samples": 123_456,
        },
        "amplitude": 0.18,
        "clip_sigma": 3.3,
        "error_free_ceiling": {},
        "warning": "synthetic comparator fixture",
    }


def _report_profile(profile: dict[str, object]) -> dict[str, object]:
    return {
        field: profile[field]
        for field in ("config", "geometry", "amplitude", "clip_sigma")
    }


def _frame(frame_index: int, verified_blocks: int) -> dict[str, object]:
    mask = [index < verified_blocks for index in range(BLOCKS_PER_SLOT)]
    return {
        "frame_index": frame_index,
        "recorded_detection": {
            "frame_index": frame_index,
            "detected_sample": 10_000 + frame_index * 200_000,
        },
        "decode_window": {
            "start_sample": 8_800 + frame_index * 200_000,
            "stop_sample_exclusive": 140_000 + frame_index * 200_000,
            "sample_count": 131_200,
            "margin_samples": 1_200,
        },
        "decode_status": "decoded",
        "crc_valid_mask": mask,
        "byte_identity_mask": mask,
        "verified_intersection_mask": mask,
        "crc_valid_blocks": verified_blocks,
        "byte_identical_blocks": verified_blocks,
        "verified_blocks": verified_blocks,
        "blocks_total": BLOCKS_PER_SLOT,
        "evm": 0.1,
        "automatic_diversity": {},
        "decoded_payload_sha256": "3" * 64,
        "decoded_payload_byte_count": BLOCKS_PER_SLOT * 256,
    }


def _summary(frame_counts: list[int]) -> dict[str, object]:
    verified = sum(frame_counts)
    denominator = SCHEDULED_SLOTS * BLOCKS_PER_SLOT
    return {
        "verification_rule": comparison.EXACT_POSITION_SCORE,
        "verified_blocks": verified,
        "crc_valid_blocks": verified,
        "byte_identical_blocks": verified,
        "total_scheduled_blocks": denominator,
        "decoded_slots": SCHEDULED_SLOTS,
        "scheduled_slots": SCHEDULED_SLOTS,
        "mean_evm_diagnostic": 0.1,
        "automatic_diversity_selected_receiver_counts": {},
        "automatic_diversity_selection_reason_counts": {},
        "automatic_diversity_slot_accounting": {},
        "metrics": {
            "verified_blocks": verified,
            "total_blocks": denominator,
            "scheduled_goodput_bps": float(verified * 1_000),
        },
    }


def _candidate_report_run(
    source_run: dict[str, object],
    profile: dict[str, object],
    frame_counts: list[int],
) -> dict[str, object]:
    frames = [_frame(index, count) for index, count in enumerate(frame_counts)]
    summary = _summary(frame_counts)
    run_id = str(source_run["run_id"])
    return {
        "run_id": run_id,
        "profile_id": source_run["profile_id"],
        "pair_index": source_run["pair_index"],
        "within_pair_order": source_run["within_pair_order"],
        "source_result": {"path": f"/retained/{run_id}/result.json", "sha256": "4" * 64},
        "capture": {
            "path": f"/retained/{run_id}/capture.pcm",
            "sha256": "5" * 64,
            "byte_count": 800_000,
            "frames": 200_000,
            "channels": 2,
        },
        "expected_payloads": [
            {
                "frame_index": frame_index,
                "path": f"/retained/{run_id}/expected-{frame_index}.bin",
                "sha256": f"{6 + frame_index}" * 64,
                "byte_count": BLOCKS_PER_SLOT * 256,
            }
            for frame_index in range(SCHEDULED_SLOTS)
        ],
        "profile": _report_profile(profile),
        "timing_reuse": {
            "source_policy": "recorded fixture detections",
            "recorded_acquisition_anchor": {"sample": 10_000},
            "recorded_detections_only": True,
            "global_reacquisition_performed": False,
            "payload_or_crc_used_for_slot_selection": False,
            "decode_margin_ms": 25.0,
            "decode_margin_samples": 1_200,
            "recorded_detected_slots": SCHEDULED_SLOTS,
            "scheduled_slots": SCHEDULED_SLOTS,
        },
        "receiver_frames": {PRIMARY_RECEIVER: frames},
        "policy_summaries": {PRIMARY_RECEIVER: summary},
        "primary_receiver": PRIMARY_RECEIVER,
        "primary_result": summary,
    }


def _finalize_report(report: dict[str, object]) -> None:
    report.pop("report_sha256", None)
    report["report_sha256"] = comparison.sha256_bytes(
        comparison.canonical_json_bytes(report)
    )


def _fixture(
    root: Path,
    *,
    current_counts: list[list[int]] | None = None,
) -> tuple[Path, Path, Path]:
    baseline_profile = _profile("baseline-cp240-p8")
    candidate_profile = _profile(PROFILE_ID)
    source_runs: list[dict[str, object]] = []
    for pair_index in range(3):
        candidate_order = pair_index % 2
        baseline_order = 1 - candidate_order
        source_runs.extend(
            [
                {
                    "run_id": f"pair-{pair_index:03d}-order-{baseline_order}-baseline",
                    "profile_id": baseline_profile["profile_id"],
                    "pair_index": pair_index,
                    "within_pair_order": baseline_order,
                },
                {
                    "run_id": f"pair-{pair_index:03d}-order-{candidate_order}-candidate",
                    "profile_id": PROFILE_ID,
                    "pair_index": pair_index,
                    "within_pair_order": candidate_order,
                },
            ]
        )
    receiver_contract = {
        "struct_size": 72,
        "abi_version": 1,
        "reliability_estimator": 1,
        "local_pilot_window": 11,
    }
    canonical_decoder_inputs = [
        {
            "relative_path": "Sources/CCyrinx/cyrinx_bulk.c",
            "byte_count": 12_345,
            "sha256": "8" * 64,
        }
    ]
    decoder_input_manifest_sha256 = comparison.sha256_bytes(
        comparison.canonical_json_bytes(canonical_decoder_inputs)
    )
    decoder_build_intent = {
        "recipe_id": "clang-c11-o2-kissfft-double-dynamiclib-v1",
        "canonical_input_manifest": canonical_decoder_inputs,
        "input_manifest_sha256": decoder_input_manifest_sha256,
    }
    implementation_intent = {
        component: {
            "path": f"/repository/scratch/hw20k/{component}.py",
            "byte_count": 10_000 + index,
            "sha256": f"{9 + index:x}" * 64,
        }
        for index, component in enumerate(
            (
                "campaign_runner",
                "goodput_referee",
                "c_binding",
                "hil_harness",
                "audio_transaction_runner",
            )
        )
    }
    plan: dict[str, object] = {
        "campaign_seed": 20260771,
        "pairs": 3,
        "profiles": [baseline_profile, candidate_profile],
        "schedule": {
            "frames": SCHEDULED_SLOTS,
            "inter_frame_gap_s": 0.25,
            "leading_pad_s": 0.0,
            "trailing_pad_s": 0.7,
            "cold_start_overhead_s": 0.0,
        },
        "runs": source_runs,
        "measurement_contract": {
            "measurement_class": "synthetic-offline-fixture",
            "primary_receiver": PRIMARY_RECEIVER,
            "decoder_build_intent": decoder_build_intent,
            "campaign_implementation_intent": implementation_intent,
            "post_capture_decoder_confirmation": {
                "comparison_id": "fresh-capture-local-llr-vs-global-only-v1",
                "profile_id": PROFILE_ID,
                "runs": 3,
                "timing_and_capture": (
                    "identical retained capture, recorded slot detections, and decode margin"
                ),
                "score": comparison.EXACT_POSITION_SCORE,
                "current_receiver_contract_v1": receiver_contract,
                "legacy_decoder_sha256": LEGACY_SHA256,
                "no_early_stopping": True,
                "all_scheduled_slots_remain_in_denominator": True,
                "predeclared_gates": {
                    "current_aggregate_verified_blocks_strictly_above_legacy": True,
                    "maximum_run_regressions": 1,
                },
                "claim_class": "fresh-OTA-capture-paired-decoder-replay",
            },
        },
    }
    decoder_binding = {
        field: CURRENT_SHA256
        for field in (
            "expected_sha256",
            "source_sha256_from_single_read",
            "before_import_sha256",
            "after_load_sha256",
            "before_playback_sha256",
            "after_campaign_sha256",
        )
    }
    decoder_binding.update({"content_addressed": True, "unchanged_through_campaign": True})
    compiler = {
        "resolved_path": "/usr/bin/clang",
        "version": "fixture clang 1.0",
        "target": "fixture-target",
    }
    normalized_argv = ["clang", "-std=c11", "-O2", "-o", "<artifact>/decoder.dylib"]
    canonical_build_attestation = {
        "recipe_id": decoder_build_intent["recipe_id"],
        "canonical_input_manifest": canonical_decoder_inputs,
        "compiler": compiler,
        "normalized_argv": normalized_argv,
        "output_byte_count": 70_000,
        "output_sha256": CURRENT_SHA256,
    }
    decoder_binding.update(
        {
            "matches_plan_build_intent": True,
            "built_from_retained_snapshots": True,
            "build_attestation": {
                **decoder_build_intent,
                "input_manifest": [
                    {
                        **canonical_decoder_inputs[0],
                        "snapshot_path": "/retained/decoder-build-inputs/cyrinx_bulk.c",
                    }
                ],
                "compiler": compiler,
                "normalized_argv": normalized_argv,
                "output_byte_count": 70_000,
                "output_sha256": CURRENT_SHA256,
                "attestation_sha256": comparison.sha256_bytes(
                    comparison.canonical_json_bytes(canonical_build_attestation)
                ),
            },
        }
    )
    source_results: list[dict[str, object]] = []
    for run in source_runs:
        result: dict[str, object] = {
            "run_id": run["run_id"],
            "status": "complete",
        }
        if run["profile_id"] == PROFILE_ID:
            run_id = str(run["run_id"])
            result.update(
                {
                    "result_artifact": {
                        "path": f"/retained/{run_id}/result.json",
                        "sha256": "4" * 64,
                    },
                    "artifacts": {
                        "capture": {
                            "path": f"/retained/{run_id}/capture.pcm",
                            "sha256": "5" * 64,
                            "pcm16_values": 400_000,
                            "frames": 200_000,
                            "channels": 2,
                        },
                        "expected_payloads": [
                            {
                                "frame_index": frame_index,
                                "path": f"/retained/{run_id}/expected-{frame_index}.bin",
                                "sha256": f"{6 + frame_index}" * 64,
                                "byte_count": BLOCKS_PER_SLOT * 256,
                            }
                            for frame_index in range(SCHEDULED_SLOTS)
                        ],
                    },
                    "timing": {
                        "detections": [
                            {
                                "frame_index": frame_index,
                                "detected_sample": 10_000 + frame_index * 200_000,
                            }
                            for frame_index in range(SCHEDULED_SLOTS)
                        ],
                        "detected_slots": SCHEDULED_SLOTS,
                        "scheduled_slots": SCHEDULED_SLOTS,
                    },
                }
            )
        source_results.append(result)

    manifest: dict[str, object] = {
        "schema": comparison.SOURCE_SCHEMA,
        "mode": "execute",
        "execution_id": "synthetic-comparison-execution",
        "plan": plan,
        "plan_sha256": comparison.sha256_bytes(comparison.canonical_json_bytes(plan)),
        "execution_provenance": {
            "source_hashes": {
                "c_bulk_dylib": {
                    "path": "/retained/current-decoder.dylib",
                    "sha256": CURRENT_SHA256,
                    "byte_count": 70_000,
                },
                **implementation_intent,
            },
            "decoder_binding": decoder_binding,
            "decoder_preflight": {
                "loaded_binary_receiver_contract": receiver_contract,
            },
            "matches_plan_implementation_intent": True,
        },
        "results": source_results,
        "summary": {"complete_runs": len(source_runs), "failed_runs": 0},
    }
    manifest_path = root / "source.manifest.json"
    _write_json(manifest_path, manifest)
    manifest_file_sha256 = comparison.sha256_file(manifest_path)

    legacy_counts = [[2, 2], [2, 2], [3, 2]]
    if current_counts is None:
        current_counts = [[3, 3], [2, 2], [2, 2]]

    def build_report(
        path: Path,
        *,
        decoder_sha256: str,
        candidate_counts: list[list[int]],
        baseline_count: int,
    ) -> None:
        report_runs: list[dict[str, object]] = []
        candidate_index = 0
        for source_run in source_runs:
            if source_run["profile_id"] == PROFILE_ID:
                report_runs.append(
                    _candidate_report_run(
                        source_run,
                        candidate_profile,
                        candidate_counts[candidate_index],
                    )
                )
                candidate_index += 1
            else:
                report_runs.append(
                    {
                        "run_id": source_run["run_id"],
                        "profile_id": source_run["profile_id"],
                        "pair_index": source_run["pair_index"],
                        "within_pair_order": source_run["within_pair_order"],
                        "diagnostic_noncandidate_verified_blocks": baseline_count,
                    }
                )
        report: dict[str, object] = {
            "schema": comparison.REANALYSIS_SCHEMA,
            "classification": {
                "dataset_role": "held-out",
                "label_supplied_by_cli": True,
            },
            "source_manifest": {
                "path": str(manifest_path),
                "sha256": manifest_file_sha256,
                "plan_sha256": manifest["plan_sha256"],
                "execution_id": manifest["execution_id"],
                "measurement_class": "synthetic-offline-fixture",
                "recorded_campaign_decoder": {
                    "recorded_path": "/retained/current-decoder.dylib",
                    "recorded_sha256": CURRENT_SHA256,
                    "recorded_byte_count": 70_000,
                },
            },
            "decoder": {
                "selection": "explicit test fixture",
                "path": f"/retained/{decoder_sha256}.dylib",
                "sha256_before_decode": decoder_sha256,
                "sha256_after_decode": decoder_sha256,
                "ordered_block_validity_api": True,
                "automatic_diversity_policy_v1_api": True,
            },
            "source_hashes": {
                "decoder_dylib": {
                    "path": f"/retained/{decoder_sha256}.dylib",
                    "sha256": decoder_sha256,
                    "byte_count": 70_000,
                    "role": "authoritative_executed_decoder",
                }
            },
            "runs": report_runs,
        }
        _finalize_report(report)
        _write_json(path, report)

    legacy_path = root / "legacy.reanalysis.json"
    current_path = root / "current.reanalysis.json"
    build_report(
        legacy_path,
        decoder_sha256=LEGACY_SHA256,
        candidate_counts=legacy_counts,
        baseline_count=0,
    )
    build_report(
        current_path,
        decoder_sha256=CURRENT_SHA256,
        candidate_counts=current_counts,
        baseline_count=99_999,
    )
    return manifest_path, legacy_path, current_path


def _rewrite_report(path: Path, mutation: object) -> None:
    report = json.loads(path.read_text(encoding="utf-8"))
    assert callable(mutation)
    mutation(report)
    _finalize_report(report)
    _write_json(path, report)


class GoodputDecoderComparisonTests(unittest.TestCase):
    def test_module_has_no_hardware_audio_or_decoder_imports(self) -> None:
        source = Path(comparison.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported_roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".")[0])
        self.assertTrue(
            {
                "adb_shell",
                "clib",
                "goodput_reanalysis",
                "harness",
                "numpy",
                "sounddevice",
                "tone_check_hardware",
            }.isdisjoint(imported_roots)
        )

    def test_valid_comparison_is_deterministic_and_candidate_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest, legacy, current = _fixture(Path(directory))
            first = comparison.build_comparison(manifest, legacy, current)
            second = comparison.build_comparison(manifest, legacy, current)
            expected_file_hashes = {
                "legacy": comparison.sha256_file(legacy),
                "current": comparison.sha256_file(current),
            }

        self.assertEqual(first, second)
        self.assertEqual(first["schema"], comparison.SCHEMA)
        self.assertTrue(comparison.verify_content_hash(first))
        self.assertEqual(
            first["integrity"]["schema_sha256"],
            comparison.sha256_bytes(comparison.canonical_json_bytes(comparison.SCHEMA)),
        )
        self.assertEqual(first["comparison_scope"]["profile_id"], PROFILE_ID)
        self.assertFalse(first["comparison_scope"]["noncandidate_profiles_compared"])
        self.assertEqual(
            [run["delta_verified_blocks"] for run in first["run_deltas"]],
            [2, 0, -1],
        )
        self.assertEqual(
            first["aggregate"]["run_outcomes"],
            {"improvements": 1, "ties": 1, "regressions": 1},
        )
        self.assertEqual(first["aggregate"]["legacy_verified_blocks"], 13)
        self.assertEqual(first["aggregate"]["current_verified_blocks"], 14)
        self.assertEqual(first["aggregate"]["total_scheduled_blocks"], 18)
        self.assertTrue(first["predeclared_gate_results"]["all_passed"])
        self.assertEqual(first["inputs"]["legacy_report"]["decoder_sha256"], LEGACY_SHA256)
        self.assertEqual(first["inputs"]["current_report"]["decoder_sha256"], CURRENT_SHA256)
        self.assertEqual(
            first["inputs"]["legacy_report"]["file_sha256"],
            expected_file_hashes["legacy"],
        )
        self.assertEqual(
            first["inputs"]["current_report"]["file_sha256"],
            expected_file_hashes["current"],
        )
        bad_schema_hash = json.loads(json.dumps(first))
        bad_schema_hash["integrity"]["schema_sha256"] = "0" * 64
        self.assertFalse(comparison.verify_content_hash(bad_schema_hash))

    def test_report_self_hash_tampering_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest, legacy, current = _fixture(Path(directory))
            report = json.loads(current.read_text(encoding="utf-8"))
            report["classification"]["dataset_role"] = "development"
            _write_json(current, report)

            with self.assertRaisesRegex(ValueError, "self-hash mismatch"):
                comparison.build_comparison(manifest, legacy, current)

    def test_source_manifest_identity_is_bound_by_both_reports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest, legacy, current = _fixture(Path(directory))

            def mutate(report: dict[str, object]) -> None:
                report["source_manifest"]["sha256"] = "a" * 64  # type: ignore[index]

            _rewrite_report(current, mutate)
            with self.assertRaisesRegex(ValueError, "source manifest sha256 differs"):
                comparison.build_comparison(manifest, legacy, current)

    def test_decoder_sha_roles_are_not_interchangeable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest, legacy, current = _fixture(Path(directory))

            def mutate(report: dict[str, object]) -> None:
                decoder = report["decoder"]  # type: ignore[index]
                decoder["sha256_before_decode"] = LEGACY_SHA256  # type: ignore[index]
                decoder["sha256_after_decode"] = LEGACY_SHA256  # type: ignore[index]
                report["source_hashes"]["decoder_dylib"]["sha256"] = LEGACY_SHA256  # type: ignore[index]

            _rewrite_report(current, mutate)
            with self.assertRaisesRegex(ValueError, "predeclared current SHA-256 role"):
                comparison.build_comparison(manifest, legacy, current)

    def test_source_provenance_gates_are_asserted_and_independently_recomputed(self) -> None:
        mutations = {
            "build intent assertion": (
                lambda source: source["execution_provenance"]["decoder_binding"].update(
                    {"matches_plan_build_intent": False}
                ),
                "does not assert the plan build intent",
            ),
            "build identity": (
                lambda source: source["execution_provenance"]["decoder_binding"][
                    "build_attestation"
                ].update({"recipe_id": "different-build-recipe"}),
                "recipe_id differs from decoder_build_intent",
            ),
            "build attestation hash": (
                lambda source: source["execution_provenance"]["decoder_binding"][
                    "build_attestation"
                ]["compiler"].update({"version": "tampered compiler"}),
                "build attestation hash is not canonical",
            ),
            "implementation intent assertion": (
                lambda source: source["execution_provenance"].update(
                    {"matches_plan_implementation_intent": False}
                ),
                "does not assert the plan implementation intent",
            ),
            "implementation identity": (
                lambda source: source["execution_provenance"]["source_hashes"][
                    "campaign_runner"
                ].update({"sha256": "f" * 64}),
                "execution source hash campaign_runner differs",
            ),
            "implementation HIL identity": (
                lambda source: source["execution_provenance"]["source_hashes"].pop(
                    "hil_harness"
                ),
                "execution source hash hil_harness differs",
            ),
        }
        for label, (mutate, error_pattern) in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                manifest, legacy, current = _fixture(Path(directory))
                source = json.loads(manifest.read_text(encoding="utf-8"))
                mutate(source)
                _write_json(manifest, source)

                with self.assertRaisesRegex(ValueError, error_pattern):
                    comparison.build_comparison(manifest, legacy, current)

    def test_candidate_denominator_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest, legacy, current = _fixture(Path(directory))

            def mutate(report: dict[str, object]) -> None:
                run = next(
                    run
                    for run in report["runs"]  # type: ignore[index]
                    if run["profile_id"] == PROFILE_ID
                )
                summary = run["policy_summaries"][PRIMARY_RECEIVER]
                summary["total_scheduled_blocks"] += 1
                run["primary_result"]["total_scheduled_blocks"] += 1

            _rewrite_report(current, mutate)
            with self.assertRaisesRegex(ValueError, "scheduled-block denominator"):
                comparison.build_comparison(manifest, legacy, current)

    def test_non_exact_intersection_mask_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest, legacy, current = _fixture(Path(directory))

            def mutate(report: dict[str, object]) -> None:
                run = next(
                    run
                    for run in report["runs"]  # type: ignore[index]
                    if run["profile_id"] == PROFILE_ID
                )
                frame = run["receiver_frames"][PRIMARY_RECEIVER][0]
                frame["verified_intersection_mask"][0] = False

            _rewrite_report(current, mutate)
            with self.assertRaisesRegex(ValueError, "intersection mask is not exact-position"):
                comparison.build_comparison(manifest, legacy, current)

    def test_failed_gates_are_retained_and_cli_returns_two(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, legacy, current = _fixture(
                root,
                current_counts=[[1, 1], [1, 1], [1, 1]],
            )
            output = root / "comparison.json"
            status = comparison.main(
                [str(manifest), str(legacy), str(current), "--out", str(output)]
            )
            written = json.loads(output.read_text(encoding="utf-8"))

            self.assertEqual(status, 2)
            self.assertFalse(written["predeclared_gate_results"]["all_passed"])
            self.assertEqual(written["aggregate"]["run_outcomes"]["regressions"], 3)
            self.assertTrue(comparison.verify_content_hash(written))
            with self.assertRaisesRegex(FileExistsError, "refusing to overwrite"):
                comparison.main(
                    [str(manifest), str(legacy), str(current), "--out", str(output)]
                )


if __name__ == "__main__":
    unittest.main()
