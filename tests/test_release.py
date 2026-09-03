from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from pathlib import Path

from getbible_bookmarks.build import render_api, write_tree
from getbible_bookmarks.release import ReleaseError, current_release, publish_release
from tests.helpers import sample_catalog


class ReleaseTests(unittest.TestCase):
    def test_publish_switch_and_prune(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            catalog = sample_catalog()
            source = root / "v1"
            target = root / "srv"
            checksums = []
            for step in range(4):
                catalog.add_verses("grace", [[1, 1, step + 1]])
                catalog.catalog_version += 1
                files = render_api(catalog)
                write_tree(source, files)
                release = publish_release(source, target, keep=2)
                checksums.append(release.name)
                current = current_release(target)
                assert current is not None
                self.assertEqual(current, release.resolve())
                self.assertEqual(
                    json.loads((current / "v1" / "index.json").read_text())["checksum"],
                    release.name,
                )
                with gzip.open(current / "v1" / "catalog.json.gz") as archive:
                    self.assertEqual(archive.read(), (current / "v1" / "catalog.json").read_bytes())
            remaining = {path.name for path in (target / "releases").iterdir()}
            self.assertIn(checksums[-1], remaining)
            self.assertLessEqual(len(remaining), 3)
            # Re-publishing the same checksum reuses the directory.
            self.assertEqual(publish_release(source, target).name, checksums[-1])

    def test_rejects_tree_without_index(self) -> None:
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(ReleaseError):
            publish_release(Path(folder), Path(folder) / "srv")
