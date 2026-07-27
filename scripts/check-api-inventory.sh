#!/usr/bin/env bash
# Validate the classified Cyrinx 2.x Swift/C public API inventory
# (docs/api-inventory.json) against the live compiler-extracted surface.
#
# Usage:
#   scripts/check-api-inventory.sh [--structural-only] [--write]
#   scripts/check-api-inventory.sh --test [unittest-discover-args...]
#
#   --structural-only  validate JSON schema/classifications only, no compiler
#                      extraction (skips the toolchain-drift check below)
#   --write            refresh docs/api-inventory.json, including its pinned
#                      "toolchain" metadata, from the active local toolchain
#   --test             run scripts/check-api-inventory.py's own unit tests
#                      (Tests/ContractTools) instead of checking the inventory
#
# Exit codes (see scripts/check-api-inventory.py's module docstring for the
# full rationale):
#   0  inventory OK, or --write completed
#   1  inventory is structurally invalid, or does not match the live surface
#      (a real API/ABI change to classify)
#   2  no Python virtual environment found to run the checker in (this
#      wrapper only — create one with: python3 -m venv .venv)
#   3  the active `swift --version` / `clang --version` does not match the
#      "toolchain" identity pinned in docs/api-inventory.json — re-extract
#      and review the diff with the pinned toolchain before trusting a
#      reported API change (mirrors scripts/swift-format-version.sh's
#      pin-and-compare pattern for the same class of false-positive drift)

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONDONTWRITEBYTECODE=1

if [[ -n "${CYRINX_PYTHON:-}" ]]; then
    PYTHON_BIN="$CYRINX_PYTHON"
elif [[ -n "${VIRTUAL_ENV:-}" && -x "$VIRTUAL_ENV/bin/python" ]]; then
    PYTHON_BIN="$VIRTUAL_ENV/bin/python"
elif [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
    PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
else
    GIT_COMMON_DIR="$(git -C "$ROOT_DIR" rev-parse --path-format=absolute --git-common-dir)"
    COMMON_WORKTREE_ROOT="$(dirname "$GIT_COMMON_DIR")"
    if [[ -x "$COMMON_WORKTREE_ROOT/.venv/bin/python" ]]; then
        PYTHON_BIN="$COMMON_WORKTREE_ROOT/.venv/bin/python"
    else
        echo "API inventory check requires a Python virtual environment." >&2
        echo "Create .venv with: python3 -m venv .venv" >&2
        exit 2
    fi
fi

if [[ "${1:-}" == "--test" ]]; then
    shift
    exec "$PYTHON_BIN" -m unittest discover \
        -s "$ROOT_DIR/Tests/ContractTools" \
        -p "test_*.py" \
        "$@"
fi

exec "$PYTHON_BIN" "$ROOT_DIR/scripts/check-api-inventory.py" "$@"
