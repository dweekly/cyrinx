"""Build the Kotlin replay probe for ART and the JNI decoder for arm64 Android."""
import os
from pathlib import Path
import secrets
import subprocess
import sys


def output(args):
    return subprocess.check_output([str(arg) for arg in args])


def build(work, adapters, jar):
    sdk = Path(os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
               or Path.home() / "Library/Android/sdk")
    ndk = Path(os.environ.get("ANDROID_NDK_HOME") or sdk / "ndk/27.2.12479018")
    cmake = sdk / "cmake" / os.environ.get("CYRINX_ANDROID_CMAKE", "4.1.2") / "bin"
    platform = sdk / "platforms/android-35/android.jar"
    d8 = sdk / "build-tools" / os.environ.get("CYRINX_ANDROID_BUILD_TOOLS", "37.0.0") / "d8"
    dex = work / "probe.zip"
    # D8 can repeat one Kotlin metadata warning for every class in the stdlib.
    compilation = subprocess.run([d8, "--min-api", "26", "--lib", platform, "--output", dex, jar],
                                 capture_output=True, text=True)
    if compilation.stderr:
        print("\n".join(dict.fromkeys(compilation.stderr.splitlines())), file=sys.stderr)
    compilation.check_returncode()
    native = work / "android"
    output([cmake / "cmake", "-S", adapters, "-B", native, "-G", "Ninja",
            f"-DCMAKE_MAKE_PROGRAM={cmake / 'ninja'}",
            f"-DCMAKE_TOOLCHAIN_FILE={ndk / 'build/cmake/android.toolchain.cmake'}",
            "-DANDROID_ABI=arm64-v8a", "-DANDROID_PLATFORM=android-26",
            "-DANDROID_SUPPORT_FLEXIBLE_PAGE_SIZES=ON", "-DCMAKE_BUILD_TYPE=Release"])
    output([cmake / "cmake", "--build", native])
    return dex, native / "libreplay.so", (ndk / "source.properties").read_text().strip()


def replay(serial, dex, library, cases, windows, expected, report, judge):
    adb = ["adb", "-s", serial]
    remote = "/data/local/tmp/cyrinx-replay-" + secrets.token_hex(8)
    report.setdefault("android", {})[serial] = {
        field: output([*adb, "shell", "getprop", field]).decode().strip()
        for field in ["ro.product.model", "ro.build.fingerprint", "ro.product.cpu.abi"]
    }
    output([*adb, "shell", "mkdir", remote])
    paths = [remote + "/" + name for name in ["probe.jar", "libreplay.so", "input.f32"]]
    try:
        for file, dest in zip([dex, library], paths):
            output([*adb, "push", file, dest])
        output([*adb, "shell", "chmod", "444", paths[0]])
        art = [*adb, "exec-out", "env", f"CLASSPATH={paths[0]}", "app_process", "/", "Replay", paths[1]]
        if output([*art, "registry"]) != b"registry OK":
            raise ValueError(f"Android/{serial}: JNI registry round trip failed")
        report["android"][serial]["registry_round_trip"] = True
        for case in cases:
            name = case["name"]
            output([*adb, "push", windows[name], paths[2]])
            packet = output([*art, str(case["profile"]), paths[2],
                             "2" if "secondary_path" in case else "1"])
            # A different C library and math library: decisions must match host C exactly, and
            # metrics stay within each fixture's tolerance.
            judge(f"KISS/Android Kotlin/JNI {serial}/{name}", case, packet, expected[name])
    finally:
        output([*adb, "shell", "rm", "-f", *paths])
        output([*adb, "shell", "rmdir", remote])
