#!/usr/bin/env python3
"""Inventory tracked Rank 11a evidence without touching ignored bench artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any


SCHEMA = "cyrinx.rank11a.retained-corpus-audit.v1"
RECORDING_EXTENSIONS = {".wav", ".flac", ".caf", ".aiff", ".aif", ".pcm", ".raw"}
ARRAY_EXTENSIONS = {".npy", ".npz", ".mat", ".h5", ".hdf5"}


def _tracked_files(root: Path, revision: str) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-tree", "-r", "-z", "--name-only", revision],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return [root / item.decode() for item in result.stdout.split(b"\0") if item]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for nested in value.values():
            yield from _strings(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _strings(nested)


def _entry(root: Path, relative: str, finding: str, rejection_reasons: list[str]) -> dict:
    path = root / relative
    return {
        "path": relative,
        "present": path.is_file(),
        "sha256": _sha256(path) if path.is_file() else None,
        "size_bytes": path.stat().st_size if path.is_file() else None,
        "finding": finding,
        "rank11a_usable": False,
        "rejection_reasons": rejection_reasons,
    }


def build_audit(root: Path, revision: str = "61558c2") -> dict:
    tracked = _tracked_files(root, revision)
    relative = [path.relative_to(root) for path in tracked]
    recordings = sorted(str(path) for path in relative if path.suffix.lower() in RECORDING_EXTENSIONS)
    arrays = sorted(str(path) for path in relative if path.suffix.lower() in ARRAY_EXTENSIONS)
    summary_paths = sorted(
        path
        for path in relative
        if path.suffix.lower() in {".json", ".jsonl"}
        and (str(path).startswith("scratch/hw20k/data/") or str(path).startswith("scratch/hw20k/evidence/"))
    )

    artifact_references: set[str] = set()
    for relative_path in summary_paths:
        path = root / relative_path
        if path.suffix.lower() != ".json":
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        for text in _strings(value):
            if text.startswith("artifacts/"):
                artifact_references.add(text)

    evidence = [
        _entry(
            root,
            "scratch/hw20k/evidence/pixel7a-cyrinx2-2026-07-17/results-ledger.json",
            "The accepted Pixel campaign is one MacBook left-speaker column observed on two Pixel inputs.",
            [
                "physical_cell.playback explicitly says built-in left speaker only",
                "the ledger says raw manifests and captures are ignored local artifacts",
                "no complex per-bin H[k], noise covariance, or repeat matrices are retained",
            ],
        ),
        _entry(
            root,
            "scratch/hw20k/evidence/pixel7a-faceup-volume50-v1/qualification.json",
            (
                "The route qualification establishes two direct Pixel microphone rows but only "
                "speaker channel 0."
            ),
            [
                "scope.speaker_channels is [0]",
                "referenced captures are absent from this isolated worktree",
                "qualification statistics do not retain complex matrix columns",
            ],
        ),
        _entry(
            root,
            "scratch/hw20k/evidence/pixel7a-faceup-volume50-v1/route-signature.json",
            "The Android route declares bottom and back microphone channel mappings.",
            [
                "a receive route signature contains no transmit observation",
                "logical direct mappings do not by themselves prove sample independence",
                "no probe, common phase, or noise record is present",
            ],
        ),
        _entry(
            root,
            "scratch/hw20k/data/channel.json",
            "The summary has two Mac-to-Android SNR rows and one reverse row, not a complex matrix.",
            [
                "rows retain SNR and noise PSD only, not complex H[k]",
                "there is no second independently driven Mac speaker column",
                "there are no jointly phase-referenced repeats or receiver noise covariance",
            ],
        ),
        _entry(
            root,
            "scratch/hw20k/data/ios_phase_coherence.json",
            "This is a scalar phone-speaker phase-jitter summary at selected tones.",
            [
                "it is not a two-output/two-input acquisition",
                "it does not retain per-bin complex path matrices or receiver covariance",
            ],
        ),
        _entry(
            root,
            "scratch/hw20k/data/freqresp/mac2pixel_dist_5cm_diag.json",
            "This retains one scalar swept path's magnitude and phase.",
            [
                "speaker and microphone port identities are not a complete 2x2 map",
                "no common-reference second transmit column or repeated matrices exist",
                "room-tone covariance is absent",
            ],
        ),
        _entry(
            root,
            "scratch/hw20k/tone_check_hardware.py",
            "The historical runner specifies separate left and right acquisitions.",
            [
                "separate captures do not establish channel-column phase",
                "the runner explicitly states that it cannot prove diversity or MIMO",
                "source code is an acquisition plan, not retained OTA evidence",
            ],
        ),
        _entry(
            root,
            "docs/NEGATIVE_FINDINGS.md",
            "A historical summary says blind L+R playback raised EVM and right arrived weaker.",
            [
                "blind duplicate playback is not an orthogonal matrix probe",
                "the underlying raw left/right captures and repeat provenance are not tracked",
                "the summary cannot recover a full phase-coherent H[k]",
            ],
        ),
    ]

    unavailable = sorted(
        {
            reference
            for reference in artifact_references
            if not (root / reference).exists()
        }
    )
    return {
        "schema": SCHEMA,
        "source_revision": revision,
        "scope": "git-tracked files in the isolated worktree only",
        "tracked_file_count": len(tracked),
        "scanned_hw20k_summary_count": len(summary_paths),
        "scanned_hw20k_summaries": [str(path) for path in summary_paths],
        "tracked_recording_files": recordings,
        "tracked_numeric_array_files": arrays,
        "referenced_but_unavailable_artifact_count": len(unavailable),
        "referenced_but_unavailable_artifacts": unavailable,
        "rank11a_evidence_audit": evidence,
        "full_phase_coherent_2x2_matrix_count": 0,
        "decision": "STOP_NOT_IDENTIFIABLE",
        "formal_stop_reasons": [
            "No tracked raw audio or numeric capture array exists.",
            (
                "The qualified Pixel corpus has two microphone rows but only one independently "
                "driven speaker column."
            ),
            (
                "The left/right tone-check design uses separate captures and therefore lacks "
                "common column phase."
            ),
            (
                "Scalar response summaries omit a jointly estimated 2x2 H[k], repeat matrices, "
                "and receiver noise covariance."
            ),
            (
                "The required five matrices in each of near-field, centered 3-ft, and "
                "lateral-offset 3-ft are absent."
            ),
        ],
        "claim_boundary": (
            "This audit rejects a retained-replay MIMO feasibility claim. It does not establish "
            "that the physical devices lack usable spatial rank; a new phase-continuous stereo "
            "acquisition is required."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--source-revision", default="61558c2")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = build_audit(args.root.resolve(), args.source_revision)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
