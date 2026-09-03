from __future__ import annotations

import json
import unittest
from pathlib import Path

from getbible_bookmarks.build import render_api
from getbible_bookmarks.sources import load_catalog, render_sources
from tests.helpers import sample_catalog

try:
    import jsonschema
    from jsonschema import Draft202012Validator
except ImportError:  # pragma: no cover - dev requirement
    jsonschema = None

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_ROOT = REPOSITORY_ROOT / "schema"


def _load(name: str) -> dict[str, object]:
    document = json.loads((SCHEMA_ROOT / name).read_text("utf-8"))
    assert isinstance(document, dict)
    return document


@unittest.skipIf(jsonschema is None, "jsonschema is not installed")
class SchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        from referencing import Registry, Resource

        schemas = {
            path: _load(path.relative_to(SCHEMA_ROOT).as_posix())
            for path in SCHEMA_ROOT.rglob("*.json")
        }
        registry = Registry()
        for document in schemas.values():
            registry = registry.with_resource(
                str(document["$id"]), Resource.from_contents(document)
            )
        self.registry = registry
        self.schemas = {
            path.relative_to(SCHEMA_ROOT).as_posix(): document for path, document in schemas.items()
        }

    def validator(self, name: str) -> Draft202012Validator:
        Draft202012Validator.check_schema(self.schemas[name])
        return Draft202012Validator(self.schemas[name], registry=self.registry)

    def check(self, name: str, content: bytes) -> None:
        errors = sorted(self.validator(name).iter_errors(json.loads(content)), key=str)
        self.assertEqual(errors, [], f"{name}: {[error.message for error in errors][:3]}")

    def test_sources_match_schemas(self) -> None:
        for catalog in (sample_catalog(), load_catalog(REPOSITORY_ROOT / "data")):
            for relative, content in render_sources(catalog).items():
                if relative == "topics.json":
                    self.check("topics.schema.json", content)
                elif relative == "retired-topics.json":
                    self.check("retired-topics.schema.json", content)
                elif relative.startswith("links/"):
                    self.check("links.schema.json", content)
                elif relative.startswith("locales/"):
                    self.check("locale.schema.json", content)
                else:
                    self.fail(f"unexpected source document {relative}")

    def test_api_documents_match_schemas(self) -> None:
        catalog = sample_catalog()
        catalog.retire_topic("gods-judgment", reason="test")
        catalog.catalog_version += 1
        for source in (catalog, load_catalog(REPOSITORY_ROOT / "data")):
            files = render_api(source)
            for relative, content in files.items():
                if relative == "index.json":
                    schema = "api/index.schema.json"
                elif relative == "catalog.json":
                    schema = "api/catalog.schema.json"
                elif relative == "all.json":
                    schema = "api/all.schema.json"
                elif relative == "topics.json":
                    schema = "api/topics.schema.json"
                elif relative == "retired.json":
                    schema = "api/retired.schema.json"
                elif relative == "locales.json":
                    schema = "api/locales.schema.json"
                elif relative == "checksums.json":
                    schema = "api/checksums.schema.json"
                elif relative.startswith("topics/"):
                    schema = "api/topic.schema.json"
                elif relative.startswith("locales/"):
                    schema = "locale.schema.json"
                elif relative.startswith("verses/") and relative.count("/") == 2:
                    schema = "api/chapter.schema.json"
                elif relative.startswith("verses/"):
                    schema = "api/book.schema.json"
                else:
                    self.fail(f"unexpected API document {relative}")
                self.check(schema, content)

    def test_bundle_schema_accepts_robot_example(self) -> None:
        bundle = {
            "schema_version": 1,
            "topics": [
                {
                    "id": "prayer-and-fasting",
                    "name": "Prayer and Fasting",
                    "color": "#93c5fd",
                    "aliases": [],
                }
            ],
            "associations": {
                "add": [{"topic_id": "prayer-and-fasting", "book": 40, "chapter": 6, "verse": 16}],
                "remove": [],
            },
        }
        self.check("contribution-bundle.schema.json", json.dumps(bundle).encode())
        errors = list(
            self.validator("contribution-bundle.schema.json").iter_errors({**bundle, "note": "x"})
        )
        self.assertTrue(errors)
