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
//   layout: docs        (optional; a documentation page: a left sidebar of every docs page,
//                        grouped by "docs", and a right "On this page" list of the page's
//                        h2 and h3 headings that have an id)
//   docs: Sources       (docs pages: the sidebar group; groups appear in DOCS_GROUPS order)
//   docs-label: GESLA   (docs pages, optional: a shorter sidebar label than the title)
//   redirect: /path/#id (optional; the page is only a redirect to that address, for old URLs.
//                        It is left out of the navigation and the sitemap; its body is ignored.)
//   ---
// The page body is inserted into src/layout.html. Placeholders: {{title}},
// {{description}}, {{canonical_tag}}, {{social_tags}}, {{nav}}, {{content}}, {{year}}, and any key of
// src/site.json as {{site.<key>}} (also usable inside page bodies). {{page}} is the page name.
// Every /assets/*.css and /assets/*.js URL in a page gets ?v=<hash of the file>, so a deploy
// reaches visitors at once even though the host lets browsers cache assets for hours.
// {{social_tags}} is the Open Graph and X card tags for link previews. The preview image is
// src/assets/og/<page>.png when that file exists, else src/assets/og/default.png. Those PNGs
// and the icons come from scripts/og-images.mjs; their URLs carry no hash, so they stay stable.
// A page body can inline a file from src/partials/ with {{include <file>}} (used for SVG artwork).
// Everything else under src/ (except pages/, partials/ and layout.html) is copied as is.

import { createHash } from "node:crypto";
import { readFileSync, writeFileSync, mkdirSync, rmSync, readdirSync, statSync, copyFileSync, existsSync } from "node:fs";
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
  .filter((p) => p.meta.nav && !p.meta.redirect)
  .sort((a, b) => Number(a.meta.order ?? 99) - Number(b.meta.order ?? 99));

const assetHash = new Map();
function versionAssets(html) {
  return html.replace(/((?:href|src)=")\/assets\/([\w.-]+\.(?:css|js))"/g, (_, attr, name) => {
    if (!assetHash.has(name)) assetHash.set(name, createHash("sha256").update(readFileSync(join(src, "assets", name))).digest("hex").slice(0, 10));
    return `${attr}/assets/${name}?v=${assetHash.get(name)}"`;
  });
}

// Link previews (Open Graph, read by Slack, iMessage, LinkedIn, Discord, Facebook; and the
// X card tags). The width and height come from the PNG header, so they match the file.
const OG_ALT = {
  default: "OpenTideConstants: open tide data, combined into one dataset.",
  "tide-mechanics": "Tide Mechanics: five constituent waves (M2, S2, N2, K1, O1) and the tide that is their sum.",
};
function pngSize(file) {
  const b = readFileSync(file);
  if (b.toString("ascii", 12, 16) !== "IHDR") throw new Error(`${file}: not a PNG`);
  return { width: b.readUInt32BE(16), height: b.readUInt32BE(20) };
}
const attr = (s) => String(s).replaceAll('"', "&quot;");
function socialTags(page, title) {
  const key = existsSync(join(src, "assets", "og", `${page.name}.png`)) ? page.name : "default";
  if (!OG_ALT[key]) throw new Error(`assets/og/${key}.png: add its alt text to OG_ALT in build.mjs`);
  const { width, height } = pngSize(join(src, "assets", "og", `${key}.png`));
  const image = `${site.url}/assets/og/${key}.png`;
  const tags = [
    ["property", "og:type", page.name === "index" ? "website" : "article"],
    ["property", "og:site_name", site.name],
    ["property", "og:title", title],
    ["property", "og:description", page.meta.description],
    ...(page.name === "404" ? [] : [["property", "og:url", `${site.url}${page.path}`]]),
    ["property", "og:image", image],
    ["property", "og:image:width", width],
    ["property", "og:image:height", height],
    ["property", "og:image:alt", OG_ALT[key]],
    ["property", "og:locale", "en_GB"],
    ["name", "twitter:card", "summary_large_image"],
    ["name", "twitter:title", title],
    ["name", "twitter:description", page.meta.description],
    ["name", "twitter:image", image],
    ["name", "twitter:image:alt", OG_ALT[key]],
  ];
  return tags.map(([k, n, v]) => `<meta ${k}="${n}" content="${attr(v)}">`).join("\n  ");
}

function redirectPage(page) {
  const to = page.meta.redirect;
  const t = JSON.stringify(to);
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>${page.meta.title} · ${site.name}</title>
  <meta name="robots" content="noindex">
  <link rel="canonical" href="${site.url}${to}">
  <meta http-equiv="refresh" content="0; url=${to}">
  <script>location.replace(location.hash ? ${t}.split("#")[0] + location.hash : ${t});</script>
</head>
<body>
  <p>${page.meta.title} has moved to <a href="${to}">${to}</a>.</p>
</body>
</html>
`;
}

const DOCS_GROUPS = ["Documentation", "Sources"];
const docsPages = pages
  .filter((p) => p.meta.layout === "docs")
  .sort((a, b) => DOCS_GROUPS.indexOf(a.meta.docs) - DOCS_GROUPS.indexOf(b.meta.docs) || Number(a.meta.order ?? 99) - Number(b.meta.order ?? 99));
for (const p of docsPages) if (!DOCS_GROUPS.includes(p.meta.docs)) throw new Error(`${p.name}: unknown docs group "${p.meta.docs}"`);
const docsHome = pages.find((p) => p.name === "documentation");

const strip = (h) => h.replace(/<[^>]+>/g, "").trim();

// Documentation pages work like the aimock docs (aimock.copilotkit.dev): a left sidebar of every
// docs page, grouped, fixed under the header and scrolling on its own; the text in a centred
// column of at most 960px; and a right "On this page" list, only when a page has four or more
// h2/h3 headings with ids. Below 1200px the right list is hidden; at 768px and below the left
// sidebar slides in from the left when the header's menu button is pressed. assets/docs.js
// adds the scroll-spy and the smooth scroll.
function docsLayout(page) {
  const groups = DOCS_GROUPS.map((g) => {
    const items = docsPages
      .filter((p) => p.meta.docs === g)
      .map((p) => {
        const current = p.path === page.path ? ' class="active" aria-current="page"' : "";
        return `<a href="${p.path}"${current}>${p.meta["docs-label"] ?? p.meta.title}</a>`;
      })
      .join("\n        ");
    return `      <div class="sidebar-section">\n        <p class="sidebar-title">${g}</p>\n        ${items}\n      </div>`;
  }).join("\n");
  // As aimock does: every h2 and h3 gets an id (a slug of its text, made unique), so every
  // heading can be linked and listed.
  const used = new Set([...page.body.matchAll(/\sid="([^"]+)"/g)].map((m) => m[1]));
  const body = page.body.replace(/<h([23])(?![^>]*\sid=)([^>]*)>([\s\S]*?)<\/h\1>/g, (_, lvl, attrs, text) => {
    const base = strip(text).toLowerCase().replace(/[^a-z0-9\s-]/g, "").replace(/\s+/g, "-").replace(/-+/g, "-").replace(/^-|-$/g, "") || "section";
    let id = base;
    for (let n = 2; used.has(id); n++) id = `${base}-${n}`;
    used.add(id);
    return `<h${lvl} id="${id}"${attrs}>${text}</h${lvl}>`;
  });
  const heads = [...body.matchAll(/<h([23]) id="([^"]+)"[^>]*>([\s\S]*?)<\/h\1>/g)];
  const toc = heads.length < 4 ? "" : `
  <aside class="page-toc" aria-labelledby="toc-title">
    <p class="page-toc-label" id="toc-title">On this page</p>
    ${heads.map(([, lvl, id, text]) => `<a href="#${id}"${lvl === "3" ? ' class="toc-h3"' : ""}>${strip(text)}</a>`).join("\n    ")}
  </aside>`;
  return `<div class="docs-layout${toc ? "" : " no-toc"}">
  <aside class="sidebar" id="docs-sidebar" aria-label="Documentation pages">
    <nav aria-label="Documentation">
${groups}
    </nav>
  </aside>
  <div class="doc docs-content">
${body.trim()}
  </div>${toc}
</div>
<script src="/assets/docs.js" defer></script>`;
}

for (const page of pages) {
  if (page.meta.redirect) {
    const dest = join(out, page.file);
    mkdirSync(dirname(dest), { recursive: true });
    writeFileSync(dest, redirectPage(page));
    continue;
  }
  const nav = navPages
    .map((p) => {
      const current = p.path === page.path ? ' aria-current="page"' : p === docsHome && page.meta.layout === "docs" ? ' aria-current="true"' : "";
      return `<li><a href="${p.path}"${current}>${p.meta.nav}</a></li>`;
    })
    .join("\n          ");
  const html = versionAssets(fillSite(
    layout
      .replaceAll("{{content}}", page.meta.layout === "full" ? fillIncludes(page.body.trim()) : page.meta.layout === "docs" ? docsLayout(page) : `<div class="wrap doc">\n${page.body.trim()}\n</div>`)
      .replaceAll("{{docs_toggle}}", page.meta.layout === "docs" ? '<button class="sidebar-toggle" type="button" aria-label="Documentation menu" aria-controls="docs-sidebar" aria-expanded="false">☰</button>' : "")
      .replaceAll("{{page}}", page.name)
      .replaceAll("{{nav}}", nav)
      .replaceAll("{{social_tags}}", socialTags(page, page.name === "index" ? `${site.name}: open tidal harmonic constants` : page.meta.title))
      .replaceAll("{{title}}", page.name === "index" ? `${site.name}: open tidal harmonic constants` : `${page.meta.title} · ${site.name}`)
      .replaceAll("{{description}}", page.meta.description)
      .replaceAll("{{canonical_tag}}", page.name === "404" ? "<meta name=\"robots\" content=\"noindex\">" : `<link rel="canonical" href="${site.url}${page.path}">`)
      .replaceAll("{{year}}", String(new Date().getUTCFullYear()))
  ));
  if (/\{\{[^}]+\}\}/.test(html)) throw new Error(`${page.file}: unfilled placeholder ${html.match(/\{\{[^}]+\}\}/)[0]}`);
  const dest = join(out, page.file);
  mkdirSync(dirname(dest), { recursive: true });
  writeFileSync(dest, html);
}

const urls = pages
  .filter((p) => p.name !== "404" && !p.meta.redirect)
  .sort((a, b) => a.path.localeCompare(b.path))
  .map((p) => `  <url><loc>${site.url}${p.path}</loc></url>`)
  .join("\n");
writeFileSync(
  join(out, "sitemap.xml"),
  `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${urls}\n</urlset>\n`
);

console.log(`built ${pages.length} pages into ${relative(root, out)}/`);
