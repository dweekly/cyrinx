#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"
# shellcheck source=scripts/swiftlint-version.sh
source "$ROOT_DIR/scripts/swiftlint-version.sh"
check_swiftlint_version

swiftlint lint --strict --config .swiftlint.yml --baseline .swiftlint-baseline.json --no-cache
./scripts/check-swiftlint-baseline.sh
shellcheck scripts/*.sh
