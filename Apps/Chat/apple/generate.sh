#!/usr/bin/env bash
set -euo pipefail

# Mirrors scripts/hil-generate.sh's pattern (Apps/HIL's XcodeGen generator),
# per the C3-29 design brief's "match Apps/HIL project conventions for
# xcodegen/gradle setup."

CHAT_APPLE_DIR="$(cd "$(dirname "$0")" && pwd)"

cd "$CHAT_APPLE_DIR"
xcodegen generate --spec project.yml

echo "Generated $CHAT_APPLE_DIR/CyrinxChat.xcodeproj"

if [[ "${1:-}" == "--open" ]]; then
  open "$CHAT_APPLE_DIR/CyrinxChat.xcodeproj"
fi
