#!/usr/bin/env python3
"""Deterministic tests for the offline-only Spike 8d estimator and planner."""

from __future__ import annotations

import ast
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

import effective_sinr_loading as spike


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class EffectiveSinrLoadingTests(unittest.TestCase):
    def test_gmi_is_bounded_monotonic_and_approaches_modulation_order(self) -> None:
        snr = np.asarray([-20.0, -5.0, 5.0, 15.0, 30.0])
        for bits in (2, 4):
            gmi = spike.bicm_gmi_awgn(snr, bits)
            self.assertTrue(np.all(gmi >= 0.0))
            self.assertTrue(np.all(gmi <= bits))
            self.assertTrue(np.all(np.diff(gmi) >= 0.0))
            self.assertLess(gmi[0], 0.1)
            self.assertGreater(gmi[-1], bits - 0.05)

    def test_frozen_raw_psd_boundaries(self) -> None:
        allocation = spike.raw_psd_allocation([5.999, 6.0, 12.999, 13.0])
        np.testing.assert_array_equal(allocation, [0, 2, 2, 4])

    def test_gmi_policy_is_no_more_aggressive_with_conservative_offset(self) -> None:
        snr = np.arange(-10.0, 31.0)
        uncalibrated = spike.pilot_gmi_allocation(snr, calibration_offset_bits=0.0)
        conservative = spike.pilot_gmi_allocation(snr, calibration_offset_bits=-0.50)
        self.assertTrue(np.all(conservative <= uncalibrated))
        self.assertTrue(np.any(conservative < uncalibrated))

    def test_known_probe_gmi_uses_heldout_symbols_and_external_variance(self) -> None:
        constellation, _ = spike._qam_constellation(4)
        generator = np.random.default_rng(20260718)
        indices = generator.integers(0, len(constellation), size=(512, 2))
        high_snr = constellation[indices]
        observed = spike.bicm_gmi_from_known_symbols(
            indices,
            high_snr,
            np.asarray([1e-4, 1e-4]),
            4,
        )
        self.assertTrue(np.all(observed > 3.95))
        with self.assertRaisesRegex(ValueError, "noise variance"):
            spike.bicm_gmi_from_known_symbols(indices, high_snr, 0.0, 4)

    def test_maximum_sinr_matches_independent_equal_noise_branches(self) -> None:
        channel = np.asarray([[1.0 + 0.0j, 0.5 + 0.0j]])
        covariance = np.asarray([np.eye(2, dtype=np.complex128)])
        observed = spike.maximum_sinr(
            channel,
            covariance,
            regularization_fraction=0.0,
        )
        self.assertAlmostEqual(float(observed[0]), 1.25, places=12)
        indefinite = np.asarray([[[1.0, 2.0], [2.0, 1.0]]], dtype=np.complex128)
        with self.assertRaisesRegex(ValueError, "positive semidefinite"):
            spike.maximum_sinr(channel, indefinite)

    def test_rank5_scalar_estimator_does_not_relabel_pilot_snr_as_raw_psd(self) -> None:
        result = spike.estimate_scalar_sounder({"snr_db": [5.0, 10.0, 15.0]})
        self.assertEqual(result["P1-raw-psd"]["status"], "unavailable")
        self.assertIn("allocation_counts", result["P2-pilot-gmi"])

    def test_estimator_rejects_same_frame_outcome_fields(self) -> None:
        with self.assertRaisesRegex(ValueError, "same-frame outcome fields"):
            spike.estimate_scalar_sounder(
                {
                    "snr_db": [10.0],
                    "block_valid": [True],
                }
            )

    def test_module_has_no_hardware_audio_or_adb_imports(self) -> None:
        source = Path(spike.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported_roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".")[0])
        self.assertTrue(
            imported_roots.isdisjoint(
                {
                    "adb",
                    "harness",
                    "pyaudio",
                    "sounddevice",
                    "subprocess",
                    "tone_check_hardware",
                }
            )
        )

    def test_audit_stops_when_only_one_waveform_and_payload_exist(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            corpus = root / "rank5"
            cells = []
            for index, cell_id in enumerate(spike.RANK5_CELL_IDS):
                split = "training" if index < 3 else "pseudo-holdout-outcome-exposed"
                cells.append({"id": cell_id, "split": split})
                cell = corpus / cell_id
                acquisition = {
                    "program": {
                        "evm_probe": {
                            "wave_sha256": "one-wave",
                            "payload_sha256": "one-payload",
                            "modulation": "16-QAM",
                            "fec": "convolutional-r1/2",
                            "nfft": 2048,
                            "cp": 768,
                            "symbols": 64,
                        }
                    }
                }
                analysis = {
                    "paths": [
                        {
                            "speaker": 0,
                            "receivers": [
                                {
                                    "receiver": 0,
                                    "sounder": {"snr_db": [0.0, 10.0, 20.0]},
                                }
                            ],
                        }
                    ]
                }
                _write_json(cell / "acquisition.json", acquisition)
                _write_json(cell / "analysis.json", analysis)
            _write_json(corpus / "preregistration.json", {})
            _write_json(corpus / "waveform-manifest.json", {})
            _write_json(corpus / "P0-passive" / "passive-analysis.json", {})
            preregistration = root / "preregistration.json"
            _write_json(preregistration, {"corpus": {"cells": cells}})

            result = spike.audit_rank5_corpus(preregistration, corpus)

            self.assertFalse(result["counterfactual_identifiable"])
            self.assertFalse(result["ota_permitted"])
            quantitative = result["quantitative_result"]
            self.assertEqual(quantitative["unique_physically_transmitted_profile_count"], 1)
            self.assertEqual(quantitative["unique_payload_hash_count"], 1)
            self.assertIsNone(quantitative["p10_net_goodput_delta_percent"])

    def test_acquisition_plan_is_unique_balanced_and_decode_barriered(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            preregistration = root / "preregistration.json"
            rank5 = root / "rank5-preregistration.json"
            _write_json(
                preregistration,
                {
                    "policies": [{"id": policy} for policy in spike.POLICY_IDS],
                    "allocation_signalling": {},
                    "gates": {
                        "offline_replay_promotion_to_ota_screen": "screen",
                        "final_promotion": "final",
                    },
                },
            )
            _write_json(rank5, {"code_and_config": {"repository_head": "fixture"}})

            manifest = spike.build_acquisition_manifest(preregistration, rank5)
            validation = spike.validate_acquisition_manifest(manifest)

            self.assertTrue(validation["valid"])
            self.assertEqual(validation["jobs"], 32)
            self.assertFalse(validation["ready"])
            self.assertFalse(validation["ota_permitted"])


if __name__ == "__main__":
    unittest.main()
