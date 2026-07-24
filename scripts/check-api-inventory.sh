#!/usr/bin/env bash
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
