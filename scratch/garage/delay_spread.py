#!/usr/bin/env python3
"""Schroeder delay-spread readout with explicit validity states (plan stage G0).

``freqresp.delay_spread`` already computes the Schroeder energy-decay curve at
-10/-15/-20 dB, and that is the convention the historical figures use, so the
numbers here are the same numbers -- ``test_delay_spread.py`` pins the agreement
on synthetic impulse responses.

What this module adds is the part stage 1G needs and ``freqresp`` does not
provide: a reading may come back as ``noise-limited`` or ``window-limited``
instead of a figure. That matters because ``freqresp.delay_spread`` returns the
analysis-window length when the decay never reaches a threshold, which is a
number, looks like a measurement, and is not one. A cell whose reading is not
``ok`` at a threshold is not evidence for the gate at that threshold.

Do not use ``characterize.py`` for this. It reports the last individual sample
above a peak-relative -30 dB threshold, which is a different quantity, and NF-1
records the -30 dB point as the wrong basis for a guard decision.

Sources:
  freqresp      scratch/hw20k/freqresp.py, delay_spread()
  NF-<n>        docs/NEGATIVE_FINDINGS.md, numbered entry
  ISO 3382-1    the decay-range-above-noise requirement cited below
"""

from dataclasses import dataclass

import numpy as np

THRESHOLDS_DB = (-10.0, -15.0, -20.0)
"""Matches freqresp.delay_spread so the readings stay comparable with the
existing tables. -15 dB is retained for that reason even though the stage 1G
gate reads -10 and -20."""

NOISE_HEADROOM_DB = 10.0
"""Decibels of headroom a decay must have above the measurement noise floor
before a threshold may be evaluated. ISO 3382-1 requires the evaluation range to
sit 10 dB clear of the noise floor for a decay measurement to be reportable;
the same requirement is applied per threshold here, so a -10 dB reading can be
valid on a capture where the -20 dB reading is not."""

MIN_NOISE_SAMPLES = 64
"""Samples required before the main tap to estimate the noise floor from the
acausal region. Below this the estimate is too short to trust and the reading is
``invalid`` rather than silently noise-blind."""

MIN_WINDOW_SAMPLES = 256
"""Samples required after the main tap for a decay curve to mean anything. At
48 kHz this is 5.3 ms, well under any guard we would consider, so it only
rejects captures that are truncated or mis-anchored."""

WINDOW_USABLE_FRACTION = 0.5
"""Fraction of the analysis window within which a threshold crossing must occur
for the figure to be reportable.

The backward-integrated curve reaches zero at the window end by construction --
there is no energy left to integrate -- so *every* threshold is crossed
eventually regardless of the channel, and a crossing near the end measures the
window rather than the room. Requiring the crossing in the first half keeps the
remaining integration support at least as long as the elapsed decay. This is why
a long decay reads as ``window-limited`` here while freqresp.delay_spread
returns a plausible-looking number."""

OK = "ok"
INVALID = "invalid"
NOISE_LIMITED = "noise-limited"
WINDOW_LIMITED = "window-limited"


@dataclass(frozen=True)
class Reading:
    """One threshold's delay-spread result and why it is or is not usable."""

    threshold_db: float
    status: str
    ms: float = None
    detail: str = ""

    @property
    def usable(self):
        return self.status == OK


@dataclass(frozen=True)
class DelaySpread:
    """A full readout: one Reading per threshold, plus what it was measured on."""

    readings: dict
    peak_to_noise_db: float = None
    window_ms: float = None
    status: str = OK
    detail: str = ""

    def at(self, threshold_db):
        return self.readings[threshold_db]

    def usable_ms(self, threshold_db):
        """The figure at ``threshold_db``, or None when it is not usable.

        Callers deciding the stage 1G gate must use this rather than reading
        ``ms`` directly, so an unusable reading cannot be mistaken for a short
        delay spread.
        """
        reading = self.readings.get(threshold_db)
        return reading.ms if reading is not None and reading.usable else None


def _invalid(detail, window_ms=None):
    return DelaySpread(
        readings={
            db: Reading(db, INVALID, None, detail) for db in THRESHOLDS_DB
        },
        window_ms=window_ms,
        status=INVALID,
        detail=detail,
    )


def measure(ir, peak_idx, sr, thresholds_db=THRESHOLDS_DB):
    """Schroeder delay spread from ``ir``, anchored at ``peak_idx``.

    ``ir`` is an impulse response, normally the deconvolved sine sweep.
    ``peak_idx`` is the index of the main tap. The noise floor is estimated from
    the acausal region before the main tap, which for a swept-sine deconvolution
    contains measurement noise rather than channel response.
    """
    ir = np.asarray(ir, dtype=np.float64)
    if ir.ndim != 1:
        return _invalid(f"impulse response must be 1-D, got shape {ir.shape}")
    if not np.all(np.isfinite(ir)):
        return _invalid("impulse response contains non-finite samples")
    if not 0 <= peak_idx < len(ir):
        return _invalid(f"peak index {peak_idx} outside impulse response")

    tail = ir[peak_idx:]
    window_ms = 1000.0 * len(tail) / sr
    if len(tail) < MIN_WINDOW_SAMPLES:
        return _invalid(
            f"only {len(tail)} samples after the main tap, need "
            f"{MIN_WINDOW_SAMPLES}",
            window_ms,
        )
    if peak_idx < MIN_NOISE_SAMPLES:
        return _invalid(
            f"only {peak_idx} samples before the main tap, need "
            f"{MIN_NOISE_SAMPLES} to estimate the noise floor",
            window_ms,
        )

    noise_rms = float(np.sqrt(np.mean(ir[:peak_idx] ** 2)))
    peak = float(np.max(np.abs(tail)))
    if peak <= 0.0:
        return _invalid("impulse response is silent after the main tap", window_ms)
    peak_to_noise_db = (
        float("inf") if noise_rms <= 0.0 else 20.0 * np.log10(peak / noise_rms)
    )

    # Schroeder backwards-integrated energy decay, normalized to its own start.
    # Same formulation as freqresp.delay_spread; test_delay_spread.py pins the
    # two to identical figures on synthetic responses.
    energy = tail**2
    edc = np.cumsum(energy[::-1])[::-1]
    edc = edc / (edc[0] + 1e-300)
    edc_db = 10.0 * np.log10(edc + 1e-300)

    readings = {}
    for db in thresholds_db:
        required_db = abs(db) + NOISE_HEADROOM_DB
        if peak_to_noise_db < required_db:
            readings[db] = Reading(
                db,
                NOISE_LIMITED,
                None,
                f"peak is {peak_to_noise_db:.1f} dB over the noise floor; "
                f"{required_db:.1f} dB needed to evaluate {db:.0f} dB",
            )
            continue
        crossed = np.flatnonzero(edc_db < db)
        usable_span = int(WINDOW_USABLE_FRACTION * len(tail))
        if len(crossed) == 0 or crossed[0] >= usable_span:
            readings[db] = Reading(
                db,
                WINDOW_LIMITED,
                None,
                f"decay does not reach {db:.0f} dB within the usable "
                f"{WINDOW_USABLE_FRACTION:.0%} of the {window_ms:.1f} ms window",
            )
            continue
        readings[db] = Reading(db, OK, float(crossed[0] / sr * 1000.0))

    status = OK if any(r.usable for r in readings.values()) else NOISE_LIMITED
    detail = "" if status == OK else "no threshold produced a reportable figure"
    return DelaySpread(readings, peak_to_noise_db, window_ms, status, detail)


def exceeds_guard_budget(reading_ms, budget_ms):
    """Whether measured late energy sits beyond the declared guard budget.

    Returns None when ``reading_ms`` is None, i.e. when the reading was not
    usable: the gate abstains rather than treating a missing measurement as a
    short delay spread.
    """
    if reading_ms is None:
        return None
    return reading_ms > budget_ms
