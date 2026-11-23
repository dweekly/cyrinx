#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"
# shellcheck source=scripts/swiftlint-version.sh
source "$ROOT_DIR/scripts/swiftlint-version.sh"
check_swiftlint_version

# Regenerate and compare the violation set instead of asking SwiftLint to lint
# directly with the baseline. SwiftLint 0.65.1 still exits nonzero for the
# baselined violations in that mode; the checker accepts the exact debt set and
# rejects any added, removed, or changed violation.
./scripts/check-swiftlint-baseline.sh
shellcheck scripts/*.sh
