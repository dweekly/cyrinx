#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

./scripts/format-check.sh
./scripts/lint.sh
./scripts/check-api-inventory.sh --test
./scripts/check-api-inventory.sh
swift test
