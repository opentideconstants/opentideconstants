// Screenshot every page at two phone sizes and one desktop size, in the day and the
// night palette, and fail if any page scrolls horizontally. Each shot waits for the
// entrance animations to end, so the shots show the final state. Needs Playwright (not a project dependency):
//   npx playwright install chromium   (once)
//   node scripts/screenshots.mjs http://localhost:8000 <out-dir>
// PLAYWRIGHT_PATH may point at a global install, e.g. "$(npm root -g)/playwright/index.mjs".
const { chromium } = await import(process.env.PLAYWRIGHT_PATH ?? "playwright");
import { readFileSync, mkdirSync } from "node:fs";

const base = process.argv[2] ?? "http://localhost:8000";
const outDir = process.argv[3] ?? "screenshots";
mkdirSync(outDir, { recursive: true });

const sitemap = readFileSync(new URL("../dist/sitemap.xml", import.meta.url), "utf8");
const paths = [...sitemap.matchAll(/<loc>https?:\/\/[^/]+(\/[^<]*)<\/loc>/g)].map((m) => m[1]);
paths.push("/404.html"); // the python test server does not serve 404.html for unknown paths

const viewports = [
  { name: "375x667", width: 375, height: 667 },
  { name: "430x932", width: 430, height: 932 },
  { name: "1440x900", width: 1440, height: 900 },
];
const schemes = (process.env.SCHEMES ?? "light,dark").split(",");

const browser = await chromium.launch();
let failures = 0;
for (const scheme of schemes) {
  for (const vp of viewports) {
    const ctx = await browser.newContext({ viewport: { width: vp.width, height: vp.height }, colorScheme: scheme });
    const page = await ctx.newPage();
    for (const p of paths) {
      const res = await page.goto(base + p, { waitUntil: "load" });
      await page.evaluate(async () => { await document.fonts.ready; await Promise.race([Promise.all(document.getAnimations().map((a) => a.finished)), new Promise((r) => setTimeout(r, 6000))]); });
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      const slug = p === "/" ? "home" : p.replace(/\//g, "") || "home";
      const file = `${outDir}/${slug}-${vp.name}-${scheme}.png`;
      await page.screenshot({ path: file, fullPage: true });
      const ok = overflow <= 0;
      if (!ok) failures++;
      console.log(`${ok ? "ok  " : "FAIL"} ${res.status()} ${p} ${vp.name} ${scheme} overflow=${overflow}px -> ${file}`);
    }
    await ctx.close();
  }
}
await browser.close();
process.exit(failures ? 1 : 0);
