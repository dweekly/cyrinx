#!/usr/bin/env python3
"""Delay spread by Lundeby truncation, with explicit validity (plan stage G0).

The stage 1G gate reads threshold-specific times at which the remaining
backward-integrated energy crosses -10, -15 and -20 dB. It is a spending
decision taken after replay diagnostics, not a claim that a position is
unrecoverable, so a reading that cannot support the decision must say so rather
than produce a number.

Four things decide whether that number is trustworthy, and each is explicit here:

1. **The noise reference is raw capture PCM, processed identically.** Room tone
   and the impulse response live in different domains -- the default inverse
   filter has energy 8.3359e-05, so stationary white noise moves by -40.79 dB
   under full overlap. Raw noise variance compared against IR energy would
   overstate the floor by roughly 40 dB. `NoiseReference` performs the
   conversion and refuses inputs too short to do it.
2. **Truncation biases the estimate short, and the bias is reported.** An
   exponential decay whose true -10 dB crossing is 16.500 ms reads 15.882 ms
   after 1% of its energy is discarded and the curve renormalized -- across the
   16 ms guard budget. Each reading carries an interval, and the gate abstains
   when the interval straddles the budget.
3. **Sparse channels take an explicit no-truncation path.** Lundeby regresses a
   diffuse decay; one tap, or two taps separated by silence, offers none, and a
   noiseless capture has no finite crosspoint. A regression that cannot be
   supported is a distinct outcome from one that succeeded, and neither is
   automatically "low SNR".
4. **Validity is relative to a declared observation horizon.** A reading says
   what it looked at and promises nothing beyond it. See `acquire.py`.

`freqresp.delay_spread` is called on the accepted integration input rather than
having its crossing arithmetic reimplemented, so the two cannot drift.

Sources:
  freqresp    scratch/hw20k/freqresp.py
  NF-<n>      docs/NEGATIVE_FINDINGS.md, numbered entry
  plan        docs/research/delay-spread-readout-plan.md
"""

import os
import sys
from dataclasses import dataclass, field

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hw20k"))

import freqresp  # noqa: E402

import acquire  # noqa: E402

THRESHOLDS_DB = (-10.0, -15.0, -20.0)
"""Fixed to what `freqresp.delay_spread` reports, so figures stay comparable
with the existing tables. -15 dB is retained for that reason even though the
stage 1G gate reads -10 and -20."""

NOISE_MARGIN_DB = 10.0
"""Margin above the processed noise floor at which the decay regression stops
and the truncation point is taken. Lundeby practice recommends 5-10 dB of safety
above the floor before truncating; the conservative end is used because this
feeds a gate rather than a report."""

NEGLIGIBLE_NOISE_FRACTION = 1e-3
"""Integrated noise energy across the analysed window, as a fraction of the
window's total energy, below which no truncation is applied and crossings are
reported directly.

The comparison is integrated against integrated. Noise power is per sample and
the energy decay curve is a running total, so comparing a per-sample noise figure
against a peak sample says nothing about whether noise can move a crossing.

The deepest reported threshold, -20 dB, sits where 1% of the window's energy
remains. Noise an order of magnitude below that -- 0.1% of total -- cannot move
it appreciably, which is where this constant comes from.

This is also the sparse-channel and clean-capture path: below it, attempting a
decay regression on a response that has no diffuse decay would fail for reasons
unrelated to noise."""

SMOOTHING_MS = 5.0
"""Block length for smoothing the squared response before regression.

Published implementations initialize broadband analysis with blocks around
30 ms; that is longer than the 16 ms guard budget this feeds and longer than the
reflection delays under test, so it would smooth away the structure the gate
cares about. 5 ms keeps several blocks inside the budget while still averaging
out sample-level variance."""

MIN_REGRESSION_BLOCKS = 8
"""Smoothed blocks required between the peak and the noise margin before a decay
regression is attempted. Fewer than this is not a low-SNR result; it is a
response without enough diffuse decay to fit, reported as `unsupported-fit`."""

MIN_DECAY_SLOPE_DB_PER_MS = 0.01
"""A fit must actually decay. A flat or rising regression means the response is
not a decaying tail, so the crosspoint would be meaningless."""

OK = "ok"
INVALID = "invalid"
NOISE_LIMITED = "noise-limited"
WINDOW_LIMITED = "window-limited"
UNRESOLVED_AT_HORIZON = "unresolved-at-horizon"
UNSUPPORTED_FIT = "unsupported-fit"


class NoiseReferenceError(ValueError):
    """The room-tone reference cannot be converted into the response domain."""


@dataclass(frozen=True)
class NoiseReference:
    """Room-tone PCM converted into the deconvolved response domain.

    Takes the raw capture and the *same* inverse filter used for the sweep, and
    reports mean power per sample after identical processing. Only the fully
    overlapped interior of the convolution is used, so the estimate is not
    diluted by the filter's ramp-in and ramp-out.

    The room tone must therefore be longer than the inverse filter. With the
    default 6.0 s filter that means a room tone of at least about 7 s, which is
    longer than ADR 0006's illustrative 2 s figure -- an acquisition
    requirement, not a tuning knob.
    """

    power: float
    samples_used: int
    duration_s: float

    @classmethod
    def from_pcm(cls, pcm, inv, sr):
        pcm = np.asarray(pcm, dtype=np.float64)
        inv = np.asarray(inv, dtype=np.float64)
        if pcm.ndim != 1:
            raise NoiseReferenceError(f"room tone must be 1-D, got shape {pcm.shape}")
        if not np.all(np.isfinite(pcm)):
            raise NoiseReferenceError("room tone contains non-finite samples")
        if len(pcm) <= len(inv):
            raise NoiseReferenceError(
                f"room tone is {len(pcm) / sr:.2f} s but the inverse filter is "
                f"{len(inv) / sr:.2f} s; a fully overlapped interior needs a "
                "longer room tone"
            )
        processed = np.convolve(pcm - pcm.mean(), inv, mode="valid")
        if len(processed) == 0:
            raise NoiseReferenceError("no fully overlapped interior in the room tone")
        return cls(
            power=float(np.mean(processed**2)),
            samples_used=len(processed),
            duration_s=len(pcm) / sr,
        )

    @classmethod
    def negligible(cls):
        """A reference asserting there is no measurable noise.

        For digitally generated fixtures only. A capture from hardware always has
        a room tone; this exists so a synthetic channel need not fabricate one.
        """
        return cls(power=0.0, samples_used=0, duration_s=0.0)


@dataclass(frozen=True)
class Reading:
    """One threshold's result, its uncertainty, and why it is or is not usable."""

    threshold_db: float
    status: str
    ms: float = None
    ms_interval: tuple = None
    detail: str = ""
    truncated: bool = False

    @property
    def usable(self):
        """Whether the figure may be read at all. Not the same as gate-usable.

        A truncated reading has a number worth reporting but no validated bound
        on the energy truncation discarded, so `exceeds_guard_budget` abstains on
        it separately.
        """
        return self.status == OK and self.ms is not None and np.isfinite(self.ms)


@dataclass(frozen=True)
class DelaySpread:
    """A full readout, with every diagnostic needed to explain an abstention."""

    readings: dict
    horizon_ms: float
    support: str
    noise_power: float
    truncation_ms: float = None
    fit_slope_db_per_ms: float = None
    fit_interval_ms: tuple = None
    truncated: bool = False
    status: str = OK
    detail: str = ""
    notes: tuple = field(default_factory=tuple)

    def at(self, threshold_db):
        return self.readings[threshold_db]

    def usable_ms(self, threshold_db):
        reading = self.readings.get(threshold_db)
        return reading.ms if reading is not None and reading.usable else None


def _all(status, detail, horizon_ms, support, noise_power, **kw):
    return DelaySpread(
        readings={db: Reading(db, status, None, None, detail) for db in THRESHOLDS_DB},
        horizon_ms=horizon_ms,
        support=support,
        noise_power=noise_power,
        status=status,
        detail=detail,
        **kw,
    )


def _crossings(tail, sr):
    """Threshold crossing times, via freqresp so the arithmetic has one home."""
    return freqresp.delay_spread(tail, 0, sr=sr)


def _fit_decay(tail, sr, noise_power):
    """Regress the smoothed decay; return (slope_db_per_ms, intercept_db, interval).

    Returns None when the response does not offer enough decay above the noise
    margin to fit -- a statement about the response, not about SNR.
    """
    block = max(1, int(SMOOTHING_MS * sr / 1000.0))
    n_blocks = len(tail) // block
    if n_blocks < MIN_REGRESSION_BLOCKS:
        return None
    energy = (tail[: n_blocks * block] ** 2).reshape(n_blocks, block).mean(axis=1)
    with np.errstate(divide="ignore"):
        energy_db = 10.0 * np.log10(np.maximum(energy, 1e-300))
    floor_db = 10.0 * np.log10(max(noise_power, 1e-300))
    usable = np.flatnonzero(energy_db > floor_db + NOISE_MARGIN_DB)
    if len(usable) < MIN_REGRESSION_BLOCKS:
        return None
    last = int(usable[-1])
    times_ms = (np.arange(last + 1) + 0.5) * block * 1000.0 / sr
    slope, intercept = np.polyfit(times_ms, energy_db[: last + 1], 1)
    if -slope < MIN_DECAY_SLOPE_DB_PER_MS:
        return None
    return float(slope), float(intercept), (float(times_ms[0]), float(times_ms[-1]))


def measure(acquisition, noise, thresholds_db=THRESHOLDS_DB):
    """Delay spread for one acquisition against one room-tone reference.

    `acquisition` is an `acquire.Acquisition`; `noise` is a `NoiseReference`
    built from raw room-tone PCM and the same inverse filter. There is no code
    path that infers noise from the response itself.
    """
    if not isinstance(acquisition, acquire.Acquisition):
        raise TypeError("measure() takes an acquire.Acquisition")
    if not isinstance(noise, NoiseReference):
        raise TypeError(
            "measure() requires a NoiseReference; a room-tone capture is a "
            "required input, not an optional one"
        )

    sr = acquisition.sr
    tail = acquisition.tail
    horizon, support = acquisition.horizon_ms, acquisition.support
    notes = (acquisition.detail,) if acquisition.detail else ()

    if not np.all(np.isfinite(tail)):
        return _all(INVALID, "response contains non-finite samples", horizon, support, noise.power, notes=notes)
    if len(tail) < 2:
        return _all(INVALID, "response has no causal tail", horizon, support, noise.power, notes=notes)
    peak_energy = float(np.max(tail**2))
    if peak_energy <= 0.0:
        return _all(INVALID, "response is silent after the main tap", horizon, support, noise.power, notes=notes)

    tail_energy = float(np.sum(tail**2))
    integrated_noise = noise.power * len(tail)
    noise_fraction = integrated_noise / max(tail_energy, 1e-300)
    negligible = noise_fraction < NEGLIGIBLE_NOISE_FRACTION

    truncation_ms, slope, fit_interval, truncated = None, None, None, False
    integrand = tail

    if negligible:
        notes = notes + (
            f"integrated noise is {noise_fraction:.2e} of window energy; "
            "no truncation applied",
        )
    else:
        fit = _fit_decay(tail, sr, noise.power)
        if fit is None:
            return _all(
                UNSUPPORTED_FIT,
                "no decay above the noise margin long enough to regress; the "
                "response has no diffuse tail to fit",
                horizon, support, noise.power, notes=notes,
            )
        slope, intercept, fit_interval = fit
        floor_db = 10.0 * np.log10(max(noise.power, 1e-300))
        crosspoint_ms = (floor_db + NOISE_MARGIN_DB - intercept) / slope
        truncation_ms = float(min(max(crosspoint_ms, 0.0), 1000.0 * len(tail) / sr))
        cut = int(truncation_ms * sr / 1000.0)
        if cut < MIN_REGRESSION_BLOCKS:
            return _all(
                NOISE_LIMITED,
                f"decay meets the noise floor {truncation_ms:.2f} ms after the "
                "main tap, leaving nothing to integrate",
                horizon, support, noise.power,
                truncation_ms=truncation_ms, fit_slope_db_per_ms=slope,
                fit_interval_ms=fit_interval, notes=notes,
            )
        integrand = tail[:cut]
        truncated = True

    primary = _crossings(integrand, sr)
    # Sensitivity of each crossing to where the tail was cut. This is a
    # diagnostic showing how much the cut point moves the answer -- it is NOT a
    # bound on the energy truncation discarded, so the gate does not treat it as
    # one; see exceeds_guard_budget.
    if truncated:
        span = max(1, int(0.1 * len(integrand)))
        shorter = _crossings(tail[: max(MIN_REGRESSION_BLOCKS, len(integrand) - span)], sr)
        longer = _crossings(tail[: min(len(tail), len(integrand) + span)], sr)
    else:
        shorter = longer = primary

    observed_ms = 1000.0 * len(integrand) / sr
    readings = {}
    for db in thresholds_db:
        key = f"{int(db)}dB"
        value = primary[key]
        lo = min(value, shorter[key], longer[key])
        hi = max(value, shorter[key], longer[key])
        at_edge = value >= observed_ms - (1000.0 / sr)
        if at_edge and not truncated and support == acquire.SUPPORT_OK:
            readings[db] = Reading(
                db, UNRESOLVED_AT_HORIZON, None, None,
                f"decay had not reached {db:.0f} dB by the {horizon:.1f} ms horizon",
            )
        elif at_edge:
            readings[db] = Reading(
                db, WINDOW_LIMITED, None, None,
                f"decay does not reach {db:.0f} dB within the "
                f"{observed_ms:.1f} ms analysed",
            )
        else:
            readings[db] = Reading(
                db, OK, float(value), (float(lo), float(hi)), truncated=truncated
            )

    if any(r.usable for r in readings.values()):
        status = OK
        unavailable = [
            f"{db:.0f} dB {readings[db].status}" for db in sorted(readings) if not readings[db].usable
        ]
        detail = ("thresholds unavailable: " + ", ".join(unavailable)) if unavailable else ""
    else:
        statuses = {r.status for r in readings.values()}
        status = statuses.pop() if len(statuses) == 1 else WINDOW_LIMITED
        detail = "no threshold produced a reportable figure"

    return DelaySpread(
        readings=readings, horizon_ms=horizon, support=support,
        noise_power=noise.power, truncation_ms=truncation_ms,
        fit_slope_db_per_ms=slope, fit_interval_ms=fit_interval,
        truncated=truncated, status=status, detail=detail, notes=notes,
    )


def exceeds_guard_budget(reading, budget_ms):
    """Whether a reading places late energy beyond the declared guard budget.

    Takes a `Reading`, never a bare number, so status can be inspected and a
    non-finite value cannot be silently compared. Returns True only when the
    whole uncertainty interval is beyond the budget, False only when all of it is
    within, and None when the reading is unusable or its interval straddles the
    budget -- a number whose uncertainty spans the decision is not evidence for
    the decision.
    """
    if reading is None:
        return None
    if not isinstance(reading, Reading):
        raise TypeError(
            "exceeds_guard_budget() takes a Reading so it can inspect status; "
            f"got {type(reading).__name__}"
        )
    if not reading.usable:
        return None
    if reading.truncated:
        # The interval is a cut-point sensitivity, not a bound on the energy
        # truncation removed, and truncation biases the figure short. Without a
        # validated omitted-energy bound a truncated reading cannot support a
        # budget decision in either direction. Truncation only engages when
        # capture noise approaches the excitation amplitude, so this abstains on
        # degenerate captures rather than on ordinary ones.
        return None
    lo, hi = reading.ms_interval or (reading.ms, reading.ms)
    if not (np.isfinite(lo) and np.isfinite(hi)):
        return None
    if lo > budget_ms:
        return True
    if hi <= budget_ms:
        return False
    return None
