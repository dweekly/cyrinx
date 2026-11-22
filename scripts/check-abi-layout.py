"""Compile the C core for every Android ABI so its layout assertions are evaluated on each.

The versioned C structures pin their sizes and field offsets with _Static_assert. Those hold
trivially on the 64-bit hosts the tests run on. Pointer size and 64-bit alignment differ on
32-bit targets, so the same assertions are compiled here for arm64-v8a, armeabi-v7a, x86_64 and
x86 with the Android NDK. A build that succeeds means every assertion held for that ABI.

A negative control then compiles one deliberately wrong size assertion for each ABI and requires
the compiler to reject it, so a toolchain that ignored the assertions could not pass.

32-bit x86 is expected to be refused. There a 64-bit integer aligns to four bytes, so the batch
channel view, which holds a pointer followed by 64-bit counts, is 28 bytes and not 32, and the
batch input, which holds two views, is 72 and not 80 (docs/NEGATIVE_FINDINGS.md entry 27). The
check passes only while x86 is refused by exactly those two assertions and the two structures
measure exactly 28 and 72 bytes there, so any other change to the layout fails it.
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
TARGETS = {"arm64-v8a": "aarch64-linux-android26", "armeabi-v7a": "armv7a-linux-androideabi26",
           "x86_64": "x86_64-linux-android26", "x86": "i686-linux-android26"}
# ABI -> the only assertions allowed to fail there, and the sizes the structures must have instead.
REFUSED = {"x86": {"assertions": {"channel view v1 size", "batch input v1 size"},
                   "sizes": {"cyrinx_batch_channel_view_t": 28, "cyrinx_batch_input_t": 72}}}
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
    if configured:
        # Keep going past the first failure, so every assertion in every file is evaluated.
        done = subprocess.run([cmake / "cmake", "--build", build, "--", "-k", "0"], capture_output=True, text=True)
        entry["objects_compiled"] = len(list(build.rglob("*.o")))
    output = (done.stdout + done.stderr).splitlines()
    # Compiler and CMake diagnostics. Any that is not one of the expected assertions fails the ABI.
    failures = [line.strip() for line in output if " error: " in line or line.startswith("CMake Error")]
    named = {line.rsplit(": ", 1)[-1] for line in failures if "static assertion failed" in line}
    entry.update(configured=configured, core_builds=configured and done.returncode == 0,
                 failures=failures[:20], failed_assertions=sorted(named))
    if abi in REFUSED:
        # Refused, by the known assertions only, and with the structures at their documented sizes.
        probe = tmp / f"{abi}-sizes.c"
        probe.write_text(HEADER + "".join(
            f'_Static_assert(sizeof({name}) == {size}, "{name} is {size} bytes");\n'
            for name, size in REFUSED[abi]["sizes"].items()))
        entry["documented_sizes_hold"], detail = compiles(clang, target, probe)
        if not entry["documented_sizes_hold"]:
            entry["failures"] += [line for line in detail.splitlines() if "static assertion failed" in line]
        entry["as_expected"] = (configured and done.returncode != 0 and named == REFUSED[abi]["assertions"]
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
                    outcome = "did not configure"
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
