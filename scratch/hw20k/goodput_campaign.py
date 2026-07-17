#!/usr/bin/env python3
"""Dry-run-first randomized Mac-to-Android goodput campaign.

The default invocation only writes a deterministic campaign manifest.  It
does not import numpy, the C codec binding, the HIL harness, sounddevice, or
ADB-related code.  Physical capture is reachable only through ``--execute``.

Each pair contains the accepted CP768 control and a configurable candidate in
seeded random order.  A headline run schedules five frames, four 250 ms gaps,
and a 1/3 s stream-end tail; a one-frame run is explicitly a non-headline smoke
test.  The headline denominator is always the complete scheduled span; the tail
is reported separately by the gross metric.

Receiver selection and timing attribution are fixed before decoding:

* a stereo max-normalized-chirp score is computed without payload knowledge;
* the strongest gated chirp in a fixed first-frame interval anchors frame zero;
* non-overlapping timing windows map at most one chirp to each schedule slot;
* mic 0, mic 1, and ordered mic-0/mic-1 MRC are all decoded; and
* only the predeclared primary receiver contributes the campaign headline.

CRC counts and known payload bytes are used only after timing attribution, to
verify byte-exact blocks at their original frame/block positions.

Examples:
  goodput_campaign.py --pairs 8 --seed 20260709
  goodput_campaign.py --selftest
  goodput_campaign.py --pairs 8 --seed 20260709 --execute \
    --manifest artifacts/bench/run.manifest.json \
    --artifact-dir artifacts/bench/run.artifacts \
    --geometry-label "predeclared placement" --mac-output-volume 8 \
    --authorization-note "current operator authorization"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import sys
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import goodput_bench as G

SCHEMA = "cyrinx.goodput-ab-campaign.v1"
DEFAULT_SEED = 20260709
PILOT_SELECT_POLICY = "pilot-select01-v1"
RECEIVER_POLICIES = ("mic0", "mic1", "mrc01", PILOT_SELECT_POLICY)
AUTO_V1_DIAGNOSTICS_ABI_VERSION = 1
AUTO_V1_POLICY_VERSION = 1
AUTO_V1_MAX_MRC_TO_PRIMARY_PILOT_RMS_RATIO = 0.95
CLIB_CODEC_PATH_ENV = "CYRINX_BULK_CODEC_PATH"
ROUTE_SIGNATURE_SCHEMA = "cyrinx.android-capture-route-signature.v1"
RECEIVER_PHASE_ESTIMATOR = "sync-snr-p90-floor0.1-unit-harmonic-3pass-v1"
# This is an exploratory bench ceiling, not a transferable safety rating.
# The earlier 14 percent limit came from single-tone qualification and left a
# real wideband OFDM capture only ~12 dB above adjacent A/C-on room noise.  The
# 30 percent ceiling permits a bounded, ascending wideband calibration while
# remaining well below full-scale output; every run still stops on capture
# clipping and records/restores the exact CoreAudio endpoint and volume.
MAX_EXECUTION_OUTPUT_VOLUME_PERCENT = 30
MAX_EXECUTION_WAVEFORM_PEAK = 0.18
HISTORICAL_CALIBRATION_OUTPUT_VOLUME_PERCENT = 100
HISTORICAL_CALIBRATION_WAVEFORM_PEAK = 0.7
PIXEL_VOLUME_50_OUTPUT_VOLUME_PERCENT = 50
PIXEL_VOLUME_50_WAVEFORM_PEAK = 0.18
PIXEL_VOLUME_50_EVIDENCE_PROFILE_KEY = "pixel7a-faceup-volume50-v1"
PIXEL_VOLUME_50_GEOMETRY_LABEL = (
    "Pixel 7a face-up on 0.5-inch soft cloth above the MacBook left function key; "
    "bottom microphone near the MacBook built-in left speaker; MacBook built-in left "
    "speaker only; A/C enabled but thermostat-cycling and uninstrumented; SPL uninstrumented"
)
PIXEL_VOLUME_50_TARGET_PROVENANCE_SHA256 = (
    "5f87abe17227315ae77ea3632bdd21cbfe3364ae5875b1f7d35ca2a04a0c8251"
)
PIXEL_VOLUME_50_ROUTE_CANONICAL_SHA256 = (
    "8da17a36acdd7a79d0f70f9fc085b99a8faf508f862387edec3b093180a827d0"
)
PIXEL_VOLUME_50_QUALIFICATION_SHA256 = (
    "ec28faa1b67bfcea8a615ee4b6dd6d85f3427d79fae075e30b5b6ffd84ff165d"
)
CAPTURE_SETUP_BUDGET_S = 4.0


@dataclass(frozen=True)
class DriveEnvelope:
    envelope_id: str
    maximum_output_volume_percent: int
    maximum_waveform_peak: float
    evidence_basis: str
    scope: str


DEFAULT_DRIVE_ENVELOPE = DriveEnvelope(
    envelope_id="reviewed-conservative-v1",
    maximum_output_volume_percent=MAX_EXECUTION_OUTPUT_VOLUME_PERCENT,
    maximum_waveform_peak=MAX_EXECUTION_WAVEFORM_PEAK,
    evidence_basis="bounded July 2026 wideband calibration ceiling",
    scope="all campaign schedules",
)

# This envelope records the nominal drive used by the original Pixel benchmark.
# It is not an acoustic-exposure rating and is intentionally limited to a single
# one-frame A/B calibration pair with explicit operator authorization.
HISTORICAL_CALIBRATION_DRIVE_ENVELOPE = DriveEnvelope(
    envelope_id="historical-pixel-calibration-v1",
    maximum_output_volume_percent=HISTORICAL_CALIBRATION_OUTPUT_VOLUME_PERCENT,
    maximum_waveform_peak=HISTORICAL_CALIBRATION_WAVEFORM_PEAK,
    evidence_basis="nominal drive used by the original measured Pixel benchmark",
    scope="one-frame non-headline calibration, one A/B pair only",
)

# This narrowly scoped policy promotes the retained Pixel level study into a
# reproducible campaign bound.  It is deliberately device/pose/route specific:
# the percentages are endpoint settings, not acoustic pressure measurements.
PIXEL_VOLUME_50_EVIDENCE_DRIVE_ENVELOPE = DriveEnvelope(
    envelope_id="pixel7a-faceup-vol50-wideband-evidence-v1",
    maximum_output_volume_percent=PIXEL_VOLUME_50_OUTPUT_VOLUME_PERCENT,
    maximum_waveform_peak=PIXEL_VOLUME_50_WAVEFORM_PEAK,
    evidence_basis=(
        "July 2026 matched-waveform Pixel 7a 30/50/70 percent drive study: "
        "50 percent increased retained-capture SNR relative to 30 percent with "
        "stable UNPROCESSED stereo routing and no observed source-sample clipping"
    ),
    scope=(
        "one- or five-frame Mac-left-speaker to Pixel-7a face-up near-field campaigns at "
        "48 kHz, only with the exact hashed target/route/calibration binding; this is a "
        "bench drive policy, not an SPL measurement or acoustic-exposure rating"
    ),
)

PIXEL_VOLUME_50_EXPECTED_TARGET = {
    "serial": "38291JEHN00306",
    "model": "Pixel 7a",
    "build_fingerprint": (
        "google/lynx/lynx:17/CP2A.260705.006/15641320:user/release-keys"
    ),
    "package": "com.dweekly.cyrinxhil",
    "installed_package_apk_sha256": (
        "9cb5fd28021edf8948ce97ded262338da2ca1428b19c263537bac6cbb7a37502"
    ),
}

_EXECUTION_BINDING_REQUIRED_KEYS = (
    "expected_target",
    "target_provenance_path",
    "target_provenance_sha256",
    "expected_route_signature",
    "expected_route_signature_path",
    "expected_route_signature_sha256",
    "calibration_artifact_path",
    "calibration_artifact_sha256",
    "calibration_profile_key",
)
_EXECUTION_BINDING_SHA256_KEYS = (
    "target_provenance_sha256",
    "expected_route_signature_sha256",
    "calibration_artifact_sha256",
)


def _is_sha256_hex(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdefABCDEF" for character in value)
    )


def _read_binding_snapshot(path_text: str, label: str) -> bytes:
    try:
        return Path(path_text).read_bytes()
    except OSError as error:
        raise ValueError(f"execution binding {label} cannot be read: {error}") from error


def _parse_json_snapshot(snapshot: bytes, label: str) -> Any:
    try:
        return json.loads(snapshot)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"execution binding {label} is not valid JSON: {error}") from error


def _target_from_provenance(provenance: Mapping[str, Any]) -> dict[str, str]:
    try:
        android = provenance["android_target"]
        apk = provenance["hil_apk"]
        return {
            "serial": str(android["serial"]),
            "model": str(android["model"]),
            "build_fingerprint": str(android["build_fingerprint"]),
            "package": "com.dweekly.cyrinxhil",
            "installed_package_apk_sha256": str(
                apk["installed_apk_sha256"]
            ).lower(),
        }
    except (KeyError, TypeError) as error:
        raise ValueError("target provenance identity is incomplete") from error


def _validate_pixel_volume_50_route_semantics(route: Mapping[str, Any]) -> None:
    device = route.get("device")
    microphones = route.get("microphones")
    if (
        route.get("actual_source_id") != 9
        or route.get("sample_rate_hz") != 48_000
        or route.get("channels") != 2
        or route.get("encoding_id") != 2
        or not isinstance(device, Mapping)
        or device.get("id") != 23
        or device.get("product_name") != "Pixel 7a"
        or not isinstance(microphones, list)
        or len(microphones) != 2
    ):
        raise ValueError("Pixel volume-50 evidence requires its qualified stereo route")
    expected_mappings = (("bottom", 0), ("back", 1))
    for microphone, (address, channel) in zip(microphones, expected_mappings):
        if not isinstance(microphone, Mapping):
            raise ValueError("Pixel volume-50 evidence route microphones are incomplete")
        if microphone.get("address") != address or microphone.get("channel_mapping") != [
            {"channel": channel, "mode": "direct"}
        ]:
            raise ValueError(
                "Pixel volume-50 evidence requires direct bottom/back channel mappings"
            )


def validate_complete_hashed_execution_binding(
    execution_binding: Mapping[str, Any] | None,
) -> None:
    """Validate the complete content-addressed physical campaign binding."""

    if not isinstance(execution_binding, Mapping):
        raise ValueError("evidence-bound drive requires a complete hashed execution binding")
    missing = [
        key
        for key in _EXECUTION_BINDING_REQUIRED_KEYS
        if key not in execution_binding or execution_binding[key] is None
    ]
    if missing:
        raise ValueError(
            "evidence-bound drive requires a complete hashed execution binding; "
            f"missing {', '.join(missing)}"
        )
    for key in _EXECUTION_BINDING_SHA256_KEYS:
        if not _is_sha256_hex(execution_binding[key]):
            raise ValueError(f"execution binding {key} must be a valid SHA-256 digest")

    expected_target = execution_binding["expected_target"]
    if not isinstance(expected_target, Mapping):
        raise ValueError("execution binding expected_target must be an object")
    target_text_keys = ("serial", "model", "build_fingerprint", "package")
    if any(
        not isinstance(expected_target.get(key), str) or not expected_target[key].strip()
        for key in target_text_keys
    ):
        raise ValueError("execution binding expected_target identity must be complete")
    if not _is_sha256_hex(expected_target.get("installed_package_apk_sha256")):
        raise ValueError("execution binding installed APK must have a valid SHA-256 digest")

    expected_route = execution_binding["expected_route_signature"]
    if (
        not isinstance(expected_route, Mapping)
        or expected_route.get("schema") != ROUTE_SIGNATURE_SCHEMA
    ):
        raise ValueError(f"execution binding route signature must use {ROUTE_SIGNATURE_SCHEMA}")
    canonical_route_hash = sha256_bytes(canonical_json_bytes(expected_route))
    if canonical_route_hash.lower() != str(
        execution_binding["expected_route_signature_sha256"]
    ).lower():
        raise ValueError("execution binding route signature hash does not match its content")

    for key in (
        "target_provenance_path",
        "expected_route_signature_path",
        "calibration_artifact_path",
        "calibration_profile_key",
    ):
        if (
            not isinstance(execution_binding[key], str)
            or not execution_binding[key].strip()
        ):
            raise ValueError(f"execution binding {key} must be nonempty")

    target_snapshot = _read_binding_snapshot(
        execution_binding["target_provenance_path"], "target provenance"
    )
    if sha256_bytes(target_snapshot).lower() != str(
        execution_binding["target_provenance_sha256"]
    ).lower():
        raise ValueError("execution binding target provenance changed after it was loaded")
    target_provenance = _parse_json_snapshot(target_snapshot, "target provenance")
    if not isinstance(target_provenance, Mapping):
        raise ValueError("execution binding target provenance must be an object")
    if _target_from_provenance(target_provenance) != dict(expected_target):
        raise ValueError("execution binding target identity does not match its provenance")

    route_snapshot = _read_binding_snapshot(
        execution_binding["expected_route_signature_path"], "route signature"
    )
    route_from_path = _parse_json_snapshot(route_snapshot, "route signature")
    if not isinstance(route_from_path, Mapping):
        raise ValueError("execution binding route signature must be an object")
    if canonical_json_bytes(route_from_path) != canonical_json_bytes(expected_route):
        raise ValueError("execution binding route signature changed after it was loaded")
    if sha256_bytes(canonical_json_bytes(route_from_path)).lower() != str(
        execution_binding["expected_route_signature_sha256"]
    ).lower():
        raise ValueError("execution binding route signature hash does not match its path")

    calibration_snapshot = _read_binding_snapshot(
        execution_binding["calibration_artifact_path"], "calibration artifact"
    )
    if sha256_bytes(calibration_snapshot).lower() != str(
        execution_binding["calibration_artifact_sha256"]
    ).lower():
        raise ValueError("execution binding calibration artifact changed after it was loaded")


def validate_pixel_volume_50_execution_binding(
    execution_binding: Mapping[str, Any],
) -> None:
    expected_target = execution_binding["expected_target"]
    if dict(expected_target) != PIXEL_VOLUME_50_EXPECTED_TARGET:
        raise ValueError("Pixel volume-50 evidence requires the qualified Pixel 7a target")
    if (
        str(execution_binding["target_provenance_sha256"]).lower()
        != PIXEL_VOLUME_50_TARGET_PROVENANCE_SHA256
    ):
        raise ValueError(
            "Pixel volume-50 evidence requires the qualified target provenance digest"
        )

    expected_route = execution_binding["expected_route_signature"]
    _validate_pixel_volume_50_route_semantics(expected_route)
    if (
        str(execution_binding["expected_route_signature_sha256"]).lower()
        != PIXEL_VOLUME_50_ROUTE_CANONICAL_SHA256
    ):
        raise ValueError("Pixel volume-50 evidence requires the qualified route digest")

    if (
        str(execution_binding["calibration_artifact_sha256"]).lower()
        != PIXEL_VOLUME_50_QUALIFICATION_SHA256
    ):
        raise ValueError("Pixel volume-50 evidence requires the tracked qualification artifact")
    if execution_binding["calibration_profile_key"] != PIXEL_VOLUME_50_EVIDENCE_PROFILE_KEY:
        raise ValueError("Pixel volume-50 evidence requires the qualified profile key")
    qualification_snapshot = _read_binding_snapshot(
        execution_binding["calibration_artifact_path"], "qualification artifact"
    )
    qualification = _parse_json_snapshot(qualification_snapshot, "qualification artifact")
    if (
        not isinstance(qualification, Mapping)
        or qualification.get("envelope_id")
        != PIXEL_VOLUME_50_EVIDENCE_DRIVE_ENVELOPE.envelope_id
        or qualification.get("profile_key") != PIXEL_VOLUME_50_EVIDENCE_PROFILE_KEY
    ):
        raise ValueError("Pixel volume-50 qualification artifact has incompatible semantics")


def validate_drive_envelope_plan(
    drive_envelope: DriveEnvelope,
    profiles: Sequence[CampaignProfile],
    schedule: G.BurstSchedule,
    *,
    pairs: int,
    authorization_note: str | None,
    execution_binding: Mapping[str, Any] | None = None,
    mac_output_volume: int | None = None,
    android_source: str | None = None,
    physical_geometry_label: str | None = None,
) -> None:
    if drive_envelope not in (
        DEFAULT_DRIVE_ENVELOPE,
        HISTORICAL_CALIBRATION_DRIVE_ENVELOPE,
        PIXEL_VOLUME_50_EVIDENCE_DRIVE_ENVELOPE,
    ):
        raise ValueError("drive envelope must be one of the runner's fixed policies")
    if drive_envelope == DEFAULT_DRIVE_ENVELOPE:
        return
    if drive_envelope == HISTORICAL_CALIBRATION_DRIVE_ENVELOPE:
        if schedule.frames != 1 or pairs != 1:
            raise ValueError(
                "historical drive calibration requires one frame and one A/B pair"
            )
        if authorization_note is None or not authorization_note.strip():
            raise ValueError("historical drive calibration requires an authorization note")
        frame_seconds = [
            G.compute_geometry(profile.config).frame_seconds for profile in profiles
        ]
        if max(frame_seconds) > 4.0:
            raise ValueError(
                "historical drive calibration frame duration must not exceed 4.0 seconds"
            )
        return

    if schedule.frames not in (1, 5):
        raise ValueError("Pixel volume-50 evidence drive permits one or five frames")
    if authorization_note is None or not authorization_note.strip():
        raise ValueError("Pixel volume-50 evidence drive requires an authorization note")
    validate_complete_hashed_execution_binding(execution_binding)
    assert execution_binding is not None
    validate_pixel_volume_50_execution_binding(execution_binding)
    if mac_output_volume is None:
        raise ValueError("Pixel volume-50 evidence drive requires an explicit output volume")
    if not 1 <= mac_output_volume <= drive_envelope.maximum_output_volume_percent:
        raise ValueError("Pixel volume-50 evidence drive exceeds its output-volume cap")
    if android_source != "unprocessed":
        raise ValueError("Pixel volume-50 evidence drive requires Android UNPROCESSED source")
    if physical_geometry_label != PIXEL_VOLUME_50_GEOMETRY_LABEL:
        raise ValueError("Pixel volume-50 evidence drive requires its qualified geometry label")
    if any(profile.config.sample_rate_hz != 48_000 for profile in profiles):
        raise ValueError("Pixel volume-50 evidence drive requires 48 kHz profiles")
    if any(
        profile.amplitude > drive_envelope.maximum_waveform_peak for profile in profiles
    ):
        raise ValueError("Pixel volume-50 evidence drive exceeds its waveform-peak cap")


@dataclass(frozen=True)
class CampaignProfile:
    profile_id: str
    config: G.PHYConfig
    amplitude: float = 0.7
    clip_sigma: float = 3.3


@dataclass(frozen=True)
class TimingDetection:
    sample: int
    score: float


@dataclass(frozen=True)
class SlotDetection:
    frame_index: int
    expected_sample: int
    detected_sample: int
    score: float
    residual_samples: int
    discarded_duplicates: tuple[TimingDetection, ...] = ()


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


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


def _stage_execution_decoder_copy(
    artifact_root: Path,
    source_path: Path | None = None,
) -> dict[str, Any]:
    """Create the content-addressed decoder file that execution will load."""
    source = (
        Path(__file__).resolve().with_name("libcyrinxbulk.dylib")
        if source_path is None
        else source_path.resolve(strict=True)
    )
    with source.open("rb") as handle:
        stat_before = os.fstat(handle.fileno())
        source_bytes = handle.read()
        stat_after = os.fstat(handle.fileno())
    identity_before = (
        stat_before.st_dev,
        stat_before.st_ino,
        stat_before.st_size,
        stat_before.st_mtime_ns,
        stat_before.st_ctime_ns,
    )
    identity_after = (
        stat_after.st_dev,
        stat_after.st_ino,
        stat_after.st_size,
        stat_after.st_mtime_ns,
        stat_after.st_ctime_ns,
    )
    if identity_before != identity_after or len(source_bytes) != stat_before.st_size:
        raise RuntimeError("decoder source changed during its single-read snapshot")
    source_sha256 = sha256_bytes(source_bytes)
    artifact_root.mkdir(parents=True, exist_ok=False)
    decoder_directory = artifact_root / "decoder"
    decoder_directory.mkdir()
    frozen_path = decoder_directory / f"libcyrinxbulk-{source_sha256}.dylib"
    frozen_path.write_bytes(source_bytes)
    os.chmod(frozen_path, 0o444)
    frozen_sha256 = sha256_file(frozen_path)
    if frozen_sha256 != source_sha256:
        raise RuntimeError("content-addressed decoder copy failed hash verification")
    os.chmod(decoder_directory, 0o555)
    return {
        "source_path": str(source),
        "source_sha256_from_single_read": source_sha256,
        "frozen_path": str(frozen_path.resolve(strict=True)),
        "expected_sha256": source_sha256,
        "frozen_mode": oct(frozen_path.stat().st_mode & 0o777),
        "decoder_directory_mode": oct(decoder_directory.stat().st_mode & 0o777),
        "content_addressed": True,
    }


def _verify_execution_decoder_copy(
    decoder_binding: dict[str, Any],
    phase: str,
) -> str:
    """Record and verify the staged decoder digest at an execution boundary."""
    path = Path(str(decoder_binding["frozen_path"])).resolve(strict=True)
    observed = sha256_file(path)
    decoder_binding[f"{phase}_sha256"] = observed
    expected = str(decoder_binding["expected_sha256"])
    if observed != expected:
        raise RuntimeError(
            f"content-addressed decoder changed at {phase}: {observed} != {expected}"
        )
    return observed


def load_execution_binding(
    target_provenance_path: Path,
    route_signature_path: Path,
    calibration_artifact_path: Path,
    calibration_profile_key: str,
) -> dict[str, Any]:
    """Load a dry, content-addressed physical target/route/calibration binding."""

    provenance_snapshot = target_provenance_path.read_bytes()
    route_snapshot = route_signature_path.read_bytes()
    calibration_snapshot = calibration_artifact_path.read_bytes()
    provenance = json.loads(provenance_snapshot)
    route_signature = json.loads(route_snapshot)
    if not isinstance(provenance, Mapping):
        raise ValueError("target provenance must be an object")
    if not isinstance(route_signature, Mapping):
        raise ValueError("route signature must be an object")
    target = _target_from_provenance(provenance)
    if route_signature.get("schema") != ROUTE_SIGNATURE_SCHEMA:
        raise ValueError(f"route signature must use {ROUTE_SIGNATURE_SCHEMA}")
    if not calibration_profile_key.strip():
        raise ValueError("calibration profile key must be nonempty")
    return {
        "expected_target": target,
        "target_provenance_path": str(target_provenance_path),
        "target_provenance_sha256": sha256_bytes(provenance_snapshot),
        "expected_route_signature": route_signature,
        "expected_route_signature_path": str(route_signature_path),
        "expected_route_signature_sha256": sha256_bytes(
            canonical_json_bytes(route_signature)
        ),
        "calibration_artifact_path": str(calibration_artifact_path),
        "calibration_artifact_sha256": sha256_bytes(calibration_snapshot),
        "calibration_profile_key": calibration_profile_key.strip(),
    }


def deterministic_payload(
    campaign_seed: int,
    profile_id: str,
    pair_index: int,
    frame_index: int,
    byte_count: int,
) -> bytes:
    """Generate independent reproducible frame payloads from a domain hash."""
    if pair_index < 0 or frame_index < 0 or byte_count < 0:
        raise ValueError("pair/frame indices and byte_count must be nonnegative")
    domain = (
        f"cyrinx-goodput-v1\0{campaign_seed}\0{profile_id}\0"
        f"pair-{pair_index}\0frame-{frame_index}\0"
    ).encode("ascii")
    output = bytearray()
    counter = 0
    while len(output) < byte_count:
        output.extend(hashlib.sha256(domain + counter.to_bytes(8, "big")).digest())
        counter += 1
    return bytes(output[:byte_count])


def profiles_from_args(args: argparse.Namespace) -> tuple[CampaignProfile, CampaignProfile]:
    shared = {
        "f_lo_hz": args.f_lo,
        "f_hi_hz": args.f_hi,
        "sample_rate_hz": args.sample_rate,
        "nfft": args.nfft,
        "data_symbols": args.symbols,
    }
    baseline = CampaignProfile(
        "baseline-cp%d-p%d" % (args.baseline_cp, args.baseline_pilot_every),
        G.PHYConfig(
            cp_samples=args.baseline_cp,
            pilot_every=args.baseline_pilot_every,
            bits_per_bin=4,
            code_rate="3/4",
            **shared,
        ),
        amplitude=args.amplitude,
        clip_sigma=args.clip_sigma,
    )
    candidate = CampaignProfile(
        "candidate-cp%d-p%d-b%d-r%s"
        % (
            args.candidate_cp,
            args.candidate_pilot_every,
            args.candidate_bits_per_bin,
            args.candidate_code_rate.replace("/", ""),
        ),
        G.PHYConfig(
            cp_samples=args.candidate_cp,
            pilot_every=args.candidate_pilot_every,
            bits_per_bin=args.candidate_bits_per_bin,
            code_rate=args.candidate_code_rate,
            **shared,
        ),
        amplitude=args.amplitude,
        clip_sigma=args.clip_sigma,
    )
    return baseline, candidate


def schedule_from_args(args: argparse.Namespace) -> G.BurstSchedule:
    return G.BurstSchedule(
        frames=1 if args.smoke_one_frame else 5,
        inter_frame_gap_s=0.25,
        leading_pad_s=0.0,
        trailing_pad_s=1.0 / 3.0,
        cold_start_overhead_s=args.cold_start_overhead_s,
    )


def _profile_document(profile: CampaignProfile, schedule: G.BurstSchedule) -> dict[str, Any]:
    geometry = G.compute_geometry(profile.config)
    ceiling = G.metric_summary(
        geometry,
        schedule,
        schedule.frames * geometry.crc_blocks,
    )
    return {
        "profile_id": profile.profile_id,
        "config": asdict(profile.config),
        "amplitude": profile.amplitude,
        "clip_sigma": profile.clip_sigma,
        "geometry": asdict(geometry),
        "error_free_ceiling": ceiling,
        "warning": "geometry-only ceiling; not an OTA result",
    }


def _run_payload_records(
    seed: int,
    profile: CampaignProfile,
    pair_index: int,
    schedule: G.BurstSchedule,
    *,
    payload_domain_id: str,
) -> list[dict[str, Any]]:
    geometry = G.compute_geometry(profile.config)
    records = []
    for frame_index in range(schedule.frames):
        payload = deterministic_payload(
            seed,
            payload_domain_id,
            pair_index,
            frame_index,
            geometry.payload_bytes,
        )
        records.append(
            {
                "frame_index": frame_index,
                "byte_count": len(payload),
                "sha256": sha256_bytes(payload),
            }
        )
    return records


def build_plan(
    profiles: Sequence[CampaignProfile],
    schedule: G.BurstSchedule,
    *,
    pairs: int,
    seed: int,
    primary_receiver: str,
    pre_roll_s: float,
    post_roll_s: float,
    origin_search_ms: float,
    anchor_search_stop_ms: float,
    minimum_chirp_score: float,
    minimum_anchor_psr_db: float,
    decode_margin_ms: float,
    android_source: str,
    mac_output_volume: int | None,
    geometry_label: str | None,
    authorization_note: str | None,
    execution_binding: Mapping[str, Any] | None = None,
    drive_envelope: DriveEnvelope = DEFAULT_DRIVE_ENVELOPE,
    paired_identical_payloads: bool = False,
    balanced_pair_order: bool = False,
) -> dict[str, Any]:
    if len(profiles) != 2:
        raise ValueError("an A/B plan requires exactly two profiles")
    if profiles[0].profile_id == profiles[1].profile_id:
        raise ValueError("A/B profiles must have distinct identifiers")
    if pairs <= 0:
        raise ValueError("pairs must be positive")
    if balanced_pair_order and pairs % 2 != 0:
        raise ValueError("balanced pair order requires an even number of pairs")
    validate_drive_envelope_plan(
        drive_envelope,
        profiles,
        schedule,
        pairs=pairs,
        authorization_note=authorization_note,
        execution_binding=execution_binding,
        mac_output_volume=mac_output_volume,
        android_source=android_source,
        physical_geometry_label=geometry_label,
    )
    if primary_receiver not in RECEIVER_POLICIES:
        raise ValueError("unknown primary receiver policy")
    if android_source not in ("camcorder", "unprocessed", "mic", "voice_recognition"):
        raise ValueError("unsupported Android capture source")
    if any(not math.isfinite(value) or value < 0 for value in (pre_roll_s, post_roll_s)):
        raise ValueError("pre/post roll must be finite and nonnegative")
    if not math.isfinite(minimum_chirp_score) or not 0 < minimum_chirp_score <= 1:
        raise ValueError("minimum chirp score must be in (0, 1]")
    if not math.isfinite(origin_search_ms) or origin_search_ms <= 0:
        raise ValueError("timing search must be finite and positive")
    if not math.isfinite(anchor_search_stop_ms) or anchor_search_stop_ms <= 0:
        raise ValueError("anchor search stop must be finite and positive")
    if not math.isfinite(minimum_anchor_psr_db) or minimum_anchor_psr_db < 0:
        raise ValueError("anchor PSR threshold must be finite and nonnegative")
    if not math.isfinite(decode_margin_ms) or decode_margin_ms < 0:
        raise ValueError("decode margin must be finite and nonnegative")
    sample_rates = {profile.config.sample_rate_hz for profile in profiles}
    if len(sample_rates) != 1:
        raise ValueError("both A/B profiles must use the same capture sample rate")
    payload_byte_counts = {
        G.compute_geometry(profile.config).payload_bytes for profile in profiles
    }
    if paired_identical_payloads and len(payload_byte_counts) != 1:
        raise ValueError(
            "paired-identical payloads require equal payload byte counts for both profiles"
        )
    for profile in profiles:
        if not math.isfinite(profile.amplitude) or not 0 < profile.amplitude <= 1:
            raise ValueError(f"{profile.profile_id} amplitude must be finite and in (0, 1]")
        if not math.isfinite(profile.clip_sigma) or not 0 < profile.clip_sigma <= 10:
            raise ValueError(f"{profile.profile_id} clip sigma must be finite and in (0, 10]")
        geometry = G.compute_geometry(profile.config)
        gap_samples = round(schedule.inter_frame_gap_s * profile.config.sample_rate_hz)
        frame_period = geometry.frame_samples + gap_samples
        search_radius = round(origin_search_ms * profile.config.sample_rate_hz / 1000)
        anchor_search_stop = round(
            anchor_search_stop_ms * profile.config.sample_rate_hz / 1000
        )
        if search_radius >= frame_period / 2:
            raise ValueError(
                f"origin search for {profile.profile_id} must be below half its frame period"
            )
        if anchor_search_stop + search_radius >= frame_period:
            raise ValueError(
                f"anchor search for {profile.profile_id} can overlap its second scheduled frame"
            )

    profile_docs = [_profile_document(profile, schedule) for profile in profiles]
    profile_by_id = {profile.profile_id: profile for profile in profiles}
    rng = random.Random(seed)
    runs: list[dict[str, Any]] = []
    payload_hash_owners: dict[str, tuple[int, int, str]] = {}
    balanced_first_profiles: list[str] | None = None
    if balanced_pair_order:
        balanced_first_profiles = [
            profile.profile_id
            for profile in profiles
            for _ in range(pairs // len(profiles))
        ]
        rng.shuffle(balanced_first_profiles)
    for pair_index in range(pairs):
        if balanced_first_profiles is None:
            order = [profile.profile_id for profile in profiles]
            rng.shuffle(order)
        else:
            first_profile = balanced_first_profiles[pair_index]
            order = [
                first_profile,
                next(
                    profile.profile_id
                    for profile in profiles
                    if profile.profile_id != first_profile
                ),
            ]
        for within_pair_order, profile_id in enumerate(order):
            profile = profile_by_id[profile_id]
            geometry = G.compute_geometry(profile.config)
            schedule_starts = G.scheduled_frame_starts(
                geometry.frame_samples,
                profile.config.sample_rate_hz,
                schedule,
            )
            payload_domain_id = (
                "paired-identical-v1" if paired_identical_payloads else profile.profile_id
            )
            payload_records = _run_payload_records(
                seed,
                profile,
                pair_index,
                schedule,
                payload_domain_id=payload_domain_id,
            )
            for payload_record in payload_records:
                payload_hash = payload_record["sha256"]
                owner = (
                    pair_index,
                    int(payload_record["frame_index"]),
                    payload_domain_id,
                )
                previous_owner = payload_hash_owners.get(payload_hash)
                if previous_owner is not None and (
                    not paired_identical_payloads or previous_owner != owner
                ):
                    raise RuntimeError("payload hash collision or invalid payload reuse")
                payload_hash_owners[payload_hash] = owner
            run_id = "pair-%03d-order-%d-%s" % (pair_index, within_pair_order, profile_id)
            runs.append(
                {
                    "run_id": run_id,
                    "pair_index": pair_index,
                    "within_pair_order": within_pair_order,
                    "profile_id": profile_id,
                    "payload_domain_id": payload_domain_id,
                    "scheduled_frame_starts_tx_samples": list(schedule_starts),
                    "scheduled_span_samples": schedule_starts[-1] + geometry.frame_samples,
                    "trailing_pad_samples": round(
                        schedule.trailing_pad_s * profile.config.sample_rate_hz
                    ),
                    "payloads": payload_records,
                }
            )

    return {
        "campaign_seed": seed,
        "pairs": pairs,
        "direction": "mac-to-android",
        "physical_geometry_label": geometry_label or "unspecified-dry-run",
        "physical_authorization_note": authorization_note or "not-authorized-dry-run",
        "profiles": profile_docs,
        "schedule": asdict(schedule),
        "runs": runs,
        "measurement_contract": {
            "metric": G.metric_definition(),
            "accepted_headline_bps": G.ACCEPTED_HEADLINE_BPS,
            "measurement_class": (
                "five-frame-headline" if schedule.frames == 5 else "one-frame-non-headline-smoke"
            ),
            "headline_eligible": schedule.frames == 5,
            "randomization": (
                "seeded exactly balanced first-position assignment across pairs"
                if balanced_pair_order
                else "seeded independent within-pair A/B permutation"
            ),
            "balanced_pair_order": balanced_pair_order,
            "payload_generator": (
                "SHA-256 counter mode over seed/payload-domain/pair/frame domains"
            ),
            "payload_pairing": {
                "policy": (
                    "identical-within-pair-v1"
                    if paired_identical_payloads
                    else "independent-by-profile-v1"
                ),
                "identical_bytes_within_pair": paired_identical_payloads,
                "independent_across_pairs_and_frames": True,
            },
            "timing_score": "max of per-microphone normalized chirp correlations",
            "per_symbol_phase_tracking": {
                "estimator_id": RECEIVER_PHASE_ESTIMATOR,
                "observations": "received pilot phases projected to unit phasors",
                "reliability_source": (
                    "per-pilot SNR estimated only from the two sync symbols"
                ),
                "reliability_bounds": {
                    "minimum_linear_snr": 0.1,
                    "maximum": "linear 90th percentile across pilot SNR estimates",
                },
                "slope_pair_weight": "a*b/(a+b) for adjacent bounded pilot weights",
                "passes": 3,
                "forbidden_inputs": [
                    "payload bytes",
                    "CRC results",
                    "decoded bits",
                    "data-symbol magnitude",
                    "EVM",
                ],
            },
            "origin_anchor": (
                "strongest known-chirp correlation in a predeclared first-frame acquisition "
                "interval, subject to score and peak-to-sidelobe gates; payload bytes, CRC count, "
                "and EVM are forbidden"
            ),
            "slot_assignment": (
                "highest chirp score in each fixed scheduled-slot window; ties use timing residual "
                "then sample index; assignment completes before decoding"
            ),
            "receiver_policies": list(RECEIVER_POLICIES),
            "primary_receiver": primary_receiver,
            "receiver_selection": (
                "predeclared automatic policy v1; each frame selects mic0 or MRC01 "
                "from held-out known pilots before data demapping"
                if primary_receiver == PILOT_SELECT_POLICY
                else "predeclared fixed receiver; no best-of-microphone headline"
            ),
            "automatic_diversity_policy": {
                "policy_id": PILOT_SELECT_POLICY,
                "implementation": "cyrinx_bulk_demodulate2_auto_v1",
                "diagnostics_abi_version": AUTO_V1_DIAGNOSTICS_ABI_VERSION,
                "policy_version": AUTO_V1_POLICY_VERSION,
                "maximum_mrc_to_primary_pilot_rms_ratio": (
                    AUTO_V1_MAX_MRC_TO_PRIMARY_PILOT_RMS_RATIO
                ),
                "training_observations": "even-ordinal known pilots in every data symbol",
                "validation_observations": "odd-ordinal known pilots in every data symbol",
                "score": "full-frame held-out pilot root-mean-square residual",
                "mrc_selection_rule": "MRC RMS / mic0 RMS strictly below 0.95",
                "fallback": "mic0 on ties, boundary, invalid scores, or unavailable mic1",
                "payload_or_crc_used_for_selection": False,
                "data_bins_used_for_selection": False,
            },
            "verification": (
                "ordered C per-block CRC mask AND byte identity at the scheduled frame/block index"
            ),
            "forbidden_selection_inputs": [
                "known payload bytes",
                "data-bin values",
                "LLRs",
                "Viterbi metrics or decoded bits",
                "blocks_ok or CRC results",
                "byte identity",
                "ordinary in-sample EVM",
            ],
            "edge_failure_policy": "all five scheduled slots remain in numerator denominator",
            "predeclared_descriptive_gates": {
                "minimum_complete_pairs": 8,
                "candidate_minimum_scheduled_goodput_bps_strictly_above": (
                    G.ACCEPTED_HEADLINE_BPS
                ),
                "minimum_positive_pair_fraction": 0.875,
                "candidate_aggregate_block_success_not_below_baseline": True,
            },
            "paired_inference": (
                "exact one-sided sign test of candidate-minus-baseline deltas; zero deltas omitted"
            ),
        },
        "capture_policy": {
            "sample_rate_hz": profiles[0].config.sample_rate_hz,
            "channels": 2,
            "android_source": android_source,
            "pre_roll_s": pre_roll_s,
            "post_roll_s": post_roll_s,
            "pre_playback_setup_budget_s": CAPTURE_SETUP_BUDGET_S,
            "anchor_search_start_sample": 0,
            "anchor_search_stop_ms": anchor_search_stop_ms,
            "minimum_anchor_peak_to_sidelobe_db": minimum_anchor_psr_db,
            "origin_search_ms": origin_search_ms,
            "minimum_normalized_chirp_score": minimum_chirp_score,
            "decode_margin_ms": decode_margin_ms,
            "mac_output_volume_percent": (
                "unchanged" if mac_output_volume is None else mac_output_volume
            ),
            "maximum_runner_output_volume_percent": (
                drive_envelope.maximum_output_volume_percent
            ),
            "maximum_runner_waveform_peak": drive_envelope.maximum_waveform_peak,
            "drive_envelope": asdict(drive_envelope),
            "profile_waveform_peaks": {
                profile.profile_id: profile.amplitude for profile in profiles
            },
            "speaker_mapping": "Mac output channel 0 only; channel 1 is zero",
            "warning": (
                "Android recorder preparation wakes the device and brings the HIL app forward; "
                "it does not change media volume"
            ),
        },
        "execution_binding": (
            None if execution_binding is None else dict(execution_binding)
        ),
    }


def wrap_manifest(plan: Mapping[str, Any], *, mode: str) -> dict[str, Any]:
    plan_copy = dict(plan)
    return {
        "schema": SCHEMA,
        "mode": mode,
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "execution_id": uuid.uuid4().hex,
        "plan_sha256": sha256_bytes(canonical_json_bytes(plan_copy)),
        "plan": plan_copy,
        "execution_provenance": None,
        "results": [],
        "summary": None,
    }


def atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def assign_detections_to_slots(
    detections: Sequence[TimingDetection],
    *,
    nominal_origin_sample: int,
    frame_period_samples: int,
    frame_count: int,
    search_radius_samples: int,
    minimum_score: float,
) -> tuple[SlotDetection, ...]:
    """Assign timing-only detections to anchored, non-overlapping slots.

    A window narrower than half the frame period makes the slot identity
    independent of which edge frames were detected.  Within a slot, the
    highest chirp score wins before any decoder or expected payload is called.
    """
    if frame_period_samples <= 0 or frame_count <= 0:
        raise ValueError("frame period and count must be positive")
    if not 0 <= search_radius_samples < frame_period_samples / 2:
        raise ValueError("search radius must be nonnegative and below half the frame period")
    if not 0 < minimum_score <= 1:
        raise ValueError("minimum score must be in (0, 1]")
    if any(detection.sample < 0 or not math.isfinite(detection.score) for detection in detections):
        raise ValueError("detections require nonnegative samples and finite scores")

    assignments: list[SlotDetection] = []
    for frame_index in range(frame_count):
        expected = nominal_origin_sample + frame_index * frame_period_samples
        eligible = [
            detection
            for detection in detections
            if detection.score >= minimum_score
            and abs(detection.sample - expected) <= search_radius_samples
        ]
        if not eligible:
            continue
        ranked = sorted(
            eligible,
            key=lambda detection: (
                -detection.score,
                abs(detection.sample - expected),
                detection.sample,
            ),
        )
        selected = ranked[0]
        assignments.append(
            SlotDetection(
                frame_index=frame_index,
                expected_sample=expected,
                detected_sample=selected.sample,
                score=selected.score,
                residual_samples=selected.sample - expected,
                discarded_duplicates=tuple(ranked[1:]),
            )
        )
    return tuple(assignments)


def normalized_chirp_score(numpy: Any, signal: Any, template: Any) -> Any:
    """FFT-based valid normalized correlation, with no scipy dependency."""
    np = numpy
    x = np.asarray(signal, dtype=np.float64)
    h = np.asarray(template, dtype=np.float64)
    if x.ndim != 1 or h.ndim != 1 or len(h) == 0 or len(x) < len(h):
        raise ValueError("signal/template must be 1-D and signal at least template length")
    convolution_size = len(x) + len(h) - 1
    fft_size = 1 << (convolution_size - 1).bit_length()
    convolution = np.fft.irfft(
        np.fft.rfft(x, fft_size) * np.fft.rfft(h[::-1], fft_size),
        fft_size,
    )[:convolution_size]
    correlation = convolution[len(h) - 1:len(x)]
    cumulative = np.concatenate(([0.0], np.cumsum(x * x)))
    window_energy = cumulative[len(h):] - cumulative[:-len(h)]
    template_energy = float(np.dot(h, h))
    denominator = np.sqrt(np.maximum(window_energy * template_energy, 1e-30))
    return np.abs(correlation) / denominator


def acquisition_anchor_from_score(
    numpy: Any,
    score: Any,
    *,
    search_start_sample: int,
    search_stop_sample: int,
    minimum_score: float,
    minimum_psr_db: float,
    exclusion_samples: int = G.CHIRP_SAMPLES,
) -> dict[str, Any]:
    """Select the first-frame schedule anchor without decoding payload content."""
    np = numpy
    values = np.asarray(score, dtype=np.float64)
    if values.ndim != 1 or not np.all(np.isfinite(values)):
        raise ValueError("anchor score must be a finite one-dimensional array")
    if not 0 <= search_start_sample < search_stop_sample <= len(values):
        raise ValueError("invalid acquisition-anchor search interval")
    if not 0 < minimum_score <= 1 or not math.isfinite(minimum_psr_db):
        raise ValueError("invalid acquisition-anchor thresholds")
    if exclusion_samples < 0:
        raise ValueError("anchor exclusion must be nonnegative")

    window = values[search_start_sample:search_stop_sample]
    relative = int(np.argmax(window))
    sample = search_start_sample + relative
    peak = float(values[sample])
    sidelobes = window.copy()
    low = max(0, relative - exclusion_samples)
    high = min(len(sidelobes), relative + exclusion_samples + 1)
    sidelobes[low:high] = 0.0
    sidelobe = float(np.max(sidelobes)) if len(sidelobes) else 0.0
    psr_db = 20.0 * math.log10(max(peak, 1e-30) / max(sidelobe, 1e-30))
    reasons = []
    if peak < minimum_score:
        reasons.append("anchor score below threshold")
    if psr_db < minimum_psr_db:
        reasons.append("anchor peak-to-sidelobe ratio below threshold")
    return {
        "valid": not reasons,
        "sample": sample,
        "score": peak,
        "sidelobe_score": sidelobe,
        "peak_to_sidelobe_db": psr_db,
        "search_start_sample": search_start_sample,
        "search_stop_sample": search_stop_sample,
        "minimum_score": minimum_score,
        "minimum_peak_to_sidelobe_db": minimum_psr_db,
        "reasons": reasons,
        "selection_inputs": "known chirp correlation only; no decoded bytes, CRC count, or EVM",
    }


def channel_observation_diagnostics(numpy: Any, capture: Any) -> dict[str, Any]:
    """Describe, but do not overclaim, two logical capture observations.

    A stereo ``AudioRecord`` configuration does not establish that Android
    exposes two physical microphones. Exact sample duplication is conclusive
    evidence that the returned observations cannot provide receive diversity.
    Conversely, unequal samples alone do not prove independent microphones;
    processing, resampling, or dither can also make a duplicated source differ.
    """
    np = numpy
    x = np.asarray(capture)
    if x.ndim != 2 or x.shape[1] != 2 or x.shape[0] == 0:
        raise ValueError("channel diagnostics require a nonempty two-channel capture")
    values = x.astype(np.float64, copy=False)
    dc = np.mean(values, axis=0)
    centered = values - dc
    covariance = centered.T @ centered / values.shape[0]
    eigenvalues = np.maximum(np.linalg.eigvalsh(covariance), 0.0)
    total_variance = float(np.sum(eigenvalues))
    eigenvalue_ratio = (
        0.0
        if total_variance == 0.0
        else float(eigenvalues[0] / max(eigenvalues[-1], 1e-300))
    )
    variance_share = (
        eigenvalues / total_variance if total_variance > 0.0 else np.array([0.0, 0.0])
    )
    nonzero_share = variance_share[variance_share > 0.0]
    effective_rank = (
        0.0
        if nonzero_share.size == 0
        else float(np.exp(-np.sum(nonzero_share * np.log(nonzero_share))))
    )
    rms = np.sqrt(np.mean(values * values, axis=0))
    peak = np.max(np.abs(values), axis=0)
    difference_rms = float(np.sqrt(np.mean((values[:, 0] - values[:, 1]) ** 2)))
    exact_equal_frames = int(np.count_nonzero(x[:, 0] == x[:, 1]))
    bit_identical = exact_equal_frames == x.shape[0]
    standard_deviation = np.sqrt(np.diag(covariance))
    denominator = float(standard_deviation[0] * standard_deviation[1])
    correlation = None if denominator == 0.0 else float(covariance[0, 1] / denominator)
    return {
        "logical_channels": 2,
        "frames_compared": int(x.shape[0]),
        "dc_by_channel": [float(value) for value in dc],
        "rms_by_channel": [float(value) for value in rms],
        "peak_abs_by_channel": [float(value) for value in peak],
        "difference_rms": difference_rms,
        "exact_equal_frames": exact_equal_frames,
        "exact_equal_fraction": float(exact_equal_frames / x.shape[0]),
        "bit_identical": bit_identical,
        "correlation": correlation,
        "covariance": [[float(value) for value in row] for row in covariance],
        "covariance_eigenvalues_ascending": [float(value) for value in eigenvalues],
        "small_to_large_eigenvalue_ratio": eigenvalue_ratio,
        "effective_rank": effective_rank,
        "receive_diversity_interpretation": (
            "not-available-bit-identical-logical-channels"
            if bit_identical
            else "not-established-from-sample-inequality-alone"
        ),
    }


def timing_detections_from_score(
    numpy: Any,
    score: Any,
    *,
    nominal_origin_sample: int,
    frame_period_samples: int,
    frame_count: int,
    search_radius_samples: int,
    minimum_score: float,
    candidates_per_slot: int = 3,
    suppression_samples: int = G.CHIRP_SAMPLES // 2,
) -> tuple[TimingDetection, ...]:
    """Extract timing candidates only inside predeclared slot windows."""
    np = numpy
    values = np.asarray(score, dtype=np.float64)
    if values.ndim != 1:
        raise ValueError("score must be one-dimensional")
    detections: list[TimingDetection] = []
    for frame_index in range(frame_count):
        expected = nominal_origin_sample + frame_index * frame_period_samples
        low = max(0, expected - search_radius_samples)
        high = min(len(values), expected + search_radius_samples + 1)
        if low >= high:
            continue
        working = values[low:high].copy()
        for _ in range(candidates_per_slot):
            relative = int(np.argmax(working))
            value = float(working[relative])
            if value < minimum_score:
                break
            sample = low + relative
            detections.append(TimingDetection(sample, value))
            suppress_low = max(0, relative - suppression_samples)
            suppress_high = min(len(working), relative + suppression_samples + 1)
            working[suppress_low:suppress_high] = -1.0
    return tuple(detections)


def _runtime_payloads(
    seed: int,
    profile: CampaignProfile,
    pair_index: int,
    schedule: G.BurstSchedule,
    *,
    payload_domain_id: str | None = None,
) -> list[bytes]:
    geometry = G.compute_geometry(profile.config)
    domain_id = profile.profile_id if payload_domain_id is None else payload_domain_id
    if not domain_id:
        raise ValueError("payload domain ID must be nonempty")
    return [
        deterministic_payload(
            seed,
            domain_id,
            pair_index,
            frame_index,
            geometry.payload_bytes,
        )
        for frame_index in range(schedule.frames)
    ]


def _make_c_config(clib: Any, profile: CampaignProfile) -> Any:
    config = profile.config
    c_config = clib.make_cfg(
        bits_per_bin=config.bits_per_bin,
        rate=config.code_rate,
        n_sym=config.data_symbols,
        f_lo=config.f_lo_hz,
        f_hi=config.f_hi_hz,
        nfft=config.nfft,
        cp=config.cp_samples,
        sr=config.sample_rate_hz,
        amp=profile.amplitude,
        clip_sigma=profile.clip_sigma,
        pilot_every=config.pilot_every,
    )
    return c_config


def _assert_c_geometry(clib: Any, c_config: Any, geometry: G.Geometry) -> None:
    c_geometry = clib.geometry(c_config)
    checks = {
        "bin_lo": (c_geometry.bin_lo, geometry.bin_lo),
        "bin_hi": (c_geometry.bin_hi, geometry.bin_hi),
        "n_blocks": (c_geometry.n_blocks, geometry.crc_blocks),
        "payload_bytes": (c_geometry.payload_bytes, geometry.payload_bytes),
        "frame_samples": (c_geometry.frame_samples, geometry.frame_samples),
    }
    mismatches = [f"{name}: C={got}, referee={want}" for name, (got, want) in checks.items() if got != want]
    if mismatches:
        raise RuntimeError("C/referee geometry mismatch: " + "; ".join(mismatches))


def _array_sha256(numpy: Any, value: Any) -> str:
    array = numpy.ascontiguousarray(value, dtype="<f4")
    return sha256_bytes(memoryview(array).cast("B"))


def _write_float32(path: Path, numpy: Any, value: Any) -> str:
    array = numpy.ascontiguousarray(value, dtype="<f4")
    path.write_bytes(memoryview(array).cast("B"))
    return sha256_file(path)


def _validate_auto_v1_diagnostics(
    diagnostics: Any,
    *,
    context: str,
) -> Mapping[str, Any]:
    """Enforce the frozen automatic-diversity contract independently of ctypes."""
    if not isinstance(diagnostics, Mapping):
        raise RuntimeError(f"{context}: automatic-diversity diagnostics are missing")
    frozen_fields = {
        "abi_version": AUTO_V1_DIAGNOSTICS_ABI_VERSION,
        "policy_version": AUTO_V1_POLICY_VERSION,
        "maximum_mrc_to_primary_pilot_rms_ratio": (
            AUTO_V1_MAX_MRC_TO_PRIMARY_PILOT_RMS_RATIO
        ),
        "payload_or_crc_used_for_selection": False,
        "data_bins_used_for_selection": False,
    }
    for field, expected in frozen_fields.items():
        observed = diagnostics.get(field)
        if observed != expected or (
            field in ("abi_version", "policy_version") and isinstance(observed, bool)
        ):
            raise RuntimeError(
                f"{context}: automatic-diversity {field} {observed!r} != {expected!r}"
            )
    if diagnostics.get("selected_receiver") not in ("mic0", "mrc01"):
        raise RuntimeError(
            f"{context}: invalid automatic-diversity selected receiver "
            f"{diagnostics.get('selected_receiver')!r}"
        )
    valid_reasons = {
        "mrc_improved",
        "primary_margin_not_met",
        "insufficient_pilots",
        "nonfinite_score",
        "resource_failure",
        "second_unavailable",
    }
    if diagnostics.get("selection_reason") not in valid_reasons:
        raise RuntimeError(
            f"{context}: invalid automatic-diversity selection reason "
            f"{diagnostics.get('selection_reason')!r}"
        )
    if not isinstance(diagnostics.get("scores_valid"), bool):
        raise RuntimeError(f"{context}: automatic-diversity scores_valid is not Boolean")
    observations = diagnostics.get("validation_observations")
    if not isinstance(observations, int) or isinstance(observations, bool) or observations < 0:
        raise RuntimeError(
            f"{context}: invalid automatic-diversity validation observation count"
        )
    return diagnostics


def _decode_one_policy(
    clib: Any,
    policy: str,
    c_config: Any,
    capture: Any,
    start: int,
    stop: int,
) -> Mapping[str, Any] | None:
    if policy == "mic0":
        return clib.decode(c_config, capture[start:stop, 0])
    if policy == "mic1":
        return clib.decode(c_config, capture[start:stop, 1])
    if policy == "mrc01":
        return clib.decode2(c_config, capture[start:stop, 0], capture[start:stop, 1])
    if policy == PILOT_SELECT_POLICY:
        decoded = clib.decode2_auto_v1(
            c_config,
            capture[start:stop, 0],
            capture[start:stop, 1],
        )
        if decoded is not None:
            _validate_auto_v1_diagnostics(
                decoded.get("automatic_diversity"),
                context="automatic-diversity frame decode",
            )
        return decoded
    raise ValueError(f"unknown receiver policy: {policy}")


def _assert_auto_v1_selected_output_parity(
    records: Mapping[str, Mapping[str, Any]],
    *,
    context: str,
) -> None:
    """Require auto-v1 output to equal the receiver named by its diagnostics."""
    automatic = records[PILOT_SELECT_POLICY]
    if automatic.get("payload") is None:
        if any(records[policy].get("payload") is not None for policy in ("mic0", "mrc01")):
            raise RuntimeError(
                f"{context}: auto-v1 returned no frame while a selectable receiver decoded"
            )
        return
    diagnostics = _validate_auto_v1_diagnostics(
        automatic.get("automatic_diversity"), context=context
    )
    selected = str(diagnostics["selected_receiver"])
    reference = records[selected]
    if reference.get("payload") is None:
        raise RuntimeError(f"{context}: selected {selected} decode is unavailable")
    for field in ("payload", "block_valid", "blocks_ok", "blocks_total", "evm"):
        if automatic.get(field) != reference.get(field):
            raise RuntimeError(
                f"{context}: auto-v1 {field} differs from selected {selected} decode"
            )


def _score_policy(
    policy: str,
    decoded_records: Sequence[Mapping[str, Any]],
    expected_payloads: Sequence[bytes],
    geometry: G.Geometry,
    schedule: G.BurstSchedule,
) -> dict[str, Any]:
    candidates = []
    for record in decoded_records:
        payload = record.get("payload")
        mask = record["block_valid"]
        candidates.append(
            G.DecodeCandidate(
                payload=payload,
                crc_valid=tuple(mask),
                expected_frame_index=record["frame_index"],
                candidate_id=record["candidate_id"],
            )
        )
    verification = G.verify_exact_positions(expected_payloads, candidates)
    metrics = G.metric_summary(geometry, schedule, verification.verified_blocks)
    headline_eligible = schedule.frames == 5
    return {
        "policy": policy,
        "verification": asdict(verification),
        "metrics": metrics,
        "accepted_headline_bps": G.ACCEPTED_HEADLINE_BPS,
        "headline_eligible": headline_eligible,
        "margin_bps": (
            metrics["scheduled_goodput_bps"] - G.ACCEPTED_HEADLINE_BPS
            if headline_eligible
            else None
        ),
        "beats_accepted_headline": (
            headline_eligible
            and metrics["scheduled_goodput_bps"] > G.ACCEPTED_HEADLINE_BPS
        ),
    }


def _runtime_preflight(
    numpy: Any,
    clib: Any,
    profiles: Sequence[CampaignProfile],
    schedule: G.BurstSchedule,
    seed: int,
) -> None:
    if not getattr(clib, "_HAS_BLOCK_VALIDITY_API", False):
        raise RuntimeError(
            "execution requires a rebuilt libcyrinxbulk with ordered block-validity APIs"
        )
    if not getattr(clib, "_HAS_AUTO_V1_API", False):
        raise RuntimeError(
            "execution requires a rebuilt libcyrinxbulk with automatic-diversity policy v1"
        )
    frozen_binding_constants = {
        "AUTO_V1_DIAGNOSTICS_ABI_VERSION": AUTO_V1_DIAGNOSTICS_ABI_VERSION,
        "AUTO_V1_POLICY_VERSION": AUTO_V1_POLICY_VERSION,
        "AUTO_V1_MAX_MRC_TO_PRIMARY_PILOT_RMS_RATIO": (
            AUTO_V1_MAX_MRC_TO_PRIMARY_PILOT_RMS_RATIO
        ),
    }
    for name, expected in frozen_binding_constants.items():
        observed = getattr(clib, name, None)
        if observed != expected:
            raise RuntimeError(f"execution codec {name} {observed!r} != {expected!r}")
    for profile in profiles:
        geometry = G.compute_geometry(profile.config)
        c_config = _make_c_config(clib, profile)
        _assert_c_geometry(clib, c_config, geometry)
        payload = deterministic_payload(seed, profile.profile_id, 0, 0, geometry.payload_bytes)
        wave = clib.encode(c_config, payload)
        padded = numpy.concatenate(
            (numpy.zeros(512, dtype=numpy.float32), wave, numpy.zeros(512, dtype=numpy.float32))
        )
        decoded = clib.decode(c_config, padded)
        if decoded is None or decoded["payload"] != payload or not all(decoded["block_valid"]):
            raise RuntimeError(f"C digital-loopback preflight failed for {profile.profile_id}")
        automatic = clib.decode2_auto_v1(c_config, padded, padded)
        if automatic is None:
            raise RuntimeError(
                f"C automatic-diversity preflight failed for {profile.profile_id}"
            )
        diagnostics = _validate_auto_v1_diagnostics(
            automatic.get("automatic_diversity"),
            context=f"{profile.profile_id} digital-loopback preflight",
        )
        if (
            diagnostics.get("selected_receiver") != "mic0"
            or diagnostics.get("selection_reason") != "primary_margin_not_met"
            or diagnostics.get("scores_valid") is not True
        ):
            raise RuntimeError(
                f"{profile.profile_id} identical-channel preflight did not fail closed to mic0"
            )
        _assert_auto_v1_selected_output_parity(
            {
                "mic0": decoded,
                "mrc01": decoded,
                PILOT_SELECT_POLICY: automatic,
            },
            context=f"{profile.profile_id} digital-loopback preflight",
        )
        if schedule.frames not in (1, 5):
            raise RuntimeError("execution permits only one-frame smoke or five-frame measurement")


def _execution_provenance(
    clib: Any,
    harness: Any,
    *,
    sample_rate_hz: int,
    geometry_label: str,
) -> dict[str, Any]:
    """Collect execution-only source and anonymized endpoint provenance."""
    repository_root = Path(__file__).resolve().parents[2]
    loaded_dylib = Path(clib.loaded_library_path()).resolve(strict=True)
    ctypes_dylib = Path(str(clib._LIB._name)).resolve(strict=True)
    if loaded_dylib != ctypes_dylib:
        raise RuntimeError(
            f"C binding library identity mismatch: codec={loaded_dylib}, ctypes={ctypes_dylib}"
        )
    paths = {
        "campaign_runner": Path(__file__).resolve(),
        "goodput_referee": Path(G.__file__).resolve(),
        "c_binding": Path(clib.__file__).resolve(),
        "hil_harness": Path(harness.__file__).resolve(),
        "audio_transaction_runner": Path(__file__).resolve().with_name(
            "tone_check_hardware.py"
        ),
        "c_bulk_source": repository_root / "Sources/CCyrinx/cyrinx_bulk.c",
        "c_bulk_header": repository_root / "Sources/CCyrinx/include/cyrinx/cyrinx_bulk.h",
        "c_bulk_dylib": loaded_dylib,
    }
    source_hashes = {}
    for label, path in paths.items():
        if not path.is_file():
            raise RuntimeError(f"provenance input is missing: {label}={path}")
        source_hashes[label] = {
            "path": str(path),
            "byte_count": path.stat().st_size,
            "sha256": sha256_file(path),
        }

    def adb_value(arguments: str, label: str) -> str:
        result = harness.adb(arguments)
        value = result.stdout.strip()
        if result.returncode != 0 or not value:
            raise RuntimeError(f"cannot read Android {label}: {result.stderr.strip()}")
        return value

    model = adb_value("shell getprop ro.product.model", "model")
    build_fingerprint = adb_value("shell getprop ro.build.fingerprint", "build fingerprint")
    serial = adb_value("get-serialno", "serial")
    serial_alias = "adb-" + sha256_bytes(("cyrinx-device-alias\0" + serial).encode("utf-8"))[:12]
    return {
        "recorded_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "physical_geometry_label": geometry_label,
        "source_hashes": source_hashes,
        "android": {
            "model": model,
            "build_fingerprint": build_fingerprint,
            "serial_alias": serial_alias,
            "raw_serial_recorded": False,
        },
        "mac_audio": {
            "configured_input_device": harness.MAC_MIC,
            "configured_output_device": harness.MAC_SPK,
            "sample_rate_hz": sample_rate_hz,
        },
    }


def _execute_run(
    numpy: Any,
    clib: Any,
    harness: Any,
    tone_hardware: Any,
    *,
    run_plan: Mapping[str, Any],
    profile: CampaignProfile,
    schedule: G.BurstSchedule,
    campaign_seed: int,
    capture_policy: Mapping[str, Any],
    primary_receiver: str,
    artifact_root: Path,
    execution_id: str,
    execution_binding: Mapping[str, Any],
) -> dict[str, Any]:
    np = numpy
    geometry = G.compute_geometry(profile.config)
    c_config = _make_c_config(clib, profile)
    _assert_c_geometry(clib, c_config, geometry)
    pair_index = int(run_plan["pair_index"])
    payload_domain_id = run_plan.get("payload_domain_id", profile.profile_id)
    if not isinstance(payload_domain_id, str) or not payload_domain_id:
        raise RuntimeError("run plan lacks a valid payload domain ID")
    expected_payloads = _runtime_payloads(
        campaign_seed,
        profile,
        pair_index,
        schedule,
        payload_domain_id=payload_domain_id,
    )
    planned_payloads = run_plan.get("payloads")
    if not isinstance(planned_payloads, list) or len(planned_payloads) != len(
        expected_payloads
    ):
        raise RuntimeError("run plan payload records differ from the scheduled frame count")
    for frame_index, (payload, planned) in enumerate(
        zip(expected_payloads, planned_payloads, strict=True)
    ):
        if not isinstance(planned, Mapping):
            raise RuntimeError("run plan payload record must be an object")
        if (
            int(planned.get("frame_index", -1)) != frame_index
            or int(planned.get("byte_count", -1)) != len(payload)
            or planned.get("sha256") != sha256_bytes(payload)
        ):
            raise RuntimeError("runtime payload differs from its manifest-bound plan record")
    waves = [clib.encode(c_config, payload) for payload in expected_payloads]
    if any(len(wave) != geometry.frame_samples for wave in waves):
        raise RuntimeError("encoder frame length does not match referee geometry")

    gap_samples = round(schedule.inter_frame_gap_s * profile.config.sample_rate_hz)
    tail_samples = round(schedule.trailing_pad_s * profile.config.sample_rate_hz)
    parts = []
    for frame_index, wave in enumerate(waves):
        parts.append(wave)
        if frame_index + 1 < schedule.frames:
            parts.append(np.zeros(gap_samples, dtype=np.float32))
    parts.append(np.zeros(tail_samples, dtype=np.float32))
    mono = np.ascontiguousarray(np.concatenate(parts), dtype=np.float32)
    stereo = np.zeros((len(mono), 2), dtype=np.float32)
    stereo[:, 0] = mono
    transmit_peak = float(np.max(np.abs(stereo)))
    maximum_transmit_peak = float(capture_policy["maximum_runner_waveform_peak"])
    maximum_transmit_peak_f32 = float(np.float32(maximum_transmit_peak))
    if not math.isfinite(transmit_peak) or transmit_peak > maximum_transmit_peak_f32:
        raise RuntimeError(
            "generated transmit waveform exceeds the selected drive envelope: "
            f"realized={transmit_peak}, maximum={maximum_transmit_peak}"
        )
    expected_samples = int(run_plan["scheduled_span_samples"]) + tail_samples
    if len(mono) != expected_samples:
        raise RuntimeError(f"transmit length {len(mono)} != scheduled+tail {expected_samples}")

    run_dir = artifact_root / str(run_plan["run_id"])
    run_dir.mkdir(parents=True, exist_ok=False)
    payload_artifacts = []
    for frame_index, payload in enumerate(expected_payloads):
        path = run_dir / f"expected-frame-{frame_index}.bin"
        path.write_bytes(payload)
        payload_artifacts.append(
            {
                "frame_index": frame_index,
                "path": str(path),
                "sha256": sha256_file(path),
                "byte_count": len(payload),
            }
        )
    tx_path = run_dir / "tx-stereo-f32le.pcm"
    tx_hash = _write_float32(tx_path, np, stereo)

    pre_roll_s = float(capture_policy["pre_roll_s"])
    post_roll_s = float(capture_policy["post_roll_s"])
    setup_budget_s = float(capture_policy["pre_playback_setup_budget_s"])
    if not math.isclose(setup_budget_s, tone_hardware.CAPTURE_SETUP_BUDGET_S):
        raise RuntimeError("campaign setup budget differs from the reviewed hardware runner")
    duration_s = (
        setup_budget_s
        + len(stereo) / profile.config.sample_rate_hz
        + post_roll_s
    )
    capture_path = run_dir / "capture-stereo-s16le.pcm"
    request_prefix = execution_id[:16]
    if len(request_prefix) != 16 or not request_prefix.isalnum():
        raise RuntimeError("manifest execution ID is invalid")
    out_name = f"{request_prefix}-{run_plan['run_id']}.pcm"
    expected_target = execution_binding["expected_target"]
    expected_route_signature = execution_binding["expected_route_signature"]
    target_before_capture = harness.assert_android_target(expected_target)
    capture_request_monotonic_ns = time.monotonic_ns()
    begin_line = None
    done_line = None
    pulled_path = None
    record_finished = False
    capture_begin_observed_monotonic_ns = None
    route_validation_complete_monotonic_ns = None
    target_revalidation_complete_monotonic_ns = None
    pre_roll_complete_monotonic_ns = None
    play_request_monotonic_ns = None
    play_complete_monotonic_ns = None
    playback_state_before = None
    playback_state_after = None
    start_route_realization = None
    pre_playback_setup_elapsed_s = None
    try:
        begin_line = harness.android_record_start(
            duration_s,
            channels=2,
            source=str(capture_policy["android_source"]),
            out_name=out_name,
            sr=profile.config.sample_rate_hz,
        )
        capture_begin_observed_monotonic_ns = time.monotonic_ns()
        start_route_log = harness.android_request_log(out_name)
        start_route_realization = harness.validate_android_record_route_start_log(
            start_route_log,
            out_name,
            expected_source=str(capture_policy["android_source"]),
            expected_sample_rate_hz=profile.config.sample_rate_hz,
            expected_channels=2,
            expected_route_signature=expected_route_signature,
            require_distinct_direct_channel_mappings=True,
        )
        route_validation_complete_monotonic_ns = time.monotonic_ns()
        target_before_playback = harness.assert_android_target(expected_target)
        target_revalidation_complete_monotonic_ns = time.monotonic_ns()
        time.sleep(pre_roll_s)
        pre_roll_complete_monotonic_ns = time.monotonic_ns()
        expected_volume = tone_hardware.MacVolumeState(
            int(capture_policy["mac_output_volume_percent"]),
            False,
        )
        playback_state_before = tone_hardware._assert_playback_state(
            route_controller=tone_hardware.MacDefaultOutputController(),
            endpoint_inspector=tone_hardware.PortAudioOutputInspector(),
            volume_controller=tone_hardware.MacVolumeController(),
            expected_volume=expected_volume,
        )
        play_request_monotonic_ns = time.monotonic_ns()
        pre_playback_setup_elapsed_s = tone_hardware.assert_capture_setup_deadline(
            capture_request_monotonic_ns,
            play_request_monotonic_ns,
        )
        harness.mac_play(stereo, block=True, sr=profile.config.sample_rate_hz)
        play_complete_monotonic_ns = time.monotonic_ns()
        playback_state_after = tone_hardware._assert_playback_state(
            route_controller=tone_hardware.MacDefaultOutputController(),
            endpoint_inspector=tone_hardware.PortAudioOutputInspector(),
            volume_controller=tone_hardware.MacVolumeController(),
            expected_volume=expected_volume,
        )
        done_line, pulled_path = harness.android_record_finish(
            duration_s,
            out_name=out_name,
            local_path=str(capture_path),
        )
        record_finished = True
    finally:
        if begin_line is not None and not record_finished:
            try:
                harness.android_record_finish(
                    duration_s,
                    out_name=out_name,
                    local_path=str(capture_path),
                )
            except Exception:
                pass
    if begin_line is None or done_line is None or pulled_path is None:
        raise RuntimeError("Android AudioRecord did not produce a complete campaign capture")
    realization = harness.parse_android_record_realization(begin_line, done_line, out_name)
    if realization["source"] != str(capture_policy["android_source"]):
        raise RuntimeError(f"Android realized unexpected capture source: {realization}")
    if realization["sample_rate_hz"] != profile.config.sample_rate_hz:
        raise RuntimeError(f"Android realized unexpected sample rate: {realization}")
    if realization["channels"] != 2:
        raise RuntimeError(f"Android realized unexpected channel count: {realization}")
    route_log = harness.android_request_log(out_name)
    route_realization = harness.validate_android_record_route_log(
        route_log,
        out_name,
        expected_source=str(capture_policy["android_source"]),
        expected_sample_rate_hz=profile.config.sample_rate_hz,
        expected_channels=2,
        expected_route_signature=expected_route_signature,
        require_distinct_direct_channel_mappings=True,
    )
    route_log_path = run_dir / "android-route.logcat.txt"
    route_log_path.write_text(route_log, encoding="utf-8")
    capture = harness.load_pcm16(pulled_path, channels=2)
    if capture.ndim != 2 or capture.shape[1] != 2:
        raise RuntimeError(f"expected stereo capture, got shape {capture.shape}")
    if capture.shape[0] != realization["captured_frames"]:
        raise RuntimeError(
            f"pulled {capture.shape[0]} frames; Android reported "
            f"{realization['captured_frames']}"
        )
    channel_diagnostics = channel_observation_diagnostics(np, capture)
    capture_peak_by_channel = np.max(np.abs(capture), axis=0)
    clipped_fraction_by_channel = np.mean(
        np.abs(capture) >= (32766.0 / 32768.0),
        axis=0,
    )
    capture_safety = {
        "peak_abs_by_channel": [float(value) for value in capture_peak_by_channel],
        "clipped_sample_fraction_by_channel": [
            float(value) for value in clipped_fraction_by_channel
        ],
        "android_clipped_samples": int(realization["clipped_samples"]),
        "passed": bool(
            float(np.max(capture_peak_by_channel)) < 0.985
            and int(realization["clipped_samples"]) == 0
            and not np.any(clipped_fraction_by_channel)
        ),
        "failure_policy": "stop the campaign before another playback run",
    }
    if not capture_safety["passed"]:
        failure_path = run_dir / "safety-failure.json"
        atomic_write_json(
            failure_path,
            {
                "schema": "cyrinx.goodput-capture-safety-failure.v1",
                "execution_id": execution_id,
                "run_id": run_plan["run_id"],
                "profile_id": profile.profile_id,
                "safety": capture_safety,
                "capture": {
                    "path": str(capture_path),
                    "sha256": sha256_file(capture_path),
                    "frames": int(capture.shape[0]),
                    "channels": 2,
                },
                "android_audio_record": {
                    "realization": realization,
                    "route_realization": route_realization,
                },
                "android_route_log": {
                    "path": str(route_log_path),
                    "sha256": sha256_file(route_log_path),
                },
            },
        )
        raise RuntimeError(
            "capture clipping/peak safety gate failed; "
            f"evidence={failure_path}: {capture_safety}"
        )

    chirp = waves[0][:G.CHIRP_SAMPLES]
    score0 = normalized_chirp_score(np, capture[:, 0], chirp)
    score1 = normalized_chirp_score(np, capture[:, 1], chirp)
    timing_score = np.maximum(score0, score1)
    frame_period = geometry.frame_samples + gap_samples
    search_radius = round(
        float(capture_policy["origin_search_ms"]) * profile.config.sample_rate_hz / 1000
    )
    anchor = acquisition_anchor_from_score(
        np,
        timing_score,
        search_start_sample=int(capture_policy["anchor_search_start_sample"]),
        search_stop_sample=round(
            float(capture_policy["anchor_search_stop_ms"])
            * profile.config.sample_rate_hz
            / 1000
        ),
        minimum_score=float(capture_policy["minimum_normalized_chirp_score"]),
        minimum_psr_db=float(capture_policy["minimum_anchor_peak_to_sidelobe_db"]),
    )
    if anchor["valid"]:
        raw_detections = timing_detections_from_score(
            np,
            timing_score,
            nominal_origin_sample=int(anchor["sample"]),
            frame_period_samples=frame_period,
            frame_count=schedule.frames,
            search_radius_samples=search_radius,
            minimum_score=float(capture_policy["minimum_normalized_chirp_score"]),
        )
        slot_detections = assign_detections_to_slots(
            raw_detections,
            nominal_origin_sample=int(anchor["sample"]),
            frame_period_samples=frame_period,
            frame_count=schedule.frames,
            search_radius_samples=search_radius,
            minimum_score=float(capture_policy["minimum_normalized_chirp_score"]),
        )
    else:
        raw_detections = ()
        slot_detections = ()

    margin = round(
        float(capture_policy["decode_margin_ms"]) * profile.config.sample_rate_hz / 1000
    )
    decoded_by_policy: dict[str, list[dict[str, Any]]] = {
        policy: [] for policy in RECEIVER_POLICIES
    }
    decoded_artifacts: list[dict[str, Any]] = []
    for detection in slot_detections:
        start = max(0, detection.detected_sample - margin)
        stop = min(len(capture), detection.detected_sample + geometry.frame_samples + margin)
        records_for_slot: dict[str, dict[str, Any]] = {}
        for policy in RECEIVER_POLICIES:
            decoded = _decode_one_policy(clib, policy, c_config, capture, start, stop)
            candidate_id = f"{policy}-slot-{detection.frame_index}"
            if decoded is None:
                record = {
                    "candidate_id": candidate_id,
                    "frame_index": detection.frame_index,
                    "payload": None,
                    "block_valid": [False] * geometry.crc_blocks,
                    "blocks_ok": 0,
                    "blocks_total": geometry.crc_blocks,
                    "evm": None,
                    "automatic_diversity": None,
                }
            else:
                block_valid = decoded.get("block_valid")
                if block_valid is None or len(block_valid) != geometry.crc_blocks:
                    raise RuntimeError("ordered per-block CRC mask missing from C decode")
                record = {
                    "candidate_id": candidate_id,
                    "frame_index": detection.frame_index,
                    "payload": decoded["payload"],
                    "block_valid": list(block_valid),
                    "blocks_ok": int(decoded["blocks_ok"]),
                    "blocks_total": int(decoded["blocks_total"]),
                    "evm": float(decoded["evm"]),
                    "automatic_diversity": decoded.get("automatic_diversity"),
                }
            records_for_slot[policy] = record
        _assert_auto_v1_selected_output_parity(
            records_for_slot,
            context=f"{profile.profile_id} scheduled slot {detection.frame_index}",
        )
        for policy in RECEIVER_POLICIES:
            record = records_for_slot[policy]
            decoded_by_policy[policy].append(record)
            if record["payload"] is None:
                continue
            payload_path = run_dir / f"decoded-{policy}-slot-{detection.frame_index}.bin"
            payload_path.write_bytes(record["payload"])
            decoded_artifacts.append(
                {
                    "candidate_id": record["candidate_id"],
                    "path": str(payload_path),
                    "sha256": sha256_file(payload_path),
                    "byte_count": len(record["payload"]),
                }
            )

    policy_scores = {
        policy: _score_policy(
            policy,
            records,
            expected_payloads,
            geometry,
            schedule,
        )
        for policy, records in decoded_by_policy.items()
    }
    decoded_diagnostics = {
        policy: [
            {
                key: value
                for key, value in record.items()
                if key != "payload"
            }
            | {
                "payload_sha256": (
                    None if record["payload"] is None else sha256_bytes(record["payload"])
                )
            }
            for record in records
        ]
        for policy, records in decoded_by_policy.items()
    }
    result = {
        "run_id": run_plan["run_id"],
        "execution_id": execution_id,
        "pair_index": pair_index,
        "within_pair_order": run_plan["within_pair_order"],
        "profile_id": profile.profile_id,
        "status": "complete",
        "artifacts": {
            "expected_payloads": payload_artifacts,
            "transmit": {
                "path": str(tx_path),
                "sha256": tx_hash,
                "float32_samples": int(stereo.size),
                "frames": int(stereo.shape[0]),
                "channels": 2,
                "peak_abs": transmit_peak,
                "drive_envelope_maximum_peak_abs": maximum_transmit_peak,
            },
            "capture": {
                "path": str(capture_path),
                "sha256": sha256_file(capture_path),
                "pcm16_values": int(capture.size),
                "frames": int(capture.shape[0]),
                "channels": 2,
                "channel_observation_diagnostics": channel_diagnostics,
                "safety": capture_safety,
            },
            "android_route_log": {
                "path": str(route_log_path),
                "sha256": sha256_file(route_log_path),
                "byte_count": route_log_path.stat().st_size,
            },
            "decoded_payloads": decoded_artifacts,
        },
        "android_audio_record": {
            "begin_log": begin_line,
            "done_log": done_line,
            "realization": realization,
            "start_route_realization": start_route_realization,
            "route_realization": route_realization,
            "target_before_capture": target_before_capture,
            "target_before_playback": target_before_playback,
        },
        "playback_transaction": {
            "scheduled_capture_seconds": duration_s,
            "capture_setup_budget_seconds": setup_budget_s,
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
        },
        "timing": {
            "policy": (
                "stereo-max normalized known-chirp acquisition anchor; then fixed schedule windows"
            ),
            "acquisition_anchor": anchor,
            "detections": [
                {
                    **asdict(detection),
                    "discarded_duplicates": [
                        asdict(duplicate) for duplicate in detection.discarded_duplicates
                    ],
                }
                for detection in slot_detections
            ],
            "detected_slots": len(slot_detections),
            "scheduled_slots": schedule.frames,
        },
        "decoded_diagnostics": decoded_diagnostics,
        "policy_scores": policy_scores,
        "primary_receiver": primary_receiver,
        "primary_receiver_interpretation": (
            "mrc-operates-on-duplicate-observations-with-no-diversity-gain"
            if primary_receiver == "mrc01" and channel_diagnostics["bit_identical"]
            else (
                "predeclared-heldout-pilot-automatic-diversity-policy"
                if primary_receiver == PILOT_SELECT_POLICY
                else "predeclared-logical-observation-policy"
            )
        ),
        "headline": policy_scores[primary_receiver],
    }
    result_path = run_dir / "result.json"
    atomic_write_json(result_path, result)
    result["result_artifact"] = {
        "path": str(result_path),
        "sha256": sha256_file(result_path),
    }
    return result


def exact_one_sided_sign_test_greater(deltas: Sequence[float]) -> dict[str, Any]:
    """Exact Pr[X >= wins], X~Binomial(non_ties, 1/2), for positive deltas."""
    if any(not math.isfinite(delta) for delta in deltas):
        raise ValueError("sign-test deltas must be finite")
    wins = sum(delta > 0 for delta in deltas)
    losses = sum(delta < 0 for delta in deltas)
    ties = len(deltas) - wins - losses
    non_ties = wins + losses
    if non_ties == 0:
        tail_numerator = 1
        tail_denominator = 1
    else:
        tail_numerator = sum(math.comb(non_ties, count) for count in range(wins, non_ties + 1))
        tail_denominator = 2**non_ties
    return {
        "alternative": "candidate scheduled goodput is greater than baseline",
        "wins": wins,
        "losses": losses,
        "ties_omitted": ties,
        "non_tied_pairs": non_ties,
        "exact_tail_numerator": tail_numerator,
        "exact_tail_denominator": tail_denominator,
        "p_value": tail_numerator / tail_denominator,
    }


def _automatic_diversity_slot_accounting(
    manifest: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Partition every planned automatic-diversity slot, including no-results."""
    frames_per_run = int(manifest["plan"]["schedule"]["frames"])
    results_by_run = {
        str(result.get("run_id")): result for result in manifest.get("results", [])
    }
    reason_names = (
        "mrc_improved",
        "primary_margin_not_met",
        "insufficient_pilots",
        "nonfinite_score",
        "resource_failure",
        "second_unavailable",
    )
    accounting: dict[str, dict[str, Any]] = {}
    for run in manifest["plan"]["runs"]:
        profile_id = str(run["profile_id"])
        profile = accounting.setdefault(
            profile_id,
            {
                "scheduled_slots": 0,
                "observed_diagnostics": 0,
                "selected_receiver": {"mic0": 0, "mrc01": 0},
                "selection_reason": {reason: 0 for reason in reason_names},
                "slot_outcomes": {
                    "diagnostics_observed": 0,
                    "missing_timing_detection": 0,
                    "decoder_returned_no_frame": 0,
                    "failed_run_unresolved": 0,
                    "run_not_yet_attempted": 0,
                },
            },
        )
        profile["scheduled_slots"] += frames_per_run
        result = results_by_run.get(str(run["run_id"]))
        if result is None:
            profile["slot_outcomes"]["run_not_yet_attempted"] += frames_per_run
            continue
        if result.get("status") != "complete":
            profile["slot_outcomes"]["failed_run_unresolved"] += frames_per_run
            continue
        records = result.get("decoded_diagnostics", {}).get(PILOT_SELECT_POLICY, [])
        if not isinstance(records, list) or len(records) > frames_per_run:
            raise ValueError("automatic-diversity records exceed scheduled slots")
        profile["slot_outcomes"]["missing_timing_detection"] += (
            frames_per_run - len(records)
        )
        for record in records:
            diagnostics = record.get("automatic_diversity")
            if not isinstance(diagnostics, Mapping):
                profile["slot_outcomes"]["decoder_returned_no_frame"] += 1
                continue
            receiver = str(diagnostics.get("selected_receiver"))
            reason = str(diagnostics.get("selection_reason"))
            if receiver not in profile["selected_receiver"]:
                raise ValueError(f"unknown automatic-diversity receiver in summary: {receiver}")
            if reason not in profile["selection_reason"]:
                raise ValueError(f"unknown automatic-diversity reason in summary: {reason}")
            profile["selected_receiver"][receiver] += 1
            profile["selection_reason"][reason] += 1
            profile["observed_diagnostics"] += 1
            profile["slot_outcomes"]["diagnostics_observed"] += 1

    for profile in accounting.values():
        outcomes = profile["slot_outcomes"]
        if sum(outcomes.values()) != profile["scheduled_slots"]:
            raise AssertionError("automatic-diversity slot outcomes do not cover schedule")
        if (
            sum(profile["selected_receiver"].values())
            != profile["observed_diagnostics"]
            or sum(profile["selection_reason"].values())
            != profile["observed_diagnostics"]
        ):
            raise AssertionError("automatic-diversity observed diagnostics totals disagree")
        missing_or_failed = profile["scheduled_slots"] - profile["observed_diagnostics"]
        profile["selected_receiver"]["missing_or_failed"] = missing_or_failed
        profile["selection_reason"]["missing_or_failed"] = missing_or_failed
    return accounting


def _campaign_summary(manifest: Mapping[str, Any]) -> dict[str, Any]:
    measurement_contract = manifest["plan"]["measurement_contract"]
    primary = measurement_contract["primary_receiver"]
    headline_eligible = bool(measurement_contract.get("headline_eligible", True))
    complete = [result for result in manifest["results"] if result.get("status") == "complete"]
    by_profile: dict[str, list[float]] = {}
    gross_by_profile: dict[str, list[float]] = {}
    evm_by_profile: dict[str, list[float]] = {}
    peak_by_profile: dict[str, list[float]] = {}
    block_counts: dict[str, list[int]] = {}
    by_pair: dict[int, dict[str, dict[str, float | int]]] = {}
    for result in complete:
        metrics = result["policy_scores"][primary]["metrics"]
        goodput = float(metrics["scheduled_goodput_bps"])
        verification = result["policy_scores"][primary]["verification"]
        by_profile.setdefault(result["profile_id"], []).append(goodput)
        gross_by_profile.setdefault(result["profile_id"], []).append(
            float(metrics["gross_goodput_bps"])
        )
        for record in result.get("decoded_diagnostics", {}).get(primary, []):
            evm = record.get("evm")
            if evm is not None and math.isfinite(float(evm)):
                evm_by_profile.setdefault(result["profile_id"], []).append(float(evm))
        capture_safety = (
            result.get("artifacts", {}).get("capture", {}).get("safety", {})
        )
        peaks = capture_safety.get("peak_abs_by_channel", [])
        if peaks and all(math.isfinite(float(value)) for value in peaks):
            peak_by_profile.setdefault(result["profile_id"], []).append(
                max(float(value) for value in peaks)
            )
        counts = block_counts.setdefault(result["profile_id"], [0, 0])
        counts[0] += int(verification["verified_blocks"])
        counts[1] += int(verification["total_blocks"])
        by_pair.setdefault(int(result["pair_index"]), {})[result["profile_id"]] = {
            "goodput_bps": goodput,
            "within_pair_order": int(result["within_pair_order"]),
        }
    profiles = [profile["profile_id"] for profile in manifest["plan"]["profiles"]]
    paired_records = []
    if len(profiles) == 2:
        for pair_index, pair in sorted(by_pair.items()):
            if all(profile in pair for profile in profiles):
                difference = float(pair[profiles[1]]["goodput_bps"]) - float(
                    pair[profiles[0]]["goodput_bps"]
                )
                paired_records.append(
                    {
                        "pair_index": pair_index,
                        "candidate_minus_baseline_bps": difference,
                        "candidate_within_pair_order": int(
                            pair[profiles[1]]["within_pair_order"]
                        ),
                    }
                )
    paired_differences = [
        record["candidate_minus_baseline_bps"] for record in paired_records
    ]
    sign_test = exact_one_sided_sign_test_greater(paired_differences)
    block_success = {}
    for profile, counts in block_counts.items():
        failure_interval = (
            G.wilson_failure_interval(counts[0], counts[1])
            if counts[1]
            else (None, None)
        )
        block_success[profile] = {
            "verified_blocks": counts[0],
            "total_blocks": counts[1],
            "rate": counts[0] / counts[1] if counts[1] else None,
            "failure_wilson95": list(failure_interval),
            "failure_wilson95_scope": "descriptive_block_level_not_cluster_adjusted",
        }
    gates = manifest["plan"]["measurement_contract"]["predeclared_descriptive_gates"]
    minimum_pairs = int(gates["minimum_complete_pairs"])
    positive_fraction_required = float(gates["minimum_positive_pair_fraction"])
    positive_pairs = sign_test["wins"]
    positive_fraction = positive_pairs / len(paired_differences) if paired_differences else 0.0
    candidate_values = by_profile.get(profiles[1], []) if len(profiles) == 2 else []
    candidate_minimum_pass = bool(candidate_values) and min(candidate_values) > float(
        gates["candidate_minimum_scheduled_goodput_bps_strictly_above"]
    )
    baseline_success = block_success.get(profiles[0], {}).get("rate") if len(profiles) == 2 else None
    candidate_success = block_success.get(profiles[1], {}).get("rate") if len(profiles) == 2 else None
    block_success_pass = (
        baseline_success is not None
        and candidate_success is not None
        and candidate_success >= baseline_success
    )
    descriptive_gate_results = {
        "minimum_complete_pairs": {
            "required": minimum_pairs,
            "observed": len(paired_differences),
            "pass": len(paired_differences) >= minimum_pairs,
        },
        "candidate_minimum_above_accepted_headline": {
            "threshold_bps_exclusive": gates[
                "candidate_minimum_scheduled_goodput_bps_strictly_above"
            ],
            "observed_minimum_bps": min(candidate_values) if candidate_values else None,
            "pass": candidate_minimum_pass,
        },
        "positive_pair_fraction": {
            "required": positive_fraction_required,
            "positive_pairs": positive_pairs,
            "complete_pairs": len(paired_differences),
            "observed": positive_fraction,
            "all_positive": bool(paired_differences) and positive_pairs == len(paired_differences),
            "pass": bool(paired_differences) and positive_fraction >= positive_fraction_required,
        },
        "candidate_block_success_not_below_baseline": {
            "baseline": baseline_success,
            "candidate": candidate_success,
            "pass": block_success_pass,
        },
    }
    order_strata = {}
    for candidate_order in (0, 1):
        values = [
            record["candidate_minus_baseline_bps"]
            for record in paired_records
            if record["candidate_within_pair_order"] == candidate_order
        ]
        order_strata[str(candidate_order)] = {
            "pairs": len(values),
            "mean_delta_bps": sum(values) / len(values) if values else None,
        }
    summary = {
        "primary_receiver": primary,
        "measurement_class": measurement_contract.get("measurement_class", "unspecified"),
        "headline_eligible": headline_eligible,
        "complete_runs": len(complete),
        "failed_runs": len(manifest["results"]) - len(complete),
        "profile_goodput_bps": {
            profile: {
                "runs": len(values),
                "minimum": min(values),
                "mean": sum(values) / len(values),
                "maximum": max(values),
            }
            for profile, values in by_profile.items()
            if values
        },
        "profile_gross_goodput_bps": {
            profile: {
                "runs": len(values),
                "minimum": min(values),
                "mean": sum(values) / len(values),
                "maximum": max(values),
            }
            for profile, values in gross_by_profile.items()
            if values
        },
        "profile_frame_evm": {
            profile: {
                "frames": len(values),
                "minimum": min(values),
                "mean": sum(values) / len(values),
                "maximum": max(values),
            }
            for profile, values in evm_by_profile.items()
            if values
        },
        "automatic_diversity_selection_counts": (
            _automatic_diversity_slot_accounting(manifest)
        ),
        "profile_capture_peak_abs": {
            profile: {
                "runs": len(values),
                "minimum": min(values),
                "mean": sum(values) / len(values),
                "maximum": max(values),
            }
            for profile, values in peak_by_profile.items()
            if values
        },
        "aggregate_block_success": block_success,
        "complete_pairs": len(paired_differences),
        "paired_records": paired_records,
        "candidate_minus_baseline_paired_bps": {
            "minimum": min(paired_differences) if paired_differences else None,
            "mean": (
                sum(paired_differences) / len(paired_differences)
                if paired_differences
                else None
            ),
            "maximum": max(paired_differences) if paired_differences else None,
        },
        "exact_one_sided_sign_test": sign_test,
        "candidate_order_strata": order_strata,
        "predeclared_descriptive_gate_results": descriptive_gate_results,
        "all_descriptive_gates_pass": headline_eligible and all(
            result["pass"] for result in descriptive_gate_results.values()
        ),
        "inference_warnings": [
            *(
                ["One-frame smoke artifacts are explicitly ineligible for a headline claim."]
                if not headline_eligible
                else []
            ),
            "The sign test uses only delta signs and omits ties; it does not estimate effect size.",
            "Eight pairs provide limited inference; room/device/time generalization needs new sessions.",
            "Inspect candidate-order strata for carryover, heating, AGC, or environmental drift.",
            "Block outcomes within a frame are correlated; block-level Wilson intervals are descriptive.",
        ],
    }
    return summary


def _execute_campaign_runs(
    np: Any,
    clib: Any,
    harness: Any,
    tone_hardware: Any,
    manifest: dict[str, Any],
    profiles: Sequence[CampaignProfile],
    schedule: G.BurstSchedule,
    manifest_path: Path,
    artifact_root: Path,
) -> None:
    profile_by_id = {profile.profile_id: profile for profile in profiles}
    for run_plan in manifest["plan"]["runs"]:
        try:
            result = _execute_run(
                np,
                clib,
                harness,
                tone_hardware,
                run_plan=run_plan,
                profile=profile_by_id[run_plan["profile_id"]],
                schedule=schedule,
                campaign_seed=int(manifest["plan"]["campaign_seed"]),
                capture_policy=manifest["plan"]["capture_policy"],
                primary_receiver=manifest["plan"]["measurement_contract"][
                    "primary_receiver"
                ],
                artifact_root=artifact_root,
                execution_id=str(manifest["execution_id"]),
                execution_binding=manifest["plan"]["execution_binding"],
            )
            manifest["results"].append(result)
            metric = result["headline"]["metrics"]["scheduled_goodput_bps"]
            print(
                f"{run_plan['run_id']}: {result['headline']['verification']['verified_blocks']}/"
                f"{result['headline']['verification']['total_blocks']} blocks, {metric:.2f} bps"
            )
        except BaseException as error:
            manifest["results"].append(
                {
                    "run_id": run_plan["run_id"],
                    "pair_index": run_plan["pair_index"],
                    "within_pair_order": run_plan["within_pair_order"],
                    "profile_id": run_plan["profile_id"],
                    "status": "failed",
                    "error_type": type(error).__name__,
                    "error": str(error),
                }
            )
            manifest["summary"] = _campaign_summary(manifest)
            atomic_write_json(manifest_path, manifest)
            raise
        manifest["summary"] = _campaign_summary(manifest)
        atomic_write_json(manifest_path, manifest)


def execute_campaign(
    manifest: dict[str, Any],
    profiles: Sequence[CampaignProfile],
    schedule: G.BurstSchedule,
    manifest_path: Path,
    artifact_root: Path,
) -> None:
    execution_binding = manifest["plan"].get("execution_binding")
    if not isinstance(execution_binding, dict):
        raise RuntimeError("physical execution requires a hashed target/route/calibration binding")
    expected_target = execution_binding.get("expected_target")
    if not isinstance(expected_target, dict):
        raise RuntimeError("physical execution binding lacks expected_target")

    decoder_binding = _stage_execution_decoder_copy(artifact_root)
    _verify_execution_decoder_copy(decoder_binding, "before_import")
    if "clib" in sys.modules:
        raise RuntimeError(
            "physical execution requires clib to be imported only after staging its decoder"
        )
    previous_codec_path = os.environ.get(CLIB_CODEC_PATH_ENV)
    os.environ[CLIB_CODEC_PATH_ENV] = str(decoder_binding["frozen_path"])
    # These imports are deliberately confined to --execute.  Importing this
    # module, self-testing it, and generating a dry manifest cannot reach the
    # HIL harness, ADB, sounddevice, or any audio device.
    try:
        import numpy as np

        import clib
    finally:
        if previous_codec_path is None:
            os.environ.pop(CLIB_CODEC_PATH_ENV, None)
        else:
            os.environ[CLIB_CODEC_PATH_ENV] = previous_codec_path
    import harness
    import tone_check_hardware as tone_hardware

    loaded_decoder_path = Path(clib.loaded_library_path()).resolve(strict=True)
    ctypes_decoder_path = Path(str(clib._LIB._name)).resolve(strict=True)
    frozen_decoder_path = Path(str(decoder_binding["frozen_path"])).resolve(strict=True)
    decoder_binding["loaded_path"] = str(loaded_decoder_path)
    decoder_binding["ctypes_loaded_path"] = str(ctypes_decoder_path)
    if loaded_decoder_path != frozen_decoder_path or ctypes_decoder_path != frozen_decoder_path:
        raise RuntimeError(
            "execution codec path mismatch: "
            f"codec={loaded_decoder_path}, ctypes={ctypes_decoder_path}, "
            f"expected={frozen_decoder_path}"
        )
    _verify_execution_decoder_copy(decoder_binding, "after_load")

    _runtime_preflight(
        np,
        clib,
        profiles,
        schedule,
        int(manifest["plan"]["campaign_seed"]),
    )
    manifest["execution_provenance"] = _execution_provenance(
        clib,
        harness,
        sample_rate_hz=profiles[0].config.sample_rate_hz,
        geometry_label=str(manifest["plan"]["physical_geometry_label"]),
    )
    manifest["execution_provenance"]["decoder_binding"] = decoder_binding
    manifest["execution_provenance"]["physical_binding"] = execution_binding
    manifest["execution_provenance"]["android_target_before_campaign"] = (
        harness.assert_android_target(expected_target)
    )
    manifest["mode"] = "execute"
    atomic_write_json(manifest_path, manifest)
    requested_volume = int(manifest["plan"]["capture_policy"]["mac_output_volume_percent"])
    volume_transaction = None
    try:
        lock_label = f"goodput-seed-{manifest['plan']['campaign_seed']}"
        volume_controller = tone_hardware.MacVolumeController()
        endpoint_inspector = tone_hardware.PortAudioOutputInspector()
        route_controller = tone_hardware.MacDefaultOutputController()
        with harness.exclusive_android_audio_lock(lock_label), tone_hardware.temporary_mac_volume(
            requested_volume,
            controller=volume_controller,
            endpoint_inspector=endpoint_inspector,
            route_controller=route_controller,
        ) as active_volume:
            volume_transaction = active_volume
            manifest["execution_provenance"]["mac_output_transaction_during"] = {
                "requested": asdict(active_volume.requested),
                "realized": (
                    None if active_volume.realized is None else asdict(active_volume.realized)
                ),
                "original_default_output": (
                    None
                    if active_volume.original_default_output is None
                    else active_volume.original_default_output.to_dict()
                ),
                "selected_default_output": (
                    None
                    if active_volume.selected_default_output is None
                    else active_volume.selected_default_output.to_dict()
                ),
            }
            atomic_write_json(manifest_path, manifest)
            try:
                _verify_execution_decoder_copy(decoder_binding, "before_playback")
                atomic_write_json(manifest_path, manifest)
                _execute_campaign_runs(
                    np,
                    clib,
                    harness,
                    tone_hardware,
                    manifest,
                    profiles,
                    schedule,
                    manifest_path,
                    artifact_root,
                )
            finally:
                harness.mac_stop()
    finally:
        harness.mac_stop()
        decoder_verification_error = None
        try:
            _verify_execution_decoder_copy(decoder_binding, "after_campaign")
            decoder_binding["unchanged_through_campaign"] = True
        except RuntimeError as error:
            decoder_binding["unchanged_through_campaign"] = False
            decoder_binding["after_campaign_error"] = str(error)
            decoder_verification_error = error
        manifest["execution_provenance"]["mac_output_restore_attempted"] = True
        if volume_transaction is not None:
            manifest["execution_provenance"]["mac_output_transaction_after"] = {
                "restored": (
                    None
                    if volume_transaction.restored is None
                    else asdict(volume_transaction.restored)
                ),
                "restored_default_output": (
                    None
                    if volume_transaction.restored_default_output is None
                    else volume_transaction.restored_default_output.to_dict()
                ),
                "original_default_volume_restored": (
                    None
                    if volume_transaction.original_default_volume_restored is None
                    else asdict(volume_transaction.original_default_volume_restored)
                ),
            }
        manifest["execution_provenance"]["mac_output_state_after"] = (
            harness.mac_get_output_state()
        )
        atomic_write_json(manifest_path, manifest)
        if decoder_verification_error is not None:
            raise decoder_verification_error


def assert_dry_run_import_boundary() -> None:
    """Fail if planning/selftests accidentally load execution-only modules."""
    forbidden = [
        name
        for name in ("numpy", "clib", "harness", "sounddevice", "adb_shell")
        if name in sys.modules
    ]
    if forbidden:
        raise AssertionError("dry-run imported execution-only modules: " + ", ".join(forbidden))


def run_selftests() -> None:
    assert_dry_run_import_boundary()
    schedule = G.BurstSchedule()
    baseline = CampaignProfile("baseline-cp768-p8", G.PHYConfig(cp_samples=768))
    candidate = CampaignProfile("candidate-cp240-p8", G.PHYConfig(cp_samples=240))
    baseline_geometry = G.compute_geometry(baseline.config)
    candidate_geometry = G.compute_geometry(candidate.config)
    baseline_metrics = G.metric_summary(
        baseline_geometry,
        schedule,
        schedule.frames * baseline_geometry.crc_blocks,
    )
    candidate_metrics = G.metric_summary(
        candidate_geometry,
        schedule,
        schedule.frames * candidate_geometry.crc_blocks,
    )
    assert math.isclose(baseline_metrics["scheduled_goodput_bps"], 36571.42857142857)
    assert math.isclose(candidate_metrics["scheduled_goodput_bps"], 44214.16234887737)
    assert math.isclose(candidate_metrics["gross_goodput_bps"], 43381.66070419883)

    payloads = [
        deterministic_payload(7, baseline.profile_id, 0, index, baseline_geometry.payload_bytes)
        for index in range(schedule.frames)
    ]
    assert len({sha256_bytes(payload) for payload in payloads}) == schedule.frames
    valid = (True,) * baseline_geometry.crc_blocks

    swapped = [
        G.DecodeCandidate(payloads[1], valid, expected_frame_index=0, candidate_id="swap-a"),
        G.DecodeCandidate(payloads[0], valid, expected_frame_index=1, candidate_id="swap-b"),
    ]
    swapped_summary = G.verify_exact_positions(payloads, swapped)
    assert swapped_summary.verified_blocks == 0
    assert swapped_summary.total_blocks == schedule.frames * baseline_geometry.crc_blocks

    missing_edges = [
        G.DecodeCandidate(
            payloads[index],
            valid,
            expected_frame_index=index,
            candidate_id=f"middle-{index}",
        )
        for index in range(1, schedule.frames - 1)
    ]
    middle_summary = G.verify_exact_positions(payloads, missing_edges)
    assert middle_summary.verified_blocks == 3 * baseline_geometry.crc_blocks
    assert middle_summary.total_blocks == 5 * baseline_geometry.crc_blocks
    empty_summary = G.verify_exact_positions(payloads, [])
    assert empty_summary.verified_blocks == 0
    assert empty_summary.total_blocks == 5 * baseline_geometry.crc_blocks
    assert G.metric_summary(baseline_geometry, schedule, 0)["scheduled_goodput_bps"] == 0

    period = baseline_geometry.frame_samples + round(
        schedule.inter_frame_gap_s * baseline.config.sample_rate_hz
    )
    detections = [
        TimingDetection(1005, 0.80),
        TimingDetection(998, 0.90),
        TimingDetection(1000 + period, 0.85),
        TimingDetection(1000 + 4 * period, 0.75),
    ]
    assigned = assign_detections_to_slots(
        detections,
        nominal_origin_sample=1000,
        frame_period_samples=period,
        frame_count=5,
        search_radius_samples=100,
        minimum_score=0.2,
    )
    assert [item.frame_index for item in assigned] == [0, 1, 4]
    assert assigned[0].detected_sample == 998
    assert len(assigned[0].discarded_duplicates) == 1

    plan = build_plan(
        (baseline, candidate),
        schedule,
        pairs=8,
        seed=DEFAULT_SEED,
        primary_receiver="mrc01",
        pre_roll_s=0.7,
        post_roll_s=0.7,
        origin_search_ms=350.0,
        anchor_search_stop_ms=2_000.0,
        minimum_chirp_score=0.12,
        minimum_anchor_psr_db=6.0,
        decode_margin_ms=25.0,
        android_source="unprocessed",
        mac_output_volume=None,
        geometry_label="offline-selftest",
        authorization_note=None,
        execution_binding=None,
    )
    assert len(plan["runs"]) == 16
    assert all(len(run["payloads"]) == 5 for run in plan["runs"])
    all_hashes = [
        payload["sha256"]
        for run in plan["runs"]
        for payload in run["payloads"]
    ]
    assert len(all_hashes) == len(set(all_hashes))
    assert exact_one_sided_sign_test_greater([1.0] * 8)["p_value"] == 1 / 256
    assert exact_one_sided_sign_test_greater([1.0] * 7 + [-1.0])["p_value"] == 9 / 256
    assert exact_one_sided_sign_test_greater([0.0, 0.0])["p_value"] == 1.0
    for pair_index in range(8):
        pair = [run for run in plan["runs"] if run["pair_index"] == pair_index]
        assert len(pair) == 2
        assert {run["profile_id"] for run in pair} == {
            baseline.profile_id,
            candidate.profile_id,
        }

    synthetic_manifest = wrap_manifest(plan, mode="dry-run")
    profile_map = {profile.profile_id: profile for profile in (baseline, candidate)}
    for run in plan["runs"]:
        profile = profile_map[run["profile_id"]]
        geometry = G.compute_geometry(profile.config)
        verification = {
            "verified_blocks": schedule.frames * geometry.crc_blocks,
            "total_blocks": schedule.frames * geometry.crc_blocks,
        }
        synthetic_manifest["results"].append(
            {
                "status": "complete",
                "profile_id": profile.profile_id,
                "pair_index": run["pair_index"],
                "within_pair_order": run["within_pair_order"],
                "policy_scores": {
                    "mrc01": {
                        "verification": verification,
                        "metrics": G.metric_summary(
                            geometry,
                            schedule,
                            verification["verified_blocks"],
                        ),
                    }
                },
            }
        )
    synthetic_summary = _campaign_summary(synthetic_manifest)
    assert synthetic_summary["complete_pairs"] == 8
    assert synthetic_summary["exact_one_sided_sign_test"]["p_value"] == 1 / 256
    assert synthetic_summary["profile_gross_goodput_bps"][candidate.profile_id]["mean"] > 0
    assert synthetic_summary["aggregate_block_success"][candidate.profile_id][
        "failure_wilson95"
    ][0] == 0
    assert synthetic_summary["all_descriptive_gates_pass"]

    assert_dry_run_import_boundary()
    print("goodput_campaign selftest: OK")
    print(f"  accepted baseline: {baseline_metrics['scheduled_goodput_bps']:.2f} bps")
    print(f"  CP240 ceiling:     {candidate_metrics['scheduled_goodput_bps']:.2f} bps")
    print(f"  CP240 gross:       {candidate_metrics['gross_goodput_bps']:.2f} bps")


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="contact hardware and run the plan")
    parser.add_argument("--selftest", action="store_true", help="run deterministic offline tests")
    parser.add_argument("--pairs", type=int, default=8)
    parser.add_argument(
        "--smoke-one-frame",
        action="store_true",
        help="run one frame per profile; artifacts are explicitly ineligible for a headline claim",
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--paired-identical-payloads",
        action="store_true",
        help=(
            "use identical payload bytes for both profiles at each pair/frame index; "
            "requires equal payload capacities and remains independent across pairs/frames"
        ),
    )
    parser.add_argument(
        "--balanced-pair-order",
        action="store_true",
        help="seed and predeclare an exactly balanced first-position assignment",
    )
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--artifact-dir", type=Path)
    parser.add_argument("--primary-receiver", choices=RECEIVER_POLICIES, default="mic0")
    parser.add_argument("--baseline-cp", type=int, default=768)
    parser.add_argument("--baseline-pilot-every", type=int, default=8)
    parser.add_argument("--candidate-pilot-every", type=int, default=8)
    parser.add_argument("--candidate-cp", type=int, default=240)
    parser.add_argument(
        "--candidate-bits-per-bin",
        type=int,
        choices=(1, 2, 4, 6, 8),
        default=4,
    )
    parser.add_argument(
        "--candidate-code-rate",
        choices=("1/2", "2/3", "3/4", "5/6"),
        default="3/4",
    )
    parser.add_argument("--f-lo", type=float, default=1100.0)
    parser.add_argument("--f-hi", type=float, default=23000.0)
    parser.add_argument("--sample-rate", type=int, default=48000)
    parser.add_argument("--nfft", type=int, default=2048)
    parser.add_argument("--symbols", type=int, default=64)
    parser.add_argument(
        "--amplitude",
        type=float,
        help=(
            "waveform peak; required for physical execution (dry runs retain "
            "the historical 0.7 geometry default when omitted)"
        ),
    )
    parser.add_argument("--clip-sigma", type=float, default=3.3)
    parser.add_argument("--pre-roll-s", type=float, default=0.7)
    parser.add_argument("--post-roll-s", type=float, default=0.7)
    parser.add_argument("--origin-search-ms", type=float, default=350.0)
    parser.add_argument("--anchor-search-stop-ms", type=float, default=2000.0)
    parser.add_argument("--minimum-chirp-score", type=float, default=0.12)
    parser.add_argument("--minimum-anchor-psr-db", type=float, default=6.0)
    parser.add_argument("--decode-margin-ms", type=float, default=25.0)
    parser.add_argument("--cold-start-overhead-s", type=float, default=0.0)
    parser.add_argument(
        "--android-source",
        choices=("camcorder", "unprocessed", "mic", "voice_recognition"),
        default="unprocessed",
    )
    parser.add_argument("--mac-output-volume", type=int)
    parser.add_argument(
        "--historical-drive-calibration-envelope",
        action="store_true",
        help=(
            "permit up to the original Pixel benchmark's nominal 100%% output / 0.7 "
            "waveform drive; requires --smoke-one-frame and --pairs 1, remains "
            "non-headline, and is not an acoustic-exposure rating"
        ),
    )
    parser.add_argument(
        "--pixel-volume-50-evidence-envelope",
        action="store_true",
        help=(
            "permit a calibration-bound Pixel 7a campaign at up to 50%% output and "
            "0.18 waveform peak; requires a complete hashed physical binding and "
            "authorization even for a dry plan, and is not an SPL/exposure rating"
        ),
    )
    parser.add_argument(
        "--geometry-label",
        help="explicit physical placement/orientation label; required with --execute",
    )
    parser.add_argument(
        "--authorization-note",
        help="current operator authorization for physical playback; required with --execute",
    )
    parser.add_argument(
        "--target-provenance",
        type=Path,
        help="JSON binding expected Android serial/model/fingerprint/installed APK",
    )
    parser.add_argument(
        "--expected-route-signature",
        type=Path,
        help="strict Android AudioRecord route signature JSON",
    )
    parser.add_argument(
        "--calibration-artifact",
        type=Path,
        help="retained calibration/reanalysis artifact bound into the plan",
    )
    parser.add_argument(
        "--calibration-profile-key",
        help="device/pose/route/level key for the calibration artifact",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = argument_parser().parse_args(argv)
    if args.selftest:
        if args.execute:
            raise SystemExit("--selftest and --execute are mutually exclusive")
        run_selftests()
        return 0
    if (
        int(args.historical_drive_calibration_envelope)
        + int(args.pixel_volume_50_evidence_envelope)
        > 1
    ):
        raise SystemExit("drive-envelope flags are mutually exclusive")
    if args.historical_drive_calibration_envelope:
        drive_envelope = HISTORICAL_CALIBRATION_DRIVE_ENVELOPE
    elif args.pixel_volume_50_evidence_envelope:
        drive_envelope = PIXEL_VOLUME_50_EVIDENCE_DRIVE_ENVELOPE
    else:
        drive_envelope = DEFAULT_DRIVE_ENVELOPE
    if args.historical_drive_calibration_envelope and (
        not args.smoke_one_frame or args.pairs != 1
    ):
        raise SystemExit(
            "--historical-drive-calibration-envelope requires "
            "--smoke-one-frame and --pairs 1"
        )
    if (
        args.mac_output_volume is not None
        and not 1
        <= args.mac_output_volume
        <= drive_envelope.maximum_output_volume_percent
    ):
        raise SystemExit(
            "--mac-output-volume must be in "
            f"[1, {drive_envelope.maximum_output_volume_percent}] for "
            f"drive envelope {drive_envelope.envelope_id}"
        )
    if args.execute and args.amplitude is None:
        raise SystemExit("--execute requires an explicit --amplitude")
    if args.amplitude is None:
        args.amplitude = 0.7
    if not math.isfinite(args.amplitude) or not 0 < args.amplitude <= 1:
        raise SystemExit("--amplitude must be finite and in (0, 1]")
    if (
        args.execute or args.pixel_volume_50_evidence_envelope
    ) and args.amplitude > drive_envelope.maximum_waveform_peak:
        raise SystemExit(
            "amplitude exceeds drive-envelope cap "
            f"{drive_envelope.maximum_waveform_peak} "
            f"({drive_envelope.envelope_id})"
        )
    if not math.isfinite(args.clip_sigma) or not 0 < args.clip_sigma <= 10:
        raise SystemExit("--clip-sigma must be finite and in (0, 10]")
    if args.smoke_one_frame and args.pairs != 1:
        raise SystemExit("--smoke-one-frame requires --pairs 1")
    if args.execute and args.mac_output_volume is None:
        raise SystemExit("--execute requires an explicit --mac-output-volume")
    if args.pixel_volume_50_evidence_envelope and args.mac_output_volume is None:
        raise SystemExit(
            "--pixel-volume-50-evidence-envelope requires an explicit --mac-output-volume"
        )
    if args.execute and (args.geometry_label is None or not args.geometry_label.strip()):
        raise SystemExit("--execute requires a nonempty --geometry-label")
    if args.execute and (
        args.authorization_note is None or not args.authorization_note.strip()
    ):
        raise SystemExit("--execute requires a nonempty --authorization-note")
    if args.pixel_volume_50_evidence_envelope and (
        args.authorization_note is None or not args.authorization_note.strip()
    ):
        raise SystemExit(
            "--pixel-volume-50-evidence-envelope requires a nonempty --authorization-note"
        )
    binding_paths = (
        args.target_provenance,
        args.expected_route_signature,
        args.calibration_artifact,
        args.calibration_profile_key,
    )
    if (args.execute or args.pixel_volume_50_evidence_envelope) and any(
        value is None for value in binding_paths
    ):
        requirement = (
            "--pixel-volume-50-evidence-envelope"
            if args.pixel_volume_50_evidence_envelope
            else "--execute"
        )
        raise SystemExit(
            f"{requirement} requires --target-provenance, --expected-route-signature, "
            "--calibration-artifact, and --calibration-profile-key"
        )
    execution_binding = None
    if all(value is not None for value in binding_paths):
        try:
            execution_binding = load_execution_binding(
                args.target_provenance,
                args.expected_route_signature,
                args.calibration_artifact,
                args.calibration_profile_key,
            )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise SystemExit(f"cannot load physical execution binding: {error}") from error
    elif any(value is not None for value in binding_paths):
        raise SystemExit("physical binding arguments must be supplied together")
    profiles = profiles_from_args(args)
    schedule = schedule_from_args(args)
    plan = build_plan(
        profiles,
        schedule,
        pairs=args.pairs,
        seed=args.seed,
        primary_receiver=args.primary_receiver,
        pre_roll_s=args.pre_roll_s,
        post_roll_s=args.post_roll_s,
        origin_search_ms=args.origin_search_ms,
        anchor_search_stop_ms=args.anchor_search_stop_ms,
        minimum_chirp_score=args.minimum_chirp_score,
        minimum_anchor_psr_db=args.minimum_anchor_psr_db,
        decode_margin_ms=args.decode_margin_ms,
        android_source=args.android_source,
        mac_output_volume=args.mac_output_volume,
        geometry_label=(args.geometry_label.strip() if args.geometry_label else None),
        authorization_note=(
            args.authorization_note.strip() if args.authorization_note else None
        ),
        execution_binding=execution_binding,
        drive_envelope=drive_envelope,
        paired_identical_payloads=args.paired_identical_payloads,
        balanced_pair_order=args.balanced_pair_order,
    )
    manifest = wrap_manifest(plan, mode="execute" if args.execute else "dry-run")
    manifest_path = args.manifest or Path(
        f"artifacts/bench/goodput-ab-seed-{args.seed}.manifest.json"
    )
    artifact_root = args.artifact_dir or Path(
        f"artifacts/bench/goodput-ab-seed-{args.seed}.artifacts"
    )
    if args.execute:
        if manifest_path.exists() or artifact_root.exists():
            raise SystemExit(
                "refusing to overwrite campaign artifacts; choose new --manifest/--artifact-dir paths"
            )
        execute_campaign(manifest, profiles, schedule, manifest_path, artifact_root)
        print(f"campaign complete: {manifest_path}")
    else:
        assert_dry_run_import_boundary()
        atomic_write_json(manifest_path, manifest)
        assert_dry_run_import_boundary()
        print("DRY RUN ONLY: no hardware/audio/ADB modules imported or invoked")
        print(f"manifest: {manifest_path}")
        print(f"plan sha256: {manifest['plan_sha256']}")
        for profile in plan["profiles"]:
            ceiling = profile["error_free_ceiling"]["scheduled_goodput_bps"]
            gross = profile["error_free_ceiling"]["gross_goodput_bps"]
            print(f"  {profile['profile_id']}: scheduled={ceiling:.2f}, gross={gross:.2f} bps")
        print("add --execute only when the physical campaign is authorized")
    return 0


if __name__ == "__main__":
    sys.exit(main())
