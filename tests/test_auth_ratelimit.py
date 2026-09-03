from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from getbible_bookmarks.api.auth import AuthError, ContributorStore, generate_token, hash_token
from getbible_bookmarks.api.ratelimit import RateLimiter


class ContributorStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / "contributors.json"
        self.store = ContributorStore(self.path)

    def tearDown(self) -> None:
        self.folder.cleanup()

    def test_create_authenticate_revoke_rotate(self) -> None:
        self.assertEqual(self.store.load(), [])
        entry, token = self.store.create(
            contributor_id="jaco", name="Brother Jaco", email="jaco@example.org", role="maintainer"
        )
        self.assertTrue(token.startswith("gbb_"))
        self.assertEqual(entry.token_sha256, hash_token(token))
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        found = self.store.authenticate(token)
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found.id, "jaco")
        self.assertTrue(found.has_role("contributor"))
        self.assertTrue(found.has_role("maintainer"))
        self.assertIsNone(self.store.authenticate(token[:-1] + ("A" if token[-1] != "A" else "B")))
        self.assertIsNone(self.store.authenticate("Bearer nonsense"))
        self.assertIsNone(self.store.authenticate(None))
        revoked = self.store.revoke("jaco")
        self.assertIsNotNone(revoked.revoked_at)
        self.assertIsNone(self.store.authenticate(token))
        rotated, new_token = self.store.rotate("jaco")
        self.assertIsNone(rotated.revoked_at)
        self.assertIsNone(self.store.authenticate(token))
        self.assertIsNotNone(self.store.authenticate(new_token))
        changed = self.store.set_role("jaco", "contributor")
        self.assertFalse(changed.has_role("maintainer"))
        with self.assertRaises(AuthError):
            self.store.set_role("jaco", "admin")
        with self.assertRaises(AuthError):
            self.store.create(
                contributor_id="jaco", name="Again", email="a@b.c", role="contributor"
            )
        with self.assertRaises(AuthError):
            self.store.revoke("nobody")
        with self.assertRaises(AuthError):
            self.store.rotate("nobody")

    def test_reloads_when_file_changes(self) -> None:
        _, token = self.store.create(
            contributor_id="a", name="A", email="a@example.org", role="contributor"
        )
        other = ContributorStore(self.path)
        self.assertIsNotNone(other.authenticate(token))
        self.store.revoke("a")
        other._signature = None  # force the stat comparison to miss the cache
        self.assertIsNone(other.authenticate(token))

    def test_rejects_invalid_entries(self) -> None:
        with self.assertRaises(AuthError):
            self.store.create(
                contributor_id="Bad Id", name="A", email="a@example.org", role="contributor"
            )
        with self.assertRaises(AuthError):
            self.store.create(
                contributor_id="ok", name="Evil <script>", email="a@example.org", role="contributor"
            )
        with self.assertRaises(AuthError):
            self.store.create(
                contributor_id="ok", name="A", email="not-an-email", role="contributor"
            )
        with self.assertRaises(AuthError):
            self.store.create(contributor_id="ok", name="A", email="a@example.org", role="root")
        self.path.write_text(json.dumps({"schema_version": 1, "contributors": [{"id": "x"}]}))
        with self.assertRaises(AuthError):
            self.store.load()
        self.path.write_text("{")
        with self.assertRaises(AuthError):
            self.store.load()

    def test_generate_token_format(self) -> None:
        tokens = {generate_token() for _ in range(50)}
        self.assertEqual(len(tokens), 50)
        self.assertTrue(all(len(token) == 47 for token in tokens))


class RateLimiterTests(unittest.TestCase):
    def test_bucket_drains_and_refills(self) -> None:
        limiter = RateLimiter(capacity=2, refill_per_second=1)
        self.assertEqual(limiter.acquire("k", now=0.0), 0.0)
        self.assertEqual(limiter.acquire("k", now=0.0), 0.0)
        self.assertAlmostEqual(limiter.acquire("k", now=0.0), 1.0)
        self.assertEqual(limiter.acquire("k", now=1.0), 0.0)
        self.assertEqual(limiter.acquire("other", now=1.0), 0.0)

    def test_prunes_when_full(self) -> None:
        limiter = RateLimiter(capacity=1, refill_per_second=1, max_keys=4)
        for index in range(4):
            limiter.acquire(f"k{index}", now=float(index))
        limiter.acquire("late", now=100.0)
        self.assertLessEqual(len(limiter._buckets), 4)
