from __future__ import annotations

import unittest

from getbible_bookmarks import model
from getbible_bookmarks.model import Catalog, CatalogError, copy_catalog
from tests.helpers import sample_catalog


class ValidatorTests(unittest.TestCase):
    def test_topic_id(self) -> None:
        self.assertEqual(model.topic_id(" grace "), "grace")
        for bad in ("Grace", "-grace", "grace-", "gr--ace", "", "a" * 81, 5):
            with self.assertRaises(CatalogError, msg=repr(bad)):
                model.topic_id(bad)

    def test_english_name(self) -> None:
        self.assertEqual(model.english_name("God's Judgment"), "God's Judgment")
        self.assertEqual(model.english_name("Wisdom (Value)"), "Wisdom (Value)")
        for bad in ("Grâce", "Grace!", " ", "A  B", "-Grace", "Grace-", "x" * 81):
            with self.assertRaises(CatalogError, msg=repr(bad)):
                model.english_name(bad)

    def test_color(self) -> None:
        self.assertEqual(model.color("#93C5FD"), "#93c5fd")
        for bad in ("93c5fd", "#93c5f", "#93c5fdd", "#gggggg", "red"):
            with self.assertRaises(CatalogError, msg=repr(bad)):
                model.color(bad)

    def test_aliases(self) -> None:
        self.assertEqual(model.aliases(None), ())
        self.assertEqual(model.aliases(["Bb", "Aa", "Aa"]), ("Aa", "Bb"))
        with self.assertRaises(CatalogError):
            model.aliases("A")
        with self.assertRaises(CatalogError):
            model.aliases([str(i) for i in range(21)])

    def test_locale_code(self) -> None:
        for good in ("fr", "zh-hant", "cop", "pt-br"):
            self.assertEqual(model.locale_code(good), good)
        for bad in ("FR", "zh_Hant", "f", "français", "en-", 1):
            with self.assertRaises(CatalogError, msg=repr(bad)):
                model.locale_code(bad)

    def test_translated_name(self) -> None:
        self.assertEqual(model.translated_name("  Grâce "), "Grâce")
        self.assertEqual(model.translated_name("姦淫"), "姦淫")
        with self.assertRaises(CatalogError):
            model.translated_name("bad\x00name")
        with self.assertRaises(CatalogError):
            model.translated_name("x" * 121)

    def test_coordinate_forms(self) -> None:
        self.assertEqual(model.coordinate([43, 3, 16]), (43, 3, 16))
        self.assertEqual(model.coordinate({"book": 43, "chapter": 3, "verse": 16}), (43, 3, 16))
        for bad in (
            [43, 3],
            [43, 3, 16, 1],
            {"book": 43, "chapter": 3},
            [0, 1, 1],
            [19, 151, 1],
            [1, 1, 2001],
            [True, 1, 1],
            "43:3:16",
        ):
            with self.assertRaises(CatalogError, msg=repr(bad)):
                model.coordinate(bad)

    def test_coordinates_dedupe_and_bound(self) -> None:
        self.assertEqual(
            model.coordinates([[1, 1, 1], [1, 1, 1], [1, 1, 2]]), [(1, 1, 1), (1, 1, 2)]
        )
        with self.assertRaises(CatalogError):
            model.coordinates("x")
        with self.assertRaises(CatalogError):
            model.coordinates([[1, 1, 1]] * 10_001)

    def test_slugify(self) -> None:
        self.assertEqual(model.slugify("God's Judgment"), "gods-judgment")
        self.assertEqual(model.slugify("Jesus Christ's Deity"), "jesus-christs-deity")
        self.assertEqual(model.slugify("Blessings & Curses"), "blessings-curses")
        with self.assertRaises(CatalogError):
            model.slugify("&&&")

    def test_exact_and_allowed_keys(self) -> None:
        self.assertEqual(dict(model.exact_keys({"a": 1}, ["a"], "x")), {"a": 1})
        with self.assertRaisesRegex(CatalogError, "missing b; unsupported c"):
            model.exact_keys({"a": 1, "c": 2}, ["a", "b"], "x")
        with self.assertRaisesRegex(CatalogError, "unsupported fields: z"):
            model.allowed_keys({"z": 1}, ["a"], "x")
        with self.assertRaises(CatalogError):
            model.allowed_keys([], ["a"], "x")


class CatalogMutationTests(unittest.TestCase):
    def test_sample_is_valid(self) -> None:
        catalog = sample_catalog()
        catalog.validate()
        self.assertEqual(catalog.link_count(), 5)
        self.assertEqual([t.id for t in catalog.sorted_topics()], ["gods-judgment", "grace"])

    def test_create_topic_derives_id_and_rejects_name_collisions(self) -> None:
        catalog = sample_catalog()
        topic = catalog.create_topic(name="Saved by Faith", color_value="#86EFAC")
        self.assertEqual(topic.id, "saved-by-faith")
        self.assertEqual(topic.color, "#86efac")
        with self.assertRaisesRegex(CatalogError, "already exists"):
            catalog.create_topic(identifier="grace", name="Other", color_value="#000000")
        with self.assertRaisesRegex(CatalogError, "already used"):
            catalog.create_topic(identifier="grace-2", name="GRACE", color_value="#000000")
        with self.assertRaisesRegex(CatalogError, "already exists"):
            catalog.create_topic(name="grace", color_value="#000000")
        with self.assertRaisesRegex(CatalogError, "already used"):
            catalog.create_topic(
                name="Mercy", color_value="#000000", alias_values=["God's Judgement"]
            )
        with self.assertRaises(CatalogError):
            catalog.create_topic(name="Mercy", color_value="#000000", default="yes")

    def test_create_topic_with_verses_and_names(self) -> None:
        catalog = sample_catalog()
        catalog.create_topic(
            name="Mercy", color_value="#ffffff", verses=[[19, 23, 6]], names={"fr": "Miséricorde"}
        )
        self.assertEqual(catalog.sorted_verses("mercy"), [(19, 23, 6)])
        self.assertEqual(catalog.locales["fr"].topics["mercy"], "Miséricorde")
        catalog.validate()

    def test_rename_keeps_previous_wording_as_alias(self) -> None:
        catalog = sample_catalog()
        updated = catalog.update_topic("grace", name="Grace of God")
        self.assertEqual(updated.name, "Grace of God")
        self.assertEqual(updated.aliases, ("Grace",))
        # Alias equal to the name is dropped; explicit aliases replace the list.
        updated = catalog.update_topic("grace", alias_values=["Grace of God", "Favour"])
        self.assertEqual(updated.aliases, ("Favour",))
        with self.assertRaisesRegex(CatalogError, "already used"):
            catalog.update_topic("grace", name="God's Judgement")
        catalog.validate()

    def test_update_color_and_default(self) -> None:
        catalog = sample_catalog()
        updated = catalog.update_topic("grace", color_value="#ABCDEF", default=False)
        self.assertEqual((updated.color, updated.default), ("#abcdef", False))
        with self.assertRaises(CatalogError):
            catalog.update_topic("grace", default="no")
        with self.assertRaises(CatalogError):
            catalog.update_topic("missing", color_value="#000000")

    def test_retire_topic_is_permanent(self) -> None:
        catalog = sample_catalog()
        tombstone = catalog.retire_topic("grace", reason="Merged into mercy")
        self.assertEqual(tombstone.retired_in, 4)
        self.assertNotIn("grace", catalog.topics)
        self.assertNotIn("grace", catalog.links)
        self.assertNotIn("grace", catalog.locales["fr"].topics)
        with self.assertRaisesRegex(CatalogError, "retired"):
            catalog.add_verses("grace", [[1, 1, 1]])
        with self.assertRaisesRegex(CatalogError, "permanently reserved"):
            catalog.create_topic(identifier="grace", name="Grace", color_value="#000000")
        catalog.validate()

    def test_verse_operations(self) -> None:
        catalog = sample_catalog()
        self.assertEqual(catalog.add_verses("grace", [[49, 2, 8], [49, 2, 10]]), 1)
        self.assertEqual(catalog.remove_verses("grace", [[49, 2, 8], [1, 1, 1]]), 1)
        self.assertEqual(catalog.replace_verses("grace", [[49, 2, 9], [40, 1, 1]]), (1, 2))
        self.assertEqual(catalog.sorted_verses("grace"), [(40, 1, 1), (49, 2, 9)])
        with self.assertRaises(CatalogError):
            catalog.add_verses("grace", [[99, 1, 1]])

    def test_locale_operations(self) -> None:
        catalog = sample_catalog()
        self.assertEqual(catalog.set_locale_names("fr", {"grace": "Grâce"}), 0)
        self.assertEqual(catalog.set_locale_names("fr", {"grace": "La grâce"}), 1)
        self.assertEqual(catalog.set_locale_names("de", {"grace": "Gnade"}, replace_all=True), 0)
        self.assertEqual(catalog.set_locale_names("de", {}, replace_all=True), 1)
        self.assertTrue(catalog.delete_locale_name("fr", "grace"))
        self.assertFalse(catalog.delete_locale_name("fr", "grace"))
        self.assertTrue(catalog.delete_locale("de"))
        self.assertFalse(catalog.delete_locale("de"))
        with self.assertRaises(CatalogError):
            catalog.set_locale_names("en", {"grace": "Grace"})
        with self.assertRaises(CatalogError):
            catalog.set_locale_names("fr", {"missing": "X"})
        with self.assertRaises(CatalogError):
            catalog.set_locale_names("fr", "Grâce")
        catalog.validate()

    def test_validate_detects_corruption(self) -> None:
        catalog = sample_catalog()
        catalog.links["ghost"] = {(1, 1, 1)}
        with self.assertRaisesRegex(CatalogError, "unknown topic"):
            catalog.validate()
        catalog = sample_catalog()
        catalog.topics["grace"] = model.Topic(id="grace", name="God's Judgment", color="#000000")
        with self.assertRaisesRegex(CatalogError, "used by both"):
            catalog.validate()
        catalog = sample_catalog()
        catalog.locales["fr"].topics["ghost"] = "x"
        with self.assertRaisesRegex(CatalogError, "unknown topic"):
            catalog.validate()
        catalog = sample_catalog()
        catalog.catalog_version = 0
        with self.assertRaises(CatalogError):
            catalog.validate()

    def test_copy_is_independent(self) -> None:
        catalog = sample_catalog()
        clone = copy_catalog(catalog)
        clone.add_verses("grace", [[1, 1, 1]])
        clone.locales["fr"].topics["grace"] = "changed"
        self.assertNotIn((1, 1, 1), catalog.links["grace"])
        self.assertEqual(catalog.locales["fr"].topics["grace"], "Grâce")

    def test_empty_catalog_validates(self) -> None:
        Catalog().validate()
