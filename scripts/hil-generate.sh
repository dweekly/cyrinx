#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
HIL_DIR="$ROOT_DIR/Apps/HIL"

cd "$HIL_DIR"
xcodegen generate --spec project.yml

echo "Generated $HIL_DIR/CyrinxHIL.xcodeproj"

if [[ "${1:-}" == "--open" ]]; then
  open "$HIL_DIR/CyrinxHIL.xcodeproj"
fi
