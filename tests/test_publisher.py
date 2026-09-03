from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from getbible_bookmarks.build import render_api, write_tree
from getbible_bookmarks.model import Catalog
from getbible_bookmarks.publisher import (
    Actor,
    DivergedRepositoryError,
    Publisher,
    PublisherConfig,
    PublishError,
    VersionConflictError,
)
from getbible_bookmarks.release import current_release
from getbible_bookmarks.sources import load_catalog, save_catalog
from tests.helpers import sample_catalog

ACTOR = Actor(id="jaco", name="Brother Jaco", email="jaco@example.org")


def git(cwd: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": str(cwd),
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.org",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.org",
        },
    ).stdout.strip()


def quiet_git(repository: Path) -> None:
    """Disable background maintenance so no detached git process outlives a test."""
    for key, value in (
        ("gc.auto", "0"),
        ("gc.autoDetach", "false"),
        ("maintenance.auto", "false"),
        ("receive.autogc", "false"),
        ("fetch.writeCommitGraph", "false"),
    ):
        git(repository, "config", key, value)


class RepositoryFixture:
    """A bare origin plus a working clone seeded with the sample catalogue."""

    def __init__(self, root: Path, catalog: Catalog | None = None) -> None:
        self.root = root
        self.origin = root / "origin.git"
        self.clone = root / "clone"
        git(root, "init", "--quiet", "--bare", "--initial-branch=main", str(self.origin))
        quiet_git(self.origin)
        git(root, "clone", "--quiet", str(self.origin), str(self.clone))
        quiet_git(self.clone)
        git(self.clone, "checkout", "--quiet", "-b", "main")
        catalog = catalog or sample_catalog()
        save_catalog(self.clone / "data", catalog)
        write_tree(self.clone / "v1", render_api(catalog))
        git(self.clone, "add", "-A")
        git(self.clone, "commit", "--quiet", "-m", "Seed")
        git(self.clone, "push", "--quiet", "-u", "origin", "main")

    def publisher(self, **overrides: object) -> Publisher:
        config = PublisherConfig(repo_dir=self.clone, remote="origin", branch="main", **overrides)  # type: ignore[arg-type]
        return Publisher(config)

    def second_clone(self) -> Path:
        other = self.root / "other"
        git(self.root, "clone", "--quiet", str(self.origin), str(other))
        quiet_git(other)
        return other


class PublisherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.fixture = RepositoryFixture(Path(self.folder.name))

    def tearDown(self) -> None:
        self.folder.cleanup()

    def test_mutation_commits_pushes_and_publishes(self) -> None:
        release_dir = Path(self.folder.name) / "srv"
        publisher = self.fixture.publisher(release_dir=release_dir)
        result = publisher.mutate(
            ACTOR, "Add verses to grace", lambda c: {"added": c.add_verses("grace", [[40, 1, 1]])}
        )
        self.assertTrue(result.changed)
        self.assertEqual(result.catalog_version, 4)
        self.assertTrue(result.pushed)
        self.assertEqual(result.as_dict()["added"], 1)
        head = git(self.fixture.clone, "log", "-1", "--format=%an <%ae>|%cn <%ce>|%s|%b")
        self.assertTrue(
            head.startswith(
                "Brother Jaco <jaco@example.org>|getBible Bookmarks Service <bookmarks@getbible.net>|Add verses to grace|"
            )
        )
        self.assertIn("Contributor: jaco", head)
        self.assertIn("Catalog-Version: 4", head)
        self.assertEqual(git(self.fixture.origin, "rev-parse", "main"), result.commit)
        self.assertEqual(git(self.fixture.clone, "status", "--porcelain"), "")
        reloaded = load_catalog(self.fixture.clone / "data")
        self.assertIn((40, 1, 1), reloaded.links["grace"])
        release = current_release(release_dir)
        self.assertIsNotNone(release)
        assert release is not None
        self.assertEqual(release.name, result.checksum)
        self.assertTrue((release / "v1" / "catalog.json.gz").exists())
        state = publisher.state()
        self.assertEqual(state["catalog_version"], 4)
        self.assertFalse(state["output_stale"])
        self.assertFalse(state["git"]["push_pending"])

    def test_noop_mutation_does_not_commit(self) -> None:
        publisher = self.fixture.publisher()
        before = git(self.fixture.clone, "rev-parse", "HEAD")
        result = publisher.mutate(
            ACTOR, "Add existing verse", lambda c: c.add_verses("grace", [[49, 2, 8]])
        )
        self.assertFalse(result.changed)
        self.assertEqual(result.catalog_version, 3)
        self.assertEqual(git(self.fixture.clone, "rev-parse", "HEAD"), before)

    def test_version_conflict(self) -> None:
        publisher = self.fixture.publisher()
        with self.assertRaises(VersionConflictError):
            publisher.mutate(
                ACTOR,
                "x",
                lambda c: c.add_verses("grace", [[40, 1, 1]]),
                expected_catalog_version=2,
            )

    def test_integrates_remote_commits_before_mutating(self) -> None:
        other = self.fixture.second_clone()
        catalog = load_catalog(other / "data")
        catalog.add_verses("gods-judgment", [[66, 20, 12]])
        catalog.catalog_version += 1
        save_catalog(other / "data", catalog)
        write_tree(other / "v1", render_api(catalog))
        git(other, "add", "-A")
        git(other, "commit", "--quiet", "-m", "Remote change")
        git(other, "push", "--quiet", "origin", "main")

        publisher = self.fixture.publisher()
        result = publisher.mutate(
            ACTOR, "Local change", lambda c: c.add_verses("grace", [[40, 1, 1]])
        )
        self.assertEqual(result.catalog_version, 5)
        merged = load_catalog(self.fixture.clone / "data")
        self.assertIn((66, 20, 12), merged.links["gods-judgment"])
        self.assertIn((40, 1, 1), merged.links["grace"])

    def test_rebases_local_commits_and_reports_conflicts(self) -> None:
        publisher = self.fixture.publisher(push=False)
        publisher.mutate(ACTOR, "Unpushed", lambda c: c.add_verses("grace", [[40, 1, 1]]))
        self.assertIsNone(publisher.retry_push())

        other = self.fixture.second_clone()
        catalog = load_catalog(other / "data")
        catalog.set_locale_names("de", {"grace": "Die Gnade"})
        catalog.catalog_version += 1
        save_catalog(other / "data", catalog)
        write_tree(other / "v1", render_api(catalog))
        git(other, "add", "-A")
        git(other, "commit", "--quiet", "-m", "Remote locale change")
        git(other, "push", "--quiet", "origin", "main")

        # Both sides bumped catalog_version in topics.json: a textual conflict.
        with self.assertRaises(DivergedRepositoryError):
            publisher.mutate(ACTOR, "Second", lambda c: c.add_verses("grace", [[40, 1, 2]]))
        self.assertEqual(git(self.fixture.clone, "status", "--porcelain"), "")

    def test_refuses_dirty_tree_and_wrong_branch(self) -> None:
        publisher = self.fixture.publisher()
        (self.fixture.clone / "data" / "stray.json").write_text("{}")
        with self.assertRaises(PublishError):
            publisher.mutate(ACTOR, "x", lambda c: c.add_verses("grace", [[40, 1, 1]]))
        (self.fixture.clone / "data" / "stray.json").unlink()
        git(self.fixture.clone, "checkout", "--quiet", "-b", "feature")
        with self.assertRaises(PublishError):
            publisher.mutate(ACTOR, "x", lambda c: c.add_verses("grace", [[40, 1, 1]]))

    def test_push_failure_is_retried(self) -> None:
        publisher = self.fixture.publisher()
        # Break the remote so the push fails, then restore it.
        git(
            self.fixture.clone,
            "remote",
            "set-url",
            "--push",
            "origin",
            str(self.fixture.root / "missing.git"),
        )
        result = publisher.mutate(ACTOR, "x", lambda c: c.add_verses("grace", [[40, 1, 1]]))
        self.assertFalse(result.pushed)
        self.assertTrue(publisher.push_pending)
        git(self.fixture.clone, "remote", "set-url", "--push", "origin", str(self.fixture.origin))
        self.assertTrue(publisher.retry_push())
        self.assertFalse(publisher.push_pending)
        self.assertEqual(git(self.fixture.origin, "rev-parse", "main"), result.commit)

    def test_sync_verifies_tree(self) -> None:
        publisher = self.fixture.publisher()
        state = publisher.sync()
        self.assertEqual(state["catalog_version"], 3)
        (self.fixture.clone / "v1" / "index.json").write_text("{}")
        git(self.fixture.clone, "commit", "--quiet", "-am", "Break tree")
        with self.assertRaisesRegex(PublishError, "stale"):
            publisher.sync()

    def test_without_remote(self) -> None:
        publisher = Publisher(PublisherConfig(repo_dir=self.fixture.clone, remote=None))
        result = publisher.mutate(ACTOR, "Offline", lambda c: c.add_verses("grace", [[40, 1, 1]]))
        self.assertTrue(result.changed)
        self.assertIsNone(result.pushed)
