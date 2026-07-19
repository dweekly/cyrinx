#!/usr/bin/env python3
"""Deterministic tests for the offline Rank 11a matrix analyzer."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
import unittest

import numpy as np

from audit_retained_corpus import build_audit
from make_synthetic_fixture import build_fixture, seal_allocation
from matrix_analysis import _capacity_metrics, analyze_dataset, bitwise_gmi


HERE = Path(__file__).resolve().parent


class MatrixAnalysisTests(unittest.TestCase):
    @staticmethod
    def _eraseModeGmi(fixture: dict, mode: int, bins: set[int]) -> None:
        for observation in fixture["gmi_observations"]:
            if observation["mode"] == mode and observation["bin"] in bins:
                observation["llrs"] = np.zeros_like(observation["llrs"]).tolist()

    def testKnownDiagonalLogDetCapacity(self) -> None:
        channel = np.eye(2, dtype=np.complex128)[None, :, :]
        covariance = np.eye(2, dtype=np.complex128)[None, :, :]
        result = _capacity_metrics(channel, covariance, per_bin_total_symbol_power=2.0)
        self.assertAlmostEqual(
            result["best_speaker_simo_logdet_bits_per_ofdm_use"],
            math.log2(3.0),
            places=12,
        )
        self.assertAlmostEqual(
            result["equal_power_mimo_logdet_bits_per_ofdm_use"],
            2.0,
            places=12,
        )
        expected_rank = 2.0
        self.assertAlmostEqual(result["effective_rank"]["median"], expected_rank, places=12)

    def testBitwiseGmiUsesKnownBitsAndLlrSign(self) -> None:
        bits = [[0, 1], [1, 0], [0, 0], [1, 1]]
        correct_llrs = [[8.0, -8.0], [-8.0, 8.0], [8.0, 8.0], [-8.0, -8.0]]
        wrong_llrs = [[-8.0, 8.0], [8.0, -8.0], [-8.0, -8.0], [8.0, 8.0]]
        self.assertGreater(bitwise_gmi(bits, correct_llrs), 1.99)
        self.assertLess(bitwise_gmi(bits, wrong_llrs), 0.0)

    def testSyntheticFixtureValidatesAllArithmeticGates(self) -> None:
        result = analyze_dataset(build_fixture())
        self.assertEqual(result["decision"], "SYNTHETIC_VALIDATION_ONLY")
        self.assertFalse(result["promotion_gate_evaluable"])
        self.assertTrue(result["all_geometry_gates_pass"])
        self.assertGreater(result["capacity"]["effective_rank"]["minimum"], 1.5)
        self.assertGreater(result["capacity"]["logdet_ratio"], 1.35)
        self.assertGreater(result["session_accounting"]["transfers"]["1MiB"]["ratio"], 1.35)
        self.assertTrue(result["gates"]["fixed_sum_digital_sample_power"])

    def testMissingCommonPhaseReferenceStopsBeforeMatrixArithmetic(self) -> None:
        fixture = build_fixture()
        fixture["provenance"]["common_phase_reference"] = False
        fixture["provenance"]["acquisition_mode"] = "separate_player_launches"
        result = analyze_dataset(fixture)
        self.assertEqual(result["decision"], "STOP_NOT_IDENTIFIABLE")
        self.assertFalse(result["promotion_gate_evaluable"])
        self.assertIn(
            "provenance.common_phase_reference is not true",
            result["identifiability_reasons"],
        )
        self.assertNotIn("capacity", result)

    def testUnstableHeldOutPhaseFailsCoherenceAndPhaseGate(self) -> None:
        fixture = copy.deepcopy(build_fixture())
        channels = np.asarray(fixture["channel_repeats"], dtype=np.float64)
        complex_channels = channels[..., 0] + 1j * channels[..., 1]
        rng = np.random.default_rng(9911)
        complex_channels[2:] *= np.exp(
            1j * rng.uniform(-math.pi, math.pi, size=complex_channels[2:].shape)
        )
        fixture["channel_repeats"] = np.stack(
            (complex_channels.real, complex_channels.imag), axis=-1
        ).tolist()
        result = analyze_dataset(fixture)
        self.assertFalse(result["gates"]["repeat_coherence_at_least_0_95"])
        self.assertFalse(result["gates"]["residual_phase_sd_at_most_15_degrees"])
        self.assertFalse(result["all_geometry_gates_pass"])

    def testWeakModeTwoLlrFailsGmiGate(self) -> None:
        fixture = build_fixture()
        self._eraseModeGmi(fixture, mode=1, bins=set(fixture["active_bins"]))
        result = analyze_dataset(fixture)
        self.assertEqual(result["gmi"]["observed_mode2_qpsk_rate_half_bin_fraction"], 0.0)
        self.assertFalse(result["gates"]["mode2_qpsk_rate_half_on_at_least_40_percent_bins"])
        ceilings = result["session_accounting"]["scheduled_ceiling_bps"]
        self.assertAlmostEqual(ceilings["two_mode_mimo"], ceilings["best_speaker_simo"])
        self.assertFalse(result["gates"]["one_mib_net_gain_at_least_1_35"])

    def testFortyPercentModeTwoCreditsOnlySupportedFrozenBins(self) -> None:
        fixture = build_fixture()
        bins = fixture["active_bins"]
        self._eraseModeGmi(fixture, mode=1, bins=set(bins[4:]))
        result = analyze_dataset(fixture)
        allocation = result["session_accounting"]["frozen_allocation"]
        self.assertEqual(allocation["usable_bin_count_by_mode"]["1"], 10)
        self.assertEqual(allocation["usable_bin_count_by_mode"]["2"], 4)
        self.assertEqual(allocation["usable_payload_fraction_by_mode"]["2"], 0.4)
        ceilings = result["session_accounting"]["scheduled_ceiling_bps"]
        self.assertAlmostEqual(ceilings["two_mode_mimo"] / ceilings["best_speaker_simo"], 1.4)
        self.assertLess(
            result["session_accounting"]["transfers"]["1MiB"]["ratio"],
            1.4,
        )

    def testPartialRankCannotFalsePassNetGainGate(self) -> None:
        fixture = build_fixture()
        bins = fixture["active_bins"]
        self._eraseModeGmi(fixture, mode=0, bins=set(bins[8:]))
        self._eraseModeGmi(fixture, mode=1, bins=set(bins[4:]))
        result = analyze_dataset(fixture)
        allocation = result["session_accounting"]["frozen_allocation"]
        self.assertEqual(allocation["usable_bin_count_by_mode"], {"1": 8, "2": 4})
        ceilings = result["session_accounting"]["scheduled_ceiling_bps"]
        self.assertAlmostEqual(ceilings["two_mode_mimo"] / ceilings["best_speaker_simo"], 1.2)
        self.assertTrue(result["gates"]["mode2_qpsk_rate_half_on_at_least_40_percent_bins"])
        self.assertFalse(result["gates"]["one_mib_net_gain_at_least_1_35"])
        self.assertFalse(result["all_geometry_gates_pass"])

    def testFrozenModeTwoMaskCannotExpandFromHeldOutGmi(self) -> None:
        fixture = build_fixture()
        bins = fixture["active_bins"]
        allocation = fixture["scheduling"]["frozen_allocation"]
        allocation["mode2"]["active_bins"] = bins[:4]
        seal_allocation(allocation)
        result = analyze_dataset(fixture)
        allocation = result["session_accounting"]["frozen_allocation"]
        self.assertEqual(allocation["allocated_bin_count_by_mode"]["2"], 4)
        self.assertEqual(allocation["usable_bin_count_by_mode"]["2"], 4)
        ceilings = result["session_accounting"]["scheduled_ceiling_bps"]
        self.assertAlmostEqual(ceilings["two_mode_mimo"] / ceilings["best_speaker_simo"], 1.4)

    def testPostFreezeAllocationMutationCannotProduceCandidateRate(self) -> None:
        fixture = build_fixture()
        fixture["scheduling"]["frozen_allocation"]["mode2"]["active_bins"].pop()
        result = analyze_dataset(fixture)
        self.assertFalse(result["session_accounting"]["available"])
        self.assertIn("SHA-256", result["session_accounting"]["reason"])
        self.assertFalse(result["gates"]["one_mib_net_gain_at_least_1_35"])

    def testMissingFrozenAllocationCannotProduceCandidateRate(self) -> None:
        fixture = build_fixture()
        del fixture["scheduling"]["frozen_allocation"]
        result = analyze_dataset(fixture)
        self.assertFalse(result["session_accounting"]["available"])
        self.assertIsNone(result["session_accounting"]["scheduled_ceiling_bps"]["two_mode_mimo"])
        self.assertFalse(result["gates"]["one_mib_net_gain_at_least_1_35"])

    def testExtraMimoPowerCannotProduceCandidateRate(self) -> None:
        fixture = build_fixture()
        policy = fixture["scheduling"]["frozen_allocation"]["power_policy"]
        policy["two_mode_bin_power_fractions"] = [1.0, 1.0]
        seal_allocation(fixture["scheduling"]["frozen_allocation"])
        result = analyze_dataset(fixture)
        self.assertFalse(result["session_accounting"]["available"])
        self.assertIn("fixed-total-power", result["session_accounting"]["reason"])
        self.assertFalse(result["gates"]["one_mib_net_gain_at_least_1_35"])

    def testFrozenRetainedCorpusFormallyStops(self) -> None:
        audit = build_audit(HERE.parents[1])
        self.assertEqual(audit["source_revision"], "61558c2")
        self.assertEqual(audit["tracked_recording_files"], [])
        self.assertEqual(audit["tracked_numeric_array_files"], [])
        self.assertEqual(audit["full_phase_coherent_2x2_matrix_count"], 0)
        self.assertEqual(audit["decision"], "STOP_NOT_IDENTIFIABLE")

    def testAcquisitionTimelineAndHoldoutAreExact(self) -> None:
        plan = json.loads((HERE / "next-acquisition-manifest.json").read_text())
        cursor = 0
        for segment in plan["waveform"]["timeline"]:
            self.assertEqual(segment["start_sample"], cursor)
            cursor += segment["sample_count"]
        self.assertEqual(cursor, plan["waveform"]["total_render_samples"])
        self.assertAlmostEqual(
            cursor / plan["waveform"]["sample_rate_hz"],
            plan["waveform"]["render_duration_s"],
            places=12,
        )

        matrix_segment = next(
            segment
            for segment in plan["waveform"]["timeline"]
            if segment["label"] == "orthogonal_matrix_probe"
        )
        code = matrix_segment["tx_code"]
        self.assertEqual(np.dot(code["tx0"], code["tx1"]), 0)

        repeat_ids: list[str] = []
        for cell in plan["geometry_cells_in_acquisition_order"]:
            calibration = set(cell["calibration_repeat_ids"])
            held_out = set(cell["held_out_repeat_ids"])
            self.assertTrue(calibration.isdisjoint(held_out))
            self.assertEqual(calibration | held_out, set(cell["repeat_ids"]))
            repeat_ids.extend(cell["repeat_ids"])
        self.assertEqual(len(repeat_ids), 15)
        self.assertEqual(len(set(repeat_ids)), 15)

        preregistration = (HERE / "preregistration.json").read_bytes()
        self.assertEqual(
            hashlib.sha256(preregistration).hexdigest(),
            plan["preregistration_sha256"],
        )
        amendment = (HERE / "analysis-amendment-001.json").read_bytes()
        self.assertEqual(
            hashlib.sha256(amendment).hexdigest(),
            plan["analysis_amendment_sha256"],
        )


if __name__ == "__main__":
    unittest.main()
