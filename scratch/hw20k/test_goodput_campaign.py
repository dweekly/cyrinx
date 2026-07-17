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


EVIDENCE_ROOT = (
    Path(__file__).resolve().parent / "evidence" / "pixel7a-faceup-volume50-v1"
)


def load_fixture_binding(root: Path, profile_key: str | None = None) -> dict:
    return campaign.load_execution_binding(
        root / "target.json",
        root / "route.json",
        root / "calibration.json",
        profile_key or campaign.PIXEL_VOLUME_50_EVIDENCE_PROFILE_KEY,
    )


def physical_binding_fixture(root: Path) -> tuple[list[str], dict]:
    provenance = root / "target.json"
    route = root / "route.json"
    calibration = root / "calibration.json"
    provenance.write_bytes((EVIDENCE_ROOT / "target-provenance.json").read_bytes())
    route.write_bytes((EVIDENCE_ROOT / "route-signature.json").read_bytes())
    calibration.write_bytes((EVIDENCE_ROOT / "qualification.json").read_bytes())
    profile_key = campaign.PIXEL_VOLUME_50_EVIDENCE_PROFILE_KEY
    binding = load_fixture_binding(root, profile_key)
    cli_arguments = [
        "--target-provenance",
        str(provenance),
        "--expected-route-signature",
        str(route),
        "--calibration-artifact",
        str(calibration),
        "--calibration-profile-key",
        profile_key,
    ]
    return cli_arguments, binding


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
    def test_tracked_pixel_volume_50_evidence_bundle_is_exact(self) -> None:
        target_path = EVIDENCE_ROOT / "target-provenance.json"
        route_path = EVIDENCE_ROOT / "route-signature.json"
        qualification_path = EVIDENCE_ROOT / "qualification.json"
        self.assertEqual(
            campaign.sha256_file(target_path),
            campaign.PIXEL_VOLUME_50_TARGET_PROVENANCE_SHA256,
        )
        route = json.loads(route_path.read_bytes())
        self.assertEqual(
            campaign.sha256_bytes(campaign.canonical_json_bytes(route)),
            campaign.PIXEL_VOLUME_50_ROUTE_CANONICAL_SHA256,
        )
        self.assertEqual(
            campaign.sha256_file(qualification_path),
            campaign.PIXEL_VOLUME_50_QUALIFICATION_SHA256,
        )
        qualification = json.loads(qualification_path.read_bytes())
        self.assertEqual(
            qualification["profile_key"],
            campaign.PIXEL_VOLUME_50_EVIDENCE_PROFILE_KEY,
        )
        campaigns = {
            item["role"]: item
            for item in qualification["retained_pixel_campaign_manifests"]
        }
        self.assertEqual(
            campaigns["volume-30-matched-descending-repeat"]["sha256"],
            "7f4b66bf9f98f35d04056a865bc26ff27671de809a1765faeca8756b30c09707",
        )
        self.assertEqual(
            campaigns["volume-50-level-smoke"]["verified_blocks"],
            {"baseline-cp768-p8": "75/75", "candidate-cp240-p8-b4-r34": "75/75"},
        )
        self.assertEqual(
            campaigns["volume-70-matched-ascending"]["sha256"],
            "a719f9c27cc0c0cc7efac209c2f51c6ba1fc9d233179f82adc299a12aba4bd05",
        )
        self.assertIn("worse block delivery", campaigns["volume-70-matched-ascending"]["outcome"])
        self.assertEqual(
            campaigns["volume-50-reverse-order-confirmation"]["sha256"],
            "8d60297837577f043421fd7721058e85ae295f99ad87a8c6801a31347b51d86f",
        )
        for retained in campaigns.values():
            self.assertEqual(set(retained["android_source_clipped_samples"].values()), {0})

    def test_binding_hashes_target_route_and_calibration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, binding = physical_binding_fixture(root)
            self.assertEqual(binding["expected_target"]["serial"], "38291JEHN00306")
            self.assertEqual(
                binding["calibration_artifact_sha256"],
                campaign.sha256_file(root / "calibration.json"),
            )
            self.assertEqual(
                binding["expected_route_signature_sha256"],
                campaign.sha256_bytes(
                    campaign.canonical_json_bytes(
                        json.loads((root / "route.json").read_bytes())
                    )
                ),
            )


class PhysicalExecutionLimitTests(unittest.TestCase):
    @staticmethod
    def _build_evidence_plan(
        *,
        execution_binding: dict | None,
        authorization_note: str | None,
        amplitude: float = 0.18,
        mac_output_volume: int | None = 50,
        frames: int = 5,
        android_source: str = "unprocessed",
        sample_rate_hz: int = 48_000,
        geometry_label: str = campaign.PIXEL_VOLUME_50_GEOMETRY_LABEL,
    ) -> dict:
        args = campaign.argument_parser().parse_args(
            [
                "--amplitude",
                str(amplitude),
                "--symbols",
                "65",
                "--sample-rate",
                str(sample_rate_hz),
                "--f-hi",
                "20000" if sample_rate_hz < 48_000 else "23000",
            ]
        )
        profiles = campaign.profiles_from_args(args)
        return campaign.build_plan(
            profiles,
            G.BurstSchedule(frames=frames),
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
            android_source=android_source,
            mac_output_volume=mac_output_volume,
            geometry_label=geometry_label,
            authorization_note=authorization_note,
            execution_binding=execution_binding,
            drive_envelope=campaign.PIXEL_VOLUME_50_EVIDENCE_DRIVE_ENVELOPE,
        )

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

    def test_drive_envelope_flags_are_mutually_exclusive(self) -> None:
        with self.assertRaisesRegex(SystemExit, "mutually exclusive"):
            campaign.main(
                [
                    "--historical-drive-calibration-envelope",
                    "--pixel-volume-50-evidence-envelope",
                ]
            )

    def test_pixel_volume_50_envelope_accepts_one_and_five_long_frames(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binding_arguments, _ = physical_binding_fixture(root)
            for frames in (1, 5):
                with self.subTest(frames=frames):
                    manifest_path = root / f"plan-{frames}.json"
                    arguments = [
                        "--pairs",
                        "1",
                        "--pixel-volume-50-evidence-envelope",
                        "--symbols",
                        "65",
                        "--amplitude",
                        "0.18",
                        "--mac-output-volume",
                        "50",
                        "--authorization-note",
                        "overnight Pixel bench authorization",
                        "--geometry-label",
                        campaign.PIXEL_VOLUME_50_GEOMETRY_LABEL,
                        "--manifest",
                        str(manifest_path),
                        *binding_arguments,
                    ]
                    if frames == 1:
                        arguments.append("--smoke-one-frame")
                    with mock.patch.object(campaign, "assert_dry_run_import_boundary"):
                        result = campaign.main(arguments)

                    self.assertEqual(result, 0)
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    plan = manifest["plan"]
                    envelope = plan["capture_policy"]["drive_envelope"]
                    self.assertEqual(
                        envelope["envelope_id"],
                        "pixel7a-faceup-vol50-wideband-evidence-v1",
                    )
                    self.assertEqual(envelope["maximum_output_volume_percent"], 50)
                    self.assertEqual(envelope["maximum_waveform_peak"], 0.18)
                    self.assertIn("not an SPL measurement", envelope["scope"])
                    self.assertIn("acoustic-exposure rating", envelope["scope"])
                    self.assertTrue(plan["execution_binding"])
                    self.assertEqual(plan["schedule"]["frames"], frames)
                    self.assertEqual(
                        plan["measurement_contract"]["headline_eligible"],
                        frames == 5,
                    )
                    self.assertGreater(
                        max(
                            profile["geometry"]["frame_seconds"]
                            for profile in plan["profiles"]
                        ),
                        4.0,
                    )

    def test_pixel_volume_50_envelope_requires_binding_in_dry_plan(self) -> None:
        with self.assertRaisesRegex(SystemExit, "requires --target-provenance"):
            campaign.main(
                [
                    "--pixel-volume-50-evidence-envelope",
                    "--amplitude",
                    "0.18",
                    "--mac-output-volume",
                    "50",
                    "--authorization-note",
                    "test authorization",
                ]
            )

    def test_pixel_volume_50_envelope_requires_authorization_in_dry_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            binding_arguments, _ = physical_binding_fixture(Path(directory))
            with self.assertRaisesRegex(SystemExit, "nonempty --authorization-note"):
                campaign.main(
                    [
                        "--pixel-volume-50-evidence-envelope",
                        "--amplitude",
                        "0.18",
                        "--mac-output-volume",
                        "50",
                        *binding_arguments,
                    ]
                )

    def test_pixel_volume_50_envelope_requires_binding_and_authorization_in_build_plan(
        self,
    ) -> None:
        with self.assertRaisesRegex(ValueError, "authorization note"):
            self._build_evidence_plan(
                execution_binding=None,
                authorization_note=None,
            )
        with self.assertRaisesRegex(ValueError, "complete hashed execution binding"):
            self._build_evidence_plan(
                execution_binding=None,
                authorization_note="test authorization",
            )

    def test_pixel_volume_50_envelope_rejects_incomplete_binding_in_build_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, binding = physical_binding_fixture(Path(directory))
            incomplete = dict(binding)
            del incomplete["calibration_artifact_sha256"]
            with self.assertRaisesRegex(ValueError, "calibration_artifact_sha256"):
                self._build_evidence_plan(
                    execution_binding=incomplete,
                    authorization_note="test authorization",
                )

    def test_pixel_volume_50_envelope_requires_explicit_volume_in_build_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, binding = physical_binding_fixture(Path(directory))
            with self.assertRaisesRegex(ValueError, "explicit output volume"):
                self._build_evidence_plan(
                    execution_binding=binding,
                    authorization_note="test authorization",
                    mac_output_volume=None,
                )

    def test_pixel_volume_50_envelope_requires_explicit_volume_in_dry_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            binding_arguments, _ = physical_binding_fixture(Path(directory))
            with self.assertRaisesRegex(SystemExit, "explicit --mac-output-volume"):
                campaign.main(
                    [
                        "--pixel-volume-50-evidence-envelope",
                        "--amplitude",
                        "0.18",
                        "--authorization-note",
                        "test authorization",
                        "--geometry-label",
                        campaign.PIXEL_VOLUME_50_GEOMETRY_LABEL,
                        *binding_arguments,
                    ]
                )

    def test_pixel_volume_50_envelope_rejects_wrong_geometry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, binding = physical_binding_fixture(Path(directory))
            with self.assertRaisesRegex(ValueError, "qualified geometry label"):
                self._build_evidence_plan(
                    execution_binding=binding,
                    authorization_note="test authorization",
                    geometry_label="Pixel moved to a different pose",
                )

    def test_pixel_volume_50_envelope_rejects_wrong_source_and_rate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, binding = physical_binding_fixture(Path(directory))
            for arguments, message in (
                ({"android_source": "camcorder"}, "UNPROCESSED"),
                ({"sample_rate_hz": 44_100}, "48 kHz"),
            ):
                with self.subTest(arguments=arguments):
                    with self.assertRaisesRegex(ValueError, message):
                        self._build_evidence_plan(
                            execution_binding=binding,
                            authorization_note="test authorization",
                            **arguments,
                        )

    def test_pixel_volume_50_envelope_rejects_unqualified_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            physical_binding_fixture(root)
            provenance = json.loads((root / "target.json").read_bytes())
            provenance["android_target"]["model"] = "Pixel 8"
            (root / "target.json").write_text(json.dumps(provenance), encoding="utf-8")
            binding = load_fixture_binding(root)
            with self.assertRaisesRegex(ValueError, "qualified Pixel 7a target"):
                self._build_evidence_plan(
                    execution_binding=binding,
                    authorization_note="test authorization",
                )

    def test_pixel_volume_50_envelope_rejects_unqualified_route(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            physical_binding_fixture(root)
            route = json.loads((root / "route.json").read_bytes())
            route["actual_source_id"] = 1
            (root / "route.json").write_text(json.dumps(route), encoding="utf-8")
            binding = load_fixture_binding(root)
            with self.assertRaisesRegex(ValueError, "qualified stereo route"):
                self._build_evidence_plan(
                    execution_binding=binding,
                    authorization_note="test authorization",
                )

    def test_pixel_volume_50_envelope_rejects_unqualified_calibration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            physical_binding_fixture(root)
            qualification = json.loads((root / "calibration.json").read_bytes())
            qualification["qualified_date"] = "changed"
            (root / "calibration.json").write_text(
                json.dumps(qualification), encoding="utf-8"
            )
            binding = load_fixture_binding(root)
            with self.assertRaisesRegex(ValueError, "tracked qualification artifact"):
                self._build_evidence_plan(
                    execution_binding=binding,
                    authorization_note="test authorization",
                )

    def test_pixel_volume_50_envelope_rejects_unqualified_profile_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            physical_binding_fixture(root)
            binding = load_fixture_binding(root, "wrong-profile")
            with self.assertRaisesRegex(ValueError, "qualified profile key"):
                self._build_evidence_plan(
                    execution_binding=binding,
                    authorization_note="test authorization",
                )

    def test_pixel_volume_50_envelope_rejects_post_load_file_mutation(self) -> None:
        for filename, mutation, message in (
            ("target.json", {"android_target": {}}, "target provenance changed"),
            ("route.json", {"schema": campaign.ROUTE_SIGNATURE_SCHEMA}, "route signature changed"),
            ("calibration.json", {"changed": True}, "calibration artifact changed"),
        ):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                _, binding = physical_binding_fixture(root)
                (root / filename).write_text(json.dumps(mutation), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, message):
                    self._build_evidence_plan(
                        execution_binding=binding,
                        authorization_note="test authorization",
                    )

    def test_pixel_volume_50_envelope_rejects_over_cap_dry_plans(self) -> None:
        with self.assertRaisesRegex(SystemExit, "amplitude exceeds drive-envelope cap"):
            campaign.main(
                [
                    "--pixel-volume-50-evidence-envelope",
                    "--amplitude",
                    "0.180001",
                    "--mac-output-volume",
                    "50",
                ]
            )
        with self.assertRaisesRegex(SystemExit, r"--mac-output-volume must be in"):
            campaign.main(
                [
                    "--pixel-volume-50-evidence-envelope",
                    "--amplitude",
                    "0.18",
                    "--mac-output-volume",
                    "51",
                ]
            )

    def test_pixel_volume_50_envelope_rejects_over_cap_build_plans(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, binding = physical_binding_fixture(Path(directory))
            for attribute, value, message in (
                ("amplitude", 0.180001, "waveform-peak cap"),
                ("mac_output_volume", 51, "output-volume cap"),
            ):
                with self.subTest(attribute=attribute):
                    arguments = {
                        "execution_binding": binding,
                        "authorization_note": "test authorization",
                        attribute: value,
                    }
                    with self.assertRaisesRegex(ValueError, message):
                        self._build_evidence_plan(**arguments)


if __name__ == "__main__":
    unittest.main()
