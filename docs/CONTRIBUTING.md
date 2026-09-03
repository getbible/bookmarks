# Contributing

There are three ways to change the catalogue. All of them end as commits on
`main` and are validated by the same rules and the same CI.

## 1. Management API

Approved contributors use the [management API](MANAGEMENT_API.md). This is the
normal path for day-to-day additions: the change is validated, committed under
your name and live within seconds. Ask a maintainer for a token.

## 2. Pull request

Edit the sources under `data/` directly:

1. `data/topics.json`: add or adjust topics. Ids are permanent slugs; English
   names must be unique (aliases count); colours are lowercase `#rrggbb`.
   **Bump `catalog_version` by one.** CI fails when `data/` changes without it.
2. `data/links/<topic-id>.json`: one file per topic, strictly ascending
   `[book, chapter, verse]` triples. Create the file when you create a topic.
3. `data/locales/<locale>.json`: translated names keyed by topic id, sorted.
4. Never edit `data/retired-topics.json` by hand except to retire a topic:
   remove it from `topics.json`, delete its links file and translations, and
   append `{"id", "name", "retired_in": <new catalog_version>, "reason"}`.
5. Regenerate and verify:

   ```bash
   getbible-bookmarks validate
   getbible-bookmarks build
   bash scripts/run-checks.sh
   ```

6. Commit `data/` and `v1/` together and open the pull request.

The generated tree is committed so any static host can serve the repository
as-is; CI rejects a stale or hand-edited `v1/`.

## 3. Robot contribution bundles

The getbible/robot moderation CLI exports reviewed changes as a privacy-safe
bundle (`schema_version` 1 with `topics` and `associations`). Either post it to
`POST /v1/manage/bundles` or apply it locally and open a pull request:

```bash
getbible-bookmarks import-bundle --check /path/to/bundle.json   # validate only
getbible-bookmarks import-bundle /path/to/bundle.json           # apply, bump, rebuild
```

The importer follows the robot's rules: existing topics may only gain aliases,
a coordinate cannot be both added and removed, and re-importing an applied
bundle is a no-op.

## Data rules (summary)

| Field | Rule |
|---|---|
| topic id | `^[a-z0-9]+(?:-[a-z0-9]+)*$`, at most 80 characters, never reused |
| English name / alias | `^[A-Za-z0-9][A-Za-z0-9 &'():?-]*[A-Za-z0-9)]$`, at most 80 characters, unique case-insensitively |
| colour | `^#[0-9a-f]{6}$` |
| verse | book 1..66, chapter within the book's chapter count, verse 1..2000 |
| locale | `^[a-z]{2,3}(?:-[a-z0-9]{2,8})*$`, `en` is generated |
| translated name | 1..120 characters, no control characters |

The complete rule set, including limits, is in
[ARCHITECTURE.md](ARCHITECTURE.md#invariants) and enforced by
`getbible_bookmarks/model.py`.

## Code changes

```bash
python3 -m venv venv
venv/bin/python -m pip install --require-hashes -r requirements-dev.txt
venv/bin/python -m pip install -e .
bash scripts/run-checks.sh
```

The check runner mirrors CI: `ruff format --check`, `ruff check`, strict
`mypy`, byte compilation, shell syntax, source validation, `build --check` and
the unittest suite (which spins up bare git repositories and a real Tornado
server). Keep dependencies pinned with hashes (`pip-compile --generate-hashes`
from `requirements*.in`).
