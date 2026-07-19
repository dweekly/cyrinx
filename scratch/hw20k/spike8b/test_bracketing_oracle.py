from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest

import numpy as np

import bracketing_oracle as oracle
import modem


class BracketingOracleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary_directory = tempfile.TemporaryDirectory()
        cls.library = oracle.build_viterbi(
            Path(cls.temporary_directory.name) / "libspike8b_viterbi.dylib"
        )
        cls.viterbi = oracle.FastViterbi(cls.library)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary_directory.cleanup()

    def test_fixed_geometry(self) -> None:
        config = oracle.make_config()
        self.assertEqual(config.n_blocks, 215)
        self.assertEqual(config.payload_bytes, 55_040)
        self.assertEqual(config.frame_samples, 284_864)

    def test_fast_viterbi_matches_python_reference(self) -> None:
        rng = np.random.default_rng(20260718)
        information_bits = 237
        steps = information_bits + 6
        llr0 = rng.normal(size=steps)
        llr1 = rng.normal(size=steps)
        reference = modem.viterbi_decode(llr0, llr1, information_bits)
        observed = self.viterbi.decode(llr0, llr1, information_bits)
        np.testing.assert_array_equal(observed, reference)

    def test_channel_interpolants_preserve_endpoints(self) -> None:
        config = oracle.make_config()
        rng = np.random.default_rng(71)
        start = rng.normal(size=(2, len(config.used))) + 1j * rng.normal(
            size=(2, len(config.used))
        )
        terminal = start * (0.9 + 0.1j)
        for method in ("complex-linear", "logmag-unwrapped-phase"):
            series, _ = oracle._channel_series(config, start, terminal, method, "full")
            np.testing.assert_allclose(series[0], start, rtol=1e-12, atol=1e-12)
            np.testing.assert_allclose(series[-1], terminal, rtol=1e-12, atol=1e-12)
        sparse_late, _ = oracle._channel_series(
            config, start, terminal, "significant-tap", "late-half"
        )
        np.testing.assert_array_equal(
            sparse_late[: config.n_sym // 2],
            np.broadcast_to(start[None, :, :], (config.n_sym // 2, *start.shape)),
        )

    def test_expected_payload_does_not_change_frozen_front_end(self) -> None:
        config = oracle.make_config()
        payload = bytes((index * 17 + 3) & 0xFF for index in range(config.payload_bytes))
        taps: dict[str, np.ndarray] = {}
        wave = modem.modulate_frame(config, payload, taps=taps)
        detected_sample = 900
        capture = np.zeros((detected_sample + len(wave) + 1000, 2), dtype=np.float32)
        capture[detected_sample : detected_sample + len(wave), 0] = wave
        capture[detected_sample : detected_sample + len(wave), 1] = wave * 0.7
        front = oracle._front_end(config, capture, detected_sample)
        fingerprint_before = hashlib.sha256(
            front["corrections"].tobytes() + front["H_start"].tobytes()
        ).hexdigest()

        changed_payload = bytes(value ^ 0x5A for value in payload)
        changed_taps: dict[str, np.ndarray] = {}
        modem.modulate_frame(config, changed_payload, taps=changed_taps)
        fingerprint_after = hashlib.sha256(
            front["corrections"].tobytes() + front["H_start"].tobytes()
        ).hexdigest()
        self.assertEqual(fingerprint_before, fingerprint_after)
        self.assertFalse(np.array_equal(taps["data_freq"], changed_taps["data_freq"]))

    def test_accounting_distinguishes_scheduled_gross_and_acquisition(self) -> None:
        config = oracle.make_config()
        run = {"scheduled_span_samples": 1_424_320, "trailing_pad_samples": 16_000}
        accounting = oracle._accounting(config, run, [1_665_919])
        self.assertEqual(accounting["scheduled"]["samples"], 1_424_320)
        self.assertEqual(accounting["gross"]["samples"], 1_440_320)
        self.assertEqual(accounting["acquisition"]["capture_samples_by_run"], [1_665_919])
        self.assertFalse(accounting["session"]["available"])
        self.assertLess(
            accounting["appended_terminal_training"][1]["error_free_ceiling_bps"],
            accounting["appended_terminal_training"][0]["error_free_ceiling_bps"],
        )


if __name__ == "__main__":
    unittest.main()
