#!/usr/bin/env bash
# Run the whole Swift and C test suite under AddressSanitizer and ThreadSanitizer.
#
# The C core is compiled into the Swift package, so `swift test --sanitize` instruments both.
# A sanitizer report fails the run: AddressSanitizer aborts the process, ThreadSanitizer is told
# to, and the log is searched for either banner in case one was printed without stopping.
#
# Before the suites run, two tiny programs with a known heap overflow and a known data race are
# compiled and run under the same sanitizers. Each must be caught. That proves the toolchain's
# sanitizers are working here, so a clean result below means something.
#
# Each sanitizer builds into its own directory under .build, leaving the normal build alone.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

work="$(mktemp -d "${TMPDIR:-/tmp}/cyrinx-sanitizers.XXXXXX")"
trap 'rm -rf "$work"' EXIT

cat >"$work/overflow.c" <<'EOF'
#include <stdlib.h>
int main(void) {
    volatile char *bytes = malloc(8);
    bytes[8] = 1; /* one past the end */
    free((void *)bytes);
    return 0;
}
EOF

cat >"$work/race.c" <<'EOF'
#include <pthread.h>
static int shared;
static void *bump(void *unused) {
    (void)unused;
    for (int i = 0; i < 100000; i++)
        shared++; /* written by two threads with no lock */
    return NULL;
}
int main(void) {
    pthread_t first, second;
    pthread_create(&first, NULL, bump, NULL);
    pthread_create(&second, NULL, bump, NULL);
    pthread_join(first, NULL);
    pthread_join(second, NULL);
    return 0;
}
EOF

# must_catch <sanitizer> <source> <banner>
must_catch() {
    local sanitizer="$1" source="$2" banner="$3"
    clang -std=c11 -g -O1 "-fsanitize=$sanitizer" "$source" -o "$work/control-$sanitizer"
    # The shell prints an "Abort trap" notice here: that is the sanitizer stopping the program.
    if TSAN_OPTIONS="halt_on_error=1" "$work/control-$sanitizer" >"$work/control-$sanitizer.log" 2>&1; then
        echo "self-check failed: the $sanitizer sanitizer let a known defect run to completion" >&2
        exit 1
    fi
    if ! grep -q "$banner" "$work/control-$sanitizer.log"; then
        echo "self-check failed: the $sanitizer sanitizer stopped without its report" >&2
        exit 1
    fi
    echo "self-check: the $sanitizer sanitizer caught a known defect"
}

must_catch address "$work/overflow.c" "AddressSanitizer"
must_catch thread "$work/race.c" "ThreadSanitizer"

for sanitizer in address thread; do
    log="$work/$sanitizer.log"
    echo "== swift test --sanitize=$sanitizer"
    if ! TSAN_OPTIONS="halt_on_error=1" swift test "--sanitize=$sanitizer" \
        --build-path ".build/sanitize-$sanitizer" >"$log" 2>&1; then
        # The whole log: the failing assertion can be far above the last lines, and the log is
        # deleted when this script exits.
        cat "$log" >&2
        echo "the test suite failed under the $sanitizer sanitizer" >&2
        exit 1
    fi
    if grep -E "AddressSanitizer|ThreadSanitizer" "$log" >&2; then
        echo "the $sanitizer sanitizer reported a defect" >&2
        exit 1
    fi
    grep -E "Executed [0-9]+ tests|Test run with" "$log" | tail -2
done
echo "Both sanitizers ran the whole suite with no report."
