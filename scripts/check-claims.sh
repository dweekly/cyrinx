#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

readonly ALLOWLIST="scripts/claims-allowlist.tsv"

RULE_IDS=(
    "stable-c-abi"
    "misclassified-aead"
    "blanket-crypto-enable"
    "legacy-fully-working"
    "legacy-flawless-session"
    "legacy-complete-kotlin-ofdm"
    "unqualified-stable-abi"
    "crypto-mathematically-secure"
    "crypto-absolute-timing-immunity"
    "crypto-sha256-ctr-label"
    "crypto-complete-validation"
    "build-proves-success"
    "wrong-nonce-size"
)

RULE_PATTERNS=(
    "stable C ABI"
    "CTR/AEAD"
    "just turn it on"
    "100% working"
    "flawless bidirectional"
    "complete Kotlin OFDM"
    "stable ABI"
    "mathematically secure"
    "absolute timing-attack immunity"
    "SHA-256-CTR"
    "scientifically validated the complete"
    "100% SUCCESS"
    "12-byte nonce"
)

matches_literal() {
    local line="$1"
    local pattern="$2"
    printf '%s\n' "$line" | grep -Fqi -- "$pattern"
}

self_test() {
    local index
    for index in "${!RULE_IDS[@]}"; do
        if ! matches_literal "fixture: ${RULE_PATTERNS[$index]} :fixture" "${RULE_PATTERNS[$index]}"; then
            echo "claim self-test failed: ${RULE_IDS[$index]} was not detected" >&2
            return 1
        fi
    done

    if matches_literal "portable C API with a versioned receiver contract" "${RULE_PATTERNS[0]}"; then
        echo "claim self-test failed: neutral ABI wording was rejected" >&2
        return 1
    fi

    echo "Claim-check self-test passed (${#RULE_IDS[@]} negative fixtures)."
}

if [[ "${1:-}" == "--self-test" ]]; then
    self_test
    exit 0
fi

self_test >/dev/null

if [[ ! -f "$ALLOWLIST" ]]; then
    echo "missing claim allowlist: $ALLOWLIST" >&2
    exit 1
fi

CLAIM_FILES=()
while IFS= read -r path; do
    CLAIM_FILES+=("$path")
done < <(rg --files -g '*.md' -g '*.html')

if [[ ${#CLAIM_FILES[@]} -eq 0 ]]; then
    echo "claim check found no Markdown or HTML inputs" >&2
    exit 1
fi

is_allowed() {
    local rule_id="$1"
    local finding_path="$2"
    local finding_text="$3"
    local allowed_id
    local allowed_path
    local line_regex
    local rationale

    while IFS=$'\t' read -r allowed_id allowed_path line_regex rationale; do
        [[ -z "$allowed_id" || "$allowed_id" == \#* ]] && continue
        if [[ -z "$allowed_path" || -z "$line_regex" || -z "$rationale" ]]; then
            echo "invalid allowlist row for rule '$allowed_id'" >&2
            return 2
        fi
        if [[ "$allowed_id" == "$rule_id" && "$allowed_path" == "$finding_path" &&
            "$finding_text" =~ $line_regex ]]; then
            return 0
        fi
    done < "$ALLOWLIST"
    return 1
}

failed=0
for index in "${!RULE_IDS[@]}"; do
    rule_id="${RULE_IDS[$index]}"
    pattern="${RULE_PATTERNS[$index]}"
    while IFS= read -r finding; do
        [[ -z "$finding" ]] && continue
        path="${finding%%:*}"
        remainder="${finding#*:}"
        line_number="${remainder%%:*}"
        line_text="${remainder#*:}"
        if ! is_allowed "$rule_id" "$path" "$line_text"; then
            echo "$path:$line_number: prohibited claim [$rule_id]: $line_text" >&2
            failed=1
        fi
    done < <(rg -n -F -i -- "$pattern" "${CLAIM_FILES[@]}" || true)
done

require_literal() {
    local path="$1"
    local literal="$2"
    if ! rg -F -q -- "$literal" "$path"; then
        echo "$path: missing required claim boundary: $literal" >&2
        failed=1
    fi
}

require_literal "README.md" "### Evidence availability and claim boundary"
require_literal "docs/releases/v2.0.0.md" "Evidence class: **integrity record, not public replay**"
require_literal "site/index.html" "Evidence class: historical aggregate record, not public replay."
require_literal "SECURITY.md" "is not a standard AEAD"
require_literal "ROADMAP.md" "BENCH-SPIKES STOPPED; no integration"
require_literal "docs/CYRINX_3_PLAN.md" "BRANCH-ONLY CANDIDATE"

if [[ "$failed" -ne 0 ]]; then
    exit 1
fi

echo "Claim checks passed (${#RULE_IDS[@]} prohibited patterns, 6 required boundaries)."
