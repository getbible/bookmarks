# getBible Bookmarks

The canonical catalogue of **topic bookmarks** for getBible: reviewed topics
(with stable ids, colours and translated names) and the verses linked to each
topic. This repository is the single source of truth consumed by the
[getBible Robot](https://github.com/getbible/robot) Telegram Mini App, the
[getBible app](https://github.com/getbible/app) and any third party project.

It ships two things:

| Surface | Purpose | Volume | Runtime |
|---|---|---|---|
| **Static JSON API** at `https://bookmarks.getbible.net/v1/` | Read the catalogue | Millions of requests | None: nginx serves committed, precompressed files |
| **Management API** at `https://bookmarks.getbible.net/v1/manage/` | Add topics, verses and translations | A dozen contributors | Small authenticated Python service that commits to this repository |

Every change, whether made through the management API or a pull request, is a
git commit. Git is the audit log and the rollback mechanism.

## Read the catalogue

```bash
# Discovery: versions, counts, checksum and resource paths
curl https://bookmarks.getbible.net/v1/index.json

# All topics with colours and verse counts
curl https://bookmarks.getbible.net/v1/topics.json

# One topic with its translated names and every linked verse
curl https://bookmarks.getbible.net/v1/topics/grace.json

# Which topics mark which verses of John 3 (book 43, chapter 3)
curl https://bookmarks.getbible.net/v1/verses/43/3.json

# Translated topic names for a locale
curl https://bookmarks.getbible.net/v1/locales/fr.json

# Everything in one document (topics, verses, tombstones, all locales)
curl https://bookmarks.getbible.net/v1/all.json
```

Verse coordinates are translation independent `[book, chapter, verse]` triples
in the 66-book Protestant canon, the same numbering
[GetBible API v2](https://api.getbible.net/v2/translations.json) uses. See
[docs/API.md](docs/API.md) for every resource, caching and versioning rules.

## Change the catalogue

Approved contributors hold a bearer token and call the management API:

```bash
curl -X POST https://bookmarks.getbible.net/v1/manage/topics/grace/verses \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"verses": [[49, 2, 8], [49, 2, 9]]}'
```

The service validates the change, commits it with the contributor as git
author, pushes to this repository and republishes the static files within the
same request. Topics can be created, renamed, recoloured and retired;
verses added, replaced and removed; translations edited; and the robot's
moderation exports imported as bundles. See
[docs/MANAGEMENT_API.md](docs/MANAGEMENT_API.md).

Pull requests that edit `data/` directly are equally welcome; see
[docs/CONTRIBUTING.md](docs/CONTRIBUTING.md).

## Repository layout

```text
data/                 canonical sources (topics, verse links, translations, tombstones)
v1/                   generated static API, committed and verified by CI
schema/               JSON Schemas for the sources and every API document
getbible_bookmarks/   Python package: validation, builder, CLI, management service
deploy/               nginx site, systemd units, install and deploy scripts
scripts/              robot import, CI guards, local check runner
tests/                unit, builder, publisher and HTTP API tests
docs/                 architecture, API contracts, deployment, consumers
```

## Development

Python 3.11 or newer and git are required; the runtime depends only on Tornado.

```bash
python3 -m venv venv
venv/bin/python -m pip install --require-hashes -r requirements-dev.txt
venv/bin/python -m pip install -e .
bash scripts/run-checks.sh          # format, lint, strict typing, sources, v1 tree, tests
```

Common commands:

```bash
getbible-bookmarks validate                 # check data/
getbible-bookmarks build                    # regenerate v1/
getbible-bookmarks build --check            # verify v1/ matches data/ byte for byte
getbible-bookmarks import-bundle bundle.json  # apply a robot contribution export
getbible-bookmarks tokens --file contributors.json create --id jaco --name "Brother Jaco" --email jaco@example.org
getbible-bookmarks serve                    # run the management API (see deploy/env.template)
```

The initial catalogue (61 topics, 2,155 verse links, 68 locales) was imported
from `getbible/robot` with `scripts/import_from_robot.py`; that repository will
switch to consuming this API.

## Documentation

- [Architecture](docs/ARCHITECTURE.md): read/write split, determinism, invariants, security boundaries
- [Static API](docs/API.md): every resource with examples, caching and versioning
- [Management API](docs/MANAGEMENT_API.md): authentication, roles, endpoints, errors
- [Deployment](docs/DEPLOYMENT.md): host layout, nginx, systemd, tokens, rollback
- [Contributing](docs/CONTRIBUTING.md): validation rules, pull requests, robot bundles
- [Consumers](docs/CONSUMERS.md): integration guide for the app, the robot and third parties

## License

Apache License 2.0; see [LICENSE](LICENSE). Topic names and verse links are
reviewed community data; Scripture text is never stored here.
