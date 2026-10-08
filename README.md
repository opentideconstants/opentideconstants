# opentideconstants.org

Source of the OpenTideConstants (OTC) website. OTC is an open dataset of tidal harmonic constants, rebuilt automatically from the latest upstream data. **There is no data release yet**; the site describes the method, the format and the sources, and marks everything that depends on a release as "coming with the first release".

## Stack, and why

Plain HTML pages, one CSS file plus one token file, and a build script of about 114 lines (`build.mjs`) that uses only Node's standard library. There are **no npm dependencies** and no client-side JavaScript.

- GitHub Pages serves static files; nothing more is needed.
- The script adds what plain HTML lacks for a multi-page site: one shared layout (header, navigation, footer), the current-page marker in the navigation, canonical URLs, `sitemap.xml`, and a check that no placeholder is left unfilled.
- A generator (Eleventy, Hugo, Jekyll) would add a toolchain and a dependency tree to keep current, for 12 pages that are hand-written HTML anyway. If the site later needs per-station pages generated from release files, the same script can read the release JSON directly, or the site can move to Eleventy then.

## Layout

```
build.mjs                 build: src/ -> dist/
src/site.json             site settings (URLs, format version), usable as {{site.<key>}}
src/layout.html           shared page layout
src/pages/*.html          one file per page, with a front-matter block (title, description, nav, order)
src/assets/tokens.css     design tokens (the theme)
src/assets/site.css       layout and element styles; uses only tokens
src/schema/               draft JSON Schema and an example document, served at /schema/
src/CNAME, src/robots.txt copied to dist/
scripts/check-links.mjs   internal link and fragment check (add --external to report external URLs)
scripts/check_schema.py   schema and example check, with negative controls
scripts/screenshots.mjs   Playwright screenshots and horizontal-overflow check (Playwright not a dependency)
.github/workflows/pages.yml  build, checks, and deploy to GitHub Pages
CITATION.cff
```

A page `src/pages/<name>.html` is published at `/<name>/`. `index.html` is `/` and `404.html` is `/404.html`.

## Commands

```sh
node build.mjs                       # build into dist/
node scripts/check-links.mjs         # internal links and #fragments; exit 1 if broken
npx html-validate "dist/**/*.html"   # HTML validation
uv run --with jsonschema python scripts/check_schema.py
python3 -m http.server 8000 --directory dist   # serve locally
PLAYWRIGHT_PATH=$(npm root -g)/playwright/index.mjs node scripts/screenshots.mjs http://localhost:8000 screenshots
```

## Theming

All colours, fonts, sizes, spacing, radii and durations are CSS custom properties in `src/assets/tokens.css`. `site.css` uses only those properties. To apply a theme, replace `tokens.css` (and adjust `site.css` only where the theme changes layout). Dark mode is the `prefers-color-scheme: dark` block in `tokens.css`.

Tokens: `--color-bg`, `--color-surface`, `--color-text`, `--color-text-muted`, `--color-border`, `--color-link`, `--color-link-hover`, `--color-accent`, `--color-focus`, `--color-note-bg`, `--color-note-border`, `--color-note-text`, `--color-code-bg`; `--font-body`, `--font-heading`, `--font-mono`, `--font-size-base`, `--font-size-small`, `--font-size-h1`, `--font-size-h2`, `--font-size-h3`, `--line-height-body`, `--line-height-heading`, `--font-weight-heading`, `--measure`; `--space-1` … `--space-7`, `--gutter`, `--content-max`; `--radius-sm`, `--radius-md`, `--border-width`; `--duration-fast`, `--easing`.

## Hosting

- GitHub Pages, deployed by `.github/workflows/pages.yml` on every push to `main` (pull requests build and check only). In the repository settings, set Pages to "GitHub Actions".
- Custom domain `opentideconstants.org` (`src/CNAME`), behind Cloudflare.
- Data downloads are not served from this site. They will come from `data.opentideconstants.org` (Cloudflare R2), with `OTC_latest.json` as the pointer to the current release.

## Licence

The data licence is described at `/licence/`. The licence for this repository (site text and code) is not chosen yet.
