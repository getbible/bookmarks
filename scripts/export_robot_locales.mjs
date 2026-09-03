#!/usr/bin/env node
// Dump the reviewed topic-name translations bundled in a getbible/robot
// checkout as one JSON document on stdout:
//   { "<locale>": { "<topic-id>": "<translated name>", ... }, ... }
// Usage: node scripts/export_robot_locales.mjs /path/to/robot
import { pathToFileURL } from "node:url";
import { resolve } from "node:path";

const robotRoot = process.argv[2];
if (!robotRoot) {
  process.stderr.write("Usage: export_robot_locales.mjs <robot checkout>\n");
  process.exit(2);
}
const modulePath = resolve(robotRoot, "miniapp/lib/bookmark-locales.js");
const { BOOKMARK_LOCALE_EXTENSION } = await import(pathToFileURL(modulePath).href);
const output = {};
for (const locale of Object.keys(BOOKMARK_LOCALE_EXTENSION).sort()) {
  const catalog = BOOKMARK_LOCALE_EXTENSION[locale];
  const names = {};
  for (const key of Object.keys(catalog).sort()) {
    if (key.startsWith("bookmark_topics.")) {
      names[key.slice("bookmark_topics.".length)] = catalog[key];
    }
  }
  output[locale] = names;
}
process.stdout.write(JSON.stringify(output, null, 2) + "\n");
