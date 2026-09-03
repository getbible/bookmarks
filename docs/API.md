# Static JSON API (v1)

Base URL: `https://bookmarks.getbible.net/v1/`

Every resource is a plain UTF-8 JSON file. Requests need no authentication,
CORS is open (`Access-Control-Allow-Origin: *`), responses are gzip encoded when
the client accepts it, and every file carries an `ETag` and `Last-Modified`.

## Resources

| Path | Document | Changes when |
|---|---|---|
| `index.json` | discovery: versions, counts, checksum, resource templates, locale codes | any change |
| `topics.json` | every active topic with colour, aliases, default flag and verse count | any change |
| `topics/{id}.json` | one topic with translated `names` and its full `verses` list | that topic, its links or its translations change |
| `catalog.json` | every topic with its verses plus `retired` tombstones (the checksum reference document) | topics or links change |
| `all.json` | `catalog.json` plus every locale | any change |
| `verses/{book}.json` | reverse index for one book: chapter → verse → topic ids | links in that book change |
| `verses/{book}/{chapter}.json` | reverse index for one chapter (every canonical chapter exists, possibly empty) | links in that chapter change |
| `locales.json` | available locales with language name and translated-name count | any change |
| `locales/{locale}.json` | translated topic names for one locale (`en` is generated from the English names) | that locale changes |
| `retired.json` | tombstones of retired topics | a topic is retired |
| `checksums.json` | SHA-256 of every file in the tree | any change |

Templates for the parametrised paths are published in `index.json` under
`resources`, so clients can discover them instead of hard-coding.

## Identifiers and coordinates

- **Topic id**: a permanent lowercase slug such as `gods-judgment`. Ids never
  change and are never reused; a retired id appears in `retired.json`.
- **Verse coordinate**: `[book, chapter, verse]`, translation independent, in
  the 66-book Protestant canon (`1` = Genesis, `40` = Matthew, `66` =
  Revelation). Book and chapter numbers match GetBible API v2, so
  `verses/43/3.json` describes the chapter served by
  `https://api.getbible.net/v2/{translation}/43/3.json`.
- **Colour**: lowercase `#rrggbb`.
- **Locale**: lowercase BCP 47 style tag (`af`, `zh-hant`). Look codes up in
  `index.json`/`locales.json`; fall back to `en` when a locale or a single name
  is missing.

## Examples

`index.json`

```json
{
  "schema_version": 1,
  "catalog_version": 1,
  "checksum": "2a8d4f5e06efb4006e8f0b52b790ef5861dc9152f746678d77e3255b7b3f42a3",
  "counts": {"topics": 61, "verses": 2155, "locales": 69, "retired": 0},
  "resources": {
    "index": "index.json",
    "catalog": "catalog.json",
    "all": "all.json",
    "topics": "topics.json",
    "topic": "topics/{id}.json",
    "book": "verses/{book}.json",
    "chapter": "verses/{book}/{chapter}.json",
    "locales": "locales.json",
    "locale": "locales/{locale}.json",
    "retired": "retired.json",
    "checksums": "checksums.json"
  },
  "locales": ["af", "ar", "br", "..."]
}
```

`topics.json` (excerpt)

```json
{
  "schema_version": 1,
  "catalog_version": 1,
  "checksum": "2a8d…",
  "topics": [
    {"id": "adultery", "name": "Adultery", "color": "#f9a8b8", "aliases": [], "default": true, "verses": 78},
    {"id": "authority-of-the-bible", "name": "Authority of the Bible", "color": "#93c5fd", "aliases": [], "default": true, "verses": 59}
  ]
}
```

`topics/grace.json` (excerpt)

```json
{
  "schema_version": 1,
  "id": "grace",
  "name": "Grace",
  "color": "#bbf7d0",
  "aliases": [],
  "default": true,
  "names": {"af": "Genade", "de": "Gnade", "en": "Grace", "fr": "Grâce"},
  "verses": [[5, 9, 5], [19, 84, 11], [25, 3, 22], [25, 3, 23], "…"]
}
```

`verses/43/3.json` (excerpt)

```json
{
  "schema_version": 1,
  "book": 43,
  "chapter": 3,
  "verses": {
    "3": ["spiritual-rebirth"],
    "5": ["baptism", "ordinances", "spiritual-rebirth"],
    "16": ["free-will", "spiritual-rebirth"]
  }
}
```

`locales/fr.json` (excerpt)

```json
{
  "schema_version": 1,
  "locale": "fr",
  "name": "French",
  "topics": {"adultery": "Adultère", "authority-of-the-bible": "Autorité de la Bible"}
}
```

`retired.json`

```json
{"schema_version": 1, "catalog_version": 7, "checksum": "…", "topics": [{"id": "old-topic", "name": "Old Topic", "retired_in": 7}]}
```

## Versioning

- `schema_version` (always `1` under `/v1/`) describes the document shapes.
  Additive fields may appear within a schema version; clients must ignore
  unknown fields. Breaking changes ship under a new path prefix.
- `catalog_version` increases by one on every published change. Cache it and
  compare it to `index.json` to decide whether anything needs refetching.
- `checksum` is the SHA-256 of `catalog.json` and identifies the exact content
  of the topic and verse data. `checksums.json` gives a per-file SHA-256 for
  integrity checks after download.

The `default` flag marks the reviewed starter set apps show before a user
customises anything; new topics are published with `default: false` until a
maintainer promotes them.

## Caching

| Header | Value |
|---|---|
| `Cache-Control` | `public, max-age=300, stale-while-revalidate=86400, stale-if-error=604800` |
| `ETag`, `Last-Modified` | per file; send `If-None-Match` to get `304 Not Modified` |
| `Content-Encoding` | `gzip` when accepted (files are precompressed at publish time) |
| `Access-Control-Allow-Origin` | `*` |

Recommended client behaviour:

1. Fetch `index.json` with `If-None-Match`. On `304`, nothing changed.
2. If `catalog_version` advanced, fetch what you use (`catalog.json` for a
   complete refresh, or `topics.json` plus `verses/{book}.json` for the books you
   display, plus `locales/{locale}.json`).
3. Verify `checksum` against `index.json` and, optionally, file digests against
   `checksums.json`.
4. Keep the last good copy when the network fails; the data changes rarely.

## Schemas

Machine-readable JSON Schemas for every document live under
[`schema/api/`](../schema/api) and are validated in CI against the generated
tree.
