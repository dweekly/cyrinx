#!/usr/bin/env bash
# Run the research test suites under scratch/ against decoder libraries built
# from this checkout.
#
# ./scripts/check.sh does not run these. They build the C codec in their own
# ways: scratch/hw20k from a fixed list of source files, scratch/garage beside
# a research decoder. A change to Sources/CCyrinx or to the Python reference
# modem can therefore break them while every package test passes, as a new
# header did on 2026-11-22. Run this after such a change.
#
# Both libraries are rebuilt first, so the tests never run against a stale
# build, and a skipped test fails the run: most skips here mean a library was
# not found, which is the failure this script exists to catch.
#
# macOS only: the research libraries are built as dylibs. Needs numpy, scipy
# and pytest in the project virtual environment.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

# A linked worktree has no virtual environment of its own; use the main checkout's.
PYTHON="${CYRINX_PYTHON:-$ROOT_DIR/.venv/bin/python}"
if [ ! -x "$PYTHON" ]; then
    COMMON_DIR="$(git rev-parse --path-format=absolute --git-common-dir)"
    PYTHON="$(dirname "$COMMON_DIR")/.venv/bin/python"
fi
if [ ! -x "$PYTHON" ]; then
    echo "no Python at $PYTHON; create .venv or set CYRINX_PYTHON" >&2
    exit 2
fi

CORE="Sources/CCyrinx"
clang -std=c11 -O2 -dynamiclib -Dkiss_fft_scalar=double \
    -I"$CORE/include" -I"$CORE/kissfft" \
    "$CORE/cyrinx_bulk.c" "$CORE/cyrinx_fft.c" "$CORE/kissfft/kiss_fft.c" "$CORE/kissfft/kiss_fftr.c" \
    -o scratch/hw20k/libcyrinxbulk.dylib -lm
RESEARCH_LIBRARY="$ROOT_DIR/scratch/garage/libcyrinxclock.dylib"
scratch/garage/build_clock_dylib.sh "$RESEARCH_LIBRARY" >/dev/null

# Run one suite; print pytest's output; fail on a failure or on any skip.
run_suite() {
    local directory="$1" log status=0
    log="$(mktemp)"
    (cd "$directory" && "$PYTHON" -m pytest -q -rs) >"$log" 2>&1 || status=$?
    cat "$log"
    if [ "$status" -ne 0 ]; then
        rm -f "$log"
        echo "$directory: tests failed" >&2
        return 1
    fi
    if grep -Eq '[0-9]+ skipped' "$log"; then
        rm -f "$log"
        echo "$directory: tests were skipped; a skipped test has not passed" >&2
        return 1
    fi
    rm -f "$log"
}

run_suite scratch/hw20k
# The garage tests load the research decoder as the codec, so its pilot-fit switch is reachable.
CYRINX_BULK_CODEC_PATH="$RESEARCH_LIBRARY" CYRINX_CLOCK_CODEC_PATH="$RESEARCH_LIBRARY" run_suite scratch/garage
echo "Research suites passed with no test skipped."
