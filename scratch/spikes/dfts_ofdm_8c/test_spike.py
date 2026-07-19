#!/usr/bin/env python3
"""Deterministic tests for the offline localized DFT-spread OFDM spike."""

from __future__ import annotations

import hashlib
import json
import math
import sys
import unittest
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import spike  # noqa: E402


class Spike8CTests(unittest.TestCase):
    def setUp(self) -> None:
        self.document = spike.load_preregistration()
        self.geometry = spike.geometry(self.document)

    def test_frozen_geometry(self) -> None:
        self.assertEqual(len(self.geometry.used), 935)
        self.assertEqual(len(self.geometry.pilot_positions), 59)
        self.assertEqual(len(self.geometry.data_positions), 876)
        self.assertEqual(self.geometry.bit_capacity, 336384)
        self.assertEqual(math.gcd(65537, self.geometry.bit_capacity), 1)

    def test_vectorized_encoder_matches_oracle(self) -> None:
        rng = np.random.default_rng(8)
        for length in (0, 1, 17, 2080):
            bits = rng.integers(0, 2, length, dtype=np.uint8)
            np.testing.assert_array_equal(
                spike.vectorized_conv_encode(bits),
                spike.M.conv_encode(bits),
            )

    def test_qam_lookup_matches_oracle(self) -> None:
        lookup = spike.qam64_lookup()
        labels = np.arange(64, dtype=np.uint8)
        bits = ((labels[:, None] >> np.arange(5, -1, -1)) & 1).astype(np.uint8)
        np.testing.assert_allclose(lookup, spike.M.qam_map(bits, 6), atol=0.0, rtol=0.0)

    def test_unitary_localized_mapping_round_trip(self) -> None:
        rng = np.random.default_rng(9)
        logical = rng.normal(size=(3, 935)) + 1j * rng.normal(size=(3, 935))
        physical = np.fft.fft(logical, axis=1, norm="ortho")
        recovered = np.fft.ifft(physical, axis=1, norm="ortho")
        np.testing.assert_allclose(recovered, logical, atol=2e-14, rtol=2e-14)

    def test_probe_placement_is_reversible_and_collision_free(self) -> None:
        rng = np.random.default_rng(10)
        _, punctured = spike.coded_probe(rng)
        labels = rng.integers(
            0,
            64,
            size=(self.geometry.data_symbols, len(self.geometry.data_positions)),
            dtype=np.uint8,
        )
        positions, label_indices, bit_indices = spike.insert_probe(
            labels, punctured, 123, self.geometry.bit_capacity
        )
        self.assertEqual(len(np.unique(positions)), len(positions))
        recovered = np.empty(len(punctured), dtype=np.uint8)
        flat = labels.reshape(-1)
        for index, (label_index, bit_index) in enumerate(zip(label_indices, bit_indices)):
            recovered[index] = (flat[label_index] >> (5 - bit_index)) & 1
        np.testing.assert_array_equal(recovered, punctured)

    def test_batch_viterbi_recovers_noiseless_probes(self) -> None:
        rng = np.random.default_rng(11)
        streams = []
        llrs = []
        for _ in range(3):
            stream, punctured = spike.coded_probe(rng)
            streams.append(stream)
            llrs.append(np.where(punctured == 0, 20.0, -20.0).astype(np.float32))
        recovered = spike.batch_viterbi_crc(np.stack(llrs), streams)
        np.testing.assert_array_equal(recovered, np.ones(3, dtype=bool))

    def test_batch_viterbi_matches_scalar_oracle_under_noise(self) -> None:
        rng = np.random.default_rng(12)
        streams = []
        llrs = []
        scalar = []
        pattern = np.resize(np.asarray(spike.M.PUNCTURE["2/3"][0], dtype=bool), 4172)
        for noise_rms in (1.0, 2.5, 4.0):
            stream, punctured = spike.coded_probe(rng)
            punctured_llr = np.where(punctured == 0, 3.0, -3.0) + rng.normal(
                0.0, noise_rms, len(punctured)
            )
            full = np.zeros(4172)
            full[pattern] = punctured_llr
            bits = spike.M.viterbi_decode(full[0::2], full[1::2], 2080)
            scalar.append(np.packbits(bits).tobytes() == stream)
            streams.append(stream)
            llrs.append(punctured_llr.astype(np.float32))
        batched = spike.batch_viterbi_crc(np.stack(llrs), streams)
        np.testing.assert_array_equal(batched, np.asarray(scalar, dtype=bool))

    def test_seed_is_stable_and_cell_separated(self) -> None:
        seed = self.document["randomization"]["master_seed"]
        first = spike.frame_seed(seed, "awgn-snr18", 0)
        self.assertEqual(first, spike.frame_seed(seed, "awgn-snr18", 0))
        self.assertNotEqual(first, spike.frame_seed(seed, "awgn-snr18", 1))
        self.assertNotEqual(first, spike.frame_seed(seed, "awgn-snr26", 0))

    def test_committed_result_is_internally_consistent(self) -> None:
        result_path = HERE / "results" / "summary.json"
        self.assertTrue(result_path.exists())
        result = json.loads(result_path.read_text())
        self.assertEqual(result["schema"], spike.SCHEMA)
        prereg_hash = hashlib.sha256(spike.PREREG_PATH.read_bytes()).hexdigest()
        implementation_hash = hashlib.sha256((HERE / "spike.py").read_bytes()).hexdigest()
        self.assertEqual(result["preregistration_sha256"], prereg_hash)
        self.assertEqual(result["implementation_sha256"], implementation_hash)
        self.assertEqual(result["frames_completed"], 10000)
        self.assertEqual(sum(cell["frames"] for cell in result["cells"]), 10000)
        conjunction = all(gate["pass"] for gate in result["gates"].values())
        self.assertEqual(result["ota_permitted_by_frozen_gate"], conjunction)
        self.assertFalse(result["ota_permitted_by_frozen_gate"])


if __name__ == "__main__":
    unittest.main()
