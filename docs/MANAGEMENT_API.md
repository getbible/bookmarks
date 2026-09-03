# Management API (v1)

Base URL: `https://bookmarks.getbible.net/v1/manage/`

The management API is the write side of the catalogue. It is small, strict and
low volume: every successful mutation becomes one git commit on
`getbible/bookmarks` (authored by the contributor, committed by the service),
pushed to GitHub and published to the static API before the response returns.

## Authentication and roles

Send a bearer token issued by a maintainer on the host:

```http
Authorization: Bearer gbb_…
```

| Role | May |
|---|---|
| `contributor` | read everything; add, replace and remove verse links; edit translations; submit contribution bundles |
| `maintainer` | everything above plus create, update and retire topics, delete whole locales, trigger a repository sync |

Tokens are created with `getbible-bookmarks tokens create` (see
[DEPLOYMENT.md](DEPLOYMENT.md)), shown once and stored only as a SHA-256 digest.
Rotate or revoke them with the same CLI; the service picks up registry changes
immediately without a restart.

## Conventions

- Request bodies are JSON objects. Unknown fields are rejected (`400`).
- Verse coordinates are `[book, chapter, verse]` triples or
  `{"book", "chapter", "verse"}` objects.
- Every mutation accepts an optional `expected_catalog_version`; if the
  catalogue has moved on, the request fails with `409` and nothing changes.
- Mutations are idempotent: re-sending the same change reports
  `"changed": false` and does not create a commit.
- Every response carries `X-Request-Id`; quote it when reporting a problem.
- Limits: 1 MiB body, 60 requests per contributor with one request per second
  refill, 10 failed authentications per client address, 10,000 coordinates per
  request.

Mutation responses share one envelope:

```json
{
  "changed": true,
  "catalog_version": 8,
  "checksum": "c5c2…",
  "commit": "3f9e0a1c…",
  "pushed": true,
  "summary": "Add 2 verse link(s) on grace",
  "…operation specific fields…": "…",
  "request_id": "8a1f0c3d9b2e4f57"
}
```

`pushed` is `true` when GitHub accepted the commit, `false` when the push
failed and will be retried (the change is already live on the static API), or
`null` when the change was a no-op or pushing is disabled.

Errors:

```json
{"error": {"code": "invalid_change", "message": "verses[0] [0, 1, 1] is outside the 66-book canon (…)"}, "request_id": "…"}
```

| Status | `code` | Meaning |
|---|---|---|
| 400 | `invalid_json`, `invalid_request` | malformed body or unknown/missing fields |
| 401 | `unauthorized` | missing, malformed, unknown or revoked token |
| 403 | `forbidden` | the role does not allow the operation |
| 404 | `not_found` | unknown topic, locale, translation or route |
| 409 | `version_conflict` | `expected_catalog_version` did not match |
| 410 | `retired` | the topic id was retired and is permanently reserved |
| 415 | `unsupported_media_type` | body is not `application/json` |
| 422 | `invalid_change` | the change violates a catalogue rule |
| 429 | `rate_limited` | see `Retry-After` |
| 503 | `publish_failed`, `repository_diverged`, `registry_unavailable`, `catalog_unavailable`, `sync_failed` | the host needs attention; nothing was committed |

## Endpoints

### `GET /health`

Unauthenticated liveness probe: `{"status": "ok"}`.

### `GET /status`

The caller's identity and the repository state.

```json
{
  "contributor": {"id": "jaco", "name": "Brother Jaco", "role": "contributor"},
  "catalog_version": 7,
  "checksum": "…",
  "counts": {"topics": 61, "verses": 2155, "locales": 69, "retired": 0},
  "output_stale": false,
  "git": {"branch": "main", "remote": "origin", "push_pending": false, "last_error": null, "head": "…"}
}
```

### Topics

| Method and path | Role | Body |
|---|---|---|
| `GET /topics` | contributor | — |
| `GET /topics/{id}` | contributor | — |
| `POST /topics` | maintainer | `name`, `color`, optional `id` (derived from the name when omitted), `aliases`, `default`, `verses`, `names` (`{locale: translated name}`) |
| `PUT /topics/{id}` | maintainer | any of `name`, `color`, `aliases`, `default` |
| `DELETE /topics/{id}` | maintainer | optional `reason` |

Creating returns `201` with the full topic. Renaming keeps the previous wording
as an alias so existing mappings keep resolving. Retiring removes the topic, its
verse links and its translations, and records a tombstone; the id can never be
used again (`410` on any later attempt).

```bash
curl -X POST "$BASE/topics" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{
  "name": "Prayer and Fasting",
  "color": "#93c5fd",
  "verses": [[40, 6, 16], [40, 6, 17], [40, 6, 18]],
  "names": {"af": "Gebed en Vas", "fr": "Prière et jeûne"}
}'
```

### Verse links

| Method and path | Role | Body | Effect |
|---|---|---|---|
| `POST /topics/{id}/verses` | contributor | `verses` | add; already-linked verses are ignored |
| `PUT /topics/{id}/verses` | contributor | `verses` | replace the whole list |
| `DELETE /topics/{id}/verses` | contributor | `verses` | remove the listed verses |
| `DELETE /topics/{id}/verses/{book}/{chapter}/{verse}` | contributor | — | remove one verse |

Responses add `added`, `removed` and the resulting `verses` count.

```bash
curl -X POST "$BASE/topics/grace/verses" -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d '{"verses": [[49, 2, 8], [49, 2, 9]], "expected_catalog_version": 7}'
curl -X DELETE "$BASE/topics/grace/verses/49/2/9" -H "Authorization: Bearer $TOKEN"
```

### Translations

| Method and path | Role | Body |
|---|---|---|
| `GET /locales` | contributor | — |
| `GET /locales/{locale}` | contributor | — |
| `PUT /locales/{locale}` | contributor | `topics` (`{topic id: translated name}`), optional `name` (language name in English), optional `replace` (`true` drops names not in `topics`) |
| `DELETE /locales/{locale}/topics/{id}` | contributor | — |
| `DELETE /locales/{locale}` | maintainer | — |

`PUT` merges by default and creates the locale when it does not exist. The
`en` locale is derived from the English topic names and cannot be edited here;
rename the topic instead.

### Contribution bundles

`POST /bundles` (contributor) accepts the privacy-safe export of the
getbible/robot moderation CLI unchanged:

```json
{
  "schema_version": 1,
  "topics": [{"id": "prayer-and-fasting", "name": "Prayer and Fasting", "color": "#93c5fd", "aliases": []}],
  "associations": {
    "add": [{"topic_id": "prayer-and-fasting", "book": 40, "chapter": 6, "verse": 16}],
    "remove": []
  }
}
```

Rules match the robot importer: a topic that already exists may only have its
aliases extended (name and colour must match), a coordinate cannot be both added
and removed, and unknown fields are rejected. The whole bundle is applied
atomically as one commit; the response adds `topics_created`,
`topics_extended`, `verses_added` and `verses_removed`. An optional top-level
`expected_catalog_version` is accepted alongside the bundle fields.

### `POST /sync` (maintainer)

Fetch the published branch, fast-forward or rebase the checkout, verify that
the committed `v1/` tree is fresh and publish it as the current release. Use it
after merging a pull request when the periodic timer has not run yet.

## Client guidance

- Read `GET /status` first to learn the current `catalog_version`, then pass it
  as `expected_catalog_version` on writes you derived from that state.
- Batch related edits into one request (one `POST …/verses` with many
  coordinates, or one bundle) to get one commit and one release.
- Treat `503` as "try again later, the host needs attention", never as a
  reason to resend the same change in a loop.
- The static API reflects a change immediately after the response; CDN and
  browser caches follow the 300 second `max-age`.
