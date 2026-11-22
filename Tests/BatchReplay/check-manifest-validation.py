"""Prove that the replay runner refuses input lists it would not apply in full."""
import copy
import importlib.util
import json
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("batch_replay", root / "scripts/check-batch-replay.py")
    replay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(replay)
    manifest = json.loads(replay.MANIFEST.read_text())
    profiles = len(manifest["profile_identities"])
    cases = {case["name"]: case for case in manifest["cases"]}
    for case in cases.values():
        replay.validate_case(case, profiles)

    def changed(name, **fields):
        case = copy.deepcopy(cases[name])
        for key, value in fields.items():
            if value is None:
                del case[key]
            else:
                case[key] = value
        return case

    rejected = {
        "a file and a recording": changed("digital-qpsk", archive="not-a-recording.npz"),
        "no source": changed("digital-qpsk", path=None),
        "dropout on a recording": changed("pixel-desk-frame-2", dropout=cases["periodic-dropout"]["dropout"]),
        "second channel on a recording": changed("pixel-desk-frame-2", secondary_path="x.f32"),
        "research on the desk profile": changed(
            "pixel-desk-frame-2", research=cases["pixel-behind-monitor-control"]["research"]),
        "research on a checkout file": changed(
            "digital-qpsk", research=cases["pixel-behind-monitor-control"]["research"]),
        "unknown field": changed("digital-qpsk", loudness=2),
        "gain on two channels": changed("two-mic-recovery", gain=2),
        "truncation on a recording": changed("pixel-desk-frame-2", truncate=100),
        "rejection that also names a mask": changed("digital-qpsk", status=-1),
        "decode failure as a status-only case": changed("truncated-capture", status=-10),
        "decode failure that names a mask": changed("late-chirp", mask="1"),
        "decode failure with no block count": changed("late-chirp", blocks=None),
        "decode failure with no metrics": changed("late-chirp", metrics=None),
        "status that is neither a refusal nor a failed decode": changed("truncated-capture", status=-3),
        "decode with no metrics": changed("digital-qpsk", metrics=None),
        "metric with no tolerance": changed("digital-qpsk", metrics={
            name: {"value": 0} for name in replay.METRICS}),
        "clipping that is not a count": changed("digital-qpsk", clipping=-1),
        "missing digest": changed("pixel-desk-frame-1", source_sha256=None),
        "profile out of range": changed("digital-qpsk", profile=profiles),
        "profile as text": changed("digital-qpsk", profile="0"),
        "mask with other characters": changed("digital-qpsk", mask="2"),
        "dropout wider than its period": changed("periodic-dropout", dropout={"period": 4, "width": 8}),
        "dropout on two channels": changed("two-mic-recovery", dropout={"period": 2, "width": 1}),
    }
    for label, case in rejected.items():
        try:
            replay.validate_case(case, profiles)
        except ValueError:
            continue
        raise RuntimeError(f"the replay runner accepted a case with {label}")
    # Faults that would not reach the decoder are refused when the input is built.
    source = cases["digital-qpsk"]
    unreachable = {
        "truncation past the end": changed("digital-qpsk", truncate=10**9),
        "truncation to nothing": changed("digital-qpsk", truncate=0),
        "injected sample past the end": changed("digital-qpsk", nonfinite_at=10**9),
        "injected sample that truncation removes": changed("digital-qpsk", nonfinite_at=10, truncate=10),
    }
    for label, case in unreachable.items():
        replay.validate_case(case, profiles)
        try:
            replay.fixture_bytes(case, None)
        except ValueError as error:
            if source["name"] in str(error) and "digest" not in str(error):
                continue
        raise RuntimeError(f"the replay runner built an input with {label}")
    print(f"Confirmed that {len(cases)} bundled cases validate, {len(rejected)} malformed cases are refused, "
          f"and {len(unreachable)} faults that cannot reach the decoder are refused.")


if __name__ == "__main__":
    main()
