#!/usr/bin/env python3
"""Offline tests for the conservative link geometries. No hardware, no audio devices.

Run: .venv/bin/python3 -m pytest scratch/garage/ -q
"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hw20k")
)

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
