#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

PROFILE="${1:-quiet}"
PACKETS="${PACKETS:-120}"
PAYLOAD="${PAYLOAD:-128}"
INTERVAL_MS="${INTERVAL_MS:-20}"
SEED="${SEED:-3331495302}"
OUT="artifacts/bench/sim-${PROFILE}.json"

swift run cyrinx-sim-bench \
  --profile "$PROFILE" \
  --packets "$PACKETS" \
  --payload "$PAYLOAD" \
  --interval-ms "$INTERVAL_MS" \
  --seed "$SEED" \
  --out "$OUT"
