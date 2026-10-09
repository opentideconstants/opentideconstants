// Render the link-preview images, the PNG icons and the SVG favicon from the HTML/SVG below.
// The output is committed under src/assets/og/, src/assets/icons/ and src/assets/favicon.svg;
// run this again after a change here:
//   npx playwright install chromium   (once)
//   node scripts/og-images.mjs
// PLAYWRIGHT_PATH may point at a global install, e.g. "$(npm root -g)/playwright/index.mjs".
// The default image uses the site's own fonts (inlined from src/assets/fonts/). The Tide
// Mechanics image uses that page's fonts, from Google Fonts, so this needs the network.
// Each preview is 1200x630, the size Open Graph and X recommend for a large card.
const { chromium } = await import(process.env.PLAYWRIGHT_PATH ?? "playwright");
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";

const src = new URL("../src/", import.meta.url);
const font = (file) => `url(data:font/woff2;base64,${readFileSync(new URL(`assets/fonts/${file}`, src)).toString("base64")}) format("woff2")`;
const siteFonts = `
@font-face { font-family: "Instrument Sans"; font-weight: 400 700; src: ${font("instrument-sans-latin-var.woff2")}; }
@font-face { font-family: "JetBrains Mono"; font-weight: 300 700; src: ${font("jetbrains-mono-latin-var.woff2")}; }`;

// The logo mark from the site header (src/layout.html), in the day palette.
const INK = "oklch(0.2 0.02 260)";
const ACCENT = "oklch(0.5 0.21 345)";
const MARK_OUTLINE = "M11 1 L21 11 L11 21 L1 11 Z";
const MARK_FILL = "M11 1 L21 11 L11 11 Z M11 21 L1 11 L11 11 Z";
const mark = (size, stroke = 1.6) => `<svg width="${size}" height="${size}" viewBox="0 0 22 22"><path d="${MARK_OUTLINE}" fill="none" stroke="${INK}" stroke-width="${stroke}"/><path d="${MARK_FILL}" fill="${ACCENT}"/></svg>`;

// A polyline of f(t) over [t0, t1], mapped into a box.
function curve(f, t0, t1, x0, x1, yMid, yScale, n = 400) {
  const pts = [];
  for (let i = 0; i <= n; i++) {
    const t = t0 + ((t1 - t0) * i) / n;
    pts.push(`${(x0 + ((x1 - x0) * i) / n).toFixed(1)},${(yMid - f(t) * yScale).toFixed(1)}`);
  }
  return pts.join(" ");
}

const rad = (d) => (d * Math.PI) / 180;

function defaultHtml() {
  // A semidiurnal tide with a little diurnal inequality, across the foot of the card.
  const tide = (t) => Math.cos(rad(28.984 * t - 40)) + 0.28 * Math.cos(rad(15.041 * t - 10));
  return `<!DOCTYPE html><html><head><meta charset="utf-8"><style>${siteFonts}
html, body { margin: 0; }
body { width: 1200px; height: 630px; overflow: hidden; position: relative; background: oklch(1 0 0); color: ${INK}; font-family: "Instrument Sans", sans-serif; }
.grat { position: absolute; inset: 0; background-image: linear-gradient(oklch(0.83 0.035 235 / 0.55) 1px, transparent 1px), linear-gradient(90deg, oklch(0.83 0.035 235 / 0.55) 1px, transparent 1px); background-size: 120px 110px; background-position: 40px 30px; }
.frame { position: absolute; inset: 28px; border: 2px solid ${INK}; }
.content { position: absolute; left: 88px; top: 82px; right: 88px; }
.brand { display: flex; align-items: center; gap: 22px; font-family: "JetBrains Mono", monospace; font-weight: 600; font-size: 50px; letter-spacing: -0.01em; font-feature-settings: "liga" 0, "calt" 0; }
.brand span { background: oklch(1 0 0); padding: 0 6px; }
h1 { margin: 58px 0 0; font-size: 84px; line-height: 1.04; font-weight: 700; letter-spacing: -0.03em; max-width: 960px; }
h1 span { background: oklch(1 0 0); box-decoration-break: clone; -webkit-box-decoration-break: clone; padding: 0 8px; }
h1 em { font-style: normal; color: ${ACCENT}; }
svg.tide { position: absolute; left: 30px; bottom: 30px; width: 1140px; height: 150px; }
.url { position: absolute; right: 88px; bottom: 72px; font-family: "JetBrains Mono", monospace; font-size: 26px; font-weight: 500; color: oklch(0.46 0.03 255); background: oklch(1 0 0); padding: 4px 10px; }
</style></head><body>
<div class="grat"></div>
<svg class="tide" viewBox="0 -75 1200 150" preserveAspectRatio="none"><polyline points="${curve(tide, 0, 62, 0, 1200, 0, 48)}" fill="none" stroke="${ACCENT}" stroke-width="5" stroke-linejoin="round"/></svg>
<div class="frame"></div>
<div class="content">
  <div class="brand">${mark(64)}<span>OpenTideConstants</span></div>
  <h1><span>Open tide data, combined into <em>one dataset.</em></span></h1>
</div>
<div class="url">opentideconstants.org</div>
</body></html>`;
}

function tideMechanicsHtml() {
  // The palette of src/assets/tide-mechanics.css (day). Amplitudes are illustrative: each
  // lane has its own scale so every wave reads at a small size, and the diurnal pair is
  // larger than at Boston so the sum shows diurnal inequality.
  const P = { bg: "#f3f5f2", paper: "#fbfcfa", ink: "#13263a", ink2: "#4a5d6e", rule: "#c6d0d6", grid: "#e1e7ea", accent: "#a8136f" };
  const cons = [
    { k: "M2", speed: 28.9841042, H: 1.0, g: 111, col: "#1c4f8a" },
    { k: "S2", speed: 30.0, H: 0.3, g: 147, col: "#a8136f" },
    { k: "N2", speed: 28.4397295, H: 0.22, g: 80, col: "#0f7a73" },
    { k: "K1", speed: 15.0410686, H: 0.4, g: 200, col: "#a8620a" },
    { k: "O1", speed: 13.9430356, H: 0.32, g: 215, col: "#4d7a1d" },
  ];
  const T = 72; // hours shown
  const x0 = 170, x1 = 1130;
  const laneTop = 268, laneH = 46;
  let lanes = "";
  cons.forEach((c, i) => {
    const y = laneTop + i * laneH + laneH / 2;
    const f = (t) => Math.cos(rad(c.speed * t - c.g));
    lanes += `<line x1="${x0}" x2="${x1}" y1="${y}" y2="${y}" stroke="${P.grid}" stroke-width="2"/>`;
    lanes += `<text x="${x0 - 28}" y="${y + 9}" text-anchor="end" fill="${c.col}">${c.k}</text>`;
    lanes += `<polyline points="${curve(f, 0, T, x0, x1, y, 16)}" fill="none" stroke="${c.col}" stroke-width="4" stroke-linejoin="round"/>`;
  });
  const sum = (t) => cons.reduce((s, c) => s + c.H * Math.cos(rad(c.speed * t - c.g)), 0);
  const ySum = laneTop + cons.length * laneH + 62;
  const sumScale = 44 / cons.reduce((s, c) => s + c.H, 0) * 1.1;
  const plus = `<text x="${x0 - 28}" y="${ySum + 9}" text-anchor="end" fill="${P.ink}">Σ</text>`;
  return `<!DOCTYPE html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Libre+Bodoni:wght@700&family=IBM+Plex+Sans:wght@500&family=IBM+Plex+Mono:wght@500;600&display=block">
<style>
html, body { margin: 0; }
body { width: 1200px; height: 630px; overflow: hidden; position: relative; background: ${P.bg}; color: ${P.ink}; font-family: "IBM Plex Sans", sans-serif; }
.head { position: absolute; left: 70px; top: 50px; right: 70px; border-bottom: 3px solid ${P.ink}; padding-bottom: 22px; display: flex; align-items: flex-end; justify-content: space-between; }
.kicker { font-family: "IBM Plex Mono", monospace; font-weight: 600; font-size: 24px; letter-spacing: 0.12em; text-transform: uppercase; color: ${P.accent}; display: flex; align-items: center; gap: 14px; }
h1 { margin: 10px 0 0; font-family: "Libre Bodoni", Georgia, serif; font-weight: 700; font-size: 92px; line-height: 1; letter-spacing: -0.01em; }
.sub { font-family: "Libre Bodoni", Georgia, serif; font-weight: 700; font-size: 34px; color: ${P.ink2}; margin-bottom: 8px; }
svg.fig { position: absolute; left: 0; top: 0; width: 1200px; height: 630px; }
svg.fig text { font-family: "IBM Plex Mono", monospace; font-weight: 600; font-size: 26px; }
</style></head><body>
<div class="head"><div><div class="kicker">${mark(30, 2)}OpenTideConstants · field guide</div><h1>Tide Mechanics</h1></div><div class="sub">A tide is a sum of waves</div></div>
<svg class="fig" viewBox="0 0 1200 630">
${lanes}
<line x1="${x0}" x2="${x1}" y1="${ySum - 52}" y2="${ySum - 52}" stroke="${P.rule}" stroke-width="2" stroke-dasharray="6 6"/>
<line x1="${x0}" x2="${x1}" y1="${ySum}" y2="${ySum}" stroke="${P.grid}" stroke-width="2"/>
${plus}
<polyline points="${curve(sum, 0, T, x0, x1, ySum, sumScale)}" fill="none" stroke="${P.ink}" stroke-width="6" stroke-linejoin="round"/>
</svg>
</body></html>`;
}

// The logo mark on a white tile, for apple-touch-icon and the PNG favicons. iOS rounds the
// corners itself, so the tile is square and opaque.
function iconHtml(size) {
  const pad = Math.round(size * (size <= 32 ? 0.06 : 0.16));
  const stroke = size <= 32 ? 2.4 : 1.6;
  return `<!DOCTYPE html><html><head><style>html, body { margin: 0; } body { width: ${size}px; height: ${size}px; background: #fff; display: grid; place-items: center; }</style></head>
<body>${mark(size - 2 * pad, stroke)}</body></html>`;
}

// The SVG favicon: the same mark on a tile, so it stands out on any tab bar. The tile and
// colours follow the site's palettes (src/assets/tokens.css): day by default, night under
// prefers-color-scheme: dark. The viewBox is a 16-unit grid and the mark is scaled onto it
// with its corners at 8,1 / 15,8 / 8,15 / 1,8 and its centre on a pixel corner, so the 16px
// render is symmetric. The outline is drawn over the fill, so all four sides keep the same
// weight at small sizes.
function faviconSvg() {
  const BG_NIGHT = "oklch(0.15 0.005 250)";
  const INK_NIGHT = "oklch(0.86 0.008 250)";
  const ACCENT_NIGHT = "oklch(0.68 0.12 158)";
  const k = 0.7; // 22-unit mark (corners 10 from its centre) to 7 units
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><style>.t{fill:#fff}.o{stroke:${INK}}.a{fill:${ACCENT}}@media (prefers-color-scheme:dark){.t{fill:${BG_NIGHT}}.o{stroke:${INK_NIGHT}}.a{fill:${ACCENT_NIGHT}}}</style><rect class="t" width="16" height="16" rx="3"/><g transform="translate(8 8) scale(${k}) translate(-11 -11)"><path class="a" d="${MARK_FILL}"/><path class="o" d="${MARK_OUTLINE}" fill="none" stroke-width="${(1.5 / k).toFixed(4)}" stroke-linejoin="round"/></g></svg>\n`;
}
writeFileSync(new URL("assets/favicon.svg", src), faviconSvg());
console.log("wrote src/assets/favicon.svg");

const targets = [
  { file: "assets/og/default.png", w: 1200, h: 630, html: defaultHtml() },
  { file: "assets/og/tide-mechanics.png", w: 1200, h: 630, html: tideMechanicsHtml() },
  { file: "assets/icons/apple-touch-icon.png", w: 180, h: 180, html: iconHtml(180) },
  { file: "assets/icons/favicon-32.png", w: 32, h: 32, html: iconHtml(32) },
];

const browser = await chromium.launch();
for (const t of targets) {
  const page = await browser.newPage({ viewport: { width: t.w, height: t.h }, deviceScaleFactor: 1 });
  await page.setContent(t.html, { waitUntil: "networkidle" });
  await page.evaluate(() => document.fonts.ready);
  const missing = await page.evaluate(() => [...document.fonts].filter((f) => f.status !== "loaded" && f.status !== "unloaded").map((f) => f.family));
  if (missing.length) throw new Error(`${t.file}: fonts not loaded: ${missing.join(", ")}`);
  const out = new URL(t.file, src);
  mkdirSync(new URL(".", out), { recursive: true });
  await page.screenshot({ path: out.pathname, clip: { x: 0, y: 0, width: t.w, height: t.h } });
  console.log(`wrote src/${t.file} (${t.w}x${t.h})`);
  await page.close();
}
await browser.close();
