#!/usr/bin/env python3
"""Device-free regression tests for the self-calibration tone program."""

from __future__ import annotations

import builtins
from unittest import mock
import unittest

import numpy as np

import tone_check_hardware as tone_check


class ToneProgramTests(unittest.TestCase):
    def test_build_is_deterministic_and_describes_sequential_stereo_matrix(self) -> None:
        real_import = builtins.__import__

        def reject_hardware_imports(name, *args, **kwargs):
            if name.split(".", 1)[0] in {
                "calibration_campaign",
                "harness",
                "sounddevice",
            }:
                raise AssertionError(f"planning imported hardware dependency {name!r}")
            return real_import(name, *args, **kwargs)

        with (
            mock.patch("builtins.__import__", side_effect=reject_hardware_imports),
            mock.patch.object(
                tone_check.subprocess,
                "run",
                side_effect=AssertionError("planning invoked a subprocess"),
            ),
            mock.patch.object(
                tone_check.subprocess,
                "check_output",
                side_effect=AssertionError("planning invoked a subprocess"),
            ),
        ):
            first = tone_check.build_tone_program()
            second = tone_check.build_tone_program()
            plan = tone_check.build_plan(
                run_id="offline-tone-program-regression",
                geometry_label="synthetic geometry; no devices",
                authorization_note="offline regression; no playback",
                expected_android_serial="offline-serial",
                expected_android_model="offline-model",
                expected_android_build_fingerprint="offline/fingerprint",
                expected_android_apk_sha256="0" * 64,
                expected_route_signature={
                    "schema": tone_check.ROUTE_SIGNATURE_SCHEMA,
                    "fixture": "offline",
                },
                calibration_artifact_sha256="1" * 64,
                calibration_profile_key="offline-profile",
            )

        self.assertTrue(np.array_equal(first.mono, second.mono))
        self.assertEqual(first.manifest(), second.manifest())
        self.assertEqual(first.sample_rate_hz, 48_000)
        self.assertEqual(first.total_samples, 140_880)
        self.assertEqual(len(first.segments), 8)
        self.assertAlmostEqual(float(np.max(np.abs(first.mono))), first.digital_peak, places=6)
        self.assertEqual(
            [segment.label for segment in first.segments],
            [
                "single-997hz",
                "single-3001hz",
                "single-7001hz",
                "single-12001hz",
                "single-17003hz",
                "single-21001hz",
                "imd-997-1499hz",
                "imd-18001-19001hz",
            ],
        )
        cursor = first.tone_probe_sample
        for segment in first.segments:
            self.assertEqual(np.count_nonzero(first.mono[cursor : segment.start_sample]), 0)
            self.assertGreater(
                np.count_nonzero(first.mono[segment.start_sample : segment.end_sample]),
                0,
            )
            cursor = segment.end_sample
        tone_probe_end = first.end_sync_sample - tone_check._samples(
            tone_check.PRE_END_SYNC_GUARD_S
        )
        self.assertEqual(np.count_nonzero(first.mono[cursor:tone_probe_end]), 0)

        manifest = first.manifest()
        self.assertEqual(manifest["tone_guard_samples"], 1_680)
        self.assertEqual(manifest["tone_edge_ramp_samples"], 480)
        self.assertEqual(
            manifest["tone_component_peak_policy"],
            "single=digital_peak; imd-pair=half-digital-peak-per-component",
        )

        left = first.stereo_for_tx(0)
        right = first.stereo_for_tx(1)
        self.assertEqual(left.shape, (first.total_samples, 2))
        self.assertTrue(np.array_equal(left[:, 0], first.mono))
        self.assertTrue(np.array_equal(right[:, 1], first.mono))
        self.assertEqual(np.count_nonzero(left[:, 1]), 0)
        self.assertEqual(np.count_nonzero(right[:, 0]), 0)

        self.assertEqual(plan["program"], first.manifest())
        self.assertEqual(
            [transmitter["active_channel"] for transmitter in plan["transmitters"]],
            ["left", "right"],
        )
        self.assertEqual(plan["transmitters"][0]["depends_on"], None)
        self.assertEqual(
            plan["transmitters"][1]["depends_on"],
            "tx0-safety-gate-pass",
        )
        self.assertEqual(
            plan["transmitters"][0]["stereo_sha256"],
            tone_check.array_sha256(left),
        )
        self.assertEqual(
            plan["transmitters"][1]["stereo_sha256"],
            tone_check.array_sha256(right),
        )
        self.assertEqual(
            [transmitter["silent_channel_peak"] for transmitter in plan["transmitters"]],
            [0.0, 0.0],
        )

    def test_shared_clock_schedule_preserves_reference_affine_mapping(self) -> None:
        reference = tone_check.AlignmentEstimate(
            valid=True,
            capture_origin_sample=1234,
            start_marker_sample=25_234,
            end_marker_sample=112_358,
            sample_scale=1.00005,
            clock_error_ppm=50.0,
            start_score=0.4,
            end_score=0.5,
            peak_to_sidelobe_db=12.0,
            uncertainty_samples=3,
            reason=None,
        )
        weak = tone_check.AlignmentEstimate(
            valid=False,
            capture_origin_sample=-50,
            start_marker_sample=100,
            end_marker_sample=200,
            sample_scale=1.0,
            clock_error_ppm=0.0,
            start_score=0.03,
            end_score=0.02,
            peak_to_sidelobe_db=1.0,
            uncertainty_samples=7,
            reason="weak marker",
        )

        inherited = tone_check.shared_clock_schedule_alignment(reference, weak)

        self.assertTrue(inherited.valid)
        self.assertEqual(inherited.capture_origin_sample, reference.capture_origin_sample)
        self.assertEqual(inherited.sample_scale, reference.sample_scale)
        self.assertEqual(inherited.clock_error_ppm, reference.clock_error_ppm)
        self.assertEqual(inherited.start_score, weak.start_score)
        self.assertEqual(inherited.end_score, weak.end_score)
        self.assertEqual(inherited.uncertainty_samples, weak.uncertainty_samples)
        self.assertIn("shared interleaved capture clock", inherited.reason)


if __name__ == "__main__":
    unittest.main()
