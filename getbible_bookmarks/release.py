"""Publish a rendered API tree into the directory nginx serves.

Releases are immutable directories named by the catalogue checksum, each holding
the API tree under its version path (``releases/<checksum>/v1/...``) so nginx
can use ``current`` as its document root. Each JSON file gets a precompressed
``.gz`` sibling (for ``gzip_static``), then the ``current`` symlink is switched
atomically so readers never observe a partial tree. Older releases are pruned,
keeping a few for instant rollback.
"""

from __future__ import annotations

import gzip
import json
import os
import shutil
import tempfile
from pathlib import Path

RELEASES_DIR = "releases"
CURRENT_LINK = "current"
DEFAULT_KEEP = 3
DEFAULT_SUBDIR = "v1"


class ReleaseError(RuntimeError):
    """The source tree is unusable or the target could not be updated."""


def release_checksum(source: Path) -> str:
    try:
        document = json.loads((source / "index.json").read_text("utf-8"))
    except (OSError, ValueError) as error:
        raise ReleaseError(f"{source} does not contain a readable index.json: {error}") from error
    checksum = document.get("checksum") if isinstance(document, dict) else None
    if not isinstance(checksum, str) or len(checksum) != 64:
        raise ReleaseError(f"{source}/index.json does not carry a catalogue checksum.")
    return checksum


def publish_release(
    source: Path, target: Path, *, keep: int = DEFAULT_KEEP, subdir: str = DEFAULT_SUBDIR
) -> Path:
    """Copy ``source`` into ``target/releases/<checksum>/<subdir>`` and point ``current`` at it."""
    source = Path(source)
    target = Path(target)
    checksum = release_checksum(source)
    releases = target / RELEASES_DIR
    releases.mkdir(parents=True, exist_ok=True)
    release = releases / checksum
    if not release.is_dir():
        staging = Path(tempfile.mkdtemp(prefix=f".{checksum[:12]}.", dir=releases))
        try:
            for path in sorted(source.rglob("*")):
                if not path.is_file():
                    continue
                relative = path.relative_to(source)
                destination = staging / subdir / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                content = path.read_bytes()
                destination.write_bytes(content)
                if path.suffix == ".json":
                    _write_gzip(destination.with_name(destination.name + ".gz"), content)
            _chmod_tree(staging)
            os.rename(staging, release)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
    _switch_symlink(target / CURRENT_LINK, Path(RELEASES_DIR) / checksum)
    _prune(releases, keep=max(keep, 1), current=checksum)
    return release


def current_release(target: Path) -> Path | None:
    link = Path(target) / CURRENT_LINK
    if not link.is_symlink():
        return None
    return (link.parent / os.readlink(link)).resolve()


def _write_gzip(path: Path, content: bytes) -> None:
    with (
        open(path, "wb") as handle,
        gzip.GzipFile(fileobj=handle, mode="wb", compresslevel=9, mtime=0) as archive,
    ):
        archive.write(content)


def _chmod_tree(root: Path) -> None:
    for path in root.rglob("*"):
        path.chmod(0o755 if path.is_dir() else 0o644)
    root.chmod(0o755)


def _switch_symlink(link: Path, destination: Path) -> None:
    temporary = link.with_name(f".{link.name}.tmp")
    if temporary.is_symlink() or temporary.exists():
        temporary.unlink()
    os.symlink(destination, temporary)
    os.replace(temporary, link)


def _prune(releases: Path, *, keep: int, current: str) -> None:
    candidates = [
        path for path in releases.iterdir() if path.is_dir() and not path.name.startswith(".")
    ]
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    kept = 0
    for path in candidates:
        if path.name == current:
            continue
        kept += 1
        if kept >= keep:
            shutil.rmtree(path, ignore_errors=True)


__all__ = ["ReleaseError", "current_release", "publish_release", "release_checksum"]
