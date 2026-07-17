#!/usr/bin/env python3
"""Hardware-in-the-loop acoustic harness: Mac <-> Android device.

The Android app (Apps/HIL/android) exposes two dumb primitives via adb:
  - rec_pcm:  record PCM16LE from the mic array to the app files dir
  - play_pcm: play a PCM16LE file pushed to /data/local/tmp without an implicit
              media-volume change

All DSP/modem work happens here on the Mac so we can iterate on the *real*
over-the-air channel without rebuilding the app each cycle.

Conventions: float32 numpy arrays in [-1, 1], 48 kHz unless stated.
"""

import contextlib
import fcntl
import hashlib
import json
import re
import shlex
import subprocess
import time
import sys
import os
import numpy as np

SR = 48000
PKG = "com.dweekly.cyrinxhil"
ACT = f"{PKG}/.MainActivity"
TAG = "CyrinxHILAndroid"
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
MAC_MIC = "MacBook Pro Microphone"
MAC_SPK = "MacBook Pro Speakers"
ANDROID_AUDIO_LOCK = "/private/tmp/cyrinx-android-audio.lock"
ANDROID_SERIAL_ENV = "ANDROID_SERIAL"
ANDROID_AUDIO_ENCODING_PCM_16BIT = 2
ANDROID_AUDIO_DEVICE_TYPE_BUILTIN_MIC = 15
ANDROID_AUDIO_SOURCE_CODES = {
    "mic": 1,
    "camcorder": 5,
    "voice_recognition": 6,
    "unprocessed": 9,
}
ANDROID_TARGET_FIELDS = (
    "serial",
    "model",
    "build_fingerprint",
    "installed_package_apk_sha256",
)


def sh(cmd, **kw):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, **kw)


def validated_android_serial(environ=None):
    """Return a shell-safe, explicitly selected ADB serial or fail closed."""
    environment = os.environ if environ is None else environ
    serial = environment.get(ANDROID_SERIAL_ENV)
    if serial is None or not serial:
        raise RuntimeError(f"{ANDROID_SERIAL_ENV} must select the reviewed Android target")
    if serial != serial.strip() or len(serial) > 255:
        raise RuntimeError(f"{ANDROID_SERIAL_ENV} is malformed")
    if serial.startswith("-") or not re.fullmatch(r"[A-Za-z0-9._:-]+", serial):
        raise RuntimeError(f"{ANDROID_SERIAL_ENV} contains unsupported characters")
    return serial


def adb(args, **kw):
    """Run ADB only against the explicitly selected ``ANDROID_SERIAL`` target."""
    serial = validated_android_serial()
    if isinstance(args, str):
        arguments = shlex.split(args)
    else:
        arguments = [str(value) for value in args]
    if not arguments:
        raise ValueError("ADB arguments must be nonempty")
    if "shell" in kw:
        raise ValueError("ADB wrapper does not permit subprocess shell execution")
    options = dict(kw)
    if not any(name in options for name in ("stdout", "stderr")):
        options["capture_output"] = True
    if "text" not in options:
        options["text"] = True
    return subprocess.run(["adb", "-s", serial, *arguments], **options)


def _adb_error_text(result):
    value = result.stderr
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").strip()
    return str(value or "").strip()


def _checked_adb(args, label):
    result = adb(args)
    if result.returncode != 0:
        raise RuntimeError(f"Android {label} failed: {_adb_error_text(result)}")
    return result


def _checked_adb_text(args, label):
    result = _checked_adb(args, label)
    value = result.stdout.strip()
    if not value:
        raise RuntimeError(f"Android {label} returned an empty value")
    return value


def collect_android_target_provenance(package=PKG):
    """Collect identity and installed-base-APK provenance for the selected target."""
    serial = validated_android_serial()
    reported_serial = _checked_adb_text(["get-serialno"], "serial query")
    if reported_serial != serial:
        raise RuntimeError(
            f"selected Android serial mismatch: expected={serial!r}, reported={reported_serial!r}"
        )
    model = _checked_adb_text(
        ["shell", "getprop", "ro.product.model"],
        "model query",
    )
    fingerprint = _checked_adb_text(
        ["shell", "getprop", "ro.build.fingerprint"],
        "build-fingerprint query",
    )
    package_paths = _checked_adb_text(
        ["shell", "pm", "path", package],
        "installed-package path query",
    )
    paths = [
        line.removeprefix("package:")
        for line in package_paths.splitlines()
        if line.startswith("package:")
    ]
    base_paths = [path for path in paths if path.endswith("/base.apk")]
    if len(base_paths) != 1:
        raise RuntimeError(
            f"installed package {package} has {len(base_paths)} base APK paths; expected one"
        )
    base_path = base_paths[0]
    if not re.fullmatch(r"/[A-Za-z0-9_./=+@~:-]+", base_path):
        raise RuntimeError(f"installed package returned an unsafe base APK path: {base_path!r}")
    apk_result = adb(["exec-out", "cat", base_path], text=False)
    if apk_result.returncode != 0:
        raise RuntimeError(f"installed APK read failed: {_adb_error_text(apk_result)}")
    apk_bytes = apk_result.stdout
    if not isinstance(apk_bytes, bytes) or not apk_bytes:
        raise RuntimeError("installed APK read returned no bytes")
    return {
        "serial": serial,
        "model": model,
        "build_fingerprint": fingerprint,
        "package": package,
        "base_apk_path": base_path,
        "base_apk_byte_count": len(apk_bytes),
        "installed_package_apk_sha256": hashlib.sha256(apk_bytes).hexdigest(),
    }


def assert_android_target(expected, observed=None):
    """Require exact reviewed target identity, collecting it when not supplied."""
    if not isinstance(expected, dict):
        raise TypeError("expected Android target must be a dictionary")
    missing = [field for field in ANDROID_TARGET_FIELDS if not expected.get(field)]
    if missing:
        raise ValueError("expected Android target lacks: " + ", ".join(missing))
    expected_digest = str(expected["installed_package_apk_sha256"]).lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected_digest):
        raise ValueError("expected installed-package APK SHA-256 is malformed")
    actual = collect_android_target_provenance() if observed is None else observed
    actual_missing = [field for field in ANDROID_TARGET_FIELDS if not actual.get(field)]
    if actual_missing:
        raise RuntimeError("observed Android target lacks: " + ", ".join(actual_missing))
    mismatches = {}
    for field in ANDROID_TARGET_FIELDS:
        wanted = expected_digest if field == "installed_package_apk_sha256" else expected[field]
        got = str(actual[field]).lower() if field == "installed_package_apk_sha256" else actual[field]
        if got != wanted:
            mismatches[field] = {"expected": wanted, "observed": got}
    if mismatches:
        raise RuntimeError(f"Android target identity mismatch: {mismatches}")
    return actual


@contextlib.contextmanager
def exclusive_android_audio_lock(label):
    """Cooperatively exclude other Cyrinx HIL audio campaigns."""
    with open(ANDROID_AUDIO_LOCK, "a+", encoding="utf-8") as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError(f"Android audio lock is held: {ANDROID_AUDIO_LOCK}") from error
        stream.seek(0)
        stream.truncate()
        stream.write(f"pid={os.getpid()} label={label}\n")
        stream.flush()
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


# ---------------- Android control ----------------

def android_prepare(media_volume=None):
    """Wake the device and bring the app to the foreground.

    The mic returns silence (zeros) if the activity is not foreground/visible,
    so this must run before every capture. The activity owns a scoped
    ``FLAG_KEEP_SCREEN_ON``; this helper does not mutate the persistent Android
    ``stay_on_while_plugged_in`` setting.

    `media_volume` is an explicit playback-only mutation. Recording callers
    leave it as None so capture preparation does not change output state.
    """
    _checked_adb("shell input keyevent KEYCODE_WAKEUP", "wake command")
    _checked_adb("shell wm dismiss-keyguard", "keyguard-dismiss command")
    # An expanded notification shade makes the activity non-top -> mic capture
    # gets silenced (zeros) by the audio policy. Always collapse it.
    _checked_adb("shell cmd statusbar collapse", "status-bar collapse command")
    _checked_adb(f"shell am start -n {ACT}", "HIL activity start")
    if media_volume is not None:
        _checked_adb(
            f"shell media volume --stream 3 --set {int(media_volume)}",
            "media-volume command",
        )
    time.sleep(0.8)
    wake = _checked_adb("shell dumpsys power", "power-state query").stdout
    if "mWakefulness=Awake" not in wake:
        raise RuntimeError("device failed to wake (screen off -> mic will be silenced)")


def logcat_wait(pattern, timeout_s, poll_s=0.5):
    """Wait until a logcat line containing pattern appears; return the line."""
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        out = _checked_adb(f"logcat -d -s {TAG}", "logcat query").stdout
        for line in out.splitlines():
            if pattern in line:
                return line
        time.sleep(poll_s)
    tail = _checked_adb(f"logcat -d -s {TAG}", "logcat timeout query").stdout
    raise TimeoutError(
        f"logcat_wait: no '{pattern}' within {timeout_s}s. Tail:\n"
        + "\n".join(tail.splitlines()[-12:])
    )


def _checked_request_id(request_id):
    """Return a shell-safe request identifier used to correlate Android logs."""
    value = str(request_id)
    if not re.fullmatch(r"[A-Za-z0-9._-]+", value):
        raise ValueError("Android audio request IDs may contain only letters, digits, dot, underscore, or hyphen")
    return value


def android_request_log(request_id):
    """Return only HIL log lines carrying the exact per-request correlation token."""
    request = _checked_request_id(request_id)
    result = adb(f"logcat -d -s {TAG}")
    if result.returncode != 0:
        raise RuntimeError(f"failed to read Android request log: {result.stderr}")
    marker = f"request={request} "
    return "\n".join(line for line in result.stdout.splitlines() if marker in line) + "\n"


def parse_android_record_realization(begin_line, done_line, request_id):
    """Parse and cross-check the AudioRecord format, frame count, and byte count."""
    request = _checked_request_id(request_id)
    begin = re.search(
        r"rec_pcm begin: request=(\S+) src=(\S+) rate=(\d+) ch=(\d+) frames=(\d+) ->",
        begin_line,
    )
    done = re.search(
        r"rec_pcm done: request=(\S+) out=(\S+) frames=(\d+) .* clipped=(\d+) bytes=(\d+)",
        done_line,
    )
    if begin is None or done is None:
        raise RuntimeError("cannot parse correlated Android AudioRecord realization logs")
    if begin.group(1) != request or done.group(1) != request:
        raise RuntimeError("Android AudioRecord request correlation mismatch")
    if done.group(2) != request:
        raise RuntimeError(
            f"Android AudioRecord output mismatch: expected={request!r}, got={done.group(2)!r}"
        )
    rate = int(begin.group(3))
    channels = int(begin.group(4))
    scheduled_frames = int(begin.group(5))
    captured_frames = int(done.group(3))
    captured_bytes = int(done.group(5))
    if captured_frames != scheduled_frames:
        raise RuntimeError(
            f"Android AudioRecord short capture: {captured_frames}/{scheduled_frames} frames"
        )
    expected_bytes = captured_frames * channels * 2
    if captured_bytes != expected_bytes:
        raise RuntimeError(
            f"Android AudioRecord byte mismatch: {captured_bytes}/{expected_bytes} bytes"
        )
    return {
        "request_id": request,
        "source": begin.group(2),
        "sample_rate_hz": rate,
        "channels": channels,
        "scheduled_frames": scheduled_frames,
        "captured_frames": captured_frames,
        "clipped_samples": int(done.group(4)),
        "captured_bytes": captured_bytes,
        "output_name": done.group(2),
    }


def _parse_integer_list(value, label, *, base):
    if value == "unspecified":
        return []
    fields = value.split(",")
    if not fields or any(not field for field in fields):
        raise RuntimeError(f"cannot parse Android route {label}: {value!r}")
    try:
        return [int(field, base) for field in fields]
    except ValueError as error:
        raise RuntimeError(f"cannot parse Android route {label}: {value!r}") from error


def _parse_android_route_description(description):
    match = re.fullmatch(
        r"session=(?P<session>\d+) source=(?P<source>-?\d+) "
        r"rate=(?P<rate>\d+) channels=(?P<channels>\d+) encoding=(?P<encoding>\d+) "
        r"mask=(?P<mask>0x[0-9a-fA-F]+) indexMask=(?P<index_mask>0x[0-9a-fA-F]+) "
        r"bufferFrames=(?P<buffer_frames>\d+) device=\[(?P<device>.*)\]",
        description,
    )
    if match is None:
        raise RuntimeError(f"cannot parse Android capture route: {description!r}")
    device_text = match.group("device")
    device = None
    if device_text != "none":
        device_match = re.fullmatch(
            r"type=(?P<type>-?\d+) id=(?P<id>-?\d+) address=(?P<address>.*?) "
            r"product=(?P<product>.*?) rates=(?P<rates>\S+) counts=(?P<counts>\S+) "
            r"masks=(?P<masks>\S+) indexMasks=(?P<index_masks>\S+)",
            device_text,
        )
        if device_match is None:
            raise RuntimeError(f"cannot parse Android routed device: {device_text!r}")
        device = {
            "type": int(device_match.group("type")),
            "id": int(device_match.group("id")),
            "address": device_match.group("address"),
            "product_name": device_match.group("product"),
            "sample_rates_hz": _parse_integer_list(
                device_match.group("rates"),
                "device sample rates",
                base=10,
            ),
            "channel_counts": _parse_integer_list(
                device_match.group("counts"),
                "device channel counts",
                base=10,
            ),
            "channel_masks": _parse_integer_list(
                device_match.group("masks"),
                "device channel masks",
                base=0,
            ),
            "channel_index_masks": _parse_integer_list(
                device_match.group("index_masks"),
                "device channel-index masks",
                base=0,
            ),
        }
    return {
        "session_id": int(match.group("session")),
        "actual_source_id": int(match.group("source")),
        "sample_rate_hz": int(match.group("rate")),
        "channels": int(match.group("channels")),
        "encoding_id": int(match.group("encoding")),
        "channel_mask": int(match.group("mask"), 0),
        "channel_index_mask": int(match.group("index_mask"), 0),
        "buffer_frames": int(match.group("buffer_frames")),
        "device": device,
    }


def _parse_android_microphone_description(description):
    match = re.fullmatch(
        r"listIndex=(?P<list_index>\d+) id=(?P<id>\S+) "
        r"description=(?P<description>.*?) address=(?P<address>.*?) "
        r"type=(?P<type>-?\d+) location=(?P<location>-?\d+) "
        r"group=(?P<group>-?\d+) groupIndex=(?P<group_index>-?\d+) "
        r"directionality=(?P<directionality>-?\d+) mapping=(?P<mapping>\S+) "
        r"position=(?P<position>\S+) orientation=(?P<orientation>\S+) "
        r"sensitivity=(?P<sensitivity>\S+) spl=(?P<spl>\S+) "
        r"response=\[(?P<response>.*)\]",
        description,
    )
    if match is None:
        raise RuntimeError(f"cannot parse Android active microphone: {description!r}")
    mappings = []
    mapping_text = match.group("mapping")
    if mapping_text != "none":
        seen_channels = set()
        for item in mapping_text.split(","):
            mapping_match = re.fullmatch(r"(\d+):(direct|processed)", item)
            if mapping_match is None:
                raise RuntimeError(f"cannot parse Android microphone mapping: {item!r}")
            channel = int(mapping_match.group(1))
            if channel in seen_channels:
                raise RuntimeError(f"Android microphone repeats logical channel {channel}")
            seen_channels.add(channel)
            mappings.append({"channel": channel, "mode": mapping_match.group(2)})
    mappings.sort(key=lambda item: (item["channel"], item["mode"]))
    return {
        "list_index": int(match.group("list_index")),
        "id": match.group("id"),
        "description": match.group("description"),
        "address": match.group("address"),
        "type": int(match.group("type")),
        "location": int(match.group("location")),
        "group": int(match.group("group")),
        "group_index": int(match.group("group_index")),
        "directionality": int(match.group("directionality")),
        "channel_mapping": mappings,
        "position": match.group("position"),
        "orientation": match.group("orientation"),
        "sensitivity": match.group("sensitivity"),
        "spl": match.group("spl"),
        "frequency_response": match.group("response"),
    }


def _canonical_microphones(microphones, expected_count, phase):
    if len(microphones) != expected_count:
        raise RuntimeError(
            f"Android {phase} microphone detail count {len(microphones)} != {expected_count}"
        )
    indices = [item["list_index"] for item in microphones]
    if sorted(indices) != list(range(expected_count)):
        raise RuntimeError(f"Android {phase} microphone list indices are not contiguous and unique")
    identifiers = [item["id"] for item in microphones]
    if len(set(identifiers)) != len(identifiers):
        raise RuntimeError(f"Android {phase} microphone identifiers are not unique")
    canonical = []
    for microphone in microphones:
        value = dict(microphone)
        value.pop("list_index")
        canonical.append(value)
    return sorted(
        canonical,
        key=lambda item: (item["id"], item["address"], json.dumps(item, sort_keys=True)),
    )


def _expected_android_source_id(value):
    if isinstance(value, bool):
        raise ValueError("Android source must be a supported name or integer ID")
    if isinstance(value, int):
        return value
    source = str(value).strip().lower()
    if source not in ANDROID_AUDIO_SOURCE_CODES:
        raise ValueError(f"unsupported Android capture source: {value!r}")
    return ANDROID_AUDIO_SOURCE_CODES[source]


def validate_android_record_route_log(
    route_log,
    request_id,
    *,
    expected_source=None,
    expected_sample_rate_hz=None,
    expected_channels=None,
    expected_route_signature=None,
    require_distinct_direct_channel_mappings=False,
):
    """Strictly validate one correlated AudioRecord route and microphone realization."""
    request = _checked_request_id(request_id)
    lines = route_log.splitlines()
    begin_prefix = f"rec_pcm begin: request={request} "
    begin_matches = [line.split(begin_prefix, 1)[1] for line in lines if begin_prefix in line]
    if len(begin_matches) != 1:
        raise RuntimeError(
            f"Android request {request} has {len(begin_matches)} begin records; expected one"
        )
    begin = re.fullmatch(
        r"src=(?P<source>\S+) rate=(?P<rate>\d+) ch=(?P<channels>\d+) "
        r"frames=\d+ -> .+",
        begin_matches[0],
    )
    if begin is None:
        raise RuntimeError(f"cannot parse Android request {request} begin record")
    requested_source_name = begin.group("source")
    requested_source_id = _expected_android_source_id(requested_source_name)
    wanted_source_id = (
        requested_source_id
        if expected_source is None
        else _expected_android_source_id(expected_source)
    )
    if requested_source_id != wanted_source_id:
        raise RuntimeError(
            f"Android request {request} source request does not match the expected source"
        )
    begin_rate = int(begin.group("rate"))
    begin_channels = int(begin.group("channels"))
    wanted_rate = begin_rate if expected_sample_rate_hz is None else int(expected_sample_rate_hz)
    wanted_channels = begin_channels if expected_channels is None else int(expected_channels)
    if begin_rate != wanted_rate or begin_channels != wanted_channels:
        raise RuntimeError(
            f"Android request {request} begin format {begin_rate}/{begin_channels} "
            f"!= expected {wanted_rate}/{wanted_channels}"
        )

    route_descriptions = {}
    routes = {}
    counts = {}
    microphones = {}
    for phase in ("start", "end"):
        route_prefix = f"rec_pcm route request={request} phase={phase} "
        route_matches = [line.split(route_prefix, 1)[1] for line in lines if route_prefix in line]
        if len(route_matches) != 1:
            raise RuntimeError(
                f"Android request {request} has {len(route_matches)} {phase} route records; expected one"
            )
        route_descriptions[phase] = route_matches[0]
        routes[phase] = _parse_android_route_description(route_matches[0])

        count_prefix = f"rec_pcm microphones request={request} phase={phase} count="
        count_matches = [line.split(count_prefix, 1)[1] for line in lines if count_prefix in line]
        if len(count_matches) != 1 or not count_matches[0].isdigit():
            raise RuntimeError(
                f"Android request {request} lacks one parseable {phase} microphone count"
            )
        counts[phase] = int(count_matches[0])
        detail_prefix = f"rec_pcm microphone request={request} phase={phase} "
        details = [
            _parse_android_microphone_description(line.split(detail_prefix, 1)[1])
            for line in lines
            if detail_prefix in line
        ]
        microphones[phase] = _canonical_microphones(details, counts[phase], phase)

    if routes["start"] != routes["end"]:
        raise RuntimeError(f"Android request {request} changed capture route during acquisition")
    if counts["start"] != counts["end"] or microphones["start"] != microphones["end"]:
        raise RuntimeError(f"Android request {request} changed active microphones during acquisition")
    if counts["start"] <= 0:
        raise RuntimeError(f"Android request {request} reported no active microphones")

    route = routes["start"]
    if route["actual_source_id"] != wanted_source_id:
        raise RuntimeError(
            f"Android request {request} actual source {route['actual_source_id']} "
            f"!= expected {wanted_source_id}"
        )
    if route["sample_rate_hz"] != wanted_rate or route["channels"] != wanted_channels:
        raise RuntimeError(
            f"Android request {request} actual format "
            f"{route['sample_rate_hz']}/{route['channels']} != expected "
            f"{wanted_rate}/{wanted_channels}"
        )
    if route["encoding_id"] != ANDROID_AUDIO_ENCODING_PCM_16BIT:
        raise RuntimeError(
            f"Android request {request} encoding {route['encoding_id']} is not PCM16"
        )
    if route["device"] is None:
        raise RuntimeError(f"Android request {request} has no routed input device")
    if route["device"]["type"] != ANDROID_AUDIO_DEVICE_TYPE_BUILTIN_MIC:
        raise RuntimeError(f"Android request {request} is not routed to a built-in microphone")
    for microphone in microphones["start"]:
        if microphone["type"] != ANDROID_AUDIO_DEVICE_TYPE_BUILTIN_MIC:
            raise RuntimeError(
                f"Android request {request} active microphone {microphone['id']} is not built-in"
            )
        for mapping in microphone["channel_mapping"]:
            if not 0 <= mapping["channel"] < wanted_channels:
                raise RuntimeError(
                    f"Android request {request} microphone mapping is outside the capture channels"
                )

    if require_distinct_direct_channel_mappings:
        if wanted_channels != 2:
            raise RuntimeError("distinct direct channel mappings require a stereo capture")
        providers = {0: [], 1: []}
        for microphone in microphones["start"]:
            for mapping in microphone["channel_mapping"]:
                if mapping["mode"] == "direct" and mapping["channel"] in providers:
                    providers[mapping["channel"]].append(microphone["id"])
        if any(len(providers[channel]) != 1 for channel in (0, 1)):
            raise RuntimeError(
                f"Android request {request} lacks exactly one direct provider for channels 0 and 1"
            )
        if providers[0][0] == providers[1][0]:
            raise RuntimeError(
                f"Android request {request} maps channels 0 and 1 to the same microphone"
            )

    signature = {
        "schema": "cyrinx.android-capture-route-signature.v1",
        "actual_source_id": route["actual_source_id"],
        "sample_rate_hz": route["sample_rate_hz"],
        "channels": route["channels"],
        "encoding_id": route["encoding_id"],
        "channel_mask": route["channel_mask"],
        "channel_index_mask": route["channel_index_mask"],
        "buffer_frames": route["buffer_frames"],
        "device": route["device"],
        "microphones": microphones["start"],
    }
    if expected_route_signature is not None and signature != expected_route_signature:
        raise RuntimeError(f"Android request {request} route signature does not match expected")
    return {
        "stable": True,
        "route_description": route_descriptions["start"],
        "active_microphone_count": counts["start"],
        "actual_source_id": route["actual_source_id"],
        "sample_rate_hz": route["sample_rate_hz"],
        "channels": route["channels"],
        "encoding_id": route["encoding_id"],
        "device": route["device"],
        "microphones": microphones["start"],
        "route_signature": signature,
    }


def validate_android_record_route_start_log(
    route_log,
    request_id,
    *,
    expected_source,
    expected_sample_rate_hz,
    expected_channels,
    expected_route_signature,
    require_distinct_direct_channel_mappings=False,
):
    """Validate the realized start route before any paired playback begins.

    AudioRecord emits its complete route and active-microphone descriptions
    before the host is allowed to energize a speaker.  The full validator also
    requires matching end records; this preflight mirrors only the correlated
    start records into an in-memory end phase so both paths share exactly the
    same parsing and safety checks.  A later full validation remains mandatory
    to detect route changes during capture.
    """
    request = _checked_request_id(request_id)
    start_marker = f"request={request} phase=start "
    start_lines = [line for line in route_log.splitlines() if start_marker in line]
    if not start_lines:
        raise RuntimeError(f"Android request {request} has no start route records")
    mirrored = [line.replace("phase=start ", "phase=end ", 1) for line in start_lines]
    synthetic = route_log.rstrip("\n") + "\n" + "\n".join(mirrored) + "\n"
    return validate_android_record_route_log(
        synthetic,
        request,
        expected_source=expected_source,
        expected_sample_rate_hz=expected_sample_rate_hz,
        expected_channels=expected_channels,
        expected_route_signature=expected_route_signature,
        require_distinct_direct_channel_mappings=require_distinct_direct_channel_mappings,
    )


def android_record_start(duration_s, channels=2, source="unprocessed", out_name="cap.pcm", sr=SR):
    request_id = _checked_request_id(out_name)
    source_name = str(source).strip().lower()
    _expected_android_source_id(source_name)
    android_prepare()
    _checked_adb(
        f"shell am start -n {ACT} --es cmd rec_pcm --ef duration_sec {duration_s} "
        f"--ei sample_rate_hz {sr} --ei channels {channels} --es source {source_name} "
        f"--es out_name {out_name} --es request_id {request_id}",
        "AudioRecord activity start",
    )
    line = logcat_wait(f"rec_pcm begin: request={request_id} ", 10)
    return line


def _pull_android_app_file(out_name, local_path, label):
    request = _checked_request_id(out_name)
    parent = os.path.dirname(local_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    try:
        with open(local_path, "wb") as stream:
            result = adb(
                ["exec-out", "run-as", PKG, "cat", f"files/{request}"],
                stdout=stream,
                stderr=subprocess.PIPE,
                text=False,
            )
    except BaseException:
        try:
            os.remove(local_path)
        except FileNotFoundError:
            pass
        raise
    if result.returncode != 0:
        try:
            os.remove(local_path)
        except FileNotFoundError:
            pass
        raise RuntimeError(f"{label} failed: {_adb_error_text(result)}")


def android_record_finish(duration_s, out_name="cap.pcm", local_path=None):
    request_id = _checked_request_id(out_name)
    line = logcat_wait(f"rec_pcm done: request={request_id} ", duration_s + 25)
    local_path = local_path or os.path.join(DATA, out_name)
    _pull_android_app_file(out_name, local_path, "AudioRecord pull")
    return line, local_path


def android_record(duration_s, channels=2, source="unprocessed", out_name="cap.pcm", sr=SR):
    android_record_start(duration_s, channels, source, out_name, sr=sr)
    return android_record_finish(duration_s, out_name)


def android_playrec(wave, out_name, source="unprocessed", input_channels=2,
                    pre_roll_ms=500, post_roll_ms=750, sr=SR, local_path=None):
    """Play stereo PCM and capture microphones concurrently on Android.

    This does not change media volume. Callers that need a particular level
    must set and later restore it explicitly. Embedded waveform markers, not
    the ADB launch time, define the offline capture origin.
    """
    samples = np.asarray(wave)
    if samples.ndim == 1:
        samples = samples[:, None]
    if samples.ndim != 2 or samples.shape[1] not in (1, 2):
        raise ValueError("wave must have one or two output channels")
    output_channels = samples.shape[1]
    pcm16 = (np.clip(samples, -1, 1) * 32767).astype("<i2")
    request_id = _checked_request_id(out_name)
    nonce = f"{os.getpid()}-{time.time_ns()}"
    local_tx = f"/tmp/cyrinx-playrec-{nonce}.pcm"
    remote_tx = f"/data/local/tmp/cyrinx-playrec-{nonce}.pcm"
    pcm16.tofile(local_tx)
    _checked_adb(f"push {local_tx} {remote_tx}", "playrec push")
    android_prepare()
    _checked_adb(
        f"shell am start -n {ACT} --es cmd playrec_pcm --es path {remote_tx} "
        f"--es out_name {out_name} --ei sample_rate_hz {sr} "
        f"--ei input_channels {input_channels} --ei output_channels {output_channels} "
        f"--ei pre_roll_ms {pre_roll_ms} --ei post_roll_ms {post_roll_ms} "
        f"--es source {source} --es request_id {request_id}",
        "playrec activity start",
    )
    begin_line = logcat_wait(f"playrec_pcm begin: request={request_id} ", 10)
    duration_s = len(samples) / sr + (pre_roll_ms + post_roll_ms) / 1000
    done_line = logcat_wait(f"playrec_pcm done: request={request_id} ", duration_s + 20)
    local_path = local_path or os.path.join(DATA, out_name)
    _pull_android_app_file(out_name, local_path, "playrec pull")
    log = android_request_log(request_id)
    return begin_line, done_line, local_path, log


def android_play(wave, channels=1, block=True, sr=SR, media_volume=None):
    """Push and play PCM without changing volume unless a level is explicitly supplied."""
    pcm = np.clip(wave, -1, 1)
    pcm16 = (pcm * 32767).astype("<i2")
    tmp = "/tmp/cyrinx_tx.pcm"
    pcm16.tofile(tmp)
    _checked_adb(f"push {tmp} /data/local/tmp/tx.pcm", "playback push")
    android_prepare(media_volume=media_volume)
    request_id = _checked_request_id(f"play-{os.getpid()}-{time.time_ns()}")
    _checked_adb(
        f"shell am start -n {ACT} --es cmd play_pcm --ei sample_rate_hz {sr} "
        f"--ei channels {channels} --ei max_volume 0 --es request_id {request_id}",
        "playback activity start",
    )
    logcat_wait(f"play_pcm begin: request={request_id} ", 10)
    if block:
        n_frames = len(pcm16) // channels if pcm16.ndim == 1 else len(pcm16)
        logcat_wait(f"play_pcm done: request={request_id} ", n_frames / sr + 20)


# ---------------- Mac audio ----------------

def mac_get_output_state():
    """Return the current macOS output volume and mute state."""
    result = sh(
        "osascript -e 'set s to get volume settings' "
        "-e 'return ((output volume of s) as text) & tab & ((output muted of s) as text)'"
    )
    if result.returncode != 0:
        raise RuntimeError(f"failed to query Mac output state: {result.stderr}")
    fields = result.stdout.strip().split("\t")
    if len(fields) != 2 or fields[1].lower() not in ("true", "false"):
        raise RuntimeError(f"unexpected Mac output state: {result.stdout!r}")
    return {"volume_percent": int(fields[0]), "muted": fields[1].lower() == "true"}


def mac_set_output_state(pct, muted):
    """Set macOS output state and fail if the OS rejects the request."""
    volume = int(pct)
    if not 0 <= volume <= 100:
        raise ValueError("Mac output volume must be in [0, 100]")
    mute_literal = "true" if bool(muted) else "false"
    result = sh(
        f"osascript -e 'set volume output volume {volume} output muted {mute_literal}'"
    )
    if result.returncode != 0:
        raise RuntimeError(f"failed to set Mac output state: {result.stderr}")


def mac_set_output_volume(pct=100):
    """Set volume while preserving the current mute state."""
    prior = mac_get_output_state()
    mac_set_output_state(pct, prior["muted"])


@contextlib.contextmanager
def temporary_mac_output_state(pct, muted=False):
    """Apply a reviewed output state and restore it even after failure."""
    prior = mac_get_output_state()
    try:
        mac_set_output_state(pct, muted)
        realized = mac_get_output_state()
        expected = {"volume_percent": int(pct), "muted": bool(muted)}
        if realized != expected:
            raise RuntimeError(
                f"Mac output-state realization mismatch: expected={expected}, got={realized}"
            )
        yield prior
    finally:
        mac_set_output_state(prior["volume_percent"], prior["muted"])
        restored = mac_get_output_state()
        if restored != prior:
            raise RuntimeError(
                f"Mac output-state restoration mismatch: expected={prior}, got={restored}"
            )


def mac_set_input_volume(pct):
    sh(f"osascript -e 'set volume input volume {pct}'")


def _sd(sr=SR, *, input_channels=0, output_channels=0):
    import sounddevice as sd

    devs = sd.query_devices()
    input_matches = [i for i, device in enumerate(devs) if device["name"] == MAC_MIC]
    output_matches = [i for i, device in enumerate(devs) if device["name"] == MAC_SPK]
    input_index = input_matches[0] if len(input_matches) == 1 else None
    output_index = output_matches[0] if len(output_matches) == 1 else None
    if input_channels and len(input_matches) != 1:
        raise RuntimeError(
            f"required input device must be unique: {MAC_MIC} (found {len(input_matches)})"
        )
    if output_channels and len(output_matches) != 1:
        raise RuntimeError(
            f"required output device must be unique: {MAC_SPK} (found {len(output_matches)})"
        )
    if output_channels:
        try:
            default_output_index = int(sd.default.device[1])
        except (IndexError, TypeError, ValueError) as error:
            raise RuntimeError("cannot determine the PortAudio default output") from error
        if output_index != default_output_index:
            raise RuntimeError(
                f"required output {MAC_SPK} is index {output_index}, but default output "
                f"is index {default_output_index}; volume control would target another device"
            )
    if input_channels:
        sd.check_input_settings(
            device=input_index,
            channels=input_channels,
            dtype="float32",
            samplerate=sr,
        )
    if output_channels:
        sd.check_output_settings(
            device=output_index,
            channels=output_channels,
            dtype="float32",
            samplerate=sr,
        )
    return sd, input_index, output_index


def mac_record(duration_s, sr=SR):
    if not np.isfinite(duration_s) or duration_s <= 0:
        raise ValueError("recording duration must be finite and positive")
    sd, input_index, _ = _sd(sr, input_channels=1)
    x = sd.rec(
        int(duration_s * sr),
        samplerate=sr,
        channels=1,
        dtype="float32",
        device=input_index,
    )
    sd.wait()
    return x[:, 0]


def mac_play(wave, block=True, sr=SR):
    samples = np.asarray(wave, dtype=np.float32)
    if samples.ndim not in (1, 2) or len(samples) == 0:
        raise ValueError("playback must be nonempty mono or channel-major PCM")
    channels = 1 if samples.ndim == 1 else samples.shape[1]
    if channels not in (1, 2):
        raise ValueError("playback must have one or two channels")
    if not np.all(np.isfinite(samples)):
        raise ValueError("playback contains non-finite samples")
    peak = float(np.max(np.abs(samples)))
    if peak > 1.0:
        raise ValueError(f"playback peak exceeds digital full scale: {peak}")
    sd, _, output_index = _sd(sr, output_channels=channels)
    try:
        sd.play(samples, samplerate=sr, device=output_index)
        if block:
            status = sd.wait()
            if status:
                raise RuntimeError(f"Mac playback reported a PortAudio status: {status}")
    except BaseException:
        sd.stop()
        raise


def mac_stop():
    """Best-effort stop for every PortAudio stream owned by this process."""
    try:
        import sounddevice as sd

        sd.stop()
    except Exception:
        pass


def mac_play_and_record(wave, extra_s=1.0):
    """Full duplex on the Mac: plays wave, records simultaneously (loopback)."""
    w = np.asarray(wave, dtype=np.float32)
    if w.ndim not in (1, 2) or len(w) == 0 or not np.all(np.isfinite(w)):
        raise ValueError("full-duplex waveform must be finite, nonempty mono or stereo PCM")
    if not np.isfinite(extra_s) or extra_s < 0:
        raise ValueError("extra capture duration must be finite and nonnegative")
    padding_shape = (round(extra_s * SR),) + w.shape[1:]
    padded = np.concatenate((w, np.zeros(padding_shape, dtype=np.float32)))
    output_channels = 1 if w.ndim == 1 else w.shape[1]
    sd, input_index, output_index = _sd(
        SR,
        input_channels=1,
        output_channels=output_channels,
    )
    rec = sd.playrec(
        padded,
        samplerate=SR,
        channels=1,
        dtype="float32",
        blocking=True,
        device=(input_index, output_index),
    )
    return rec[:, 0] if rec is not None else None


# ---------------- Cross-device captures ----------------

def mac_to_android(wave, channels_out=2, rec_channels=2, source="unprocessed",
                   pre_s=0.7, post_s=0.7, out_name="m2a.pcm", sr=SR):
    """Play `wave` from Mac speakers while the phone records. Returns phone capture path."""
    dur = len(wave) / sr + pre_s + post_s + 8.0
    android_record_start(dur, channels=rec_channels, source=source, out_name=out_name, sr=sr)
    time.sleep(pre_s)
    mac_play(wave, block=True, sr=sr)
    line, path = android_record_finish(dur, out_name=out_name)
    return line, path


def android_to_mac(wave, channels=1, pre_s=0.7, post_s=0.7, sr=SR):
    """Play `wave` from phone speaker while the Mac records. Returns float32 mono capture."""
    import threading
    dur = (len(wave) if np.ndim(wave) == 1 else wave.shape[0]) / sr + pre_s + post_s + 2.5
    result = {}

    def rec():
        result["x"] = mac_record(dur, sr=sr)

    t = threading.Thread(target=rec)
    t.start()
    time.sleep(pre_s + 0.3)
    android_play(wave, channels=channels, block=True, sr=sr)
    t.join()
    return result["x"]


# ---------------- Helpers ----------------

def load_pcm16(path, channels=2):
    x = np.fromfile(path, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        x = x[: len(x) // channels * channels].reshape(-1, channels)
    return x


def save_wav(path, x):
    import wave as wavemod
    x = np.asarray(x)
    if x.ndim == 1:
        x = x[:, None]
    w = wavemod.open(path, "wb")
    w.setnchannels(x.shape[1])
    w.setsampwidth(2)
    w.setframerate(SR)
    w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())
    w.close()


def chirp(f0, f1, dur_s, amp=0.5, sr=SR):
    t = np.arange(int(dur_s * sr)) / sr
    ph = 2 * np.pi * (f0 * t + 0.5 * (f1 - f0) * t * t / dur_s)
    w = amp * np.sin(ph)
    # 10 ms raised-cosine ramps to avoid clicks
    r = int(0.010 * sr)
    env = np.ones(len(w))
    env[:r] = 0.5 - 0.5 * np.cos(np.pi * np.arange(r) / r)
    env[-r:] = env[:r][::-1]
    return (w * env).astype(np.float32)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "smoke"
    if cmd == "prepare":
        android_prepare()
        print("android prepared")
    elif cmd == "smoke":
        android_prepare()
        print("1) Android ambient record 2s ...")
        line, path = android_record(2.0, out_name="smoke_ambient.pcm")
        print("   ", line.strip())
        x = load_pcm16(path)
        print(f"    pulled {x.shape}, rms={np.sqrt((x**2).mean()):.6f}")
        print("2) Mac ambient record 2s ...")
        m = mac_record(2.0)
        print(f"    rms={np.sqrt((m**2).mean()):.6f}")
        print("3) Mac plays 1 kHz tone, Android records ...")
        tone = chirp(1000, 1000, 1.0, amp=0.4)
        mac_set_output_volume(100)
        line, path = mac_to_android(tone, out_name="smoke_m2a.pcm")
        x = load_pcm16(path)
        print(f"    capture rms={np.sqrt((x**2).mean()):.6f} peak={np.abs(x).max():.4f}")
        print("4) Android plays 1 kHz tone, Mac records ...")
        m = android_to_mac(tone)
        print(f"    capture rms={np.sqrt((m**2).mean()):.6f} peak={np.abs(m).max():.4f}")
        print("smoke ok")
