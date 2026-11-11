"""The speaker path-matrix analysis on a generated two-speaker, two-microphone capture."""

import os

import numpy as np
import pytest

import speaker_matrix as M


@pytest.fixture(scope="module")
def resample():
    clock_offset = pytest.importorskip("clock_offset")
    if not os.path.exists(clock_offset.library_path()):
        pytest.skip("research dylib not built: run scratch/garage/build_clock_dylib.sh")
    return clock_offset.resample


def _capture(resample, ppm, right_gain=1.0, seed=0):
    rng = np.random.default_rng(seed)
    program = M.stereo_program(0.25).astype(float)
    mics = []
    paths = {}
    for mic in range(2):
        total = np.zeros(len(program) + 48000)
        for speaker in range(2):
            h = np.zeros(2400)
            taps = rng.integers(0, 2400, 30)
            h[taps] = rng.normal(size=30) * np.exp(-taps / 600)
            h[10 + 7 * mic + 3 * speaker] += 1.5
            h *= right_gain if speaker else 1.0
            paths[mic, speaker] = h
            total[:len(program) + len(h) - 1] += np.convolve(program[:, speaker], h)
        total = np.concatenate([np.zeros(20000), total]) + rng.normal(size=len(total) + 20000) * 1e-4
        mics.append(np.asarray(resample(total, -ppm), float) if ppm else total)
    return np.stack(mics, axis=1), paths


def test_recovers_the_clock_offset_and_passes_its_own_checks(resample):
    capture, _ = _capture(resample, 28.0)
    summary, _ = M.analyse(capture, resample)
    assert summary["clock_offset_ppm"] == pytest.approx(28.0, abs=1.0)
    for mic in summary["microphones"].values():
        assert mic["both_vs_sum_error"] < 0.02
        assert mic["left_repeat_error"] < 0.02


def test_reports_a_quieter_right_speaker(resample):
    capture, paths = _capture(resample, 0.0, right_gain=0.5)
    summary, _ = M.analyse(capture, resample)
    freqs = np.fft.rfftfreq(M.NFFT, 1 / M.SR)
    band = (freqs >= M.F_LO + 200) & (freqs <= M.F_HI - 200)
    for index, mic in summary["microphones"].items():
        power = [np.mean(np.abs(np.fft.rfft(paths[index, speaker], M.NFFT))[band] ** 2) for speaker in range(2)]
        truth = 10 * np.log10(power[1] / power[0])
        assert truth < -3.0
        assert mic["mean_power_db_re_left"]["right"] == pytest.approx(truth, abs=0.5)
        assert mic["mean_power_db_re_left"]["phase_aligned"] > mic["mean_power_db_re_left"]["same_signal"]


def test_measured_response_matches_the_path_that_made_it(resample):
    capture, paths = _capture(resample, 0.0)
    _, arrays = M.analyse(capture, resample)
    band = (np.fft.rfftfreq(M.NFFT, 1 / M.SR) >= M.F_LO + 200) & (np.fft.rfftfreq(M.NFFT, 1 / M.SR) <= M.F_HI - 200)
    for (mic, speaker), h in paths.items():
        truth = np.abs(np.fft.rfft(h, M.NFFT))[band]
        got = np.abs(arrays["H"][mic, speaker])
        got = got * (np.sum(truth * got) / np.sum(got * got))  # the sweep's own gain is not of interest
        assert np.sum((got - truth) ** 2) / np.sum(truth ** 2) < 0.05
