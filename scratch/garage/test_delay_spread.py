#!/usr/bin/env python3
"""Acceptance tests for the delay-spread readout.

Physical-behaviour cases run through `freqresp.make_ess` and the garage
acquisition adapter, so they exercise the path the readout sits in rather than
the function alone. Argument and status handling keep direct unit tests.

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

import acquire  # noqa: E402
import delay_spread as ds  # noqa: E402
import freqresp as F  # noqa: E402
import geometries as geo  # noqa: E402

SR = F.SR
ROOM_TONE_S = 7.0  # longer than the 6.0 s inverse filter; see NoiseReference


def channel(taps, length_s=1.0):
    """An impulse response from (delay_ms, gain) pairs."""
    ir = np.zeros(int(length_s * SR))
    for delay_ms, gain in taps:
        ir[int(delay_ms * SR / 1000.0)] = gain
    return ir


def diffuse(t60_ms, length_s=1.0, seed=0):
    """Exponentially decaying noise: -10 dB at t60/6, -20 dB at t60/3."""
    rng = np.random.default_rng(seed)
    n = int(length_s * SR)
    t = np.arange(n) / SR
    return 10.0 ** (-3.0 * t / (t60_ms / 1000.0)) * rng.standard_normal(n)


def acquire_through(channel_ir, noise_rms=0.0, horizon_ms=250.0, seed=1):
    """Sweep through a channel, with noise added to the raw capture PCM.

    The room tone is an *independent* realization at the same level, as it would
    be in the field: two separate recordings of the same noise process.
    """
    x, inv = F.make_ess()
    rng = np.random.default_rng(seed)
    captured = np.convolve(x, channel_ir)[: len(x) + len(channel_ir)]
    if noise_rms > 0:
        captured = captured + rng.normal(0.0, noise_rms, len(captured))
        tone = rng.normal(0.0, noise_rms, int(ROOM_TONE_S * SR))
        noise = ds.NoiseReference.from_pcm(tone, inv, SR)
    else:
        noise = ds.NoiseReference.negligible()
    return acquire.deconvolve(captured, inv, SR, horizon_ms=horizon_ms), noise


# --- 1, 2: pinned figures on the real acquisition path ----------------------


def test_sweep_against_itself_matches_freqresp_exactly():
    acq, noise = acquire_through(channel([(0.0, 1.0)]))
    out = ds.measure(acq, noise)
    assert out.usable_ms(-10.0) == pytest.approx(0.020833, abs=1e-6)
    assert out.usable_ms(-15.0) == pytest.approx(0.020833, abs=1e-6)
    assert out.usable_ms(-20.0) == pytest.approx(0.166667, abs=1e-6)


def test_a_thirty_millisecond_reflection_reports_thirty_milliseconds():
    acq, noise = acquire_through(channel([(0.0, 1.0), (30.0, 0.5)]))
    out = ds.measure(acq, noise)
    for db in ds.THRESHOLDS_DB:
        assert out.usable_ms(db) == pytest.approx(30.020833, abs=1e-6), (
            f"{db} dB must find the reflection, not a short answer"
        )


# --- 3: the observation horizon ---------------------------------------------


def test_a_late_reflection_is_seen_when_the_horizon_covers_it():
    """freqresp's 120 ms crop reports this as a single tap; the adapter must not."""
    acq, noise = acquire_through(channel([(0.0, 1.0), (150.0, 0.5)]), horizon_ms=250.0)
    out = ds.measure(acq, noise)
    assert out.usable_ms(-10.0) == pytest.approx(150.020833, abs=1e-6)

    cropped_ir, cropped_peak = F.deconvolve_ir(
        np.convolve(F.make_ess()[0], channel([(0.0, 1.0), (150.0, 0.5)]))[: len(F.make_ess()[0]) + SR],
        F.make_ess()[1],
    )
    assert F.delay_spread(cropped_ir, cropped_peak, sr=SR)["-10dB"] == pytest.approx(0.020833, abs=1e-6)


def test_a_reading_from_the_freqresp_crop_names_its_horizon():
    x, inv = F.make_ess()
    ir, peak = F.deconvolve_ir(np.convolve(x, channel([(0.0, 1.0), (150.0, 0.5)]))[: len(x) + SR], inv)
    acq = acquire.from_freqresp_crop(ir, peak, SR)
    out = ds.measure(acq, ds.NoiseReference.negligible())
    assert out.support == acquire.SUPPORT_HORIZON_TRUNCATED
    assert out.horizon_ms == pytest.approx(120.0, abs=1.0)
    assert any("discarded" in n for n in out.notes)


def test_a_recording_that_ended_early_says_so():
    x, inv = F.make_ess()
    captured = np.convolve(x, channel([(0.0, 1.0)]))[: len(x) + int(0.02 * SR)]
    acq = acquire.deconvolve(captured, inv, SR, horizon_ms=250.0)
    assert acq.support == acquire.SUPPORT_RECORDING_ENDED_EARLY
    assert not acq.covers_requested_horizon


# --- 4, 5: the diffuse case the method exists for ---------------------------


@pytest.mark.parametrize("seed", [1, 2, 3])
@pytest.mark.parametrize("t60_ms", [60.0, 120.0])
def test_a_noisy_diffuse_decay_returns_accurate_numbers(t60_ms, seed):
    acq, noise = acquire_through(diffuse(t60_ms, seed=seed), noise_rms=2e-4, seed=seed)
    out = ds.measure(acq, noise)
    reading = out.at(-10.0)
    assert reading.usable, f"T60 {t60_ms} seed {seed}: {reading.detail}"
    expected = t60_ms / 6.0
    assert reading.ms == pytest.approx(expected, rel=0.35)
    lo, hi = reading.ms_interval
    assert lo <= reading.ms <= hi


@pytest.mark.parametrize("t60_ms", [78.0, 114.0])
def test_decays_either_side_of_the_budget_are_right_or_abstain(t60_ms):
    """-10 dB lands at 13 ms and 19 ms; neither may fall on the wrong side quietly."""
    acq, noise = acquire_through(diffuse(t60_ms, seed=7), noise_rms=2e-4, seed=7)
    out = ds.measure(acq, noise)
    verdict = ds.exceeds_guard_budget(out.at(-10.0), geo.PRACTICAL_GUARD_BUDGET_MS)
    truth = (t60_ms / 6.0) > geo.PRACTICAL_GUARD_BUDGET_MS
    assert verdict is None or verdict == truth, (
        f"T60 {t60_ms}: gate said {verdict}, truth is {truth}"
    )


def test_degrades_gracefully_as_capture_noise_rises():
    """The whole point, as one ladder: right, then truncated but still right, then silent.

    Farina deconvolution buys roughly 40 dB of processing gain against stationary
    noise, so truncation does not engage until capture noise approaches the sweep
    amplitude itself. That is good news for the gate -- truncation bias cannot
    move a decision that is never truncated -- and it means the Lundeby path is a
    safety net for degenerate captures rather than the ordinary case.
    """
    truth_ms = 90.0 / 6.0
    seen = []
    for rms in (0.15, 0.4, 0.8, 1.5, 3.0):
        acq, noise = acquire_through(diffuse(90.0, seed=8), noise_rms=rms, seed=8)
        out = ds.measure(acq, noise)
        seen.append((rms, out.truncated, out.usable_ms(-10.0)))
        if out.usable_ms(-10.0) is not None:
            assert out.usable_ms(-10.0) == pytest.approx(truth_ms, rel=0.15), (
                f"rms {rms}: reported {out.usable_ms(-10.0)} for a {truth_ms} ms decay"
            )

    assert not seen[0][1], "clean capture must not need truncation"
    assert any(t for _, t, _ in seen), "high noise must engage truncation"
    assert seen[-1][2] is None, "noise above the sweep must abstain, not guess"


# --- 6, 7: both prior P1 cases, permanently -----------------------------------


def test_an_impulse_in_stationary_noise_is_not_a_long_delay_spread():
    """First review's P1, in the deconvolved domain with an independent tone."""
    acq, noise = acquire_through(channel([(0.0, 1.0)]), noise_rms=1e-3, seed=11)
    out = ds.measure(acq, noise)
    reading = out.at(-10.0)
    assert not reading.usable or reading.ms < 5.0, (
        f"reflection-free channel reported {reading.ms} ms"
    )
    assert ds.exceeds_guard_budget(reading, geo.PRACTICAL_GUARD_BUDGET_MS) is not True


def test_a_clean_two_tap_channel_reports_its_crossing():
    """Second review's P1: clean sparse channels must not be rejected."""
    acq, noise = acquire_through(channel([(0.0, 1.0), (30.0, 0.5)]), noise_rms=1e-6, seed=13)
    out = ds.measure(acq, noise)
    assert out.at(-10.0).usable, out.at(-10.0).detail
    assert out.usable_ms(-10.0) == pytest.approx(30.02, abs=0.5)


# --- 8: noise-domain calibration ---------------------------------------------


def test_white_noise_reference_matches_the_analytic_conversion():
    x, inv = F.make_ess()
    rms = 1e-3
    tone = np.random.default_rng(5).normal(0.0, rms, int(ROOM_TONE_S * SR))
    reference = ds.NoiseReference.from_pcm(tone, inv, SR)
    predicted = rms**2 * float(np.sum(inv**2))
    assert reference.power == pytest.approx(predicted, rel=0.1)


def test_coloured_noise_reference_is_finite_and_positive():
    x, inv = F.make_ess()
    white = np.random.default_rng(6).normal(0.0, 1e-3, int(ROOM_TONE_S * SR))
    coloured = np.convolve(white, np.ones(64) / 64.0, mode="same")
    reference = ds.NoiseReference.from_pcm(coloured, inv, SR)
    assert 0.0 < reference.power < float("inf")


def test_scaling_the_sweep_and_the_room_tone_together_changes_nothing():
    acq, noise = acquire_through(diffuse(90.0, seed=2), noise_rms=2e-4, seed=2)
    scaled_acq = acquire.Acquisition(
        ir=acq.ir * 8.0, peak_idx=acq.peak_idx, sr=acq.sr, horizon_ms=acq.horizon_ms,
        requested_horizon_ms=acq.requested_horizon_ms, support=acq.support,
    )
    scaled_noise = ds.NoiseReference(noise.power * 64.0, noise.samples_used, noise.duration_s)
    assert ds.measure(scaled_acq, scaled_noise).usable_ms(-10.0) == pytest.approx(
        ds.measure(acq, noise).usable_ms(-10.0)
    )


def test_a_room_tone_shorter_than_the_inverse_filter_is_refused():
    x, inv = F.make_ess()
    short = np.random.default_rng(3).normal(0.0, 1e-3, int(2.0 * SR))
    with pytest.raises(ds.NoiseReferenceError, match="longer room tone"):
        ds.NoiseReference.from_pcm(short, inv, SR)


# --- 9, 10, 11: contracts -----------------------------------------------------


def test_a_noise_reference_is_required():
    acq, _ = acquire_through(channel([(0.0, 1.0)]))
    with pytest.raises(TypeError, match="required input"):
        ds.measure(acq, None)
    with pytest.raises(TypeError, match="required input"):
        ds.measure(acq, 0.001)


def test_measure_requires_an_acquisition_not_a_bare_array():
    with pytest.raises(TypeError, match="Acquisition"):
        ds.measure(np.zeros(1000), ds.NoiseReference.negligible())


def test_the_gate_takes_a_reading_not_a_float():
    with pytest.raises(TypeError, match="inspect status"):
        ds.exceeds_guard_budget(35.8, geo.PRACTICAL_GUARD_BUDGET_MS)


@pytest.mark.parametrize(
    "status", [ds.INVALID, ds.NOISE_LIMITED, ds.WINDOW_LIMITED, ds.UNRESOLVED_AT_HORIZON, ds.UNSUPPORTED_FIT]
)
def test_the_gate_abstains_for_every_non_ok_status(status):
    """Including one that carries a diagnostic number, which must not be compared."""
    carrying_a_number = ds.Reading(-10.0, status, ms=38.8, ms_interval=(38.8, 38.8))
    assert ds.exceeds_guard_budget(carrying_a_number, geo.PRACTICAL_GUARD_BUDGET_MS) is None


def test_the_gate_rejects_a_non_finite_value():
    assert ds.exceeds_guard_budget(
        ds.Reading(-10.0, ds.OK, ms=float("nan"), ms_interval=(float("nan"), float("nan"))),
        geo.PRACTICAL_GUARD_BUDGET_MS,
    ) is None


def test_the_gate_abstains_when_the_interval_straddles_the_budget():
    budget = geo.PRACTICAL_GUARD_BUDGET_MS
    assert ds.exceeds_guard_budget(ds.Reading(-10.0, ds.OK, 16.2, (15.5, 17.0)), budget) is None
    assert ds.exceeds_guard_budget(ds.Reading(-10.0, ds.OK, 35.8, (34.0, 37.0)), budget) is True
    assert ds.exceeds_guard_budget(ds.Reading(-10.0, ds.OK, 5.0, (4.5, 5.5)), budget) is False


def test_partial_success_keeps_the_unavailable_thresholds_visible():
    acq, noise = acquire_through(diffuse(400.0, seed=4), noise_rms=2e-4, horizon_ms=60.0, seed=4)
    out = ds.measure(acq, noise)
    unusable = [db for db in ds.THRESHOLDS_DB if not out.at(db).usable]
    if unusable and out.status == ds.OK:
        assert "unavailable" in out.detail
        for db in unusable:
            assert out.at(db).detail, f"{db} dB gives no reason"


def test_every_reading_carries_its_diagnostics():
    """Uses a genuinely noisy capture, so the truncation path actually engages.

    Farina deconvolution buys about 40 dB of processing gain against stationary
    noise, so ordinary bench noise lands far below NEGLIGIBLE_NOISE_DB and takes
    the no-truncation path. Truncation only matters on poor captures, which is
    what this fixture is.
    """
    acq, noise = acquire_through(diffuse(90.0, seed=8), noise_rms=0.8, seed=8)
    out = ds.measure(acq, noise)
    assert out.noise_power > 0
    assert out.horizon_ms > 0
    assert out.truncated and out.truncation_ms > 0
    assert out.fit_slope_db_per_ms < 0
    assert out.fit_interval_ms is not None


def test_malformed_responses_are_invalid():
    acq, noise = acquire_through(channel([(0.0, 1.0)]))
    broken = acquire.Acquisition(
        ir=np.full_like(acq.ir, np.nan), peak_idx=acq.peak_idx, sr=SR,
        horizon_ms=acq.horizon_ms, requested_horizon_ms=acq.requested_horizon_ms,
        support=acq.support,
    )
    assert ds.measure(broken, noise).status == ds.INVALID

    silent = acquire.Acquisition(
        ir=np.zeros_like(acq.ir), peak_idx=acq.peak_idx, sr=SR,
        horizon_ms=acq.horizon_ms, requested_horizon_ms=acq.requested_horizon_ms,
        support=acq.support,
    )
    assert ds.measure(silent, noise).status == ds.INVALID


# --- Real hardware fixture ----------------------------------------------------
#
# A deconvolved response from an actual MacBook Pro speaker-to-microphone
# capture, retained so the readout's behaviour on real data is a regression
# rather than a memory. Provenance is in the fixture; the raw capture stays in
# the session's ignored artifacts directory.

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "mac_selfcal_ir.npz")


def real_acquisition(horizon_ms=None):
    data = np.load(FIXTURE)
    ir, peak, sr = data["ir"].astype(np.float64), int(data["peak_idx"]), int(data["sr"])
    if horizon_ms is not None:
        ir = ir[: peak + int(horizon_ms * sr / 1000.0)]
    return (
        acquire.Acquisition(
            ir=ir, peak_idx=peak, sr=sr,
            horizon_ms=1000.0 * (len(ir) - peak) / sr,
            requested_horizon_ms=float(data["horizon_ms"]),
            support=acquire.SUPPORT_OK,
        ),
        ds.NoiseReference(float(data["noise_power"]), 1, 8.0),
    )


def test_the_real_capture_reads_a_short_strong_tap_spread():
    acq, noise = real_acquisition()
    out = ds.measure(acq, noise)
    assert out.at(-10.0).usable
    assert out.usable_ms(-10.0) < 2.0, "a laptop's own speaker-to-mic path is short"
    assert ds.exceeds_guard_budget(out.at(-10.0), geo.PRACTICAL_GUARD_BUDGET_MS) is False


def test_the_deep_threshold_needs_a_longer_horizon_than_the_shallow_one():
    """Measured on the real capture: -10 dB is horizon-insensitive, -20 dB is not.

    The energy decay curve normalizes to the energy inside the analysed window,
    so a shorter window inflates every remaining fraction and pulls crossings
    earlier. The effect is negligible where the crossing is early and decisive
    where it is late -- which is exactly where a guard-budget decision lives.
    """
    shallow = [ds.measure(*real_acquisition(h)).usable_ms(-10.0) for h in (60, 120, 250, 500)]
    deep = [ds.measure(*real_acquisition(h)).usable_ms(-20.0) for h in (60, 120, 250, 500)]

    assert max(shallow) - min(shallow) < 0.1, f"-10 dB should be stable, got {shallow}"
    assert deep == sorted(deep), f"-20 dB should rise monotonically with horizon, got {deep}"
    assert deep[-1] - deep[0] > 3.0, f"-20 dB should move materially, got {deep}"
    assert deep[1] < geo.PRACTICAL_GUARD_BUDGET_MS < deep[-1], (
        "the 120 ms crop should land this capture on the wrong side of the budget"
    )


# --- Merge-review P1 regressions ---------------------------------------------


def test_playback_latency_does_not_overstate_supported_horizon():
    """Support is measured from the main tap, not from the recording start.

    Real playback has latency -- 154 ms on the MacBook path measured here -- so a
    sweep sits later in the recording than it does in the excitation, and the
    observable window after the main tap is shorter by exactly that much.
    """
    x, inv = F.make_ess()
    latency = 720
    capture = np.zeros(288960)
    capture[latency : latency + len(x)] = x[: len(capture) - latency]
    acq = acquire.deconvolve(capture, inv, SR, horizon_ms=20.0)
    assert acq.support == acquire.SUPPORT_RECORDING_ENDED_EARLY
    assert acq.horizon_ms == pytest.approx(5.0, abs=1.0), (
        f"only ~5 ms is supported after the tap, adapter claimed {acq.horizon_ms}"
    )


def test_a_truncated_reading_never_decides_the_gate():
    """The interval is a cut-point sensitivity, not an omitted-energy bound.

    Reproduced from the merge review: a 99 ms decay reads 16.083 ms clean, and
    under heavy noise reports [15.625, 16.000] -- an interval that sits wholly
    below the 16 ms budget while the truth sits above it. Truncation biases
    short, so without a validated bound a truncated reading cannot decide the
    budget in either direction.
    """
    acq, noise = acquire_through(diffuse(99.0, seed=27), horizon_ms=500.0, seed=27)
    clean = ds.measure(acq, noise).usable_ms(-10.0)
    assert clean == pytest.approx(16.0833, abs=0.01)

    acq, noise = acquire_through(diffuse(99.0, seed=27), noise_rms=2.0, horizon_ms=500.0, seed=27)
    out = ds.measure(acq, noise)
    reading = out.at(-10.0)
    assert out.truncated and reading.truncated
    assert ds.exceeds_guard_budget(reading, 16.0) is None, (
        "a truncated reading must abstain, not answer"
    )


def test_an_untruncated_reading_still_decides_the_gate():
    """The abstention above must not disable the gate on ordinary captures."""
    acq, noise = real_acquisition()
    out = ds.measure(acq, noise)
    assert not out.truncated
    assert ds.exceeds_guard_budget(out.at(-10.0), geo.PRACTICAL_GUARD_BUDGET_MS) is False


def test_denoised_crossings_match_freqresp_when_there_is_no_noise():
    """One source of truth: the local helper and freqresp must not drift."""
    acq, _ = acquire_through(diffuse(90.0, seed=31))
    tail = acq.tail
    remaining = np.cumsum((tail**2)[::-1])[::-1]
    ours = ds._crossings_from_remaining(remaining, SR)
    theirs = F.delay_spread(tail, 0, sr=SR)
    for key in ("-10dB", "-15dB", "-20dB"):
        assert ours[key] == pytest.approx(theirs[key], abs=1e-9)


def test_a_recording_that_missed_the_horizon_never_decides_the_gate():
    """A short reading from a short recording looks like a short room.

    The channel has a 150 ms reflection. Keeping only 20 ms of tail hides it
    entirely, and the -10 dB figure drops from 150.02 ms to 0.02 ms -- from one
    side of the budget to the other -- with nothing in the number to say so.
    """
    x, inv = F.make_ess()
    ch = channel([(0.0, 1.0), (150.0, 0.5)])
    short = np.convolve(x, ch)[: len(x) + int(0.02 * SR)]
    acq = acquire.deconvolve(short, inv, SR, horizon_ms=500.0)
    out = ds.measure(acq, ds.NoiseReference.negligible())
    assert acq.support == acquire.SUPPORT_RECORDING_ENDED_EARLY
    assert out.at(-10.0).ms == pytest.approx(0.020833, abs=1e-6)
    assert ds.exceeds_guard_budget(out.at(-10.0), 16.0) is None

    full, noise = acquire_through(ch, horizon_ms=500.0)
    assert ds.measure(full, noise).usable_ms(-10.0) == pytest.approx(150.020833, abs=1e-6)


def test_noise_uncertainty_widens_a_knife_edge_crossing():
    """Near a discrete tap the crossing is discontinuous in the energy fraction.

    Two taps at gain 1.0 and 0.33 leave 9.8% of the energy after the first --
    just under the -10 dB threshold -- so a hair of noise pushes the crossing
    past the second tap and the answer jumps 25 ms. The interval must span that,
    and the gate must abstain rather than pick a side.
    """
    clean, noise = acquire_through(channel([(0.0, 1.0), (30.0, 0.33)]), horizon_ms=500.0)
    assert ds.measure(clean, noise).usable_ms(-10.0) == pytest.approx(4.229167, abs=1e-5)

    acq, noise = acquire_through(
        channel([(0.0, 1.0), (30.0, 0.33)]), noise_rms=0.02, horizon_ms=500.0, seed=11
    )
    out = ds.measure(acq, noise)
    reading = out.at(-10.0)
    assert not out.truncated, "this is the negligible-noise path, and it still must not be exact"
    lo, hi = reading.ms_interval
    assert lo < 16.0 < hi, f"interval {reading.ms_interval} should span the budget"
    assert ds.exceeds_guard_budget(reading, 16.0) is None
