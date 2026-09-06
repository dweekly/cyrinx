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


LAPTOP = geo.MACBOOK_PRO_M4
PIXEL = geo.PIXEL_7A
IPHONE = geo.IPHONE_17_PRO_MAX
MOTO = geo.MOTO_G_2026


def link(tx, rx):
    return geo.conservative_link(tx, rx, amp=0.5)


def test_every_conservative_link_is_qpsk_rate_half():
    for tx, rx in ((LAPTOP, PIXEL), (PIXEL, LAPTOP), (MOTO, IPHONE)):
        g = link(tx, rx)
        assert g.bits_per_bin == 2, "NF-12: 1-bit bins are broken, QPSK is the floor"
        assert g.code_rate == "1/2"


def test_a_symmetric_pair_gets_a_symmetric_band():
    """Two of the same device must not be handed an asymmetry."""
    for endpoint in (LAPTOP, PIXEL, MOTO):
        out, back = geo.conservative_pair(endpoint, endpoint, 0.5, 0.5)
        assert (out.f_lo_hz, out.f_hi_hz) == (back.f_lo_hz, back.f_hi_hz), (
            f"{endpoint.label} to itself came out asymmetric"
        )


def test_two_laptops_keep_the_wide_band_in_both_directions():
    out, back = geo.conservative_pair(LAPTOP, LAPTOP, 0.5, 0.5)
    for g in (out, back):
        assert g.f_hi_hz == pytest.approx(23000.0), (
            "a laptop pair must not inherit a phone speaker limit"
        )


def test_a_phone_pair_is_narrow_in_both_directions():
    out, back = geo.conservative_pair(MOTO, IPHONE, 0.7, 0.7)
    assert out.f_hi_hz == pytest.approx(14000.0), "Moto emission cliffs at 14 kHz"
    assert back.f_hi_hz == pytest.approx(11000.0), "iPhone emission rolls off by 11 kHz"


def test_asymmetry_is_computed_from_capability_not_from_a_role():
    """A high-end phone talking to a low-end phone, and the reverse."""
    strong_to_weak = link(IPHONE, MOTO)
    weak_to_strong = link(MOTO, IPHONE)
    assert strong_to_weak.f_hi_hz == pytest.approx(11000.0)
    assert weak_to_strong.f_hi_hz == pytest.approx(14000.0)
    assert weak_to_strong.f_hi_hz > strong_to_weak.f_hi_hz, (
        "the 'weaker' endpoint emits the wider band here; a role would get this "
        "backwards"
    )


def test_the_band_is_the_intersection_of_emission_and_capture():
    g = link(PIXEL, LAPTOP)
    assert g.f_lo_hz == pytest.approx(max(PIXEL.emit_hz[0], LAPTOP.capture_hz[0]))
    assert g.f_hi_hz == pytest.approx(
        min(PIXEL.emit_hz[1], LAPTOP.capture_hz[1], geo.COHERENT_CEILING_HZ)
    )


def test_no_phone_transmits_a_coherent_geometry_above_the_incoherence_ceiling():
    """NF-9: both tested phone speakers are phase-incoherent above ~18 kHz."""
    for phone in (PIXEL, IPHONE, MOTO):
        assert link(phone, LAPTOP).f_hi_hz <= geo.COHERENT_CEILING_HZ


def test_an_uncharacterized_endpoint_degrades_to_the_narrowest_measured_band():
    g = link(geo.UNKNOWN_ENDPOINT, geo.UNKNOWN_ENDPOINT)
    widest = max(e.emit_hz[1] for e in geo.MEASURED_ENDPOINTS.values())
    assert g.f_hi_hz < widest, "unknown must not resolve to an optimistic band"
    assert g.f_hi_hz == pytest.approx(11000.0)


def test_a_pair_with_no_overlapping_band_is_an_error_not_a_silent_empty_geometry():
    # A receiver that only hears above where the iPhone speaker stops emitting.
    highpass_only = geo.EndpointCapability(
        label="highpass-only", emit_hz=(12000.0, 20000.0),
        capture_hz=(12000.0, 20000.0), provenance="test fixture",
    )
    with pytest.raises(ValueError, match="no usable band"):
        link(IPHONE, highpass_only)


def test_capability_bands_are_validated():
    with pytest.raises(ValueError):
        geo.EndpointCapability("bad", (1000.0, 500.0), (300.0, 400.0), "test fixture")


def test_guard_budget_matches_the_frozen_cyclic_prefix():
    g = link(LAPTOP, PIXEL)
    assert g.guard_ms == pytest.approx(geo.PRACTICAL_GUARD_BUDGET_MS)
    assert g.cyclic_prefix == geo.CYCLIC_PREFIX_SAMPLES


def test_amplitude_is_a_session_parameter_and_is_range_checked():
    g = link(LAPTOP, PIXEL).at_amplitude(0.2)
    assert g.amp == 0.2
    for bad in (0.0, -0.1, 1.5):
        with pytest.raises(ValueError):
            g.at_amplitude(bad)


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


PAIRS_UNDER_TEST = (
    (LAPTOP, PIXEL), (PIXEL, LAPTOP),
    (LAPTOP, LAPTOP), (MOTO, IPHONE), (IPHONE, MOTO),
    (geo.UNKNOWN_ENDPOINT, geo.UNKNOWN_ENDPOINT),
)


def test_every_geometry_builds_a_valid_config_the_c_codec_accepts(codec):
    for tx, rx in PAIRS_UNDER_TEST:
        g = link(tx, rx)
        geometry = codec.geometry(g.to_clib_cfg(codec))
        assert geometry.n_blocks > 0, f"{g.label} carries no blocks"
        assert geometry.payload_bytes > 0


def test_round_trip_through_the_c_codec_is_byte_exact(codec):
    """The plan requires a byte-exact digital round trip before any playback."""
    for tx, rx in PAIRS_UNDER_TEST:
        g = link(tx, rx)
        cfg = g.to_clib_cfg(codec)
        payload = np.random.default_rng(7).integers(
            0, 256, codec.geometry(cfg).payload_bytes, dtype=np.uint8
        ).tobytes()
        decoded = codec.decode(cfg, codec.encode(cfg, payload))
        assert decoded is not None, f"{g.label} decode returned nothing"
        assert decoded["blocks_ok"] == decoded["blocks_total"], (
            f"{g.label} lost blocks in a digital round trip"
        )
        assert decoded["payload"][: len(payload)] == payload, (
            f"{g.label} digital round trip is not byte-exact"
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
