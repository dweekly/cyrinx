"""Compare the C batch ABI, Swift C interop and Kotlin/JNI on fixed PCM windows."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
ADAPTERS = ROOT / "Tests/BatchReplay"
CORE = ROOT / "Sources/CCyrinx"
MANIFEST = ROOT / "Tests/Fixtures/conformance/november-batch.json"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def run(args):
    return subprocess.run([str(arg) for arg in args], check=True, stdout=subprocess.PIPE).stdout


def compile_probes(build, backend, java_home, jar):
    apple = sys.platform == "darwin"
    suffix = ".dylib" if apple else ".so"
    shared = "-dynamiclib" if apple else "-shared"
    library = build / ("libbatch" + suffix)
    includes = ["-I", CORE / "include", "-I", CORE / "kissfft"]
    sources = [CORE / name for name in (
        "cyrinx_batch.c", "cyrinx_profiles.c", "cyrinx_sha256.c", "cyrinx_bulk.c",
        "cyrinx_fft.c", "kissfft/kiss_fft.c", "kissfft/kiss_fftr.c",
    )]
    fft = ["-DCYRINX_FFT_ACCELERATE", "-framework", "Accelerate"] if backend == "Accelerate" else []
    run(["clang", "-std=c11", "-O2", "-fPIC", shared, "-Dkiss_fft_scalar=double",
         *includes, *sources, *fft, "-lm", "-o", library])
    link = [library, f"-Wl,-rpath,{build}"]
    direct = build / "direct"
    run(["clang", *includes, ADAPTERS / "direct.c", *link, "-o", direct])
    swift = build / "swift-replay"
    # Both backends compile the shipping Swift wrapper against their own C build.
    headers = " ".join(f'header "{CORE}/include/cyrinx/{name}.h"' for name in ("cyrinx_batch", "cyrinx_bulk"))
    (build / "module.modulemap").write_text(f"module CCyrinx {{ {headers} export * }}\n")
    swift_flags = ["swiftc", "-swift-version", "6", "-module-cache-path", build.parent / "modules",
                   "-I", build, "-I", CORE / "include"]
    wrapper = build / ("libCyrinx" + suffix)
    run([*swift_flags, "-enable-testing", "-emit-module", "-emit-library", "-module-name", "Cyrinx",
         "-emit-module-path", build / "Cyrinx.swiftmodule", ROOT / "Sources/Cyrinx/BatchPHY.swift",
         library, "-Xlinker", "-rpath", "-Xlinker", build, "-o", wrapper])
    run([*swift_flags, "-I", ADAPTERS, ADAPTERS / "Replay.swift", wrapper,
         library, "-Xlinker", "-rpath", "-Xlinker", build, "-o", swift])
    jni = build / ("libreplay" + suffix)
    run(["clang", "-std=c11", shared, "-fPIC", *includes, "-I", java_home / "include",
         "-I", java_home / "include" / ("darwin" if apple else "linux"),
         ADAPTERS / "jni.c", *link, "-o", jni])
    return {"C": [direct], "Swift": [swift],
            "Kotlin/JNI": [java_home / "bin/java", "-Xcheck:jni", "-cp", jar, "Replay", jni]}


def fixture_bytes(case, archive):
    if "path" in case:
        pcm = (ROOT / case["path"]).read_bytes()
        if "secondary_path" in case:
            from array import array

            primary = array("f", pcm)
            secondary = array("f", (ROOT / case["secondary_path"]).read_bytes())
            aligned = min(len(primary), len(secondary))
            stereo = array("f", [0]) * (aligned * 2)
            stereo[::2], stereo[1::2] = primary[:aligned], secondary[:aligned]
            pcm = stereo.tobytes()
        if "dropout" in case:
            pcm = bytearray(pcm)
            period, width = case["dropout"]["period"], case["dropout"]["width"]
            for start in range(0, len(pcm), 4 * period):
                end = min(start + 4 * width, len(pcm))
                pcm[start:end] = bytes(end - start)
    else:
        import numpy as np

        source = archive / case["archive"]
        with source.open("rb") as file:
            if hashlib.file_digest(file, "sha256").hexdigest() != case["source_sha256"]:
                raise ValueError(f"source digest mismatch for {source}")
        if "member" in case:
            with np.load(source, allow_pickle=False) as arrays:
                samples = arrays[case["member"]]
        else:
            samples = np.load(source, allow_pickle=False)
        start, count = case["start"], case["samples"]
        pcm = samples[start:start + count].astype("<f4").tobytes()
        if len(pcm) != 4 * count:
            raise ValueError(f"truncated window in {source}")
    if sha(pcm) != case["pcm_sha256"]:
        raise ValueError(f"PCM digest mismatch in {case['name']}")
    return pcm


def verify(expected, mask, payload):
    ordered = "".join(str(v) for v in mask)
    if any(v > 1 for v in mask) or ordered != expected["mask"] or len(payload) != 256 * len(mask):
        raise ValueError(f"expected mask {expected['mask']}, observed {ordered}, {len(payload)} payload bytes")
    valid = b"".join(payload[i * 256:(i + 1) * 256] for i, v in enumerate(mask) if v)
    if sha(valid) != expected["valid_payload_sha256"]:
        raise ValueError("decoded bytes differ from the transmitted blocks")
    return {"mask": ordered, "valid_payload_sha256": sha(valid)}


def retained_research(build, cases, windows, rows):
    import numpy as np

    library = build / "research.dylib"
    run(["bash", ROOT / "scratch/garage/build_clock_dylib.sh", library])
    # clib reads its library path at import, so both variables are set first. A checkout
    # with no locally built scratch/hw20k library then uses this build as well.
    os.environ["CYRINX_CLOCK_CODEC_PATH"] = str(library)
    os.environ["CYRINX_BULK_CODEC_PATH"] = str(library)
    sys.path.insert(0, str(ROOT / "scratch/garage"))
    import clock_offset
    import clib

    if Path(clib.loaded_library_path()).resolve() != library.resolve():
        raise ValueError("research lane loaded a codec other than the one it built")
    clock_offset.set_pilot_fit(clock_offset.FIT_COHERENT)
    cfg = clib.make_cfg(f_lo=300, f_hi=11000, bits_per_bin=2, rate="1/2", nfft=16384, cp=8192, n_sym=3)
    for case in cases:
        if "research" in case:
            samples = np.fromfile(windows[case["name"]], dtype="<f4")
            decoded = clock_offset.decode(cfg, samples)
            if decoded is None:
                raise ValueError(f"research decoder failed on {case['name']}")
            actual = verify(case["research"], bytes(decoded["block_valid"]), decoded["payload"])
            rows.append(dict(fixture=case["name"], binding="research C via ctypes", backend="KISS", **actual))


def execute(args, report):
    if sys.byteorder != "little":
        raise ValueError("replay adapters require a little-endian host")
    manifest = json.loads(args.manifest.read_bytes())
    if manifest["version"] != 1:
        raise ValueError("unsupported replay manifest")
    cases = [case for case in manifest["cases"] if not args.digital_only or "archive" not in case]
    # The tier names the evidence this run reads, whichever flags selected it.
    report["tier"] = "retained" if any("archive" in case for case in cases) else "digital"
    if not cases:
        raise ValueError("empty replay selection")
    kotlin = os.environ.get("KOTLINC") or shutil.which("kotlinc") or (
        "/Applications/Android Studio.app/Contents/plugins/Kotlin/kotlinc/bin/kotlinc")
    java = os.environ.get("JAVA_HOME")
    if not java:
        java = (run(["/usr/libexec/java_home"]).decode().strip() if sys.platform == "darwin"
                else Path(shutil.which("javac")).resolve().parents[1])
    java_home = Path(java)
    report["toolchains"] = {}
    for name, command in {"C": ["clang", "--version"], "Swift": ["swiftc", "--version"],
                          "Kotlin": [kotlin, "-version"], "JVM": [java_home / "bin/java", "-version"]}.items():
        report["toolchains"][name] = subprocess.check_output(
            command, stderr=subprocess.STDOUT, text=True).strip()
    with tempfile.TemporaryDirectory(prefix="cyrinx-replay-") as tmp:
        build = Path(tmp)
        windows = {}
        for case in cases:
            path = build / (case["name"] + ".f32")
            path.write_bytes(fixture_bytes(case, args.archive))
            windows[case["name"]] = path
        jar = build / "adapter.jar"
        run([kotlin, ADAPTERS / "Replay.kt", "-include-runtime", "-d", jar])
        kiss_reference = {}
        for backend in (["KISS", "Accelerate"] if sys.platform == "darwin" else ["KISS"]):
            lane = build / backend
            lane.mkdir()
            probes = compile_probes(lane, backend, java_home, jar)
            if run([*probes["Kotlin/JNI"], "registry"]) != b"registry OK":
                raise ValueError(f"{backend}: JNI registry round trip failed")
            report.setdefault("jni_registry", []).append(backend)
            for case in cases:
                reference = None
                key, blocks = case["profile"], len(case["mask"])
                for name, cmd in probes.items():
                    label = f"{backend}/{name}/{case['name']}"
                    report["checking"] = label
                    packet = run([*cmd, key, windows[case["name"]], 2 if "secondary_path" in case else 1])
                    identity = packet[:32].hex()
                    if identity != manifest["profile_identities"][key]:
                        raise ValueError(f"{label} returned a different profile identity")
                    receiver, reason = struct.unpack("<II", packet[32:40])
                    if (receiver, reason) != (case["receiver"], case["reason"]):
                        raise ValueError(f"{label}: unexpected receiver {receiver} or reason {reason}")
                    actual = verify(case, packet[40:40 + blocks], packet[40 + blocks:])
                    if reference is not None and packet != reference:
                        raise ValueError(f"{label} differs from the direct C output")
                    if reference is None:
                        reference = packet
                        if backend == "KISS":
                            kiss_reference[case["name"]] = packet
                    report["results"].append(dict(binding=name, backend=backend, fixture=case["name"],
                                                  profile_identity=identity, receiver=receiver, reason=reason, **actual))
        if args.android:
            import android_batch_replay as android

            dex, library, ndk = android.build(build, ADAPTERS, jar)
            report["toolchains"]["Android NDK"] = ndk
            for serial in args.android:
                android.replay(serial, dex, library, cases, windows, kiss_reference, report)
        if any("research" in case for case in cases):
            report["checking"] = "research control frames"
            retained_research(build, cases, windows, report["results"])
    report.pop("checking", None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--archive", type=Path, default=ROOT.parent / "cyrinx-artifacts/garage")
    parser.add_argument("--android", action="append", metavar="SERIAL", help="also run on this USB arm64 Android device")
    parser.add_argument("--digital-only", action="store_true", help="only run fixtures available in a checkout")
    parser.add_argument("--report", type=Path, default=ROOT / "artifacts/conformance/november-batch.json")
    args = parser.parse_args()
    # Everything that can fail runs inside the try, so a failure replaces an earlier passing report.
    report = {"status": "failed", "command": shlex.join(sys.argv), "host": platform.platform(),
              "arch": platform.machine(), "results": []}
    try:
        report["revision"] = run(["git", "-C", ROOT, "rev-parse", "HEAD"]).decode().strip()
        report["worktree_dirty"] = bool(run(["git", "-C", ROOT, "status", "--porcelain"]))
        report["manifest_sha256"] = sha(args.manifest.read_bytes())
        execute(args, report)
        report["status"] = "passed"
    except Exception as error:
        report["error"] = str(error)
        raise
    finally:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
        print(f"{report['status']}: {len(report['results'])} results in {args.report}")


if __name__ == "__main__":
    main()
