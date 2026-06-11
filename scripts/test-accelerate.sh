#!/usr/bin/env bash
# Validate the Apple vDSP/Accelerate FFT backend of the bulk-PHY codec against
# the same golden vectors as the portable KISS default (docs/PUBLICATION.md 1.6).
# KISS is the CI default (portable, ships to Android via JNI too); this script
# exercises the Accelerate backend, which must produce identical decode results.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"
exec swift test \
  --filter "CyrinxBulkTXTests|CyrinxBulkPHYTests|CyrinxGoldenVectorTests" \
  -Xcc -DCYRINX_FFT_ACCELERATE \
  -Xlinker -framework -Xlinker Accelerate
