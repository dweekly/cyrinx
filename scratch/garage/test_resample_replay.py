"""The clock-offset resampler must do what its name says, in the stated direction."""

import numpy as np
import pytest

resample_replay = pytest.importorskip("resample_replay", reason="needs the C codec binding")


def _tone_hz(x, sr):
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x)), 1 << 22))
    return np.argmax(spec) * sr / (1 << 22)


def test_zero_offset_returns_the_capture_unchanged():
    x = np.random.default_rng(0).normal(size=4096)
    assert resample_replay.resample(x, 0) is x


@pytest.mark.parametrize("ppm", [28.0, -27.4, 500.0])
def test_a_tone_moves_by_the_requested_fraction(ppm):
    sr, f = 48000, 10000.0
    x = np.sin(2 * np.pi * f * np.arange(sr * 2) / sr)
    y = resample_replay.resample(x, ppm)
    shift_ppm = (_tone_hz(y[1000:-1000], sr) / _tone_hz(x[1000:-1000], sr) - 1) * 1e6
    assert shift_ppm == pytest.approx(ppm, abs=2.0)


def test_amplitude_is_preserved_up_to_20_khz():
    """The 32-tap windowed sinc is flat to 20 kHz. Above that it droops (about
    0.2 dB at 22 kHz and up to 1.4 dB at 23 kHz, measured), which makes a
    corrected decode slightly pessimistic at the very top of the band."""
    sr = 48000
    for f in (1000.0, 6000.0, 12000.0, 18000.0, 20000.0):
        x = np.sin(2 * np.pi * f * np.arange(sr) / sr)
        y = resample_replay.resample(x, 28.0)[2000:-2000]
        assert np.sqrt(np.mean(y ** 2)) == pytest.approx(np.sqrt(0.5), rel=0.01), f


def test_capture_key_matches_how_ladder_stores_repeats():
    assert resample_replay.capture_key({"name": "qpsk-full-g85"}) == "qpsk-full-g85"
    assert resample_replay.capture_key({"name": "qpsk-full-g85", "run": 0}) == "qpsk-full-g85#0"
    assert resample_replay.capture_key({"name": "qpsk-full-g85", "run": 3}) == "qpsk-full-g85#3"
