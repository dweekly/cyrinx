#!/usr/bin/env python3
import ctypes
import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import clib


class AutomaticDiversityV1Tests(unittest.TestCase):
    def setUp(self):
        self.config = clib.make_cfg(
            bits_per_bin=2,
            rate="1/2",
            n_sym=8,
            amp=0.5,
        )
        geometry = clib.geometry(self.config)
        self.payload = bytes((index * 31 + 7) & 0xFF for index in range(geometry.payload_bytes))
        wave = clib.encode(self.config, self.payload)
        self.capture = np.concatenate(
            (
                np.zeros(3000, dtype=np.float32),
                wave,
                np.zeros(2000, dtype=np.float32),
            )
        )

    @staticmethod
    def valid_raw_diagnostics():
        diagnostics = clib.DiversityDiagnostics()
        diagnostics.struct_size = ctypes.sizeof(clib.DiversityDiagnostics)
        diagnostics.abi_version = clib.AUTO_V1_DIAGNOSTICS_ABI_VERSION
        diagnostics.policy_version = clib.AUTO_V1_POLICY_VERSION
        diagnostics.selected_receiver = 0
        diagnostics.scores_valid = 1
        diagnostics.selection_reason = 2
        diagnostics.validation_observations = 8
        diagnostics.primary_holdout_pilot_rms = 0.1
        diagnostics.mrc_holdout_pilot_rms = 0.1
        diagnostics.observed_mrc_to_primary_pilot_rms_ratio = 1.0
        diagnostics.maximum_mrc_to_primary_pilot_rms_ratio = (
            clib.AUTO_V1_MAX_MRC_TO_PRIMARY_PILOT_RMS_RATIO
        )
        return diagnostics

    def test_identical_channels_select_primary(self):
        mono = clib.decode(self.config, self.capture)
        result = clib.decode2_auto_v1(self.config, self.capture, self.capture)

        self.assertEqual(result["payload"], self.payload)
        self.assertTrue(all(result["block_valid"]))
        for field in ("payload", "block_valid", "blocks_ok", "blocks_total", "evm"):
            self.assertEqual(result[field], mono[field], field)
        diagnostics = result["automatic_diversity"]
        self.assertEqual(diagnostics["selected_receiver"], "mic0")
        self.assertEqual(diagnostics["selection_reason"], "primary_margin_not_met")
        self.assertTrue(diagnostics["scores_valid"])
        self.assertEqual(diagnostics["abi_version"], clib.AUTO_V1_DIAGNOSTICS_ABI_VERSION)
        self.assertEqual(diagnostics["policy_version"], clib.AUTO_V1_POLICY_VERSION)
        self.assertFalse(diagnostics["payload_or_crc_used_for_selection"])
        self.assertFalse(diagnostics["data_bins_used_for_selection"])
        self.assertEqual(
            diagnostics["maximum_mrc_to_primary_pilot_rms_ratio"],
            clib.AUTO_V1_MAX_MRC_TO_PRIMARY_PILOT_RMS_RATIO,
        )

    def test_missing_secondary_is_json_safe(self):
        result = clib.decode2_auto_v1(self.config, self.capture, None)

        self.assertEqual(result["payload"], self.payload)
        diagnostics = result["automatic_diversity"]
        self.assertEqual(diagnostics["selected_receiver"], "mic0")
        self.assertEqual(diagnostics["selection_reason"], "second_unavailable")
        self.assertIsNone(diagnostics["primary_holdout_pilot_rms"])
        self.assertIsNone(diagnostics["mrc_holdout_pilot_rms"])
        self.assertIsNone(diagnostics["observed_mrc_to_primary_pilot_rms_ratio"])
        json.dumps(result, allow_nan=False, default=lambda value: "bytes")

    def test_binding_rejects_unknown_diagnostics_layout(self):
        diagnostics = self.valid_raw_diagnostics()
        diagnostics.struct_size = ctypes.sizeof(clib.DiversityDiagnostics) - 1

        with self.assertRaisesRegex(RuntimeError, "diagnostics size"):
            clib._diversity_result(diagnostics)

    def test_binding_rejects_changed_maximum_ratio(self):
        diagnostics = self.valid_raw_diagnostics()
        diagnostics.maximum_mrc_to_primary_pilot_rms_ratio = np.nextafter(0.95, 1.0)

        with self.assertRaisesRegex(RuntimeError, "maximum MRC pilot RMS ratio"):
            clib._diversity_result(diagnostics)

    def test_explicit_codec_does_not_require_default_library(self):
        missing_default = str(Path(__file__).resolve().with_name("missing-default.dylib"))
        previous = os.environ.get(clib.CODEC_PATH_ENV)
        os.environ[clib.CODEC_PATH_ENV] = missing_default
        try:
            spec = importlib.util.spec_from_file_location(
                "isolated_lazy_clib", Path(clib.__file__).resolve()
            )
            self.assertIsNotNone(spec)
            self.assertIsNotNone(spec.loader)
            isolated = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(isolated)
        finally:
            if previous is None:
                os.environ.pop(clib.CODEC_PATH_ENV, None)
            else:
                os.environ[clib.CODEC_PATH_ENV] = previous

        self.assertIsNone(isolated._DEFAULT_CODEC)
        explicit = isolated.BulkCodec(clib.loaded_library_path())
        self.assertEqual(explicit.library_path, clib.loaded_library_path())


if __name__ == "__main__":
    unittest.main()
