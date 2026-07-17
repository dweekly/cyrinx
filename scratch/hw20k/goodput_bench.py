#!/usr/bin/env python3
"""Offline planner and referee for the five-frame acoustic goodput benchmark.

The official finite-burst metric is ordered, CRC-verified user payload bits
divided by the *scheduled* span from the first frame's first preamble sample to
the last scheduled frame's final sample.  The denominator is derived from the
transmit schedule, not from the subset of frames the receiver happened to
detect.  Consequently, sync/decode failures at either edge cannot make the
measured rate increase.

No audio, device, ADB, or platform harness code is imported here.  The module
is intentionally standard-library-only so its geometry, verification, metric,
confidence-interval, and planning functions can act as an independent referee.

Examples:
  goodput_bench.py selftest
  goodput_bench.py plan --top 20
  goodput_bench.py plan --cp 384,768 --pilot-every 4,8,16 --json
  goodput_bench.py score result.json --json

A score document contains config and schedule objects, expected_payloads_hex,
and decoded_candidates.  Each candidate supplies payload_hex, a per-block
JSON-boolean crc_valid array, and a scheduled expected_frame_index.  The
candidate_id is optional.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass
from fractions import Fraction
from itertools import product
from statistics import NormalDist
from typing import Any, Mapping, Sequence

CHIRP_SAMPLES = 4096
GUARD_SAMPLES = 2048
SYNC_SYMBOLS = 2
CRC_BLOCK_BYTES = 256
TAIL_BITS = 6
ACCEPTED_HEADLINE_BPS = 5 * 75 * CRC_BLOCK_BYTES * 8 / 21.0
RATE_FRACTIONS = {
    "1/2": Fraction(1, 2),
    "2/3": Fraction(2, 3),
    "3/4": Fraction(3, 4),
    "5/6": Fraction(5, 6),
}


@dataclass(frozen=True)
class PHYConfig:
    """Inputs that determine the shipped bulk-PHY frame geometry."""

    f_lo_hz: float = 1100.0
    f_hi_hz: float = 23000.0
    sample_rate_hz: int = 48000
    nfft: int = 2048
    cp_samples: int = 768
    pilot_every: int = 8
    bits_per_bin: int = 4
    code_rate: str = "3/4"
    data_symbols: int = 64

    def validate(self) -> None:
        if self.sample_rate_hz <= 0 or self.nfft <= 0 or self.cp_samples < 0:
            raise ValueError("sample rate and NFFT must be positive; CP must be nonnegative")
        if self.nfft & (self.nfft - 1):
            raise ValueError("NFFT must be a power of two for the current FFT implementations")
        if not 0 <= self.f_lo_hz < self.f_hi_hz <= self.sample_rate_hz / 2:
            raise ValueError("band must satisfy 0 <= f_lo < f_hi <= Nyquist")
        bin_hz = self.sample_rate_hz / self.nfft
        bin_lo = math.ceil(self.f_lo_hz / bin_hz)
        bin_hi = math.floor(self.f_hi_hz / bin_hz)
        if bin_lo <= 0 or bin_hi >= self.nfft // 2:
            raise ValueError("selected band must exclude DC and Nyquist bins")
        if self.pilot_every <= 1:
            raise ValueError("pilot_every must leave room for data bins")
        if self.bits_per_bin not in (1, 2, 4, 6, 8):
            raise ValueError("bits_per_bin must be one of 1, 2, 4, 6, or 8")
        if self.code_rate not in RATE_FRACTIONS:
            raise ValueError(f"unsupported code rate: {self.code_rate}")
        if self.data_symbols <= 0:
            raise ValueError("data_symbols must be positive")


@dataclass(frozen=True)
class Geometry:
    bin_lo: int
    bin_hi: int
    used_bins: int
    pilot_bins: int
    data_bins: int
    bits_per_symbol: int
    coded_capacity_bits: int
    info_bits: int
    crc_blocks: int
    payload_bytes: int
    payload_bits: int
    crc_bits: int
    fill_bits: int
    frame_samples: int
    frame_seconds: float


@dataclass(frozen=True)
class BurstSchedule:
    """A finite transmit schedule and its explicitly counted outer overhead."""

    frames: int = 5
    inter_frame_gap_s: float = 0.25
    leading_pad_s: float = 0.0
    trailing_pad_s: float = 1.0 / 3.0
    cold_start_overhead_s: float = 0.0

    def validate(self) -> None:
        if self.frames <= 0:
            raise ValueError("frames must be positive")
        values = (
            self.inter_frame_gap_s,
            self.leading_pad_s,
            self.trailing_pad_s,
            self.cold_start_overhead_s,
        )
        if any(not math.isfinite(value) or value < 0 for value in values):
            raise ValueError("all schedule durations must be finite and nonnegative")


@dataclass(frozen=True)
class DecodeCandidate:
    """One receiver output with per-block CRC status.

    Headline scoring requires expected_frame_index to come from the scheduled
    slot or independently measured timing. Content-only attribution is
    available solely as an explicitly enabled diagnostic.
    """

    payload: bytes | None
    crc_valid: tuple[bool, ...]
    expected_frame_index: int | None = None
    candidate_id: str = ""


@dataclass(frozen=True)
class VerificationSummary:
    verified_blocks: int
    total_blocks: int
    assignments: tuple[dict[str, Any], ...]
    decoded_candidates: int
    expected_frames: int


def compute_geometry(config: PHYConfig) -> Geometry:
    """Mirror cyrinx_bulk_compute_geometry without loading the C library."""
    config.validate()
    bin_hz = config.sample_rate_hz / config.nfft
    bin_lo = math.ceil(config.f_lo_hz / bin_hz)
    bin_hi = math.floor(config.f_hi_hz / bin_hz)
    used_bins = bin_hi - bin_lo + 1
    if used_bins <= 1:
        raise ValueError("configured band contains too few FFT bins")
    pilot_bins = (used_bins + config.pilot_every - 1) // config.pilot_every
    data_bins = used_bins - pilot_bins
    bits_per_symbol = data_bins * config.bits_per_bin
    coded_capacity_bits = bits_per_symbol * config.data_symbols
    rate = RATE_FRACTIONS[config.code_rate]
    info_bits = coded_capacity_bits * rate.numerator // rate.denominator - TAIL_BITS
    framed_block_bits = (CRC_BLOCK_BYTES + 4) * 8
    crc_blocks = max(0, info_bits // framed_block_bits)
    payload_bytes = crc_blocks * CRC_BLOCK_BYTES
    payload_bits = payload_bytes * 8
    crc_bits = crc_blocks * 4 * 8
    fill_bits = max(0, info_bits - payload_bits - crc_bits)
    frame_samples = (
        CHIRP_SAMPLES
        + GUARD_SAMPLES
        + (SYNC_SYMBOLS + config.data_symbols) * (config.nfft + config.cp_samples)
    )
    return Geometry(
        bin_lo=bin_lo,
        bin_hi=bin_hi,
        used_bins=used_bins,
        pilot_bins=pilot_bins,
        data_bins=data_bins,
        bits_per_symbol=bits_per_symbol,
        coded_capacity_bits=coded_capacity_bits,
        info_bits=info_bits,
        crc_blocks=crc_blocks,
        payload_bytes=payload_bytes,
        payload_bits=payload_bits,
        crc_bits=crc_bits,
        fill_bits=fill_bits,
        frame_samples=frame_samples,
        frame_seconds=frame_samples / config.sample_rate_hz,
    )


def scheduled_span_s(frame_seconds: float, schedule: BurstSchedule) -> float:
    """First scheduled preamble through the last scheduled frame sample."""
    schedule.validate()
    if not math.isfinite(frame_seconds) or frame_seconds <= 0:
        raise ValueError("frame_seconds must be finite and positive")
    return schedule.frames * frame_seconds + (schedule.frames - 1) * schedule.inter_frame_gap_s


def scheduled_frame_starts(
    frame_samples: int,
    sample_rate_hz: int,
    schedule: BurstSchedule,
    origin_sample: int = 0,
) -> tuple[int, ...]:
    """Return authoritative scheduled starts for timing-based frame IDs."""
    schedule.validate()
    if frame_samples <= 0 or sample_rate_hz <= 0:
        raise ValueError("frame_samples and sample_rate_hz must be positive")
    gap_samples_float = schedule.inter_frame_gap_s * sample_rate_hz
    gap_samples = round(gap_samples_float)
    if not math.isclose(gap_samples_float, gap_samples, abs_tol=1e-9):
        raise ValueError("inter-frame gap must map to an integral sample count")
    period = frame_samples + gap_samples
    return tuple(origin_sample + index * period for index in range(schedule.frames))


def payload_goodput_bps(payload_bits_per_frame: int, verified_fraction: float, span_s: float) -> float:
    """User-payload goodput for one frame-equivalent span.

    This compatibility-sized helper deliberately accepts payload bits, not the
    PHY's info_bits, because info_bits also contains CRCs and random fill.
    """
    if payload_bits_per_frame < 0:
        raise ValueError("payload_bits_per_frame must be nonnegative")
    if not 0 <= verified_fraction <= 1:
        raise ValueError("verified_fraction must be in [0, 1]")
    if span_s <= 0:
        return 0.0
    return payload_bits_per_frame * verified_fraction / span_s


def metric_summary(
    geometry: Geometry,
    schedule: BurstSchedule,
    verified_blocks: int,
) -> dict[str, Any]:
    """Compute finite-burst and amortized goodput from scheduled slots."""
    schedule.validate()
    total_blocks = schedule.frames * geometry.crc_blocks
    if total_blocks <= 0:
        raise ValueError("configuration carries no complete CRC payload block")
    if not 0 <= verified_blocks <= total_blocks:
        raise ValueError("verified_blocks must be within the scheduled denominator")
    verified_bits = verified_blocks * CRC_BLOCK_BYTES * 8
    success_fraction = verified_blocks / total_blocks
    scheduled = scheduled_span_s(geometry.frame_seconds, schedule)
    gross = schedule.leading_pad_s + scheduled + schedule.trailing_pad_s
    cold_start = schedule.cold_start_overhead_s + gross
    steady_period = geometry.frame_seconds + schedule.inter_frame_gap_s
    fail_lo, fail_hi = wilson_failure_interval(verified_blocks, total_blocks)
    return {
        "verified_blocks": verified_blocks,
        "total_blocks": total_blocks,
        "verified_payload_bits": verified_bits,
        "block_success_rate": success_fraction,
        "block_failure_rate": 1.0 - success_fraction,
        "block_failure_wilson95": [fail_lo, fail_hi],
        "block_failure_wilson95_scope": "descriptive_block_level_not_cluster_adjusted",
        "scheduled_span_s": scheduled,
        "gross_span_s": gross,
        "cold_start_span_s": cold_start,
        "steady_state_period_s": steady_period,
        "scheduled_goodput_bps": verified_bits / scheduled,
        "gross_goodput_bps": verified_bits / gross,
        "cold_start_goodput_bps": verified_bits / cold_start,
        "steady_state_goodput_bps": geometry.payload_bits * success_fraction / steady_period,
    }


def exact_position_matches(
    expected: bytes,
    decoded: bytes,
    block_bytes: int = CRC_BLOCK_BYTES,
) -> tuple[bool, ...]:
    """Return byte-exact matches at the same block indices; never set-membership matches."""
    if block_bytes <= 0 or len(expected) % block_bytes:
        raise ValueError("expected payload must contain an integral number of blocks")
    if len(decoded) != len(expected):
        raise ValueError("decoded payload length must equal the scheduled payload length")
    return tuple(
        decoded[offset:offset + block_bytes] == expected[offset:offset + block_bytes]
        for offset in range(0, len(expected), block_bytes)
    )


def ordered_verified_blocks(
    expected: bytes,
    decoded: bytes,
    *,
    crc_valid: Sequence[bool] | None = None,
    crc_valid_count: int | None = None,
    block_bytes: int = CRC_BLOCK_BYTES,
) -> int:
    """Count CRC-valid, byte-exact blocks at the same ordered positions.

    A per-block crc_valid mask is the referee-grade path.  crc_valid_count is a
    compatibility path for the current C API, which exposes only an aggregate;
    it caps exact-position matches by that aggregate but cannot prove which
    individual positions passed CRC.  Callers must not supply both forms.
    """
    matches = exact_position_matches(expected, decoded, block_bytes)
    if (crc_valid is None) == (crc_valid_count is None):
        raise ValueError("supply exactly one of crc_valid or crc_valid_count")
    if crc_valid is not None:
        if len(crc_valid) != len(matches):
            raise ValueError("crc_valid length must equal the payload block count")
        return sum(match and bool(valid) for match, valid in zip(matches, crc_valid))
    assert crc_valid_count is not None
    if not 0 <= crc_valid_count <= len(matches):
        raise ValueError("crc_valid_count is outside the payload block count")
    return min(sum(matches), crc_valid_count)


def verify_exact_positions(
    expected_payloads: Sequence[bytes],
    candidates: Sequence[DecodeCandidate],
    block_bytes: int = CRC_BLOCK_BYTES,
    *,
    allow_content_attribution: bool = False,
) -> VerificationSummary:
    """One-to-one scheduled-frame attribution and ordered byte verification.

    Missing/failed candidates remain in total_blocks because that denominator
    comes exclusively from expected_payloads. Headline scoring requires every
    decoded payload to carry its schedule/timing-derived frame index. The
    opt-in content-attribution mode is diagnostic and must not be reported as
    ordered headline goodput. Dynamic programming prevents duplicate decodes
    from receiving credit twice.
    """
    if not expected_payloads:
        raise ValueError("at least one expected payload is required")
    payload_len = len(expected_payloads[0])
    if payload_len == 0 or payload_len % block_bytes:
        raise ValueError("expected payload size must be a positive integral block count")
    if any(len(payload) != payload_len for payload in expected_payloads):
        raise ValueError("all expected frame payloads must have equal length")
    blocks_per_frame = payload_len // block_bytes
    frame_count = len(expected_payloads)

    scores: list[list[int]] = []
    for candidate in candidates:
        if (
            candidate.payload is not None
            and candidate.expected_frame_index is None
            and not allow_content_attribution
        ):
            raise ValueError("decoded payload lacks a schedule/timing-derived frame index")
        if candidate.expected_frame_index is not None and not (
            0 <= candidate.expected_frame_index < frame_count
        ):
            raise ValueError("candidate expected_frame_index is out of range")
        if len(candidate.crc_valid) != blocks_per_frame:
            raise ValueError("candidate CRC mask length does not match expected payload")
        row = []
        for frame_index, expected in enumerate(expected_payloads):
            allowed = candidate.expected_frame_index == frame_index or (
                allow_content_attribution and candidate.expected_frame_index is None
            )
            if not allowed or candidate.payload is None:
                row.append(0)
            else:
                row.append(
                    ordered_verified_blocks(
                        expected,
                        candidate.payload,
                        crc_valid=candidate.crc_valid,
                        block_bytes=block_bytes,
                    )
                )
        scores.append(row)

    # map used-frame bitmask -> (score, ((candidate_index, frame_index, blocks), ...))
    states: dict[int, tuple[int, tuple[tuple[int, int, int], ...]]] = {0: (0, ())}
    for candidate_index, row in enumerate(scores):
        next_states = dict(states)  # candidate may be a sync/decode failure or duplicate
        for used_mask, (score, assignments) in states.items():
            for frame_index, blocks in enumerate(row):
                bit = 1 << frame_index
                if blocks <= 0 or used_mask & bit:
                    continue
                new_mask = used_mask | bit
                proposal = (score + blocks, assignments + ((candidate_index, frame_index, blocks),))
                current = next_states.get(new_mask)
                if current is None or proposal[0] > current[0]:
                    next_states[new_mask] = proposal
        states = next_states
    _, (verified, winning) = max(
        states.items(),
        key=lambda item: (item[1][0], -len(item[1][1]), -item[0]),
    )
    assignments = tuple(
        {
            "candidate_index": candidate_index,
            "candidate_id": candidates[candidate_index].candidate_id,
            "expected_frame_index": frame_index,
            "verified_blocks": blocks,
        }
        for candidate_index, frame_index, blocks in winning
    )
    return VerificationSummary(
        verified_blocks=verified,
        total_blocks=frame_count * blocks_per_frame,
        assignments=assignments,
        decoded_candidates=len(candidates),
        expected_frames=frame_count,
    )


def wilson_interval(successes: int, total: int, confidence: float = 0.95) -> tuple[float, float]:
    """Two-sided Wilson score interval for a binomial proportion."""
    if total <= 0 or not 0 <= successes <= total:
        raise ValueError("require 0 <= successes <= total and total > 0")
    if not 0 < confidence < 1:
        raise ValueError("confidence must be in (0, 1)")
    z = NormalDist().inv_cdf(0.5 + confidence / 2)
    proportion = successes / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    half_width = (
        z
        * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total))
        / denominator
    )
    return max(0.0, center - half_width), min(1.0, center + half_width)


def wilson_failure_interval(verified_blocks: int, total_blocks: int) -> tuple[float, float]:
    return wilson_interval(total_blocks - verified_blocks, total_blocks)


def plan_candidates(
    base: PHYConfig,
    schedule: BurstSchedule,
    cp_values: Sequence[int],
    pilot_values: Sequence[int],
    bpb_values: Sequence[int],
    rate_values: Sequence[str],
    gap_values: Sequence[float],
    target_bps: float,
) -> list[dict[str, Any]]:
    """Build a deterministic offline full-factorial candidate matrix."""
    rows = []
    for cp, pilots, bpb, rate, gap in product(
        cp_values, pilot_values, bpb_values, rate_values, gap_values
    ):
        config = PHYConfig(
            f_lo_hz=base.f_lo_hz,
            f_hi_hz=base.f_hi_hz,
            sample_rate_hz=base.sample_rate_hz,
            nfft=base.nfft,
            cp_samples=cp,
            pilot_every=pilots,
            bits_per_bin=bpb,
            code_rate=rate,
            data_symbols=base.data_symbols,
        )
        geometry = compute_geometry(config)
        if geometry.crc_blocks == 0:
            continue
        candidate_schedule = BurstSchedule(
            frames=schedule.frames,
            inter_frame_gap_s=gap,
            leading_pad_s=schedule.leading_pad_s,
            trailing_pad_s=schedule.trailing_pad_s,
            cold_start_overhead_s=schedule.cold_start_overhead_s,
        )
        metrics = metric_summary(
            geometry,
            candidate_schedule,
            candidate_schedule.frames * geometry.crc_blocks,
        )
        row_id = (
            f"cp{cp}-p{pilots}-b{bpb}-r{rate.replace('/', '')}-"
            f"g{gap:.4f}".rstrip("0").rstrip(".")
        )
        rows.append(
            {
                "id": row_id,
                "config": asdict(config),
                "schedule": asdict(candidate_schedule),
                "geometry": asdict(geometry),
                "nominal_metrics": metrics,
                "nominal_target_margin_bps": metrics["scheduled_goodput_bps"] - target_bps,
                "nominal_beats_target": metrics["scheduled_goodput_bps"] > target_bps,
            }
        )
    rows.sort(
        key=lambda row: (
            -row["nominal_metrics"]["scheduled_goodput_bps"],
            row["config"]["cp_samples"],
            row["config"]["pilot_every"],
            row["config"]["bits_per_bin"],
            row["config"]["code_rate"],
            row["schedule"]["inter_frame_gap_s"],
        )
    )
    return rows


def score_document(document: Mapping[str, Any]) -> dict[str, Any]:
    """Score a JSON result document with strict per-block CRC masks."""
    config = PHYConfig(**document["config"])
    schedule = BurstSchedule(**document.get("schedule", {}))
    geometry = compute_geometry(config)
    expected = [bytes.fromhex(value) for value in document["expected_payloads_hex"]]
    if len(expected) != schedule.frames:
        raise ValueError("expected_payloads_hex count must equal schedule.frames")
    if any(len(payload) != geometry.payload_bytes for payload in expected):
        raise ValueError("expected payload length does not match computed PHY geometry")
    candidates = []
    for index, raw in enumerate(document.get("decoded_candidates", [])):
        payload_hex = raw.get("payload_hex")
        crc_valid = raw["crc_valid"]
        if not isinstance(crc_valid, list) or any(type(value) is not bool for value in crc_valid):
            raise ValueError("each crc_valid entry must be a JSON boolean")
        expected_frame_index = raw.get("expected_frame_index")
        if type(expected_frame_index) is not int:
            raise ValueError("score candidates require an integer expected_frame_index from timing")
        candidates.append(
            DecodeCandidate(
                payload=None if payload_hex is None else bytes.fromhex(payload_hex),
                crc_valid=tuple(crc_valid),
                expected_frame_index=expected_frame_index,
                candidate_id=str(raw.get("candidate_id", index)),
            )
        )
    verification = verify_exact_positions(expected, candidates)
    expected_total = schedule.frames * geometry.crc_blocks
    if verification.total_blocks != expected_total:
        raise ValueError("verification denominator does not match scheduled PHY geometry")
    metrics = metric_summary(geometry, schedule, verification.verified_blocks)
    margin_bps = metrics["scheduled_goodput_bps"] - ACCEPTED_HEADLINE_BPS
    return {
        "schema": "cyrinx.goodput-result.v1",
        "metric_definition": metric_definition(),
        "config": asdict(config),
        "geometry": asdict(geometry),
        "schedule": asdict(schedule),
        "verification": asdict(verification),
        "metrics": metrics,
        "headline_comparison": {
            "eligible_five_frame_burst": schedule.frames == 5,
            "accepted_headline_bps": ACCEPTED_HEADLINE_BPS,
            "margin_bps": margin_bps,
            "beats_accepted_headline": schedule.frames == 5 and margin_bps > 0,
        },
    }


def metric_definition() -> dict[str, str]:
    return {
        "numerator": "CRC-valid user payload blocks byte-identical at the same frame/block position",
        "scheduled_denominator": "all five scheduled frame slots and four scheduled inter-frame gaps",
        "gross_denominator": "scheduled span plus explicit leading and trailing pads",
        "cold_start_denominator": "gross span plus declared calibration/sounding/setup overhead",
        "steady_state_denominator": "asymptotic frame duration plus one inter-frame gap",
        "failure_policy": "sync/decode failures remain in total scheduled blocks and elapsed slots",
        "accepted_headline": "36,571.428571 bps under the scheduled five-frame metric",
        "ci_scope": (
            "Wilson interval is descriptive at block level; correlated frame errors need run-level inference"
        ),
    }


def _parse_csv(value: str, converter: Any) -> list[Any]:
    try:
        parsed = [converter(item.strip()) for item in value.split(",") if item.strip()]
    except (TypeError, ValueError) as error:
        raise argparse.ArgumentTypeError(str(error)) from error
    if not parsed:
        raise argparse.ArgumentTypeError("list must not be empty")
    return parsed


def _plan_document(args: argparse.Namespace) -> dict[str, Any]:
    base = PHYConfig(
        f_lo_hz=args.f_lo,
        f_hi_hz=args.f_hi,
        sample_rate_hz=args.sample_rate,
        nfft=args.nfft,
        data_symbols=args.symbols,
    )
    schedule = BurstSchedule(
        frames=args.frames,
        leading_pad_s=args.leading_pad,
        trailing_pad_s=args.trailing_pad,
        cold_start_overhead_s=args.cold_start,
    )
    candidates = plan_candidates(
        base,
        schedule,
        _parse_csv(args.cp, int),
        _parse_csv(args.pilot_every, int),
        _parse_csv(args.bpb, int),
        _parse_csv(args.rate, str),
        _parse_csv(args.gap, float),
        args.target_bps,
    )
    return {
        "schema": "cyrinx.goodput-plan.v1",
        "metric_definition": metric_definition(),
        "target_bps": args.target_bps,
        "candidate_count": len(candidates),
        "warning": "rates are geometry-only ceilings; OTA candidates require scheduled, byte-verified trials",
        "candidates": candidates,
    }


def _print_plan_table(document: Mapping[str, Any], top: int) -> None:
    candidates = document["candidates"]
    shown = candidates if top <= 0 else candidates[:top]
    print(
        "rank  cp(ms) pilot bpb rate gap(ms) blocks payload(B) frame(s) "
        "scheduled(kbps) gross(kbps) margin(kbps)"
    )
    for rank, row in enumerate(shown, 1):
        config = row["config"]
        schedule = row["schedule"]
        geometry = row["geometry"]
        metrics = row["nominal_metrics"]
        cp_ms = 1000 * config["cp_samples"] / config["sample_rate_hz"]
        print(
            f"{rank:4d} {cp_ms:7.2f} {config['pilot_every']:5d} "
            f"{config['bits_per_bin']:3d} {config['code_rate']:>4s} "
            f"{schedule['inter_frame_gap_s'] * 1000:7.1f} {geometry['crc_blocks']:6d} "
            f"{geometry['payload_bytes']:10d} {geometry['frame_seconds']:8.3f} "
            f"{metrics['scheduled_goodput_bps'] / 1000:15.2f} "
            f"{metrics['gross_goodput_bps'] / 1000:11.2f} "
            f"{row['nominal_target_margin_bps'] / 1000:12.2f}"
        )
    print(
        f"shown {len(shown)}/{document['candidate_count']} geometry-only candidates; "
        f"target={document['target_bps'] / 1000:.2f} kbps"
    )
    print("OTA success, sync failures, and robustness are deliberately not predicted by this table.")


def _selftest() -> int:
    baseline = PHYConfig()
    geometry = compute_geometry(baseline)
    assert geometry.info_bits == 157050, geometry
    assert geometry.crc_blocks == 75, geometry
    assert geometry.payload_bytes == 19200, geometry
    assert geometry.payload_bits == 153600, geometry
    assert geometry.crc_bits == 2400 and geometry.fill_bits == 1050, geometry
    assert geometry.frame_samples == 192000 and geometry.frame_seconds == 4.0, geometry

    schedule = BurstSchedule(cold_start_overhead_s=2.0)
    metrics = metric_summary(geometry, schedule, verified_blocks=375)
    assert math.isclose(metrics["scheduled_span_s"], 21.0)
    assert math.isclose(metrics["gross_span_s"], 64 / 3)
    assert math.isclose(metrics["cold_start_span_s"], 70 / 3)
    assert math.isclose(metrics["scheduled_goodput_bps"], 36571.42857142857)
    assert math.isclose(metrics["gross_goodput_bps"], 36000.0)
    assert math.isclose(metrics["cold_start_goodput_bps"], 32914.28571428572)
    assert math.isclose(metrics["steady_state_goodput_bps"], 36141.17647058824)
    assert math.isclose(metrics["scheduled_goodput_bps"], ACCEPTED_HEADLINE_BPS)
    assert scheduled_frame_starts(geometry.frame_samples, baseline.sample_rate_hz, BurstSchedule()) == (
        0,
        204000,
        408000,
        612000,
        816000,
    )
    assert geometry.info_bits > geometry.payload_bits
    print("  baseline geometry/accounting: 75 blocks, 4.000 s/frame, 36.571 kbps scheduled")

    expected = [b"AAAABBBB", b"AAAACCCC"]
    candidates = [
        DecodeCandidate(b"AAAACCCC", (True, True), 1, "frame-1"),
        DecodeCandidate(b"AAAAXXXX", (True, True), 0, "frame-0-partial"),
        DecodeCandidate(b"BBBBAAAA", (True, True), 0, "wrong-position"),
        DecodeCandidate(None, (False, False), candidate_id="sync-failure"),
    ]
    verification = verify_exact_positions(expected, candidates, block_bytes=4)
    assert verification.verified_blocks == 3, verification
    assert verification.total_blocks == 4, verification
    assert {item["candidate_id"] for item in verification.assignments} == {
        "frame-1",
        "frame-0-partial",
    }
    assert ordered_verified_blocks(
        b"AAAABBBB", b"AAAABBBB", crc_valid=(True, False), block_bytes=4
    ) == 1
    assert ordered_verified_blocks(
        b"AAAABBBB", b"AAAABBBB", crc_valid_count=1, block_bytes=4
    ) == 1

    swapped_expected = [b"AAAABBBB", b"CCCCDDDD"]
    swapped = [
        DecodeCandidate(swapped_expected[1], (True, True), 0, "swapped-into-0"),
        DecodeCandidate(swapped_expected[0], (True, True), 1, "swapped-into-1"),
    ]
    assert verify_exact_positions(swapped_expected, swapped, block_bytes=4).verified_blocks == 0
    try:
        verify_exact_positions(
            swapped_expected,
            [DecodeCandidate(swapped_expected[0], (True, True))],
            block_bytes=4,
        )
    except ValueError as error:
        assert "frame index" in str(error)
    else:
        raise AssertionError("strict verification accepted content-only frame attribution")
    diagnostic = verify_exact_positions(
        swapped_expected,
        [DecodeCandidate(swapped_expected[0], (True, True))],
        block_bytes=4,
        allow_content_attribution=True,
    )
    assert diagnostic.verified_blocks == 2
    print("  exact verification: scheduled frame/block order, CRC mask, swaps, dedupe, and failures OK")

    lo, hi = wilson_interval(0, 100)
    assert lo == 0.0 and math.isclose(hi, 0.03699349820698568, rel_tol=1e-12)
    fail_lo, fail_hi = wilson_failure_interval(100, 100)
    assert fail_lo == 0.0 and math.isclose(fail_hi, hi, rel_tol=1e-12)
    print("  Wilson 95% interval: boundary cases OK")

    rows = plan_candidates(
        baseline,
        BurstSchedule(),
        cp_values=[768],
        pilot_values=[8],
        bpb_values=[4],
        rate_values=["3/4"],
        gap_values=[0.25],
        target_bps=ACCEPTED_HEADLINE_BPS,
    )
    assert len(rows) == 1
    assert math.isclose(rows[0]["nominal_metrics"]["scheduled_goodput_bps"], ACCEPTED_HEADLINE_BPS)
    assert rows[0]["nominal_beats_target"] is False
    assert math.isclose(rows[0]["nominal_target_margin_bps"], 0.0, abs_tol=1e-12)
    print("  candidate planner: baseline reproduces (but does not beat) accepted 36,571.43 bps")
    print("SELFTEST PASS")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command")

    plan = subparsers.add_parser("plan", help="print an offline candidate matrix")
    plan.add_argument("--f-lo", type=float, default=1100.0)
    plan.add_argument("--f-hi", type=float, default=23000.0)
    plan.add_argument("--sample-rate", type=int, default=48000)
    plan.add_argument("--nfft", type=int, default=2048)
    plan.add_argument("--symbols", type=int, default=64)
    plan.add_argument("--frames", type=int, default=5)
    plan.add_argument(
        "--cp",
        default="96,144,240,384,768",
        help="comma-separated CP samples; add 1536 for the long-tail rung",
    )
    plan.add_argument("--pilot-every", default="4,8,16", help="comma-separated pilot spacings")
    plan.add_argument("--bpb", default="2,4,6", help="comma-separated uniform bits/bin")
    plan.add_argument("--rate", default="1/2,2/3,3/4,5/6", help="comma-separated code rates")
    plan.add_argument("--gap", default="0,0.05,0.1,0.25", help="comma-separated gaps in seconds")
    plan.add_argument("--leading-pad", type=float, default=0.0)
    plan.add_argument("--trailing-pad", type=float, default=1.0 / 3.0)
    plan.add_argument("--cold-start", type=float, default=0.0)
    plan.add_argument("--target-bps", type=float, default=ACCEPTED_HEADLINE_BPS)
    plan.add_argument("--top", type=int, default=25, help="table rows; <=0 prints all")
    plan.add_argument("--json", action="store_true", help="emit the complete matrix as JSON")
    plan.add_argument("--output", help="write JSON to a file instead of stdout")

    score = subparsers.add_parser("score", help="referee a strict JSON result document")
    score.add_argument("input", help="JSON result document")
    score.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    subparsers.add_parser("selftest", help="run deterministic offline checks")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        args = parser.parse_args(["plan"])
    if args.command == "selftest":
        return _selftest()
    if args.command == "plan":
        document = _plan_document(args)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as handle:
                json.dump(document, handle, indent=2, sort_keys=True)
                handle.write("\n")
            print(f"wrote {document['candidate_count']} candidates to {args.output}")
        elif args.json:
            json.dump(document, sys.stdout, indent=2, sort_keys=True)
            sys.stdout.write("\n")
        else:
            _print_plan_table(document, args.top)
        return 0
    if args.command == "score":
        with open(args.input, encoding="utf-8") as handle:
            result = score_document(json.load(handle))
        if args.json:
            json.dump(result, sys.stdout, indent=2, sort_keys=True)
            sys.stdout.write("\n")
        else:
            metrics = result["metrics"]
            verification = result["verification"]
            print(
                f"verified {verification['verified_blocks']}/{verification['total_blocks']} blocks; "
                f"scheduled={metrics['scheduled_goodput_bps'] / 1000:.3f} kbps, "
                f"gross={metrics['gross_goodput_bps'] / 1000:.3f} kbps, "
                f"cold-start={metrics['cold_start_goodput_bps'] / 1000:.3f} kbps, "
                f"steady-state={metrics['steady_state_goodput_bps'] / 1000:.3f} kbps"
            )
        return 0
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
