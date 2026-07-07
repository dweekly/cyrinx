#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"
# shellcheck source=scripts/swift-format-version.sh
source "$ROOT_DIR/scripts/swift-format-version.sh"
check_swift_format_version

swift-format format --in-place --recursive --configuration .swift-format Sources Tests

find Sources -type f \( -name '*.c' -o -name '*.h' \) -print0 | \
  xargs -0 clang-format -i -style=file
