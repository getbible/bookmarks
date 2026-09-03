#!/usr/bin/env python3
"""Seed or refresh the canonical sources from a getbible/robot checkout.

The robot repository carried the original catalogue as
``data/global-bookmarks/topics.json`` (topic metadata), ``tag-verse.csv``
(topic-to-verse links keyed by English topic name) and the reviewed topic-name
translations inside ``miniapp/lib/bookmark-locales*.js``. This script converts
those three sources into the repository-native layout under ``data/`` so the
bookmarks repository becomes the single source of truth.

Usage::

    python3 scripts/import_from_robot.py /path/to/robot

The script is deliberately standalone (no package import) so it can run from
any checkout; the resulting files are then validated by ``getbible-bookmarks
validate`` and rendered by ``getbible-bookmarks build``.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = REPOSITORY_ROOT / "data"
REFERENCE_RE = re.compile(r"^\s*(\d{1,2})\s+(\d{1,3}):(\d{1,3})\s*$")

# English display names for locale codes that the robot ships. The robot only
# stores the code; the name is optional presentation metadata for consumers.
LANGUAGE_NAMES = {
    "af": "Afrikaans",
    "ar": "Arabic",
    "br": "Breton",
    "ch": "Chamorro",
    "chr": "Cherokee",
    "cop": "Coptic",
    "cs": "Czech",
    "cu": "Church Slavonic",
    "da": "Danish",
    "de": "German",
    "el": "Greek",
    "en": "English",
    "enm": "Middle English",
    "eo": "Esperanto",
    "es": "Spanish",
    "et": "Estonian",
    "eu": "Basque",
    "fi": "Finnish",
    "fr": "French",
    "gd": "Scottish Gaelic",
    "got": "Gothic",
    "grc": "Ancient Greek",
    "gv": "Manx",
    "hbo": "Ancient Hebrew",
    "he": "Hebrew",
    "hr": "Croatian",
    "hu": "Hungarian",
    "hy": "Armenian",
    "it": "Italian",
    "ja": "Japanese",
    "ko": "Korean",
    "la": "Latin",
    "lt": "Lithuanian",
    "lv": "Latvian",
    "mg": "Malagasy",
    "mi": "Maori",
    "mn": "Mongolian",
    "my": "Burmese",
    "nb": "Norwegian Bokmal",
    "nd": "Northern Ndebele",
    "nl": "Dutch",
    "nn": "Norwegian Nynorsk",
    "pl": "Polish",
    "pon": "Pohnpeian",
    "pot": "Potawatomi",
    "ppk": "Uma",
    "prs": "Dari",
    "pt": "Portuguese",
    "rmq": "Calo",
    "ro": "Romanian",
    "ru": "Russian",
    "sn": "Shona",
    "sq": "Albanian",
    "sr": "Serbian",
    "sv": "Swedish",
    "sw": "Swahili",
    "syr": "Syriac",
    "th": "Thai",
    "tl": "Tagalog",
    "tlh": "Klingon",
    "tpi": "Tok Pisin",
    "tr": "Turkish",
    "tsg": "Tausug",
    "uk": "Ukrainian",
    "vi": "Vietnamese",
    "zh": "Chinese",
    "zh-hans": "Chinese (Simplified)",
    "zh-hant": "Chinese (Traditional)",
}


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: import_from_robot.py <robot checkout>", file=sys.stderr)
        return 2
    robot = Path(argv[1]).resolve()
    topics_path = robot / "data" / "global-bookmarks" / "topics.json"
    csv_path = robot / "data" / "global-bookmarks" / "tag-verse.csv"
    if not topics_path.is_file() or not csv_path.is_file():
        print(f"{robot} does not look like a getbible/robot checkout.", file=sys.stderr)
        return 2

    topic_document = json.loads(topics_path.read_text("utf-8"))
    topics = topic_document["topics"]
    by_name: dict[str, str] = {}
    for topic in topics:
        for candidate in (topic["name"], *topic.get("aliases", [])):
            by_name[_fold(candidate)] = topic["id"]

    links: dict[str, set[tuple[int, int, int]]] = {topic["id"]: set() for topic in topics}
    for line_number, line in enumerate(csv_path.read_text("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        reference, _, name = line.partition(",")
        match = REFERENCE_RE.match(reference)
        topic_id = by_name.get(_fold(name))
        if match is None or topic_id is None:
            raise SystemExit(f"{csv_path}:{line_number}: unrecognised row {line!r}")
        links[topic_id].add((int(match[1]), int(match[2]), int(match[3])))

    locales = json.loads(
        subprocess.run(
            ["node", str(REPOSITORY_ROOT / "scripts" / "export_robot_locales.mjs"), str(robot)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )

    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    (DATA_ROOT / "links").mkdir(exist_ok=True)
    (DATA_ROOT / "locales").mkdir(exist_ok=True)

    _write(
        DATA_ROOT / "topics.json",
        {
            "schema_version": 1,
            "catalog_version": int(topic_document["catalog_version"]),
            "topics": [
                {
                    "id": topic["id"],
                    "name": topic["name"],
                    "color": topic["color"].lower(),
                    "aliases": sorted(topic.get("aliases", [])),
                    "default": bool(topic.get("default", False)),
                }
                for topic in sorted(topics, key=lambda item: item["id"])
            ],
        },
    )
    retired = DATA_ROOT / "retired-topics.json"
    if not retired.exists():
        _write(retired, {"schema_version": 1, "topics": []})

    for topic_id, verses in links.items():
        _write(
            DATA_ROOT / "links" / f"{topic_id}.json",
            {
                "schema_version": 1,
                "topic": topic_id,
                "verses": [list(verse) for verse in sorted(verses)],
            },
        )

    known_ids = {topic["id"] for topic in topics}
    for locale, names in locales.items():
        document: dict[str, object] = {"schema_version": 1, "locale": locale}
        if locale in LANGUAGE_NAMES:
            document["name"] = LANGUAGE_NAMES[locale]
        document["topics"] = {
            topic_id: names[topic_id] for topic_id in sorted(names) if topic_id in known_ids
        }
        _write(DATA_ROOT / "locales" / f"{locale}.json", document)

    _normalize_with_package()

    link_count = sum(len(verses) for verses in links.values())
    print(
        f"Imported {len(topics)} topics, {link_count} verse links and "
        f"{len(locales)} locales from {robot}."
    )
    return 0


def _normalize_with_package() -> None:
    """Re-render through the package writer so the files match ``save_catalog`` byte for byte."""
    sys.path.insert(0, str(REPOSITORY_ROOT))
    from getbible_bookmarks.sources import load_catalog, save_catalog

    save_catalog(DATA_ROOT, load_catalog(DATA_ROOT))


def _fold(value: str) -> str:
    return " ".join(value.lower().split())


def _write(path: Path, document: object) -> None:
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", "utf-8")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
