#!/usr/bin/env python3
import unittest

import final_measurement as measurement
import modem


class FinalMeasurementAccountingTests(unittest.TestCase):
    frame_samples = 192_000
    frame_count = 5
    blocks_per_frame = 56
    origin = 12_345
    period = frame_samples + 12_000

    @classmethod
    def payload(cls, slot):
        block = bytes([slot]) * modem.CRC_BLOCK
        return block * cls.blocks_per_frame

    @classmethod
    def result(cls, slot):
        block = bytes([slot]) * modem.CRC_BLOCK
        return {
            "ok": True,
            "blocks": [
                (block_index, True, block)
                for block_index in range(cls.blocks_per_frame)
            ],
        }

    def score(self, slots):
        payloads = [self.payload(slot) for slot in range(self.frame_count)]
        results = [
            (self.origin + slot * self.period, self.result(slot))
            for slot in slots
        ]
        return measurement.score_scheduled_results(
            results,
            payloads,
            self.origin,
            self.period,
            480,
        )

    def test_complete_schedule_recovers_every_ordered_block(self):
        verified, attributions = self.score(range(self.frame_count))

        self.assertEqual(verified, 280)
        self.assertEqual(attributions, [(slot, 56) for slot in range(5)])
        self.assertAlmostEqual(
            measurement.scheduled_goodput_bps(verified, self.frame_samples),
            27_306.666_666_666_668,
        )

    def test_missing_slot_remains_in_the_denominator(self):
        verified, _ = self.score([0, 1, 3, 4])

        self.assertEqual(verified, 224)
        self.assertAlmostEqual(
            measurement.scheduled_goodput_bps(verified, self.frame_samples),
            21_845.333_333_333_332,
        )

    def test_duplicate_or_mistimed_detection_cannot_add_credit(self):
        payloads = [self.payload(slot) for slot in range(self.frame_count)]
        results = [
            (self.origin, self.result(0)),
            (self.origin, self.result(0)),
            (self.origin + self.period + 481, self.result(1)),
        ]

        verified, attributions = measurement.score_scheduled_results(
            results,
            payloads,
            self.origin,
            self.period,
            480,
        )

        self.assertEqual(verified, 56)
        self.assertEqual(attributions, [(0, 56), (0, 0), (None, 0)])


if __name__ == "__main__":
    unittest.main()
