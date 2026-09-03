"""Deterministic JSON rendering shared by the sources and the generated API.

Objects keep insertion order and are indented; arrays whose members are all
scalars are rendered on one line so verse coordinates stay compact and diffs
stay readable. Output always ends with one newline and is UTF-8 without any
ASCII escaping of non-ASCII text.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

Scalar = str | int | float | bool | None


def dumps(value: Any, *, indent: int = 2) -> str:
    return _render(value, indent, 0) + "\n"


def dump_bytes(value: Any) -> bytes:
    return dumps(value).encode("utf-8")


def canonical_bytes(value: Any) -> bytes:
    """Compact, key-sorted encoding used only for content checksums."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _render(value: Any, indent: int, depth: int) -> str:
    if isinstance(value, Mapping):
        if not value:
            return "{}"
        pad = " " * (indent * (depth + 1))
        close = " " * (indent * depth)
        items = [
            f"{pad}{json.dumps(str(key), ensure_ascii=False)}: {_render(item, indent, depth + 1)}"
            for key, item in value.items()
        ]
        return "{\n" + ",\n".join(items) + "\n" + close + "}"
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        if not value:
            return "[]"
        if all(_is_scalar(item) for item in value):
            return "[" + ", ".join(json.dumps(item, ensure_ascii=False) for item in value) + "]"
        pad = " " * (indent * (depth + 1))
        close = " " * (indent * depth)
        items = [f"{pad}{_render(item, indent, depth + 1)}" for item in value]
        return "[\n" + ",\n".join(items) + "\n" + close + "]"
    if _is_scalar(value):
        return json.dumps(value, ensure_ascii=False)
    raise TypeError(f"Unsupported JSON value of type {type(value).__name__}.")


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, str | int | float | bool)
