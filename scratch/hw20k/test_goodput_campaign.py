#!/usr/bin/env python3
"""Deterministic tests for executable goodput-campaign diagnostics."""

import unittest
from unittest import mock
from pathlib import Path
import tempfile
import json

import numpy as np

import goodput_campaign as campaign
import goodput_bench as G
import modem


def automatic_diagnostics(*, selected_receiver="mic0", selection_reason="primary_margin_not_met"):
    return {
        "abi_version": campaign.AUTO_V1_DIAGNOSTICS_ABI_VERSION,
        "policy_version": campaign.AUTO_V1_POLICY_VERSION,
        "selected_receiver": selected_receiver,
        "scores_valid": True,
        "selection_reason": selection_reason,
        "payload_or_crc_used_for_selection": False,
        "data_bins_used_for_selection": False,
        "validation_observations": 8,
        "primary_holdout_pilot_rms": 0.1,
        "mrc_holdout_pilot_rms": 0.1,
        "observed_mrc_to_primary_pilot_rms_ratio": 1.0,
        "maximum_mrc_to_primary_pilot_rms_ratio": (
            campaign.AUTO_V1_MAX_MRC_TO_PRIMARY_PILOT_RMS_RATIO
        ),
    }


class GeometryValidationTests(unittest.TestCase):
    def test_real_fft_modems_reject_dc_and_nyquist_data_bins(self) -> None:
        for config in (G.PHYConfig(f_lo_hz=0), G.PHYConfig(f_hi_hz=24_000)):
            with self.assertRaisesRegex(ValueError, "exclude DC and Nyquist"):
                G.compute_geometry(config)
        for bounds in ({"f_lo": 0}, {"f_hi": 24_000}):
            with self.assertRaisesRegex(ValueError, "exclude DC and Nyquist"):
                modem.Config(**bounds)


class ChannelObservationDiagnosticsTests(unittest.TestCase):
    def test_bit_identical_channels_are_not_receive_diversity(self) -> None:
        mono = np.array([0, 1, -2, 4, -8, 16], dtype=np.int16)
        stereo = np.column_stack((mono, mono))

        result = campaign.channel_observation_diagnostics(np, stereo)

        self.assertTrue(result["bit_identical"])
        self.assertEqual(result["exact_equal_frames"], len(mono))
        self.assertEqual(result["difference_rms"], 0.0)
        self.assertEqual(result["effective_rank"], 1.0)
        self.assertEqual(
            result["receive_diversity_interpretation"],
            "not-available-bit-identical-logical-channels",
        )

    def test_unequal_channels_do_not_claim_physical_independence(self) -> None:
        stereo = np.array(
            [
                [1.0, 0.0],
                [0.0, 1.0],
                [-1.0, 0.0],
                [0.0, -1.0],
            ]
        )

        result = campaign.channel_observation_diagnostics(np, stereo)

        self.assertFalse(result["bit_identical"])
        self.assertAlmostEqual(result["effective_rank"], 2.0)
        self.assertEqual(
            result["receive_diversity_interpretation"],
            "not-established-from-sample-inequality-alone",
        )

    def test_requires_exactly_two_nonempty_channels(self) -> None:
        with self.assertRaises(ValueError):
            campaign.channel_observation_diagnostics(np, np.zeros((4, 1)))
        with self.assertRaises(ValueError):
            campaign.channel_observation_diagnostics(np, np.zeros((0, 2)))


class AcquisitionAnchorTests(unittest.TestCase):
    def test_selects_known_chirp_peak_without_payload_inputs(self) -> None:
        score = np.full(20_000, 0.01)
        score[12_345] = 0.8
        result = campaign.acquisition_anchor_from_score(
            np,
            score,
            search_start_sample=0,
            search_stop_sample=len(score),
            minimum_score=0.12,
            minimum_psr_db=6.0,
            exclusion_samples=100,
        )
        self.assertTrue(result["valid"])
        self.assertEqual(result["sample"], 12_345)
        self.assertIn("no decoded bytes", result["selection_inputs"])

    def test_rejects_ambiguous_or_weak_anchor(self) -> None:
        score = np.full(1_000, 0.01)
        score[200] = 0.2
        score[800] = 0.19
        ambiguous = campaign.acquisition_anchor_from_score(
            np,
            score,
            search_start_sample=0,
            search_stop_sample=len(score),
            minimum_score=0.12,
            minimum_psr_db=6.0,
            exclusion_samples=20,
        )
        self.assertFalse(ambiguous["valid"])
        score[200] = 0.1
        score[800] = 0.01
        weak = campaign.acquisition_anchor_from_score(
            np,
            score,
            search_start_sample=0,
            search_stop_sample=len(score),
            minimum_score=0.12,
            minimum_psr_db=6.0,
            exclusion_samples=20,
        )
        self.assertFalse(weak["valid"])


class ManifestIdentityTests(unittest.TestCase):
    def test_identical_plans_receive_distinct_execution_ids(self) -> None:
        first = campaign.wrap_manifest({"fixed": "plan"}, mode="dry-run")
        second = campaign.wrap_manifest({"fixed": "plan"}, mode="dry-run")
        self.assertEqual(first["plan_sha256"], second["plan_sha256"])
        self.assertNotEqual(first["execution_id"], second["execution_id"])
        self.assertRegex(first["execution_id"], r"^[0-9a-f]{32}$")


class ReceiverPolicyTests(unittest.TestCase):
    def test_pilot_selector_dispatches_to_c_auto_v1(self) -> None:
        class FakeCodec:
            def decode2_auto_v1(self, config, primary, secondary):
                return {
                    "config": config,
                    "primary": primary.copy(),
                    "secondary": secondary.copy(),
                    "automatic_diversity": automatic_diagnostics(),
                }

        capture = np.arange(20, dtype=np.float32).reshape(10, 2)
        result = campaign._decode_one_policy(
            FakeCodec(),
            campaign.PILOT_SELECT_POLICY,
            "config",
            capture,
            2,
            7,
        )

        self.assertEqual(result["config"], "config")
        np.testing.assert_array_equal(result["primary"], capture[2:7, 0])
        np.testing.assert_array_equal(result["secondary"], capture[2:7, 1])

    def test_manifest_predeclares_payload_independent_auto_policy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = Path(directory) / "plan.json"
            with mock.patch.object(campaign, "assert_dry_run_import_boundary"):
                campaign.main(
                    [
                        "--primary-receiver",
                        campaign.PILOT_SELECT_POLICY,
                        "--manifest",
                        str(manifest_path),
                    ]
                )
            contract = json.loads(manifest_path.read_text(encoding="utf-8"))["plan"][
                "measurement_contract"
            ]
            policy = contract["automatic_diversity_policy"]
            self.assertEqual(policy["policy_id"], campaign.PILOT_SELECT_POLICY)
            self.assertEqual(policy["diagnostics_abi_version"], 1)
            self.assertEqual(policy["policy_version"], 1)
            self.assertEqual(policy["maximum_mrc_to_primary_pilot_rms_ratio"], 0.95)
            self.assertFalse(policy["payload_or_crc_used_for_selection"])
            self.assertFalse(policy["data_bins_used_for_selection"])
            self.assertIn("held-out known pilots", contract["receiver_selection"])
            self.assertIn("ordinary in-sample EVM", contract["forbidden_selection_inputs"])

    def test_frozen_diagnostics_invariants_are_validated(self) -> None:
        campaign._validate_auto_v1_diagnostics(
            automatic_diagnostics(), context="test frame"
        )
        mutations = {
            "abi_version": 2,
            "policy_version": 2,
            "maximum_mrc_to_primary_pilot_rms_ratio": np.nextafter(0.95, 1.0),
            "payload_or_crc_used_for_selection": True,
            "data_bins_used_for_selection": True,
        }
        for field, value in mutations.items():
            with self.subTest(field=field):
                diagnostics = automatic_diagnostics()
                diagnostics[field] = value
                with self.assertRaisesRegex(RuntimeError, field):
                    campaign._validate_auto_v1_diagnostics(
                        diagnostics, context="test frame"
                    )

    def test_auto_output_must_match_selected_receiver_exactly(self) -> None:
        primary = {
            "payload": b"primary",
            "block_valid": [True, False],
            "blocks_ok": 1,
            "blocks_total": 2,
            "evm": 0.125,
        }
        mrc = {
            "payload": b"mrc",
            "block_valid": [True, True],
            "blocks_ok": 2,
            "blocks_total": 2,
            "evm": 0.0625,
        }
        automatic = dict(primary)
        automatic["automatic_diversity"] = automatic_diagnostics()
        records = {
            "mic0": primary,
            "mrc01": mrc,
            campaign.PILOT_SELECT_POLICY: automatic,
        }
        campaign._assert_auto_v1_selected_output_parity(records, context="test frame")

        for field in ("payload", "block_valid", "blocks_ok", "blocks_total", "evm"):
            with self.subTest(field=field):
                mismatched = {key: dict(value) for key, value in records.items()}
                mismatched[campaign.PILOT_SELECT_POLICY][field] = object()
                with self.assertRaisesRegex(RuntimeError, field):
                    campaign._assert_auto_v1_selected_output_parity(
                        mismatched, context="test frame"
                    )

        automatic_failed = dict(automatic)
        automatic_failed["payload"] = None
        with self.assertRaisesRegex(RuntimeError, "returned no frame"):
            campaign._assert_auto_v1_selected_output_parity(
                {
                    "mic0": primary,
                    "mrc01": mrc,
                    campaign.PILOT_SELECT_POLICY: automatic_failed,
                },
                context="test frame",
            )


class RuntimePreflightTests(unittest.TestCase):
    class FakeCodec:
        _HAS_BLOCK_VALIDITY_API = True
        _HAS_AUTO_V1_API = True
        AUTO_V1_DIAGNOSTICS_ABI_VERSION = 1
        AUTO_V1_POLICY_VERSION = 1
        AUTO_V1_MAX_MRC_TO_PRIMARY_PILOT_RMS_RATIO = 0.95

        def __init__(self, geometry):
            self.referee_geometry = geometry
            self.payload = None

        def make_cfg(self, **kwargs):
            return kwargs

        def geometry(self, config):
            class Geometry:
                pass

            result = Geometry()
            result.bin_lo = self.referee_geometry.bin_lo
            result.bin_hi = self.referee_geometry.bin_hi
            result.n_blocks = self.referee_geometry.crc_blocks
            result.payload_bytes = self.referee_geometry.payload_bytes
            result.frame_samples = self.referee_geometry.frame_samples
            return result

        def encode(self, config, payload):
            self.payload = payload
            return np.zeros(self.referee_geometry.frame_samples, dtype=np.float32)

        def decode(self, config, capture):
            return self._result()

        def decode2_auto_v1(self, config, primary, secondary):
            result = self._result()
            result["automatic_diversity"] = automatic_diagnostics()
            return result

        def _result(self):
            return {
                "payload": self.payload,
                "block_valid": [True] * self.referee_geometry.crc_blocks,
                "blocks_ok": self.referee_geometry.crc_blocks,
                "blocks_total": self.referee_geometry.crc_blocks,
                "evm": 0.125,
            }

    def test_identical_channel_auto_v1_preflight_matches_mono(self) -> None:
        profile = campaign.CampaignProfile("preflight", G.PHYConfig())
        geometry = G.compute_geometry(profile.config)
        codec = self.FakeCodec(geometry)

        campaign._runtime_preflight(
            np,
            codec,
            [profile],
            G.BurstSchedule(frames=1),
            seed=7,
        )


class DecoderBindingTests(unittest.TestCase):
    def test_content_addressed_decoder_copy_is_read_only_and_reverified(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.dylib"
            source.write_bytes(b"deterministic decoder bytes")
            artifact_root = root / "artifacts"

            binding = campaign._stage_execution_decoder_copy(artifact_root, source)
            frozen = Path(binding["frozen_path"])

            self.assertEqual(frozen.read_bytes(), source.read_bytes())
            self.assertIn(binding["expected_sha256"], frozen.name)
            self.assertEqual(frozen.stat().st_mode & 0o222, 0)
            self.assertEqual(frozen.parent.stat().st_mode & 0o222, 0)
            self.assertEqual(
                campaign._verify_execution_decoder_copy(binding, "before_playback"),
                binding["expected_sha256"],
            )

            frozen.chmod(0o644)
            frozen.write_bytes(b"changed decoder bytes")
            with self.assertRaisesRegex(RuntimeError, "changed at after_campaign"):
                campaign._verify_execution_decoder_copy(binding, "after_campaign")
            frozen.parent.chmod(0o755)


class AutomaticDiversitySlotAccountingTests(unittest.TestCase):
    def test_scheduled_denominator_includes_missing_failed_and_unattempted_slots(self) -> None:
        manifest = {
            "plan": {
                "schedule": {"frames": 3},
                "runs": [
                    {"run_id": "complete", "profile_id": "profile"},
                    {"run_id": "failed", "profile_id": "profile"},
                    {"run_id": "pending", "profile_id": "profile"},
                ],
            },
            "results": [
                {
                    "run_id": "complete",
                    "status": "complete",
                    "decoded_diagnostics": {
                        campaign.PILOT_SELECT_POLICY: [
                            {"automatic_diversity": automatic_diagnostics()},
                            {"automatic_diversity": None},
                        ]
                    },
                },
                {"run_id": "failed", "status": "failed"},
            ],
        }

        result = campaign._automatic_diversity_slot_accounting(manifest)["profile"]

        self.assertEqual(result["scheduled_slots"], 9)
        self.assertEqual(result["observed_diagnostics"], 1)
        self.assertEqual(
            result["slot_outcomes"],
            {
                "diagnostics_observed": 1,
                "missing_timing_detection": 1,
                "decoder_returned_no_frame": 1,
                "failed_run_unresolved": 3,
                "run_not_yet_attempted": 3,
            },
        )
        self.assertEqual(result["selected_receiver"]["mic0"], 1)
        self.assertEqual(result["selected_receiver"]["mrc01"], 0)
        self.assertEqual(result["selected_receiver"]["missing_or_failed"], 8)
        self.assertEqual(sum(result["slot_outcomes"].values()), result["scheduled_slots"])


class PairedPayloadTests(unittest.TestCase):
    def test_identical_policy_pairs_same_bytes_but_not_different_frames(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = Path(directory) / "plan.json"
            with mock.patch.object(campaign, "assert_dry_run_import_boundary"):
                result = campaign.main(
                    [
                        "--pairs",
                        "2",
                        "--baseline-cp",
                        "240",
                        "--candidate-cp",
                        "384",
                        "--paired-identical-payloads",
                        "--balanced-pair-order",
                        "--manifest",
                        str(manifest_path),
                    ]
                )

            self.assertEqual(result, 0)
            plan = json.loads(manifest_path.read_text(encoding="utf-8"))["plan"]
            self.assertEqual(
                plan["measurement_contract"]["payload_pairing"]["policy"],
                "identical-within-pair-v1",
            )
            profiles = {profile["profile_id"]: profile for profile in plan["profiles"]}
            self.assertEqual(
                set(profiles),
                {"baseline-cp240-p8", "candidate-cp384-p8-b4-r34"},
            )
            self.assertAlmostEqual(
                profiles["baseline-cp240-p8"]["error_free_ceiling"][
                    "scheduled_goodput_bps"
                ],
                44_214.16234887737,
            )
            self.assertAlmostEqual(
                profiles["candidate-cp384-p8-b4-r34"]["error_free_ceiling"][
                    "scheduled_goodput_bps"
                ],
                41_830.06535947713,
            )
            runs_by_pair: dict[int, list[dict]] = {}
            for run in plan["runs"]:
                runs_by_pair.setdefault(run["pair_index"], []).append(run)
            first_profiles = [
                min(runs, key=lambda run: run["within_pair_order"])["profile_id"]
                for _, runs in sorted(runs_by_pair.items())
            ]
            self.assertEqual(len(set(first_profiles)), 2)
            unique_hashes: set[str] = set()
            for runs in runs_by_pair.values():
                self.assertEqual(len(runs), 2)
                first_hashes = [record["sha256"] for record in runs[0]["payloads"]]
                second_hashes = [record["sha256"] for record in runs[1]["payloads"]]
                self.assertEqual(first_hashes, second_hashes)
                self.assertEqual(len(first_hashes), len(set(first_hashes)))
                unique_hashes.update(first_hashes)
            self.assertEqual(len(unique_hashes), 10)

    def test_balanced_order_requires_even_pair_count(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "even number of pairs"):
                campaign.main(
                    [
                        "--pairs",
                        "3",
                        "--balanced-pair-order",
                        "--manifest",
                        str(Path(directory) / "plan.json"),
                    ]
                )

    def test_identical_policy_rejects_unequal_payload_capacities(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "equal payload byte counts"):
                campaign.main(
                    [
                        "--paired-identical-payloads",
                        "--candidate-bits-per-bin",
                        "6",
                        "--manifest",
                        str(Path(directory) / "plan.json"),
                    ]
                )

    def test_default_policy_keeps_profile_payloads_independent(self) -> None:
        args = campaign.argument_parser().parse_args(["--pairs", "1"])
        args.amplitude = 0.7
        profiles = campaign.profiles_from_args(args)
        plan = campaign.build_plan(
            profiles,
            campaign.schedule_from_args(args),
            pairs=1,
            seed=1,
            primary_receiver="mic0",
            pre_roll_s=0.7,
            post_roll_s=0.7,
            origin_search_ms=350,
            anchor_search_stop_ms=2_000,
            minimum_chirp_score=0.12,
            minimum_anchor_psr_db=6,
            decode_margin_ms=25,
            android_source="unprocessed",
            mac_output_volume=None,
            geometry_label=None,
            authorization_note=None,
        )

        runs = sorted(plan["runs"], key=lambda run: run["profile_id"])
        self.assertEqual(
            plan["measurement_contract"]["payload_pairing"]["policy"],
            "independent-by-profile-v1",
        )
        self.assertNotEqual(
            [record["sha256"] for record in runs[0]["payloads"]],
            [record["sha256"] for record in runs[1]["payloads"]],
        )


class PhysicalBindingTests(unittest.TestCase):
    def test_binding_hashes_target_route_and_calibration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            provenance = root / "target.json"
            route = root / "route.json"
            calibration = root / "calibration.json"
            provenance.write_text(
                json.dumps(
                    {
                        "android_target": {
                            "serial": "pixel",
                            "model": "Pixel 7a",
                            "build_fingerprint": "google/test",
                        },
                        "hil_apk": {"installed_apk_sha256": "a" * 64},
                    }
                ),
                encoding="utf-8",
            )
            route.write_text(
                json.dumps({"schema": campaign.ROUTE_SIGNATURE_SCHEMA}),
                encoding="utf-8",
            )
            calibration.write_text("{}\n", encoding="utf-8")
            binding = campaign.load_execution_binding(
                provenance,
                route,
                calibration,
                "pixel|fixed-pose|vol14|peak0.06",
            )
            self.assertEqual(binding["expected_target"]["serial"], "pixel")
            self.assertEqual(
                binding["calibration_artifact_sha256"],
                campaign.sha256_file(calibration),
            )
            self.assertEqual(
                binding["expected_route_signature_sha256"],
                campaign.sha256_bytes(
                    campaign.canonical_json_bytes(
                        {"schema": campaign.ROUTE_SIGNATURE_SCHEMA}
                    )
                ),
            )


class PhysicalExecutionLimitTests(unittest.TestCase):
    def test_execute_requires_explicit_waveform_amplitude(self) -> None:
        with self.assertRaisesRegex(SystemExit, "explicit --amplitude"):
            campaign.main(["--execute"])

    def test_execute_rejects_waveform_above_reviewed_cap(self) -> None:
        with self.assertRaisesRegex(SystemExit, "exceeds drive-envelope cap"):
            campaign.main(["--execute", "--amplitude", "0.180001"])

    def test_execute_rejects_output_volume_above_reviewed_cap(self) -> None:
        with self.assertRaisesRegex(SystemExit, r"--mac-output-volume must be in"):
            campaign.main(
                [
                    "--execute",
                    "--amplitude",
                    "0.18",
                    "--mac-output-volume",
                    "31",
                ]
            )

    def test_historical_envelope_requires_one_frame_one_pair(self) -> None:
        with self.assertRaisesRegex(SystemExit, "requires --smoke-one-frame"):
            campaign.main(["--historical-drive-calibration-envelope"])

    def test_historical_envelope_is_recorded_in_dry_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = Path(directory) / "plan.json"
            with mock.patch.object(campaign, "assert_dry_run_import_boundary"):
                result = campaign.main(
                    [
                        "--pairs",
                        "1",
                        "--smoke-one-frame",
                        "--historical-drive-calibration-envelope",
                        "--amplitude",
                        "0.7",
                        "--mac-output-volume",
                        "100",
                        "--authorization-note",
                        "test operator authorization",
                        "--manifest",
                        str(manifest_path),
                    ]
                )

            self.assertEqual(result, 0)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            policy = manifest["plan"]["capture_policy"]
            self.assertEqual(
                policy["drive_envelope"]["envelope_id"],
                "historical-pixel-calibration-v1",
            )
            self.assertEqual(policy["maximum_runner_output_volume_percent"], 100)
            self.assertEqual(policy["maximum_runner_waveform_peak"], 0.7)
            self.assertFalse(manifest["plan"]["measurement_contract"]["headline_eligible"])

    def test_default_drive_envelope_is_recorded_in_dry_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = Path(directory) / "plan.json"
            with mock.patch.object(campaign, "assert_dry_run_import_boundary"):
                result = campaign.main(
                    [
                        "--amplitude",
                        "0.18",
                        "--mac-output-volume",
                        "30",
                        "--manifest",
                        str(manifest_path),
                    ]
                )

            self.assertEqual(result, 0)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            envelope = manifest["plan"]["capture_policy"]["drive_envelope"]
            self.assertEqual(envelope["envelope_id"], "reviewed-conservative-v1")
            self.assertEqual(envelope["maximum_output_volume_percent"], 30)
            self.assertEqual(envelope["maximum_waveform_peak"], 0.18)

    def test_historical_execute_requires_authorization_note(self) -> None:
        with self.assertRaisesRegex(SystemExit, "nonempty --authorization-note"):
            campaign.main(
                [
                    "--execute",
                    "--pairs",
                    "1",
                    "--smoke-one-frame",
                    "--historical-drive-calibration-envelope",
                    "--amplitude",
                    "0.7",
                    "--mac-output-volume",
                    "100",
                    "--geometry-label",
                    "test geometry",
                ]
            )

    def test_historical_envelope_bounds_frame_duration(self) -> None:
        args = campaign.argument_parser().parse_args(
            [
                "--pairs",
                "1",
                "--smoke-one-frame",
                "--historical-drive-calibration-envelope",
                "--symbols",
                "65",
                "--amplitude",
                "0.18",
            ]
        )
        profiles = campaign.profiles_from_args(args)
        with self.assertRaisesRegex(ValueError, "must not exceed 4.0 seconds"):
            campaign.build_plan(
                profiles,
                campaign.schedule_from_args(args),
                pairs=1,
                seed=1,
                primary_receiver="mic0",
                pre_roll_s=0.7,
                post_roll_s=0.7,
                origin_search_ms=350,
                anchor_search_stop_ms=2_600,
                minimum_chirp_score=0.12,
                minimum_anchor_psr_db=6,
                decode_margin_ms=25,
                android_source="unprocessed",
                mac_output_volume=50,
                geometry_label="test geometry",
                authorization_note="test operator authorization",
                drive_envelope=campaign.HISTORICAL_CALIBRATION_DRIVE_ENVELOPE,
            )

    def test_historical_envelope_rejects_values_above_historical_drive(self) -> None:
        common = [
            "--execute",
            "--pairs",
            "1",
            "--smoke-one-frame",
            "--historical-drive-calibration-envelope",
        ]
        with self.assertRaisesRegex(SystemExit, "exceeds drive-envelope cap"):
            campaign.main(common + ["--amplitude", "0.700001"])
        with self.assertRaisesRegex(SystemExit, r"--mac-output-volume must be in"):
            campaign.main(
                common
                + [
                    "--amplitude",
                    "0.7",
                    "--mac-output-volume",
                    "101",
                ]
            )


if __name__ == "__main__":
    unittest.main()
