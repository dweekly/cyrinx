"""Prove that reversing only the JVM mask fails a real four-block decode."""
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile


def main():
    root = Path(__file__).resolve().parents[2]
    runner = root / "scripts/check-batch-replay.py"
    spec = importlib.util.spec_from_file_location("batch_replay", runner)
    replay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(replay)
    with tempfile.TemporaryDirectory(prefix="cyrinx-mask-order-") as temp:
        adapters = Path(temp) / "adapters"
        shutil.copytree(replay.ADAPTERS, adapters)
        kotlin = adapters / "Replay.kt"
        source = kotlin.read_text()
        original = "System.out.write(result.mask)"
        if source.count(original) != 1:
            raise RuntimeError("could not identify the Kotlin mask output for mutation")
        kotlin.write_text(source.replace(original, "System.out.write(result.mask.reversedArray())"))
        replay.ADAPTERS = adapters
        report = Path(temp) / "result.json"
        sys.argv = [str(runner), "--digital-only", "--report", str(report)]
        try:
            replay.main()
        except ValueError:
            result = json.loads(report.read_text())
            if (result.get("checking") == "KISS/Kotlin/JNI/periodic-dropout"
                    and "expected mask 1101, observed 1011" in result.get("error", "")):
                print("Confirmed that a Kotlin mask reordering fails the dropout fixture.")
                return
            raise
        raise RuntimeError("the Kotlin adapter reversed a partial mask and the replay check passed")


if __name__ == "__main__":
    main()
