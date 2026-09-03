from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from getbible_bookmarks.build import (
    RESOURCES,
    catalog_checksum,
    render_api,
    stale_paths,
    write_tree,
)
from getbible_bookmarks.canon import CHAPTER_COUNT
from getbible_bookmarks.model import CatalogError
from getbible_bookmarks.sources import load_catalog, render_sources, save_catalog
from tests.helpers import sample_catalog

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


class SourceRoundTripTests(unittest.TestCase):
    def test_save_and_load_round_trip(self) -> None:
        catalog = sample_catalog()
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            changed = save_catalog(data, catalog)
            self.assertIn("topics.json", changed)
            self.assertIn("links/grace.json", changed)
            self.assertIn("locales/fr.json", changed)
            loaded = load_catalog(data)
            self.assertEqual(render_sources(loaded), render_sources(catalog))
            self.assertEqual(save_catalog(data, loaded), [])
            # Retiring removes the link file and rewrites the topic list.
            loaded.retire_topic("grace")
            changed = save_catalog(data, loaded)
            self.assertIn("links/grace.json", changed)
            self.assertFalse((data / "links" / "grace.json").exists())
            reloaded = load_catalog(data)
            self.assertEqual(list(reloaded.retired), ["grace"])

    def test_load_rejects_defects(self) -> None:
        catalog = sample_catalog()
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            save_catalog(data, catalog)
            links = data / "links" / "grace.json"
            document = json.loads(links.read_text())
            document["verses"].append(document["verses"][0])
            links.write_text(json.dumps(document))
            with self.assertRaisesRegex(CatalogError, "ascending"):
                load_catalog(data)
            links.write_text(json.dumps({"schema_version": 1, "topic": "ghost", "verses": []}))
            with self.assertRaisesRegex(CatalogError, "must be ghost.json"):
                load_catalog(data)
            links.unlink()
            (data / "links" / "ghost.json").write_text(
                json.dumps({"schema_version": 1, "topic": "ghost", "verses": []})
            )
            with self.assertRaisesRegex(CatalogError, "unknown topic"):
                load_catalog(data)
            (data / "links" / "ghost.json").unlink()
            (data / "topics.json").write_text("{not json")
            with self.assertRaisesRegex(CatalogError, "not valid"):
                load_catalog(data)
            (data / "topics.json").unlink()
            with self.assertRaisesRegex(CatalogError, "missing"):
                load_catalog(data)

    def test_repository_sources_load_and_are_normalized(self) -> None:
        catalog = load_catalog(REPOSITORY_ROOT / "data")
        self.assertGreaterEqual(len(catalog.topics), 61)
        self.assertGreaterEqual(catalog.link_count(), 2155)
        self.assertGreaterEqual(len(catalog.locales), 68)
        for relative, content in render_sources(catalog).items():
            self.assertEqual((REPOSITORY_ROOT / "data" / relative).read_bytes(), content, relative)


class BuildTests(unittest.TestCase):
    def test_render_is_deterministic_and_complete(self) -> None:
        catalog = sample_catalog()
        files = render_api(catalog)
        self.assertEqual(files, render_api(sample_catalog()))
        # 66 book files + every canonical chapter + top-level documents + topics + locales.
        expected = 66 + CHAPTER_COUNT + 7 + 2 + 3
        self.assertEqual(len(files), expected)
        index = json.loads(files["index.json"])
        self.assertEqual(index["counts"], {"topics": 2, "verses": 5, "locales": 3, "retired": 0})
        self.assertEqual(index["checksum"], catalog_checksum(files))
        self.assertEqual(index["resources"], dict(RESOURCES))
        self.assertEqual(index["locales"], ["de", "en", "fr"])
        topics = json.loads(files["topics.json"])
        self.assertEqual(
            topics["topics"][1],
            {
                "id": "grace",
                "name": "Grace",
                "color": "#bbf7d0",
                "aliases": [],
                "default": True,
                "verses": 3,
            },
        )
        topic = json.loads(files["topics/grace.json"])
        self.assertEqual(topic["verses"], [[45, 5, 20], [49, 2, 8], [49, 2, 9]])
        self.assertEqual(topic["names"], {"de": "Gnade", "en": "Grace", "fr": "Grâce"})
        chapter = json.loads(files["verses/49/2.json"])
        self.assertEqual(
            chapter,
            {
                "schema_version": 1,
                "book": 49,
                "chapter": 2,
                "verses": {"8": ["grace"], "9": ["grace"]},
            },
        )
        book = json.loads(files["verses/45.json"])
        self.assertEqual(book["chapters"], {"2": {"5": ["gods-judgment"]}, "5": {"20": ["grace"]}})
        empty = json.loads(files["verses/1/1.json"])
        self.assertEqual(empty["verses"], {})
        english = json.loads(files["locales/en.json"])
        self.assertEqual(english["topics"], {"gods-judgment": "God's Judgment", "grace": "Grace"})
        locales = json.loads(files["locales.json"])
        self.assertEqual(locales["locales"][0], {"code": "de", "name": "German", "topics": 1})
        everything = json.loads(files["all.json"])
        self.assertEqual(everything["locales"]["fr"]["topics"]["grace"], "Grâce")
        checksums = json.loads(files["checksums.json"])
        self.assertEqual(set(checksums["files"]), set(files) - {"checksums.json"})
        self.assertEqual(json.loads(files["retired.json"])["topics"], [])

    def test_retired_topics_are_published_as_tombstones(self) -> None:
        catalog = sample_catalog()
        catalog.retire_topic("grace", reason="private note")
        files = render_api(catalog)
        retired = json.loads(files["retired.json"])["topics"]
        self.assertEqual(retired, [{"id": "grace", "name": "Grace", "retired_in": 4}])
        self.assertNotIn("topics/grace.json", files)
        self.assertNotIn(b"private note", files["catalog.json"])

    def test_write_tree_and_stale_paths(self) -> None:
        catalog = sample_catalog()
        files = render_api(catalog)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "v1"
            write_tree(root, files)
            self.assertEqual(stale_paths(root, files), [])
            (root / "extra.json").write_text("{}")
            (root / "index.json").write_text("{}")
            self.assertEqual(stale_paths(root, files), ["extra.json", "index.json"])
            write_tree(root, files)
            self.assertEqual(stale_paths(root, files), [])
            self.assertFalse((root / "extra.json").exists())

    def test_committed_tree_matches_sources(self) -> None:
        root = REPOSITORY_ROOT / "v1"
        if not root.is_dir():
            self.skipTest("v1 tree not generated yet")
        files = render_api(load_catalog(REPOSITORY_ROOT / "data"))
        self.assertEqual(stale_paths(root, files), [])
