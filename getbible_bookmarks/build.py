"""Render the versioned static JSON API tree from a validated catalogue.

Every document is deterministic: the same sources always produce the same
bytes, which lets CI verify the committed ``v1/`` tree and lets nginx serve it
with plain file ETags. ``catalog.json`` is the reference document; its SHA-256
is the catalogue checksum quoted by every top-level document.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from . import SCHEMA_VERSION
from .canon import BOOK_CHAPTER_COUNTS, BOOK_COUNT
from .jsonfmt import dump_bytes
from .model import ENGLISH_LOCALE, Catalog, Topic

API_VERSION = "v1"
RESOURCES: Mapping[str, str] = {
    "index": "index.json",
    "catalog": "catalog.json",
    "all": "all.json",
    "topics": "topics.json",
    "topic": "topics/{id}.json",
    "book": "verses/{book}.json",
    "chapter": "verses/{book}/{chapter}.json",
    "locales": "locales.json",
    "locale": "locales/{locale}.json",
    "retired": "retired.json",
    "checksums": "checksums.json",
}


def sha256_hex(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def render_api(catalog: Catalog) -> dict[str, bytes]:
    """Return the complete API tree as ``relative path -> bytes``."""
    catalog.validate()
    files: dict[str, bytes] = {}

    topics = catalog.sorted_topics()
    retired = [
        {"id": item.id, "name": item.name, "retired_in": item.retired_in}
        for item in catalog.sorted_retired()
    ]
    catalog_document = {
        "schema_version": SCHEMA_VERSION,
        "catalog_version": catalog.catalog_version,
        "topics": [_topic_with_verses(catalog, topic) for topic in topics],
        "retired": retired,
    }
    files[RESOURCES["catalog"]] = dump_bytes(catalog_document)
    checksum = sha256_hex(files[RESOURCES["catalog"]])

    def head(**extra: Any) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "catalog_version": catalog.catalog_version,
            "checksum": checksum,
            **extra,
        }

    locale_documents: dict[str, dict[str, Any]] = {
        ENGLISH_LOCALE: {
            "schema_version": SCHEMA_VERSION,
            "locale": ENGLISH_LOCALE,
            "name": "English",
            "topics": {topic.id: topic.name for topic in topics},
        }
    }
    for locale in catalog.sorted_locales():
        document: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "locale": locale.code}
        if locale.name is not None:
            document["name"] = locale.name
        document["topics"] = {key: locale.topics[key] for key in sorted(locale.topics)}
        locale_documents[locale.code] = document
    locale_codes = sorted(locale_documents)

    files[RESOURCES["topics"]] = dump_bytes(
        head(topics=[_topic_summary(catalog, topic) for topic in topics])
    )
    for topic in topics:
        names = {
            code: locale_documents[code]["topics"][topic.id]
            for code in locale_codes
            if topic.id in locale_documents[code]["topics"]
        }
        files[RESOURCES["topic"].format(id=topic.id)] = dump_bytes(
            {
                "schema_version": SCHEMA_VERSION,
                **_topic_fields(topic),
                "names": names,
                "verses": [list(verse) for verse in catalog.sorted_verses(topic.id)],
            }
        )

    by_verse: dict[int, dict[int, dict[int, list[str]]]] = {}
    for topic in topics:
        for book, chapter, verse in catalog.sorted_verses(topic.id):
            by_verse.setdefault(book, {}).setdefault(chapter, {}).setdefault(verse, []).append(
                topic.id
            )
    for book in range(1, BOOK_COUNT + 1):
        chapters = by_verse.get(book, {})
        files[RESOURCES["book"].format(book=book)] = dump_bytes(
            {
                "schema_version": SCHEMA_VERSION,
                "book": book,
                "chapters": {
                    str(chapter): {str(verse): ids for verse, ids in sorted(verses.items())}
                    for chapter, verses in sorted(chapters.items())
                },
            }
        )
        for chapter in range(1, BOOK_CHAPTER_COUNTS[book - 1] + 1):
            verses = chapters.get(chapter, {})
            files[RESOURCES["chapter"].format(book=book, chapter=chapter)] = dump_bytes(
                {
                    "schema_version": SCHEMA_VERSION,
                    "book": book,
                    "chapter": chapter,
                    "verses": {str(verse): ids for verse, ids in sorted(verses.items())},
                }
            )

    for code in locale_codes:
        files[RESOURCES["locale"].format(locale=code)] = dump_bytes(locale_documents[code])
    files[RESOURCES["locales"]] = dump_bytes(
        head(
            locales=[
                {
                    "code": code,
                    "name": locale_documents[code].get("name"),
                    "topics": len(locale_documents[code]["topics"]),
                }
                for code in locale_codes
            ]
        )
    )
    files[RESOURCES["retired"]] = dump_bytes(head(topics=retired))
    files[RESOURCES["all"]] = dump_bytes(
        head(
            topics=catalog_document["topics"],
            retired=retired,
            locales={code: locale_documents[code] for code in locale_codes},
        )
    )
    files[RESOURCES["index"]] = dump_bytes(
        head(
            counts={
                "topics": len(topics),
                "verses": catalog.link_count(),
                "locales": len(locale_codes),
                "retired": len(retired),
            },
            resources=dict(RESOURCES),
            locales=locale_codes,
        )
    )
    files[RESOURCES["checksums"]] = dump_bytes(
        head(files={path: sha256_hex(content) for path, content in sorted(files.items())})
    )
    return files


def catalog_checksum(files: Mapping[str, bytes]) -> str:
    return sha256_hex(files[RESOURCES["catalog"]])


def write_tree(root: Path, files: Mapping[str, bytes]) -> None:
    """Replace ``root`` with the rendered tree atomically (build beside, then swap)."""
    root = root.resolve()
    root.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{root.name}.", dir=root.parent))
    try:
        for relative, content in files.items():
            path = staging / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        previous = root.with_name(f".{root.name}.previous")
        if previous.exists():
            shutil.rmtree(previous)
        if root.exists():
            os.rename(root, previous)
        os.rename(staging, root)
        if previous.exists():
            shutil.rmtree(previous)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def stale_paths(root: Path, files: Mapping[str, bytes]) -> list[str]:
    """Return paths whose committed bytes differ from a fresh render, plus extras."""
    stale: list[str] = []
    for relative, content in files.items():
        path = root / relative
        if not path.is_file() or path.read_bytes() != content:
            stale.append(relative)
    if root.is_dir():
        for path in root.rglob("*"):
            if path.is_file():
                relative = path.relative_to(root).as_posix()
                if relative not in files:
                    stale.append(relative)
    return sorted(stale)


def _topic_fields(topic: Topic) -> dict[str, Any]:
    return {
        "id": topic.id,
        "name": topic.name,
        "color": topic.color,
        "aliases": list(topic.aliases),
        "default": topic.default,
    }


def _topic_summary(catalog: Catalog, topic: Topic) -> dict[str, Any]:
    return {**_topic_fields(topic), "verses": len(catalog.links.get(topic.id, ()))}


def _topic_with_verses(catalog: Catalog, topic: Topic) -> dict[str, Any]:
    return {
        **_topic_fields(topic),
        "verses": [list(verse) for verse in catalog.sorted_verses(topic.id)],
    }
