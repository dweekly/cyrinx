#!/usr/bin/env python3
"""Offline tests for the G0 deliverables. No hardware, no audio devices.

Run: .venv/bin/python3 -m pytest scratch/garage/test_garage_g0.py -q
"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hw20k")
)

import delay_spread as ds  # noqa: E402
import geometries as geo  # noqa: E402

SR = 48000


def synthetic_ir(decay_ms, length_ms=200.0, sr=SR, noise_rms=0.0, pre_ms=10.0, seed=1):
    """An exponentially decaying impulse response with a known decay rate.

    ``decay_ms`` is the 60 dB decay time, so the -10/-20 dB crossings are
    analytically decay_ms/6 and decay_ms/3.
    """
    rng = np.random.default_rng(seed)
    pre = int(pre_ms * sr / 1000)
    n = int(length_ms * sr / 1000)
    t = np.arange(n) / sr
    # Amplitude decay giving -60 dB of *energy* at decay_ms.
    amplitude = 10.0 ** (-3.0 * t / (decay_ms / 1000.0))
    tail = amplitude * rng.standard_normal(n)
    tail[0] = 1.0
    # Unit peak, so peak-to-noise is exactly 20*log10(1/noise_rms) and the
    # noise-headroom rule can be exercised deterministically.
    tail = tail / np.max(np.abs(tail))
    ir = np.concatenate([np.zeros(pre), tail])
    if noise_rms > 0:
        ir = ir + rng.normal(0.0, noise_rms, len(ir))
    return ir, pre


# --- geometries -------------------------------------------------------------


def test_conservative_pair_is_qpsk_rate_half():
    for direction in ("downlink", "uplink"):
        g = geo.conservative(direction)
        assert g.bits_per_bin == 2, "NF-12: 1-bit bins are broken, QPSK is the floor"
        assert g.code_rate == "1/2"


def test_uplink_band_is_narrower_and_within_every_measured_speaker_limit():
    up = geo.conservative("uplink")
    down = geo.conservative("downlink")
    assert up.f_hi_hz < down.f_hi_hz
    # NF-9: both tested phone speakers are phase-incoherent above ~18 kHz, so a
    # coherent uplink must stay well below that.
    assert up.f_hi_hz <= 18000.0
    # A5: the Moto G speaker cliffs at 14 kHz.
    assert up.f_hi_hz <= 14000.0


def test_guard_budget_matches_the_frozen_cyclic_prefix():
    g = geo.conservative("downlink")
    assert g.guard_ms == pytest.approx(geo.PRACTICAL_GUARD_BUDGET_MS)
    assert g.cyclic_prefix == geo.CYCLIC_PREFIX_SAMPLES


def test_amplitude_is_a_session_parameter_and_is_range_checked():
    g = geo.conservative("downlink").at_amplitude(0.2)
    assert g.amp == 0.2
    assert geo.conservative("downlink").amp == geo.REFERENCE_AMP_DOWNLINK, (
        "at_amplitude must not mutate the frozen geometry"
    )
    for bad in (0.0, -0.1, 1.5):
        with pytest.raises(ValueError):
            g.at_amplitude(bad)


def test_unknown_direction_is_rejected():
    with pytest.raises(ValueError):
        geo.conservative("sideways")


@pytest.fixture(scope="module")
def codec():
    """The C bulk codec, or a skip if the dylib is not built.

    ``importorskip`` alone is not enough: ``clib`` imports fine and loads the
    library lazily, so an absent dylib surfaces as FileNotFoundError from the
    first call rather than as an ImportError. Only that error skips; a codec
    that is present but wrong must fail.
    """
    clib = pytest.importorskip("clib", reason="clib not importable")
    try:
        clib.geometry(clib.make_cfg())
    except FileNotFoundError as exc:
        pytest.skip(f"C bulk codec not built: {exc}")
    return clib


def test_geometry_builds_a_valid_config_the_c_codec_accepts(codec):
    clib = codec
    for direction in ("downlink", "uplink"):
        g = geo.conservative(direction)
        cfg = g.to_clib_cfg(clib)
        geometry = clib.geometry(cfg)
        assert geometry.n_blocks > 0, f"{direction} geometry carries no blocks"
        assert geometry.payload_bytes > 0


def test_round_trip_through_the_c_codec_is_byte_exact(codec):
    """The plan requires a byte-exact digital round trip before any playback."""
    clib = codec
    for direction in ("downlink", "uplink"):
        cfg = geo.conservative(direction).to_clib_cfg(clib)
        payload = np.random.default_rng(7).integers(
            0, 256, clib.geometry(cfg).payload_bytes, dtype=np.uint8
        ).tobytes()
        wave = clib.encode(cfg, payload)
        decoded = clib.decode(cfg, wave)
        assert decoded is not None, f"{direction} decode returned nothing"
        assert decoded["blocks_ok"] == decoded["blocks_total"], (
            f"{direction} lost {decoded['blocks_total'] - decoded['blocks_ok']} "
            f"of {decoded['blocks_total']} blocks in a digital round trip"
        )
        assert decoded["payload"][: len(payload)] == payload, (
            f"{direction} digital round trip is not byte-exact"
        )


# --- delay spread -----------------------------------------------------------


def test_matches_freqresp_on_a_clean_response():
    """Same numbers as the existing implementation; this pins the agreement."""
    freqresp = pytest.importorskip("freqresp", reason="freqresp not importable")
    ir, peak = synthetic_ir(decay_ms=90.0)
    ours = ds.measure(ir, peak, SR)
    theirs = freqresp.delay_spread(ir, peak, sr=SR)
    for db, key in ((-10.0, "-10dB"), (-15.0, "-15dB"), (-20.0, "-20dB")):
        assert ours.at(db).usable, f"{db} dB should be usable on a clean response"
        assert ours.at(db).ms == pytest.approx(theirs[key], abs=1e-9)


def test_recovers_a_known_decay_rate():
    """A 90 ms T60 crosses -10 dB at 15 ms and -20 dB at 30 ms."""
    ir, peak = synthetic_ir(decay_ms=90.0)
    out = ds.measure(ir, peak, SR)
    assert out.usable_ms(-10.0) == pytest.approx(15.0, rel=0.15)
    assert out.usable_ms(-20.0) == pytest.approx(30.0, rel=0.15)


def test_noise_invalidates_the_deep_threshold_first():
    """A capture can support -10 dB while -20 dB is already in the noise.

    The deep threshold fails first because the curve has less signal energy left
    at its crossing while the integrated noise ahead of it has barely shrunk.
    """
    ir, peak = synthetic_ir(decay_ms=20.0, length_ms=300.0, noise_rms=0.002)
    out = ds.measure(ir, peak, SR)
    assert out.at(-10.0).usable
    assert out.at(-20.0).status == ds.NOISE_LIMITED
    assert out.usable_ms(-20.0) is None
    assert "integrated noise" in out.at(-20.0).detail
    assert out.status == ds.OK, "one usable threshold still makes the readout usable"


def test_an_impulse_in_stationary_noise_is_not_a_long_delay_spread():
    """The P1 from review: peak-to-noise cannot validate an integrated curve.

    One impulse, no reflections, stationary noise 46 dB below the peak. The
    Schroeder curve integrates that noise across the window and crosses -10 dB
    at 38.8 ms; the noiseless answer is 0.02 ms. Accepting it would put a
    reflection-free channel past the 16 ms guard budget and flip the stage 1G
    gate the wrong way.
    """
    ir = np.random.default_rng(1).normal(0, 0.0055, 6000)
    ir[240] = 1.0
    out = ds.measure(ir, 240, SR)
    assert out.peak_to_noise_db > 40.0, "the peak ratio looks excellent, and lies"
    assert out.at(-10.0).status == ds.NOISE_LIMITED
    assert out.usable_ms(-10.0) is None
    assert ds.exceeds_guard_budget(
        out.usable_ms(-10.0), geo.PRACTICAL_GUARD_BUDGET_MS
    ) is None, "the gate must abstain, not conclude the guard is exceeded"

    clean = np.zeros(6000)
    clean[240] = 1.0
    assert ds.measure(clean, 240, SR).usable_ms(-10.0) == pytest.approx(0.02, abs=0.01)


def test_aggregate_status_does_not_blame_noise_for_a_window_limit():
    """P2 from review: a noiseless truncated capture is not noise-limited."""
    out = ds.measure(np.r_[np.zeros(240), np.ones(1920)], 240, SR)
    assert all(r.status == ds.WINDOW_LIMITED for r in out.readings.values())
    assert out.status == ds.WINDOW_LIMITED
    assert "noise" not in out.detail


def test_a_decay_longer_than_the_window_is_window_limited_not_a_number():
    """freqresp returns the window length here; this must not read as a figure."""
    ir, peak = synthetic_ir(decay_ms=4000.0, length_ms=40.0)
    out = ds.measure(ir, peak, SR)
    assert out.at(-20.0).status == ds.WINDOW_LIMITED
    assert out.usable_ms(-20.0) is None


def test_truncated_and_malformed_captures_are_invalid():
    ir, peak = synthetic_ir(decay_ms=60.0, length_ms=2.0)
    assert ds.measure(ir, peak, SR).status == ds.INVALID

    ir, peak = synthetic_ir(decay_ms=60.0, pre_ms=0.2)
    assert ds.measure(ir, peak, SR).status == ds.INVALID, (
        "too few pre-peak samples to estimate a noise floor"
    )

    ir, peak = synthetic_ir(decay_ms=60.0)
    ir[500] = np.nan
    assert ds.measure(ir, peak, SR).status == ds.INVALID

    assert ds.measure(np.zeros(SR), SR * 2, SR).status == ds.INVALID


def test_silence_is_invalid_rather_than_a_zero_delay_spread():
    ir = np.concatenate([np.zeros(1000), np.zeros(SR // 10)])
    assert ds.measure(ir, 1000, SR).status == ds.INVALID


# --- the gate ---------------------------------------------------------------


def test_gate_abstains_when_the_reading_is_not_usable():
    assert ds.exceeds_guard_budget(None, geo.PRACTICAL_GUARD_BUDGET_MS) is None


def test_gate_compares_against_the_declared_budget():
    budget = geo.PRACTICAL_GUARD_BUDGET_MS
    assert ds.exceeds_guard_budget(35.8, budget) is True, (
        "NF-13's strong-tap spread is beyond the budget"
    )
    assert ds.exceeds_guard_budget(5.0, budget) is False
