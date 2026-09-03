"""Serialize catalogue mutations into validated commits on the repository.

One mutation is: integrate the remote branch, load the sources, apply the
change, bump ``catalog_version``, save the sources, rebuild ``v1/``, commit
with the contributor as author and the service as committer, push, and finally
publish the release directory nginx serves. Git is the audit log and the
rollback mechanism; there is no other database.
"""

from __future__ import annotations

import contextlib
import logging
import os
import subprocess
import threading
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .build import RESOURCES, catalog_checksum, render_api, stale_paths, write_tree
from .model import Catalog, CatalogError
from .release import publish_release
from .sources import load_catalog, render_sources, save_catalog

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX hosts
    fcntl = None  # type: ignore[assignment]

log = logging.getLogger("getbible.bookmarks.publisher")

DEFAULT_COMMITTER_NAME = "getBible Bookmarks Service"
DEFAULT_COMMITTER_EMAIL = "bookmarks@getbible.net"
GIT_TIMEOUT_SECONDS = 180.0


class PublishError(RuntimeError):
    """The repository cannot accept a commit right now (dirty tree, wrong branch, ...)."""


class DivergedRepositoryError(PublishError):
    """Local and remote history conflict; an operator has to reconcile them."""


class VersionConflictError(PublishError):
    """The caller's expected catalogue version no longer matches the repository."""


@dataclass(frozen=True)
class Actor:
    id: str
    name: str
    email: str


@dataclass(frozen=True)
class PublisherConfig:
    repo_dir: Path
    remote: str | None = "origin"
    branch: str = "main"
    push: bool = True
    committer_name: str = DEFAULT_COMMITTER_NAME
    committer_email: str = DEFAULT_COMMITTER_EMAIL
    release_dir: Path | None = None
    git_timeout: float = GIT_TIMEOUT_SECONDS
    data_subdir: str = "data"
    output_subdir: str = "v1"

    @property
    def data_dir(self) -> Path:
        return self.repo_dir / self.data_subdir

    @property
    def output_dir(self) -> Path:
        return self.repo_dir / self.output_subdir


@dataclass
class MutationResult:
    changed: bool
    catalog_version: int
    checksum: str
    commit: str | None
    pushed: bool | None
    result: Any = None
    summary: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "changed": self.changed,
            "catalog_version": self.catalog_version,
            "checksum": self.checksum,
            "commit": self.commit,
            "pushed": self.pushed,
            "summary": self.summary,
        }
        if isinstance(self.result, Mapping):
            payload.update(self.result)
        payload.update(self.extra)
        return payload


LOCK_FILE = "bookmarks-publisher.lock"


class Publisher:
    def __init__(self, config: PublisherConfig) -> None:
        self.config = config
        self.lock = threading.RLock()
        self.push_pending = False
        self.last_error: str | None = None

    @contextlib.contextmanager
    def _exclusive(self) -> Iterator[None]:
        """Serialize against other threads and against deploy.sh via a shared flock."""
        with self.lock:
            lock_path = self.config.repo_dir / ".git" / LOCK_FILE
            try:
                handle = open(lock_path, "a+b")  # noqa: SIM115 - held for the block below
            except OSError as error:
                raise PublishError(f"Cannot open {lock_path}: {error}") from error
            try:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                handle.close()

    # -- read side --------------------------------------------------------- #

    def load(self) -> Catalog:
        return load_catalog(self.config.data_dir)

    def state(self) -> dict[str, Any]:
        with self._exclusive():
            catalog = self.load()
            files = render_api(catalog)
            stale = stale_paths(self.config.output_dir, files)
            state: dict[str, Any] = {
                "catalog_version": catalog.catalog_version,
                "checksum": catalog_checksum(files),
                "counts": {
                    "topics": len(catalog.topics),
                    "verses": catalog.link_count(),
                    "locales": len(catalog.locales) + 1,
                    "retired": len(catalog.retired),
                },
                "output_stale": bool(stale),
                "git": self._git_state(),
            }
            return state

    # -- write side -------------------------------------------------------- #

    def mutate(
        self,
        actor: Actor,
        summary: str,
        change: Callable[[Catalog], Any],
        *,
        expected_catalog_version: int | None = None,
    ) -> MutationResult:
        with self._exclusive():
            self._integrate_remote()
            self._require_clean()
            catalog = self.load()
            if (
                expected_catalog_version is not None
                and catalog.catalog_version != expected_catalog_version
            ):
                raise VersionConflictError(
                    f"The catalogue is at version {catalog.catalog_version}, "
                    f"not {expected_catalog_version}."
                )
            before = render_sources(catalog)
            result = change(catalog)
            after = render_sources(catalog)
            if before == after:
                files = render_api(catalog)
                return MutationResult(
                    changed=False,
                    catalog_version=catalog.catalog_version,
                    checksum=catalog_checksum(files),
                    commit=self._head(),
                    pushed=None,
                    result=result,
                    summary=summary,
                )
            catalog.catalog_version += 1
            save_catalog(self.config.data_dir, catalog)
            files = render_api(catalog)
            write_tree(self.config.output_dir, files)
            checksum = catalog_checksum(files)
            message = (
                f"{summary}\n\n"
                f"Contributor: {actor.id}\n"
                f"Catalog-Version: {catalog.catalog_version}\n"
                f"Checksum: {checksum}\n"
            )
            try:
                self._git("add", "-A", "--", self.config.data_subdir, self.config.output_subdir)
                self._git(
                    "-c",
                    "commit.gpgsign=false",
                    "commit",
                    "--quiet",
                    "--no-verify",
                    f"--author={actor.name} <{actor.email}>",
                    "-m",
                    message,
                )
            except PublishError:
                # Leave the checkout exactly as HEAD describes it so the next
                # mutation is not refused for a dirty tree we created.
                self._restore_tracked_tree()
                raise
            commit = self._head()
            pushed = self._push()
            self._publish_release()
            log.info(
                "catalogue %s -> version %s by %s (%s)",
                commit[:12],
                catalog.catalog_version,
                actor.id,
                summary,
            )
            return MutationResult(
                changed=True,
                catalog_version=catalog.catalog_version,
                checksum=checksum,
                commit=commit,
                pushed=pushed,
                result=result,
                summary=summary,
            )

    def sync(self) -> dict[str, Any]:
        """Integrate the remote branch, verify the committed tree, publish the release."""
        with self._exclusive():
            self._integrate_remote()
            self._require_clean()
            catalog = self.load()
            files = render_api(catalog)
            stale = stale_paths(self.config.output_dir, files)
            if stale:
                raise PublishError(
                    f"The committed {self.config.output_subdir}/ tree is stale for "
                    f"{len(stale)} file(s); run `getbible-bookmarks build` and commit."
                )
            self._publish_release()
            pushed = self._push() if self.push_pending else None
            return {
                "catalog_version": catalog.catalog_version,
                "checksum": catalog_checksum(files),
                "commit": self._head(),
                "pushed": pushed,
                "git": self._git_state(),
            }

    def retry_push(self) -> bool | None:
        with self._exclusive():
            if not self.push_pending:
                return None
            return self._push()

    # -- git plumbing ------------------------------------------------------ #

    def _integrate_remote(self) -> None:
        remote = self.config.remote
        if remote is None:
            return
        branch = self.config.branch
        current = self._git_output("symbolic-ref", "--quiet", "--short", "HEAD")
        if current != branch:
            raise PublishError(f"The checkout is on {current!r}; the service publishes {branch!r}.")
        try:
            self._git("fetch", "--quiet", remote, f"refs/heads/{branch}")
        except PublishError as error:
            self.last_error = f"fetch failed: {error}"
            log.warning("fetch from %s failed; continuing with the local branch: %s", remote, error)
            return
        local = self._head()
        upstream = self._git_output("rev-parse", "--verify", "FETCH_HEAD^{commit}")
        if local == upstream:
            return
        if self._is_ancestor(local, upstream):
            self._git("merge", "--quiet", "--ff-only", "FETCH_HEAD")
            return
        if self._is_ancestor(upstream, local):
            self.push_pending = True
            return
        try:
            self._git("rebase", "--quiet", "FETCH_HEAD")
            self.push_pending = True
        except PublishError as error:
            with contextlib.suppress(PublishError):
                self._git("rebase", "--abort")
            raise DivergedRepositoryError(
                f"Local commits conflict with {remote}/{branch}; resolve the checkout manually."
            ) from error

    def _push(self) -> bool | None:
        if self.config.remote is None or not self.config.push:
            return None
        try:
            self._git(
                "push",
                "--quiet",
                self.config.remote,
                f"HEAD:refs/heads/{self.config.branch}",
            )
        except PublishError as error:
            self.push_pending = True
            self.last_error = f"push failed: {error}"
            log.warning("push to %s failed; will retry: %s", self.config.remote, error)
            return False
        self.push_pending = False
        self.last_error = None
        return True

    def _publish_release(self) -> None:
        if self.config.release_dir is None:
            return
        publish_release(self.config.output_dir, self.config.release_dir)

    def _restore_tracked_tree(self) -> None:
        paths = ("--", self.config.data_subdir, self.config.output_subdir)
        with contextlib.suppress(PublishError):
            self._git("reset", "--quiet", "HEAD", *paths)
        with contextlib.suppress(PublishError):
            self._git("checkout", "--quiet", "HEAD", *paths)
        with contextlib.suppress(PublishError):
            self._git("clean", "--quiet", "-fd", *paths)

    def _require_clean(self) -> None:
        status = self._git_output(
            "status",
            "--porcelain",
            "--untracked-files=all",
            "--",
            self.config.data_subdir,
            self.config.output_subdir,
        )
        if status.strip():
            raise PublishError(
                "The checkout has uncommitted changes under data/ or v1/; "
                "commit or discard them before the service can publish."
            )

    def _git_state(self) -> dict[str, Any]:
        state: dict[str, Any] = {
            "branch": self.config.branch,
            "remote": self.config.remote,
            "push_pending": self.push_pending,
            "last_error": self.last_error,
        }
        try:
            state["head"] = self._head()
        except PublishError as error:
            state["head"] = None
            state["last_error"] = str(error)
        return state

    def _head(self) -> str:
        return self._git_output("rev-parse", "--verify", "HEAD^{commit}")

    def _is_ancestor(self, ancestor: str, descendant: str) -> bool:
        completed = self._run("merge-base", "--is-ancestor", ancestor, descendant)
        if completed.returncode in (0, 1):
            return completed.returncode == 0
        raise PublishError(_describe(completed))

    def _git(self, *arguments: str) -> None:
        completed = self._run(*arguments)
        if completed.returncode != 0:
            raise PublishError(_describe(completed))

    def _git_output(self, *arguments: str) -> str:
        completed = self._run(*arguments)
        if completed.returncode != 0:
            raise PublishError(_describe(completed))
        return completed.stdout.strip()

    def _run(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", "/nonexistent"),
            "LANG": "C.UTF-8",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_COMMITTER_NAME": self.config.committer_name,
            "GIT_COMMITTER_EMAIL": self.config.committer_email,
        }
        for key in ("SSH_AUTH_SOCK", "GIT_SSH_COMMAND", "XDG_CONFIG_HOME", "GIT_CONFIG_GLOBAL"):
            if key in os.environ:
                environment[key] = os.environ[key]
        try:
            return subprocess.run(
                ["git", *arguments],
                cwd=self.config.repo_dir,
                env=environment,
                capture_output=True,
                text=True,
                timeout=self.config.git_timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise PublishError(
                f"git {arguments[0]} timed out after {self.config.git_timeout}s."
            ) from error
        except OSError as error:
            raise PublishError(f"git could not be executed: {error}") from error


def _describe(completed: subprocess.CompletedProcess[str]) -> str:
    detail = (completed.stderr or completed.stdout or "").strip().splitlines()
    tail = detail[-1] if detail else "no output"
    return f"git {' '.join(completed.args[1:3])} failed ({completed.returncode}): {tail}"


__all__ = [
    "RESOURCES",
    "Actor",
    "CatalogError",
    "DivergedRepositoryError",
    "MutationResult",
    "PublishError",
    "Publisher",
    "PublisherConfig",
    "VersionConflictError",
]
