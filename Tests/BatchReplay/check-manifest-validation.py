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
    # The whole list is checked by one function that every runner calls, including the 32-bit
    # x86 one, which sets recording cases aside only after the list has passed.
    replay.validate_manifest(manifest)
    frames = manifest["frame_samples"]
    damaged = {
        "a frame length that is not a whole number": dict(manifest, frame_samples=[float(frames[0]), *frames[1:]]),
        "a missing frame length": dict(manifest, frame_samples=[*frames[:-1], None]),
        "too few frame lengths": dict(manifest, frame_samples=frames[:-1]),
        "two cases with one name": dict(manifest, cases=[*manifest["cases"], manifest["cases"][0]]),
        "an unknown version": dict(manifest, version=2),
    }
    for label, value in damaged.items():
        try:
            replay.validate_manifest(value)
        except ValueError:
            continue
        raise RuntimeError(f"the replay runner accepted a list with {label}")
    x86 = (root / "scripts/check-x86-32.py").read_text()
    if "replay.validate_manifest(manifest)" not in x86 or "validate_case" in x86:
        raise RuntimeError("the 32-bit x86 runner does not use the replay's own check of the list")
    # A payload of the wrong length is refused and named as the payload, with a matching mask.
    case = cases["digital-qpsk"]
    expected = replay.expectation(case, manifest, b"")
    record = dict(expected, mask=bytes(int(bit) for bit in case["mask"]))
    for length in (256 * len(case["mask"]) - 1, 256 * len(case["mask"]) + 1):
        try:
            replay.compare(case, expected, dict(record, payload=bytes(length)))
        except replay.Mismatch as error:
            if str(error) == f"payload: expected {256 * len(case['mask'])} bytes, observed {length}":
                continue
            raise RuntimeError(f"a {length}-byte payload was reported as: {error}") from error
        raise RuntimeError(f"the replay runner accepted a {length}-byte payload")
    # The research comparison refuses a result whose length is not its mask's. The case is made
    # here so that only the length can differ: one valid block of zeros, then one invalid block.
    # The hash covers the valid block alone, which is the same bytes at every length tried.
    control = {"research": {"mask": "10", "valid_payload_sha256": replay.sha(bytes(256))}}
    if replay.research_outcome(control, bytes([1, 0]), bytes(512))[1]:
        raise RuntimeError("the research comparison refused a result of the right length")
    for length in (511, 513):
        if not replay.research_outcome(control, bytes([1, 0]), bytes(length))[1]:
            raise RuntimeError(f"the research comparison accepted a {length}-byte payload for two blocks")
    print(f"Confirmed that {len(cases)} bundled cases validate, {len(rejected)} malformed cases are refused, "
          f"{len(unreachable)} faults that cannot reach the decoder are refused, {len(damaged)} damaged lists "
          "are refused, and a payload of the wrong length is refused in both comparisons.")


if __name__ == "__main__":
    main()
