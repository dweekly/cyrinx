"""Run the C batch decoder as a 32-bit x86 program and compare it with this host, byte for byte.

scripts/check-abi-layout.py proves the versioned structures have the same layout on 32-bit x86.
This goes one step further and runs the decoder there. The checkout-only replay fixtures are
decoded by the direct C probe built for this host and by the same probe built inside a 32-bit
x86 Linux container on a remote machine, with the floating-point settings of the Android x86
ABI. The 32-bit record is held to the same expectations as every other lane of the replay: the
status, identity, decisions, counts, ordered block mask and valid bytes exactly, and the metrics
within each fixture's tolerance. Its decisions and every payload byte must also equal this
host's.

It needs ssh access to a Linux machine with Docker. No 32-bit x86 Android device or emulator
is involved, so this covers the C core on that architecture, not the Android runtime.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "Sources/CCyrinx"
ADAPTERS = ROOT / "Tests/BatchReplay"
SOURCES = ["cyrinx_batch.c", "cyrinx_profiles.c", "cyrinx_sha256.c", "cyrinx_bulk.c", "cyrinx_fft.c",
           "kissfft/kiss_fft.c", "kissfft/kiss_fftr.c"]
IMAGE = "i386/debian:bookworm-slim"
# Inside the container: confirm it is 32-bit, build with the Android x86 ABI's SSE arithmetic, decode.
REMOTE = r"""set -eu
[ "$(getconf LONG_BIT)" = 32 ] || { echo "container is not 32-bit" >&2; exit 1; }
apt-get update -qq >/dev/null && apt-get install -y -qq gcc libc6-dev >/dev/null
cd /work
gcc -std=c11 -O2 -m32 -march=i686 -msse3 -mfpmath=sse -Dkiss_fft_scalar=double \
    -I core/include -I core/kissfft -I adapters adapters/direct.c SOURCES -lm -o direct
mkdir -p out
while read -r index profile channels; do
    ./direct "$profile" "inputs/$index.f32" "$channels" > "out/$index.bin"
done < cases.txt
"""


# A remote machine that goes away mid-run must fail the check, not hang it: ssh gives up after a
# minute without an answer, and no single step may take longer than TIMEOUT seconds.
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=15",
       "-o", "ServerAliveCountMax=4"]
TIMEOUT = 600


def run(args, **kwargs):
    return subprocess.run([str(arg) for arg in args], check=True, timeout=TIMEOUT, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", required=True, help="ssh name of a Linux machine with Docker")
    parser.add_argument("--report", type=Path, default=ROOT / "artifacts/conformance/x86-32.json")
    parser.add_argument("--manifest", type=Path, help="replay list to run; default the checked-in one")
    args = parser.parse_args()
    # Written first and filled inside the try, so any failure replaces an earlier passing report.
    report = {"status": "failed", "host": args.host, "image": IMAGE, "results": []}
    remote = None
    try:
        spec = importlib.util.spec_from_file_location("batch_replay", ROOT / "scripts/check-batch-replay.py")
        replay = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(replay)
        manifest = json.loads((args.manifest or replay.MANIFEST).read_text())
        # The replay's own check of the whole list, before any entry is set aside: a damaged
        # entry fails the run and only a well-formed recording case is skipped for needing the
        # local archive.
        replay.validate_manifest(manifest)
        cases = [case for case in manifest["cases"] if "path" in case]
        report["skipped_recording_cases"] = [case["name"] for case in manifest["cases"] if "archive" in case]
        if not cases:
            raise ValueError("no checkout-only fixtures to run")
        with tempfile.TemporaryDirectory(prefix="cyrinx-x86-32-") as tmp:
            tmp = Path(tmp)
            (tmp / "inputs").mkdir()
            lines, inputs = [], []
            for index, case in enumerate(cases):
                inputs.append(replay.fixture_bytes(case, None))
                (tmp / "inputs" / f"{index}.f32").write_bytes(inputs[-1])
                lines.append(f"{index} {replay.probe_profile(case)} {2 if 'secondary_path' in case else 1}")
            (tmp / "cases.txt").write_text("\n".join(lines) + "\n")
            (tmp / "run.sh").write_text(REMOTE.replace("SOURCES", " ".join("core/" + name for name in SOURCES)))
            local = tmp / "direct"
            run(["clang", "-std=c11", "-O2", "-Dkiss_fft_scalar=double", "-I", CORE / "include", "-I",
                 CORE / "kissfft", "-I", ADAPTERS, ADAPTERS / "direct.c", *[CORE / name for name in SOURCES],
                 "-lm", "-o", local])
            bundle = tmp / "bundle.tar"
            with tarfile.open(bundle, "w") as archive:
                archive.add(CORE, arcname="core", filter=lambda item: None if "/.build" in item.name else item)
                archive.add(ADAPTERS, arcname="adapters")
                for name in ("inputs", "cases.txt", "run.sh"):
                    archive.add(tmp / name, arcname=name)
            remote = run([*SSH, args.host, "mktemp", "-d", "/tmp/cyrinx-x86-32.XXXXXX"], capture_output=True,
                         text=True).stdout.strip()
            if not remote.startswith("/tmp/cyrinx-x86-32."):
                raise ValueError(f"unexpected remote directory {remote!r}")
            run(["scp", "-q", *SSH[1:], bundle, f"{args.host}:{remote}/bundle.tar"])
            run([*SSH, args.host, f"cd {remote} && tar xf bundle.tar && docker run --rm --platform linux/386 "
                 f"-v {remote}:/work {IMAGE} sh /work/run.sh"])
            for index, case in enumerate(cases):
                channels = 2 if "secondary_path" in case else 1
                here = run([local, replay.probe_profile(case), tmp / "inputs" / f"{index}.f32", channels],
                           capture_output=True).stdout
                there = run([*SSH, args.host, "cat", f"{remote}/out/{index}.bin"], capture_output=True).stdout
                try:
                    # The runner's own parsing and comparison: a record of the wrong length, a
                    # different decision or count, or a metric outside its tolerance all fail here.
                    record = replay.parse(there)
                    outcome = replay.compare(case, replay.expectation(case, manifest, inputs[index]), record)
                    same = replay.same_decision(record, replay.parse(here))
                except replay.Mismatch as error:
                    # Keep what was compared before it failed, as the replay's own report does.
                    if error.outcome:
                        report["results"].append({**error.outcome, "fixture": case["name"],
                                                  "status": record["status"], "bytes": len(there),
                                                  "passed": False})
                    raise ValueError(f"{case['name']}: {error}") from error
                except ValueError as error:
                    raise ValueError(f"{case['name']}: {error}") from error
                report["results"].append({**outcome, "fixture": case["name"], "status": record["status"],
                                          "bytes": len(there), "identical_decisions_to_host": same})
                print(f"{case['name']:<22} status {record['status']:>3}  mask {outcome.get('mask', '-'):<6} "
                      f"same decisions as this host: {same}")
        good = all(row.get("identical_decisions_to_host") for row in report["results"])
        report["status"] = "passed" if good and len(report["results"]) == len(cases) else "failed"
    except Exception as error:
        report["error"] = str(error)
        raise
    finally:
        if remote and remote.startswith("/tmp/cyrinx-x86-32."):
            # The container wrote its outputs as root, so it removes them; then the directory goes.
            try:
                subprocess.run([*SSH, args.host, f"docker run --rm -v {remote}:/work {IMAGE} sh -c "
                                f"'rm -rf /work/* /work/.[!.]*' ; rmdir {remote}"], capture_output=True, timeout=120,
                               check=True)
            except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
                # A warning, so a failed tidy-up never hides the replay's own result.
                print(f"could not remove {remote} on {args.host}", file=sys.stderr)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
        print(f"{report['status']}: {len(report['results'])} fixtures decoded as 32-bit x86, report in {args.report}")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
