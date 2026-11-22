"""Compare the C batch ABI, Swift C interop and Kotlin/JNI on fixed PCM windows."""
import argparse
import hashlib
import json
import math
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
    probes = {"C": [direct], "Swift": [swift],
              "Kotlin/JNI": [java_home / "bin/java", "-Xcheck:jni", "-cp", jar, "Replay", jni]}
    if backend == "KISS":
        # The same C sources and probe, instrumented. Any report from either sanitizer ends the
        # process with a failure, which fails the run.
        checked = build / "direct-sanitized"
        run(["clang", "-std=c11", "-O1", "-g", "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
             "-Dkiss_fft_scalar=double", *includes, ADAPTERS / "direct.c", *sources, "-lm", "-o", checked])
        probes[SANITIZED] = [checked]
    return probes


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
        if "gain" in case or "nonfinite_at" in case or "truncate" in case:
            from array import array

            samples = array("f", bytes(pcm))
            if "gain" in case:  # Overdrive, then clamp as a full-scale recorder would.
                samples = array("f", (max(-1.0, min(1.0, s * case["gain"])) for s in samples))
            if "nonfinite_at" in case:
                samples[case["nonfinite_at"]] = math.nan
            pcm = samples[:case.get("truncate", len(samples))].tobytes()
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


SANITIZED = "C (ASan+UBSan)"
HEADER = struct.Struct("<i32s6I2Q3d")  # Tests/BatchReplay/Packet.h
EXACT = ("status", "identity", "receiver", "reason", "blocks_total", "blocks_ok", "clipping", "nonfinite",
         "payload_bytes", "logical_samples")
METRICS = ("evm_rms", "primary_holdout_pilot_rms", "mrc_holdout_pilot_rms")


def plain(value):
    """A metric as JSON can carry it: the number, or a name for a value that is not finite."""
    if isinstance(value, float) and not math.isfinite(value):
        return "nan" if math.isnan(value) else ("inf" if value > 0 else "-inf")
    return value


def parse(packet):
    """Split one binding's record into named fields, the ordered mask and the payload."""
    if len(packet) < HEADER.size:
        raise ValueError(f"record: {len(packet)} bytes is shorter than the header")
    values = HEADER.unpack_from(packet)
    record = dict(zip(EXACT + METRICS, values))
    record["identity"] = record["identity"].hex()
    body = packet[HEADER.size:]
    if record["status"] == 0:
        blocks = record["blocks_total"]
        record["mask"], record["payload"] = body[:blocks], body[blocks:]
    elif body:
        raise ValueError("record: a rejected decode carried a mask or payload")
    return record


def expectation(case, manifest, pcm):
    """What every binding must report for this case, from the list and the input itself."""
    if case.get("status", 0):
        return dict(status=case["status"])
    mask = case["mask"]
    channels = 2 if "secondary_path" in case else 1
    return dict(status=0, identity=manifest["profile_identities"][case["profile"]],
                receiver=case["receiver"], reason=case["reason"], blocks_total=len(mask),
                blocks_ok=mask.count("1"), clipping=case["clipping"], nonfinite=case["nonfinite"],
                payload_bytes=256 * len(mask), logical_samples=len(pcm) // 4 // channels)


def compare(case, expected, record):
    """Raise naming the first field that differs; return the metric comparisons otherwise."""
    for field, value in expected.items():
        if record[field] != value:
            raise ValueError(f"{field}: expected {value}, observed {record[field]}")
    if expected["status"]:
        return {}
    mask, payload = record["mask"], record["payload"]
    ordered = "".join(str(v) for v in mask)
    if any(v > 1 for v in mask) or ordered != case["mask"] or len(payload) != 256 * len(mask):
        raise ValueError(f"mask: expected mask {case['mask']}, observed {ordered}, {len(payload)} payload bytes")
    valid = b"".join(payload[i * 256:(i + 1) * 256] for i, v in enumerate(mask) if v)
    if sha(valid) != case["valid_payload_sha256"]:
        raise ValueError("payload: decoded bytes differ from the transmitted blocks")
    metrics = {}
    for field in METRICS:
        target, tolerance, observed = case["metrics"][field]["value"], case["metrics"][field]["tolerance"], record[field]
        if isinstance(target, str):  # The decoder reports "not evaluated" as a non-finite value.
            delta = 0.0 if plain(observed) == target else math.inf
        else:
            delta = abs(observed - target)
        if not delta <= tolerance:  # Also false when the difference is not a number.
            raise ValueError(f"{field}: expected {target} within {tolerance}, observed {plain(observed)}")
        metrics[field] = dict(observed=plain(observed), expected=target, delta=delta, tolerance=tolerance,
                              passed=True)
    return dict(mask=ordered, valid_payload_sha256=sha(valid), metrics=metrics)


def same_decision(first, second):
    """Whether two records agree on everything except floating-point metrics."""
    return all(first.get(k) == second.get(k) for k in EXACT + ("mask", "payload"))


def mutate(packet, field, case):
    """Deliberately change one field of a record, for the negative controls."""
    data = bytearray(packet)
    offsets = dict(identity=4, blocks_ok=48, logical_samples=68, evm_rms=76)
    if field == "payload":
        data[HEADER.size + len(case["mask"]) + 256 * case["mask"].index("1")] ^= 1
    elif field == "mask":
        data[HEADER.size] ^= 1
    elif field == "evm_rms":
        value = struct.unpack_from("<d", data, offsets[field])[0] + 4 * case["metrics"][field]["tolerance"] + 1e-6
        struct.pack_into("<d", data, offsets[field], value)
    elif field in offsets:
        data[offsets[field]] ^= 1
    else:
        raise ValueError(f"no mutation defined for {field}")
    return bytes(data)


def validate_report(report):
    """The report's own shape, checked before it is written. Version: REPORT_SCHEMA."""
    required = {"schema": str, "status": str, "command": str, "host": str, "arch": str, "results": list}
    for key, kind in required.items():
        if not isinstance(report.get(key), kind):
            raise ValueError(f"report: {key} is missing or not a {kind.__name__}")
    if report["status"] == "passed":
        for key in ("revision", "manifest_sha256", "tier", "toolchains"):
            if key not in report:
                raise ValueError(f"report: a passing report needs {key}")
    for row in report["results"]:
        if not {"binding", "backend", "fixture"} <= row.keys():
            raise ValueError("report: a result lacks its binding, backend or fixture")
        for metric in row.get("metrics", {}).values():
            if metric.keys() != {"observed", "expected", "delta", "tolerance", "passed"}:
                raise ValueError("report: a metric comparison is incomplete")


REPORT_SCHEMA = "cyrinx-batch-replay-report/2"
IDENTITY_FIELDS = {"name", "profile", "pcm_sha256"}
DECODED_FIELDS = {"mask", "receiver", "reason", "valid_payload_sha256", "clipping", "nonfinite", "metrics"}
FILE_FIELDS = {"path"}, {"secondary_path", "dropout", "gain", "nonfinite_at", "truncate", "status"}
RECORDING_FIELDS = {"archive", "source_sha256", "start", "samples"}, {"member", "research"}
RESEARCH_PROFILE = 2  # The control geometry is the only one the research lane decodes.


def validate_case(case, profile_count):
    """Refuse any case this runner would not apply in full.

    A field it ignored would let a run pass against a different input or decoder than the list
    asked for, so the set of fields is closed: one source, and only the options that source reads.
    """
    name = case["name"]
    sources = [fields for fields in (FILE_FIELDS, RECORDING_FIELDS) if fields[0] & case.keys()]
    if len(sources) != 1:
        raise ValueError(f"{name}: name exactly one of path or archive")
    required, optional = sources[0]
    # A case the decoder must reject carries the status alone; any other carries the full result.
    rejected = case.get("status", 0) != 0
    if type(case.get("status", 0)) is not int:
        raise ValueError(f"{name}: status is a whole number")
    required = required | IDENTITY_FIELDS | (set() if rejected else DECODED_FIELDS)
    if not required <= case.keys() <= required | optional:
        odd = sorted((required - case.keys()) | (case.keys() - required - optional))
        raise ValueError(f"{name}: missing or unsupported fields {', '.join(odd)}")
    profile = case["profile"]
    if type(profile) is not int or not 0 <= profile < profile_count:
        raise ValueError(f"{name}: unknown profile {profile!r}")
    if not rejected:
        mask, metrics = case["mask"], case["metrics"]
        if not isinstance(mask, str) or not mask or set(mask) - {"0", "1"}:
            raise ValueError(f"{name}: the mask is a string of 0 and 1")
        if any(type(case[key]) is not int or case[key] < 0 for key in ("clipping", "nonfinite")):
            raise ValueError(f"{name}: clipping and nonfinite are counts")
        if (not isinstance(metrics, dict) or metrics.keys() != set(METRICS) or not all(
                isinstance(m, dict) and m.keys() == {"value", "tolerance"}
                and (m["value"] in ("inf", "-inf", "nan") if isinstance(m["value"], str)
                     else type(m["value"]) in (int, float) and math.isfinite(m["value"]))
                and type(m["tolerance"]) in (int, float) and math.isfinite(m["tolerance"]) and m["tolerance"] >= 0
                for m in metrics.values())):
            raise ValueError(f"{name}: metrics needs a value and a tolerance for each of {', '.join(METRICS)}")
    transforms = {"gain", "nonfinite_at", "truncate"} & case.keys()
    if transforms and "secondary_path" in case:
        raise ValueError(f"{name}: {', '.join(sorted(transforms))} apply to one-channel inputs only")
    if "gain" in case and (type(case["gain"]) not in (int, float) or not 0 < case["gain"] < 1e6):
        raise ValueError(f"{name}: gain is a positive number")
    if any(key in case and (type(case[key]) is not int or case[key] < 0) for key in ("nonfinite_at", "truncate")):
        raise ValueError(f"{name}: nonfinite_at and truncate are sample positions")
    dropout = case.get("dropout")
    if dropout is not None and "secondary_path" in case:
        # The transform zeroes single-channel samples; applied to interleaved audio it would mute
        # one microphone instead of a span of time.
        raise ValueError(f"{name}: dropout is applied to one-channel inputs only")
    if dropout is not None and (not isinstance(dropout, dict) or dropout.keys() != {"period", "width"}
                                or not all(type(v) is int for v in dropout.values())
                                or not 0 < dropout["width"] <= dropout["period"]):
        raise ValueError(f"{name}: dropout needs whole-number period and width, with width within the period")
    research = case.get("research")
    if research is not None and (profile != RESEARCH_PROFILE or not isinstance(research, dict)
                                 or research.keys() != {"mask", "valid_payload_sha256"}):
        raise ValueError(f"{name}: a research expectation needs profile {RESEARCH_PROFILE}, a mask and a hash")


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
            mask, payload = bytes(decoded["block_valid"]), decoded["payload"]
            ordered = "".join(str(v) for v in mask)
            valid = b"".join(payload[i * 256:(i + 1) * 256] for i, v in enumerate(mask) if v)
            if ordered != case["research"]["mask"] or sha(valid) != case["research"]["valid_payload_sha256"]:
                raise ValueError(f"research decoder: unexpected result on {case['name']}")
            actual = dict(mask=ordered, valid_payload_sha256=sha(valid))
            rows.append(dict(fixture=case["name"], binding="research C via ctypes", backend="KISS", **actual))


def execute(args, report):
    if sys.byteorder != "little":
        raise ValueError("replay adapters require a little-endian host")
    manifest = json.loads(args.manifest.read_bytes())
    if manifest["version"] != 1:
        raise ValueError("unsupported replay manifest")
    names = [case.get("name") for case in manifest["cases"]]
    if not all(isinstance(name, str) and name for name in names) or len(set(names)) != len(names):
        raise ValueError("every replay case needs its own name")
    for case in manifest["cases"]:
        validate_case(case, len(manifest["profile_identities"]))
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
        windows, expected = {}, {}
        for index, case in enumerate(cases):
            # Staged by position: names that differ only in case would share a file on macOS.
            path = build / f"input-{index:03d}.f32"
            pcm = fixture_bytes(case, args.archive)
            path.write_bytes(pcm)
            windows[case["name"]] = path
            expected[case["name"]] = expectation(case, manifest, pcm)
        jar = build / "adapter.jar"
        run([kotlin, ADAPTERS / "Replay.kt", "-include-runtime", "-d", jar])
        kiss_reference = {}

        def judge(label, case, packet, reference=None):
            """Compare one binding's record with the list, then with direct C on the same backend."""
            report["checking"] = label
            if args.mutate and args.mutate.rsplit(":", 1)[0] == label:
                packet = mutate(packet, args.mutate.rsplit(":", 1)[1], case)
            try:
                record = parse(packet)
                if args.observe:
                    outcome = {k: plain(v) for k, v in record.items() if k not in ("payload", "mask")}
                    if record["status"] == 0:
                        outcome["mask"] = "".join(str(v) for v in record["mask"])
                        outcome["valid_payload_sha256"] = sha(b"".join(
                            record["payload"][i * 256:(i + 1) * 256] for i, v in enumerate(record["mask"]) if v))
                else:
                    outcome = compare(case, expected[case["name"]], record)
                    if reference is not None and not same_decision(record, parse(reference)):
                        raise ValueError("record: differs from the direct C output")
            except ValueError as error:
                raise ValueError(f"{label}: {error}") from error
            binding, backend = label.split("/", 1)[1].rsplit("/", 1)[0], label.split("/", 1)[0]
            report["results"].append({**outcome, "binding": binding, "backend": backend,
                                      "fixture": case["name"], "status": record["status"]})
            return packet

        for backend in (["KISS", "Accelerate"] if sys.platform == "darwin" else ["KISS"]):
            lane = build / backend
            lane.mkdir()
            probes = compile_probes(lane, backend, java_home, jar)
            if run([*probes["Kotlin/JNI"], "registry"]) != b"registry OK":
                raise ValueError(f"{backend}: JNI registry round trip failed")
            report.setdefault("jni_registry", []).append(backend)
            for case in cases:
                reference = None
                for name, cmd in probes.items():
                    packet = run([*cmd, case["profile"], windows[case["name"]],
                                  2 if "secondary_path" in case else 1])
                    packet = judge(f"{backend}/{name}/{case['name']}", case, packet, reference)
                    # Bindings share one C build, so their records are identical to the byte. The
                    # sanitized probe is a separate build and is held to the tolerances instead.
                    if reference is not None and name != SANITIZED and packet != reference and not args.observe:
                        raise ValueError(f"{backend}/{name}/{case['name']}: record: differs from direct C")
                    if reference is None:
                        reference = packet
                        if backend == "KISS":
                            kiss_reference[case["name"]] = packet
        if args.android:
            import android_batch_replay as android

            dex, library, ndk = android.build(build, ADAPTERS, jar)
            report["toolchains"]["Android NDK"] = ndk
            for serial in args.android:
                android.replay(serial, dex, library, cases, windows, kiss_reference, report, judge)
        if any("research" in case for case in cases):
            report["checking"] = "research control frames"
            retained_research(build, cases, windows, report["results"])
    report.pop("checking", None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    # The same default as scratch/garage/retain.py, so a linked worktree finds the recordings.
    parser.add_argument("--archive", type=Path, help="retained recordings; default $CYRINX_GARAGE_ARTIFACTS "
                        "or ~/dev/cyrinx-artifacts/garage",
                        default=Path(os.environ.get("CYRINX_GARAGE_ARTIFACTS")
                                     or Path.home() / "dev/cyrinx-artifacts/garage"))
    parser.add_argument("--android", action="append", metavar="SERIAL", help="also run on this USB arm64 Android device")
    parser.add_argument("--digital-only", action="store_true", help="only run fixtures available in a checkout")
    parser.add_argument("--report", type=Path, default=ROOT / "artifacts/conformance/november-batch.json")
    parser.add_argument("--mutate", metavar="BACKEND/BINDING/FIXTURE:FIELD",
                        help="negative control: change one field of one record before it is compared")
    parser.add_argument("--observe", action="store_true",
                        help="record what each binding reports without comparing; for regenerating a list")
    args = parser.parse_args()
    # Everything that can fail runs inside the try, so a failure replaces an earlier passing report.
    report = {"schema": REPORT_SCHEMA, "status": "failed", "command": shlex.join(sys.argv),
              "host": platform.platform(), "arch": platform.machine(), "results": []}
    try:
        report["revision"] = run(["git", "-C", ROOT, "rev-parse", "HEAD"]).decode().strip()
        report["worktree_dirty"] = bool(run(["git", "-C", ROOT, "status", "--porcelain"]))
        report["manifest_sha256"] = sha(args.manifest.read_bytes())
        args.archive = args.archive.expanduser()
        execute(args, report)
        report["status"] = "observed" if args.observe else "passed"
        validate_report(report)
    except Exception as error:
        report["error"] = str(error)
        raise
    finally:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
        print(f"{report['status']}: {len(report['results'])} results in {args.report}")


if __name__ == "__main__":
    main()
