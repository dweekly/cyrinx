#!/usr/bin/env bash

# Shared first-party C file selection for format.sh and format-check.sh.
# KISS FFT is an exact vendored snapshot checked by check-vendored.sh; it is
# deliberately not a repository-formatter-owned source tree.

readonly CYRINX_VENDORED_C_ROOT="Sources/CCyrinx/kissfft"

cyrinx_first_party_c_files() {
    find Sources \
        -path "$CYRINX_VENDORED_C_ROOT" -prune -o \
        -type f \( -name '*.c' -o -name '*.h' \) -print0
}
