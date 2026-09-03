from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from tornado.httpclient import HTTPResponse
from tornado.testing import AsyncHTTPTestCase

from getbible_bookmarks.api.app import ApiConfig, config_from_environment, make_app
from getbible_bookmarks.api.auth import ContributorStore
from getbible_bookmarks.publisher import Publisher, PublisherConfig
from getbible_bookmarks.sources import load_catalog
from tests.test_publisher import RepositoryFixture, git

PREFIX = "/v1/manage"


class ApiTestCase(AsyncHTTPTestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.folder.name)
        self.fixture = RepositoryFixture(root)
        self.store = ContributorStore(root / "contributors.json")
        _, self.maintainer = self.store.create(
            contributor_id="paul", name="Paul", email="paul@example.org", role="maintainer"
        )
        _, self.contributor = self.store.create(
            contributor_id="jaco", name="Brother Jaco", email="jaco@example.org", role="contributor"
        )
        _, revoked = self.store.create(
            contributor_id="gone", name="Gone", email="gone@example.org", role="maintainer"
        )
        self.store.revoke("gone")
        self.revoked = revoked
        self.publisher = Publisher(
            PublisherConfig(
                repo_dir=self.fixture.clone,
                remote="origin",
                branch="main",
                release_dir=root / "srv",
            )
        )
        super().setUp()

    def tearDown(self) -> None:
        super().tearDown()
        self.get_app().settings["services"].executor.shutdown(wait=True)
        self.folder.cleanup()

    def get_app(self) -> Any:
        if not hasattr(self, "_app_instance"):
            self._app_instance = make_app(
                self.publisher,
                self.store,
                ApiConfig(
                    prefix=PREFIX,
                    rate_capacity=100,
                    auth_failure_capacity=5,
                    auth_failure_refill_per_second=0.0,
                    allowed_origins=("https://admin.example.org",),
                ),
            )
        return self._app_instance

    def call(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        body: Any = None,
        headers: dict[str, str] | None = None,
        raw: bytes | None = None,
    ) -> HTTPResponse:
        request_headers = dict(headers or {})
        if token is not None:
            request_headers["Authorization"] = f"Bearer {token}"
        payload = raw
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")
        return self.fetch(
            f"{PREFIX}{path}",
            method=method,
            headers=request_headers,
            body=payload,
            allow_nonstandard_methods=True,
            raise_error=False,
        )

    @staticmethod
    def payload(response: HTTPResponse) -> dict[str, Any]:
        document = json.loads(response.body.decode("utf-8"))
        assert isinstance(document, dict)
        return document


class AuthenticationTests(ApiTestCase):
    def test_health_is_public_and_hardened(self) -> None:
        response = self.call("GET", "/health")
        self.assertEqual(response.code, 200)
        self.assertEqual(self.payload(response)["status"], "ok")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertNotIn("Server", response.headers)
        self.assertNotIn("Access-Control-Allow-Origin", response.headers)

    def test_requires_bearer_token(self) -> None:
        for headers in (
            {},
            {"Authorization": "Basic abc"},
            {"Authorization": "Bearer gbb_short"},
            {"Authorization": f"Bearer {self.revoked}"},
        ):
            response = self.call("GET", "/status", headers=headers)
            self.assertEqual(response.code, 401, headers)
            self.assertEqual(self.payload(response)["error"]["code"], "unauthorized")
            self.assertIn("Bearer", response.headers["WWW-Authenticate"])

    def test_failed_authentication_is_rate_limited(self) -> None:
        for _ in range(5):
            self.assertEqual(self.call("GET", "/status").code, 401)
        response = self.call("GET", "/status")
        self.assertEqual(response.code, 429)
        self.assertEqual(response.headers["Retry-After"], "3600")
        self.assertEqual(self.payload(response)["error"]["code"], "rate_limited")

    def test_unknown_route_and_cors(self) -> None:
        response = self.call("GET", "/nope", token=self.maintainer)
        self.assertEqual(response.code, 404)
        self.assertEqual(self.payload(response)["error"]["code"], "not_found")
        response = self.call(
            "OPTIONS",
            "/topics",
            headers={
                "Origin": "https://admin.example.org",
                "Access-Control-Request-Method": "POST",
            },
        )
        self.assertEqual(response.code, 204)
        self.assertEqual(
            response.headers["Access-Control-Allow-Origin"], "https://admin.example.org"
        )
        response = self.call("OPTIONS", "/topics", headers={"Origin": "https://evil.example.org"})
        self.assertEqual(response.code, 404)
        self.assertNotIn("Access-Control-Allow-Origin", response.headers)

    def test_status_reports_contributor_and_catalog(self) -> None:
        response = self.call("GET", "/status", token=self.contributor)
        self.assertEqual(response.code, 200)
        document = self.payload(response)
        self.assertEqual(
            document["contributor"], {"id": "jaco", "name": "Brother Jaco", "role": "contributor"}
        )
        self.assertEqual(document["catalog_version"], 3)
        self.assertEqual(document["counts"]["topics"], 2)
        self.assertEqual(document["git"]["branch"], "main")
        self.assertIn("X-Request-Id", response.headers)


class TopicTests(ApiTestCase):
    def test_list_and_get(self) -> None:
        response = self.call("GET", "/topics", token=self.contributor)
        self.assertEqual(response.code, 200)
        topics = self.payload(response)["topics"]
        self.assertEqual([t["id"] for t in topics], ["gods-judgment", "grace"])
        self.assertEqual(topics[1]["verses"], 3)
        response = self.call("GET", "/topics/grace", token=self.contributor)
        topic = self.payload(response)["topic"]
        self.assertEqual(topic["verses"], [[45, 5, 20], [49, 2, 8], [49, 2, 9]])
        self.assertEqual(topic["names"], {"de": "Gnade", "fr": "Grâce"})
        self.assertEqual(self.call("GET", "/topics/missing", token=self.contributor).code, 404)

    def test_create_requires_maintainer(self) -> None:
        body = {"name": "Mercy", "color": "#FFFFFF"}
        response = self.call("POST", "/topics", token=self.contributor, body=body)
        self.assertEqual(response.code, 403)
        self.assertEqual(self.payload(response)["error"]["code"], "forbidden")
        response = self.call(
            "POST",
            "/topics",
            token=self.maintainer,
            body={**body, "verses": [[19, 23, 6]], "names": {"fr": "Miséricorde"}, "default": True},
        )
        self.assertEqual(response.code, 201, response.body)
        document = self.payload(response)
        self.assertTrue(document["changed"])
        self.assertEqual(document["catalog_version"], 4)
        self.assertTrue(document["pushed"])
        self.assertEqual(document["topic"]["id"], "mercy")
        self.assertEqual(document["topic"]["color"], "#ffffff")
        self.assertEqual(document["topic"]["verses"], [[19, 23, 6]])
        self.assertEqual(document["topic"]["names"], {"fr": "Miséricorde"})
        log_line = git(self.fixture.clone, "log", "-1", "--format=%an|%s")
        self.assertEqual(log_line, "Paul|Create topic Mercy")
        self.assertEqual(git(self.fixture.origin, "rev-parse", "main"), document["commit"])
        # The published release follows the commit.
        current = Path(self.folder.name) / "srv" / "current" / "v1" / "topics" / "mercy.json"
        self.assertTrue(current.exists())

    def test_create_validation(self) -> None:
        response = self.call("POST", "/topics", token=self.maintainer, body={"name": "Mercy"})
        self.assertEqual(response.code, 400)
        response = self.call(
            "POST",
            "/topics",
            token=self.maintainer,
            body={"name": "Mercy", "color": "#fff", "bogus": 1},
        )
        self.assertEqual(response.code, 400)
        self.assertIn("unsupported fields", self.payload(response)["error"]["message"])
        response = self.call(
            "POST", "/topics", token=self.maintainer, body={"name": "Mercy", "color": "#fff"}
        )
        self.assertEqual(response.code, 422)
        self.assertEqual(self.payload(response)["error"]["code"], "invalid_change")
        response = self.call(
            "POST", "/topics", token=self.maintainer, body={"name": "Grace", "color": "#ffffff"}
        )
        self.assertEqual(response.code, 422)
        response = self.call(
            "POST",
            "/topics",
            token=self.maintainer,
            raw=b"{not json",
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.code, 400)
        response = self.call(
            "POST",
            "/topics",
            token=self.maintainer,
            raw=b"x=1",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(response.code, 415)
        response = self.call(
            "POST",
            "/topics",
            token=self.maintainer,
            body={"name": "Mercy", "color": "#ffffff", "expected_catalog_version": 1},
        )
        self.assertEqual(response.code, 409)
        self.assertEqual(self.payload(response)["error"]["code"], "version_conflict")

    def test_update_and_retire(self) -> None:
        response = self.call(
            "PUT",
            "/topics/grace",
            token=self.maintainer,
            body={"name": "Grace of God", "color": "#ABCDEF"},
        )
        self.assertEqual(response.code, 200, response.body)
        topic = self.payload(response)["topic"]
        self.assertEqual(
            (topic["name"], topic["color"], topic["aliases"]),
            ("Grace of God", "#abcdef", ["Grace"]),
        )
        self.assertEqual(
            self.call("PUT", "/topics/grace", token=self.maintainer, body={}).code, 400
        )
        self.assertEqual(
            self.call(
                "PUT", "/topics/grace", token=self.contributor, body={"color": "#000000"}
            ).code,
            403,
        )
        self.assertEqual(
            self.call(
                "PUT", "/topics/missing", token=self.maintainer, body={"color": "#000000"}
            ).code,
            404,
        )
        response = self.call(
            "DELETE", "/topics/grace", token=self.maintainer, body={"reason": "Merged"}
        )
        self.assertEqual(response.code, 200, response.body)
        self.assertEqual(self.payload(response)["retired"]["id"], "grace")
        self.assertEqual(self.call("GET", "/topics/grace", token=self.contributor).code, 410)
        self.assertEqual(
            self.call(
                "POST", "/topics/grace/verses", token=self.contributor, body={"verses": [[1, 1, 1]]}
            ).code,
            410,
        )
        response = self.call(
            "POST",
            "/topics",
            token=self.maintainer,
            body={"id": "grace", "name": "Grace Again", "color": "#ffffff"},
        )
        self.assertEqual(response.code, 410)
        catalog = load_catalog(self.fixture.clone / "data")
        self.assertEqual(list(catalog.retired), ["grace"])
        self.assertFalse((self.fixture.clone / "v1" / "topics" / "grace.json").exists())


class VerseTests(ApiTestCase):
    def test_add_remove_replace(self) -> None:
        response = self.call(
            "POST",
            "/topics/grace/verses",
            token=self.contributor,
            body={"verses": [[40, 1, 1], [49, 2, 8]]},
        )
        self.assertEqual(response.code, 200, response.body)
        document = self.payload(response)
        self.assertEqual((document["added"], document["removed"], document["verses"]), (1, 0, 4))
        self.assertEqual(
            git(self.fixture.clone, "log", "-1", "--format=%an|%s"),
            "Brother Jaco|Add 2 verse link(s) on grace",
        )
        response = self.call(
            "DELETE", "/topics/grace/verses", token=self.contributor, body={"verses": [[40, 1, 1]]}
        )
        self.assertEqual(self.payload(response)["removed"], 1)
        response = self.call("DELETE", "/topics/grace/verses/49/2/8", token=self.contributor)
        self.assertEqual(response.code, 200, response.body)
        self.assertEqual(self.payload(response)["removed"], 1)
        self.assertEqual(
            self.call("DELETE", "/topics/grace/verses/99/1/1", token=self.contributor).code, 404
        )
        response = self.call(
            "PUT", "/topics/grace/verses", token=self.contributor, body={"verses": [[43, 3, 16]]}
        )
        document = self.payload(response)
        self.assertEqual((document["added"], document["removed"], document["verses"]), (1, 2, 1))
        response = self.call(
            "POST", "/topics/grace/verses", token=self.contributor, body={"verses": [[43, 3, 16]]}
        )
        self.assertEqual(response.code, 200)
        self.assertFalse(self.payload(response)["changed"])
        self.assertEqual(
            self.call(
                "POST", "/topics/grace/verses", token=self.contributor, body={"verses": [[0, 1, 1]]}
            ).code,
            422,
        )
        self.assertEqual(
            self.call("POST", "/topics/grace/verses", token=self.contributor, body={}).code, 400
        )
        self.assertEqual(
            self.call(
                "POST", "/topics/ghost/verses", token=self.contributor, body={"verses": [[1, 1, 1]]}
            ).code,
            404,
        )
        chapter = json.loads((self.fixture.clone / "v1" / "verses" / "43" / "3.json").read_text())
        self.assertEqual(chapter["verses"], {"16": ["grace"]})


class LocaleAndBundleTests(ApiTestCase):
    def test_locales(self) -> None:
        response = self.call("GET", "/locales", token=self.contributor)
        self.assertEqual([item["code"] for item in self.payload(response)["locales"]], ["de", "fr"])
        response = self.call(
            "PUT",
            "/locales/af",
            token=self.contributor,
            body={"name": "Afrikaans", "topics": {"grace": "Genade"}},
        )
        self.assertEqual(response.code, 200, response.body)
        self.assertEqual(self.payload(response)["names_changed"], 1)
        response = self.call("GET", "/locales/af", token=self.contributor)
        self.assertEqual(
            self.payload(response)["locale"],
            {"code": "af", "name": "Afrikaans", "topics": {"grace": "Genade"}},
        )
        self.assertEqual(
            self.call(
                "PUT", "/locales/en", token=self.contributor, body={"topics": {"grace": "Grace"}}
            ).code,
            422,
        )
        self.assertEqual(
            self.call(
                "PUT", "/locales/af", token=self.contributor, body={"topics": {"ghost": "X"}}
            ).code,
            404,
        )
        response = self.call("DELETE", "/locales/af/topics/grace", token=self.contributor)
        self.assertEqual(response.code, 200, response.body)
        self.assertEqual(
            self.call("DELETE", "/locales/af/topics/grace", token=self.contributor).code, 404
        )
        self.assertEqual(self.call("DELETE", "/locales/de", token=self.contributor).code, 403)
        self.assertEqual(self.call("DELETE", "/locales/de", token=self.maintainer).code, 200)
        self.assertEqual(self.call("GET", "/locales/de", token=self.contributor).code, 404)
        self.assertFalse((self.fixture.clone / "data" / "locales" / "de.json").exists())
        self.assertTrue((self.fixture.clone / "v1" / "locales" / "af.json").exists())

    def test_bundle_and_sync(self) -> None:
        bundle = {
            "schema_version": 1,
            "topics": [{"id": "prayer", "name": "Prayer", "color": "#93c5fd", "aliases": []}],
            "associations": {
                "add": [{"topic_id": "prayer", "book": 40, "chapter": 6, "verse": 9}],
                "remove": [],
            },
        }
        response = self.call("POST", "/bundles", token=self.contributor, body=bundle)
        self.assertEqual(response.code, 200, response.body)
        document = self.payload(response)
        self.assertEqual(document["topics_created"], ["prayer"])
        self.assertEqual(document["verses_added"], 1)
        response = self.call(
            "POST", "/bundles", token=self.contributor, body={**bundle, "note": "private"}
        )
        self.assertEqual(response.code, 400)
        response = self.call(
            "POST",
            "/bundles",
            token=self.contributor,
            body={**bundle, "expected_catalog_version": 4},
        )
        self.assertEqual(response.code, 200)
        self.assertFalse(self.payload(response)["changed"])
        self.assertEqual(self.call("POST", "/sync", token=self.contributor).code, 403)
        response = self.call("POST", "/sync", token=self.maintainer)
        self.assertEqual(response.code, 200, response.body)
        self.assertEqual(self.payload(response)["catalog_version"], 4)


class ConfigTests(unittest.TestCase):
    def test_environment_parsing(self) -> None:
        publisher, api, contributors = config_from_environment(
            {
                "BOOKMARKS_GIT_REMOTE": "",
                "BOOKMARKS_GIT_PUSH": "no",
                "BOOKMARKS_PORT": "9000",
                "BOOKMARKS_API_PREFIX": "manage/",
                "BOOKMARKS_ALLOWED_ORIGINS": "https://a.example, https://b.example",
                "BOOKMARKS_CONTRIBUTORS_FILE": "/etc/getbible-bookmarks/contributors.json",
                "BOOKMARKS_RELEASE_DIR": "/srv/getbible-bookmarks",
            },
            repo_dir=Path("/srv/repo"),
        )
        self.assertIsNone(publisher.remote)
        self.assertFalse(publisher.push)
        self.assertEqual(publisher.release_dir, Path("/srv/getbible-bookmarks"))
        self.assertEqual(api.port, 9000)
        self.assertEqual(api.prefix, "/manage")
        self.assertEqual(api.allowed_origins, ("https://a.example", "https://b.example"))
        self.assertEqual(contributors, Path("/etc/getbible-bookmarks/contributors.json"))
        with self.assertRaises(ValueError):
            config_from_environment({"BOOKMARKS_PORT": "many"}, repo_dir=Path("/srv/repo"))
