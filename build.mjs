// Static site build for opentideconstants.org.
// No dependencies: Node's standard library only.
//
// src/pages/<name>.html  -> dist/<name>/index.html  (index.html -> dist/index.html,
//                                                   404.html   -> dist/404.html)
// Each page starts with a front-matter block:
//   ---
//   title: Why
//   description: One sentence for search results.
//   nav: Why            (optional; label in the main navigation)
//   order: 2            (optional; navigation order)
//   layout: full        (optional; the body is inserted as is, for full-width sections.
//                        Otherwise it goes inside <div class="wrap doc">.)
//   ---
// The page body is inserted into src/layout.html. Placeholders: {{title}},
// {{description}}, {{canonical_tag}}, {{nav}}, {{content}}, {{year}}, and any key of
// src/site.json as {{site.<key>}} (also usable inside page bodies). {{page}} is the page name.
// A page body can inline a file from src/partials/ with {{include <file>}} (used for SVG artwork).
// Everything else under src/ (except pages/, partials/ and layout.html) is copied as is.

import { readFileSync, writeFileSync, mkdirSync, rmSync, readdirSync, statSync, copyFileSync } from "node:fs";
import { join, dirname, relative } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(fileURLToPath(import.meta.url));
const src = join(root, "src");
const out = join(root, "dist");
const site = JSON.parse(readFileSync(join(src, "site.json"), "utf8"));
const layout = readFileSync(join(src, "layout.html"), "utf8");

function parsePage(file) {
  const raw = readFileSync(file, "utf8");
  const m = raw.match(/^---\n([\s\S]*?)\n---\n/);
  if (!m) throw new Error(`${file}: missing front matter`);
  const meta = {};
  for (const line of m[1].split("\n")) {
    const i = line.indexOf(":");
    if (i > 0) meta[line.slice(0, i).trim()] = line.slice(i + 1).trim();
  }
  for (const key of ["title", "description"]) {
    if (!meta[key]) throw new Error(`${file}: front matter needs "${key}"`);
  }
  return { meta, body: raw.slice(m[0].length) };
}

function fillSite(text) {
  return text.replace(/\{\{site\.(\w+)\}\}/g, (_, k) => {
    if (!(k in site)) throw new Error(`unknown site key: ${k}`);
    return String(site[k]);
  });
}

function fillIncludes(text) {
  return text.replace(/\{\{include ([\w.-]+)\}\}/g, (_, name) => readFileSync(join(src, "partials", name), "utf8").trim());
}

function copyTree(from, to) {
  for (const name of readdirSync(from)) {
    if (from === src && (name === "pages" || name === "partials" || name === "layout.html" || name === "site.json")) continue;
    const a = join(from, name);
    const b = join(to, name);
    if (statSync(a).isDirectory()) {
      mkdirSync(b, { recursive: true });
      copyTree(a, b);
    } else {
      copyFileSync(a, b);
    }
  }
}

rmSync(out, { recursive: true, force: true });
mkdirSync(out, { recursive: true });
copyTree(src, out);

const pages = readdirSync(join(src, "pages"))
  .filter((f) => f.endsWith(".html"))
  .map((f) => {
    const name = f.replace(/\.html$/, "");
    const { meta, body } = parsePage(join(src, "pages", f));
    const path = name === "index" ? "/" : name === "404" ? "/404.html" : `/${name}/`;
    const file = name === "index" ? "index.html" : name === "404" ? "404.html" : `${name}/index.html`;
    return { name, meta, body, path, file };
  });

const navPages = pages
  .filter((p) => p.meta.nav)
  .sort((a, b) => Number(a.meta.order ?? 99) - Number(b.meta.order ?? 99));

for (const page of pages) {
  const nav = navPages
    .map((p) => {
      const current = p.path === page.path ? ' aria-current="page"' : "";
      return `<li><a href="${p.path}"${current}>${p.meta.nav}</a></li>`;
    })
    .join("\n          ");
  const html = fillSite(
    layout
      .replaceAll("{{content}}", page.meta.layout === "full" ? fillIncludes(page.body.trim()) : `<div class="wrap doc">\n${page.body.trim()}\n</div>`)
      .replaceAll("{{page}}", page.name)
      .replaceAll("{{nav}}", nav)
      .replaceAll("{{title}}", page.name === "index" ? `${site.name}: open tidal harmonic constants` : `${page.meta.title} · ${site.name}`)
      .replaceAll("{{description}}", page.meta.description)
      .replaceAll("{{canonical_tag}}", page.name === "404" ? "<meta name=\"robots\" content=\"noindex\">" : `<link rel="canonical" href="${site.url}${page.path}">`)
      .replaceAll("{{year}}", String(new Date().getUTCFullYear()))
  );
  if (/\{\{[^}]+\}\}/.test(html)) throw new Error(`${page.file}: unfilled placeholder ${html.match(/\{\{[^}]+\}\}/)[0]}`);
  const dest = join(out, page.file);
  mkdirSync(dirname(dest), { recursive: true });
  writeFileSync(dest, html);
}

const urls = pages
  .filter((p) => p.name !== "404")
  .sort((a, b) => a.path.localeCompare(b.path))
  .map((p) => `  <url><loc>${site.url}${p.path}</loc></url>`)
  .join("\n");
writeFileSync(
  join(out, "sitemap.xml"),
  `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${urls}\n</urlset>\n`
);

console.log(`built ${pages.length} pages into ${relative(root, out)}/`);
