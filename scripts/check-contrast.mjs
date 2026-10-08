// WCAG contrast check of the colour pairs the site uses, in the day and the night palette.
// Reads src/assets/tokens.css. Text pairs need 4.5:1; non-text marks (plot lines) need 3:1.
//   node scripts/check-contrast.mjs        exit 1 if a pair fails
import { readFileSync } from "node:fs";

const css = readFileSync(new URL("../src/assets/tokens.css", import.meta.url), "utf8");
const block = (re) => Object.fromEntries([...css.match(re)[1].matchAll(/--color-([\w-]+):\s*(oklch\([^)]*\))/g)].map((m) => [m[1], m[2]]));
const day = block(/:root \{([\s\S]*?)\n\}/);
const night = block(/@media \(prefers-color-scheme: dark\) \{\s*:root \{([\s\S]*?)\n  \}/);

function oklchToSrgb(s) {
  const [L, C, H] = s.replace(/oklch\(|\)/g, "").trim().split(/\s+/).map(Number);
  const h = (H * Math.PI) / 180, a = C * Math.cos(h), b = C * Math.sin(h);
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const q = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
  const lin = [4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * q, -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * q, -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * q];
  return lin.map((x) => Math.min(1, Math.max(0, x)));
}
const lum = (s) => { const [r, g, b] = oklchToSrgb(s); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };

// [foreground, background, minimum, where]
const pairs = [
  ["text", "bg", 4.5, "body text"],
  ["text-muted", "bg", 4.5, "muted text, captions"],
  ["link", "bg", 4.5, "links"],
  ["link-hover", "bg", 4.5, "hovered links"],
  ["text", "surface", 4.5, "text on surface"],
  ["text-muted", "surface", 4.5, "muted text on surface"],
  ["link", "surface", 4.5, "documentation sidebar, current page"],
  ["code-text", "code-bg", 4.5, "code"],
  ["link", "code-bg", 4.5, "links next to code"],
  ["on-accent", "accent", 4.5, "button label"],
  ["on-accent", "link-hover", 4.5, "hovered button label"],
  ["note-text", "note-bg", 4.5, "status note"],
  ["link", "note-bg", 4.5, "status note heading"],
  ["text", "shallow", 4.5, "source streams"],
  ["link", "shallow", 4.5, "links in source streams"],
  ["text", "panel", 4.5, "panels on the graticule"],
  ["text-muted", "panel", 4.5, "muted text in panels"],
  ["sounding", "bg", 4.5, "chart soundings"],
  ["plot-official", "panel", 3, "plot: official curve"],
  ["plot-ticon", "panel", 4.5, "plot: TICON-4 curve and label"],
  ["plot-fit", "panel", 4.5, "plot: our fit curve and label"],
  ["contour", "bg", 3, "chart contours, flow lines"],
];
let fails = 0;
for (const [name, t] of [["day", day], ["night", night]]) {
  console.log(`${name} palette`);
  for (const [fg, bg, min, where] of pairs) {
    const r = ratio(t[fg], t[bg]);
    const ok = r >= min;
    if (!ok) fails++;
    console.log(`  ${ok ? "ok  " : "FAIL"} ${r.toFixed(2).padStart(5)}:1 (min ${min}) ${fg} on ${bg}  ${where}`);
  }
}
process.exit(fails ? 1 : 0);
