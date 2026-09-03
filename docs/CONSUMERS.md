# Consuming the catalogue

This guide is for the two first-party consumers, the getBible Flutter app and
the getBible Robot Mini App, and for anyone else who wants topic bookmarks.

## General pattern

1. Fetch `v1/index.json` (send `If-None-Match` with the stored ETag). Store
   `catalog_version` and `checksum`.
2. When `catalog_version` advanced, fetch the documents you use and replace
   your cached copy atomically.
3. Keep the last good copy on network failure. The data is small (about 56 KB
   for `catalog.json`, under 10 KB gzipped) and changes rarely.
4. Resolve verse text through GetBible API v2 with the same numeric
   coordinates; this catalogue never contains Scripture text.

Translated names: fetch `v1/locales/{locale}.json` for the user's locale and
fall back to the English name from the topic itself when a name is missing.
Locale codes are lowercase (`zh-hant`, not `zh-Hant`).

## getBible app (Flutter)

Today `lib/core/starter_marking_groups.dart` hard-codes 61 names and colours.
Replace it with the catalogue:

- `MarkingGroup.id` ← topic `id` (the slugs match what the app derives today).
- `MarkingGroup.name` ← locale name for the app language, falling back to `name`.
- `MarkingGroup.color` ← `color` (uppercase it if the app prefers `#RRGGBB`).
- `isStarter` ← `default`; `sortOrder` ← position in `topics.json`.
- Verse markers while reading a chapter: `v1/verses/{book}/{chapter}.json`
  gives `verse number → topic ids` for exactly the chapter being displayed; one
  small request per chapter, cacheable alongside the chapter text. Use
  `v1/verses/{book}.json` if you prefer one request per book.

Cache the documents in SQLite next to chapters, keyed by `catalog_version`,
and refresh them with the seven-day translation refresh the app already runs.
No account or write access is needed; the app stays read-only.

## getBible Robot (Telegram Mini App)

The robot currently bundles the catalogue (`data/global-bookmarks/`,
`miniapp/lib/global-bookmark-data.js`, `bookmark-topic-definitions.js`,
`bookmark-locales-*.js`) and overlays a live revision from its own SQLite
store. With this repository as the source of truth:

- **Bundled fallback**: generate the robot's static modules from
  `v1/catalog.json` and `v1/locales/*.json` at build time instead of from the
  robot's own CSV/JSON (the coordinate format and ids are identical).
- **Live catalogue**: point `bookmarks/catalog` (or the browser directly) at
  `https://bookmarks.getbible.net/v1/catalog.json`; `catalog_version` plays the
  role of the robot's `revision` and `checksum` is the same SHA-256 concept.
- **Contribution publication**: the moderation CLI already exports the
  version 1 bundle this API accepts. Give the publisher a contributor token
  and `POST` the export to `/v1/manage/bundles` instead of pushing a branch to
  `getbible/robot`. Retired topics appear in `v1/retired.json`, which gives the
  robot the tombstone signal its schema currently lacks.

Robot-specific limits (100 topics, 39 overlay topics) are the robot's to lift
or keep; this catalogue allows up to 1,000 topics and publishes the count in
`index.json`.

## Third parties

Everything under `/v1/` is public, CORS-open and cacheable. Please:

- honour `Cache-Control` and use conditional requests;
- identify your client with a `User-Agent`;
- credit getBible and keep topic ids intact so users can move between apps.

Contributions are welcome through the [management API](MANAGEMENT_API.md) or
pull requests; see [CONTRIBUTING.md](CONTRIBUTING.md).
