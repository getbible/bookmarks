"""Protestant canon bounds shared by every validator in this repository.

Verse coordinates are translation independent: ``(book, chapter, verse)`` with
the 66-book Protestant order used by GetBible API v2 and by the robot's global
bookmark catalogue. Verse numbers are bounded loosely because versification
differs between translations; the chapter bound is exact.
"""

from __future__ import annotations

from typing import Final

BOOK_CHAPTER_COUNTS: Final[tuple[int, ...]] = (
    50,
    40,
    27,
    36,
    34,
    24,
    21,
    4,
    31,
    24,
    22,
    25,
    29,
    36,
    10,
    13,
    10,
    42,
    150,
    31,
    12,
    8,
    66,
    52,
    5,
    48,
    12,
    14,
    3,
    9,
    1,
    4,
    7,
    3,
    3,
    3,
    2,
    14,
    4,
    28,
    16,
    24,
    21,
    28,
    16,
    16,
    13,
    6,
    6,
    4,
    4,
    5,
    3,
    6,
    4,
    3,
    1,
    13,
    5,
    5,
    3,
    5,
    1,
    1,
    1,
    22,
)

BOOK_NAMES: Final[tuple[str, ...]] = (
    "Genesis",
    "Exodus",
    "Leviticus",
    "Numbers",
    "Deuteronomy",
    "Joshua",
    "Judges",
    "Ruth",
    "1 Samuel",
    "2 Samuel",
    "1 Kings",
    "2 Kings",
    "1 Chronicles",
    "2 Chronicles",
    "Ezra",
    "Nehemiah",
    "Esther",
    "Job",
    "Psalms",
    "Proverbs",
    "Ecclesiastes",
    "Song of Solomon",
    "Isaiah",
    "Jeremiah",
    "Lamentations",
    "Ezekiel",
    "Daniel",
    "Hosea",
    "Joel",
    "Amos",
    "Obadiah",
    "Jonah",
    "Micah",
    "Nahum",
    "Habakkuk",
    "Zephaniah",
    "Haggai",
    "Zechariah",
    "Malachi",
    "Matthew",
    "Mark",
    "Luke",
    "John",
    "Acts",
    "Romans",
    "1 Corinthians",
    "2 Corinthians",
    "Galatians",
    "Ephesians",
    "Philippians",
    "Colossians",
    "1 Thessalonians",
    "2 Thessalonians",
    "1 Timothy",
    "2 Timothy",
    "Titus",
    "Philemon",
    "Hebrews",
    "James",
    "1 Peter",
    "2 Peter",
    "1 John",
    "2 John",
    "3 John",
    "Jude",
    "Revelation",
)

BOOK_COUNT: Final[int] = len(BOOK_CHAPTER_COUNTS)
CHAPTER_COUNT: Final[int] = sum(BOOK_CHAPTER_COUNTS)
# Psalm 119 has 176 verses; the bound stays loose for divergent versification.
MAX_VERSE: Final[int] = 2000

assert len(BOOK_NAMES) == BOOK_COUNT
assert CHAPTER_COUNT == 1189


def is_canonical_coordinate(book: object, chapter: object, verse: object) -> bool:
    """Return whether ``(book, chapter, verse)`` lies inside the Protestant canon."""
    if not all(
        isinstance(part, int) and not isinstance(part, bool) for part in (book, chapter, verse)
    ):
        return False
    assert isinstance(book, int) and isinstance(chapter, int) and isinstance(verse, int)
    return (
        1 <= book <= BOOK_COUNT
        and 1 <= chapter <= BOOK_CHAPTER_COUNTS[book - 1]
        and 1 <= verse <= MAX_VERSE
    )


def reference(book: int, chapter: int, verse: int) -> str:
    """Render a human readable English reference such as ``John 3:16``."""
    return f"{BOOK_NAMES[book - 1]} {chapter}:{verse}"
