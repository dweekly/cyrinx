#!/usr/bin/env python3
"""Build a frozen Spike 8a pre-EQ burst without opening an audio device."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

import pre_eq_replay as replay


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--payload", required=True, action="append", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--metadata-output", type=Path)
    parser.add_argument("--inter-frame-gap-samples", type=int, default=12000)
    parser.add_argument("--trailing-pad-samples", type=int, default=16000)
    return parser.parse_args()


def load_weights(profile: dict[str, object], expected_count: int) -> np.ndarray:
    magnitude_db = np.asarray(profile["weight_magnitude_db"], dtype=float)
    phase = np.asarray(profile["weight_phase_radians"], dtype=float)
    if magnitude_db.shape != (expected_count,) or phase.shape != (expected_count,):
        raise ValueError("profile weight geometry does not match the Cyrinx bulk profile")
    return 10.0 ** (magnitude_db / 20.0) * np.exp(1j * phase)


def main() -> int:
    args = parse_args()
    if args.inter_frame_gap_samples < 0 or args.trailing_pad_samples < 0:
        raise ValueError("padding counts must be nonnegative")
    profile = json.loads(args.profile.read_text())
    cfg, _ = replay.make_configs()
    replay._CONFIG_CACHE = replay.make_configs()
    weights = load_weights(profile, len(cfg.used))
    frames: list[np.ndarray] = []
    frame_metadata: list[dict[str, float | int | str]] = []
    for index, payload_path in enumerate(args.payload):
        payload = payload_path.read_bytes()
        flat, _ = replay.reconstruct_flat(cfg, payload)
        candidate = replay.realize_transmit(cfg, flat.ideal_spectra, weights)
        mono = np.concatenate(
            (cfg.chirp_wave * cfg.amp, np.zeros(replay.modem.GUARD), candidate.body)
        ).astype(np.float32)
        stereo = np.column_stack((mono, np.zeros_like(mono)))
        frames.append(stereo)
        frame_metadata.append(
            {
                "frame_index": index,
                "payload_path": str(payload_path),
                "payload_sha256": replay.sha256_file(payload_path),
                "body_peak": candidate.stats["body_peak"],
                "body_rms": candidate.stats["body_rms"],
                "intentional_clip_fraction": candidate.stats["intentional_clip_fraction"],
            }
        )
    gap = np.zeros((args.inter_frame_gap_samples, 2), dtype=np.float32)
    pieces: list[np.ndarray] = []
    for index, frame in enumerate(frames):
        pieces.append(frame)
        if index + 1 < len(frames):
            pieces.append(gap)
    pieces.append(np.zeros((args.trailing_pad_samples, 2), dtype=np.float32))
    burst = np.concatenate(pieces, axis=0)
    peak = float(np.max(np.abs(burst)))
    if not np.all(np.isfinite(burst)) or peak > cfg.amp + 1e-7:
        raise RuntimeError(f"unsafe generated burst peak {peak}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    burst.astype("<f4").tofile(args.output)
    metadata_path = args.metadata_output or args.output.with_suffix(args.output.suffix + ".json")
    replay.write_json(
        metadata_path,
        {
            "schema": "cyrinx.spike-8a-preeq.tx-burst.v1",
            "profile_path": str(args.profile),
            "profile_sha256": replay.sha256_file(args.profile),
            "output_path": str(args.output),
            "output_sha256": replay.sha256_file(args.output),
            "sample_rate_hz": cfg.sr,
            "channels": 2,
            "speaker_mapping": "channel 0 candidate; channel 1 zero",
            "frames": len(frames),
            "inter_frame_gap_samples": args.inter_frame_gap_samples,
            "trailing_pad_samples": args.trailing_pad_samples,
            "float32_frames": len(burst),
            "peak_abs": peak,
            "frame_metadata": frame_metadata,
        },
    )
    print(json.dumps({"output": str(args.output), "sha256": replay.sha256_file(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
