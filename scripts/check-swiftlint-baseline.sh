#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"
# shellcheck source=scripts/swiftlint-version.sh
source "$ROOT_DIR/scripts/swiftlint-version.sh"
check_swiftlint_version

readonly BASELINE=".swiftlint-baseline.json"
readonly FIXTURE="scripts/fixtures/swiftlint/new-violation.swift.txt"

if [[ ! -f "$BASELINE" ]]; then
    echo "missing SwiftLint baseline: $BASELINE" >&2
    exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
    echo "jq is required to canonicalize the SwiftLint baseline" >&2
    exit 1
fi

TEMP_ROOT="${TMPDIR:-/tmp}"
TEMP_DIR="$(mktemp -d "$TEMP_ROOT/cyrinx-swiftlint-baseline.XXXXXX")"
trap 'rm -rf "$TEMP_DIR"' EXIT

set +e
swiftlint lint --strict --config .swiftlint.yml \
    --write-baseline "$TEMP_DIR/generated.json" --no-cache \
    >"$TEMP_DIR/generation.log" 2>&1
generation_status=$?
set -e

if [[ "$generation_status" -ne 0 && "$generation_status" -ne 2 ]]; then
    echo "SwiftLint baseline generation failed with status $generation_status:" >&2
    sed -n '1,120p' "$TEMP_DIR/generation.log" >&2
    exit 1
fi

if [[ ! -s "$TEMP_DIR/generated.json" ]]; then
    echo "SwiftLint baseline generation produced no JSON" >&2
    exit 1
fi

readonly SORT_FILTER='sort_by([.violation.location.file, .violation.location.line, .violation.location.character, .violation.ruleIdentifier])'
jq -cS "$SORT_FILTER" "$BASELINE" >"$TEMP_DIR/expected.json"
jq -cS "$SORT_FILTER" "$TEMP_DIR/generated.json" >"$TEMP_DIR/actual.json"

if ! cmp -s "$TEMP_DIR/expected.json" "$TEMP_DIR/actual.json"; then
    echo "SwiftLint baseline differs from the current violation set:" >&2
    diff -u "$TEMP_DIR/expected.json" "$TEMP_DIR/actual.json" >&2 || true
    echo "Update the baseline only in a dedicated lint-debt change." >&2
    exit 1
fi

cp "$FIXTURE" "$TEMP_DIR/NewViolation.swift"
if swiftlint lint --strict --config .swiftlint.yml --baseline "$BASELINE" \
    --no-cache "$TEMP_DIR/NewViolation.swift" >"$TEMP_DIR/negative.log" 2>&1; then
    echo "SwiftLint baseline self-test failed: a seeded new violation passed" >&2
    exit 1
fi

if ! rg -q "Line Length Violation" "$TEMP_DIR/negative.log"; then
    echo "SwiftLint baseline self-test failed for an unexpected reason:" >&2
    sed -n '1,120p' "$TEMP_DIR/negative.log" >&2
    exit 1
fi

baseline_violation_count="$(jq 'length' "$TEMP_DIR/expected.json")"
echo "SwiftLint baseline matches $baseline_violation_count existing violations; seeded new violation rejected."
