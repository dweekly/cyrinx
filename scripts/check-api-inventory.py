#!/usr/bin/env python3
"""Validate the classified Cyrinx 2.x Swift and C public API inventory.

The live surface is extracted with the Swift and Clang symbol-graph tools. The
checked-in inventory is deliberately keyed by language, declaration kind, and
public path instead of compiler USRs: Clang macro USRs contain byte offsets and
would otherwise change when comments move.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
INVENTORY_PATH = ROOT / "docs" / "api-inventory.json"
ALLOWED_DISPOSITIONS = {"retain", "deprecate", "experimental", "replace"}
ALLOWED_PLATFORMS = {"all", "linux", "macosx"}
ALLOWED_LANGUAGES = {"c", "swift"}
ALLOWED_KINDS = {
    "c": {
        "c.enum",
        "c.enum.case",
        "c.func",
        "c.macro",
        "c.property",
        "c.struct",
        "c.tag.enum",
        "c.tag.struct",
        "c.typealias",
    },
    "swift": {
        "swift.class",
        "swift.enum",
        "swift.enum.case",
        "swift.func",
        "swift.init",
        "swift.method",
        "swift.property",
        "swift.struct",
        "swift.type.method",
        "swift.type.property",
    },
}
EXPECTED_SCHEMA_VERSION = 2
REVIEWED = "reviewed"
PENDING_REVIEW_STATES = {
    "new_unclassified",
    "changed_unreviewed",
    "removal_unreviewed",
}
FINGERPRINT_FIELDS = (
    "language",
    "kind",
    "path",
    "declaration",
    "availability",
    "conformances",
    "enumValue",
    "ordinal",
    "attributes",
)
TECHNICAL_FIELDS = (
    "language",
    "kind",
    "path",
    "declaration",
    "source",
    "origin",
    "availability",
    "conformances",
    "enumValue",
    "ordinal",
    "attributes",
)
PLACEHOLDER_MARKERS = ("UNCLASSIFIED", "Classify this symbol")
FROZEN_API_TARGETS = {
    "api:CyrinxConnection",
    "api:CyrinxError",
    "api:CyrinxTransfer",
    "api:CyrinxTransport",
    "api:CyrinxTransport.Configuration",
    "api:CyrinxTransport.Event",
    "api:LinkEstimate",
    "api:SecurityStatus",
    "api:SendOptions",
    "api:SendOptions.DeliveryMode",
}
FROZEN_MODULE_TARGETS = {
    "module:CCyrinxDSP",
    "module:CyrinxExperimental",
    "module:CyrinxSimulation",
}


class InventoryError(RuntimeError):
    """A deterministic inventory or extraction failure."""


def stable_identifier(language: str, kind: str, path: str) -> str:
    """Return a logical identity that survives declaration changes."""

    module = "Cyrinx" if language == "swift" else "CCyrinx"
    return f"{language}:{module}:{kind}:{path}"


def symbol_fingerprint(symbol: dict[str, Any]) -> str:
    """Fingerprint source-contract facts separately from stable identity."""

    facts = {
        field: symbol[field]
        for field in FINGERPRINT_FIELDS
        if field in symbol
    }
    canonical = json.dumps(
        facts,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def replacement_target_is_defined(target: str) -> bool:
    if target in FROZEN_API_TARGETS or target in FROZEN_MODULE_TARGETS:
        return True
    match = re.fullmatch(r"plan:(C3-\d+[a-z]?)", target)
    if match is None:
        return False
    plan_path = ROOT / "docs" / "CYRINX_3_PLAN.md"
    if not plan_path.is_file():
        return False
    heading = re.compile(rf"^### {re.escape(match.group(1))}\b", re.MULTILINE)
    return heading.search(plan_path.read_text(encoding="utf-8")) is not None


def run(
    arguments: list[str],
    *,
    environment: dict[str, str] | None = None,
    capture: bool = False,
) -> str:
    completed = subprocess.run(
        arguments,
        cwd=ROOT,
        env=environment,
        check=False,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )
    if completed.returncode != 0:
        detail = ""
        if capture:
            detail = f"\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        raise InventoryError(
            f"command failed ({completed.returncode}): {' '.join(arguments)}{detail}"
        )
    return completed.stdout.strip() if capture else ""


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise InventoryError(f"missing inventory: {path.relative_to(ROOT)}") from error
    except json.JSONDecodeError as error:
        raise InventoryError(
            f"invalid JSON in {path.relative_to(ROOT)}: {error}"
        ) from error
    if not isinstance(value, dict):
        raise InventoryError(f"{path.relative_to(ROOT)} must contain a JSON object")
    return value


def canonical_availability(value: Any) -> list[dict[str, Any]]:
    """Normalize a Swift or Clang symbol-graph availability mixin."""

    if value is None:
        return []
    if not isinstance(value, list) or not all(
        isinstance(item, dict) for item in value
    ):
        raise InventoryError("availability must be an array of objects")
    normalized_by_encoding: dict[str, dict[str, Any]] = {}
    for item in value:
        normalized = {key: item[key] for key in sorted(item)}
        encoding = json.dumps(
            normalized,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        normalized_by_encoding[encoding] = normalized
    return [
        normalized_by_encoding[encoding]
        for encoding in sorted(normalized_by_encoding)
    ]


def validate_manifest(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    errors: list[str] = []
    if manifest.get("schemaVersion") != EXPECTED_SCHEMA_VERSION:
        errors.append(
            f"schemaVersion must be {EXPECTED_SCHEMA_VERSION}, "
            f"found {manifest.get('schemaVersion')!r}"
        )

    surface = manifest.get("surface")
    required_surface_strings = (
        "swiftModule",
        "cHeaders",
        "swiftExtraction",
        "cExtraction",
        "platformPolicy",
        "valueSemanticsBoundary",
    )
    if not isinstance(surface, dict):
        errors.append("surface must be an object")
    else:
        for key in required_surface_strings:
            value = surface.get(key)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"surface.{key} must be a non-empty string")

    replacement_targets = manifest.get("replacementTargets")
    if (
        not isinstance(replacement_targets, list)
        or not replacement_targets
        or not all(isinstance(item, str) and item for item in replacement_targets)
        or replacement_targets != sorted(set(replacement_targets))
    ):
        errors.append(
            "replacementTargets must be a sorted array of unique non-empty strings"
        )
        replacement_target_set: set[str] = set()
    else:
        replacement_target_set = set(replacement_targets)
        for target in replacement_targets:
            if not replacement_target_is_defined(target):
                errors.append(
                    f"replacementTargets contains no frozen API/module/plan target: "
                    f"{target!r}"
                )

    symbols = manifest.get("symbols")
    if not isinstance(symbols, list):
        raise InventoryError("symbols must be a JSON array")

    seen: set[str] = set()
    seen_semantic: set[tuple[Any, Any, Any]] = set()
    required_strings = (
        "id",
        "fingerprint",
        "language",
        "kind",
        "path",
        "declaration",
        "source",
        "origin",
        "disposition",
        "decision",
        "compatibility",
        "reviewState",
    )
    for index, raw_symbol in enumerate(symbols):
        label = f"symbols[{index}]"
        if not isinstance(raw_symbol, dict):
            errors.append(f"{label} must be an object")
            continue
        for key in required_strings:
            value = raw_symbol.get(key)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{label}.{key} must be a non-empty string")

        identifier = raw_symbol.get("id")
        if isinstance(identifier, str):
            if identifier in seen:
                errors.append(f"duplicate symbol id: {identifier}")
            seen.add(identifier)

        language = raw_symbol.get("language")
        kind = raw_symbol.get("kind")
        path = raw_symbol.get("path")
        expected_identifier = stable_identifier(
            str(language),
            str(kind),
            str(path),
        )
        if identifier != expected_identifier:
            errors.append(
                f"{label}.id must be {expected_identifier!r}, found {identifier!r}"
            )
        semantic_key = (language, kind, path)
        if semantic_key in seen_semantic:
            errors.append(f"duplicate semantic symbol identity: {semantic_key!r}")
        seen_semantic.add(semantic_key)

        if language not in ALLOWED_LANGUAGES:
            errors.append(
                f"{label}.language must be one of {sorted(ALLOWED_LANGUAGES)}, "
                f"found {language!r}"
            )
        elif kind not in ALLOWED_KINDS[language]:
            errors.append(
                f"{label}.kind is not valid for {language!r}: {kind!r}"
            )

        expected_fingerprint = symbol_fingerprint(raw_symbol)
        if raw_symbol.get("fingerprint") != expected_fingerprint:
            errors.append(
                f"{label}.fingerprint must be {expected_fingerprint!r}, "
                f"found {raw_symbol.get('fingerprint')!r}"
            )

        present = raw_symbol.get("present")
        if not isinstance(present, bool):
            errors.append(f"{label}.present must be a boolean")
        review_state = raw_symbol.get("reviewState")
        if review_state != REVIEWED:
            allowed = sorted({REVIEWED, *PENDING_REVIEW_STATES})
            if review_state not in allowed:
                errors.append(
                    f"{label}.reviewState must be one of {allowed}, "
                    f"found {review_state!r}"
                )
            else:
                errors.append(
                    f"{label}.reviewState is pending review: {review_state!r}"
                )
        if present is False:
            removal_target = raw_symbol.get("removalTarget")
            if (
                not isinstance(removal_target, str)
                or removal_target not in replacement_target_set
            ):
                errors.append(
                    f"{label}.removalTarget must name a declared replacement "
                    "or plan target for an absent symbol"
                )

        disposition = raw_symbol.get("disposition")
        if disposition not in ALLOWED_DISPOSITIONS:
            errors.append(
                f"{label}.disposition must be one of "
                f"{sorted(ALLOWED_DISPOSITIONS)}, found {disposition!r}"
            )
        if disposition != "retain":
            replacement = raw_symbol.get("replacement")
            if not isinstance(replacement, str) or not replacement.strip():
                errors.append(
                    f"{label}.replacement must name a symbol or plan target "
                    f"for {disposition!r}"
                )
            elif replacement not in replacement_target_set:
                errors.append(
                    f"{label}.replacement is not a declared replacement target: "
                    f"{replacement!r}"
                )
        for key in ("decision", "compatibility"):
            value = raw_symbol.get(key)
            if isinstance(value, str) and any(
                marker in value for marker in PLACEHOLDER_MARKERS
            ):
                errors.append(f"{label}.{key} contains an unreviewed placeholder")

        conformances = raw_symbol.get("conformances")
        if (
            not isinstance(conformances, list)
            or not all(isinstance(item, str) and item for item in conformances)
            or conformances != sorted(set(conformances))
        ):
            errors.append(
                f"{label}.conformances must be a sorted array of unique strings"
            )
        if raw_symbol.get("origin") not in {"source", "synthesized"}:
            errors.append(f"{label}.origin must be 'source' or 'synthesized'")
        if raw_symbol.get("origin") == "synthesized" and language != "swift":
            errors.append(f"{label}.origin may be synthesized only for Swift")

        source = raw_symbol.get("source")
        expected_root = (
            "Sources/Cyrinx/" if language == "swift" else "Sources/CCyrinx/include/cyrinx/"
        )
        if isinstance(source, str) and not source.startswith(expected_root):
            errors.append(
                f"{label}.source must be under {expected_root!r}, found {source!r}"
            )

        availability = raw_symbol.get("availability")
        try:
            normalized_availability = canonical_availability(availability)
            if availability != normalized_availability:
                errors.append(f"{label}.availability is not canonical")
        except InventoryError as error:
            errors.append(f"{label}.availability: {error}")

        platforms = raw_symbol.get("platforms")
        if (
            not isinstance(platforms, list)
            or not platforms
            or not all(item in ALLOWED_PLATFORMS for item in platforms)
            or platforms != sorted(set(platforms))
            or ("all" in platforms and len(platforms) != 1)
        ):
            errors.append(
                f"{label}.platforms must be ['all'] or a sorted non-empty subset "
                f"of {sorted(ALLOWED_PLATFORMS - {'all'})}"
            )
        if language == "c" and platforms != ["all"]:
            errors.append(f"{label}.platforms must be ['all'] for C")

        attributes = raw_symbol.get("attributes")
        if (
            not isinstance(attributes, list)
            or not all(isinstance(item, str) and item for item in attributes)
            or attributes != sorted(set(attributes))
        ):
            errors.append(
                f"{label}.attributes must be a sorted array of unique strings"
            )

        ordinal = raw_symbol.get("ordinal")
        if kind in {"c.enum.case", "c.property"}:
            if not isinstance(ordinal, int) or ordinal < 0:
                errors.append(f"{label}.ordinal must be a nonnegative integer")
        elif "ordinal" in raw_symbol:
            errors.append(f"{label}.ordinal is only valid on C enum cases and fields")

        enum_value = raw_symbol.get("enumValue")
        if kind == "c.enum.case":
            if not isinstance(enum_value, int):
                errors.append(f"{label}.enumValue must be an integer")
        elif "enumValue" in raw_symbol:
            errors.append(f"{label}.enumValue is only valid on C enum cases")

        if isinstance(path, str) and "*" in path:
            errors.append(f"{label}.path must name one symbol, not a wildcard: {path}")

    expected_counts = manifest.get("classificationCounts")
    actual_counts: dict[str, dict[str, int]] = {
        language: {disposition: 0 for disposition in sorted(ALLOWED_DISPOSITIONS)}
        for language in sorted(ALLOWED_LANGUAGES)
    }
    for symbol in symbols:
        if (
            isinstance(symbol, dict)
            and symbol.get("present") is True
            and symbol.get("language") in actual_counts
            and symbol.get("disposition") in ALLOWED_DISPOSITIONS
        ):
            actual_counts[symbol["language"]][symbol["disposition"]] += 1
    if expected_counts != actual_counts:
        errors.append(
            "classificationCounts does not match present symbols: "
            f"expected {actual_counts!r}, found {expected_counts!r}"
        )

    if errors:
        formatted = "\n".join(f"- {error}" for error in errors)
        raise InventoryError(f"inventory structure is invalid:\n{formatted}")
    return symbols


def validate_documented_counts(manifest: dict[str, Any]) -> None:
    documentation_path = ROOT / "docs" / "API_INVENTORY.md"
    try:
        documentation = documentation_path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise InventoryError("missing docs/API_INVENTORY.md") from error
    counts = manifest["classificationCounts"]
    rows: list[tuple[str, str]] = []
    for language, label in (("swift", "Swift"), ("c", "C")):
        values = counts[language]
        total = sum(values.values())
        rows.append(
            (
                label,
                f"| {label} | {values['retain']} | {values['deprecate']} | "
                f"{values['experimental']} | {values['replace']} | {total} |",
            )
        )
    overall = {
        disposition: sum(
            counts[language][disposition] for language in sorted(ALLOWED_LANGUAGES)
        )
        for disposition in sorted(ALLOWED_DISPOSITIONS)
    }
    rows.append(
        (
            "Total",
            f"| **Total** | **{overall['retain']}** | **{overall['deprecate']}** | "
            f"**{overall['experimental']}** | **{overall['replace']}** | "
            f"**{sum(overall.values())}** |",
        )
    )
    missing = [label for label, row in rows if row not in documentation]
    if missing:
        raise InventoryError(
            "docs/API_INVENTORY.md classification totals are stale for: "
            + ", ".join(missing)
        )


def extraction_environment(cache_root: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment["CLANG_MODULE_CACHE_PATH"] = str(cache_root / "clang-module-cache")
    environment["XDG_CACHE_HOME"] = str(cache_root / "xdg-cache")
    return environment


def find_swift_command(description: dict[str, Any]) -> dict[str, Any]:
    commands = description.get("swiftCommands", {})
    if not isinstance(commands, dict):
        raise InventoryError("SwiftPM description has no swiftCommands object")
    matches = [
        command
        for command in commands.values()
        if isinstance(command, dict) and command.get("moduleName") == "Cyrinx"
    ]
    if len(matches) != 1:
        raise InventoryError(
            f"expected one SwiftPM command for Cyrinx, found {len(matches)}"
        )
    return matches[0]


def find_symbolgraph_extractor() -> str:
    configured = os.environ.get("SWIFT_SYMBOLGRAPH_EXTRACT")
    if configured:
        return configured
    discovered = shutil.which("swift-symbolgraph-extract")
    if discovered:
        return discovered
    xcrun = shutil.which("xcrun")
    if xcrun:
        result = run(
            [xcrun, "--find", "swift-symbolgraph-extract"],
            capture=True,
        )
        if result:
            return result
    raise InventoryError(
        "swift-symbolgraph-extract was not found in PATH or the active Xcode toolchain"
    )


def tri_not(value: bool | None) -> bool | None:
    return None if value is None else not value


def tri_and(left: bool | None, right: bool | None) -> bool | None:
    if left is False or right is False:
        return False
    if left is True and right is True:
        return True
    return None


def tri_or(left: bool | None, right: bool | None) -> bool | None:
    if left is True or right is True:
        return True
    if left is False and right is False:
        return False
    return None


class ConditionParser:
    """Evaluate boolean compilation conditions with an injectable atom policy."""

    def __init__(self, expression: str, atom_value: Any):
        self.expression = expression
        self.atom_value = atom_value
        self.index = 0
        self.invalid = False

    def parse(self) -> bool | None:
        value = self.parse_or()
        self.skip_space()
        if self.invalid or self.index != len(self.expression):
            return None
        return value

    def skip_space(self) -> None:
        while (
            self.index < len(self.expression)
            and self.expression[self.index].isspace()
        ):
            self.index += 1

    def consume(self, token: str) -> bool:
        self.skip_space()
        if self.expression.startswith(token, self.index):
            self.index += len(token)
            return True
        return False

    def parse_or(self) -> bool | None:
        value = self.parse_and()
        while self.consume("||"):
            value = tri_or(value, self.parse_and())
        return value

    def parse_and(self) -> bool | None:
        value = self.parse_unary()
        while self.consume("&&"):
            value = tri_and(value, self.parse_unary())
        return value

    def parse_unary(self) -> bool | None:
        if self.consume("!"):
            return tri_not(self.parse_unary())
        return self.parse_primary()

    def parse_primary(self) -> bool | None:
        self.skip_space()
        if self.consume("("):
            value = self.parse_or()
            if not self.consume(")"):
                self.invalid = True
            return value

        match = re.match(
            r"(?:[A-Za-z_][A-Za-z0-9_.]*|[0-9]+)",
            self.expression[self.index :],
        )
        if match is None:
            self.invalid = True
            return None
        atom = match.group(0)
        self.index += len(atom)
        self.skip_space()
        if self.index < len(self.expression) and self.expression[self.index] == "(":
            start = self.index
            depth = 0
            while self.index < len(self.expression):
                character = self.expression[self.index]
                self.index += 1
                if character == "(":
                    depth += 1
                elif character == ")":
                    depth -= 1
                    if depth == 0:
                        break
            if depth != 0:
                self.invalid = True
                return None
            atom += re.sub(r"\s+", "", self.expression[start : self.index])
        return self.atom_value(atom)


def evaluate_condition(expression: str, atom_value: Any) -> bool | None:
    return ConditionParser(expression, atom_value).parse()


def update_condition_stack(
    stack: list[dict[str, Any]],
    directive: str,
    expression: str,
    atom_value: Any,
) -> None:
    """Advance one #if family while retaining effective parent visibility."""

    if directive in {"if", "ifdef", "ifndef"}:
        if directive == "ifdef":
            expression = f"defined({expression.strip()})"
        elif directive == "ifndef":
            expression = f"!defined({expression.strip()})"
        branch = evaluate_condition(expression, atom_value)
        parent = stack[-1]["current"] if stack else True
        stack.append(
            {
                "parent": parent,
                "seen": branch,
                "current": tri_and(parent, branch),
                "elseSeen": False,
            }
        )
        return

    if not stack:
        raise InventoryError(f"unmatched conditional directive: #{directive}")
    frame = stack[-1]
    if directive in {"elseif", "elif"}:
        if frame["elseSeen"]:
            raise InventoryError(f"#{directive} follows #else")
        branch = evaluate_condition(expression, atom_value)
        eligible = tri_and(tri_not(frame["seen"]), branch)
        frame["seen"] = tri_or(frame["seen"], branch)
        frame["current"] = tri_and(frame["parent"], eligible)
    elif directive == "else":
        if frame["elseSeen"]:
            raise InventoryError("duplicate #else")
        frame["elseSeen"] = True
        frame["current"] = tri_and(frame["parent"], tri_not(frame["seen"]))
        frame["seen"] = True
    elif directive == "endif":
        stack.pop()


def macos_swift_atom_value(atom: str) -> bool | None:
    if atom in {"true", "1"}:
        return True
    if atom in {"false", "0"}:
        return False
    if atom == "os(macOS)":
        return True
    if atom.startswith("os("):
        return False
    if atom in {
        "canImport(Accelerate)",
        "canImport(AppKit)",
        "canImport(AVFoundation)",
        "canImport(Darwin)",
        "canImport(Foundation)",
    }:
        return True
    if atom in {"canImport(Glibc)", "canImport(UIKit)"}:
        return False
    return None


def swift_line_declares_public_api(line: str) -> bool:
    """Recognize a public/open token after comments and strings are removed."""

    code = line.strip()
    if not code or code.startswith("#"):
        return False
    return re.search(r"(?<![`A-Za-z0-9_])(public|open)(?![`A-Za-z0-9_])", code) is not None


def swift_code_without_comments(
    line: str,
    block_comment_depth: int,
    string_delimiter: tuple[int, bool] | None,
    regex_delimiter: tuple[int, bool] | None,
) -> tuple[str, int, tuple[int, bool] | None, tuple[int, bool] | None]:
    """Remove Swift comments and literals while retaining cross-line state."""

    output: list[str] = []
    index = 0
    while index < len(line):
        if block_comment_depth:
            if line.startswith("/*", index):
                block_comment_depth += 1
                index += 2
            elif line.startswith("*/", index):
                block_comment_depth -= 1
                index += 2
                if block_comment_depth == 0:
                    output.append(" ")
            else:
                index += 1
            continue

        if string_delimiter is not None:
            hash_count, multiline = string_delimiter
            closing = ('"""' if multiline else '"') + ("#" * hash_count)
            escaped = False
            if line.startswith(closing, index):
                if hash_count:
                    escape_prefix = "\\" + ("#" * hash_count)
                    escaped = line[max(0, index - len(escape_prefix)) : index] == escape_prefix
                else:
                    preceding_backslashes = 0
                    cursor = index - 1
                    while cursor >= 0 and line[cursor] == "\\":
                        preceding_backslashes += 1
                        cursor -= 1
                    escaped = preceding_backslashes % 2 == 1
            if line.startswith(closing, index) and not escaped:
                index += len(closing)
                string_delimiter = None
            else:
                index += 1
            continue

        if regex_delimiter is not None:
            hash_count, in_character_class = regex_delimiter
            if hash_count:
                closing = "/" + ("#" * hash_count)
                preceding_backslashes = 0
                cursor = index - 1
                while cursor >= 0 and line[cursor] == "\\":
                    preceding_backslashes += 1
                    cursor -= 1
                escaped = preceding_backslashes % 2 == 1
                if line.startswith(closing, index) and not escaped:
                    index += len(closing)
                    regex_delimiter = None
                else:
                    index += 1
                continue

            preceding_backslashes = 0
            cursor = index - 1
            while cursor >= 0 and line[cursor] == "\\":
                preceding_backslashes += 1
                cursor -= 1
            escaped = preceding_backslashes % 2 == 1
            if line[index] == "[" and not escaped:
                in_character_class = True
            elif line[index] == "]" and not escaped:
                in_character_class = False
            elif line[index] == "/" and not escaped and not in_character_class:
                regex_delimiter = None
                index += 1
                continue
            regex_delimiter = (0, in_character_class)
            index += 1
            continue

        if line.startswith("//", index):
            break
        if line.startswith("/*", index):
            block_comment_depth = 1
            index += 2
            output.append(" ")
            continue

        delimiter = re.match(r'(#+)?(""")', line[index:])
        if delimiter is None:
            delimiter = re.match(r'(#+)?(")', line[index:])
        if delimiter is not None:
            hashes = len(delimiter.group(1) or "")
            marker = delimiter.group(2)
            output.append('""')
            index += len(delimiter.group(0))
            string_delimiter = (hashes, marker == '"""')
            continue

        extended_regex = re.match(r"(#+)/", line[index:])
        if extended_regex is not None:
            hash_count = len(extended_regex.group(1))
            output.append("/_/")
            index += len(extended_regex.group(0))
            regex_delimiter = (hash_count, False)
            continue

        if line[index] == "/":
            prefix = "".join(output).rstrip()
            preceding_word = re.search(r"([A-Za-z_][A-Za-z0-9_]*)$", prefix)
            prefix_bang = False
            if prefix.endswith("!"):
                before_bang = prefix[:-1].rstrip()
                word_before_bang = re.search(
                    r"([A-Za-z_][A-Za-z0-9_]*)$",
                    before_bang,
                )
                prefix_bang = (
                    not before_bang
                    or before_bang[-1] in "=([{,:;!?&|+-*%<>^~"
                    or (
                        word_before_bang is not None
                        and word_before_bang.group(1) == "try"
                    )
                )
            expression_prefix = (
                not prefix
                or prefix[-1] in "=([{,:;?&|"
                or prefix_bang
                or (
                    preceding_word is not None
                    and preceding_word.group(1)
                    in {"await", "case", "else", "in", "return", "throw", "try", "where", "yield"}
                )
            )
            if expression_prefix:
                output.append("/_/")
                index += 1
                regex_delimiter = (0, False)
                continue

        output.append(line[index])
        index += 1

    if regex_delimiter is not None and regex_delimiter[0] == 0:
        raise InventoryError("unterminated Swift bare regex literal")
    return "".join(output), block_comment_depth, string_delimiter, regex_delimiter


def swift_attribute_prefix_is_incomplete(code: str) -> bool:
    """Return whether a leading Swift attribute continues onto another line."""

    if not code.lstrip().startswith("@"):
        return False
    depth = 0
    in_string = False
    escaped = False
    for character in code:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == "(":
            depth += 1
        elif character == ")" and depth:
            depth -= 1
    return depth > 0


def exclusive_public_lines(source_text: str) -> list[int]:
    """Find public declarations not proven visible to the macOS reference."""

    stack: list[dict[str, Any]] = []
    violations: list[int] = []
    block_comment_depth = 0
    string_delimiter: tuple[int, bool] | None = None
    regex_delimiter: tuple[int, bool] | None = None
    pending_attribute_prefix = ""
    directive_pattern = re.compile(r"^\s*#(if|elseif|else|endif)\b(.*)$")
    for line_number, line in enumerate(source_text.splitlines(), start=1):
        (
            code,
            block_comment_depth,
            string_delimiter,
            regex_delimiter,
        ) = swift_code_without_comments(
            line,
            block_comment_depth,
            string_delimiter,
            regex_delimiter,
        )
        directive = directive_pattern.match(code)
        if directive:
            pending_attribute_prefix = ""
            kind, expression = directive.groups()
            update_condition_stack(stack, kind, expression, macos_swift_atom_value)
            continue
        declaration = (
            f"{pending_attribute_prefix} {code}".strip()
            if pending_attribute_prefix
            else code
        )
        if (
            stack
            and stack[-1]["current"] is not True
            and swift_line_declares_public_api(declaration)
        ):
            violations.append(line_number)
            pending_attribute_prefix = ""
        elif (
            stack
            and stack[-1]["current"] is not True
            and swift_attribute_prefix_is_incomplete(declaration)
        ):
            pending_attribute_prefix = declaration
        else:
            pending_attribute_prefix = ""
    if block_comment_depth:
        raise InventoryError("unterminated Swift block comment")
    if string_delimiter is not None:
        raise InventoryError("unterminated Swift string literal")
    if regex_delimiter is not None:
        raise InventoryError("unterminated Swift regex literal")
    if stack:
        raise InventoryError("unterminated Swift conditional compilation block")
    return violations


def comment_free_c_source(source_text: str) -> str:
    """Replace C comments with whitespace without touching string literals."""

    output: list[str] = []
    index = 0
    state = "code"
    while index < len(source_text):
        character = source_text[index]
        following = source_text[index + 1] if index + 1 < len(source_text) else ""
        if state == "code":
            if character == "/" and following == "*":
                output.append(" ")
                index += 2
                state = "block"
                continue
            if character == "/" and following == "/":
                output.append(" ")
                index += 2
                state = "line"
                continue
            output.append(character)
            index += 1
            if character == '"':
                state = "string"
            elif character == "'":
                state = "character"
            continue
        if state == "block":
            if character == "*" and following == "/":
                output.append(" ")
                index += 2
                state = "code"
            else:
                if character == "\n":
                    output.append("\n")
                index += 1
            continue
        if state == "line":
            if character == "\n":
                output.append("\n")
                state = "code"
            index += 1
            continue

        output.append(character)
        index += 1
        if character == "\\" and index < len(source_text):
            output.append(source_text[index])
            index += 1
        elif (state == "string" and character == '"') or (
            state == "character" and character == "'"
        ):
            state = "code"

    if state == "block":
        raise InventoryError("unterminated C block comment")
    return "".join(output)


def c_header_guard(source_text: str) -> str | None:
    lines = comment_free_c_source(source_text).splitlines()
    significant = [line.strip() for line in lines if line.strip()]
    if len(significant) < 2:
        return None
    first = re.fullmatch(r"#\s*ifndef\s+([A-Za-z_][A-Za-z0-9_]*)", significant[0])
    if first is None:
        return None
    guard = first.group(1)
    if re.fullmatch(rf"#\s*define\s+{re.escape(guard)}(?:\s+.*)?", significant[1]):
        return guard
    return None


def conditional_c_api_lines(source_text: str) -> list[int]:
    """Reject C declarations whose platform/configuration branch is not universal."""

    stripped_source = comment_free_c_source(source_text)
    guard = c_header_guard(source_text)

    def c_atom_value(atom: str) -> bool | None:
        if atom in {"1", "true"}:
            return True
        if atom in {"0", "false"}:
            return False
        if guard is not None and atom == f"defined({guard})":
            return False
        if atom == "defined(__cplusplus)":
            return False
        return None

    stack: list[dict[str, Any]] = []
    violations: list[int] = []
    directive_pattern = re.compile(
        r"^\s*#\s*(if|ifdef|ifndef|elif|else|endif)\b(.*)$"
    )
    macro_continuation = False
    for line_number, line in enumerate(stripped_source.splitlines(), start=1):
        if macro_continuation:
            macro_continuation = line.rstrip().endswith("\\")
            continue
        directive = directive_pattern.match(line)
        if directive:
            kind, expression = directive.groups()
            update_condition_stack(stack, kind, expression, c_atom_value)
            continue
        if line.lstrip().startswith("#"):
            macro_continuation = (
                re.match(r"^\s*#\s*(?:define|undef)\b", line) is not None
                and line.rstrip().endswith("\\")
            )
            continue
        if not stack or stack[-1]["current"] is True:
            continue
        code = line.strip()
        if not code or code in {"{", "}", "};"} or re.fullmatch(
            r'extern\s+"C"\s*\{?', code
        ):
            continue
        violations.append(line_number)
    if stack:
        raise InventoryError("unterminated C preprocessor conditional")
    return violations


def enforce_macos_union_policy() -> None:
    """Forbid public API that the current reference extraction cannot observe."""

    violations: list[str] = []
    source_root = ROOT / "Sources" / "Cyrinx"
    for source in sorted(source_root.rglob("*.swift")):
        lines = exclusive_public_lines(source.read_text(encoding="utf-8"))
        violations.extend(
            f"{source.relative_to(ROOT)}:{line}" for line in lines
        )
    if violations:
        raise InventoryError(
            "platform-exclusive iOS/Linux public Swift declarations are "
            "prohibited until a compiler-extracted platform-union lane exists:\n"
            + "\n".join(f"- {violation}" for violation in violations)
        )


def enforce_c_union_policy(headers: list[Path]) -> None:
    """Keep the checked-in all-platform C surface fail-closed until matrix extraction."""

    violations: list[str] = []
    for header in headers:
        lines = conditional_c_api_lines(header.read_text(encoding="utf-8"))
        violations.extend(f"{header.relative_to(ROOT)}:{line}" for line in lines)
    if violations:
        raise InventoryError(
            "conditional public C declarations are prohibited until a "
            "compiler-extracted configuration-union lane exists:\n"
            + "\n".join(f"- {violation}" for violation in violations)
        )


def c_macro_names_from_graph(path: Path) -> set[str]:
    """Return macro identities visible to the reference Clang extraction."""

    graph = load_json(path)
    symbols = graph.get("symbols")
    if not isinstance(symbols, list):
        raise InventoryError(f"{path.name} has no symbols array")
    names: set[str] = set()
    for symbol in symbols:
        if not isinstance(symbol, dict):
            continue
        kind = symbol.get("kind", {})
        if not isinstance(kind, dict) or kind.get("identifier") != "c.macro":
            continue
        components = symbol.get("pathComponents")
        if isinstance(components, list) and all(
            isinstance(component, str) for component in components
        ):
            names.add(".".join(components))
    return names


def enforce_c_macro_union_policy(headers: list[Path], graph_path: Path) -> None:
    """Reject conditional macros invisible to the reference extraction."""

    extracted_names = c_macro_names_from_graph(graph_path)
    violations: list[str] = []
    for header in headers:
        conditional = conditional_c_macro_lines(
            header.read_text(encoding="utf-8")
        )
        violations.extend(
            f"{header.relative_to(ROOT)}:{line}: {name}"
            for name, line in sorted(conditional.items())
            if name not in extracted_names
        )
    if violations:
        raise InventoryError(
            "conditional public C macros are absent from the reference "
            "compiler extraction:\n"
            + "\n".join(f"- {violation}" for violation in violations)
        )


def forwarded_swift_arguments(arguments: list[str]) -> tuple[str, list[str]]:
    target: str | None = None
    forwarded: list[str] = []
    paired = {"-sdk", "-F", "-I", "-L", "-plugin-path"}
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "-target":
            if index + 1 >= len(arguments):
                raise InventoryError("SwiftPM emitted -target without a value")
            target = arguments[index + 1]
            index += 2
            continue
        if argument in paired:
            if index + 1 >= len(arguments):
                raise InventoryError(f"SwiftPM emitted {argument} without a value")
            forwarded.extend((argument, arguments[index + 1]))
            index += 2
            continue
        if argument == "-Xcc":
            if index + 1 >= len(arguments):
                raise InventoryError("SwiftPM emitted -Xcc without a value")
            forwarded.extend((argument, arguments[index + 1]))
            index += 2
            continue
        index += 1

    if target is None:
        raise InventoryError("SwiftPM command did not declare a compilation target")
    return target, forwarded


def extract_swift_symbol_graph(
    temporary_directory: Path,
    environment: dict[str, str],
) -> Path:
    enforce_macos_union_policy()
    run(
        ["swift", "build", "--disable-sandbox", "--target", "Cyrinx"],
        environment=environment,
    )
    binary_path = Path(
        run(
            ["swift", "build", "--show-bin-path"],
            environment=environment,
            capture=True,
        )
    )
    description_path = binary_path / "description.json"
    description = load_json(description_path)
    command = find_swift_command(description)
    target, forwarded = forwarded_swift_arguments(command.get("otherArguments", []))

    output_directory = temporary_directory / "swift-symbols"
    output_directory.mkdir(parents=True)
    module_cache = temporary_directory / "swift-symbol-cache"
    extractor = find_symbolgraph_extractor()
    arguments = [
        extractor,
        "-module-name",
        "Cyrinx",
        "-target",
        target,
        "-module-cache-path",
        str(module_cache),
        "-I",
        str(command["importPath"]),
        *forwarded,
        "-minimum-access-level",
        "public",
        "-skip-synthesized-members",
        "-output-dir",
        str(output_directory),
    ]
    run(arguments, environment=environment)
    graph_path = output_directory / "Cyrinx.symbols.json"
    if not graph_path.is_file():
        raise InventoryError("Swift symbol extraction did not produce Cyrinx.symbols.json")
    return graph_path


def extract_c_symbol_graph(
    temporary_directory: Path,
    environment: dict[str, str],
) -> Path:
    clang = os.environ.get("CLANG") or shutil.which("clang")
    if not clang:
        raise InventoryError("clang was not found in PATH")
    include_root = ROOT / "Sources" / "CCyrinx" / "include"
    headers = sorted((include_root / "cyrinx").glob("*.h"))
    if not headers:
        raise InventoryError("no public C headers found")
    enforce_c_union_policy(headers)
    graph_path = temporary_directory / "CCyrinx.symbols.json"
    run(
        [
            clang,
            "-extract-api",
            "-x",
            "c-header",
            "-std=c11",
            "-I",
            str(include_root),
            *(str(header) for header in headers),
            "-o",
            str(graph_path),
        ],
        environment=environment,
    )
    if not graph_path.is_file():
        raise InventoryError("Clang API extraction did not produce a symbol graph")
    enforce_c_macro_union_policy(headers, graph_path)
    return graph_path


def extract_c_ast(
    temporary_directory: Path,
    environment: dict[str, str],
) -> dict[str, Any]:
    """Extract source-contract facts that Clang ExtractAPI omits."""

    clang = os.environ.get("CLANG") or shutil.which("clang")
    if not clang:
        raise InventoryError("clang was not found in PATH")
    include_root = ROOT / "Sources" / "CCyrinx" / "include"
    headers = sorted((include_root / "cyrinx").glob("*.h"))
    umbrella = temporary_directory / "cyrinx-public-headers.c"
    umbrella.write_text(
        "".join(f"#include <cyrinx/{header.name}>\n" for header in headers),
        encoding="utf-8",
    )
    raw_ast = run(
        [
            clang,
            "-x",
            "c",
            "-std=c11",
            "-I",
            str(include_root),
            "-Xclang",
            "-ast-dump=json",
            "-fsyntax-only",
            str(umbrella),
        ],
        environment=environment,
        capture=True,
    )
    try:
        ast = json.loads(raw_ast)
    except json.JSONDecodeError as error:
        raise InventoryError(f"Clang AST output is invalid JSON: {error}") from error
    if not isinstance(ast, dict) or ast.get("kind") != "TranslationUnitDecl":
        raise InventoryError("Clang AST output has no translation unit")
    return ast


def source_path_from_uri(uri: str) -> str:
    prefix = "file://"
    raw_path = unquote(uri[len(prefix) :]) if uri.startswith(prefix) else uri
    candidate = Path(raw_path)
    if candidate.is_absolute():
        try:
            candidate = candidate.relative_to(ROOT)
        except ValueError as error:
            raise InventoryError(f"symbol source is outside the repository: {uri}") from error
    normalized = candidate.as_posix()
    if not normalized or normalized.startswith("../"):
        raise InventoryError(f"invalid symbol source URI: {uri}")
    return normalized


def normalized_declaration(symbol: dict[str, Any]) -> str:
    fragments = symbol.get("declarationFragments", [])
    if not isinstance(fragments, list):
        raise InventoryError("symbol declarationFragments must be an array")
    spelling = "".join(
        fragment.get("spelling", "")
        for fragment in fragments
        if isinstance(fragment, dict)
    )
    return " ".join(spelling.split())


def normalized_preprocessor_expression(expression: str) -> str:
    value = " ".join(expression.strip().split())
    value = re.sub(
        r"\bdefined\s+([A-Za-z_][A-Za-z0-9_]*)",
        r"defined(\1)",
        value,
    )
    value = re.sub(r"\s*(&&|\|\|)\s*", r" \1 ", value)
    value = re.sub(r"\s*([(),!])\s*", r"\1", value)
    return value


def preprocessor_logical_lines(source_text: str) -> list[tuple[int, str]]:
    """Return comment-free, line-spliced preprocessor input with source lines."""

    lines = comment_free_c_source(source_text).splitlines()
    logical_lines: list[tuple[int, str]] = []
    index = 0
    while index < len(lines):
        start_line = index + 1
        fragments = [lines[index].strip()]
        while fragments[-1].endswith("\\"):
            fragments[-1] = fragments[-1][:-1].rstrip()
            index += 1
            if index >= len(lines):
                raise InventoryError(
                    f"unterminated preprocessor directive at line {start_line}"
                )
            fragments.append(lines[index].strip())
        logical_lines.append(
            (start_line, " ".join(" ".join(fragments).split()))
        )
        index += 1
    return logical_lines


def effective_preprocessor_condition(stack: list[dict[str, Any]]) -> str:
    conditions = [frame["current"] for frame in stack if frame["current"] != "1"]
    if not conditions:
        return "1"
    return " && ".join(f"({condition})" for condition in conditions)


def update_textual_preprocessor_stack(
    stack: list[dict[str, Any]],
    kind: str,
    expression: str,
    header_guard: str | None,
    start_line: int,
) -> bool:
    """Apply a textual preprocessor branch and report whether it was consumed."""

    if kind in {"if", "ifdef", "ifndef"}:
        if kind == "ifdef":
            condition = f"defined({expression})"
        elif kind == "ifndef":
            condition = f"!defined({expression})"
        else:
            condition = expression
        if (
            kind == "ifndef"
            and not stack
            and header_guard is not None
            and expression == header_guard
        ):
            condition = "1"
        stack.append(
            {
                "branches": [condition],
                "current": condition,
                "elseSeen": False,
            }
        )
        return True

    if kind not in {"elif", "else", "endif"}:
        return False
    if not stack:
        raise InventoryError(
            f"unmatched preprocessor directive at line {start_line}: #{kind}"
        )
    frame = stack[-1]
    prior = " || ".join(frame["branches"])
    if kind == "elif":
        if frame["elseSeen"]:
            raise InventoryError(f"#elif follows #else at line {start_line}")
        frame["current"] = f"!({prior}) && ({expression})"
        frame["branches"].append(expression)
    elif kind == "else":
        if frame["elseSeen"]:
            raise InventoryError(f"duplicate #else at line {start_line}")
        frame["elseSeen"] = True
        frame["current"] = f"!({prior})"
    else:
        stack.pop()
    return True


def macro_definitions_from_text(source_text: str, macro_name: str) -> list[str]:
    """Collect define/undef events together with their effective predicates."""

    header_guard = c_header_guard(source_text)
    stack: list[dict[str, Any]] = []
    events: list[str] = []
    directive_pattern = re.compile(
        r"^#\s*(if|ifdef|ifndef|elif|else|endif|define|undef)\b(.*)$"
    )
    definition_pattern = re.compile(
        rf"^#\s*define\s+{re.escape(macro_name)}(?=\s|\(|$)"
    )
    undefinition_pattern = re.compile(
        rf"^#\s*undef\s+{re.escape(macro_name)}(?:\s|$)"
    )

    for start_line, logical_line in preprocessor_logical_lines(source_text):
        directive = directive_pattern.match(logical_line)
        if directive is None:
            continue
        kind, raw_expression = directive.groups()
        expression = normalized_preprocessor_expression(raw_expression)

        if update_textual_preprocessor_stack(
            stack,
            kind,
            expression,
            header_guard,
            start_line,
        ):
            continue

        matches_target = (
            definition_pattern.match(logical_line)
            if kind == "define"
            else undefinition_pattern.match(logical_line)
        )
        if matches_target:
            condition = effective_preprocessor_condition(stack)
            event = logical_line
            if condition != "1":
                event = f"[{condition}] {event}"
            events.append(event)

    if stack:
        raise InventoryError("unterminated preprocessor conditional")
    return events


def conditional_c_macro_lines(source_text: str) -> dict[str, int]:
    """Return conditional macro define/undef names and their first source line."""

    header_guard = c_header_guard(source_text)
    stack: list[dict[str, Any]] = []
    conditional: dict[str, int] = {}
    directive_pattern = re.compile(
        r"^#\s*(if|ifdef|ifndef|elif|else|endif|define|undef)\b(.*)$"
    )
    macro_pattern = re.compile(
        r"^#\s*(?:define|undef)\s+([A-Za-z_][A-Za-z0-9_]*)"
    )
    for start_line, logical_line in preprocessor_logical_lines(source_text):
        directive = directive_pattern.match(logical_line)
        if directive is None:
            continue
        kind, raw_expression = directive.groups()
        expression = normalized_preprocessor_expression(raw_expression)
        if update_textual_preprocessor_stack(
            stack,
            kind,
            expression,
            header_guard,
            start_line,
        ):
            continue
        macro = macro_pattern.match(logical_line)
        if (
            macro is not None
            and effective_preprocessor_condition(stack) != "1"
        ):
            conditional.setdefault(macro.group(1), start_line)
    if stack:
        raise InventoryError("unterminated preprocessor conditional")
    return conditional


def macro_declaration(
    symbol: dict[str, Any],
    source: str,
    macro_name: str,
) -> str:
    """Return every conditional definition of one logical public macro."""

    lines = (ROOT / source).read_text(encoding="utf-8").splitlines()
    definitions = macro_definitions_from_text("\n".join(lines), macro_name)
    if definitions:
        return " || ".join(definitions)

    location = symbol.get("location", {})
    position = location.get("position", {}) if isinstance(location, dict) else {}
    line_index = position.get("line") if isinstance(position, dict) else None
    if not isinstance(line_index, int):
        raise InventoryError(f"macro {symbol.get('identifier')} has no source line")
    if line_index < 0 or line_index >= len(lines):
        raise InventoryError(f"macro source line is out of range: {source}:{line_index + 1}")
    declaration_lines = [lines[line_index].strip()]
    while declaration_lines[-1].endswith("\\"):
        next_index = line_index + len(declaration_lines)
        if next_index >= len(lines):
            raise InventoryError(f"unterminated macro definition: {source}:{line_index + 1}")
        declaration_lines.append(lines[next_index].strip())
    return " ".join(" ".join(declaration_lines).split())


def repository_source_path(raw_path: str | None) -> str | None:
    if not isinstance(raw_path, str) or not raw_path:
        return None
    candidate = Path(raw_path)
    if candidate.is_absolute():
        try:
            candidate = candidate.relative_to(ROOT)
        except ValueError:
            return None
    normalized = candidate.as_posix()
    if not normalized.startswith("Sources/CCyrinx/include/cyrinx/"):
        return None
    return normalized


def owned_tag_identifier(node: dict[str, Any]) -> str | None:
    stack: list[Any] = list(node.get("inner", []))
    while stack:
        value = stack.pop()
        if not isinstance(value, dict):
            continue
        owned = value.get("ownedTagDecl")
        if isinstance(owned, dict) and isinstance(owned.get("id"), str):
            return owned["id"]
        stack.extend(value.get("inner", []))
    return None


def constant_expression_value(node: dict[str, Any]) -> int | None:
    stack: list[Any] = list(node.get("inner", []))
    while stack:
        value = stack.pop(0)
        if not isinstance(value, dict):
            continue
        if value.get("kind") == "ConstantExpr" and isinstance(
            value.get("value"), str
        ):
            try:
                return int(value["value"], 0)
            except ValueError as error:
                raise InventoryError(
                    f"non-integral enum value for {node.get('name')}: "
                    f"{value['value']!r}"
                ) from error
        stack[0:0] = value.get("inner", [])
    return None


def ast_attribute_signature(node: dict[str, Any]) -> str:
    """Return a stable signature for one explicit Clang AST attribute."""

    def canonical_value(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: canonical_value(value[key])
                for key in sorted(value)
                if key
                not in {
                    "id",
                    "implicit",
                    "inherited",
                    "isImplicit",
                    "isInherited",
                    "loc",
                    "range",
                }
            }
        if isinstance(value, list):
            return [canonical_value(item) for item in value]
        return value

    canonical = canonical_value(node)
    if not isinstance(canonical, dict):
        raise InventoryError("Clang AST attribute must be an object")
    kind = canonical.pop("kind", None)
    if not isinstance(kind, str) or not kind.endswith("Attr"):
        raise InventoryError(f"invalid Clang AST attribute kind: {kind!r}")
    if not canonical:
        return kind
    payload = json.dumps(
        canonical,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return f"{kind}:{payload}"


def direct_ast_attributes(node: dict[str, Any]) -> list[str]:
    """Return explicit attributes attached directly to one AST declaration."""

    children = node.get("inner", [])
    if not isinstance(children, list):
        return []
    return sorted(
        {
            ast_attribute_signature(child)
            for child in children
            if isinstance(child, dict)
            and isinstance(child.get("kind"), str)
            and child["kind"].endswith("Attr")
            and child.get("isImplicit") is not True
            and child.get("implicit") is not True
            and child.get("inherited") is not True
            and child.get("isInherited") is not True
        }
    )


def add_symbol_attributes(
    attributes: dict[str, set[str]],
    identifier: str,
    values: list[str],
) -> None:
    if values:
        attributes.setdefault(identifier, set()).update(values)


def clang_ast_metadata(ast: dict[str, Any]) -> dict[str, Any]:
    """Derive C layout facts, explicit attributes, and namespace identities."""

    raw_declarations = ast.get("inner")
    if not isinstance(raw_declarations, list):
        raise InventoryError("Clang translation unit has no declarations")

    declarations: list[tuple[dict[str, Any], str]] = []
    current_source: str | None = None
    for raw_node in raw_declarations:
        if not isinstance(raw_node, dict):
            continue
        location = raw_node.get("loc", {})
        location_file = location.get("file") if isinstance(location, dict) else None
        if isinstance(location_file, str):
            current_source = repository_source_path(location_file)
        if current_source is not None:
            declarations.append((raw_node, current_source))

    record_declarations: dict[str, tuple[dict[str, Any], str]] = {}
    enum_declarations: dict[str, tuple[dict[str, Any], str]] = {}
    typedefs: list[tuple[dict[str, Any], str]] = []
    functions: list[dict[str, Any]] = []
    for node, source in declarations:
        identifier = node.get("id")
        if not isinstance(identifier, str):
            continue
        if node.get("kind") == "RecordDecl":
            record_declarations[identifier] = (node, source)
        elif node.get("kind") == "EnumDecl":
            enum_declarations[identifier] = (node, source)
        elif node.get("kind") == "TypedefDecl":
            typedefs.append((node, source))
        elif node.get("kind") == "FunctionDecl":
            functions.append(node)

    def declaration_root(
        identifier: str,
        declaration_map: dict[str, tuple[dict[str, Any], str]],
    ) -> str:
        current = identifier
        seen_identifiers: set[str] = set()
        while current in declaration_map:
            if current in seen_identifiers:
                raise InventoryError(f"Clang redeclaration cycle at {identifier}")
            seen_identifiers.add(current)
            previous = declaration_map[current][0].get("previousDecl")
            if not isinstance(previous, str) or previous not in declaration_map:
                return current
            current = previous
        return identifier

    records: dict[str, list[tuple[dict[str, Any], str]]] = {}
    for identifier, declaration in record_declarations.items():
        records.setdefault(
            declaration_root(identifier, record_declarations),
            [],
        ).append(declaration)
    enums: dict[str, list[tuple[dict[str, Any], str]]] = {}
    for identifier, declaration in enum_declarations.items():
        enums.setdefault(
            declaration_root(identifier, enum_declarations),
            [],
        ).append(declaration)

    aliases_by_tag: dict[str, list[tuple[str, str, dict[str, Any]]]] = {}
    for typedef, source in typedefs:
        tag_identifier = owned_tag_identifier(typedef)
        alias = typedef.get("name")
        if not isinstance(tag_identifier, str) or not isinstance(alias, str):
            continue
        if tag_identifier in record_declarations:
            tag_identifier = declaration_root(tag_identifier, record_declarations)
        elif tag_identifier in enum_declarations:
            tag_identifier = declaration_root(tag_identifier, enum_declarations)
        aliases_by_tag.setdefault(tag_identifier, []).append(
            (alias, source, typedef)
        )

    fields: dict[str, dict[str, Any]] = {}
    record_attributes: dict[str, list[str]] = {}
    symbol_attributes: dict[str, set[str]] = {}
    aggregate_replacements: dict[str, set[str]] = {}
    supplements: list[dict[str, Any]] = []

    for function in functions:
        function_name = function.get("name")
        if not isinstance(function_name, str) or not function_name:
            continue
        function_identifier = stable_identifier("c", "c.func", function_name)
        add_symbol_attributes(
            symbol_attributes,
            function_identifier,
            direct_ast_attributes(function),
        )
        parameter_ordinal = 0
        for child in function.get("inner", []):
            if not isinstance(child, dict) or child.get("kind") != "ParmVarDecl":
                continue
            parameter_name = child.get("name")
            label = (
                f"parameter[{parameter_ordinal}:{parameter_name}]"
                if isinstance(parameter_name, str) and parameter_name
                else f"parameter[{parameter_ordinal}]"
            )
            add_symbol_attributes(
                symbol_attributes,
                function_identifier,
                [
                    f"{label}:{attribute}"
                    for attribute in direct_ast_attributes(child)
                ],
            )
            parameter_ordinal += 1

    for typedef, _ in typedefs:
        alias = typedef.get("name")
        if isinstance(alias, str) and alias:
            add_symbol_attributes(
                symbol_attributes,
                stable_identifier("c", "c.typealias", alias),
                direct_ast_attributes(typedef),
            )

    for tag_identifier, redeclarations in records.items():
        aliases = aliases_by_tag.get(tag_identifier, [])
        definition = next(
            (
                declaration
                for declaration in reversed(redeclarations)
                if declaration[0].get("completeDefinition") is True
            ),
            None,
        )
        record, source = definition or redeclarations[-1]
        tag_name = record.get("name")
        complete = definition is not None
        children = definition[0].get("inner", []) if definition is not None else []
        if not isinstance(children, list):
            children = []
        attributes = sorted(
            {
                attribute
                for declaration, _ in redeclarations
                for attribute in direct_ast_attributes(declaration)
            }
        )
        named_tag = isinstance(tag_name, str) and bool(tag_name)
        tag_symbol_identifier: str | None = None
        alias_symbol_identifiers: list[str] = []

        if named_tag:
            tag_symbol_identifier = stable_identifier("c", "c.tag.struct", tag_name)
            add_symbol_attributes(
                symbol_attributes,
                tag_symbol_identifier,
                attributes,
            )
            supplements.append(
                make_symbol(
                    language="c",
                    kind="c.tag.struct",
                    public_path=tag_name,
                    declaration=f"struct {tag_name}",
                    source=source,
                    attributes=attributes,
                )
            )
            for alias, alias_source, alias_node in aliases:
                alias_identifier = stable_identifier("c", "c.typealias", alias)
                alias_symbol_identifiers.append(alias_identifier)
                alias_attributes = direct_ast_attributes(alias_node)
                supplements.append(
                    make_symbol(
                        language="c",
                        kind="c.typealias",
                        public_path=alias,
                        declaration=f"typedef struct {tag_name} {alias}",
                        source=alias_source,
                        attributes=alias_attributes,
                    )
                )
            replacements = {tag_symbol_identifier, *alias_symbol_identifiers}
            for aggregate_path in {tag_name, *(alias for alias, _, _ in aliases)}:
                aggregate_replacements.setdefault(
                    stable_identifier("c", "c.struct", aggregate_path),
                    set(),
                ).update(replacements)

        for alias, _, alias_node in aliases:
            record_attributes[alias] = attributes
            if not named_tag:
                add_symbol_attributes(
                    symbol_attributes,
                    stable_identifier("c", "c.struct", alias),
                    [*attributes, *direct_ast_attributes(alias_node)],
                )

        if not complete:
            continue
        owner_names = [
            *([tag_name] if named_tag else []),
            *(alias for alias, _, _ in aliases),
        ]
        ordinal = 0
        for child in children:
            if not isinstance(child, dict) or child.get("kind") != "FieldDecl":
                continue
            field_name = child.get("name")
            if isinstance(field_name, str) and field_name:
                for owner_name in owner_names:
                    field_path = f"{owner_name}.{field_name}"
                    fields[field_path] = {"ordinal": ordinal}
                    add_symbol_attributes(
                        symbol_attributes,
                        stable_identifier("c", "c.property", field_path),
                        direct_ast_attributes(child),
                    )
            ordinal += 1

    enum_cases: dict[str, dict[str, Any]] = {}
    for tag_identifier, redeclarations in enums.items():
        aliases = aliases_by_tag.get(tag_identifier, [])
        definition = next(
            (
                declaration
                for declaration in reversed(redeclarations)
                if any(
                    isinstance(child, dict)
                    and child.get("kind") == "EnumConstantDecl"
                    for child in declaration[0].get("inner", [])
                )
            ),
            None,
        )
        enum, source = definition or redeclarations[-1]
        tag_name = enum.get("name")
        attributes = sorted(
            {
                attribute
                for declaration, _ in redeclarations
                for attribute in direct_ast_attributes(declaration)
            }
        )
        named_tag = isinstance(tag_name, str) and bool(tag_name)
        tag_symbol_identifier: str | None = None
        alias_symbol_identifiers: list[str] = []
        if named_tag:
            tag_symbol_identifier = stable_identifier("c", "c.tag.enum", tag_name)
            add_symbol_attributes(
                symbol_attributes,
                tag_symbol_identifier,
                attributes,
            )
            supplements.append(
                make_symbol(
                    language="c",
                    kind="c.tag.enum",
                    public_path=tag_name,
                    declaration=f"enum {tag_name}",
                    source=source,
                    attributes=attributes,
                )
            )
            for alias, alias_source, alias_node in aliases:
                alias_identifier = stable_identifier("c", "c.typealias", alias)
                alias_symbol_identifiers.append(alias_identifier)
                alias_attributes = direct_ast_attributes(alias_node)
                supplements.append(
                    make_symbol(
                        language="c",
                        kind="c.typealias",
                        public_path=alias,
                        declaration=f"typedef enum {tag_name} {alias}",
                        source=alias_source,
                        attributes=alias_attributes,
                    )
                )
            replacements = {tag_symbol_identifier, *alias_symbol_identifiers}
            for aggregate_path in {tag_name, *(alias for alias, _, _ in aliases)}:
                aggregate_replacements.setdefault(
                    stable_identifier("c", "c.enum", aggregate_path),
                    set(),
                ).update(replacements)
        else:
            for alias, _, alias_node in aliases:
                add_symbol_attributes(
                    symbol_attributes,
                    stable_identifier("c", "c.enum", alias),
                    [*attributes, *direct_ast_attributes(alias_node)],
                )

        children = enum.get("inner", [])
        if not isinstance(children, list):
            continue
        cases: list[tuple[dict[str, Any], str, int, int]] = []
        current_value = -1
        ordinal = 0
        for child in children:
            if not isinstance(child, dict) or child.get("kind") != "EnumConstantDecl":
                continue
            explicit_value = constant_expression_value(child)
            current_value = current_value + 1 if explicit_value is None else explicit_value
            case_name = child.get("name")
            if isinstance(case_name, str) and case_name:
                cases.append((child, case_name, ordinal, current_value))
            ordinal += 1
        owner_names = [
            *([tag_name] if named_tag else []),
            *(alias for alias, _, _ in aliases),
        ]
        for owner_name in owner_names:
            for case_node, case_name, case_ordinal, value in cases:
                case_path = f"{owner_name}.{case_name}"
                enum_cases[case_path] = {
                    "enumValue": value,
                    "ordinal": case_ordinal,
                }
                add_symbol_attributes(
                    symbol_attributes,
                    stable_identifier("c", "c.enum.case", case_path),
                    direct_ast_attributes(case_node),
                )

    return {
        "aggregateReplacements": {
            identifier: sorted(replacements)
            for identifier, replacements in aggregate_replacements.items()
        },
        "attributes": {
            identifier: sorted(values)
            for identifier, values in symbol_attributes.items()
        },
        "enumCases": enum_cases,
        "fields": fields,
        "recordAttributes": record_attributes,
        "supplements": supplements,
    }


def normalize_graph(
    path: Path,
    language: str,
    *,
    c_metadata: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    graph = load_json(path)
    raw_symbols = graph.get("symbols")
    if not isinstance(raw_symbols, list):
        raise InventoryError(f"{path.name} has no symbols array")
    conformances_by_identifier: dict[str, set[str]] = {}
    owner_by_identifier: dict[str, str] = {}
    if language == "swift":
        relationships = graph.get("relationships", [])
        if not isinstance(relationships, list):
            raise InventoryError(f"{path.name} has no relationships array")
        for relationship in relationships:
            if not isinstance(relationship, dict):
                continue
            if relationship.get("kind") != "conformsTo":
                if relationship.get("kind") == "memberOf":
                    source_identifier = relationship.get("source")
                    target_identifier = relationship.get("target")
                    if isinstance(source_identifier, str) and isinstance(
                        target_identifier, str
                    ):
                        owner_by_identifier[source_identifier] = target_identifier
                continue
            source_identifier = relationship.get("source")
            target = relationship.get("targetFallback")
            if isinstance(source_identifier, str) and isinstance(target, str):
                # Swift 6.3 emits this implicit marker while Swift 6.0 does not.
                # It is not a source-declared compatibility conformance.
                if target != "Swift.SendableMetatype":
                    conformances_by_identifier.setdefault(source_identifier, set()).add(
                        target
                    )

    source_uri_by_identifier: dict[str, str] = {}
    if language == "swift":
        for raw_symbol in raw_symbols:
            if not isinstance(raw_symbol, dict):
                continue
            identifier_value = raw_symbol.get("identifier", {})
            location = raw_symbol.get("location", {})
            precise = (
                identifier_value.get("precise")
                if isinstance(identifier_value, dict)
                else None
            )
            uri = location.get("uri") if isinstance(location, dict) else None
            if isinstance(precise, str) and isinstance(uri, str):
                source_uri_by_identifier[precise] = uri

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    c_availability_overlays: dict[str, list[dict[str, Any]]] = {}
    for raw_symbol in raw_symbols:
        if not isinstance(raw_symbol, dict):
            raise InventoryError(f"{path.name} contains a non-object symbol")
        kind_value = raw_symbol.get("kind", {})
        kind = kind_value.get("identifier") if isinstance(kind_value, dict) else None
        identifier_value = raw_symbol.get("identifier", {})
        compiler_identifier = (
            identifier_value.get("precise")
            if isinstance(identifier_value, dict)
            else None
        )
        components = raw_symbol.get("pathComponents")
        location = raw_symbol.get("location", {})
        uri = location.get("uri") if isinstance(location, dict) else None
        if not isinstance(kind, str) or not isinstance(components, list):
            raise InventoryError(f"{path.name} contains an incomplete symbol")
        if not all(isinstance(component, str) for component in components):
            raise InventoryError(f"{path.name} contains invalid path components")
        origin = "source"
        if not isinstance(uri, str) and language == "swift":
            owner_identifier = owner_by_identifier.get(compiler_identifier)
            uri = source_uri_by_identifier.get(owner_identifier)
            origin = "synthesized"
        if not isinstance(uri, str):
            raise InventoryError(
                f"public symbol {'.'.join(components)} has no source location"
            )
        public_path = ".".join(components)
        declaration = normalized_declaration(raw_symbol)
        source = source_path_from_uri(uri)
        if kind == "c.macro":
            declaration = macro_declaration(raw_symbol, source, public_path)
        identifier = stable_identifier(language, kind, public_path)
        if identifier in seen:
            raise InventoryError(
                "stable public path collision; inventory key needs refinement: "
                f"{identifier}"
            )
        seen.add(identifier)
        normalized_symbol: dict[str, Any] = {
            "id": identifier,
            "language": language,
            "kind": kind,
            "path": public_path,
            "declaration": declaration,
            "source": source,
            "origin": origin,
            "availability": canonical_availability(raw_symbol.get("availability")),
            "conformances": sorted(
                conformances_by_identifier.get(compiler_identifier, set())
            ),
            "attributes": [],
        }
        if language == "c":
            if c_metadata is None:
                raise InventoryError("C normalization requires Clang AST metadata")
            normalized_symbol["attributes"] = c_metadata["attributes"].get(
                identifier,
                [],
            )
            if kind == "c.enum.case":
                details = c_metadata["enumCases"].get(public_path)
                if not isinstance(details, dict):
                    raise InventoryError(
                        f"Clang AST has no enum value/order for {public_path}"
                    )
                normalized_symbol.update(details)
            elif kind == "c.property":
                details = c_metadata["fields"].get(public_path)
                if not isinstance(details, dict):
                    raise InventoryError(
                        f"Clang AST has no field order for {public_path}"
                    )
                normalized_symbol.update(details)
            replacements = c_metadata["aggregateReplacements"].get(identifier)
            if isinstance(replacements, list):
                for replacement in replacements:
                    c_availability_overlays.setdefault(
                        replacement,
                        [],
                    ).extend(normalized_symbol["availability"])
                continue
        normalized_symbol["fingerprint"] = symbol_fingerprint(normalized_symbol)
        normalized.append(normalized_symbol)
    if language == "c":
        if c_metadata is None:
            raise InventoryError("C normalization requires Clang AST metadata")
        existing = {symbol["id"] for symbol in normalized}
        for supplement in c_metadata["supplements"]:
            if supplement["id"] not in existing:
                normalized.append(supplement)
                existing.add(supplement["id"])
        by_identifier = {symbol["id"]: symbol for symbol in normalized}
        for identifier, overlay in c_availability_overlays.items():
            symbol = by_identifier.get(identifier)
            if symbol is None:
                raise InventoryError(
                    "Clang aggregate replacement has no live identity: "
                    f"{identifier}"
                )
            symbol["availability"] = canonical_availability(
                [*symbol["availability"], *overlay]
            )
            symbol["fingerprint"] = symbol_fingerprint(symbol)
    identifiers = [symbol["id"] for symbol in normalized]
    if len(identifiers) != len(set(identifiers)):
        raise InventoryError(f"{path.name} produced duplicate live symbol identities")
    return normalized


def make_symbol(
    *,
    language: str,
    kind: str,
    public_path: str,
    declaration: str,
    source: str,
    attributes: list[str] | None = None,
) -> dict[str, Any]:
    symbol: dict[str, Any] = {
        "id": stable_identifier(language, kind, public_path),
        "language": language,
        "kind": kind,
        "path": public_path,
        "declaration": declaration,
        "source": source,
        "origin": "source",
        "availability": [],
        "conformances": [],
        "attributes": sorted(set(attributes or [])),
    }
    symbol["fingerprint"] = symbol_fingerprint(symbol)
    return symbol


def symbol_graph_platform(path: Path) -> str:
    graph = load_json(path)
    module = graph.get("module", {})
    platform = module.get("platform", {}) if isinstance(module, dict) else {}
    operating_system = (
        platform.get("operatingSystem") if isinstance(platform, dict) else None
    )
    name = (
        operating_system.get("name")
        if isinstance(operating_system, dict)
        else None
    )
    if not isinstance(name, str) or not name:
        raise InventoryError(f"{path.name} does not identify its target platform")
    return name


def extract_live_surface() -> tuple[list[dict[str, Any]], str]:
    with tempfile.TemporaryDirectory(prefix="cyrinx-api-inventory-") as temporary:
        temporary_directory = Path(temporary)
        environment = extraction_environment(temporary_directory)
        swift_graph = extract_swift_symbol_graph(temporary_directory, environment)
        c_graph = extract_c_symbol_graph(temporary_directory, environment)
        c_ast = extract_c_ast(temporary_directory, environment)
        c_metadata = clang_ast_metadata(c_ast)
        symbols = normalize_graph(swift_graph, "swift")
        symbols.extend(normalize_graph(c_graph, "c", c_metadata=c_metadata))
        swift_platform = symbol_graph_platform(swift_graph)
    return sorted(symbols, key=lambda item: item["id"]), swift_platform


def compare_surface(
    recorded_symbols: list[dict[str, Any]],
    live_symbols: list[dict[str, Any]],
    swift_platform: str,
) -> None:
    live_identifiers = [symbol.get("id") for symbol in live_symbols]
    if len(live_identifiers) != len(set(live_identifiers)):
        duplicates = sorted(
            {
                str(identifier)
                for identifier in live_identifiers
                if live_identifiers.count(identifier) > 1
            }
        )
        raise InventoryError(
            "live extraction contains duplicate stable identities:\n"
            + "\n".join(f"- {identifier}" for identifier in duplicates)
        )
    recorded = {
        symbol["id"]: symbol
        for symbol in recorded_symbols
        if symbol["present"]
        and (
            symbol["language"] == "c"
            or "all" in symbol["platforms"]
            or swift_platform in symbol["platforms"]
        )
    }
    live = {symbol["id"]: symbol for symbol in live_symbols}
    missing = sorted(live.keys() - recorded.keys())
    stale = sorted(recorded.keys() - live.keys())
    changed: list[str] = []
    for identifier in sorted(recorded.keys() & live.keys()):
        for field in (*TECHNICAL_FIELDS, "fingerprint"):
            if recorded[identifier].get(field) != live[identifier].get(field):
                changed.append(
                    f"{identifier}: {field} changed from "
                    f"{recorded[identifier].get(field)!r} to "
                    f"{live[identifier].get(field)!r}"
                )

    if missing or stale or changed:
        details: list[str] = []
        if missing:
            details.append("unclassified live symbols:\n" + "\n".join(f"  + {v}" for v in missing))
        if stale:
            details.append(
                "inventory symbols absent from the live API:\n"
                + "\n".join(f"  - {value}" for value in stale)
            )
        if changed:
            details.append("changed declarations:\n" + "\n".join(f"  ! {v}" for v in changed))
        raise InventoryError(
            "public API/ABI inventory does not match the compiler-extracted surface.\n"
            + "\n".join(details)
            + "\nRun scripts/check-api-inventory.py --write, classify every "
            "UNCLASSIFIED entry, and review the resulting contract diff."
        )


def write_surface(
    manifest: dict[str, Any] | None,
    live_symbols: list[dict[str, Any]],
    swift_platform: str,
) -> None:
    if swift_platform != "macosx":
        raise InventoryError(
            "--write requires the macOS reference platform; "
            f"the active Swift target is {swift_platform!r}"
        )
    existing: dict[str, dict[str, Any]] = {}
    legacy_migration = (
        manifest is not None
        and manifest.get("schemaVersion") == EXPECTED_SCHEMA_VERSION - 1
    )
    if manifest is not None:
        raw_existing = manifest.get("symbols", [])
        if isinstance(raw_existing, list):
            existing = {
                stable_identifier(
                    str(symbol.get("language")),
                    str(symbol.get("kind")),
                    str(symbol.get("path")),
                ): symbol
                for symbol in raw_existing
                if isinstance(symbol, dict)
                and all(
                    isinstance(symbol.get(field), str)
                    for field in ("language", "kind", "path")
                )
            }

    merged: list[dict[str, Any]] = []
    live_identifiers: set[str] = set()
    for live_symbol in live_symbols:
        old = existing.get(live_symbol["id"], {})
        live_identifiers.add(live_symbol["id"])
        changed = bool(old) and any(
            old.get(field, [] if field in {"availability", "attributes"} else None)
            != live_symbol.get(field)
            for field in TECHNICAL_FIELDS
        )
        review_state = (
            "new_unclassified"
            if not old
            else (
                "changed_unreviewed"
                if changed
                else (
                    REVIEWED
                    if legacy_migration
                    else old.get("reviewState", REVIEWED)
                )
            )
        )
        record = {
            **live_symbol,
            "present": True,
            "reviewState": review_state,
            "disposition": old.get("disposition", "UNCLASSIFIED"),
            "decision": old.get(
                "decision",
                "Classify this symbol before committing the refreshed inventory.",
            ),
            "compatibility": old.get(
                "compatibility",
                "UNCLASSIFIED compatibility promise.",
            ),
            "platforms": old.get(
                "platforms",
                ["all"] if live_symbol["language"] == "c" else ["UNCLASSIFIED"],
            ),
        }
        if isinstance(old.get("replacement"), str):
            record["replacement"] = old["replacement"]
        merged.append(record)

    for identifier, old in existing.items():
        if identifier in live_identifiers:
            continue
        platforms = old.get("platforms", [])
        active_on_reference = (
            old.get("language") == "c"
            or "all" in platforms
            or swift_platform in platforms
        )
        if active_on_reference:
            tombstone = {
                **old,
                "id": identifier,
                "present": False,
                "reviewState": "removal_unreviewed",
                "availability": canonical_availability(old.get("availability")),
                "attributes": old.get("attributes", []),
                "conformances": old.get("conformances", []),
                "removalTarget": old.get(
                    "removalTarget",
                    old.get("replacement", "plan:C3-35"),
                ),
            }
            tombstone["fingerprint"] = symbol_fingerprint(tombstone)
            merged.append(tombstone)
        else:
            merged.append(old)

    merged.sort(key=lambda item: item["id"])
    replacement_targets = sorted(
        {
            value
            for symbol in merged
            for value in (symbol.get("replacement"), symbol.get("removalTarget"))
            if isinstance(value, str)
        }
    )
    counts = {
        language: {disposition: 0 for disposition in sorted(ALLOWED_DISPOSITIONS)}
        for language in sorted(ALLOWED_LANGUAGES)
    }
    for symbol in merged:
        language = symbol.get("language")
        disposition = symbol.get("disposition")
        if (
            symbol.get("present") is True
            and language in counts
            and disposition in ALLOWED_DISPOSITIONS
        ):
            counts[language][disposition] += 1

    output = {
        "schemaVersion": EXPECTED_SCHEMA_VERSION,
        "surface": {
            "swiftModule": "Cyrinx",
            "cHeaders": "Sources/CCyrinx/include/cyrinx/*.h",
            "swiftExtraction": (
                "swift-symbolgraph-extract --minimum-access-level public "
                "--skip-synthesized-members"
            ),
            "cExtraction": (
                "clang -extract-api -x c-header plus clang -ast-dump=json "
                "availability, source attributes, value/layout facts, and "
                "separate C namespace identities"
            ),
            "platformPolicy": (
                "macOS is the checked-in Swift reference; conditional Swift "
                "declarations not proven visible there and conditional public "
                "C declarations are prohibited until compiler-extracted unions exist"
            ),
            "valueSemanticsBoundary": (
                "Swift and C availability are fingerprinted; legacy Swift raw "
                "enum values and static constant initializers require C3-02 "
                "semantic fixtures"
            ),
        },
        "replacementTargets": replacement_targets,
        "classificationCounts": counts,
        "symbols": merged,
    }
    INVENTORY_PATH.write_text(
        json.dumps(output, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    try:
        display_path: Path | str = INVENTORY_PATH.relative_to(ROOT)
    except ValueError:
        display_path = INVENTORY_PATH
    print(f"wrote {len(merged)} symbols to {display_path}")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--structural-only",
        action="store_true",
        help="validate JSON schema, uniqueness, and classifications without compiler extraction",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="refresh technical fields; new symbols remain UNCLASSIFIED",
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    manifest: dict[str, Any] | None = None
    if INVENTORY_PATH.exists():
        manifest = load_json(INVENTORY_PATH)

    if arguments.write:
        live_symbols, swift_platform = extract_live_surface()
        write_surface(manifest, live_symbols, swift_platform)
        return 0

    if manifest is None:
        raise InventoryError(f"missing inventory: {INVENTORY_PATH.relative_to(ROOT)}")
    recorded_symbols = validate_manifest(manifest)
    validate_documented_counts(manifest)
    if not arguments.structural_only:
        live_symbols, swift_platform = extract_live_surface()
        compare_surface(recorded_symbols, live_symbols, swift_platform)

    counts: dict[str, int] = {}
    for symbol in recorded_symbols:
        if symbol["present"]:
            counts[symbol["language"]] = counts.get(symbol["language"], 0) + 1
    count_summary = ", ".join(f"{key}={counts[key]}" for key in sorted(counts))
    print(f"API inventory OK: total={len(recorded_symbols)}, {count_summary}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except InventoryError as error:
        print(f"API inventory check failed: {error}", file=sys.stderr)
        sys.exit(1)
