// Check every internal link and fragment in dist/. Exit 1 on any broken one.
// With --external, also fetch each external URL once and report its status
// (reported only; external failures do not change the exit code).
import { readFileSync, readdirSync, statSync, existsSync } from "node:fs";
import { join, dirname, relative } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const dist = join(root, "dist");
const checkExternal = process.argv.includes("--external");

function walk(dir) {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    return statSync(p).isDirectory() ? walk(p) : [p];
  });
}

const htmlFiles = walk(dist).filter((f) => f.endsWith(".html"));
const idsByFile = new Map();
function ids(file) {
  if (!idsByFile.has(file)) {
    const html = readFileSync(file, "utf8");
    idsByFile.set(file, new Set([...html.matchAll(/\sid="([^"]+)"/g)].map((m) => m[1])));
  }
  return idsByFile.get(file);
}

function resolve(pathname) {
  let p = join(dist, decodeURIComponent(pathname));
  if (pathname.endsWith("/")) p = join(p, "index.html");
  if (existsSync(p) && statSync(p).isFile()) return p;
  return null;
}

const broken = [];
const external = new Map();
let internalCount = 0;

const site = JSON.parse(readFileSync(join(root, "src/site.json"), "utf8"));

for (const file of htmlFiles) {
  const html = readFileSync(file, "utf8");
  const page = "/" + relative(dist, file).replace(/index\.html$/, "");
  for (const m of html.matchAll(/\s(?:href|src)="([^"]+)"/g)) {
    let url = m[1];
    // Links to this site's own domain are checked as internal paths.
    if (url.startsWith(site.url + "/")) url = url.slice(site.url.length);
    if (/^(mailto|tel):/.test(url)) continue;
    if (/^https?:\/\//.test(url)) {
      if (!external.has(url)) external.set(url, new Set());
      external.get(url).add(page);
      continue;
    }
    internalCount++;
    const u = new URL(url, "https://site.invalid" + page);
    const target = u.pathname === page && url.startsWith("#") ? file : resolve(u.pathname);
    if (!target) {
      broken.push(`${page}: ${url} (no such file)`);
      continue;
    }
    if (u.hash && target.endsWith(".html") && !ids(target).has(decodeURIComponent(u.hash.slice(1)))) {
      broken.push(`${page}: ${url} (no element with id ${u.hash})`);
    }
  }
}

// Every URL in the sitemap must exist too.
const sitemap = readFileSync(join(dist, "sitemap.xml"), "utf8");
for (const m of sitemap.matchAll(/<loc>([^<]+)<\/loc>/g)) {
  internalCount++;
  if (!resolve(new URL(m[1]).pathname)) broken.push(`sitemap.xml: ${m[1]} (no such file)`);
}

console.log(`checked ${internalCount} internal links in ${htmlFiles.length} pages and the sitemap`);
if (broken.length) {
  console.log(`BROKEN (${broken.length}):\n  ` + broken.join("\n  "));
}

console.log(`${external.size} distinct external URLs`);
if (checkExternal) {
  for (const [url, pages] of external) {
    let status;
    try {
      const res = await fetch(url, { method: "GET", redirect: "follow", signal: AbortSignal.timeout(15000) });
      status = res.status;
    } catch (e) {
      status = `error: ${e.cause?.code ?? e.name}`;
    }
    console.log(`  ${status}  ${url}  (${[...pages].join(", ")})`);
  }
}

process.exit(broken.length ? 1 : 0);
