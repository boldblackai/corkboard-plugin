#!/usr/bin/env python3
"""Unit tests for scripts/validate.py.

Covers the VS Code / Open VSX extension manifest checks (added when the repo
started shipping a VSIX) and re-runs the whole validator against this repo so a
regression in the pre-existing checks is caught here too.

Stdlib only; run directly:  python3 tests/test_validate.py
"""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load_validator():
    spec = importlib.util.spec_from_file_location(
        "corkboard_validate", REPO / "scripts" / "validate.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validate = _load_validator()


def valid_manifest() -> dict:
    """A minimal-but-valid extension manifest (mirrors the real one)."""
    return {
        "name": "corkboard",
        "displayName": "Corkboard",
        "description": "Corkboard wiki integration for VS Code and Cursor.",
        "version": "0.1.0",
        "publisher": "boldblackai",
        "license": "MIT",
        "engines": {"vscode": "^1.101.0"},
        "activationEvents": ["onStartupFinished"],
        "main": "./out/extension.js",
        "icon": "assets/icon.png",
        "contributes": {
            "mcpServerDefinitionProviders": [
                {"id": "corkboardMcpProvider", "label": "Corkboard"}
            ]
        },
        "devDependencies": {"@types/vscode": "^1.101.0"},
    }


class Fixture:
    """Temp repo root holding a package.json plus the files it references."""

    def __init__(self, manifest: dict | str | None = None):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        if manifest is None:
            manifest = valid_manifest()
        if isinstance(manifest, str):
            (self.root / "package.json").write_text(manifest)
        else:
            (self.root / "package.json").write_text(json.dumps(manifest))
        (self.root / "out").mkdir()
        (self.root / "out" / "extension.js").write_text("// built\n")
        (self.root / "src").mkdir()
        (self.root / "src" / "extension.ts").write_text(
            'vscode.lm.registerMcpServerDefinitionProvider("corkboardMcpProvider", x);\n'
            'const uri = "https://corkboard.wiki/mcp";\n'
            "new vscode.McpHttpServerDefinition('Corkboard', uri);\n"
        )
        (self.root / "assets").mkdir()
        (self.root / "assets" / "icon.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    def errors(self) -> list[str]:
        return validate.package_manifest_errors(self.root)

    def close(self) -> None:
        self._tmp.cleanup()


class PackageManifestTest(unittest.TestCase):
    def assert_mentions(self, errors: list[str], needle: str) -> None:
        self.assertTrue(
            any(needle in e for e in errors),
            f"expected an error mentioning {needle!r}, got {errors}",
        )

    def test_valid_fixture_has_no_errors(self):
        fx = Fixture()
        try:
            self.assertEqual(fx.errors(), [])
        finally:
            fx.close()

    def test_repo_manifest_is_valid(self):
        self.assertEqual(validate.package_manifest_errors(REPO), [])

    def test_missing_required_field(self):
        for field in ("name", "displayName", "description", "version", "publisher", "license"):
            with self.subTest(field=field):
                manifest = valid_manifest()
                del manifest[field]
                fx = Fixture(manifest)
                try:
                    self.assert_mentions(fx.errors(), field)
                finally:
                    fx.close()

    def test_bad_engines_vscode(self):
        manifest = valid_manifest()
        manifest["engines"] = {"vscode": "1.101"}
        fx = Fixture(manifest)
        try:
            self.assert_mentions(fx.errors(), "engines.vscode")
        finally:
            fx.close()

    def test_runtime_dependencies_rejected(self):
        manifest = valid_manifest()
        manifest["dependencies"] = {"axios": "^1.0.0"}
        fx = Fixture(manifest)
        try:
            self.assert_mentions(fx.errors(), "dependencies")
        finally:
            fx.close()

    def test_main_entry_must_exist(self):
        manifest = valid_manifest()
        manifest["main"] = "./out/missing.js"
        fx = Fixture(manifest)
        try:
            self.assert_mentions(fx.errors(), "main")
        finally:
            fx.close()

    def test_icon_must_exist(self):
        manifest = valid_manifest()
        manifest["icon"] = "assets/nope.png"
        fx = Fixture(manifest)
        try:
            self.assert_mentions(fx.errors(), "icon")
        finally:
            fx.close()

    def test_activation_event_required(self):
        manifest = valid_manifest()
        manifest["activationEvents"] = ["onCommand:x"]
        fx = Fixture(manifest)
        try:
            self.assert_mentions(fx.errors(), "onStartupFinished")
        finally:
            fx.close()

    def test_mcp_provider_contribution_required(self):
        manifest = valid_manifest()
        manifest["contributes"] = {"mcpServerDefinitionProviders": []}
        fx = Fixture(manifest)
        try:
            self.assert_mentions(fx.errors(), "corkboardMcpProvider")
        finally:
            fx.close()

    def test_invalid_json(self):
        fx = Fixture("{not json")
        try:
            self.assert_mentions(fx.errors(), "invalid JSON")
        finally:
            fx.close()

    def test_missing_manifest(self):
        fx = Fixture()
        try:
            (fx.root / "package.json").unlink()
            self.assert_mentions(fx.errors(), "package.json")
        finally:
            fx.close()

    def test_source_must_register_provider_and_uri(self):
        manifest = valid_manifest()
        fx = Fixture(manifest)
        try:
            (fx.root / "src" / "extension.ts").write_text("// nothing here\n")
            errors = fx.errors()
            self.assert_mentions(errors, "src/extension.ts")
        finally:
            fx.close()


class WholeValidatorTest(unittest.TestCase):
    def test_main_passes_on_this_repo(self):
        self.assertEqual(validate.main(), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
