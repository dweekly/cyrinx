#!/usr/bin/env python3
"""Dry-run-first, low-level Mac-speaker to Android-microphone tone check.

The default invocation constructs and prints a content-addressed acquisition
plan.  It does not import the HIL harness, run ADB, access an audio device, or
change system volume.  Physical execution requires ``--execute``, a geometry
label, an authorization note, and an explicit artifact directory.

The physical runner is intentionally narrow:

* 48 kHz PCM only;
* Mac output volume no higher than the currently reviewed 14 percent staircase
  step and waveform peak no higher than 0.18;
* one capture with only the left digital output active, followed by a safety
  gate, then one capture with only the right output active; and
* both Android logical microphone channels retained in their original order.

This is an end-to-end relative channel check, not an SPL instrument.  It cannot
separate loudspeaker, room, microphone, analog gain, resampling, or OS effects.
Nor can it prove that two logical input channels are independent physical
microphones.  The report therefore never permits a diversity or MIMO claim.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Callable, Iterator, Mapping, Sequence
import uuid

import numpy as np


SAMPLE_RATE = 48_000
LOCK_PATH = Path("/private/tmp/cyrinx-android-audio.lock")
SCHEMA = "cyrinx.mac-to-android-tone-check.v1"
ANDROID_PACKAGE = "com.dweekly.cyrinxhil"
EXPECTED_MAC_OUTPUT_NAME = "MacBook Pro Speakers"
EXPECTED_MAC_OUTPUT_UID = "BuiltInSpeakerDevice"
SWITCH_AUDIO_SOURCE = "/opt/homebrew/bin/SwitchAudioSource"
ROUTE_SIGNATURE_SCHEMA = "cyrinx.android-capture-route-signature.v1"

MAX_OUTPUT_VOLUME_PERCENT = 14
DEFAULT_OUTPUT_VOLUME_PERCENT = 8
MAX_DIGITAL_PEAK = 0.18
DEFAULT_DIGITAL_PEAK = 0.18

LEAD_SILENCE_S = 0.50
SYNC_DURATION_S = 0.12
POST_SYNC_GUARD_S = 0.15
PRE_END_SYNC_GUARD_S = 0.15
TAIL_SILENCE_S = 0.50
CAPTURE_PRE_ROLL_S = 0.40
CAPTURE_POST_ROLL_S = 0.40
TONE_GUARD_S = 0.035
TONE_RAMP_S = 0.010
# Bound all route/APK validation performed after AudioRecord starts and before
# the speaker is energized. Callers fail closed if this budget is exceeded.
CAPTURE_SETUP_BUDGET_S = 4.0
TONE_EDGE_TRIM_S = 0.020

MINIMUM_SYNC_SCORE = 0.08
MINIMUM_SYNC_PSR_DB = 8.0
MAXIMUM_CLOCK_ERROR_PPM = 5_000.0
MAXIMUM_RX_ORIGIN_DISAGREEMENT_SAMPLES = 256
MINIMUM_FUNDAMENTAL_SNR_DB = 12.0

ABSOLUTE_SAMPLE_PEAK_LIMIT = 0.985
CLIPPED_SAMPLE_FRACTION_LIMIT = 1e-5
WITHIN_STAGE_GAIN_JUMP_LIMIT_DB = 4.0
MAXIMUM_THD_DB = -25.0
MAXIMUM_IMD_DB = -30.0
POST_SIGNAL_NOISE_INCREASE_LIMIT_DB = 6.0

SINGLE_TONES_HZ = (997.0, 3_001.0, 7_001.0, 12_001.0, 17_003.0, 21_001.0)
IMD_PAIRS_HZ = ((997.0, 1_499.0), (18_001.0, 19_001.0))


class ToneCheckError(RuntimeError):
    """Raised when a plan or physical acquisition violates its contract."""


def assert_capture_setup_deadline(
    capture_request_monotonic_ns: int,
    playback_candidate_monotonic_ns: int,
) -> float:
    """Return setup time, refusing playback after the bounded setup window."""

    if playback_candidate_monotonic_ns < capture_request_monotonic_ns:
        raise ToneCheckError("monotonic clock moved backwards during capture setup")
    elapsed_s = (
        playback_candidate_monotonic_ns - capture_request_monotonic_ns
    ) / 1_000_000_000.0
    if elapsed_s > CAPTURE_SETUP_BUDGET_S:
        raise ToneCheckError(
            "capture setup exceeded the pre-playback budget; refusing playback "
            f"({elapsed_s:.3f}s > {CAPTURE_SETUP_BUDGET_S:.3f}s)"
        )
    return elapsed_s


@dataclasses.dataclass(frozen=True)
class ToneSegment:
    label: str
    kind: str
    start_sample: int
    end_sample: int
    frequencies_hz: tuple[float, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "kind": self.kind,
            "start_sample": self.start_sample,
            "end_sample": self.end_sample,
            "frequencies_hz": list(self.frequencies_hz),
        }


@dataclasses.dataclass(frozen=True)
class ToneProgram:
    sample_rate_hz: int
    digital_peak: float
    mono: np.ndarray
    sync_marker: np.ndarray
    start_sync_sample: int
    tone_probe_sample: int
    end_sync_sample: int
    segments: tuple[ToneSegment, ...]

    @property
    def total_samples(self) -> int:
        return int(len(self.mono))

    @property
    def sync_separation_samples(self) -> int:
        return self.end_sync_sample - self.start_sync_sample

    def stereo_for_tx(self, tx_index: int) -> np.ndarray:
        if tx_index not in (0, 1):
            raise ValueError("tx_index must be 0 or 1")
        value = np.zeros((self.total_samples, 2), dtype=np.float32)
        value[:, tx_index] = self.mono
        return value

    def manifest(self) -> dict[str, Any]:
        return {
            "sample_rate_hz": self.sample_rate_hz,
            "digital_peak": self.digital_peak,
            "total_samples": self.total_samples,
            "duration_seconds": self.total_samples / self.sample_rate_hz,
            "start_sync_sample": self.start_sync_sample,
            "end_sync_sample": self.end_sync_sample,
            "sync_separation_samples": self.sync_separation_samples,
            "tone_probe_sample": self.tone_probe_sample,
            "sync_marker_samples": int(len(self.sync_marker)),
            "sync_marker_peak": float(np.max(np.abs(self.sync_marker))),
            "tone_guard_samples": _samples(TONE_GUARD_S),
            "tone_edge_ramp_samples": _samples(TONE_RAMP_S),
            "tone_component_peak_policy": (
                "single=digital_peak; imd-pair=half-digital-peak-per-component"
            ),
            "mono_sha256": array_sha256(self.mono),
            "sync_sha256": array_sha256(self.sync_marker),
            "segments": [segment.to_dict() for segment in self.segments],
        }


@dataclasses.dataclass(frozen=True)
class AlignmentEstimate:
    valid: bool
    capture_origin_sample: int
    start_marker_sample: int
    end_marker_sample: int
    sample_scale: float
    clock_error_ppm: float
    start_score: float
    end_score: float
    peak_to_sidelobe_db: float
    uncertainty_samples: int
    reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True)
class MacVolumeState:
    output_volume_percent: int
    output_muted: bool


@dataclasses.dataclass(frozen=True)
class MacOutputEndpoint:
    portaudio_index: int
    name: str
    hostapi_index: int
    max_output_channels: int
    default_sample_rate_hz: float
    is_default_output: bool

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True)
class MacDefaultOutput:
    name: str
    uid: str
    device_id: str

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class VolumeTransaction:
    prior: MacVolumeState
    requested: MacVolumeState
    realized: MacVolumeState | None = None
    restored: MacVolumeState | None = None
    endpoint_before_set: MacOutputEndpoint | None = None
    endpoint_after_set: MacOutputEndpoint | None = None
    endpoint_before_restore: MacOutputEndpoint | None = None
    endpoint_after_restore: MacOutputEndpoint | None = None
    original_default_output: MacDefaultOutput | None = None
    selected_default_output: MacDefaultOutput | None = None
    restored_default_output: MacDefaultOutput | None = None
    original_default_volume: MacVolumeState | None = None
    original_default_volume_restored: MacVolumeState | None = None


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def sha256_bytes(value: bytes | bytearray | memoryview) -> str:
    digest = hashlib.sha256()
    digest.update(value)
    return digest.hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _require_sha256(value: str, label: str) -> str:
    normalized = str(value).strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", normalized):
        raise ValueError(f"{label} must be a 64-character hexadecimal SHA-256")
    return normalized


def _require_nonempty(value: str, label: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{label} must be nonempty")
    return normalized


def array_sha256(value: np.ndarray) -> str:
    canonical = np.ascontiguousarray(value, dtype="<f4")
    return sha256_bytes(memoryview(canonical).cast("B"))


def _samples(seconds: float) -> int:
    return round(seconds * SAMPLE_RATE)


def _db20(numerator: float, denominator: float = 1.0) -> float:
    floor = np.finfo(float).tiny
    return float(20.0 * math.log10(max(float(numerator), floor) / max(float(denominator), floor)))


def _finite(value: float, fallback: float = -600.0) -> float:
    return float(value) if math.isfinite(float(value)) else fallback


def _squared_sine_ramp(sample_count: int) -> np.ndarray:
    if sample_count <= 0:
        raise ValueError("edge ramp requires a positive sample count")
    return np.sin(
        np.linspace(0.0, np.pi / 2.0, sample_count, endpoint=False, dtype=np.float64)
    ) ** 2


def build_sync_marker(digital_peak: float) -> np.ndarray:
    """Build a low-amplitude, click-free deterministic chirp marker."""

    peak = min(0.06, digital_peak / 3.0)
    count = _samples(SYNC_DURATION_S)
    time_axis = np.arange(count, dtype=np.float64) / SAMPLE_RATE
    f0 = 650.0
    f1 = 9_050.0
    phase = 2 * np.pi * (
        f0 * time_axis + 0.5 * (f1 - f0) * time_axis * time_axis / SYNC_DURATION_S
    )
    marker = np.sin(phase)
    ramp_count = _samples(0.012)
    ramp = _squared_sine_ramp(ramp_count)
    marker[:ramp_count] *= ramp
    marker[-ramp_count:] *= ramp[::-1]
    marker *= peak / max(float(np.max(np.abs(marker))), np.finfo(float).tiny)
    return marker.astype(np.float32)


def _tone_schedule(tone_start_sample: int) -> tuple[ToneSegment, ...]:
    guard = _samples(TONE_GUARD_S)
    cursor = tone_start_sample + guard
    result: list[ToneSegment] = []
    for frequency in SINGLE_TONES_HZ:
        duration = _samples(0.12)
        result.append(
            ToneSegment(
                label=f"single-{frequency:.0f}hz",
                kind="single",
                start_sample=cursor,
                end_sample=cursor + duration,
                frequencies_hz=(frequency,),
            )
        )
        cursor += duration + guard
    for first, second in IMD_PAIRS_HZ:
        duration = _samples(0.18)
        result.append(
            ToneSegment(
                label=f"imd-{first:.0f}-{second:.0f}hz",
                kind="imd-pair",
                start_sample=cursor,
                end_sample=cursor + duration,
                frequencies_hz=(first, second),
            )
        )
        cursor += duration + guard
    return tuple(result)


def _build_tone_probe(
    digital_peak: float,
) -> tuple[np.ndarray, tuple[ToneSegment, ...]]:
    """Render the deterministic tone schedule without importing hardware code.

    ``_tone_schedule`` is the single source of truth for both synthesis and
    analysis metadata.  Each single-tone segment uses the requested peak.  An
    IMD pair assigns half of that peak to each component, bounding their sum by
    the same requested peak.  Ten-millisecond raised-cosine edges suppress
    clicks while remaining outside the analysis core's 20 ms trim.
    """

    segments = _tone_schedule(0)
    guard_samples = _samples(TONE_GUARD_S)
    total_samples = segments[-1].end_sample + guard_samples
    probe = np.zeros(total_samples, dtype=np.float64)
    ramp_samples = _samples(TONE_RAMP_S)

    for segment in segments:
        count = segment.end_sample - segment.start_sample
        if count <= 2 * ramp_samples:
            raise AssertionError("tone segment is too short for its edge ramps")
        positions = np.arange(segment.start_sample, segment.end_sample, dtype=np.float64)
        component_peak = digital_peak / len(segment.frequencies_hz)
        value = np.zeros(count, dtype=np.float64)
        for frequency in segment.frequencies_hz:
            value += component_peak * np.sin(2 * np.pi * frequency * positions / SAMPLE_RATE)

        ramp = _squared_sine_ramp(ramp_samples)
        value[:ramp_samples] *= ramp
        value[-ramp_samples:] *= ramp[::-1]
        probe[segment.start_sample : segment.end_sample] = value

    if float(np.max(np.abs(probe))) > digital_peak + 1e-12:
        raise AssertionError("tone probe exceeds its requested digital peak")
    return np.ascontiguousarray(probe, dtype=np.float32), segments


def build_tone_program(digital_peak: float = DEFAULT_DIGITAL_PEAK) -> ToneProgram:
    """Build the complete, inert waveform and exact sample schedule."""

    if not 0 < digital_peak <= MAX_DIGITAL_PEAK:
        raise ValueError(f"digital_peak must be in (0, {MAX_DIGITAL_PEAK}]")
    tone_probe, relative_segments = _build_tone_probe(digital_peak)
    sync = build_sync_marker(digital_peak)
    lead = np.zeros(_samples(LEAD_SILENCE_S), dtype=np.float32)
    post_sync = np.zeros(_samples(POST_SYNC_GUARD_S), dtype=np.float32)
    pre_end_sync = np.zeros(_samples(PRE_END_SYNC_GUARD_S), dtype=np.float32)
    tail = np.zeros(_samples(TAIL_SILENCE_S), dtype=np.float32)

    start_sync_sample = len(lead)
    tone_probe_sample = start_sync_sample + len(sync) + len(post_sync)
    end_sync_sample = tone_probe_sample + len(tone_probe) + len(pre_end_sync)
    mono = np.concatenate((lead, sync, post_sync, tone_probe, pre_end_sync, sync, tail))
    if float(np.max(np.abs(mono))) > digital_peak + 1e-7:
        raise AssertionError("program exceeds its requested digital peak")

    segments = tuple(
        dataclasses.replace(
            segment,
            start_sample=segment.start_sample + tone_probe_sample,
            end_sample=segment.end_sample + tone_probe_sample,
        )
        for segment in relative_segments
    )
    expected_tone_end = segments[-1].end_sample + _samples(TONE_GUARD_S)
    if expected_tone_end != tone_probe_sample + len(tone_probe):
        raise AssertionError("tone segment schedule diverges from rendered probe")
    return ToneProgram(
        sample_rate_hz=SAMPLE_RATE,
        digital_peak=digital_peak,
        mono=np.ascontiguousarray(mono, dtype=np.float32),
        sync_marker=sync,
        start_sync_sample=start_sync_sample,
        tone_probe_sample=tone_probe_sample,
        end_sync_sample=end_sync_sample,
        segments=segments,
    )


def normalized_correlation(signal: np.ndarray, template: np.ndarray) -> np.ndarray:
    """Return absolute zero-mean normalized correlation for every valid lag."""

    x = np.asarray(signal, dtype=np.float64)
    reference = np.asarray(template, dtype=np.float64)
    if x.ndim != 1 or reference.ndim != 1 or len(x) < len(reference):
        raise ValueError("normalized correlation requires 1-D signal >= template")
    reference = reference - np.mean(reference)
    template_energy = float(np.dot(reference, reference))
    if template_energy <= np.finfo(float).tiny:
        raise ValueError("correlation template has zero energy")

    convolution_count = len(x) + len(reference) - 1
    fft_count = 1 << (convolution_count - 1).bit_length()
    numerator_full = np.fft.irfft(
        np.fft.rfft(x, fft_count) * np.fft.rfft(reference[::-1], fft_count),
        fft_count,
    )[:convolution_count]
    numerator = numerator_full[len(reference) - 1 : len(x)]

    prefix = np.concatenate(([0.0], np.cumsum(x)))
    prefix_squared = np.concatenate(([0.0], np.cumsum(x * x)))
    window_sum = prefix[len(reference) :] - prefix[: -len(reference)]
    window_squared = prefix_squared[len(reference) :] - prefix_squared[: -len(reference)]
    window_variance = np.maximum(
        window_squared - window_sum * window_sum / len(reference),
        0.0,
    )
    denominator = np.sqrt(window_variance * template_energy)
    score = np.zeros_like(numerator)
    valid = denominator > np.finfo(float).tiny
    score[valid] = np.abs(numerator[valid]) / denominator[valid]
    return np.clip(score, 0.0, 1.0)


def _nonmaximum_candidates(score: np.ndarray, count: int, radius: int) -> list[int]:
    working = np.array(score, copy=True)
    result: list[int] = []
    for _ in range(count):
        index = int(np.argmax(working))
        if working[index] <= 0:
            break
        result.append(index)
        lo = max(0, index - radius)
        hi = min(len(working), index + radius + 1)
        working[lo:hi] = 0
    return result


def _peak_width(score: np.ndarray, index: int) -> int:
    threshold = score[index] / math.sqrt(2.0)
    lo = index
    hi = index
    while lo > 0 and score[lo - 1] >= threshold:
        lo -= 1
    while hi + 1 < len(score) and score[hi + 1] >= threshold:
        hi += 1
    return max(index - lo, hi - index, 1)


def estimate_alignment(program: ToneProgram, capture_channel: np.ndarray) -> AlignmentEstimate:
    """Estimate the program-to-capture affine sample mapping for one RX."""

    score = normalized_correlation(capture_channel, program.sync_marker)
    candidates = _nonmaximum_candidates(score, count=16, radius=len(program.sync_marker) // 2)
    expected_separation = program.sync_separation_samples
    search_radius = _samples(0.025)
    best: tuple[float, int, int] | None = None
    for start in candidates:
        expected_end = start + expected_separation
        lo = max(0, expected_end - search_radius)
        hi = min(len(score), expected_end + search_radius + 1)
        if lo >= hi:
            continue
        end = lo + int(np.argmax(score[lo:hi]))
        pair_score = min(float(score[start]), float(score[end]))
        candidate = (pair_score, -start, end)
        if best is None or candidate > (best[0], -best[1], best[2]):
            best = (pair_score, start, end)

    # A fixed-duration capture can contain a high-confidence first marker but
    # end before its repeated mate.  Without this check the pair search skips
    # the real marker and reports two unrelated low peaks as a fictitious clock
    # estimate.  Preserve the strong marker coordinate and fail explicitly.
    if candidates:
        strongest = candidates[0]
        strongest_score = float(score[strongest])
        strongest_expected_end = strongest + expected_separation
        strongest_mate_is_out_of_bounds = strongest_expected_end >= len(score)
        best_pair_floor = 0.0 if best is None else float(best[0])
        if (
            strongest_mate_is_out_of_bounds
            and strongest_score >= MINIMUM_SYNC_SCORE
            and best_pair_floor < min(MINIMUM_SYNC_SCORE, strongest_score / 2.0)
        ):
            origin = strongest - program.start_sync_sample
            return AlignmentEstimate(
                valid=False,
                capture_origin_sample=int(origin),
                start_marker_sample=int(strongest),
                end_marker_sample=-1,
                sample_scale=1.0,
                clock_error_ppm=0.0,
                start_score=strongest_score,
                end_score=0.0,
                peak_to_sidelobe_db=-600.0,
                uncertainty_samples=int(_peak_width(score, strongest)),
                reason="strong marker candidate has no in-bounds mate; capture truncated or program incomplete",
            )

    if best is None:
        return AlignmentEstimate(False, 0, 0, 0, 1.0, 0.0, 0.0, 0.0, -600.0, 0, "no marker pair")
    _, start, end = best
    scale = (end - start) / expected_separation
    origin = round(start - scale * program.start_sync_sample)
    clock_error_ppm = (scale - 1.0) * 1e6

    excluded = np.zeros(len(score), dtype=bool)
    exclusion_radius = len(program.sync_marker)
    for peak in (start, end):
        excluded[max(0, peak - exclusion_radius) : min(len(score), peak + exclusion_radius + 1)] = True
    sidelobe = float(np.max(score[~excluded])) if np.any(~excluded) else np.finfo(float).tiny
    marker_floor = min(float(score[start]), float(score[end]))
    psr = _db20(marker_floor, sidelobe)
    uncertainty = max(_peak_width(score, start), _peak_width(score, end))

    reasons = []
    if float(score[start]) < MINIMUM_SYNC_SCORE or float(score[end]) < MINIMUM_SYNC_SCORE:
        reasons.append("sync score below threshold")
    if psr < MINIMUM_SYNC_PSR_DB:
        reasons.append("sync PSR below threshold")
    if abs(clock_error_ppm) > MAXIMUM_CLOCK_ERROR_PPM:
        reasons.append("sample-scale estimate outside bound")
    if origin < 0:
        reasons.append("program origin precedes capture")
    return AlignmentEstimate(
        valid=not reasons,
        capture_origin_sample=origin,
        start_marker_sample=start,
        end_marker_sample=end,
        sample_scale=float(scale),
        clock_error_ppm=float(clock_error_ppm),
        start_score=float(score[start]),
        end_score=float(score[end]),
        peak_to_sidelobe_db=float(psr),
        uncertainty_samples=int(uncertainty),
        reason="; ".join(reasons) if reasons else None,
    )


def _capture_slice(
    capture_channel: np.ndarray,
    alignment: AlignmentEstimate,
    schedule_start: int,
    schedule_end: int,
) -> tuple[np.ndarray, np.ndarray]:
    start = math.ceil(alignment.capture_origin_sample + alignment.sample_scale * schedule_start)
    end = math.floor(alignment.capture_origin_sample + alignment.sample_scale * schedule_end)
    if start < 0 or end > len(capture_channel) or end - start < 8:
        raise ToneCheckError(
            f"scheduled slice [{schedule_start}, {schedule_end}) maps outside capture"
        )
    capture_indices = np.arange(start, end, dtype=np.float64)
    schedule_positions = (
        capture_indices - alignment.capture_origin_sample
    ) / alignment.sample_scale
    return np.asarray(capture_channel[start:end], dtype=np.float64), schedule_positions


def _fit_components(
    samples: np.ndarray,
    schedule_positions: np.ndarray,
    frequencies_hz: Sequence[float],
) -> dict[float, complex]:
    if len(samples) != len(schedule_positions) or len(samples) < 8:
        raise ValueError("component fit requires equal, nontrivial sample arrays")
    columns: list[np.ndarray] = []
    for frequency in frequencies_hz:
        phase = 2 * np.pi * frequency * schedule_positions / SAMPLE_RATE
        columns.extend((np.sin(phase), np.cos(phase)))
    columns.append(np.ones(len(samples)))
    design = np.column_stack(columns)
    coefficients, *_ = np.linalg.lstsq(design, samples, rcond=None)
    result = {}
    for index, frequency in enumerate(frequencies_hz):
        # y = a*sin(theta) + b*cos(theta); a+j*b has phase relative to sin(theta).
        result[float(frequency)] = complex(coefficients[2 * index], coefficients[2 * index + 1])
    return result


def _unique_products(first: float, second: float) -> tuple[float, ...]:
    candidates = (
        abs(second - first),
        first + second,
        abs(2 * first - second),
        abs(2 * second - first),
        2 * first + second,
        2 * second + first,
    )
    result = []
    for frequency in candidates:
        if not 20.0 <= frequency < SAMPLE_RATE / 2 - 20.0:
            continue
        if min(abs(frequency - first), abs(frequency - second)) < 1.0:
            continue
        if all(abs(frequency - existing) >= 1.0 for existing in result):
            result.append(float(frequency))
    return tuple(result)


def _noise_windows(
    program: ToneProgram,
    capture_channel: np.ndarray,
    alignment: AlignmentEstimate,
    requested_samples: int,
) -> tuple[tuple[np.ndarray, np.ndarray], tuple[np.ndarray, np.ndarray]]:
    margin = _samples(0.040)
    available_pre = program.start_sync_sample - 2 * margin
    after_sync = program.end_sync_sample + len(program.sync_marker) + margin
    available_post = program.total_samples - after_sync - margin
    count = min(requested_samples, available_pre, available_post)
    if count < _samples(0.040):
        raise ToneCheckError("program does not contain adequate matched noise windows")
    pre = _capture_slice(capture_channel, alignment, margin, margin + count)
    post = _capture_slice(capture_channel, alignment, after_sync, after_sync + count)
    return pre, post


def _rms(value: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.asarray(value, dtype=np.float64) ** 2)))


def _component_vector_power(values: Sequence[complex]) -> float:
    return float(math.sqrt(sum(abs(value) ** 2 for value in values)))


def _analyze_segment(
    program: ToneProgram,
    capture_channel: np.ndarray,
    alignment: AlignmentEstimate,
    segment: ToneSegment,
) -> dict[str, Any]:
    trim = _samples(TONE_EDGE_TRIM_S)
    core_start = segment.start_sample + trim
    core_end = segment.end_sample - trim
    received, positions = _capture_slice(capture_channel, alignment, core_start, core_end)
    reference_positions = np.arange(core_start, core_end, dtype=np.float64)
    reference = np.asarray(program.mono[core_start:core_end], dtype=np.float64)
    fundamentals = tuple(float(value) for value in segment.frequencies_hz)
    reference_fit = _fit_components(reference, reference_positions, fundamentals)
    received_fit = _fit_components(received, positions, fundamentals)

    pre_noise, post_noise = _noise_windows(program, capture_channel, alignment, len(reference))
    noise_fit_pre = _fit_components(pre_noise[0], pre_noise[1], fundamentals)
    noise_fit_post = _fit_components(post_noise[0], post_noise[1], fundamentals)

    gains = []
    fundamental_snrs = []
    for frequency in fundamentals:
        reference_component = reference_fit[frequency]
        received_component = received_fit[frequency]
        gain = received_component / reference_component
        noise_amplitude = max(abs(noise_fit_pre[frequency]), abs(noise_fit_post[frequency]))
        snr = _db20(abs(received_component), max(noise_amplitude, np.finfo(float).tiny))
        fundamental_snrs.append(snr)
        gains.append(
            {
                "frequency_hz": frequency,
                "gain_db": _db20(abs(gain)),
                "phase_rad": float(np.angle(gain)),
                "received_amplitude": float(abs(received_component)),
                "reference_amplitude": float(abs(reference_component)),
                "noise_projection_amplitude": float(noise_amplitude),
                "fundamental_snr_db": float(snr),
            }
        )

    third = max(8, len(received) // 3)
    early_fit = _fit_components(received[:third], positions[:third], fundamentals)
    late_fit = _fit_components(received[-third:], positions[-third:], fundamentals)
    early_amplitude = _component_vector_power([early_fit[f] for f in fundamentals])
    late_amplitude = _component_vector_power([late_fit[f] for f in fundamentals])
    agc_change = _db20(late_amplitude, early_amplitude)

    result: dict[str, Any] = {
        "label": segment.label,
        "kind": segment.kind,
        "schedule_start_sample": segment.start_sample,
        "schedule_end_sample": segment.end_sample,
        "core_start_sample": core_start,
        "core_end_sample": core_end,
        "fundamentals": gains,
        "minimum_fundamental_snr_db": float(min(fundamental_snrs)),
        "early_to_late_gain_change_db": float(agc_change),
    }

    if segment.kind == "single":
        fundamental = fundamentals[0]
        harmonics = tuple(
            harmonic * fundamental
            for harmonic in range(2, 6)
            if harmonic * fundamental < SAMPLE_RATE / 2 - 20.0
        )
        if harmonics:
            frequencies = (fundamental, *harmonics)
            distortion_fit = _fit_components(received, positions, frequencies)
            pre_fit = _fit_components(pre_noise[0], pre_noise[1], harmonics)
            post_fit = _fit_components(post_noise[0], post_noise[1], harmonics)
            distortion = _component_vector_power([distortion_fit[f] for f in harmonics])
            noise = _component_vector_power(
                [max((pre_fit[f], post_fit[f]), key=abs) for f in harmonics]
            )
            fundamental_amplitude = abs(distortion_fit[fundamental])
            conservative = max(distortion, 3.0 * noise)
            result["thd"] = {
                "harmonics_hz": list(harmonics),
                "raw_dbc": _db20(distortion, fundamental_amplitude),
                "conservative_upper_bound_dbc": _db20(conservative, fundamental_amplitude),
                "noise_floor_dbc": _db20(noise, fundamental_amplitude),
                "noise_limited": bool(distortion <= 3.0 * noise),
            }
        else:
            result["thd"] = None
    else:
        first, second = fundamentals
        products = _unique_products(first, second)
        frequencies = (*fundamentals, *products)
        imd_fit = _fit_components(received, positions, frequencies)
        pre_fit = _fit_components(pre_noise[0], pre_noise[1], products)
        post_fit = _fit_components(post_noise[0], post_noise[1], products)
        distortion = _component_vector_power([imd_fit[f] for f in products])
        noise = _component_vector_power(
            [max((pre_fit[f], post_fit[f]), key=abs) for f in products]
        )
        fundamental_amplitude = _component_vector_power([imd_fit[first], imd_fit[second]])
        conservative = max(distortion, 3.0 * noise)
        result["imd"] = {
            "products_hz": list(products),
            "raw_dbc": _db20(distortion, fundamental_amplitude),
            "conservative_upper_bound_dbc": _db20(conservative, fundamental_amplitude),
            "noise_floor_dbc": _db20(noise, fundamental_amplitude),
            "noise_limited": bool(distortion <= 3.0 * noise),
        }
    return result


def _best_delayed_copy(first: np.ndarray, second: np.ndarray, max_lag: int = 96) -> dict[str, Any]:
    x = np.asarray(first, dtype=np.float64)
    y = np.asarray(second, dtype=np.float64)
    best: tuple[float, int, float, float] | None = None
    best_key: tuple[float, int] | None = None
    for lag in range(-max_lag, max_lag + 1):
        if lag >= 0:
            left = x[: len(x) - lag or None]
            right = y[lag:]
        else:
            left = x[-lag:]
            right = y[: len(y) + lag]
        if len(left) < 16:
            continue
        left = left - np.mean(left)
        right = right - np.mean(right)
        denominator = math.sqrt(float(np.dot(left, left) * np.dot(right, right)))
        correlation = abs(float(np.dot(left, right))) / max(denominator, np.finfo(float).tiny)
        scale = float(np.dot(left, right) / max(np.dot(left, left), np.finfo(float).tiny))
        residual = right - scale * left
        residual_db = _db20(_rms(residual), _rms(right))
        candidate_key = (correlation, -abs(lag))
        if best is None or best_key is None or candidate_key > best_key:
            best = (correlation, lag, scale, residual_db)
            best_key = candidate_key
    if best is None:
        raise ToneCheckError("insufficient samples for duplicate-channel diagnostic")
    return {
        "absolute_correlation": float(best[0]),
        "best_lag_samples_rx1_relative_to_rx0": int(best[1]),
        "fitted_scale_rx1_per_rx0": float(best[2]),
        "scaled_copy_residual_db": float(best[3]),
    }


def _welch_coherence(first: np.ndarray, second: np.ndarray, nfft: int = 1024) -> dict[str, float]:
    x = np.asarray(first, dtype=np.float64)
    y = np.asarray(second, dtype=np.float64)
    count = min(nfft, len(x), len(y))
    if count < 64:
        return {"median": 0.0, "p95": 0.0}
    step = count // 2
    window = np.hanning(count)
    pxx = np.zeros(count // 2 + 1)
    pyy = np.zeros_like(pxx)
    pxy = np.zeros(count // 2 + 1, dtype=np.complex128)
    observations = 0
    for start in range(0, min(len(x), len(y)) - count + 1, step):
        first_spectrum = np.fft.rfft((x[start : start + count] - np.mean(x[start : start + count])) * window)
        second_spectrum = np.fft.rfft((y[start : start + count] - np.mean(y[start : start + count])) * window)
        pxx += np.abs(first_spectrum) ** 2
        pyy += np.abs(second_spectrum) ** 2
        pxy += first_spectrum * second_spectrum.conj()
        observations += 1
    if observations == 0:
        return {"median": 0.0, "p95": 0.0}
    coherence = np.abs(pxy) ** 2 / np.maximum(pxx * pyy, np.finfo(float).tiny)
    frequencies = np.fft.rfftfreq(count, 1.0 / SAMPLE_RATE)
    occupied = (frequencies >= 300.0) & (frequencies <= 22_000.0)
    selected = np.clip(coherence[occupied], 0.0, 1.0)
    return {
        "median": float(np.median(selected)),
        "p95": float(np.quantile(selected, 0.95)),
    }


def duplicate_channel_diagnostics(
    program: ToneProgram,
    capture: np.ndarray,
    alignments: Sequence[AlignmentEstimate],
) -> dict[str, Any]:
    """Look for duplicated logical RX channels without claiming independence."""

    if len(alignments) != 2 or not all(item.valid for item in alignments):
        return {
            "logical_channels_appear_duplicated": None,
            "diversity_claim_permitted": False,
            "reason": "alignment invalid; logical-channel independence is unverified",
        }
    start = min(item.start_marker_sample for item in alignments)
    end = max(item.end_marker_sample + len(program.sync_marker) for item in alignments)
    active = capture[start:end]
    delayed = _best_delayed_copy(active[:, 0], active[:, 1])

    pre_end = min(item.start_marker_sample for item in alignments)
    post_start = max(item.end_marker_sample + len(program.sync_marker) for item in alignments)
    passive_parts = []
    if pre_end >= 64:
        passive_parts.append(capture[:pre_end])
    if len(capture) - post_start >= 64:
        passive_parts.append(capture[post_start:])
    passive = np.concatenate(passive_parts, axis=0) if passive_parts else capture[:0]
    covariance_condition = None
    if len(passive) >= 64:
        covariance = np.cov(passive.T)
        eigenvalues = np.linalg.eigvalsh(covariance)
        covariance_condition = float(
            eigenvalues[-1] / max(eigenvalues[0], np.finfo(float).tiny)
        )

    sync_start = min(item.start_marker_sample for item in alignments)
    sync_end = max(item.start_marker_sample + len(program.sync_marker) for item in alignments)
    coherence = _welch_coherence(capture[sync_start:sync_end, 0], capture[sync_start:sync_end, 1])
    duplicate_votes = [
        delayed["absolute_correlation"] >= 0.9995,
        delayed["scaled_copy_residual_db"] <= -35.0,
        coherence["median"] >= 0.995,
        covariance_condition is not None and covariance_condition >= 10_000.0,
    ]
    appears_duplicated = sum(duplicate_votes) >= 3
    return {
        **delayed,
        "sync_magnitude_squared_coherence": coherence,
        "passive_covariance_condition_number": covariance_condition,
        "duplicate_votes": duplicate_votes,
        "logical_channels_appear_duplicated": appears_duplicated,
        "diversity_claim_permitted": False,
        "reason": (
            "logical channels are consistent with delayed/scaled duplication"
            if appears_duplicated
            else "this short end-to-end check cannot establish physical microphone independence"
        ),
    }


def _noise_summary(
    program: ToneProgram,
    capture_channel: np.ndarray,
    alignment: AlignmentEstimate,
) -> dict[str, float]:
    requested = _samples(0.30)
    pre, post = _noise_windows(program, capture_channel, alignment, requested)
    pre_rms = _rms(pre[0])
    post_rms = _rms(post[0])
    return {
        "pre_signal_rms": pre_rms,
        "post_signal_rms": post_rms,
        "post_signal_noise_increase_db": _db20(post_rms, pre_rms),
    }


def analyze_capture(program: ToneProgram, capture: np.ndarray, tx_index: int) -> dict[str, Any]:
    """Analyze one speaker's capture while preserving both receiver identities."""

    value = np.asarray(capture, dtype=np.float64)
    if value.ndim != 2 or value.shape[1] != 2:
        raise ValueError("capture must have shape [sample, 2]")
    alignments = [estimate_alignment(program, value[:, rx]) for rx in range(2)]
    valid_rx = [rx for rx, alignment in enumerate(alignments) if alignment.valid]
    timing_reference_rx = None
    if valid_rx:
        timing_reference_rx = max(
            valid_rx,
            key=lambda rx: (
                min(alignments[rx].start_score, alignments[rx].end_score),
                alignments[rx].peak_to_sidelobe_db,
            ),
        )
    schedule_alignments = []
    for rx, alignment in enumerate(alignments):
        if alignment.valid or timing_reference_rx is None:
            schedule_alignments.append(alignment)
        else:
            schedule_alignments.append(
                shared_clock_schedule_alignment(alignments[timing_reference_rx], alignment)
            )
    agreement = abs(
        alignments[0].capture_origin_sample - alignments[1].capture_origin_sample
    )
    schedule_agreement = abs(
        schedule_alignments[0].capture_origin_sample
        - schedule_alignments[1].capture_origin_sample
    )
    clipped_threshold = 32766.0 / 32768.0
    receivers = []
    for rx, alignment in enumerate(alignments):
        schedule_alignment = schedule_alignments[rx]
        peak = float(np.max(np.abs(value[:, rx])))
        clipped_fraction = float(np.mean(np.abs(value[:, rx]) >= clipped_threshold))
        receiver: dict[str, Any] = {
            "rx_index": rx,
            "alignment": alignment.to_dict(),
            "schedule_alignment": schedule_alignment.to_dict(),
            "own_marker_detection_valid": alignment.valid,
            "timing_reference_rx": rx if alignment.valid else timing_reference_rx,
            "timing_method": (
                "independent-repeated-marker"
                if alignment.valid
                else (
                    "shared-interleaved-capture-clock"
                    if schedule_alignment.valid
                    else "unavailable"
                )
            ),
            "absolute_peak": peak,
            "clipped_sample_fraction": clipped_fraction,
        }
        if schedule_alignment.valid:
            segments = [
                _analyze_segment(program, value[:, rx], schedule_alignment, segment)
                for segment in program.segments
            ]
            thd_values = [
                item["thd"]["conservative_upper_bound_dbc"]
                for item in segments
                if item.get("thd") is not None
            ]
            imd_values = [
                item["imd"]["conservative_upper_bound_dbc"]
                for item in segments
                if item.get("imd") is not None
            ]
            receiver.update(
                {
                    "segments": segments,
                    "noise": _noise_summary(
                        program,
                        value[:, rx],
                        schedule_alignment,
                    ),
                    "maximum_thd_dbc": max(thd_values),
                    "maximum_imd_dbc": max(imd_values),
                    "maximum_absolute_within_stage_gain_jump_db": max(
                        abs(item["early_to_late_gain_change_db"]) for item in segments
                    ),
                    "minimum_fundamental_snr_db": min(
                        item["minimum_fundamental_snr_db"] for item in segments
                    ),
                }
            )
        receivers.append(receiver)

    result = {
        "schema": SCHEMA,
        "tx_index": tx_index,
        "matrix_semantics": "receivers[rx] is TX tx_index -> logical RX rx; channels are never mixed",
        "origin_method": "known-probe-correlation",
        "origin_semantics": (
            "composite arrival coordinate including playback, capture, acoustic, and filtering latency; "
            "not propagation delay"
        ),
        "candidate_samples_by_rx": [item.capture_origin_sample for item in alignments],
        "origin_agreement_samples": int(agreement),
        "schedule_samples_by_rx": [
            item.capture_origin_sample for item in schedule_alignments
        ],
        "schedule_origin_agreement_samples": int(schedule_agreement),
        "timing_reference_rx": timing_reference_rx,
        "receivers": receivers,
        "duplicate_channel_diagnostics": duplicate_channel_diagnostics(
            program, value, schedule_alignments
        ),
    }
    result["safety_gate"] = evaluate_safety_gate(result)
    return result


def evaluate_safety_gate(analysis: dict[str, Any]) -> dict[str, Any]:
    reasons = []
    all_alignments_valid = all(
        receiver["alignment"]["valid"] for receiver in analysis["receivers"]
    )
    if (
        all_alignments_valid
        and analysis["origin_agreement_samples"] > MAXIMUM_RX_ORIGIN_DISAGREEMENT_SAMPLES
    ):
        reasons.append("receiver origins disagree beyond the declared limit")
    for receiver in analysis["receivers"]:
        rx = receiver["rx_index"]
        alignment = receiver["alignment"]
        if not alignment["valid"]:
            reasons.append(f"rx{rx} alignment invalid: {alignment['reason']}")
            continue
        if receiver["absolute_peak"] >= ABSOLUTE_SAMPLE_PEAK_LIMIT:
            reasons.append(f"rx{rx} peak reaches the safety limit")
        if receiver["clipped_sample_fraction"] > CLIPPED_SAMPLE_FRACTION_LIMIT:
            reasons.append(f"rx{rx} clipped-sample fraction exceeds the limit")
        if receiver["maximum_thd_dbc"] > MAXIMUM_THD_DB:
            reasons.append(f"rx{rx} conservative THD upper bound exceeds {MAXIMUM_THD_DB} dBc")
        if receiver["maximum_imd_dbc"] > MAXIMUM_IMD_DB:
            reasons.append(f"rx{rx} conservative IMD upper bound exceeds {MAXIMUM_IMD_DB} dBc")
        if (
            receiver["maximum_absolute_within_stage_gain_jump_db"]
            > WITHIN_STAGE_GAIN_JUMP_LIMIT_DB
        ):
            reasons.append(f"rx{rx} within-stage gain change indicates AGC or rerouting")
        if receiver["noise"]["post_signal_noise_increase_db"] > POST_SIGNAL_NOISE_INCREASE_LIMIT_DB:
            reasons.append(f"rx{rx} post-signal noise increase exceeds the limit")
        if receiver["minimum_fundamental_snr_db"] < MINIMUM_FUNDAMENTAL_SNR_DB:
            reasons.append(f"rx{rx} tone SNR is insufficient to resolve distortion")
    return {
        "passed": not reasons,
        "reasons": reasons,
        "failure_policy": "do not energize the next speaker; stop audio and restore Mac volume",
    }


def build_plan(
    *,
    run_id: str,
    geometry_label: str,
    authorization_note: str,
    expected_android_serial: str,
    expected_android_model: str,
    expected_android_build_fingerprint: str,
    expected_android_apk_sha256: str,
    expected_route_signature: Mapping[str, Any],
    calibration_artifact_sha256: str,
    calibration_profile_key: str,
    output_volume_percent: int = DEFAULT_OUTPUT_VOLUME_PERCENT,
    digital_peak: float = DEFAULT_DIGITAL_PEAK,
    android_source: str = "unprocessed",
) -> dict[str, Any]:
    if not run_id or not re.fullmatch(r"[A-Za-z0-9._-]+", run_id):
        raise ValueError("run_id must use only letters, digits, dot, underscore, or hyphen")
    if not geometry_label.strip():
        raise ValueError("a nonempty physical geometry label is required")
    if not authorization_note.strip():
        raise ValueError("a nonempty physical-test authorization note is required")
    target = {
        "serial": _require_nonempty(expected_android_serial, "expected Android serial"),
        "model": _require_nonempty(expected_android_model, "expected Android model"),
        "build_fingerprint": _require_nonempty(
            expected_android_build_fingerprint,
            "expected Android build fingerprint",
        ),
        "package": ANDROID_PACKAGE,
        "installed_package_apk_sha256": _require_sha256(
            expected_android_apk_sha256,
            "expected Android APK SHA-256",
        ),
    }
    if not isinstance(expected_route_signature, Mapping):
        raise ValueError("expected route signature must be a JSON object")
    route_signature = dict(expected_route_signature)
    if route_signature.get("schema") != ROUTE_SIGNATURE_SCHEMA:
        raise ValueError(
            f"expected route signature schema must be {ROUTE_SIGNATURE_SCHEMA!r}"
        )
    try:
        route_signature_sha256 = sha256_bytes(canonical_json_bytes(route_signature))
    except (TypeError, ValueError) as error:
        raise ValueError("expected route signature must be canonical JSON data") from error
    calibration_sha256 = _require_sha256(
        calibration_artifact_sha256,
        "calibration artifact SHA-256",
    )
    calibration_key = _require_nonempty(calibration_profile_key, "calibration profile key")
    if not 1 <= output_volume_percent <= MAX_OUTPUT_VOLUME_PERCENT:
        raise ValueError(
            f"output volume must be in [1, {MAX_OUTPUT_VOLUME_PERCENT}] for this low-level runner"
        )
    program = build_tone_program(digital_peak)
    transmitters = []
    for tx_index in (0, 1):
        stereo = program.stereo_for_tx(tx_index)
        transmitters.append(
            {
                "tx_index": tx_index,
                "active_channel": "left" if tx_index == 0 else "right",
                "silent_channel": "right" if tx_index == 0 else "left",
                "stereo_sha256": array_sha256(stereo),
                "active_channel_peak": float(np.max(np.abs(stereo[:, tx_index]))),
                "silent_channel_peak": float(np.max(np.abs(stereo[:, 1 - tx_index]))),
                "depends_on": None if tx_index == 0 else "tx0-safety-gate-pass",
            }
        )
    plan: dict[str, Any] = {
        "schema": SCHEMA,
        "run_id": run_id,
        "default_mode": "dry-run",
        "physical_execution_requires": "--execute",
        "expected_target": target,
        "route": {
            "tx_device": EXPECTED_MAC_OUTPUT_NAME,
            "tx_device_uid": EXPECTED_MAC_OUTPUT_UID,
            "rx_device": "connected Android HIL device",
            "sample_rate_hz": SAMPLE_RATE,
            "output_channels": ["left-logical", "right-logical"],
            "input_channels": ["rx0-logical", "rx1-logical"],
            "android_source_requested": android_source,
            "signature_schema": ROUTE_SIGNATURE_SCHEMA,
            "expected_signature": route_signature,
            "expected_signature_sha256": route_signature_sha256,
            "warning": (
                f"requested {android_source} does not prove that all OS or vendor DSP is absent"
            ),
        },
        "calibration_provenance": {
            "artifact_sha256": calibration_sha256,
            "profile_key": calibration_key,
            "android_source": android_source,
            "route_signature_schema": ROUTE_SIGNATURE_SCHEMA,
            "expected_route_signature_sha256": route_signature_sha256,
            "target_installed_package_apk_sha256": target[
                "installed_package_apk_sha256"
            ],
        },
        "physical_context": {
            "geometry_label": geometry_label,
            "authorization_note": authorization_note,
        },
        "levels": {
            "mac_output_volume_percent": output_volume_percent,
            "digital_peak": digital_peak,
            "absolute_spl_calibrated": False,
        },
        "lock_path": str(LOCK_PATH),
        "capture": {
            "channels": 2,
            "format": "interleaved-s16le",
            "pre_roll_seconds": CAPTURE_PRE_ROLL_S,
            "post_roll_seconds": CAPTURE_POST_ROLL_S,
            "scheduled_program_seconds": program.total_samples / SAMPLE_RATE,
        },
        "program": program.manifest(),
        "transmitters": transmitters,
        "safety_gate_between_transmitters": True,
        "analysis_contract": {
            "matrix_shape": "[tx][rx]; no averaging, channel selection, or content-based combining",
            "alignment": "per-RX repeated-marker normalized correlation with PSR and uncertainty",
            "distortion": (
                "exact-frequency LS with a deterministic 3x-reference screening statistic; "
                "not a confidence bound"
            ),
            "diversity_claim_permitted": False,
        },
    }
    plan["plan_sha256"] = sha256_bytes(canonical_json_bytes(plan))
    return plan


class PortAudioOutputInspector:
    """Fail-closed binding between AppleScript volume and PortAudio output.

    AppleScript controls the current macOS default output. Playback is safe only
    when PortAudio exposes exactly one stereo-capable endpoint with the expected
    name and that same endpoint is the current PortAudio default output.
    ``sounddevice`` is imported lazily so planning and offline tests do not
    inspect audio state.
    """

    def __init__(self, sounddevice_module: Any | None = None) -> None:
        self._sounddevice_module = sounddevice_module

    def query(self) -> MacOutputEndpoint:
        if self._sounddevice_module is None:
            import sounddevice as sd
        else:
            sd = self._sounddevice_module
        devices = list(sd.query_devices())
        matches = [
            (index, device)
            for index, device in enumerate(devices)
            if str(device.get("name", "")) == EXPECTED_MAC_OUTPUT_NAME
            and int(device.get("max_output_channels", 0)) >= 2
        ]
        if len(matches) != 1:
            raise ToneCheckError(
                f"expected exactly one stereo-capable {EXPECTED_MAC_OUTPUT_NAME!r} "
                f"PortAudio endpoint, found {len(matches)}"
            )
        default_device = sd.default.device
        try:
            default_output_index = int(default_device[1])
        except (IndexError, TypeError, ValueError) as error:
            raise ToneCheckError(f"cannot determine PortAudio default output: {default_device!r}") from error
        index, device = matches[0]
        endpoint = MacOutputEndpoint(
            portaudio_index=index,
            name=str(device["name"]),
            hostapi_index=int(device.get("hostapi", -1)),
            max_output_channels=int(device["max_output_channels"]),
            default_sample_rate_hz=float(device.get("default_samplerate", 0.0)),
            is_default_output=index == default_output_index,
        )
        if not endpoint.is_default_output:
            raise ToneCheckError(
                f"{EXPECTED_MAC_OUTPUT_NAME!r} PortAudio index {index} is not the default "
                f"output index {default_output_index}; refusing AppleScript volume mutation"
            )
        return endpoint


class MacDefaultOutputController:
    """Query and select the CoreAudio default output by stable UID."""

    def __init__(self, runner: Callable[..., Any] | None = None) -> None:
        self._runner = runner or subprocess.run

    def _run(self, arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
        try:
            return self._runner(
                [SWITCH_AUDIO_SOURCE, *arguments],
                check=True,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.CalledProcessError) as error:
            raise ToneCheckError(f"CoreAudio output command failed: {error}") from error

    def query(self) -> MacDefaultOutput:
        completed = self._run(("-c", "-t", "output", "-f", "json"))
        try:
            value = json.loads(completed.stdout)
        except json.JSONDecodeError:
            # switchaudio-osx 1.2.2 writes raw CR/LF characters inside the
            # JSON name string for some virtual devices (for example Stage
            # V2). Parse its fixed record shape without discarding that name.
            match = re.fullmatch(
                r'\{"name": "(.*)", "type": "output", "id": "([^"]+)", '
                r'"uid": "([^"]+)"\}\s*',
                completed.stdout,
                flags=re.DOTALL,
            )
            if match is None:
                raise ToneCheckError(
                    f"cannot parse CoreAudio default output: {completed.stdout!r}"
                )
            value = {"name": match.group(1), "id": match.group(2), "uid": match.group(3)}
        try:
            endpoint = MacDefaultOutput(
                name=str(value["name"]),
                uid=str(value["uid"]),
                device_id=str(value["id"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ToneCheckError(
                f"cannot parse CoreAudio default output: {completed.stdout!r}"
            ) from error
        if not endpoint.uid:
            raise ToneCheckError("CoreAudio default output has no UID")
        return endpoint

    def select_uid(self, uid: str) -> MacDefaultOutput:
        value = str(uid)
        if not value or any(character in value for character in ("\0", "\n", "\r")):
            raise ValueError("CoreAudio output UID is malformed")
        self._run(("-u", value, "-t", "output"))
        selected = self.query()
        if selected.uid != value:
            raise ToneCheckError(
                f"CoreAudio output selection mismatch: expected={value!r}, got={selected.uid!r}"
            )
        return selected


class MacVolumeController:
    """Checked AppleScript volume access, injected in offline tests."""

    def __init__(self, runner: Callable[..., Any] | None = None) -> None:
        self._runner = runner or subprocess.run

    def _script(self, source: str) -> str:
        completed = self._runner(
            ["osascript", "-e", source],
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip()

    def query(self) -> MacVolumeState:
        raw_volume = self._script("output volume of (get volume settings)")
        raw_muted = self._script("output muted of (get volume settings)").lower()
        try:
            volume = int(raw_volume)
        except ValueError as error:
            raise ToneCheckError(f"cannot parse Mac output volume: {raw_volume!r}") from error
        if raw_muted not in ("true", "false"):
            raise ToneCheckError(f"cannot parse Mac mute state: {raw_muted!r}")
        return MacVolumeState(volume, raw_muted == "true")

    def set(self, state: MacVolumeState) -> None:
        if not 0 <= state.output_volume_percent <= 100:
            raise ValueError("output volume must be in [0, 100]")
        self._script(f"set volume output volume {state.output_volume_percent}")
        mute_script = (
            "set volume with output muted"
            if state.output_muted
            else "set volume without output muted"
        )
        self._script(mute_script)


@contextlib.contextmanager
def temporary_mac_volume(
    output_volume_percent: int,
    controller: MacVolumeController | None = None,
    endpoint_inspector: PortAudioOutputInspector | None = None,
    route_controller: MacDefaultOutputController | None = None,
) -> Iterator[VolumeTransaction]:
    """Select, level, and restore the built-in output as one transaction."""

    control = controller or MacVolumeController()
    inspector = endpoint_inspector or PortAudioOutputInspector()
    routes = route_controller or MacDefaultOutputController()
    original_output = routes.query()
    original_volume = control.query()
    selected_output: MacDefaultOutput | None = None
    endpoint_before_set: MacOutputEndpoint | None = None
    prior: MacVolumeState | None = None
    requested = MacVolumeState(output_volume_percent, False)
    transaction = VolumeTransaction(
        prior=original_volume,
        requested=requested,
        original_default_output=original_output,
        original_default_volume=original_volume,
    )
    try:
        selected_output = routes.select_uid(EXPECTED_MAC_OUTPUT_UID)
        if selected_output.name != EXPECTED_MAC_OUTPUT_NAME:
            raise ToneCheckError(
                f"built-in output UID resolved to unexpected name {selected_output.name!r}"
            )
        transaction.selected_default_output = selected_output
        endpoint_before_set = inspector.query()
        transaction.endpoint_before_set = endpoint_before_set
        prior = control.query()
        transaction.prior = prior
        control.set(requested)
        transaction.realized = control.query()
        if transaction.realized != requested:
            raise ToneCheckError(
                f"Mac volume realization mismatch: requested={requested}, got={transaction.realized}"
            )
        transaction.endpoint_after_set = inspector.query()
        if transaction.endpoint_after_set != endpoint_before_set:
            raise ToneCheckError("Mac output endpoint changed while applying the volume transaction")
        yield transaction
    finally:
        restore_error: BaseException | None = None
        try:
            current = routes.query()
            if current.uid != EXPECTED_MAC_OUTPUT_UID:
                current = routes.select_uid(EXPECTED_MAC_OUTPUT_UID)
            if current.name != EXPECTED_MAC_OUTPUT_NAME:
                raise ToneCheckError("cannot restore volume: built-in output identity changed")
            if prior is not None and endpoint_before_set is not None:
                transaction.endpoint_before_restore = inspector.query()
                if transaction.endpoint_before_restore != endpoint_before_set:
                    raise ToneCheckError("Mac output endpoint changed before volume restoration")
                control.set(prior)
                transaction.restored = control.query()
                if transaction.restored != prior:
                    raise ToneCheckError(
                        f"Mac volume restoration mismatch: expected={prior}, "
                        f"got={transaction.restored}"
                    )
                transaction.endpoint_after_restore = inspector.query()
                if transaction.endpoint_after_restore != endpoint_before_set:
                    raise ToneCheckError("Mac output endpoint changed during volume restoration")
        except BaseException as error:
            restore_error = error
        try:
            transaction.restored_default_output = routes.select_uid(original_output.uid)
            if transaction.restored_default_output != original_output:
                raise ToneCheckError(
                    "CoreAudio default output restoration did not reproduce the prior endpoint"
                )
            transaction.original_default_volume_restored = control.query()
            if transaction.original_default_volume_restored != original_volume:
                raise ToneCheckError(
                    "the restored original CoreAudio output volume/mute state changed"
                )
        except BaseException as error:
            if restore_error is None:
                restore_error = error
        if restore_error is not None:
            raise restore_error


@contextlib.contextmanager
def exclusive_hardware_lock(run_id: str) -> Iterator[None]:
    """Acquire the shared Android-audio lock without waiting."""

    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ToneCheckError(f"hardware lock is already held: {LOCK_PATH}") from error
        handle.seek(0)
        handle.truncate()
        handle.write(f"pid={os.getpid()} run_id={run_id}\n")
        handle.flush()
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _write_json(path: Path, value: Any) -> dict[str, Any]:
    payload = canonical_json_bytes(value)
    path.write_bytes(payload + b"\n")
    return {
        "path": str(path),
        "byte_count": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _write_float32(path: Path, value: np.ndarray) -> dict[str, Any]:
    canonical = np.ascontiguousarray(value, dtype="<f4")
    path.write_bytes(memoryview(canonical).cast("B"))
    return {
        "path": str(path),
        "byte_count": path.stat().st_size,
        "sha256": sha256_file(path),
        "frames": int(canonical.shape[0]),
        "channels": int(canonical.shape[1]) if canonical.ndim == 2 else 1,
    }


def _checked_adb_value(harness: Any, arguments: str, label: str) -> str:
    completed = harness.adb(arguments)
    if completed.returncode != 0 or not completed.stdout.strip():
        raise ToneCheckError(f"cannot read Android {label}: {completed.stderr.strip()}")
    return completed.stdout.strip()


def _parse_begin_log(line: str) -> tuple[int, int]:
    match = re.search(r"rate=(\d+) ch=(\d+)", line)
    if not match:
        raise ToneCheckError(f"cannot parse AudioRecord realization: {line!r}")
    return int(match.group(1)), int(match.group(2))


def _stop_mac_audio() -> None:
    try:
        import sounddevice as sd

        sd.stop()
    except Exception:
        # This is best-effort emergency cleanup. Volume restoration still runs.
        pass


def _assert_playback_state(
    *,
    route_controller: MacDefaultOutputController,
    endpoint_inspector: PortAudioOutputInspector,
    volume_controller: MacVolumeController,
    expected_volume: MacVolumeState,
) -> dict[str, Any]:
    default_output = route_controller.query()
    if (
        default_output.uid != EXPECTED_MAC_OUTPUT_UID
        or default_output.name != EXPECTED_MAC_OUTPUT_NAME
    ):
        raise ToneCheckError(f"Mac default output drifted before playback: {default_output}")
    endpoint = endpoint_inspector.query()
    volume = volume_controller.query()
    if volume != expected_volume:
        raise ToneCheckError(
            f"Mac output volume/mute drifted before playback: expected={expected_volume}, got={volume}"
        )
    return {
        "coreaudio_default": default_output.to_dict(),
        "portaudio_output": endpoint.to_dict(),
        "volume": dataclasses.asdict(volume),
    }


def _execute_one_capture(
    *,
    harness: Any,
    program: ToneProgram,
    tx_index: int,
    run_id: str,
    run_root: Path,
    android_source: str,
    expected_target: Mapping[str, Any],
    expected_route_signature: Mapping[str, Any],
    volume_controller: MacVolumeController,
    endpoint_inspector: PortAudioOutputInspector,
    route_controller: MacDefaultOutputController,
    expected_volume: MacVolumeState,
) -> dict[str, Any]:
    tx_root = run_root / f"tx{tx_index}"
    tx_root.mkdir(parents=True, exist_ok=False)
    stereo = program.stereo_for_tx(tx_index)
    if np.any(stereo[:, 1 - tx_index] != 0):
        raise AssertionError("inactive transmitter channel is not digital zero")
    transmit_artifact = _write_float32(tx_root / "transmit-stereo-f32le.pcm", stereo)
    capture_path = tx_root / "capture-stereo-s16le.pcm"
    request_nonce = uuid.uuid4().hex
    out_name = f"{run_id}-tx{tx_index}-{request_nonce}.pcm"
    duration = planned_capture_duration_seconds(program)
    target_before_capture = harness.assert_android_target(dict(expected_target))
    capture_request_monotonic_ns = time.monotonic_ns()
    begin_line = None
    done_line = None
    pulled_path = None
    record_finished = False
    capture_begin_observed_monotonic_ns = None
    route_validation_complete_monotonic_ns = None
    target_revalidation_complete_monotonic_ns = None
    pre_roll_complete_monotonic_ns = None
    pre_playback_setup_elapsed_s = None
    try:
        begin_line = harness.android_record_start(
            duration,
            channels=2,
            source=android_source,
            out_name=out_name,
            sr=SAMPLE_RATE,
        )
        capture_begin_observed_monotonic_ns = time.monotonic_ns()
        actual_rate, actual_channels = _parse_begin_log(begin_line)
        if actual_rate != SAMPLE_RATE or actual_channels != 2:
            raise ToneCheckError(
                f"AudioRecord realized rate/channels {actual_rate}/{actual_channels}, "
                "expected 48000/2"
            )
        start_route_log = harness.android_request_log(out_name)
        start_route = harness.validate_android_record_route_start_log(
            start_route_log,
            out_name,
            expected_source=android_source,
            expected_sample_rate_hz=SAMPLE_RATE,
            expected_channels=2,
            expected_route_signature=dict(expected_route_signature),
            require_distinct_direct_channel_mappings=True,
        )
        route_validation_complete_monotonic_ns = time.monotonic_ns()
        target_before_playback = harness.assert_android_target(dict(expected_target))
        target_revalidation_complete_monotonic_ns = time.monotonic_ns()
        time.sleep(CAPTURE_PRE_ROLL_S)
        pre_roll_complete_monotonic_ns = time.monotonic_ns()
        playback_state_before = _assert_playback_state(
            route_controller=route_controller,
            endpoint_inspector=endpoint_inspector,
            volume_controller=volume_controller,
            expected_volume=expected_volume,
        )
        play_request_monotonic_ns = time.monotonic_ns()
        pre_playback_setup_elapsed_s = assert_capture_setup_deadline(
            capture_request_monotonic_ns,
            play_request_monotonic_ns,
        )
        harness.mac_play(stereo, block=True, sr=SAMPLE_RATE)
        play_complete_monotonic_ns = time.monotonic_ns()
        playback_state_after = _assert_playback_state(
            route_controller=route_controller,
            endpoint_inspector=endpoint_inspector,
            volume_controller=volume_controller,
            expected_volume=expected_volume,
        )
        done_line, pulled_path = harness.android_record_finish(
            duration,
            out_name=out_name,
            local_path=str(capture_path),
        )
        record_finished = True
    finally:
        if begin_line is not None and not record_finished:
            try:
                harness.android_record_finish(
                    duration,
                    out_name=out_name,
                    local_path=str(capture_path),
                )
            except Exception:
                pass
    if done_line is None or pulled_path is None:
        raise ToneCheckError("AudioRecord did not produce a completed capture")
    realization = harness.parse_android_record_realization(begin_line, done_line, out_name)
    if (
        realization["source"] != android_source
        or realization["sample_rate_hz"] != SAMPLE_RATE
        or realization["channels"] != 2
    ):
        raise ToneCheckError(f"unexpected AudioRecord realization: {realization}")
    route_log = harness.android_request_log(out_name)
    route_realization = harness.validate_android_record_route_log(
        route_log,
        out_name,
        expected_source=android_source,
        expected_sample_rate_hz=SAMPLE_RATE,
        expected_channels=2,
        expected_route_signature=dict(expected_route_signature),
        require_distinct_direct_channel_mappings=True,
    )
    route_log_path = tx_root / "android-route.logcat.txt"
    route_log_path.write_text(route_log, encoding="utf-8")
    raw = np.fromfile(pulled_path, dtype="<i2")
    if len(raw) % 2:
        raise ToneCheckError("stereo capture contains an odd PCM16 value count")
    capture = raw.reshape(-1, 2).astype(np.float64) / 32768.0
    if len(capture) != realization["captured_frames"]:
        raise ToneCheckError(
            f"pulled capture has {len(capture)} frames; Android reported "
            f"{realization['captured_frames']}"
        )
    analysis = analyze_capture(program, capture, tx_index)
    analysis_artifact = _write_json(tx_root / "analysis.json", analysis)
    capture_artifact = {
        "path": str(capture_path),
        "byte_count": capture_path.stat().st_size,
        "sha256": sha256_file(capture_path),
        "frames": int(len(capture)),
        "channels": 2,
    }
    metadata = {
        "tx_index": tx_index,
        "request_nonce": request_nonce,
        "android_out_name": out_name,
        "android_target_before_capture": target_before_capture,
        "android_target_before_playback": target_before_playback,
        "android_record_begin_log": begin_line,
        "android_record_done_log": done_line,
        "android_record_realization": realization,
        "android_route_realization": route_realization,
        "android_start_route_realization": start_route,
        "realized_sample_rate_hz": actual_rate,
        "realized_channels": actual_channels,
        "scheduled_capture_seconds": duration,
        "capture_setup_budget_seconds": CAPTURE_SETUP_BUDGET_S,
        "capture_request_monotonic_ns": capture_request_monotonic_ns,
        "capture_begin_observed_monotonic_ns": capture_begin_observed_monotonic_ns,
        "route_validation_complete_monotonic_ns": route_validation_complete_monotonic_ns,
        "target_revalidation_complete_monotonic_ns": target_revalidation_complete_monotonic_ns,
        "pre_roll_complete_monotonic_ns": pre_roll_complete_monotonic_ns,
        "pre_playback_setup_elapsed_s": pre_playback_setup_elapsed_s,
        "play_request_monotonic_ns": play_request_monotonic_ns,
        "play_complete_monotonic_ns": play_complete_monotonic_ns,
        "playback_state_before": playback_state_before,
        "playback_state_after": playback_state_after,
        "host_timestamps_are_alignment_authority": False,
        "artifacts": {
            "transmit": transmit_artifact,
            "capture": capture_artifact,
            "analysis": analysis_artifact,
            "android_route_log": {
                "path": str(route_log_path),
                "byte_count": route_log_path.stat().st_size,
                "sha256": sha256_file(route_log_path),
            },
        },
    }
    metadata["metadata_artifact"] = _write_json(tx_root / "capture-metadata.json", metadata)
    return {"metadata": metadata, "analysis": analysis}


def execute_plan(plan: dict[str, Any], artifact_dir: Path) -> dict[str, Any]:
    """Execute the narrow physical plan. Call only after explicit authorization."""

    if plan.get("schema") != SCHEMA:
        raise ToneCheckError("execution plan has an unsupported schema")
    if not plan.get("plan_sha256"):
        raise ToneCheckError("execution requires a hashed plan")
    expected = dict(plan)
    recorded_hash = expected.pop("plan_sha256")
    if sha256_bytes(canonical_json_bytes(expected)) != recorded_hash:
        raise ToneCheckError("plan hash mismatch")
    context = plan.get("physical_context", {})
    if not str(context.get("geometry_label", "")).strip():
        raise ToneCheckError("execution plan lacks a physical geometry label")
    if not str(context.get("authorization_note", "")).strip():
        raise ToneCheckError("execution plan lacks a physical-test authorization note")
    if plan.get("route", {}).get("sample_rate_hz") != SAMPLE_RATE:
        raise ToneCheckError("this runner permits only 48 kHz execution")
    expected_target = plan.get("expected_target")
    expected_route_signature = plan.get("route", {}).get("expected_signature")
    if not isinstance(expected_target, dict):
        raise ToneCheckError("execution plan lacks an expected Android target")
    if not isinstance(expected_route_signature, dict):
        raise ToneCheckError("execution plan lacks an expected Android route signature")
    requested_volume = plan.get("levels", {}).get("mac_output_volume_percent")
    requested_peak = plan.get("levels", {}).get("digital_peak")
    if not isinstance(requested_volume, int) or not 1 <= requested_volume <= MAX_OUTPUT_VOLUME_PERCENT:
        raise ToneCheckError("execution plan exceeds the low-level output-volume bound")
    if not isinstance(requested_peak, (int, float)) or not 0 < requested_peak <= MAX_DIGITAL_PEAK:
        raise ToneCheckError("execution plan exceeds the digital-peak bound")
    if plan.get("safety_gate_between_transmitters") is not True:
        raise ToneCheckError("execution plan must gate between transmitters")
    run_root = artifact_dir / plan["run_id"]
    run_root.mkdir(parents=True, exist_ok=False)
    _write_json(run_root / "plan.json", plan)

    # Execution-only import: dry runs cannot import or contact the HIL harness.
    import harness

    program = build_tone_program(plan["levels"]["digital_peak"])
    transaction: VolumeTransaction | None = None
    speaker_results = []
    status = "complete"
    device: dict[str, Any] | None = None
    volume_controller = MacVolumeController()
    endpoint_inspector = PortAudioOutputInspector()
    route_controller = MacDefaultOutputController()
    try:
        with exclusive_hardware_lock(plan["run_id"]):
            device = harness.assert_android_target(expected_target)
            with temporary_mac_volume(
                plan["levels"]["mac_output_volume_percent"],
                controller=volume_controller,
                endpoint_inspector=endpoint_inspector,
                route_controller=route_controller,
            ) as active_volume:
                transaction = active_volume
                try:
                    for tx_index in (0, 1):
                        result = _execute_one_capture(
                            harness=harness,
                            program=program,
                            tx_index=tx_index,
                            run_id=plan["run_id"],
                            run_root=run_root,
                            android_source=plan["route"]["android_source_requested"],
                            expected_target=expected_target,
                            expected_route_signature=expected_route_signature,
                            volume_controller=volume_controller,
                            endpoint_inspector=endpoint_inspector,
                            route_controller=route_controller,
                            expected_volume=active_volume.requested,
                        )
                        speaker_results.append(result)
                        if not result["analysis"]["next_transmitter_interlock"]["passed"]:
                            status = "aborted-by-next-transmitter-interlock"
                            break
                finally:
                    _stop_mac_audio()
    finally:
        _stop_mac_audio()

    if transaction is None:
        raise ToneCheckError("volume transaction was not established")
    if device is None:
        raise ToneCheckError("Android provenance was not collected")
    execution = {
        "schema": SCHEMA,
        "status": status,
        "plan_sha256": plan["plan_sha256"],
        "run_id": plan["run_id"],
        "device": device,
        "physical_context": plan["physical_context"],
        "volume": {
            "prior": dataclasses.asdict(transaction.prior),
            "requested": dataclasses.asdict(transaction.requested),
            "realized": dataclasses.asdict(transaction.realized) if transaction.realized else None,
            "restored": dataclasses.asdict(transaction.restored) if transaction.restored else None,
            "original_default_output": (
                transaction.original_default_output.to_dict()
                if transaction.original_default_output else None
            ),
            "selected_default_output": (
                transaction.selected_default_output.to_dict()
                if transaction.selected_default_output else None
            ),
            "restored_default_output": (
                transaction.restored_default_output.to_dict()
                if transaction.restored_default_output else None
            ),
            "original_default_volume": (
                dataclasses.asdict(transaction.original_default_volume)
                if transaction.original_default_volume else None
            ),
            "original_default_volume_restored": (
                dataclasses.asdict(transaction.original_default_volume_restored)
                if transaction.original_default_volume_restored else None
            ),
        },
        "speaker_results": speaker_results,
        "speakers_energized": len(speaker_results),
        "diversity_claim_permitted": False,
        "limitations": [
            "relative electrical/digital level only; no calibrated SPL",
            "known-marker origin includes all buffer, filtering, and acoustic delay",
            "UNPROCESSED is requested but platform DSP absence is not established",
            "separate safety-gated captures do not preserve coherent phase between TX columns",
            "logical microphone independence is not established by this check",
            "the cooperative lock cannot exclude software that ignores the same lock file",
            "correlation uncertainty is the marker peak width, not a bound on systematic group delay",
        ],
    }
    execution["execution_artifact"] = _write_json(run_root / "execution.json", execution)
    return execution


def _default_run_id() -> str:
    return time.strftime("tonecheck-%Y%m%dT%H%M%SZ-", time.gmtime()) + uuid.uuid4().hex[:8]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--execute", action="store_true", help="contact hardware and play the plan")
    parser.add_argument("--run-id", default=None, help="unique artifact/run identifier")
    parser.add_argument("--geometry-label", required=True, help="fixed device pose/distance description")
    parser.add_argument(
        "--authorization-note",
        required=True,
        help="record why audible physical playback is authorized",
    )
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        help="parent artifact directory; mandatory with --execute",
    )
    parser.add_argument(
        "--target-provenance",
        type=Path,
        required=True,
        help="offline JSON binding the expected serial/model/fingerprint/installed APK",
    )
    parser.add_argument(
        "--expected-route-signature",
        type=Path,
        required=True,
        help="offline strict Android capture-route signature JSON",
    )
    parser.add_argument(
        "--calibration-artifact",
        type=Path,
        required=True,
        help="source-survey/calibration artifact whose SHA-256 is bound into the plan",
    )
    parser.add_argument(
        "--calibration-profile-key",
        required=True,
        help="route/pose/level profile key associated with the calibration artifact",
    )
    parser.add_argument(
        "--output-volume-percent",
        type=int,
        default=DEFAULT_OUTPUT_VOLUME_PERCENT,
    )
    parser.add_argument("--digital-peak", type=float, default=DEFAULT_DIGITAL_PEAK)
    parser.add_argument(
        "--android-source",
        choices=("unprocessed", "mic", "voice_recognition", "camcorder"),
        default="unprocessed",
    )
    parser.add_argument("--compact", action="store_true", help="emit compact dry-run JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.execute and args.artifact_dir is None:
        raise SystemExit("--execute requires --artifact-dir")
    try:
        provenance = json.loads(args.target_provenance.read_text(encoding="utf-8"))
        route_signature = json.loads(
            args.expected_route_signature.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"cannot read required preflight JSON: {error}") from error
    try:
        android_target = provenance["android_target"]
        apk = provenance["hil_apk"]
        expected_serial = android_target["serial"]
        expected_model = android_target["model"]
        expected_fingerprint = android_target["build_fingerprint"]
        expected_apk_sha256 = apk["installed_apk_sha256"]
    except (KeyError, TypeError) as error:
        raise SystemExit(f"target provenance is incomplete: {error}") from error
    if not args.calibration_artifact.is_file():
        raise SystemExit("--calibration-artifact must name an existing file")
    run_id = args.run_id or _default_run_id()
    plan = build_plan(
        run_id=run_id,
        geometry_label=args.geometry_label,
        authorization_note=args.authorization_note,
        expected_android_serial=expected_serial,
        expected_android_model=expected_model,
        expected_android_build_fingerprint=expected_fingerprint,
        expected_android_apk_sha256=expected_apk_sha256,
        expected_route_signature=route_signature,
        calibration_artifact_sha256=sha256_file(args.calibration_artifact),
        calibration_profile_key=args.calibration_profile_key,
        output_volume_percent=args.output_volume_percent,
        digital_peak=args.digital_peak,
        android_source=args.android_source,
    )
    if not args.execute:
        print(json.dumps(plan, indent=None if args.compact else 2, sort_keys=True))
        return 0
    result = execute_plan(plan, args.artifact_dir)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "complete" else 2


if __name__ == "__main__":
    sys.exit(main())
