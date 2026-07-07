#!/usr/bin/env bash
# Shared version pin for swift-format, sourced by scripts/format.sh and
# scripts/format-check.sh.
#
# Why pin: issue #25 — the format gate silently drifted to ~900 violations
# because the version used to write the code diverged from the version
# available locally. `swift-format`'s rule set has changed across releases
# (Homebrew `swift-format` 602.0.0 vs. the Xcode-bundled toolchain binary,
# which self-reports as "6.3.0" for the same Xcode 26 / Swift 6.3.2 release —
# `xcrun --find swift-format` resolves a *different* binary than the one on
# PATH via Homebrew). Pinning to one canonical version keeps `swift-format
# lint` reproducible across contributors' machines and (eventually) CI.
#
# Verified clean at 0 violations against .swift-format on 2026-07-06 with:
#   $ brew info swift-format   # 602.0.0 installed, 603.0.0 available upstream
#   $ swift-format --version   # 602.0.0
readonly CYRINX_SWIFT_FORMAT_VERSION="602.0.0"

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
