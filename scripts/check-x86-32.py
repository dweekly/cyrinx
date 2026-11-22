"""Run the C batch decoder as a 32-bit x86 program and compare it with this host, byte for byte.

scripts/check-abi-layout.py proves the versioned structures have the same layout on 32-bit x86.
This goes one step further and runs the decoder there. The checkout-only replay fixtures are
decoded by the direct C probe built for this host and by the same probe built inside a 32-bit
x86 Linux container on a remote machine, with the floating-point settings of the Android x86
ABI. Each pair of results must be identical: profile identity, receiver decision, ordered block
mask and every payload byte.

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


def run(args, **kwargs):
    return subprocess.run([str(arg) for arg in args], check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", required=True, help="ssh name of a Linux machine with Docker")
    parser.add_argument("--report", type=Path, default=ROOT / "artifacts/conformance/x86-32.json")
    args = parser.parse_args()
    # Written first and filled inside the try, so any failure replaces an earlier passing report.
    report = {"status": "failed", "host": args.host, "image": IMAGE, "results": []}
    remote = None
    try:
        spec = importlib.util.spec_from_file_location("batch_replay", ROOT / "scripts/check-batch-replay.py")
        replay = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(replay)
        manifest = json.loads(replay.MANIFEST.read_text())
        cases = [case for case in manifest["cases"] if "path" in case]
        if not cases:
            raise ValueError("no checkout-only fixtures to run")
        with tempfile.TemporaryDirectory(prefix="cyrinx-x86-32-") as tmp:
            tmp = Path(tmp)
            (tmp / "inputs").mkdir()
            lines = []
            for index, case in enumerate(cases):
                (tmp / "inputs" / f"{index}.f32").write_bytes(replay.fixture_bytes(case, None))
                lines.append(f"{index} {case['profile']} {2 if 'secondary_path' in case else 1}")
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
            remote = run(["ssh", args.host, "mktemp", "-d", "/tmp/cyrinx-x86-32.XXXXXX"], capture_output=True,
                         text=True).stdout.strip()
            if not remote.startswith("/tmp/cyrinx-x86-32."):
                raise ValueError(f"unexpected remote directory {remote!r}")
            run(["scp", "-q", bundle, f"{args.host}:{remote}/bundle.tar"])
            run(["ssh", args.host, f"cd {remote} && tar xf bundle.tar && docker run --rm --platform linux/386 "
                 f"-v {remote}:/work {IMAGE} sh /work/run.sh"])
            for index, case in enumerate(cases):
                channels = 2 if "secondary_path" in case else 1
                here = run([local, case["profile"], tmp / "inputs" / f"{index}.f32", channels],
                           capture_output=True).stdout
                there = run(["ssh", args.host, "cat", f"{remote}/out/{index}.bin"], capture_output=True).stdout
                blocks = len(case["mask"])
                mask = "".join(str(v) for v in there[40:40 + blocks])
                same = bool(here) and here == there
                report["results"].append(dict(fixture=case["name"], bytes=len(there), mask=mask,
                                              expected_mask=case["mask"], identical_to_host=same))
                print(f"{case['name']:<22} {len(there):>6} bytes  mask {mask:<6} identical to this host: {same}")
        good = all(row["identical_to_host"] and row["mask"] == row["expected_mask"] for row in report["results"])
        report["status"] = "passed" if good and len(report["results"]) == len(cases) else "failed"
    except Exception as error:
        report["error"] = str(error)
        raise
    finally:
        if remote and remote.startswith("/tmp/cyrinx-x86-32."):
            # The container wrote its outputs as root, so it removes them; then the directory goes.
            subprocess.run(["ssh", args.host, f"docker run --rm -v {remote}:/work {IMAGE} sh -c "
                            f"'rm -rf /work/* /work/.[!.]*' ; rmdir {remote}"], capture_output=True)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
        print(f"{report['status']}: {len(report['results'])} fixtures decoded as 32-bit x86, report in {args.report}")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
