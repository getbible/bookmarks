from __future__ import annotations

import unittest

from getbible_bookmarks.canon import (
    BOOK_CHAPTER_COUNTS,
    BOOK_COUNT,
    CHAPTER_COUNT,
    is_canonical_coordinate,
    reference,
)


class CanonTests(unittest.TestCase):
    def test_bounds(self) -> None:
        self.assertEqual(BOOK_COUNT, 66)
        self.assertEqual(CHAPTER_COUNT, 1189)
        self.assertEqual(BOOK_CHAPTER_COUNTS[18], 150)
        self.assertEqual(BOOK_CHAPTER_COUNTS[65], 22)

    def test_coordinates(self) -> None:
        self.assertTrue(is_canonical_coordinate(43, 3, 16))
        self.assertTrue(is_canonical_coordinate(19, 150, 6))
        self.assertFalse(is_canonical_coordinate(0, 1, 1))
        self.assertFalse(is_canonical_coordinate(67, 1, 1))
        self.assertFalse(is_canonical_coordinate(19, 151, 1))
        self.assertFalse(is_canonical_coordinate(1, 1, 0))
        self.assertFalse(is_canonical_coordinate(1, 1, 2001))
        self.assertFalse(is_canonical_coordinate(True, 1, 1))
        self.assertFalse(is_canonical_coordinate("43", 3, 16))

    def test_reference(self) -> None:
        self.assertEqual(reference(43, 3, 16), "John 3:16")
        self.assertEqual(reference(66, 22, 21), "Revelation 22:21")
