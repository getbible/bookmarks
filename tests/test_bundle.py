from __future__ import annotations

import unittest

from getbible_bookmarks.bundle import apply_bundle
from getbible_bookmarks.model import CatalogError
from tests.helpers import sample_catalog


def bundle(**overrides: object) -> dict[str, object]:
    document: dict[str, object] = {
        "schema_version": 1,
        "topics": [],
        "associations": {"add": [], "remove": []},
    }
    document.update(overrides)
    return document


class BundleTests(unittest.TestCase):
    def test_applies_topics_and_associations(self) -> None:
        catalog = sample_catalog()
        result = apply_bundle(
            catalog,
            bundle(
                topics=[
                    {
                        "id": "prayer-and-fasting",
                        "name": "Prayer and Fasting",
                        "color": "#93c5fd",
                        "aliases": [],
                    },
                    {
                        "id": "grace",
                        "name": "Grace",
                        "color": "#bbf7d0",
                        "aliases": ["Unmerited Favour"],
                    },
                ],
                associations={
                    "add": [
                        {"topic_id": "prayer-and-fasting", "book": 40, "chapter": 6, "verse": 16},
                        {"topic_id": "grace", "book": 49, "chapter": 2, "verse": 8},
                        {"topic_id": "grace", "book": 56, "chapter": 2, "verse": 11},
                    ],
                    "remove": [{"topic_id": "grace", "book": 45, "chapter": 5, "verse": 20}],
                },
            ),
        )
        self.assertEqual(result.topics_created, ("prayer-and-fasting",))
        self.assertEqual(result.topics_extended, ("grace",))
        self.assertEqual((result.verses_added, result.verses_removed), (2, 1))
        self.assertTrue(result.changed())
        self.assertEqual(catalog.topics["grace"].aliases, ("Unmerited Favour",))
        self.assertEqual(catalog.sorted_verses("grace"), [(49, 2, 8), (49, 2, 9), (56, 2, 11)])
        catalog.validate()

    def test_noop_bundle(self) -> None:
        catalog = sample_catalog()
        result = apply_bundle(catalog, bundle())
        self.assertFalse(result.changed())
        self.assertEqual(result.as_dict()["verses_added"], 0)

    def test_rejects_changes_to_established_topics(self) -> None:
        catalog = sample_catalog()
        with self.assertRaisesRegex(CatalogError, "established name or colour"):
            apply_bundle(
                catalog,
                bundle(
                    topics=[{"id": "grace", "name": "Grace", "color": "#000000", "aliases": []}]
                ),
            )
        with self.assertRaisesRegex(CatalogError, "established name or colour"):
            apply_bundle(
                catalog,
                bundle(
                    topics=[{"id": "grace", "name": "Favour", "color": "#bbf7d0", "aliases": []}]
                ),
            )

    def test_rejects_malformed_bundles(self) -> None:
        catalog = sample_catalog()
        with self.assertRaisesRegex(CatalogError, "schema_version"):
            apply_bundle(catalog, bundle(schema_version=2))
        with self.assertRaisesRegex(CatalogError, "unsupported"):
            apply_bundle(catalog, {**bundle(), "note": "private"})
        with self.assertRaisesRegex(CatalogError, "adds and removes"):
            apply_bundle(
                catalog,
                bundle(
                    associations={
                        "add": [{"topic_id": "grace", "book": 1, "chapter": 1, "verse": 1}],
                        "remove": [{"topic_id": "grace", "book": 1, "chapter": 1, "verse": 1}],
                    }
                ),
            )
        with self.assertRaisesRegex(CatalogError, "does not exist"):
            apply_bundle(
                catalog,
                bundle(
                    associations={
                        "add": [{"topic_id": "ghost", "book": 1, "chapter": 1, "verse": 1}],
                        "remove": [],
                    }
                ),
            )
        with self.assertRaisesRegex(CatalogError, "repeats"):
            apply_bundle(
                catalog,
                bundle(
                    topics=[
                        {"id": "xx", "name": "Xx", "color": "#000000", "aliases": []},
                        {"id": "xx", "name": "Xx", "color": "#000000", "aliases": []},
                    ]
                ),
            )
