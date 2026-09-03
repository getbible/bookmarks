"""Shared fixtures for the test suite."""

from __future__ import annotations

from getbible_bookmarks.model import Catalog


def sample_catalog() -> Catalog:
    catalog = Catalog(catalog_version=3)
    catalog.create_topic(identifier="grace", name="Grace", color_value="#bbf7d0", default=True)
    catalog.create_topic(
        identifier="gods-judgment",
        name="God's Judgment",
        color_value="#fb7185",
        alias_values=["God's Judgement"],
        default=True,
    )
    catalog.add_verses("grace", [[49, 2, 8], [49, 2, 9], [45, 5, 20]])
    catalog.add_verses("gods-judgment", [[45, 2, 5], [58, 9, 27]])
    catalog.set_locale_names(
        "fr", {"grace": "Grâce", "gods-judgment": "Jugement de Dieu"}, name="French"
    )
    catalog.set_locale_names("de", {"grace": "Gnade"}, name="German")
    return catalog
