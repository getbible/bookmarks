"""Tornado application exposing the authenticated management API.

The service runs behind nginx on a loopback port. Every request except the
health probe requires a contributor bearer token; every mutation is applied by
the :class:`~getbible_bookmarks.publisher.Publisher` on a single worker thread
so changes are serialized, validated, committed and pushed one at a time.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import signal
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tornado.httpserver
import tornado.ioloop
import tornado.web
from tornado.httputil import HTTPServerRequest

from ..build import catalog_checksum, render_api
from ..bundle import apply_bundle
from ..canon import reference
from ..model import (
    Catalog,
    CatalogError,
    NotFoundError,
    RetiredError,
    Topic,
    allowed_keys,
    coordinate,
    locale_code,
    topic_id,
)
from ..publisher import (
    Actor,
    DivergedRepositoryError,
    MutationResult,
    Publisher,
    PublisherConfig,
    PublishError,
    VersionConflictError,
)
from .auth import AuthError, Contributor, ContributorStore
from .ratelimit import RateLimiter

log = logging.getLogger("getbible.bookmarks.api")
access_log = logging.getLogger("getbible.bookmarks.access")

TOPIC_PATTERN = r"([a-z0-9-]{1,80})"
LOCALE_PATTERN = r"([a-z0-9-]{2,16})"
INT_PATTERN = r"([0-9]{1,4})"


@dataclass(frozen=True)
class ApiConfig:
    prefix: str = "/v1/manage"
    bind: str = "127.0.0.1"
    port: int = 8787
    max_body_bytes: int = 1024 * 1024
    rate_capacity: float = 60.0
    rate_refill_per_second: float = 1.0
    auth_failure_capacity: float = 10.0
    auth_failure_refill_per_second: float = 0.05
    trust_proxy_headers: bool = True
    allowed_origins: tuple[str, ...] = ()
    push_retry_seconds: float = 60.0


class ApiError(tornado.web.HTTPError):
    def __init__(self, status: int, code: str, message: str, **extra: Any) -> None:
        super().__init__(status, message)
        self.code = code
        self.message = message
        self.extra = extra


class Services:
    """Shared state handed to every handler."""

    def __init__(self, publisher: Publisher, store: ContributorStore, config: ApiConfig) -> None:
        self.publisher = publisher
        self.store = store
        self.config = config
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="publisher")
        self.request_limiter = RateLimiter(config.rate_capacity, config.rate_refill_per_second)
        self.auth_limiter = RateLimiter(
            config.auth_failure_capacity, config.auth_failure_refill_per_second
        )

    async def run(self, function: Callable[[], Any]) -> Any:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self.executor, function)


class BaseHandler(tornado.web.RequestHandler):
    services: Services
    contributor: Contributor | None
    request_id: str
    public = False

    def initialize(self, services: Services) -> None:
        self.services = services
        self.contributor = None
        self.request_id = secrets.token_hex(8)
        self.started = time.monotonic()

    # -- lifecycle --------------------------------------------------------- #

    def set_default_headers(self) -> None:
        self.clear_header("Server")
        self.set_header("Content-Type", "application/json; charset=utf-8")
        self.set_header("Cache-Control", "no-store")
        self.set_header("X-Content-Type-Options", "nosniff")
        self.set_header("Referrer-Policy", "no-referrer")
        self.set_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        self.set_header("X-Frame-Options", "DENY")

    def prepare(self) -> None:
        self.set_header("X-Request-Id", self.request_id)
        self._apply_cors()
        if self.request.method == "OPTIONS":
            return
        if self.public:
            return
        self._authenticate()

    def options(self, *_: str) -> None:
        if not self._cors_allowed():
            raise ApiError(404, "not_found", "Unknown resource.")
        self.set_status(204)
        self.finish()

    def on_finish(self) -> None:
        access_log.info(
            "%s %s %s %s %s %.1fms",
            self.request_id,
            self.request.method,
            self.request.path,
            self.get_status(),
            self.contributor.id if self.contributor else "-",
            (time.monotonic() - self.started) * 1000,
        )

    def log_exception(self, typ: Any, value: Any, tb: Any) -> None:
        if isinstance(value, ApiError):
            log.debug("%s %s %s", self.request_id, value.status_code, value.message)
            return
        super().log_exception(typ, value, tb)

    def write_error(self, status_code: int, **kwargs: Any) -> None:
        error = kwargs.get("exc_info", (None, None, None))[1]
        if isinstance(error, ApiError):
            code, message, extra = error.code, error.message, error.extra
            if error.status_code == 401:
                self.set_header("WWW-Authenticate", 'Bearer realm="getbible-bookmarks"')
            if error.status_code == 429:
                self.set_header("Retry-After", str(_retry_after(extra.pop("retry_after", 1.0))))
        elif isinstance(error, tornado.web.HTTPError):
            code = {404: "not_found", 405: "method_not_allowed"}.get(status_code, "http_error")
            message = error.log_message or "Request failed."
            extra = {}
        else:
            code, message, extra = "internal_error", "The request could not be processed.", {}
            log.exception("%s unhandled error", self.request_id)
        self.set_header("Content-Type", "application/json; charset=utf-8")
        self.finish(
            _json(
                {
                    "error": {"code": code, "message": message, **extra},
                    "request_id": self.request_id,
                }
            )
        )

    # -- helpers ----------------------------------------------------------- #

    def _cors_allowed(self) -> bool:
        origin = self.request.headers.get("Origin")
        return bool(origin) and origin in self.services.config.allowed_origins

    def _apply_cors(self) -> None:
        if not self._cors_allowed():
            return
        self.set_header("Access-Control-Allow-Origin", self.request.headers["Origin"])
        self.set_header("Vary", "Origin")
        self.set_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.set_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.set_header("Access-Control-Max-Age", "600")

    def _authenticate(self) -> None:
        client = self.request.remote_ip or "unknown"
        header = self.request.headers.get("Authorization", "")
        scheme, _, token = header.partition(" ")
        try:
            contributor = (
                self.services.store.authenticate(token.strip())
                if scheme.lower() == "bearer"
                else None
            )
        except AuthError as error:
            log.error("%s contributor registry unreadable: %s", self.request_id, error)
            raise ApiError(
                503, "registry_unavailable", "The contributor registry is unavailable."
            ) from error
        if contributor is None:
            wait = self.services.auth_limiter.acquire(client)
            if wait > 0:
                raise ApiError(
                    429, "rate_limited", "Too many failed authentications.", retry_after=wait
                )
            raise ApiError(401, "unauthorized", "A valid contributor bearer token is required.")
        wait = self.services.request_limiter.acquire(contributor.id)
        if wait > 0:
            raise ApiError(429, "rate_limited", "Request rate limit exceeded.", retry_after=wait)
        self.contributor = contributor

    def require_role(self, role: str) -> Contributor:
        assert self.contributor is not None
        if not self.contributor.has_role(role):
            raise ApiError(403, "forbidden", f"This operation requires the {role} role.")
        return self.contributor

    def actor(self) -> Actor:
        assert self.contributor is not None
        return Actor(
            id=self.contributor.id, name=self.contributor.name, email=self.contributor.email
        )

    def json_body(self, allowed: tuple[str, ...], *, optional: bool = False) -> dict[str, Any]:
        raw = self.request.body
        if not raw.strip():
            if optional:
                return {}
            raise ApiError(400, "invalid_json", "A JSON object body is required.")
        content_type = self.request.headers.get("Content-Type", "")
        if content_type and not content_type.lower().startswith("application/json"):
            raise ApiError(415, "unsupported_media_type", "Send application/json.")
        try:
            document = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
        except (UnicodeDecodeError, ValueError) as error:
            raise ApiError(400, "invalid_json", f"The body is not valid JSON: {error}") from error
        try:
            return dict(allowed_keys(document, (*allowed, "expected_catalog_version"), "body"))
        except CatalogError as error:
            raise ApiError(400, "invalid_request", str(error)) from error

    def expected_version(self, body: Mapping[str, Any]) -> int | None:
        value = body.get("expected_catalog_version")
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ApiError(
                400, "invalid_request", "expected_catalog_version must be a positive integer."
            )
        return value

    async def mutate(
        self,
        summary: str,
        change: Callable[[Catalog], Any],
        *,
        role: str = "contributor",
        expected_catalog_version: int | None = None,
    ) -> MutationResult:
        self.require_role(role)
        actor = self.actor()
        publisher = self.services.publisher
        try:
            result: MutationResult = await self.services.run(
                lambda: publisher.mutate(
                    actor, summary, change, expected_catalog_version=expected_catalog_version
                )
            )
        except VersionConflictError as error:
            raise ApiError(409, "version_conflict", str(error)) from error
        except DivergedRepositoryError as error:
            raise ApiError(503, "repository_diverged", str(error)) from error
        except PublishError as error:
            raise ApiError(503, "publish_failed", str(error)) from error
        except CatalogError as error:
            raise _catalog_error(error) from error
        return result

    async def snapshot(self) -> Catalog:
        try:
            catalog: Catalog = await self.services.run(self.services.publisher.load)
        except CatalogError as error:
            raise ApiError(
                503, "catalog_invalid", f"The committed catalogue is invalid: {error}"
            ) from error
        return catalog

    def send(self, payload: Mapping[str, Any], status: int = 200) -> None:
        self.set_status(status)
        self.finish(_json({**payload, "request_id": self.request_id}))


def _catalog_error(error: CatalogError) -> ApiError:
    if isinstance(error, NotFoundError):
        return ApiError(404, "not_found", str(error))
    if isinstance(error, RetiredError):
        return ApiError(410, "retired", str(error))
    return ApiError(422, "invalid_change", str(error))


def _retry_after(value: object) -> int:
    seconds = value if isinstance(value, int | float) else 1.0
    if seconds != seconds or seconds == float("inf"):  # noqa: PLR0124 - NaN check
        seconds = 3600.0
    return max(1, min(3600, int(seconds) + 1))


def _reject_constant(name: str) -> None:
    raise ValueError(f"{name} is not valid JSON.")


def _json(payload: Mapping[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def _topic_payload(catalog: Catalog, topic: Topic, *, verses: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": topic.id,
        "name": topic.name,
        "color": topic.color,
        "aliases": list(topic.aliases),
        "default": topic.default,
    }
    if verses:
        payload["verses"] = [list(item) for item in catalog.sorted_verses(topic.id)]
        payload["names"] = {
            code: locale.topics[topic.id]
            for code, locale in sorted(catalog.locales.items())
            if topic.id in locale.topics
        }
    else:
        payload["verses"] = len(catalog.links.get(topic.id, ()))
    return payload


# --------------------------------------------------------------------------- #
# Handlers
# --------------------------------------------------------------------------- #


class HealthHandler(BaseHandler):
    public = True

    def get(self) -> None:
        self.send({"status": "ok"})


class StatusHandler(BaseHandler):
    async def get(self) -> None:
        assert self.contributor is not None
        try:
            state: dict[str, Any] = await self.services.run(self.services.publisher.state)
        except (CatalogError, PublishError) as error:
            raise ApiError(503, "catalog_unavailable", str(error)) from error
        self.send({"contributor": self.contributor.public(), **state})


class TopicsHandler(BaseHandler):
    async def get(self) -> None:
        catalog = await self.snapshot()
        self.send(
            {
                "catalog_version": catalog.catalog_version,
                "topics": [
                    _topic_payload(catalog, topic, verses=False)
                    for topic in catalog.sorted_topics()
                ],
                "retired": [
                    {"id": item.id, "name": item.name, "retired_in": item.retired_in}
                    for item in catalog.sorted_retired()
                ],
            }
        )

    async def post(self) -> None:
        body = self.json_body(("id", "name", "color", "aliases", "default", "verses", "names"))
        if "name" not in body or "color" not in body:
            raise ApiError(400, "invalid_request", "name and color are required.")
        created: dict[str, Topic] = {}

        def change(catalog: Catalog) -> dict[str, Any]:
            topic = catalog.create_topic(
                identifier=body.get("id"),
                name=body["name"],
                color_value=body["color"],
                alias_values=body.get("aliases"),
                default=body.get("default", False),
                verses=body.get("verses"),
                names=body.get("names"),
            )
            created["topic"] = topic
            return {"topic": _topic_payload(catalog, topic, verses=True)}

        label = body.get("id") or body["name"]
        result = await self.mutate(
            f"Create topic {label}",
            change,
            role="maintainer",
            expected_catalog_version=self.expected_version(body),
        )
        self.send(result.as_dict(), status=201 if result.changed else 200)


class TopicHandler(BaseHandler):
    async def get(self, identifier: str) -> None:
        catalog = await self.snapshot()
        topic = catalog.topics.get(identifier)
        if topic is None:
            raise _catalog_error(_lookup_error(catalog, identifier))
        self.send(
            {
                "catalog_version": catalog.catalog_version,
                "topic": _topic_payload(catalog, topic, verses=True),
            }
        )

    async def put(self, identifier: str) -> None:
        body = self.json_body(("name", "color", "aliases", "default"))
        if not any(key in body for key in ("name", "color", "aliases", "default")):
            raise ApiError(
                400, "invalid_request", "Provide at least one of name, color, aliases, default."
            )

        def change(catalog: Catalog) -> dict[str, Any]:
            topic = catalog.update_topic(
                identifier,
                name=body.get("name"),
                color_value=body.get("color"),
                alias_values=body.get("aliases"),
                default=body.get("default"),
            )
            return {"topic": _topic_payload(catalog, topic, verses=False)}

        result = await self.mutate(
            f"Update topic {identifier}",
            change,
            role="maintainer",
            expected_catalog_version=self.expected_version(body),
        )
        self.send(result.as_dict())

    async def delete(self, identifier: str) -> None:
        body = self.json_body(("reason",), optional=True)

        def change(catalog: Catalog) -> dict[str, Any]:
            tombstone = catalog.retire_topic(identifier, reason=body.get("reason"))
            return {
                "retired": {
                    "id": tombstone.id,
                    "name": tombstone.name,
                    "retired_in": tombstone.retired_in,
                }
            }

        result = await self.mutate(
            f"Retire topic {identifier}",
            change,
            role="maintainer",
            expected_catalog_version=self.expected_version(body),
        )
        self.send(result.as_dict())


class TopicVersesHandler(BaseHandler):
    async def put(self, identifier: str) -> None:
        await self._apply(identifier, "replace")

    async def post(self, identifier: str) -> None:
        await self._apply(identifier, "add")

    async def delete(self, identifier: str) -> None:
        await self._apply(identifier, "remove")

    async def _apply(self, identifier: str, operation: str) -> None:
        body = self.json_body(("verses",))
        if "verses" not in body:
            raise ApiError(400, "invalid_request", "verses is required.")
        verses = body["verses"]
        count = len(verses) if isinstance(verses, list) else 0

        def change(catalog: Catalog) -> dict[str, Any]:
            if operation == "add":
                added = catalog.add_verses(identifier, verses)
                outcome = {"added": added, "removed": 0}
            elif operation == "remove":
                removed = catalog.remove_verses(identifier, verses)
                outcome = {"added": 0, "removed": removed}
            else:
                added, removed = catalog.replace_verses(identifier, verses)
                outcome = {"added": added, "removed": removed}
            outcome["verses"] = len(catalog.links.get(identifier, ()))
            return outcome

        verb = {"add": "Add", "remove": "Remove", "replace": "Replace with"}[operation]
        result = await self.mutate(
            f"{verb} {count} verse link(s) {'for' if operation == 'replace' else 'on'} {identifier}",
            change,
            expected_catalog_version=self.expected_version(body),
        )
        self.send(result.as_dict())


class TopicVerseHandler(BaseHandler):
    async def delete(self, identifier: str, book: str, chapter: str, verse: str) -> None:
        try:
            triple = coordinate([int(book), int(chapter), int(verse)])
        except CatalogError as error:
            raise ApiError(404, "not_found", str(error)) from error

        def change(catalog: Catalog) -> dict[str, Any]:
            removed = catalog.remove_verses(identifier, [list(triple)])
            return {
                "added": 0,
                "removed": removed,
                "verses": len(catalog.links.get(identifier, ())),
            }

        result = await self.mutate(f"Remove {reference(*triple)} from {identifier}", change)
        self.send(result.as_dict())


class LocalesHandler(BaseHandler):
    async def get(self) -> None:
        catalog = await self.snapshot()
        self.send(
            {
                "catalog_version": catalog.catalog_version,
                "locales": [
                    {"code": locale.code, "name": locale.name, "topics": len(locale.topics)}
                    for locale in catalog.sorted_locales()
                ],
            }
        )


class LocaleHandler(BaseHandler):
    async def get(self, code: str) -> None:
        catalog = await self.snapshot()
        locale = catalog.locales.get(code)
        if locale is None:
            raise ApiError(404, "not_found", f"Locale {code!r} does not exist.")
        self.send(
            {
                "catalog_version": catalog.catalog_version,
                "locale": {
                    "code": locale.code,
                    "name": locale.name,
                    "topics": dict(sorted(locale.topics.items())),
                },
            }
        )

    async def put(self, code: str) -> None:
        body = self.json_body(("name", "topics", "replace"))
        if "topics" not in body:
            raise ApiError(400, "invalid_request", "topics is required.")
        replace_all = body.get("replace", False)
        if not isinstance(replace_all, bool):
            raise ApiError(400, "invalid_request", "replace must be true or false.")

        def change(catalog: Catalog) -> dict[str, Any]:
            changed = catalog.set_locale_names(
                code, body["topics"], name=body.get("name"), replace_all=replace_all
            )
            return {
                "names_changed": changed,
                "names": len(catalog.locales[locale_code(code)].topics),
            }

        count = len(body["topics"]) if isinstance(body["topics"], Mapping) else 0
        result = await self.mutate(
            f"Update {code} translations ({count} name(s))",
            change,
            expected_catalog_version=self.expected_version(body),
        )
        self.send(result.as_dict())

    async def delete(self, code: str) -> None:
        body = self.json_body((), optional=True)

        def change(catalog: Catalog) -> dict[str, Any]:
            if not catalog.delete_locale(code):
                raise NotFoundError(f"Locale {code!r} does not exist.")
            return {"deleted": code}

        result = await self.mutate(
            f"Delete locale {code}",
            change,
            role="maintainer",
            expected_catalog_version=self.expected_version(body),
        )
        self.send(result.as_dict())


class LocaleTopicHandler(BaseHandler):
    async def delete(self, code: str, identifier: str) -> None:
        def change(catalog: Catalog) -> dict[str, Any]:
            if not catalog.delete_locale_name(code, identifier):
                raise NotFoundError(f"Locale {code!r} has no name for topic {identifier!r}.")
            return {"deleted": identifier}

        result = await self.mutate(f"Remove {code} translation of {identifier}", change)
        self.send(result.as_dict())


class BundlesHandler(BaseHandler):
    async def post(self) -> None:
        body = self.json_body(("schema_version", "topics", "associations"))
        expected = self.expected_version(body)
        bundle = {key: value for key, value in body.items() if key != "expected_catalog_version"}
        topics = len(bundle.get("topics", [])) if isinstance(bundle.get("topics"), list) else 0

        def change(catalog: Catalog) -> dict[str, Any]:
            return apply_bundle(catalog, bundle).as_dict()

        result = await self.mutate(
            f"Apply contribution bundle ({topics} topic(s))",
            change,
            expected_catalog_version=expected,
        )
        self.send(result.as_dict())


class SyncHandler(BaseHandler):
    async def post(self) -> None:
        self.require_role("maintainer")
        try:
            state: dict[str, Any] = await self.services.run(self.services.publisher.sync)
        except DivergedRepositoryError as error:
            raise ApiError(503, "repository_diverged", str(error)) from error
        except (PublishError, CatalogError) as error:
            raise ApiError(503, "sync_failed", str(error)) from error
        self.send(state)


class NotFoundHandler(BaseHandler):
    public = True

    def prepare(self) -> None:
        raise ApiError(404, "not_found", "Unknown resource.")


def _lookup_error(catalog: Catalog, identifier: str) -> CatalogError:
    try:
        catalog.require_topic(topic_id(identifier))
    except CatalogError as error:
        return error
    return NotFoundError(f"Topic {identifier!r} does not exist.")


# --------------------------------------------------------------------------- #
# Application factory
# --------------------------------------------------------------------------- #


def make_app(
    publisher: Publisher, store: ContributorStore, config: ApiConfig
) -> tornado.web.Application:
    services = Services(publisher, store, config)
    prefix = config.prefix.rstrip("/")
    routes: list[tuple[str, type[BaseHandler]]] = [
        (rf"{prefix}/health", HealthHandler),
        (rf"{prefix}/status", StatusHandler),
        (rf"{prefix}/topics", TopicsHandler),
        (rf"{prefix}/topics/{TOPIC_PATTERN}", TopicHandler),
        (rf"{prefix}/topics/{TOPIC_PATTERN}/verses", TopicVersesHandler),
        (
            rf"{prefix}/topics/{TOPIC_PATTERN}/verses/{INT_PATTERN}/{INT_PATTERN}/{INT_PATTERN}",
            TopicVerseHandler,
        ),
        (rf"{prefix}/locales", LocalesHandler),
        (rf"{prefix}/locales/{LOCALE_PATTERN}", LocaleHandler),
        (rf"{prefix}/locales/{LOCALE_PATTERN}/topics/{TOPIC_PATTERN}", LocaleTopicHandler),
        (rf"{prefix}/bundles", BundlesHandler),
        (rf"{prefix}/sync", SyncHandler),
    ]
    application = tornado.web.Application(
        [(pattern, handler, {"services": services}) for pattern, handler in routes],
        default_handler_class=NotFoundHandler,
        default_handler_args={"services": services},
        log_function=lambda handler: None,
        compress_response=False,
    )
    application.settings["services"] = services
    return application


def config_from_environment(
    environment: Mapping[str, str], *, repo_dir: Path
) -> tuple[PublisherConfig, ApiConfig, Path]:
    def flag(name: str, default: bool) -> bool:
        value = environment.get(name)
        if value is None or value == "":
            return default
        return value.strip().lower() in ("1", "true", "yes", "on")

    def number(name: str, default: float) -> float:
        value = environment.get(name)
        try:
            return float(value) if value else default
        except ValueError as error:
            raise ValueError(f"{name} must be a number.") from error

    release = environment.get("BOOKMARKS_RELEASE_DIR")
    remote = environment.get("BOOKMARKS_GIT_REMOTE", "origin").strip()
    publisher = PublisherConfig(
        repo_dir=Path(environment.get("BOOKMARKS_REPO_DIR") or repo_dir).resolve(),
        remote=remote or None,
        branch=environment.get("BOOKMARKS_GIT_BRANCH", "main").strip() or "main",
        push=flag("BOOKMARKS_GIT_PUSH", True),
        committer_name=environment.get("BOOKMARKS_COMMITTER_NAME", "getBible Bookmarks Service"),
        committer_email=environment.get("BOOKMARKS_COMMITTER_EMAIL", "bookmarks@getbible.net"),
        release_dir=Path(release).resolve() if release else None,
        git_timeout=number("BOOKMARKS_GIT_TIMEOUT", 180.0),
    )
    origins = tuple(
        origin.strip()
        for origin in environment.get("BOOKMARKS_ALLOWED_ORIGINS", "").split(",")
        if origin.strip()
    )
    api = ApiConfig(
        prefix="/" + environment.get("BOOKMARKS_API_PREFIX", "/v1/manage").strip("/"),
        bind=environment.get("BOOKMARKS_BIND", "127.0.0.1"),
        port=int(number("BOOKMARKS_PORT", 8787)),
        max_body_bytes=int(number("BOOKMARKS_MAX_BODY_BYTES", 1024 * 1024)),
        rate_capacity=number("BOOKMARKS_RATE_CAPACITY", 60.0),
        rate_refill_per_second=number("BOOKMARKS_RATE_REFILL_PER_SECOND", 1.0),
        auth_failure_capacity=number("BOOKMARKS_AUTH_FAILURE_CAPACITY", 10.0),
        auth_failure_refill_per_second=number("BOOKMARKS_AUTH_FAILURE_REFILL_PER_SECOND", 0.05),
        trust_proxy_headers=flag("BOOKMARKS_TRUST_PROXY_HEADERS", True),
        allowed_origins=origins,
        push_retry_seconds=number("BOOKMARKS_PUSH_RETRY_SECONDS", 60.0),
    )
    contributors = Path(
        environment.get("BOOKMARKS_CONTRIBUTORS_FILE")
        or (publisher.repo_dir.parent / "contributors.json")
    ).resolve()
    return publisher, api, contributors


def serve_from_environment(*, repo_dir: Path) -> int:
    publisher_config, api_config, contributors = config_from_environment(
        os.environ, repo_dir=repo_dir
    )
    publisher = Publisher(publisher_config)
    store = ContributorStore(contributors)
    store.load()
    publisher.load()
    application = make_app(publisher, store, api_config)
    return run_server(application, api_config)


def run_server(application: tornado.web.Application, config: ApiConfig) -> int:
    services: Services = application.settings["services"]
    server = tornado.httpserver.HTTPServer(
        application,
        xheaders=config.trust_proxy_headers,
        max_body_size=config.max_body_bytes,
        max_buffer_size=config.max_body_bytes * 2,
        idle_connection_timeout=60,
        body_timeout=30,
    )
    server.listen(config.port, address=config.bind, reuse_port=False)
    loop = tornado.ioloop.IOLoop.current()

    def retry_push() -> None:
        services.executor.submit(services.publisher.retry_push)

    retry = tornado.ioloop.PeriodicCallback(retry_push, config.push_retry_seconds * 1000)
    retry.start()

    def shutdown(*_: object) -> None:
        log.info("shutting down")
        retry.stop()
        server.stop()
        loop.add_callback(loop.stop)

    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, shutdown)
    log.info("management API listening on %s:%s%s", config.bind, config.port, config.prefix)
    loop.start()
    services.executor.shutdown(wait=True)
    return 0


def _unused(_: HTTPServerRequest) -> None:  # pragma: no cover
    return None


__all__ = [
    "ApiConfig",
    "ApiError",
    "Services",
    "catalog_checksum",
    "config_from_environment",
    "make_app",
    "render_api",
    "run_server",
    "serve_from_environment",
]
