"""Compile the C core for every Android ABI so its layout assertions are evaluated on each.

The versioned C structures pin their sizes and field offsets with _Static_assert. Those hold
trivially on the 64-bit hosts the tests run on. Pointer size and 64-bit alignment differ on
32-bit targets, so the same assertions are compiled here for arm64-v8a, armeabi-v7a, x86_64 and
x86 with the Android NDK. A build that succeeds means every assertion held for that ABI.

A negative control then compiles one deliberately wrong size assertion for each ABI and requires
the compiler to reject it, so a toolchain that ignored the assertions could not pass.

All four must build. 32-bit x86 aligns a 64-bit integer to four bytes, which once made the batch
channel view 28 bytes there and not 32 (docs/NEGATIVE_FINDINGS.md entry 27). The view's first
64-bit member is now eight-byte aligned on every ABI, and this check is what keeps it so.

REFUSED can name an ABI that is expected to fail, with the assertions allowed to fail there and
the size the compiler must report for each. It is empty: no ABI is expected to fail.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "Sources/CCyrinx"
TARGETS = {"arm64-v8a": "aarch64-linux-android26", "armeabi-v7a": "armv7a-linux-androideabi26",
           "x86_64": "x86_64-linux-android26", "x86": "i686-linux-android26"}
# ABI -> the only assertions allowed to fail there, with the size the compiler must report for each.
REFUSED = {}
HEADER = '#include "cyrinx/cyrinx_batch.h"\n'
WRONG = HEADER + '_Static_assert(sizeof(cyrinx_batch_result_t) == 1, "negative control");\n'


def compiles(clang, target, source):
    """Whether `source` passes a syntax-only compile for `target`, with the compiler's messages."""
    done = subprocess.run([clang, f"--target={target}", "-std=c11", "-fsyntax-only", "-I", CORE / "include",
                           source], capture_output=True, text=True)
    return done.returncode == 0, done.stderr


def check(abi, target, tmp, cmake, toolchain, clang, wrong):
    build = tmp / abi
    entry = {"core_builds": False, "objects_compiled": 0, "failed_assertions": [], "failures": []}
    done = subprocess.run([cmake / "cmake", "-S", ROOT / "Tests/BatchReplay", "-B", build, "-G", "Ninja",
                           f"-DCMAKE_MAKE_PROGRAM={cmake / 'ninja'}", f"-DCMAKE_TOOLCHAIN_FILE={toolchain}",
                           f"-DANDROID_ABI={abi}", "-DANDROID_PLATFORM=android-26", "-DCMAKE_BUILD_TYPE=Release"],
                          capture_output=True, text=True)
    configured = done.returncode == 0
    if not configured:
        # Why configuration failed is in the lines after CMake's first error; keep the tail, since
        # the build directory is temporary.
        entry["configure_output"] = (done.stdout + done.stderr).splitlines()[-15:]
    if configured:
        # Keep going past the first failure, so every assertion in every file is evaluated.
        done = subprocess.run([cmake / "cmake", "--build", build, "--", "-k", "0"], capture_output=True, text=True)
        entry["objects_compiled"] = len(list((build / "CMakeFiles/replay.dir").rglob("*.o")))
    output = (done.stdout + done.stderr).splitlines()
    # Compiler and CMake diagnostics. Any that is not one of the expected assertions fails the ABI.
    failures = [line.strip() for line in output if " error: " in line or line.startswith("CMake Error")]
    named = {line.rsplit(": ", 1)[-1] for line in failures if "static assertion failed" in line}
    entry.update(configured=configured, core_builds=configured and done.returncode == 0,
                 failures=failures[:20], failed_assertions=sorted(named))
    if abi in REFUSED:
        # The sizes come from the build that was refused, not from a separate compile of the
        # header: after each failed assertion the compiler notes the value it evaluated.
        measured, pending = {}, None
        for line in output:
            if "static assertion failed" in line:
                pending = line.rsplit(": ", 1)[-1].strip()
            found = re.search(r"expression evaluates to '(\d+) == \d+'", line)
            if found and pending:
                measured[pending], pending = int(found.group(1)), None
        entry["measured_sizes"] = measured
        entry["documented_sizes_hold"] = measured == REFUSED[abi]
        # Refused, by the known assertions only, and with the structures at their documented sizes.
        entry["as_expected"] = (configured and done.returncode != 0 and named == set(REFUSED[abi])
                                and len(failures) == len(named) and entry["documented_sizes_hold"])
    else:
        entry["as_expected"] = entry["core_builds"]
    rejected, detail = compiles(clang, target, wrong)
    entry["negative_control_rejected"] = not rejected and "negative control" in detail
    return entry


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--report", type=Path, default=ROOT / "artifacts/conformance/abi-layout.json")
    args = parser.parse_args()
    # Written first and filled inside the try, so any failure replaces an earlier passing report.
    report = {"status": "failed", "abis": {}}
    try:
        sdk = Path(os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
                   or Path.home() / "Library/Android/sdk")
        ndk = Path(os.environ.get("ANDROID_NDK_HOME") or sdk / "ndk/27.2.12479018")
        cmake = sdk / "cmake" / os.environ.get("CYRINX_ANDROID_CMAKE", "4.1.2") / "bin"
        report["ndk"] = (ndk / "source.properties").read_text().strip()
        clang = next((ndk / "toolchains/llvm/prebuilt").iterdir()) / "bin/clang"
        with tempfile.TemporaryDirectory(prefix="cyrinx-abi-layout-") as tmp:
            wrong = Path(tmp) / "wrong.c"
            wrong.write_text(WRONG)
            for abi, target in TARGETS.items():
                entry = check(abi, target, Path(tmp), cmake, ndk / "build/cmake/android.toolchain.cmake", clang,
                              wrong)
                report["abis"][abi] = entry
                if entry["core_builds"]:
                    outcome = "builds"
                elif not entry["configured"]:
                    outcome = "did not configure: " + " | ".join(entry["configure_output"][-3:])
                else:
                    outcome = "refused by " + (", ".join(entry["failed_assertions"]) or "another error")
                print(f"{abi:<12} {outcome}; {entry['objects_compiled']} files compiled; as expected: "
                      f"{entry['as_expected']}; negative control rejected: {entry['negative_control_rejected']}")
        good = len(report["abis"]) == len(TARGETS) and all(
            entry["as_expected"] and entry["negative_control_rejected"] for entry in report["abis"].values())
        report["status"] = "passed" if good else "failed"
    except Exception as error:
        report["error"] = str(error)
        raise
    finally:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
        print(f"{report['status']}: layout assertions on {len(report['abis'])} of {len(TARGETS)} Android ABIs, "
              f"report in {args.report}")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
