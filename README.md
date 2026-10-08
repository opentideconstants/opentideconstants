# opentideconstants.org

Source of the OpenTideConstants (OTC) website. OTC combines open tide data (official agency constants, constants fitted from gauge records, and tide-model constants) into one consistent, documented dataset of tidal harmonic constants: one format, declared conventions, provenance for every record, and the accuracy of every station against official predictions. It is rebuilt automatically from the latest upstream data. The site describes the method, the format and the sources.

## Stack, and why

Plain HTML pages, one CSS file plus one token file, and a build script of about 120 lines (`build.mjs`) that uses only Node's standard library. There are **no npm dependencies**. Client-side JavaScript is limited to two small optional scripts: the day/night palette toggle (`assets/mode.js`) and the Home page plot animation (`assets/plot.js`). Every page works without them.

- GitHub Pages serves static files; nothing more is needed.
- The script adds what plain HTML lacks for a multi-page site: one shared layout (header, navigation, footer), the current-page marker in the navigation, canonical URLs, `sitemap.xml`, and a check that no placeholder is left unfilled.
- A generator (Eleventy, Hugo, Jekyll) would add a toolchain and a dependency tree to keep current, for 12 pages that are hand-written HTML anyway. If the site later needs per-station pages generated from release files, the same script can read the release JSON directly, or the site can move to Eleventy then.

## Layout

```
build.mjs                 build: src/ -> dist/
src/site.json             site settings (URLs, format version), usable as {{site.<key>}}
src/layout.html           shared page layout
src/pages/*.html          one file per page, with a front-matter block (title, description, nav, order, layout)
src/partials/             SVG artwork inlined into pages with {{include <file>}} (generated, see scripts/art/)
src/assets/tokens.css     design tokens (the theme), with the day and night palettes and @font-face rules
src/assets/site.css       layout and element styles; uses only tokens
src/assets/fonts/         self-hosted fonts (woff2, latin subset) and their SIL OFL licences
src/assets/mode.js        day/night palette toggle, kept in localStorage
src/assets/plot.js        plays the Home tide-curve animation when it scrolls into view
src/schema/               draft JSON Schema and an example document, served at /schema/
src/CNAME, src/robots.txt copied to dist/
scripts/check-links.mjs   internal link and fragment check (add --external to report external URLs)
scripts/check_schema.py   schema and example check, with negative controls
scripts/screenshots.mjs   Playwright screenshots (3 sizes, day and night) and horizontal-overflow check (Playwright not a dependency)
scripts/check-contrast.mjs  WCAG contrast of the colour pairs the site uses, in both palettes
scripts/art/              generators for the chart sheet and the tide-curve plot in src/partials/
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
node scripts/check-contrast.mjs      # contrast of text and plot colours, day and night
node scripts/art/generate.mjs        # regenerate the SVG artwork in src/partials/
```

## Theming

The theme is B2 "Contour": the site reads like a nautical chart, light by default with a night palette. `DESIGN.md` describes the system. All colours, fonts, sizes, spacing, radii and durations are CSS custom properties in `src/assets/tokens.css`. `site.css` uses only those properties. The night palette applies when the OS asks for dark (`prefers-color-scheme: dark`) unless the reader chose the day palette, or when the header toggle sets `data-theme="dark"` on `<html>`.

Tokens: colours `--color-bg`, `-surface`, `-text`, `-text-muted`, `-border`, `-link`, `-link-hover`, `-accent`, `-on-accent`, `-focus`, `-note-bg`, `-note-border`, `-note-text`, `-code-bg`, `-code-text`, `-panel`; chart colours `--color-shoal`, `-shallow`, `-land`, `-coast`, `-grat`, `-contour`, `-sounding`, `-d10`; plot colours `--color-plot-official`, `-plot-ticon`, `-plot-fit`, `-plot-grid`, `-plot-grid-strong`; `--font-body`, `--font-heading`, `--font-display`, `--font-label`, `--font-mono`, `--font-size-base`, `--font-size-small`, `--font-size-display`, `--font-size-h1`, `--font-size-h2`, `--font-size-h3`, `--line-height-body`, `--line-height-heading`, `--font-weight-heading`, `--measure`; `--space-1` … `--space-8`, `--gutter`, `--content-max`; `--radius-sm`, `--radius-md`, `--border-width`, `--graticule`; `--duration-fast`, `--easing`, `--easing-expo`.

## Hosting

- GitHub Pages, deployed by `.github/workflows/pages.yml` on every push to `main` (pull requests build and check only). In the repository settings, set Pages to "GitHub Actions".
- Custom domain `opentideconstants.org` (`src/CNAME`), behind Cloudflare.
- Data downloads are not served from this site. They will come from `data.opentideconstants.org` (Cloudflare R2), with `OTC_latest.json` as the pointer to the current release.

## Licence

The data licence is described at `/licence/`. The licence for this repository (site text and code) is not chosen yet.
