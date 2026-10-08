// Writes the Home page artwork into src/partials/. Run after changing plot.mjs or sheet.mjs:
//   node scripts/art/generate.mjs
// The output is committed; build.mjs inlines it with {{include <name>}}.
import { writeFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { tidePlot } from "./plot.mjs";
import { chartSheet } from "./sheet.mjs";

const out = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "src", "partials");
const files = {
  "chart-sheet.svg": chartSheet(),
  "tide-wide.svg": tidePlot({ w: 1100, h: 360, padT: 80, padB: 42, padL: 8, padR: 8, id: "tide", label: "wide", fs: 19 }),
  "tide-compact.svg": tidePlot({ w: 520, h: 420, t0: 7.5, t1: 16.5, padT: 118, tiAbove: true, padB: 46, padL: 6, padR: 6, id: "tidec", label: "compact", fs: 21 }),
};
for (const [name, svg] of Object.entries(files)) writeFileSync(join(out, name), svg + "\n");
console.log(`wrote ${Object.keys(files).length} files to src/partials/`);
