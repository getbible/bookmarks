"""Apply a getbible/robot contribution bundle (schema version 1) to a catalogue.

The bundle is the privacy-safe export the robot's moderation CLI produces::

    {
      "schema_version": 1,
      "topics": [{"id": "...", "name": "...", "color": "#93c5fd", "aliases": []}],
      "associations": {
        "add": [{"topic_id": "...", "book": 40, "chapter": 6, "verse": 16}],
        "remove": []
      }
    }

Rules follow the robot importer: an existing topic's id, English name and
colour cannot be changed by a bundle (only its aliases may be extended), and a
coordinate cannot be both added and removed in one bundle.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from . import SCHEMA_VERSION
from .model import (
    Catalog,
    CatalogError,
    Coordinate,
    aliases,
    color,
    coordinate,
    english_name,
    exact_keys,
    fold_name,
    topic_id,
)

MAX_BUNDLE_TOPICS = 200
MAX_BUNDLE_ASSOCIATIONS = 10_000


@dataclass(frozen=True)
class BundleResult:
    topics_created: tuple[str, ...]
    topics_extended: tuple[str, ...]
    verses_added: int
    verses_removed: int

    def changed(self) -> bool:
        return bool(
            self.topics_created or self.topics_extended or self.verses_added or self.verses_removed
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "topics_created": list(self.topics_created),
            "topics_extended": list(self.topics_extended),
            "verses_added": self.verses_added,
            "verses_removed": self.verses_removed,
        }


def apply_bundle(catalog: Catalog, bundle: object) -> BundleResult:
    document = exact_keys(bundle, ("schema_version", "topics", "associations"), "bundle")
    if document["schema_version"] != SCHEMA_VERSION:
        raise CatalogError("bundle.schema_version must be 1.")
    raw_topics = document["topics"]
    if not isinstance(raw_topics, list) or len(raw_topics) > MAX_BUNDLE_TOPICS:
        raise CatalogError(f"bundle.topics must be an array of at most {MAX_BUNDLE_TOPICS} topics.")
    associations = exact_keys(document["associations"], ("add", "remove"), "bundle.associations")
    add = _associations(associations["add"], "bundle.associations.add")
    remove = _associations(associations["remove"], "bundle.associations.remove")
    if len(add) + len(remove) > MAX_BUNDLE_ASSOCIATIONS:
        raise CatalogError(
            f"bundle has more than {MAX_BUNDLE_ASSOCIATIONS} association operations."
        )
    contradiction = set(add) & set(remove)
    if contradiction:
        raise CatalogError(f"bundle both adds and removes {sorted(contradiction)[0]!r}.")

    created: list[str] = []
    extended: list[str] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_topics):
        label = f"bundle.topics[{index}]"
        item = exact_keys(raw, ("id", "name", "color", "aliases"), label)
        key = topic_id(item["id"], f"{label}.id")
        if key in seen:
            raise CatalogError(f"{label} repeats topic {key!r}.")
        seen.add(key)
        name = english_name(item["name"], f"{label}.name")
        colour = color(item["color"], f"{label}.color")
        alias_list = aliases(item["aliases"], f"{label}.aliases")
        existing = catalog.topics.get(key)
        if existing is None:
            catalog.create_topic(
                identifier=key, name=name, color_value=colour, alias_values=list(alias_list)
            )
            created.append(key)
            continue
        if fold_name(existing.name) != fold_name(name) or existing.color != colour:
            raise CatalogError(
                f"{label} would change the established name or colour of topic {key!r}; "
                "bundles may only extend aliases."
            )
        merged = tuple(sorted({*existing.aliases, *alias_list} - {fold_name(existing.name)}))
        merged = tuple(a for a in merged if fold_name(a) != fold_name(existing.name))
        if merged != existing.aliases:
            catalog.update_topic(key, alias_values=list(merged))
            extended.append(key)

    added = 0
    for key, verses in _group(add).items():
        added += catalog.add_verses(key, [list(verse) for verse in verses])
    removed = 0
    for key, verses in _group(remove).items():
        removed += catalog.remove_verses(key, [list(verse) for verse in verses])
    return BundleResult(tuple(created), tuple(extended), added, removed)


def _associations(value: object, label: str) -> list[tuple[str, Coordinate]]:
    if not isinstance(value, list):
        raise CatalogError(f"{label} must be an array.")
    result: dict[tuple[str, Coordinate], None] = {}
    for index, raw in enumerate(value):
        item = exact_keys(raw, ("topic_id", "book", "chapter", "verse"), f"{label}[{index}]")
        key = topic_id(item["topic_id"], f"{label}[{index}].topic_id")
        triple = coordinate(
            {"book": item["book"], "chapter": item["chapter"], "verse": item["verse"]},
            f"{label}[{index}]",
        )
        result.setdefault((key, triple), None)
    return list(result)


def _group(items: list[tuple[str, Coordinate]]) -> dict[str, list[Coordinate]]:
    grouped: dict[str, list[Coordinate]] = {}
    for key, triple in items:
        grouped.setdefault(key, []).append(triple)
    return grouped


__all__ = ["BundleResult", "apply_bundle"]


def _unused(_: Mapping[str, Any]) -> None:  # pragma: no cover - keeps Mapping import explicit
    return None
