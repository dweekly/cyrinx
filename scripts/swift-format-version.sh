#!/usr/bin/env bash
# Shared version pin for swift-format, sourced by scripts/format.sh and
# scripts/format-check.sh.
#
# Why keep an exact tool version: issue #25 — the format gate silently drifted
# to ~900 violations because the version used to write the code diverged from
# the version available locally. `swift-format`'s rule set can change across
# releases (Homebrew `swift-format` 603.0.0 vs. the Xcode-bundled toolchain binary,
# which can self-report a different version for the same Xcode toolchain —
# `xcrun --find swift-format` resolves a *different* binary than the one on
# PATH via Homebrew). Pinning to one canonical version keeps `swift-format
# lint` reproducible across contributors' machines and (eventually) CI.
#
# The pin is deliberately advanced after formatting the tree and running the
# strict Sources/Tests gate; it is not a compatibility dependency on an older
# formatter.
#
# Verified clean at 0 violations against .swift-format on 2026-07-17 with:
#   $ brew info swift-format   # 603.0.0 installed
#   $ swift-format --version   # 603.0.0
readonly CYRINX_SWIFT_FORMAT_VERSION="603.0.0"

check_swift_format_version() {
  if ! command -v swift-format >/dev/null 2>&1; then
    echo "error: swift-format not found on PATH." >&2
    echo "  Install the pinned version: brew install swift-format" >&2
    echo "  (this repo is pinned to swift-format ${CYRINX_SWIFT_FORMAT_VERSION})" >&2
    exit 1
  fi

  local installed
  installed="$(swift-format --version 2>&1 | tr -d '[:space:]')"

  if [[ "$installed" != "$CYRINX_SWIFT_FORMAT_VERSION" ]]; then
    echo "error: swift-format version mismatch — refusing to run the gate." >&2
    echo "  found:  ${installed}" >&2
    echo "  pinned: ${CYRINX_SWIFT_FORMAT_VERSION}" >&2
    echo "  This is exactly the drift that caused issue #25 (~900 stale" >&2
    echo "  violations). Install the pinned version and re-run." >&2
    echo "  (Also check 'which swift-format' — Homebrew and the Xcode toolchain" >&2
    echo "  each ship their own swift-format binary and can silently disagree.)" >&2
    echo "  Set CYRINX_SKIP_FORMAT_VERSION_CHECK=1 to bypass at your own risk." >&2
    if [[ "${CYRINX_SKIP_FORMAT_VERSION_CHECK:-}" != "1" ]]; then
      exit 1
    fi
  fi
}
