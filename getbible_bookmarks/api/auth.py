"""Contributor registry and bearer-token authentication.

Tokens are high-entropy random secrets prefixed ``gbb_``. Only their SHA-256
digest is stored, in a mode-0600 JSON file kept outside the repository, so a
leaked registry cannot be replayed. Two roles exist:

* ``contributor`` may add, replace and remove verse links, edit translations
  and submit robot contribution bundles;
* ``maintainer`` may additionally create, rename, recolour and retire topics,
  delete whole locales and trigger a repository sync.

Token issuance and revocation are CLI operations, never HTTP operations.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from ..jsonfmt import dumps

TOKEN_PREFIX = "gbb_"  # noqa: S105 - a public prefix, not a secret
TOKEN_RE = re.compile(r"gbb_[A-Za-z0-9_-]{43}\Z")
CONTRIBUTOR_ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
EMAIL_RE = re.compile(r"[^@\s]{1,128}@[^@\s]{1,125}\Z")
ROLES = ("contributor", "maintainer")
ROLE_RANK = {"contributor": 1, "maintainer": 2}
MAX_NAME = 100
MAX_CONTRIBUTORS = 500
MAX_FILE_BYTES = 1024 * 1024


class AuthError(ValueError):
    """The contributor registry or a registry operation is invalid."""


@dataclass(frozen=True)
class Contributor:
    id: str
    name: str
    email: str
    role: str
    token_sha256: str
    created_at: str
    revoked_at: str | None = None

    @property
    def active(self) -> bool:
        return self.revoked_at is None

    def has_role(self, role: str) -> bool:
        return ROLE_RANK[self.role] >= ROLE_RANK[role]

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "role": self.role}


def generate_token() -> str:
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    assert TOKEN_RE.fullmatch(token)
    return token


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class ContributorStore:
    """File-backed registry; re-read whenever the file changes on disk."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._signature: tuple[int, int] | None = None
        self._entries: list[Contributor] = []

    # -- reading ------------------------------------------------------------ #

    def load(self) -> list[Contributor]:
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            self._signature = None
            self._entries = []
            return []
        signature = (stat.st_mtime_ns, stat.st_size)
        if signature == self._signature:
            return list(self._entries)
        if stat.st_size > MAX_FILE_BYTES:
            raise AuthError(f"{self.path} exceeds {MAX_FILE_BYTES} bytes.")
        try:
            document = json.loads(self.path.read_text("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise AuthError(f"{self.path} is not valid JSON: {error}") from error
        self._entries = _parse(document, str(self.path))
        self._signature = signature
        return list(self._entries)

    def authenticate(self, token: object) -> Contributor | None:
        if not isinstance(token, str) or TOKEN_RE.fullmatch(token) is None:
            return None
        digest = hash_token(token)
        found: Contributor | None = None
        # Compare against every entry so timing does not reveal which token exists.
        for entry in self.load():
            if hmac.compare_digest(entry.token_sha256, digest) and entry.active:
                found = entry
        return found

    def get(self, contributor_id: str) -> Contributor | None:
        for entry in self.load():
            if entry.id == contributor_id:
                return entry
        return None

    # -- writing ------------------------------------------------------------ #

    def create(
        self, *, contributor_id: str, name: str, email: str, role: str
    ) -> tuple[Contributor, str]:
        entries = self.load()
        if len(entries) >= MAX_CONTRIBUTORS:
            raise AuthError(f"The registry may contain at most {MAX_CONTRIBUTORS} contributors.")
        if any(entry.id == contributor_id for entry in entries):
            raise AuthError(f"Contributor {contributor_id!r} already exists; use rotate or revoke.")
        token = generate_token()
        entry = _validated(
            Contributor(
                id=contributor_id,
                name=name,
                email=email,
                role=role,
                token_sha256=hash_token(token),
                created_at=_now(),
            )
        )
        self._write([*entries, entry])
        return entry, token

    def rotate(self, contributor_id: str) -> tuple[Contributor, str]:
        entries = self.load()
        token = generate_token()
        updated: Contributor | None = None
        for index, entry in enumerate(entries):
            if entry.id == contributor_id:
                updated = replace(entry, token_sha256=hash_token(token), revoked_at=None)
                entries[index] = updated
        if updated is None:
            raise AuthError(f"Contributor {contributor_id!r} does not exist.")
        self._write(entries)
        return updated, token

    def revoke(self, contributor_id: str) -> Contributor:
        entries = self.load()
        updated: Contributor | None = None
        for index, entry in enumerate(entries):
            if entry.id == contributor_id:
                updated = replace(entry, revoked_at=entry.revoked_at or _now())
                entries[index] = updated
        if updated is None:
            raise AuthError(f"Contributor {contributor_id!r} does not exist.")
        self._write(entries)
        return updated

    def set_role(self, contributor_id: str, role: str) -> Contributor:
        if role not in ROLES:
            raise AuthError(f"role must be one of {', '.join(ROLES)}.")
        entries = self.load()
        updated: Contributor | None = None
        for index, entry in enumerate(entries):
            if entry.id == contributor_id:
                updated = replace(entry, role=role)
                entries[index] = updated
        if updated is None:
            raise AuthError(f"Contributor {contributor_id!r} does not exist.")
        self._write(entries)
        return updated

    def _write(self, entries: list[Contributor]) -> None:
        document = {"schema_version": 1, "contributors": [asdict(entry) for entry in entries]}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(dumps(document))
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        os.replace(temporary, self.path)
        os.chmod(self.path, 0o600)
        self._signature = None


def _parse(document: object, label: str) -> list[Contributor]:
    if not isinstance(document, dict) or set(document) != {"schema_version", "contributors"}:
        raise AuthError(f"{label} must contain schema_version and contributors.")
    if document["schema_version"] != 1:
        raise AuthError(f"{label} has an unsupported schema_version.")
    raw = document["contributors"]
    if not isinstance(raw, list) or len(raw) > MAX_CONTRIBUTORS:
        raise AuthError(f"{label}.contributors must be a bounded array.")
    entries: list[Contributor] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        expected = {"id", "name", "email", "role", "token_sha256", "created_at", "revoked_at"}
        if not isinstance(item, dict) or set(item) != expected:
            raise AuthError(f"{label}.contributors[{index}] has unexpected fields.")
        entry = _validated(Contributor(**item))
        if entry.id in seen:
            raise AuthError(f"{label} lists contributor {entry.id!r} twice.")
        seen.add(entry.id)
        entries.append(entry)
    return entries


def _validated(entry: Contributor) -> Contributor:
    if not isinstance(entry.id, str) or CONTRIBUTOR_ID_RE.fullmatch(entry.id) is None:
        raise AuthError("contributor id must be a lowercase slug (letters, digits, . _ -).")
    if not isinstance(entry.name, str) or not entry.name.strip() or len(entry.name) > MAX_NAME:
        raise AuthError(f"contributor name must be 1..{MAX_NAME} characters.")
    if any(ord(char) < 32 or char in "<>" for char in entry.name):
        raise AuthError("contributor name must not contain control characters or angle brackets.")
    if (
        not isinstance(entry.email, str)
        or EMAIL_RE.fullmatch(entry.email) is None
        or any(char in entry.email for char in "<>,")
    ):
        raise AuthError("contributor email must be a plain address.")
    if entry.role not in ROLES:
        raise AuthError(f"contributor role must be one of {', '.join(ROLES)}.")
    if (
        not isinstance(entry.token_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", entry.token_sha256) is None
    ):
        raise AuthError("contributor token_sha256 must be a hex SHA-256 digest.")
    if not isinstance(entry.created_at, str) or not entry.created_at:
        raise AuthError("contributor created_at must be a timestamp.")
    if entry.revoked_at is not None and (
        not isinstance(entry.revoked_at, str) or not entry.revoked_at
    ):
        raise AuthError("contributor revoked_at must be null or a timestamp.")
    return entry


__all__ = [
    "ROLES",
    "TOKEN_PREFIX",
    "AuthError",
    "Contributor",
    "ContributorStore",
    "generate_token",
    "hash_token",
]
