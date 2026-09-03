from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from getbible_bookmarks.build import render_api, write_tree
from getbible_bookmarks.cli import main
from getbible_bookmarks.sources import load_catalog, save_catalog
from tests.helpers import sample_catalog


class CliTests(unittest.TestCase):
    def run_cli(self, *arguments: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(arguments))
        return code, out.getvalue(), err.getvalue()

    def test_validate_build_check_and_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            repo = Path(folder)
            save_catalog(repo / "data", sample_catalog())
            code, out, _ = self.run_cli("--repo", str(repo), "validate")
            self.assertEqual(code, 0)
            self.assertIn("2 topics", out)
            code, _, err = self.run_cli("--repo", str(repo), "build", "--check")
            self.assertEqual(code, 1)
            self.assertIn("STALE", err)
            code, out, _ = self.run_cli("--repo", str(repo), "build")
            self.assertEqual(code, 0)
            self.assertTrue((repo / "v1" / "index.json").exists())
            code, out, _ = self.run_cli("--repo", str(repo), "build", "--check")
            self.assertEqual(code, 0)
            self.assertIn("Verified", out)

            bundle = repo / "bundle.json"
            bundle.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "topics": [
                            {"id": "mercy", "name": "Mercy", "color": "#ffffff", "aliases": []}
                        ],
                        "associations": {
                            "add": [{"topic_id": "mercy", "book": 19, "chapter": 23, "verse": 6}],
                            "remove": [],
                        },
                    }
                )
            )
            code, out, _ = self.run_cli(
                "--repo", str(repo), "import-bundle", "--check", str(bundle)
            )
            self.assertEqual(code, 0)
            self.assertIn("would create 1", out)
            self.assertFalse((repo / "data" / "links" / "mercy.json").exists())
            code, out, _ = self.run_cli("--repo", str(repo), "import-bundle", str(bundle))
            self.assertEqual(code, 0)
            catalog = load_catalog(repo / "data")
            self.assertEqual(catalog.catalog_version, 4)
            self.assertEqual(catalog.sorted_verses("mercy"), [(19, 23, 6)])
            code, _, _ = self.run_cli("--repo", str(repo), "build", "--check")
            self.assertEqual(code, 0)
            code, out, _ = self.run_cli("--repo", str(repo), "import-bundle", str(bundle))
            self.assertEqual(code, 0)
            self.assertIn("nothing to do", out)

            bad = repo / "bad.json"
            bad.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "topics": [],
                        "associations": {"add": [], "remove": []},
                        "note": "x",
                    }
                )
            )
            code, _, err = self.run_cli("--repo", str(repo), "import-bundle", str(bad))
            self.assertEqual(code, 1)
            self.assertIn("error:", err)

    def test_publish_and_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            repo = Path(folder)
            write_tree(repo / "v1", render_api(sample_catalog()))
            code, out, _ = self.run_cli(
                "--repo", str(repo), "publish", "--target", str(repo / "srv")
            )
            self.assertEqual(code, 0)
            self.assertTrue((repo / "srv" / "current" / "v1" / "index.json").exists())
            registry = repo / "contributors.json"
            code, out, _ = self.run_cli(
                "tokens",
                "--file",
                str(registry),
                "create",
                "--id",
                "jaco",
                "--name",
                "Jaco",
                "--email",
                "j@example.org",
            )
            self.assertEqual(code, 0)
            token = out.strip().splitlines()[-1]
            self.assertTrue(token.startswith("gbb_"))
            code, out, _ = self.run_cli("tokens", "--file", str(registry), "list")
            self.assertIn("jaco", out)
            self.assertNotIn(token, out)
            code, out, _ = self.run_cli(
                "tokens",
                "--file",
                str(registry),
                "set-role",
                "--id",
                "jaco",
                "--role",
                "maintainer",
            )
            self.assertIn("maintainer", out)
            code, out, _ = self.run_cli("tokens", "--file", str(registry), "revoke", "--id", "jaco")
            self.assertIn("Revoked", out)
            code, out, _ = self.run_cli("tokens", "--file", str(registry), "rotate", "--id", "jaco")
            self.assertIn("New token", out)
            code, _, err = self.run_cli(
                "tokens", "--file", str(registry), "revoke", "--id", "ghost"
            )
            self.assertEqual(code, 1)
            self.assertIn("does not exist", err)
