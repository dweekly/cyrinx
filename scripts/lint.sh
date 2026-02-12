#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

swiftlint lint --strict --config .swiftlint.yml
shellcheck scripts/*.sh
