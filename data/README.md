# Canonical sources

These files are the only hand-editable state of the catalogue; everything under
`v1/` is generated from them by `getbible-bookmarks build`.

| File | Content |
|---|---|
| `topics.json` | active topics (`id`, English `name`, lowercase `color`, sorted `aliases`, `default`) and the `catalog_version`, which must increase by one with every change |
| `retired-topics.json` | permanent tombstones (`id`, `name`, `retired_in`, `reason`); a retired id is never reused |
| `links/<topic-id>.json` | strictly ascending `[book, chapter, verse]` triples for one topic |
| `locales/<locale>.json` | translated topic names for one locale plus an optional English `name` of the language |

Validation rules and limits: [docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md#invariants).
JSON Schemas: [`schema/`](../schema).

The initial content was imported from `getbible/robot` (`data/global-bookmarks/`
and the reviewed `miniapp/lib/bookmark-locales-*.js` translations) with
`scripts/import_from_robot.py`.
