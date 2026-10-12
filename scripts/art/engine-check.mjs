// Writes src/partials/engine-check-wide.svg and engine-check-compact.svg for /home-c/ and /home-d/:
//   node scripts/art/engine-check.mjs
// The data is engine-check.json: at 178 NOAA gauges, two prediction engines were given NOAA's own
// harmonic constants, and each one's high and low water times were compared with NOAA's published
// predictions. Each dot is one gauge; its x position is that gauge's mean time error, on a log scale.
// The output is committed; build.mjs inlines it with {{include <name>}}.
import { readFileSync, writeFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const { rows } = JSON.parse(readFileSync(join(here, "engine-check.json"), "utf8"));
const f = (n) => n.toFixed(1);
const median = (xs) => { const s = [...xs].sort((a, b) => a - b); const m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };

// A strip: each dot at its gauge's error, spread over a fixed band by a fixed sequence (golden-ratio
// steps), so the same data always gives the same picture. Dots are translucent, so a dense cluster reads
// as a solid mark.
const spread = (xs, band) => xs.map((x, i) => ({ x, dy: (((i * 0.618034) % 1) - 0.5) * band }));

function chart({ w, h, padL, padR, rowA, rowB, axisY, r, band, fs, id, label }) {
  const lo = Math.log10(0.15), hi = Math.log10(20);
  const X = (m) => padL + ((Math.log10(m) - lo) / (hi - lo)) * (w - padL - padR);
  const otc = rows.map((x) => x[1]), other = rows.map((x) => x[2]);
  const mOtc = median(otc), mOther = median(other);
  const dots = (vals, y, cls) => spread(vals.map(X), band)
    .map((p, i) => `<circle class="${cls}" style="--i:${i}" cx="${f(p.x)}" cy="${f(y + p.dy)}" r="${r}"/>`).join("");
  const ticks = [0.2, 0.5, 1, 2, 5, 10, 20]
    .map((t) => `<path class="g" d="M${f(X(t))} ${rowA - band / 2 - 38} V${axisY}"/><text x="${f(X(t))}" y="${axisY + fs + 6}">${t}</text>`).join("");
  // The median line rises above the strip, and its label sits beside the top of the line, clear of the dots
  const med = (m, y, cls, text, anchor, dx = 8) =>
    `<path class="med ${cls}" d="M${f(X(m))} ${y - band / 2 - 30} V${y + band / 2 + 8}"/><text class="lab ${cls}" x="${f(X(m) + (anchor === "start" ? dx : -dx))}" y="${y - band / 2 - 14}" text-anchor="${anchor}">${text}</text>`;
  return `<svg class="eng ${label}" viewBox="0 0 ${w} ${h}" role="img" aria-labelledby="${id}-t" style="--fs:${fs}px" preserveAspectRatio="xMidYMid meet">
  <title id="${id}-t">At 178 NOAA gauges, both engines were given NOAA's own constants. Each dot is one gauge's mean time error of high and low water against NOAA's published predictions, on a log scale in minutes. OTC's predictor: median ${mOtc.toFixed(2)} min, every gauge under ${Math.ceil(Math.max(...otc) * 10) / 10} min. The other engine: median ${mOther.toFixed(2)} min, up to ${Math.max(...other).toFixed(1)} min.</title>
  <g class="grid">${ticks}<path class="g g3" d="M${padL} ${axisY} H${w - padR}"/></g>
  <text class="unit" x="${w - padR}" y="${axisY + 2 * fs + 12}" text-anchor="end">minutes from NOAA's tables (log scale)</text>
  <text class="row-lab r-otc" x="${padL}" y="${rowA - band / 2 - 46}">OTC predictor</text>
  <text class="row-lab r-other" x="${padL}" y="${rowB - band / 2 - 46}">Another open engine</text>
  <g class="dots">${dots(otc, rowA, "d-otc")}${dots(other, rowB, "d-other")}</g>
  <g class="meds">${med(mOtc, rowA, "m-otc", `median ${mOtc.toFixed(2)} min`, "start", 14)}${med(mOther, rowB, "m-other", `median ${mOther.toFixed(2)} min`, "start")}</g>
</svg>`;
}

const out = join(here, "..", "..", "src", "partials");
const files = {
  "engine-check-wide.svg": chart({ w: 1100, h: 400, padL: 8, padR: 8, rowA: 120, rowB: 280, axisY: 350, r: 4.5, band: 64, fs: 18, id: "engw", label: "wide" }),
  "engine-check-compact.svg": chart({ w: 520, h: 560, padL: 6, padR: 6, rowA: 150, rowB: 380, axisY: 500, r: 4.5, band: 90, fs: 20, id: "engc", label: "compact" }),
};
for (const [name, svg] of Object.entries(files)) writeFileSync(join(out, name), svg + "\n");
console.log(`wrote ${Object.keys(files).length} files to src/partials/`);
