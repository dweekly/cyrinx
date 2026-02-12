#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

swift-format format --in-place --recursive --configuration .swift-format Sources Tests

find Sources -type f \( -name '*.c' -o -name '*.h' \) -print0 | \
  xargs -0 clang-format -i -style=file
