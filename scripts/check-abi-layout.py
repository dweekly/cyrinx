"""Compile the C core for every Android ABI so its layout assertions are evaluated on each.

The versioned C structures pin their sizes and field offsets with _Static_assert. Those hold
trivially on the 64-bit hosts the tests run on. Pointer size and 64-bit alignment differ on
32-bit targets, so the same assertions are compiled here for arm64-v8a, armeabi-v7a, x86_64 and
x86 with the Android NDK. A build that succeeds means every assertion held for that ABI.

A negative control then compiles one deliberately wrong size assertion for each ABI and requires
the compiler to reject it, so a toolchain that ignored the assertions could not pass.

32-bit x86 is expected to be refused. There a 64-bit integer aligns to four bytes, so the batch
channel view, which holds a pointer followed by 64-bit counts, is 28 bytes and not 32, and the
build stops on that assertion (docs/NEGATIVE_FINDINGS.md entry 27). The check passes only while
x86 is refused for exactly that reason, so it has to be revisited if the layout is ever changed.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "Sources/CCyrinx"
ABIS = ["arm64-v8a", "armeabi-v7a", "x86_64", "x86"]
# ABI -> the only assertions allowed to fail there.
REFUSED = {"x86": {"channel view v1 size", "batch input v1 size"}}
WRONG = '#include "cyrinx/cyrinx_batch.h"\n_Static_assert(sizeof(cyrinx_batch_result_t) == 1, "negative control");\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--report", type=Path, default=ROOT / "artifacts/conformance/abi-layout.json")
    args = parser.parse_args()
    sdk = Path(os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
               or Path.home() / "Library/Android/sdk")
    ndk = Path(os.environ.get("ANDROID_NDK_HOME") or sdk / "ndk/27.2.12479018")
    cmake = sdk / "cmake" / os.environ.get("CYRINX_ANDROID_CMAKE", "4.1.2") / "bin"
    toolchain = ndk / "build/cmake/android.toolchain.cmake"
    report = {"status": "failed", "ndk": (ndk / "source.properties").read_text().strip(), "abis": {}}
    try:
        with tempfile.TemporaryDirectory(prefix="cyrinx-abi-layout-") as tmp:
            wrong = Path(tmp) / "wrong.c"
            wrong.write_text(WRONG)
            for abi in ABIS:
                build = Path(tmp) / abi
                common = [f"-DCMAKE_MAKE_PROGRAM={cmake / 'ninja'}", f"-DCMAKE_TOOLCHAIN_FILE={toolchain}",
                          f"-DANDROID_ABI={abi}", "-DANDROID_PLATFORM=android-26", "-DCMAKE_BUILD_TYPE=Release"]
                done = subprocess.run([cmake / "cmake", "-S", ROOT / "Tests/BatchReplay", "-B", build, "-G", "Ninja",
                                       *common], capture_output=True, text=True)
                if done.returncode == 0:
                    # Keep going past the first failure, so every assertion in every file is evaluated.
                    done = subprocess.run([cmake / "cmake", "--build", build, "--", "-k", "0"],
                                          capture_output=True, text=True)
                    objects = len(list(build.rglob("*.o")))
                failures = [line for line in (done.stdout + done.stderr).splitlines()
                            if "static assertion failed" in line or "error:" in line]
                named = {line.rsplit(": ", 1)[-1].strip() for line in failures if "static assertion failed" in line}
                entry = {"core_builds": done.returncode == 0, "failures": failures[:20],
                         "failed_assertions": sorted(named), "objects_compiled": objects}
                if abi in REFUSED:
                    # Refused, and only by the assertions that are known to differ.
                    entry["as_expected"] = (done.returncode != 0 and named == REFUSED[abi]
                                            and len(failures) == len(named))
                else:
                    entry["as_expected"] = done.returncode == 0
                # The same compiler and target, on an assertion that is false everywhere.
                target = {"arm64-v8a": "aarch64", "armeabi-v7a": "armv7a", "x86_64": "x86_64", "x86": "i686"}[abi]
                clang = next((ndk / "toolchains/llvm/prebuilt").iterdir()) / "bin/clang"
                control = subprocess.run(
                    [clang, f"--target={target}-linux-android{'eabi' if abi == 'armeabi-v7a' else ''}26",
                     "-std=c11", "-fsyntax-only", "-I", CORE / "include", wrong], capture_output=True, text=True)
                entry["negative_control_rejected"] = (control.returncode != 0
                                                      and "negative control" in control.stderr)
                report["abis"][abi] = entry
                outcome = "builds" if entry["core_builds"] else "refused by " + ", ".join(sorted(named))
                print(f"{abi:<12} {outcome}; {objects} files compiled; as expected: {entry['as_expected']}; "
                      f"negative control rejected: "
                      f"{entry['negative_control_rejected']}")
        good = all(e["as_expected"] and e["negative_control_rejected"] for e in report["abis"].values())
        report["status"] = "passed" if good else "failed"
    finally:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(f"{report['status']}: layout assertions on {len(report['abis'])} Android ABIs, report in {args.report}")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
