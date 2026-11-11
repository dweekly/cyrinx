"""The research clock-offset decoder and pilot fit, on generated frames only.

These need the research dylib (build_clock_dylib.sh) loaded as the codec, so
that the pilot-fit switch reaches the decoder; they skip otherwise.
"""

import os

import numpy as np
import pytest


@pytest.fixture(scope="module")
def research(codec):
    clock_offset = pytest.importorskip("clock_offset")
    if not os.path.exists(clock_offset.library_path()):
        pytest.skip("research dylib not built: run scratch/garage/build_clock_dylib.sh")
    if os.path.realpath(codec.loaded_library_path()) != os.path.realpath(clock_offset.library_path()):
        pytest.skip("CYRINX_BULK_CODEC_PATH does not point at the research dylib")
    yield clock_offset
    clock_offset.set_pilot_fit(clock_offset.FIT_SHIPPED)


LONG = dict(bits_per_bin=4, rate="1/2", n_sym=6, f_lo=300, f_hi=23000, nfft=16384, cp=8192, amp=0.5)
"""The best repeated downlink cell of the 2.5 ft session."""


def _frame(codec, seed, **cfg_kwargs):
    cfg = codec.make_cfg(**cfg_kwargs)
    payload = np.random.default_rng(seed).integers(
        0, 256, codec.geometry(cfg).payload_bytes, dtype=np.uint8).tobytes()
    wave = np.asarray(codec.encode(cfg, payload), dtype=float)
    return cfg, payload, np.concatenate([np.zeros(3000), wave, np.zeros(6000)])


def _noisy(x, snr_db, seed):
    signal = x[np.abs(x) > 0]
    sigma = np.sqrt(np.mean(signal ** 2) / 10 ** (snr_db / 10))
    return x + np.random.default_rng(1000 + seed).normal(size=len(x)) * sigma


@pytest.mark.parametrize("freq", [1000.0, 12000.0, 20000.0, 22000.0, 23000.0])
def test_resampler_is_flat_across_the_whole_band(research, freq):
    sr = 48000
    x = np.sin(2 * np.pi * freq * np.arange(sr) / sr)
    y = research.resample(x, 28.0)[2000:-2000]
    assert 20 * np.log10(np.sqrt(np.mean(y ** 2)) / np.sqrt(0.5)) == pytest.approx(0.0, abs=0.02)


def test_resampling_there_and_back_returns_the_signal(research):
    sr = 48000
    t = np.arange(sr) / sr
    x = sum(np.sin(2 * np.pi * f * t + f) for f in (700.0, 5300.0, 14100.0, 22100.0)) / 4
    back = research.resample(research.resample(x, 40.0), -40.0 / (1 + 40e-6))
    assert np.max(np.abs(back[500:-500] - x[500:-500])) < 1e-3


@pytest.mark.parametrize("ppm", [28.0, -27.4, 90.0, 0.0])
def test_estimate_recovers_an_applied_offset(research, codec, ppm):
    cfg, _, x = _frame(codec, 5, **LONG)
    captured = research.resample(x, -ppm) if ppm else x  # a capture that needs +ppm to undo
    estimate, coherence = research.estimate(cfg, _noisy(np.asarray(captured, float), 20, 5))
    assert estimate == pytest.approx(ppm, abs=0.5)
    # A large offset also smears each training symbol, which a slide cannot
    # explain, so coherence falls even though the estimate stays right.
    assert coherence > (0.9 if abs(ppm) < 40 else 0.5)


def test_estimate_refuses_a_capture_too_short_for_a_frame(research, codec):
    cfg, _, x = _frame(codec, 5, **LONG)
    assert research.estimate(cfg, x[:20000]) is None


def test_blind_decode_recovers_an_offset_frame(research, codec):
    cfg, payload, x = _frame(codec, 7, **LONG)
    captured = _noisy(np.asarray(research.resample(x, -28.0), float), 25, 7)
    decoded = research.decode(cfg, captured)
    assert decoded["ppm"] == pytest.approx(28.0, abs=0.5)
    assert codec.ordered_verified_blocks(decoded, payload) == codec.geometry(cfg).n_blocks


def test_blind_decode_leaves_a_frame_without_offset_alone(research, codec):
    cfg, payload, x = _frame(codec, 8, **LONG)
    decoded = research.decode(cfg, x)
    assert decoded["ppm"] == 0.0
    assert decoded["payload"] == codec.decode(cfg, x)["payload"] == payload


def test_shipped_fit_is_the_default_and_unknown_modes_are_rejected(research):
    assert research.set_pilot_fit(research.FIT_SHIPPED) == research.FIT_SHIPPED
    with pytest.raises(ValueError):
        research.set_pilot_fit(7)


def test_coherent_fit_decodes_noisy_long_symbols_the_shipped_fit_loses(research, codec):
    """White noise only, no echo and no clock offset: at 14 dB the shipped fit
    loses about half the blocks of a wide 16384-point frame and the coherent
    fit loses none (six seeds: 111 and 234 of 234 when this was written)."""
    totals = {research.FIT_SHIPPED: 0, research.FIT_COHERENT: 0}
    scheduled = 0
    for seed in range(4):
        cfg, payload, x = _frame(codec, seed, **LONG)
        noisy = _noisy(x, 14, seed)
        for mode in totals:
            research.set_pilot_fit(mode)
            totals[mode] += codec.ordered_verified_blocks(codec.decode(cfg, noisy), payload)
        scheduled += codec.geometry(cfg).n_blocks
    research.set_pilot_fit(research.FIT_SHIPPED)
    assert totals[research.FIT_COHERENT] == scheduled
    assert totals[research.FIT_SHIPPED] < 0.8 * scheduled


def test_both_fits_agree_on_a_clean_standard_frame(research, codec):
    cfg, payload, x = _frame(codec, 9, bits_per_bin=4, rate="3/4", n_sym=16)
    results = []
    for mode in (research.FIT_SHIPPED, research.FIT_COHERENT):
        research.set_pilot_fit(mode)
        results.append(codec.decode(cfg, x)["payload"])
    research.set_pilot_fit(research.FIT_SHIPPED)
    assert results[0] == results[1] == payload
