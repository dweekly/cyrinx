"""Prove that the replay run fails, and says where, when one field of one record changes."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile

LABEL = "KISS/Swift/periodic-dropout"
# One of each kind the conformance gate names: a payload byte, a validity position, an exact
# count, an identity byte, and a floating metric moved beyond its tolerance.
FIELDS = ["payload", "mask", "blocks_ok", "identity", "evm_rms"]


def main():
    root = Path(__file__).resolve().parents[2]
    runner = root / "scripts/check-batch-replay.py"
    spec = importlib.util.spec_from_file_location("batch_replay", runner)
    replay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(replay)
    with tempfile.TemporaryDirectory(prefix="cyrinx-negative-controls-") as temp:
        report = Path(temp) / "result.json"
        for field in FIELDS:
            sys.argv = [str(runner), "--digital-only", "--report", str(report), "--mutate", f"{LABEL}:{field}"]
            try:
                replay.main()
            except ValueError:
                result = json.loads(report.read_text())
                error = result.get("error", "")
                if result["status"] == "failed" and error.startswith(f"{LABEL}: {field}:"):
                    print(f"Confirmed that a changed {field} fails and is named: {error[:90]}")
                    continue
                raise RuntimeError(f"a changed {field} failed without naming the record and field: {error}")
            raise RuntimeError(f"the replay check passed with a changed {field}")
    print(f"All {len(FIELDS)} negative controls failed for the intended reason.")


if __name__ == "__main__":
    main()
