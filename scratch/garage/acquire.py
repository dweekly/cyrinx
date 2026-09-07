#!/usr/bin/env python3
"""Garage-facing acquisition: swept-sine capture with a declared horizon.

`freqresp.deconvolve_ir` returns exactly 120 ms after the largest peak and
nothing else, and that crop is invisible downstream. Measured through the real
acquisition path, a 150 ms reflection at gain 0.5 produces the same -10 dB
figure as a single tap (0.020833 ms). Since the stage 1G gate exists to find
late energy, a silent crop is the most dangerous failure available to it.

This module keeps `freqresp` untouched and adds what the gate needs: an
observation horizon that is declared rather than assumed, and capture-support
metadata saying whether the recording actually covered it.

Device selection is explicit and never mutates the system default output, so a
session runs at its own fixed level rather than at whatever the machine happens
to be set to.

Sources:
  freqresp   scratch/hw20k/freqresp.py -- make_ess, deconvolve_ir, delay_spread
  ADR 0006   docs/adr/0006-session-initiation.md -- the three association phases
"""

from dataclasses import dataclass

import numpy as np

DEFAULT_HORIZON_MS = 500.0
"""Observation horizon requested by default, in ms after the main tap.

The energy decay curve normalizes to the energy inside the analysed window, so a
shorter window inflates every remaining fraction and pulls crossings earlier.
Measured on a real MacBook speaker-to-microphone capture, the -20 dB figure rises
monotonically with horizon and only settles past about 400 ms:

    horizon    60 ms   90    120    160    200    250    400    700   1000
    -20 dB     12.00  14.58  15.29  15.75  16.33  16.75  17.04  17.06  17.06

At the 120 ms `freqresp.deconvolve_ir` retains, that capture reads 15.29 ms --
below the 16 ms guard budget -- while its converged value is 17.06 ms, above it.
The crop alone flips the gate on real hardware. 500 ms is past convergence for
this path with margin for a more reverberant one; NF-13 measured 82 ms at -20 dB
in a reverberant desk geometry, and a worse room is what the gate is looking for.

A horizon is a claim about what was observed, not about what exists: a reading is
valid with respect to its horizon and promises nothing beyond it. Cells compared
against each other must share one."""

PRE_TAP_MS = 5.0
"""Samples retained before the main tap, matching `freqresp.deconvolve_ir`.

These are the band-limited response's own sidelobes, not noise. Nothing in this
module estimates a noise floor from them."""

MAC_SPEAKERS = "MacBook Pro Speakers"
MAC_MICROPHONE = "MacBook Pro Microphone"

SUPPORT_OK = "ok"
SUPPORT_RECORDING_ENDED_EARLY = "recording-ended-early"
SUPPORT_HORIZON_TRUNCATED = "horizon-truncated"


@dataclass(frozen=True)
class Acquisition:
    """One deconvolved response plus what is known about its support.

    `ir` starts PRE_TAP_MS before the main tap. `peak_idx` indexes the main tap
    within it. `horizon_ms` is how far past the main tap the response actually
    extends, which is at most the requested horizon and may be less when the
    recording ran out.
    """

    ir: np.ndarray
    peak_idx: int
    sr: int
    horizon_ms: float
    requested_horizon_ms: float
    support: str
    detail: str = ""

    @property
    def tail(self):
        """The causal response from the main tap onward."""
        return self.ir[self.peak_idx:]

    @property
    def covers_requested_horizon(self):
        return self.support == SUPPORT_OK


def deconvolve(capture, inv, sr, horizon_ms=DEFAULT_HORIZON_MS, pre_tap_ms=PRE_TAP_MS):
    """Recover the impulse response, retaining a declared observation horizon.

    Same Farina deconvolution as `freqresp.deconvolve_ir` -- convolve the capture
    with the inverse filter and locate the linear response by its energy peak --
    but the causal window is the caller's horizon rather than a fixed 120 ms, and
    the result records whether the recording actually supplied it.
    """
    capture = np.asarray(capture, dtype=np.float64)
    if capture.ndim != 1:
        raise ValueError(f"capture must be 1-D, got shape {capture.shape}")
    if horizon_ms <= 0:
        raise ValueError(f"horizon must be positive, got {horizon_ms}")

    inv = np.asarray(inv, dtype=np.float64)
    capture = capture - capture.mean()
    full = np.convolve(capture, inv, mode="full")
    peak = int(np.argmax(np.abs(full)))

    # Convolution output is fully overlapped -- every inverse-filter sample
    # backed by a recorded sample -- only up to index len(capture) - 1. Past
    # that the filter is ramping down, not the room, and analysing it would
    # repeat the original defect in a new place.
    #
    # Measured from the main tap, not from the recording start: real playback
    # has latency, so the tap sits later than the sweep does and the observable
    # window after it is correspondingly shorter. Measuring from the start
    # overstates support by exactly that latency.
    supported = max(0, len(capture) - peak)

    pre = int(pre_tap_ms * sr / 1000.0)
    want = int(horizon_ms * sr / 1000.0)
    lo = max(0, peak - pre)
    available = min(want, supported)
    hi = peak + available

    if supported >= want:
        support, detail = SUPPORT_OK, ""
    else:
        support = SUPPORT_RECORDING_ENDED_EARLY
        detail = (
            f"recording supports {1000.0 * supported / sr:.1f} ms after the main "
            f"tap; {horizon_ms:.1f} ms was requested"
        )

    ir = full[lo:hi]
    return Acquisition(
        ir=ir,
        peak_idx=peak - lo,
        sr=sr,
        horizon_ms=1000.0 * (len(ir) - (peak - lo)) / sr,
        requested_horizon_ms=horizon_ms,
        support=support,
        detail=detail,
    )


def from_freqresp_crop(ir, peak_idx, sr):
    """Wrap an already-cropped `freqresp.deconvolve_ir` result.

    Its 120 ms window is recorded as a horizon truncation, so a reading derived
    from it can never claim to have looked further than it did.
    """
    ir = np.asarray(ir, dtype=np.float64)
    horizon = 1000.0 * (len(ir) - peak_idx) / sr
    return Acquisition(
        ir=ir,
        peak_idx=peak_idx,
        sr=sr,
        horizon_ms=horizon,
        requested_horizon_ms=horizon,
        support=SUPPORT_HORIZON_TRUNCATED,
        detail=(
            "analysis crop from freqresp.deconvolve_ir; energy beyond "
            f"{horizon:.1f} ms after the main tap was discarded before analysis"
        ),
    )


# --- Hardware acquisition ---------------------------------------------------
#
# Imported lazily so every offline test in this package runs without an audio
# stack present.


def _device_index(name, kind):
    import sounddevice as sd

    for index, device in enumerate(sd.query_devices()):
        channels = device["max_input_channels" if kind == "input" else "max_output_channels"]
        if device["name"] == name and channels > 0:
            return index
    raise RuntimeError(f"no {kind} device named {name!r}")


def play_and_record(wave, sr, output_device, input_device, tail_s=1.0, input_channels=1):
    """Play `wave` on a named output while recording a named input.

    Neither device is made the system default. `tail_s` of extra recording is
    retained after playback so a reverberant tail is not clipped by the stream
    ending, which is the stream-end fade NEGATIVE_FINDINGS entry 5 describes.
    """
    import sounddevice as sd

    wave = np.asarray(wave, dtype=np.float32)
    frames = len(wave) + int(tail_s * sr)
    padded = np.zeros(frames, dtype=np.float32)
    padded[: len(wave)] = wave

    recorded = sd.playrec(
        padded.reshape(-1, 1),
        samplerate=sr,
        channels=input_channels,
        device=(_device_index(input_device, "input"), _device_index(output_device, "output")),
        dtype="float32",
        blocking=True,
    )
    return np.asarray(recorded, dtype=np.float64).reshape(frames, input_channels)


def record(duration_s, sr, input_device, input_channels=1):
    """Record from a named input with nothing playing -- the room-tone reference.

    Must be taken at the same gain as the sweep it accompanies; ADR 0006 makes
    this a required local phase rather than optional bookkeeping.
    """
    import sounddevice as sd

    frames = int(duration_s * sr)
    captured = sd.rec(
        frames,
        samplerate=sr,
        channels=input_channels,
        device=_device_index(input_device, "input"),
        dtype="float32",
        blocking=True,
    )
    return np.asarray(captured, dtype=np.float64).reshape(frames, input_channels)
