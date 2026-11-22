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
        "unknown field": changed("digital-qpsk", gain=2),
        "missing digest": changed("pixel-desk-frame-1", source_sha256=None),
        "profile out of range": changed("digital-qpsk", profile=profiles),
        "profile as text": changed("digital-qpsk", profile="0"),
        "mask with other characters": changed("digital-qpsk", mask="2"),
        "dropout wider than its period": changed("periodic-dropout", dropout={"period": 4, "width": 8}),
    }
    for label, case in rejected.items():
        try:
            replay.validate_case(case, profiles)
        except ValueError:
            continue
        raise RuntimeError(f"the replay runner accepted a case with {label}")
    print(f"Confirmed that {len(cases)} bundled cases validate and {len(rejected)} malformed cases are refused.")


if __name__ == "__main__":
    main()
