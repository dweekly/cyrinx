"""Prove that the replay run fails, and says where, when one field of one record changes."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile

LABEL = "KISS/Swift/periodic-dropout"
# One of each kind the conformance gate names: a payload byte, a validity position, an exact
# count, a required capacity, an identity byte, and a floating metric moved beyond its tolerance.
FIELDS = ["payload", "mask", "blocks_ok", "payload_required", "identity", "evm_rms"]


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
                    kept = [row for row in result["results"] if row["binding"] == "Swift"]
                    if field == "evm_rms" and not (kept and kept[-1]["metrics"][field]["passed"] is False):
                        raise RuntimeError("the failed metric comparison was not kept in the report")
                    print(f"Confirmed that a changed {field} fails and is named: {error[:90]}")
                    continue
                raise RuntimeError(f"a changed {field} failed without naming the record and field: {error}")
            raise RuntimeError(f"the replay check passed with a changed {field}")
        # A control aimed at a record the run never produces must fail too, not pass untested.
        sys.argv = [str(runner), "--digital-only", "--report", str(report), "--mutate",
                    "KISS/C/pixel-desk-frame-1:identity"]
        try:
            replay.main()
        except ValueError as error:
            if "no record matched" not in str(error):
                raise
        else:
            raise RuntimeError("an unmatched negative control was reported as a pass")
    print(f"All {len(FIELDS)} negative controls failed for the intended reason, and an unmatched one is refused.")


if __name__ == "__main__":
    main()
