import copy
import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts" / "check-api-inventory.py"
SPEC = importlib.util.spec_from_file_location("check_api_inventory", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {MODULE_PATH}")
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


def refresh_fingerprint(value):
    value["fingerprint"] = CHECKER.symbol_fingerprint(value)
    return value


def symbol(
    *,
    language="swift",
    kind="swift.struct",
    path="Example",
    declaration="struct Example",
    disposition="retain",
):
    value = {
        "id": CHECKER.stable_identifier(language, kind, path),
        "fingerprint": "",
        "language": language,
        "kind": kind,
        "path": path,
        "declaration": declaration,
        "source": (
            "Sources/Cyrinx/Example.swift"
            if language == "swift"
            else "Sources/CCyrinx/include/cyrinx/example.h"
        ),
        "origin": "source",
        "availability": [],
        "conformances": [],
        "attributes": [],
        "platforms": ["all"],
        "present": True,
        "reviewState": "reviewed",
        "disposition": disposition,
        "decision": "A reviewed classification.",
        "compatibility": "A reviewed compatibility promise.",
    }
    if disposition != "retain":
        value["replacement"] = "plan:C3-24"
    return refresh_fingerprint(value)


def manifest(*symbols):
    counts = {
        language: {disposition: 0 for disposition in sorted(CHECKER.ALLOWED_DISPOSITIONS)}
        for language in sorted(CHECKER.ALLOWED_LANGUAGES)
    }
    for value in symbols:
        if (
            value["present"]
            and value["language"] in counts
            and value["disposition"] in CHECKER.ALLOWED_DISPOSITIONS
        ):
            counts[value["language"]][value["disposition"]] += 1
    return {
        "schemaVersion": CHECKER.EXPECTED_SCHEMA_VERSION,
        "surface": {
            "swiftModule": "Cyrinx",
            "cHeaders": "Sources/CCyrinx/include/cyrinx/*.h",
            "swiftExtraction": "fixture",
            "cExtraction": "fixture",
            "platformPolicy": "fixture",
            "valueSemanticsBoundary": "fixture",
        },
        "replacementTargets": ["plan:C3-24"],
        "classificationCounts": counts,
        "symbols": list(symbols),
    }


class ManifestValidationTests(unittest.TestCase):
    def assert_invalid(self, value, expected):
        with self.assertRaises(CHECKER.InventoryError) as context:
            CHECKER.validate_manifest(value)
        self.assertIn(expected, str(context.exception))

    def test_accepts_a_complete_classified_symbol(self):
        symbols = CHECKER.validate_manifest(manifest(symbol()))
        self.assertEqual([item["path"] for item in symbols], ["Example"])

    def test_rejects_duplicate_semantic_identities(self):
        value = symbol()
        self.assert_invalid(manifest(value, copy.deepcopy(value)), "duplicate symbol id")

    def test_rejects_unclassified_or_pending_symbols(self):
        value = symbol()
        value["reviewState"] = "new_unclassified"
        self.assert_invalid(manifest(value), "pending review")

    def test_rejects_wildcard_paths(self):
        self.assert_invalid(
            manifest(symbol(path="cyrinx_phy_stub_*")),
            "must name one symbol",
        )

    def test_rejects_missing_or_unknown_replacement(self):
        value = symbol(disposition="replace")
        value["replacement"] = "api:DoesNotExist"
        self.assert_invalid(manifest(value), "not a declared replacement target")

    def test_rejects_a_fingerprint_mismatch(self):
        value = symbol()
        value["declaration"] = "struct Renamed"
        self.assert_invalid(manifest(value), ".fingerprint must be")

    def test_signature_change_preserves_stable_identity(self):
        before = symbol(declaration="func example(_ value: Int)")
        after = symbol(declaration="func example(_ value: String)")
        self.assertEqual(before["id"], after["id"])
        self.assertNotEqual(before["fingerprint"], after["fingerprint"])

    def test_rejects_unknown_language_and_kind(self):
        value = symbol()
        value["language"] = "swfit"
        refresh_fingerprint(value)
        self.assert_invalid(manifest(value), ".language must be one of")

    def test_rejects_placeholder_review_text(self):
        value = symbol()
        value["decision"] = "Classify this symbol before committing."
        self.assert_invalid(manifest(value), "unreviewed placeholder")

    def test_rejects_missing_surface_metadata(self):
        value = manifest(symbol())
        del value["surface"]
        self.assert_invalid(value, "surface must be an object")

    def test_rejects_an_unreviewed_platform_scope(self):
        value = symbol()
        value["platforms"] = ["UNCLASSIFIED"]
        self.assert_invalid(manifest(value), ".platforms must be")

    def test_requires_c_enum_value_and_order(self):
        value = symbol(
            language="c",
            kind="c.enum.case",
            path="example_t.EXAMPLE",
            declaration="EXAMPLE",
        )
        self.assert_invalid(manifest(value), ".ordinal must be")

    def test_accepts_c_deprecation_availability(self):
        value = symbol(
            language="c",
            kind="c.func",
            path="cyrinx_old",
            declaration="int cyrinx_old(void);",
        )
        value["availability"] = [
            {
                "domain": "*",
                "isUnconditionallyDeprecated": True,
            }
        ]
        refresh_fingerprint(value)
        symbols = CHECKER.validate_manifest(manifest(value))
        self.assertEqual(symbols[0]["availability"], value["availability"])

    def test_rejects_count_drift(self):
        value = manifest(symbol())
        value["classificationCounts"]["swift"]["retain"] = 2
        self.assert_invalid(value, "classificationCounts does not match")


class PlatformSurfaceTests(unittest.TestCase):
    @staticmethod
    def live_projection(record):
        return {
            key: record[key]
            for key in ("id", *CHECKER.TECHNICAL_FIELDS, "fingerprint")
            if key in record
        }

    def test_linux_ignores_a_macos_only_record(self):
        portable = symbol(path="Portable")
        macos_only = symbol(path="MacOnly")
        macos_only["platforms"] = ["macosx"]
        CHECKER.compare_surface(
            [portable, macos_only],
            [self.live_projection(portable)],
            "linux",
        )

    def test_macos_requires_a_macos_only_record(self):
        portable = symbol(path="Portable")
        macos_only = symbol(path="MacOnly")
        macos_only["platforms"] = ["macosx"]
        with self.assertRaises(CHECKER.InventoryError):
            CHECKER.compare_surface(
                [portable, macos_only],
                [self.live_projection(portable)],
                "macosx",
            )

    def test_rejects_duplicate_live_identities(self):
        value = symbol()
        live = self.live_projection(value)
        with self.assertRaisesRegex(CHECKER.InventoryError, "duplicate stable"):
            CHECKER.compare_surface([value], [live, copy.deepcopy(live)], "macosx")

    def test_fingerprints_swift_availability(self):
        recorded = symbol()
        live = self.live_projection(recorded)
        live["availability"] = [{"domain": "*", "isUnconditionallyDeprecated": True}]
        live["fingerprint"] = CHECKER.symbol_fingerprint(live)
        with self.assertRaisesRegex(CHECKER.InventoryError, "availability changed"):
            CHECKER.compare_surface([recorded], [live], "macosx")


class ClangMetadataTests(unittest.TestCase):
    @staticmethod
    def constant(name, value=None):
        result = {"kind": "EnumConstantDecl", "name": name}
        if value is not None:
            result["inner"] = [{"kind": "ConstantExpr", "value": str(value)}]
        return result

    @staticmethod
    def graph_symbol(kind, path, declaration, source, availability=None):
        return {
            "availability": availability or [],
            "declarationFragments": [
                {"kind": "text", "spelling": declaration},
            ],
            "identifier": {
                "interfaceLanguage": "c",
                "precise": f"c:fixture@{kind}@{path}",
            },
            "kind": {"identifier": kind},
            "location": {"uri": (ROOT / source).as_uri()},
            "pathComponents": path.split("."),
        }

    @staticmethod
    def normalize_c_graph(symbols, metadata):
        with tempfile.TemporaryDirectory() as directory:
            graph_path = Path(directory) / "CCyrinx.symbols.json"
            graph_path.write_text(
                json.dumps({"symbols": symbols}),
                encoding="utf-8",
            )
            return CHECKER.normalize_graph(
                graph_path,
                "c",
                c_metadata=metadata,
            )

    def test_derives_enum_values_field_order_attributes_and_opaque_types(self):
        source = "Sources/CCyrinx/include/cyrinx/fixture.h"
        ast = {
            "kind": "TranslationUnitDecl",
            "inner": [
                {
                    "id": "opaque",
                    "kind": "RecordDecl",
                    "name": "opaque_tag",
                    "loc": {"file": source},
                },
                {
                    "id": "opaque-alias",
                    "kind": "TypedefDecl",
                    "name": "opaque_t",
                    "loc": {},
                    "inner": [{"ownedTagDecl": {"id": "opaque"}}],
                },
                {
                    "id": "record",
                    "kind": "RecordDecl",
                    "name": "",
                    "completeDefinition": True,
                    "loc": {},
                    "inner": [
                        {"kind": "FieldDecl", "name": "first"},
                        {"kind": "FieldDecl", "name": "second"},
                        {"kind": "PackedAttr"},
                    ],
                },
                {
                    "id": "record-alias",
                    "kind": "TypedefDecl",
                    "name": "record_t",
                    "loc": {},
                    "inner": [{"ownedTagDecl": {"id": "record"}}],
                },
                {
                    "id": "enum",
                    "kind": "EnumDecl",
                    "name": "",
                    "loc": {},
                    "inner": [
                        self.constant("ZERO", 0),
                        self.constant("ONE"),
                        self.constant("SEVEN", 7),
                    ],
                },
                {
                    "id": "enum-alias",
                    "kind": "TypedefDecl",
                    "name": "example_t",
                    "loc": {},
                    "inner": [{"ownedTagDecl": {"id": "enum"}}],
                },
            ],
        }
        metadata = CHECKER.clang_ast_metadata(ast)
        self.assertEqual(metadata["fields"]["record_t.first"]["ordinal"], 0)
        self.assertEqual(metadata["fields"]["record_t.second"]["ordinal"], 1)
        self.assertEqual(metadata["recordAttributes"]["record_t"], ["PackedAttr"])
        self.assertEqual(
            metadata["enumCases"]["example_t.ONE"],
            {"enumValue": 1, "ordinal": 1},
        )
        self.assertEqual(
            metadata["enumCases"]["example_t.SEVEN"],
            {"enumValue": 7, "ordinal": 2},
        )
        identities = {(item["kind"], item["path"]) for item in metadata["supplements"]}
        self.assertEqual(
            identities,
            {
                ("c.tag.struct", "opaque_tag"),
                ("c.typealias", "opaque_t"),
            },
        )

    def test_preserves_c_availability_and_explicit_function_parameter_and_typedef_attrs(
        self,
    ):
        source = "Sources/CCyrinx/include/cyrinx/fixture.h"
        ast = {
            "kind": "TranslationUnitDecl",
            "inner": [
                {
                    "id": "function",
                    "kind": "FunctionDecl",
                    "name": "cyrinx_old",
                    "loc": {"file": source},
                    "inner": [
                        {
                            "kind": "ParmVarDecl",
                            "name": "buffer",
                            "inner": [{"kind": "NonNullAttr"}],
                        },
                        {"kind": "WarnUnusedResultAttr"},
                    ],
                },
                {
                    "id": "alias",
                    "kind": "TypedefDecl",
                    "name": "aligned_t",
                    "loc": {},
                    "inner": [
                        {"kind": "BuiltinType"},
                        {"kind": "AlignedAttr", "alignment": 16},
                    ],
                },
            ],
        }
        metadata = CHECKER.clang_ast_metadata(ast)
        deprecated = {
            "domain": "*",
            "isUnconditionallyDeprecated": True,
            "message": "Use cyrinx_current",
        }
        normalized = self.normalize_c_graph(
            [
                self.graph_symbol(
                    "c.func",
                    "cyrinx_old",
                    "int cyrinx_old(void);",
                    source,
                    [deprecated, deprecated],
                ),
                self.graph_symbol(
                    "c.typealias",
                    "aligned_t",
                    "typedef int aligned_t;",
                    source,
                ),
            ],
            metadata,
        )
        by_path = {item["path"]: item for item in normalized}
        function = by_path["cyrinx_old"]
        alias = by_path["aligned_t"]
        self.assertEqual(function["availability"], [deprecated])
        self.assertEqual(
            function["attributes"],
            [
                "WarnUnusedResultAttr",
                "parameter[0:buffer]:NonNullAttr",
            ],
        )
        self.assertEqual(
            alias["attributes"],
            ['AlignedAttr:{"alignment":16}'],
        )
        self.assertEqual(
            function["fingerprint"],
            CHECKER.symbol_fingerprint(function),
        )
        self.assertEqual(alias["fingerprint"], CHECKER.symbol_fingerprint(alias))

    def test_splits_complete_same_spelled_struct_and_enum_tag_typedef_pairs(self):
        source = "Sources/CCyrinx/include/cyrinx/fixture.h"
        ast = {
            "kind": "TranslationUnitDecl",
            "inner": [
                {
                    "id": "record",
                    "kind": "RecordDecl",
                    "name": "named",
                    "completeDefinition": True,
                    "loc": {"file": source},
                    "inner": [{"kind": "FieldDecl", "name": "x"}],
                },
                {
                    "id": "record-alias",
                    "kind": "TypedefDecl",
                    "name": "named",
                    "loc": {},
                    "inner": [{"ownedTagDecl": {"id": "record"}}],
                },
                {
                    "id": "enum",
                    "kind": "EnumDecl",
                    "name": "status",
                    "loc": {},
                    "inner": [self.constant("STATUS_OK", 0)],
                },
                {
                    "id": "enum-alias",
                    "kind": "TypedefDecl",
                    "name": "status",
                    "loc": {},
                    "inner": [{"ownedTagDecl": {"id": "enum"}}],
                },
            ],
        }
        metadata = CHECKER.clang_ast_metadata(ast)
        supplement_identities = {
            (item["kind"], item["path"])
            for item in metadata["supplements"]
        }
        self.assertEqual(
            supplement_identities,
            {
                ("c.tag.struct", "named"),
                ("c.typealias", "named"),
                ("c.tag.enum", "status"),
                ("c.typealias", "status"),
            },
        )

        deprecated = {
            "domain": "*",
            "isUnconditionallyDeprecated": True,
        }
        normalized = self.normalize_c_graph(
            [
                self.graph_symbol(
                    "c.struct",
                    "named",
                    "typedef struct named { int x; } named;",
                    source,
                    [deprecated],
                ),
                self.graph_symbol(
                    "c.typealias",
                    "named",
                    "typedef struct named named;",
                    source,
                ),
                self.graph_symbol("c.property", "named.x", "int x", source),
                self.graph_symbol(
                    "c.enum",
                    "status",
                    "typedef enum status { STATUS_OK = 0 } status;",
                    source,
                ),
                self.graph_symbol(
                    "c.typealias",
                    "status",
                    "typedef enum status status;",
                    source,
                ),
                self.graph_symbol(
                    "c.enum.case",
                    "status.STATUS_OK",
                    "STATUS_OK",
                    source,
                ),
            ],
            metadata,
        )
        identities = [(item["kind"], item["path"]) for item in normalized]
        self.assertNotIn(("c.struct", "named"), identities)
        self.assertNotIn(("c.enum", "status"), identities)
        for identity in supplement_identities:
            self.assertEqual(identities.count(identity), 1)
        by_identity = {
            (item["kind"], item["path"]): item
            for item in normalized
        }
        self.assertEqual(
            by_identity[("c.tag.struct", "named")]["availability"],
            [deprecated],
        )
        self.assertEqual(
            by_identity[("c.typealias", "named")]["availability"],
            [deprecated],
        )

    def test_merges_forward_declarations_with_later_tag_definitions(self):
        source = "Sources/CCyrinx/include/cyrinx/fixture.h"
        ast = {
            "kind": "TranslationUnitDecl",
            "inner": [
                {
                    "id": "record-forward",
                    "kind": "RecordDecl",
                    "name": "named",
                    "loc": {"file": source},
                },
                {
                    "id": "record-alias",
                    "kind": "TypedefDecl",
                    "name": "named",
                    "loc": {},
                    "inner": [{"ownedTagDecl": {"id": "record-forward"}}],
                },
                {
                    "id": "record-definition",
                    "kind": "RecordDecl",
                    "name": "named",
                    "previousDecl": "record-forward",
                    "completeDefinition": True,
                    "loc": {},
                    "inner": [
                        {"kind": "FieldDecl", "name": "x"},
                        {"kind": "PackedAttr"},
                    ],
                },
                {
                    "id": "enum-forward",
                    "kind": "EnumDecl",
                    "name": "status",
                    "loc": {},
                },
                {
                    "id": "enum-alias",
                    "kind": "TypedefDecl",
                    "name": "status",
                    "loc": {},
                    "inner": [{"ownedTagDecl": {"id": "enum-forward"}}],
                },
                {
                    "id": "enum-definition",
                    "kind": "EnumDecl",
                    "name": "status",
                    "previousDecl": "enum-forward",
                    "loc": {},
                    "inner": [
                        self.constant("STATUS_OK", 0),
                        {"kind": "DeprecatedAttr"},
                    ],
                },
            ],
        }
        metadata = CHECKER.clang_ast_metadata(ast)
        deprecated = {
            "domain": "*",
            "isUnconditionallyDeprecated": True,
        }
        normalized = self.normalize_c_graph(
            [
                self.graph_symbol(
                    "c.struct",
                    "named",
                    "struct named;",
                    source,
                    [deprecated],
                ),
                self.graph_symbol("c.property", "named.x", "int x", source),
                self.graph_symbol(
                    "c.enum",
                    "status",
                    "enum status : unsigned int;",
                    source,
                    [deprecated],
                ),
                self.graph_symbol(
                    "c.enum.case",
                    "status.STATUS_OK",
                    "STATUS_OK",
                    source,
                ),
            ],
            metadata,
        )
        by_identity = {
            (item["kind"], item["path"]): item
            for item in normalized
        }
        self.assertEqual(
            by_identity[("c.tag.struct", "named")]["attributes"],
            ["PackedAttr"],
        )
        self.assertEqual(
            by_identity[("c.tag.enum", "status")]["attributes"],
            ["DeprecatedAttr"],
        )
        self.assertEqual(
            by_identity[("c.typealias", "named")]["availability"],
            [deprecated],
        )
        self.assertEqual(
            by_identity[("c.typealias", "status")]["availability"],
            [deprecated],
        )

    def test_collects_all_conditional_macro_definitions(self):
        definitions = CHECKER.macro_definitions_from_text(
            """
            #if defined(_WIN32)
            #define CYRINX_API __declspec(dllexport)
            #else
            #define CYRINX_API __attribute__((visibility("default")))
            #endif
            """,
            "CYRINX_API",
        )
        self.assertEqual(len(definitions), 2)
        self.assertIn("__declspec(dllexport)", definitions[0])
        self.assertIn("visibility", definitions[1])

    def test_macro_fingerprint_includes_predicates(self):
        windows = CHECKER.macro_definitions_from_text(
            """
            #if defined(_WIN32)
            #define CYRINX_API visible
            #endif
            """,
            "CYRINX_API",
        )
        apple = CHECKER.macro_definitions_from_text(
            """
            #if defined(__APPLE__)
            #define CYRINX_API visible
            #endif
            """,
            "CYRINX_API",
        )
        windows_symbol = symbol(
            language="c",
            kind="c.macro",
            path="CYRINX_API",
            declaration=" || ".join(windows),
        )
        apple_symbol = symbol(
            language="c",
            kind="c.macro",
            path="CYRINX_API",
            declaration=" || ".join(apple),
        )
        self.assertNotEqual(windows_symbol["declaration"], apple_symbol["declaration"])
        self.assertNotEqual(windows_symbol["fingerprint"], apple_symbol["fingerprint"])

    def test_macro_events_cover_function_like_elif_else_and_undef(self):
        events = CHECKER.macro_definitions_from_text(
            """
            #if FIRST
            #define CYRINX_WRAP(value) (value)
            #elif SECOND
            #define CYRINX_WRAP(value) alternate(value)
            #else
            #undef CYRINX_WRAP
            #endif
            """,
            "CYRINX_WRAP",
        )
        self.assertEqual(len(events), 3)
        self.assertIn("[(FIRST)]", events[0])
        self.assertIn("!(FIRST) && (SECOND)", events[1])
        self.assertIn("!(FIRST || SECOND)", events[2])
        self.assertIn("#define CYRINX_WRAP(value)", events[0])
        self.assertIn("#undef CYRINX_WRAP", events[2])

    def test_macro_events_accept_comment_separated_directives(self):
        events = CHECKER.macro_definitions_from_text(
            """
            #if FIRST
            #define/**/CYRINX_WRAP(value) (value)
            #elif/**/ SECOND
            #/**/define CYRINX_WRAP(value) alternate(value)
            #else
            #undef/**/CYRINX_WRAP
            #endif
            """,
            "CYRINX_WRAP",
        )
        self.assertEqual(len(events), 3)
        self.assertIn("[(FIRST)]", events[0])
        self.assertIn("!(FIRST) && (SECOND)", events[1])
        self.assertIn("!(FIRST || SECOND)", events[2])
        self.assertIn("#define CYRINX_WRAP(value)", events[0])
        self.assertIn("#undef CYRINX_WRAP", events[2])

    def test_rejects_a_conditional_macro_missing_from_reference_extraction(self):
        source = """
            #if defined(_WIN32)
            #define CYRINX_WINDOWS_ONLY(value) (value)
            #endif
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            header = root / "Sources" / "CCyrinx" / "include" / "cyrinx" / "fixture.h"
            header.parent.mkdir(parents=True)
            header.write_text(source, encoding="utf-8")
            graph = root / "CCyrinx.symbols.json"
            graph.write_text(
                json.dumps(
                    {
                        "symbols": [
                            {
                                "kind": {"identifier": "c.macro"},
                                "pathComponents": ["CYRINX_ALWAYS"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            original_root = CHECKER.ROOT
            CHECKER.ROOT = root
            try:
                with self.assertRaisesRegex(
                    CHECKER.InventoryError,
                    "CYRINX_WINDOWS_ONLY",
                ):
                    CHECKER.enforce_c_macro_union_policy([header], graph)
            finally:
                CHECKER.ROOT = original_root


class RefreshTests(unittest.TestCase):
    def test_missing_symbol_becomes_a_pending_tombstone(self):
        recorded = symbol(disposition="replace")
        value = manifest(recorded)
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "inventory.json"
            original = CHECKER.INVENTORY_PATH
            CHECKER.INVENTORY_PATH = destination
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    CHECKER.write_surface(value, [], "macosx")
            finally:
                CHECKER.INVENTORY_PATH = original
            refreshed = json.loads(destination.read_text(encoding="utf-8"))
        self.assertEqual(len(refreshed["symbols"]), 1)
        tombstone = refreshed["symbols"][0]
        self.assertFalse(tombstone["present"])
        self.assertEqual(tombstone["reviewState"], "removal_unreviewed")

    def test_changed_fingerprint_requires_review_but_preserves_disposition(self):
        recorded = symbol(declaration="struct Example")
        value = manifest(recorded)
        live = symbol(declaration="struct Example: Sendable")
        live = {
            key: live[key]
            for key in ("id", *CHECKER.TECHNICAL_FIELDS, "fingerprint")
            if key in live
        }
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "inventory.json"
            original = CHECKER.INVENTORY_PATH
            CHECKER.INVENTORY_PATH = destination
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    CHECKER.write_surface(value, [live], "macosx")
            finally:
                CHECKER.INVENTORY_PATH = original
            refreshed = json.loads(destination.read_text(encoding="utf-8"))
        changed = refreshed["symbols"][0]
        self.assertEqual(changed["reviewState"], "changed_unreviewed")
        self.assertEqual(changed["disposition"], "retain")

    def test_legacy_schema_does_not_auto_approve_technical_drift(self):
        recorded = symbol(declaration="struct Example")
        value = manifest(recorded)
        value["schemaVersion"] = 1
        live = symbol(declaration="struct Example: Sendable")
        live = {
            key: live[key]
            for key in ("id", *CHECKER.TECHNICAL_FIELDS, "fingerprint")
            if key in live
        }
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "inventory.json"
            original = CHECKER.INVENTORY_PATH
            CHECKER.INVENTORY_PATH = destination
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    CHECKER.write_surface(value, [live], "macosx")
            finally:
                CHECKER.INVENTORY_PATH = original
            refreshed = json.loads(destination.read_text(encoding="utf-8"))
        self.assertEqual(
            refreshed["symbols"][0]["reviewState"],
            "changed_unreviewed",
        )

    def test_legacy_schema_may_migrate_an_identical_record_as_reviewed(self):
        recorded = symbol(declaration="struct Example")
        value = manifest(recorded)
        value["schemaVersion"] = 1
        live = {
            key: recorded[key]
            for key in ("id", *CHECKER.TECHNICAL_FIELDS, "fingerprint")
            if key in recorded
        }
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "inventory.json"
            original = CHECKER.INVENTORY_PATH
            CHECKER.INVENTORY_PATH = destination
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    CHECKER.write_surface(value, [live], "macosx")
            finally:
                CHECKER.INVENTORY_PATH = original
            refreshed = json.loads(destination.read_text(encoding="utf-8"))
        self.assertEqual(refreshed["symbols"][0]["reviewState"], "reviewed")


class ExclusivePlatformPolicyTests(unittest.TestCase):
    def test_rejects_i_os_only_public_declaration(self):
        lines = CHECKER.exclusive_public_lines(
            """
            #if os(iOS)
            public struct IOSOnly {}
            #endif
            """
        )
        self.assertEqual(lines, [3])

    def test_rejects_a_commented_attributed_i_os_only_public_declaration(self):
        lines = CHECKER.exclusive_public_lines(
            """
            #if os(iOS)
            /* legal leading comment */ @MainActor final public class Hidden {}
            #endif
            """
        )
        self.assertEqual(lines, [3])

    def test_rejects_interstitial_comments_before_i_os_only_public_access(self):
        cases = (
            (
                """
            #if os(iOS)
            @MainActor /* legal */ final public class Hidden {}
            #endif
            """,
                [3],
            ),
            (
                """
            #if os(iOS)
            final /* legal */ public class Hidden {}
            #endif
            """,
                [3],
            ),
            (
                """
            #if os(iOS)
            final /* outer
                /* nested */ legal */ public class Hidden {}
            #endif
            """,
                [4],
            ),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(CHECKER.exclusive_public_lines(source), expected)

    def test_ignores_fake_conditionals_inside_multiline_swift_strings(self):
        lines = CHECKER.exclusive_public_lines(
            '''
            #if os(iOS)
            let decoy = """
            #endif
            #if os(macOS)
            """
            public struct Hidden {}
            #endif
            '''
        )
        self.assertEqual(lines, [7])

    def test_ignores_comment_markers_after_an_escaped_swift_quote(self):
        lines = CHECKER.exclusive_public_lines(
            r'''
            #if os(iOS)
            @available(*, message: "not \" // a comment") final public class Hidden {}
            #endif
            '''
        )
        self.assertEqual(lines, [3])

    def test_ignores_comment_markers_after_an_escaped_raw_swift_quote(self):
        lines = CHECKER.exclusive_public_lines(
            r'''
            #if os(iOS)
            let text = #"start \#"#foo//bar"#; public struct Hidden {}
            #endif
            '''
        )
        self.assertEqual(lines, [3])

    def test_rejects_semicolon_separated_i_os_only_public_declaration(self):
        lines = CHECKER.exclusive_public_lines(
            """
            #if os(iOS)
            let decoy = 0; public struct Hidden {}
            #endif
            """
        )
        self.assertEqual(lines, [3])

    def test_ignores_comment_markers_inside_extended_swift_regex_literals(self):
        cases = (
            r"""
            #if os(iOS)
            let pattern = #/foo//bar/#; public struct Hidden {}
            #endif
            """,
            r"""
            #if os(iOS)
            let pattern = ##/foo/*bar/##; public struct Hidden {}
            #endif
            """,
            r"""
            #if os(iOS)
            let pattern = #/start\/#foo//bar/#; public struct Hidden {}
            #endif
            """,
        )
        for source in cases:
            with self.subTest(source=source):
                self.assertEqual(CHECKER.exclusive_public_lines(source), [3])

    def test_ignores_comment_markers_inside_bare_swift_regex_literals(self):
        lines = CHECKER.exclusive_public_lines(
            r"""
            #if os(iOS)
            let pattern = /foo\/\/bar/; public struct Hidden {}
            #endif
            """
        )
        self.assertEqual(lines, [3])

    def test_does_not_treat_swift_division_as_a_regex_literal(self):
        lines = CHECKER.exclusive_public_lines(
            """
            #if os(iOS)
            let ratio = numerator / denominator; public struct Hidden {}
            #endif
            """
        )
        self.assertEqual(lines, [3])

    def test_does_not_treat_force_unwrap_division_as_a_multiline_regex(self):
        lines = CHECKER.exclusive_public_lines(
            """
            #if os(iOS)
            let x: Double? = 4
            let y = 2.0
            let a = x! / y
            public struct Hidden {}
            let p = 4.0, q = 2.0
            let b = p / q
            #endif
            """
        )
        self.assertEqual(lines, [6])

    def test_rejects_a_multiline_attributed_i_os_only_public_declaration(self):
        lines = CHECKER.exclusive_public_lines(
            """
            #if os(iOS)
            @available(
                iOS 17, *) public struct Hidden {}
            #endif
            """
        )
        self.assertEqual(lines, [4])

    def test_allows_macos_or_i_os_public_declaration_visible_to_reference(self):
        lines = CHECKER.exclusive_public_lines(
            """
            #if os(macOS) || os(iOS)
            public struct AppleAPI {}
            #endif
            """
        )
        self.assertEqual(lines, [])

    def test_rejects_negated_unknown_attributed_and_reordered_swift_declarations(self):
        cases = {
            "negated macOS": """
                #if !os(macOS)
                public struct Hidden {}
                #endif
            """,
            "attributed iOS": """
                #if os(iOS)
                @MainActor public struct Hidden {}
                #endif
            """,
            "reordered modifier": """
                #if os(iOS)
                final public class Hidden {}
                #endif
            """,
            "unknown condition": """
                #if CYRINX_IOS_ONLY
                public struct Hidden {}
                #endif
            """,
        }
        for label, source in cases.items():
            with self.subTest(label=label):
                self.assertEqual(CHECKER.exclusive_public_lines(source), [3])

    def test_rejects_a_windows_only_c_declaration_but_allows_macro_variants(self):
        source = """
            #if defined(_WIN32)
            #define CYRINX_API __declspec(dllexport)
            CYRINX_API int cyrinx_windows_only(void);
            #else
            #define CYRINX_API __attribute__((visibility("default")))
            #endif
        """
        self.assertEqual(CHECKER.conditional_c_api_lines(source), [4])


if __name__ == "__main__":
    unittest.main()
