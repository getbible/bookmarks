from __future__ import annotations

import json
import unittest

from getbible_bookmarks.jsonfmt import canonical_bytes, dump_bytes, dumps


class JsonFormatTests(unittest.TestCase):
    def test_scalar_arrays_inline_objects_indented(self) -> None:
        text = dumps({"a": [1, 2, 3], "b": {"c": "é", "d": []}, "e": [[1, 2], {"f": None}]})
        self.assertEqual(
            text,
            '{\n  "a": [1, 2, 3],\n  "b": {\n    "c": "é",\n    "d": []\n  },\n'
            '  "e": [\n    [1, 2],\n    {\n      "f": null\n    }\n  ]\n}\n',
        )
        self.assertEqual(
            json.loads(text), {"a": [1, 2, 3], "b": {"c": "é", "d": []}, "e": [[1, 2], {"f": None}]}
        )

    def test_empty_and_bytes(self) -> None:
        self.assertEqual(dumps({}), "{}\n")
        self.assertEqual(dumps([]), "[]\n")
        self.assertEqual(dump_bytes({"x": True}), b'{\n  "x": true\n}\n')
        self.assertEqual(canonical_bytes({"b": 1, "a": [1, 2]}), b'{"a":[1,2],"b":1}')

    def test_rejects_unknown_types(self) -> None:
        with self.assertRaises(TypeError):
            dumps({"x": object()})
