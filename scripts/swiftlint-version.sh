#!/usr/bin/env bash

readonly CYRINX_SWIFTLINT_VERSION="0.65.1"

check_swiftlint_version() {
    if ! command -v swiftlint >/dev/null 2>&1; then
        echo "error: swiftlint not found on PATH" >&2
        echo "  required version: $CYRINX_SWIFTLINT_VERSION" >&2
        exit 1
    fi

    local installed
    installed="$(swiftlint version 2>&1 | tr -d '[:space:]')"
    if [[ "$installed" != "$CYRINX_SWIFTLINT_VERSION" ]]; then
        echo "error: swiftlint version mismatch" >&2
        echo "  found:  $installed" >&2
        echo "  pinned: $CYRINX_SWIFTLINT_VERSION" >&2
        echo "The checked-in baseline is valid only for the pinned rule engine." >&2
        exit 1
    fi
}
