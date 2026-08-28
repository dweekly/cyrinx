#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"
# shellcheck source=scripts/swift-format-version.sh
source "$ROOT_DIR/scripts/swift-format-version.sh"
# shellcheck source=scripts/format-file-selection.sh
source "$ROOT_DIR/scripts/format-file-selection.sh"
check_swift_format_version

./scripts/check-vendored.sh

swift-format format --in-place --recursive --configuration .swift-format Sources Tests

cyrinx_first_party_c_files | \
  xargs -0 clang-format -i -style=file
