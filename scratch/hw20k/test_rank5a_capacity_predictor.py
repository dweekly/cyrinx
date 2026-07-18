#!/usr/bin/env python3

import unittest

import numpy as np

import modem as M
import rank5a_capacity_predictor as P


class Rank5aCapacityPredictorTests(unittest.TestCase):
    def test_exact_probe_schedule(self):
        cfg = P.config()
        self.assertEqual(len(cfg.used), 935)
        self.assertEqual(len(cfg.pilot_idx), 117)
        self.assertEqual(len(cfg.data_bins), 818)
        self.assertEqual(cfg.bits_per_sym, 3_272)
        self.assertEqual(cfg.info_bits, 104_698)
        self.assertEqual(cfg.n_blocks, 50)
        self.assertEqual(cfg.payload_bytes, 12_800)
        self.assertEqual(cfg.frame_samples, 192_000)
        self.assertEqual(cfg.airtime_s, 4.0)
        self.assertEqual(cfg.payload_bytes * 8 / cfg.airtime_s, 25_600.0)

    def test_gmi_rewards_correct_llr_sign(self):
        bits = np.array([0, 1, 0, 1], dtype=np.uint8)
        correct = np.array([8.0, -8.0, 8.0, -8.0])
        wrong = -correct
        self.assertGreater(P.gmi_for_scale(bits, correct, 1.0), 0.99)
        self.assertLess(P.gmi_for_scale(bits, wrong, 1.0), 0.0)
        _, optimized_wrong, scale = P.optimized_gmi(bits, wrong)
        self.assertEqual(optimized_wrong, 0.0)
        self.assertLess(scale, 1e-6)

    def test_local_pilot_noise_replicates_edges_and_extends_last(self):
        pilots = np.ones(117)
        positions = np.array([1, 7, 929, 934])
        observed = P._local_pilot_noise(pilots, 8, positions)
        np.testing.assert_allclose(observed, np.ones(len(positions)))

    def test_receiver_llrs_decode_digital_loopback(self):
        cfg = P.config()
        payload = P.expected_payload(cfg)
        wave = M.modulate_frame(cfg, payload)
        capture = np.concatenate([np.zeros(3_000), wave, np.zeros(12_000)])
        result = P.receiver_llrs(cfg, capture)
        self.assertEqual(result["block_valid"], [True] * cfg.n_blocks)
        self.assertEqual(result["decoded_payload"], payload)
        transmitted = P.transmitted_interleaved_bits(cfg, payload)
        _, gmi, _ = P.optimized_gmi(transmitted, result["llrs"])
        self.assertGreater(gmi, 0.999)

    def test_average_ranks_ties(self):
        np.testing.assert_allclose(P.average_ranks([4.0, 1.0, 4.0]), [2.5, 1.0, 2.5])


if __name__ == "__main__":
    unittest.main()
