#!/usr/bin/env python3
"""Offline integrity tests for content-addressed goodput reanalysis."""

from __future__ import annotations

import ast
import ctypes
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

import clib
import goodput_bench as G
import goodput_reanalysis as reanalysis


DYLIB = Path(clib.loaded_library_path())


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _artifact(path: Path, **extra: object) -> dict[str, object]:
    return {
        "path": str(path),
        "sha256": reanalysis.sha256_file(path),
        "byte_count": path.stat().st_size,
        **extra,
    }


def _fixture(root: Path) -> tuple[Path, Path, Path]:
    codec = clib.BulkCodec(DYLIB)
    config = G.PHYConfig(cp_samples=240, data_symbols=8)
    geometry = G.compute_geometry(config)
    profile = {
        "profile_id": "candidate-test",
        "config": asdict(config),
        "amplitude": 0.25,
        "clip_sigma": 3.3,
        "geometry": asdict(geometry),
        "error_free_ceiling": {},
        "warning": "test fixture",
    }
    unused_profile = {**profile, "profile_id": "unused-control-test"}
    c_config = clib.make_cfg(
        bits_per_bin=config.bits_per_bin,
        rate=config.code_rate,
        n_sym=config.data_symbols,
        f_lo=config.f_lo_hz,
        f_hi=config.f_hi_hz,
        nfft=config.nfft,
        cp=config.cp_samples,
        sr=config.sample_rate_hz,
        amp=profile["amplitude"],
        clip_sigma=profile["clip_sigma"],
        pilot_every=config.pilot_every,
    )
    payload = bytes((index * 29 + 7) & 0xFF for index in range(geometry.payload_bytes))
    wave = codec.encode(c_config, payload)
    detected_sample = 2_000
    mono = np.concatenate(
        (
            np.zeros(detected_sample, dtype=np.float32),
            wave,
            np.zeros(2_000, dtype=np.float32),
        )
    )
    stereo = np.column_stack((mono, mono))
    pcm = np.clip(np.rint(stereo * 32768.0), -32768, 32767).astype("<i2")

    run_root = root / "run"
    run_root.mkdir()
    capture_path = run_root / "capture-stereo-s16le.pcm"
    capture_path.write_bytes(memoryview(np.ascontiguousarray(pcm)).cast("B"))
    expected_path = run_root / "expected-frame-0.bin"
    expected_path.write_bytes(payload)
    payload_record = {
        "frame_index": 0,
        "path": str(expected_path),
        "sha256": reanalysis.sha256_file(expected_path),
        "byte_count": len(payload),
    }
    run_plan = {
        "run_id": "pair-000-order-0-candidate-test",
        "pair_index": 0,
        "within_pair_order": 0,
        "profile_id": profile["profile_id"],
        "scheduled_frame_starts_tx_samples": [0],
        "scheduled_span_samples": geometry.frame_samples,
        "trailing_pad_samples": 16_000,
        "payloads": [
            {
                "frame_index": 0,
                "sha256": payload_record["sha256"],
                "byte_count": len(payload),
            }
        ],
    }
    plan = {
        "campaign_seed": 123,
        "pairs": 1,
        "direction": "mac-to-android",
        "physical_geometry_label": "synthetic digital fixture",
        "physical_authorization_note": "not physical",
        "profiles": [profile, unused_profile],
        "schedule": {
            "frames": 1,
            "inter_frame_gap_s": 0.25,
            "leading_pad_s": 0.0,
            "trailing_pad_s": 1.0 / 3.0,
            "cold_start_overhead_s": 0.0,
        },
        "runs": [run_plan],
        "measurement_contract": {
            "measurement_class": "one-frame-non-headline-smoke",
            "primary_receiver": "mic0",
        },
        "capture_policy": {
            "decode_margin_ms": 25.0,
        },
        "execution_binding": {},
    }
    result = {
        "run_id": run_plan["run_id"],
        "execution_id": "fixture-execution",
        "pair_index": 0,
        "within_pair_order": 0,
        "profile_id": profile["profile_id"],
        "status": "complete",
        "artifacts": {
            "expected_payloads": [payload_record],
            "capture": {
                "path": str(capture_path),
                "sha256": reanalysis.sha256_file(capture_path),
                "pcm16_values": int(pcm.size),
                "frames": int(len(pcm)),
                "channels": 2,
            },
        },
        "timing": {
            "policy": "fixture-recorded-known-chirp anchor; then fixed schedule windows",
            "acquisition_anchor": {"sample": detected_sample, "valid": True},
            "detections": [
                {
                    "frame_index": 0,
                    "expected_sample": detected_sample,
                    "detected_sample": detected_sample,
                    "score": 1.0,
                    "residual_samples": 0,
                    "discarded_duplicates": [],
                }
            ],
            "detected_slots": 1,
            "scheduled_slots": 1,
        },
        "primary_receiver": "mic0",
    }
    result_path = run_root / "result.json"
    _write_json(result_path, result)
    manifest_result = {
        **result,
        "result_artifact": _artifact(result_path),
    }
    manifest = {
        "schema": reanalysis.SOURCE_SCHEMA,
        "mode": "execute",
        "execution_id": "fixture-execution",
        "plan": plan,
        "plan_sha256": reanalysis.sha256_bytes(reanalysis.canonical_json_bytes(plan)),
        "execution_provenance": {
            "source_hashes": {
                "c_bulk_dylib": {
                    "path": str(DYLIB),
                    "sha256": reanalysis.sha256_file(DYLIB),
                    "byte_count": DYLIB.stat().st_size,
                }
            }
        },
        "results": [manifest_result],
        "summary": {"complete_runs": 1, "failed_runs": 0},
    }
    manifest_path = root / "manifest.json"
    _write_json(manifest_path, manifest)
    return manifest_path, capture_path, expected_path


class GoodputReanalysisTests(unittest.TestCase):
    def test_python_diversity_diagnostics_are_strict_json_values(self) -> None:
        diagnostics = clib.DiversityDiagnostics()
        diagnostics.struct_size = ctypes.sizeof(clib.DiversityDiagnostics)
        diagnostics.abi_version = 1
        diagnostics.policy_version = 1
        diagnostics.selected_receiver = 0
        diagnostics.scores_valid = 0
        diagnostics.selection_reason = 4
        diagnostics.validation_observations = 0
        diagnostics.primary_holdout_pilot_rms = float("inf")
        diagnostics.mrc_holdout_pilot_rms = float("nan")
        diagnostics.observed_mrc_to_primary_pilot_rms_ratio = float("inf")
        diagnostics.maximum_mrc_to_primary_pilot_rms_ratio = 0.95

        result = clib._diversity_result(diagnostics)

        self.assertIsNone(result["primary_holdout_pilot_rms"])
        self.assertIsNone(result["mrc_holdout_pilot_rms"])
        self.assertIsNone(result["observed_mrc_to_primary_pilot_rms_ratio"])
        json.dumps(result, allow_nan=False)

    def test_module_has_no_hardware_or_audio_imports(self) -> None:
        source = Path(reanalysis.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported_roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".")[0])
        self.assertTrue(
            {"harness", "tone_check_hardware", "sounddevice", "adb_shell"}.isdisjoint(
                imported_roots
            )
        )

    def test_crc_and_byte_identity_are_intersected_by_position(self) -> None:
        expected = bytes(G.CRC_BLOCK_BYTES * 2)
        decoded_payload = bytearray(expected)
        decoded_payload[G.CRC_BLOCK_BYTES] = 1
        result = reanalysis._intersection_record(
            {
                "payload": bytes(decoded_payload),
                "block_valid": [True, True],
                "blocks_ok": 2,
                "blocks_total": 2,
                "evm": 0.1,
            },
            expected,
            blocks=2,
        )
        self.assertEqual(result["crc_valid_mask"], [True, True])
        self.assertEqual(result["byte_identity_mask"], [True, False])
        self.assertEqual(result["verified_intersection_mask"], [True, False])
        self.assertEqual(result["verified_blocks"], 1)

    def test_explicit_decoder_reuses_window_and_verifies_exact_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest, _, _ = _fixture(Path(directory))
            report = reanalysis.build_reanalysis(
                manifest,
                DYLIB,
                dataset_role="development",
                label_note="synthetic fixture",
            )

            self.assertEqual(report["classification"]["dataset_role"], "development")
            self.assertEqual(report["schema"], "cyrinx.goodput-reanalysis.v2")
            self.assertEqual(Path(report["decoder"]["path"]), DYLIB.resolve())
            recorded_decoder = report["source_manifest"]["recorded_campaign_decoder"]
            self.assertNotIn("path", recorded_decoder)
            self.assertEqual(recorded_decoder["recorded_sha256"], reanalysis.sha256_file(DYLIB))
            self.assertIn("without resolving", recorded_decoder["path_semantics"])
            self.assertEqual(
                report["source_hashes"]["decoder_dylib"]["role"],
                "authoritative_executed_decoder",
            )
            self.assertEqual(
                report["source_hashes"]["c_bulk_source"]["role"],
                "repository_context_only_not_proven_to_build_selected_decoder",
            )
            self.assertTrue(report["decoder"]["automatic_diversity_policy_v1_api"])
            self.assertTrue(
                any(
                    "source-to-binary binding requires separate evidence" in limitation
                    for limitation in report["limitations"]
                )
            )
            self.assertTrue(report["timing_policy"]["recorded_slot_detections_reused"])
            run = report["runs"][0]
            blocks = run["profile"]["geometry"]["crc_blocks"]
            for policy in reanalysis.RECEIVER_POLICIES:
                summary = run["policy_summaries"][policy]
                self.assertEqual(summary["verified_blocks"], blocks)
                record = run["receiver_frames"][policy][0]
                self.assertTrue(all(record["crc_valid_mask"]))
                self.assertTrue(all(record["byte_identity_mask"]))
                self.assertTrue(all(record["verified_intersection_mask"]))
                self.assertEqual(record["decode_window"]["start_sample"], 800)
            automatic_record = run["receiver_frames"][reanalysis.PILOT_SELECT_POLICY][0]
            automatic_diagnostics = automatic_record["automatic_diversity"]
            self.assertEqual(automatic_diagnostics["policy_version"], 1)
            self.assertEqual(automatic_diagnostics["selected_receiver"], "mic0")
            self.assertFalse(automatic_diagnostics["payload_or_crc_used_for_selection"])
            automatic_summary = run["policy_summaries"][reanalysis.PILOT_SELECT_POLICY]
            self.assertEqual(
                automatic_summary["automatic_diversity_selected_receiver_counts"],
                {"mic0": 1, "mrc01": 0, "missing_or_failed": 0},
            )
            accounting = automatic_summary["automatic_diversity_slot_accounting"]
            self.assertEqual(accounting["scheduled_slots"], 1)
            self.assertEqual(accounting["observed_diagnostics"], 1)
            self.assertEqual(accounting["slot_outcomes"]["diagnostics_observed"], 1)
            unhashed = dict(report)
            recorded_hash = unhashed.pop("report_sha256")
            self.assertEqual(
                recorded_hash,
                reanalysis.sha256_bytes(reanalysis.canonical_json_bytes(unhashed)),
            )

    def test_capture_tampering_is_rejected_before_decode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest, capture, _ = _fixture(Path(directory))
            data = bytearray(capture.read_bytes())
            data[-1] ^= 1
            capture.write_bytes(data)

            with self.assertRaisesRegex(ValueError, "capture hash mismatch"):
                reanalysis.build_reanalysis(manifest, DYLIB, dataset_role="development")

    def test_selector_slot_accounting_includes_missing_and_failed_decodes(self) -> None:
        records = [
            {
                "decode_status": "decoded",
                "automatic_diversity": {
                    "selected_receiver": "mic0",
                    "selection_reason": "primary_margin_not_met",
                },
            },
            {
                "decode_status": "missing-recorded-slot",
                "automatic_diversity": None,
            },
            {
                "decode_status": "decoder-returned-no-frame",
                "automatic_diversity": None,
            },
        ]

        accounting = reanalysis._automatic_diversity_slot_accounting(
            records, scheduled_slots=3
        )

        self.assertEqual(accounting["observed_diagnostics"], 1)
        self.assertEqual(
            accounting["slot_outcomes"],
            {
                "diagnostics_observed": 1,
                "missing_timing_detection": 1,
                "decoder_returned_no_frame": 1,
                "failed_run_unresolved": 0,
                "run_not_yet_attempted": 0,
            },
        )
        self.assertEqual(accounting["selected_receiver"]["missing_or_failed"], 2)

    def test_expected_payload_tampering_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest, _, expected = _fixture(Path(directory))
            data = bytearray(expected.read_bytes())
            data[0] ^= 1
            expected.write_bytes(data)

            with self.assertRaisesRegex(ValueError, "expected frame 0 hash mismatch"):
                reanalysis.build_reanalysis(manifest, DYLIB, dataset_role="development")

    def test_manifest_result_divergence_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest_path, _, _ = _fixture(Path(directory))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["results"][0]["timing"]["detections"][0]["detected_sample"] += 1
            _write_json(manifest_path, manifest)

            with self.assertRaisesRegex(ValueError, "manifest/result content mismatch"):
                reanalysis.build_reanalysis(
                    manifest_path,
                    DYLIB,
                    dataset_role="held-out",
                )

    def test_plan_tampering_and_ambiguous_role_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest_path, _, _ = _fixture(Path(directory))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["plan"]["capture_policy"]["decode_margin_ms"] = 50.0
            _write_json(manifest_path, manifest)
            with self.assertRaisesRegex(ValueError, "source plan hash mismatch"):
                reanalysis.build_reanalysis(
                    manifest_path,
                    DYLIB,
                    dataset_role="development",
                )
            with self.assertRaisesRegex(ValueError, "dataset_role"):
                reanalysis.build_reanalysis(
                    manifest_path,
                    DYLIB,
                    dataset_role="unspecified",
                )


if __name__ == "__main__":
    unittest.main()
