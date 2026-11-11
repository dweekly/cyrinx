"""The stereo buffers the ladder builds for each speaker arrangement."""

import numpy as np
import pytest

ladder = pytest.importorskip("ladder", reason="needs the hardware harness imports")


def test_each_arrangement_drives_the_speakers_it_names():
    w = np.arange(1, 101, dtype=np.float32) / 100
    d = ladder.SPEAKER_DELAY_SAMPLES
    both, left, right, delay = (ladder.speaker_program(w, name) for name in ("both", "left", "right", "delay"))
    assert np.array_equal(both[:100, 0], w) and np.array_equal(both[:100, 1], w)
    assert np.array_equal(left[:100, 0], w) and not left[:, 1].any()
    assert np.array_equal(right[:100, 1], w) and not right[:, 0].any()
    assert np.array_equal(delay[:100, 0], w) and np.array_equal(delay[d:d + 100, 1], w)
    assert not delay[:d, 1].any()
