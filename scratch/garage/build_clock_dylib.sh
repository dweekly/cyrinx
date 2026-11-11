#!/usr/bin/env bash
# Build the research dylib: the unmodified C codec plus clock_offset.c.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="${1:-$ROOT/scratch/garage/libcyrinxclock.dylib}"
clang -std=c11 -O2 -dynamiclib -Dkiss_fft_scalar=double \
    -I"$ROOT/Sources/CCyrinx/include" -I"$ROOT/Sources/CCyrinx/kissfft" \
    "$ROOT/scratch/garage/clock_offset.c" "$ROOT/Sources/CCyrinx/cyrinx_fft.c" \
    "$ROOT/Sources/CCyrinx/kissfft/kiss_fft.c" "$ROOT/Sources/CCyrinx/kissfft/kiss_fftr.c" \
    -o "$OUT" -lm
echo "built $OUT"
