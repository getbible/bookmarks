#!/usr/bin/env python3
"""Fail when data/ changed relative to a base commit without bumping catalog_version.

Usage: python3 scripts/check_catalog_version.py <base-ref> [<head-ref>]

The management API bumps the version on every commit; this guards manual pull
requests so consumers can rely on ``catalog_version`` strictly increasing.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


def git(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments], cwd=REPOSITORY_ROOT, capture_output=True, text=True, check=False
    )


def version_at(ref: str) -> int | None:
    shown = git("show", f"{ref}:data/topics.json")
    if shown.returncode != 0:
        return None
    document = json.loads(shown.stdout)
    value = document.get("catalog_version")
    return value if isinstance(value, int) else None


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 3):
        print(__doc__, file=sys.stderr)
        return 2
    base, head = argv[1], (argv[2] if len(argv) == 3 else "HEAD")
    changed = git("diff", "--quiet", base, head, "--", "data")
    if changed.returncode == 0:
        print("data/ unchanged; catalog_version check not required.")
        return 0
    if changed.returncode != 1:
        print(changed.stderr.strip() or "git diff failed", file=sys.stderr)
        return 2
    before, after = version_at(base), version_at(head)
    if before is None:
        print("No base topics.json; skipping the version comparison.")
        return 0
    if after is None or after <= before:
        print(
            f"data/ changed but catalog_version did not increase (base {before}, head {after}). "
            "Bump catalog_version in data/topics.json and rebuild v1/.",
            file=sys.stderr,
        )
        return 1
    print(f"catalog_version advanced from {before} to {after}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
