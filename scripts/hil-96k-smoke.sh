#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
HIL_DIR="$ROOT_DIR/Apps/HIL"
PROJECT_PATH="$HIL_DIR/CyrinxHIL.xcodeproj"

"$ROOT_DIR/scripts/probe-macos-audio-rates.sh" "$ROOT_DIR/artifacts/bench/audio-rates-mac.json"
"$ROOT_DIR/scripts/hil-generate.sh"

xcodebuild \
  -project "$PROJECT_PATH" \
  -scheme CyrinxHILMac \
  -destination "platform=macOS" \
  -configuration Debug \
  CODE_SIGNING_ALLOWED=NO \
  -quiet \
  build

xcodebuild \
  -project "$PROJECT_PATH" \
  -scheme CyrinxHILiOS \
  -destination "generic/platform=iOS" \
  -configuration Debug \
  CODE_SIGNING_ALLOWED=NO \
  -quiet \
  build

cat <<'EOF'
Built HIL apps for macOS and iOS.

Manual 96 kHz validation steps:
1. Open Apps/HIL/CyrinxHIL.xcodeproj in Xcode.
2. Run CyrinxHILMac on your MacBook Pro.
3. Run CyrinxHILiOS on your physical iPhone.
4. On both apps choose:
   - Role: opposite sides (Master/Slave)
   - Sample Rate: 96 kHz
5. Press Start on both sides, then Probe Local Audio.
6. Confirm diagnostics:
   - configuredHz=96000
   - inHz / outHz show the observed negotiated route rates.

Mac capability JSON:
- artifacts/bench/audio-rates-mac.json
EOF

if [[ "${1:-}" == "--open" ]]; then
  open "$PROJECT_PATH"
fi
