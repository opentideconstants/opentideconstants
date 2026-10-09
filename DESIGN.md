# Design system: B2 "Contour"

The site reads like a nautical chart. The scene: a marine-software developer checks a station against a chart, on a day chart at a desk or, at night, on a dimmed bridge display. So the site is light by default and has a night palette. Product context, audiences and principles are in `PRODUCT.md`.

All values are CSS custom properties in `src/assets/tokens.css`. `src/assets/site.css` uses only those properties. The one exception is the Tide Mechanics page, which has its own scoped palette and type (see [Tide Mechanics](#tide-mechanics)).

## Colour

Strategy: full palette, from the chart symbol set. Each colour has one job.

| Role | Day (OKLCH) | Night (OKLCH) | Used for |
|---|---|---|---|
| Water (`--color-bg`) | `1 0 0` | `0.15 0.005 250` | page background |
| Sounding black (`--color-text`) | `0.2 0.02 260` (18.1:1) | `0.86 0.008 250` (12.9:1) | headings, bold labels, neatlines, frames |
| Muted (`--color-text-muted`) | `0.46 0.03 255` (7.1:1) | `0.635 0.02 250` (5.75:1) | secondary text, captions |
| Prose (`--color-prose`) | = muted | = muted | running text: paragraphs, list items, definitions, section introductions |
| Accent (`--color-accent`, `--color-link`, `--color-focus`): chart magenta by day, sea-glass green by night | `0.5 0.21 345` (6.8:1) | `0.68 0.12 158`, `#4dae7b` (7.2:1); hover `0.78 0.09 160` (10.2:1) | links, the button, focus ring, cautions, badges, the compass rose and highlight, our fit |
| Surface (`--color-surface`, `--color-code-bg`) | `0.955 0.018 230` | `0.2 0.008 250` | code, the documentation sidebar |
| Shoal (`--color-shoal`) and shallow (`--color-shallow`) blue | `0.86 0.06 225`, `0.92 0.04 225` | `0.3 0.045 235`, `0.22 0.03 235` | depth bands, source streams |
| Land buff (`--color-land`) | `0.89 0.075 85` | `0.34 0.04 85` | the island fill only; never a background |
| Graticule, contour, sounding | `--color-grat`, `--color-contour`, `--color-sounding` | | chart texture |
| Plot lines | official = text colour, TICON-4 `0.52 0.12 240` / `0.8 0.1 225`, our fit = the accent (magenta by day, sea-glass green by night) | | tide-curve plot |

Running text is one step softer than headings, as on aimock: it uses the muted colour, and bold run-in labels inside it keep the text colour at weight 600.

Contrast is checked by `node scripts/check-contrast.mjs` for every pair the site uses, in both palettes. All text pairs are 4.5:1 or more; the lowest day text pair is magenta links on the shallow blue, 5.4:1; the lowest night text pair is muted text on the surface, 5.3:1. The script covers the `tokens.css` palettes only, not the Tide Mechanics palette. Colour never carries meaning alone: plot lines and status notes carry words.

### Night palette

- The night palette applies when the OS asks for dark (`prefers-color-scheme: dark`). It is automatic: there is no switch, and it needs no JavaScript.
- The night accent is sea-glass green (`0.68 0.12 158`, `#4dae7b`), not magenta. It is 7.2:1 on the night background and is used for links, the button, the focus ring, caution frames, badges, the compass rose and highlight, and our fit line. The day palette keeps chart magenta.
- Font smoothing differs by palette. The day palette sets `-webkit-font-smoothing: antialiased` (and `-moz-osx-font-smoothing: grayscale`). The night palette keeps the default (`auto`), because `antialiased` turns off macOS stem darkening, and regular-weight light text on the dark background then renders thin and grainy. Bold headings are not affected.

## Type

The type system follows aimock.copilotkit.dev: Instrument Sans for prose and headings, JetBrains Mono for everything that reads as a label or a value.

| Token | Family | Use |
|---|---|---|
| `--font-body`, `--font-heading`, `--font-display` (`--font-sans`) | Instrument Sans, 400–700 | body text, headings (700), card and list headings (600), nav links (500) |
| `--font-label` | Instrument Sans italic 500 | hydrographic names only (Helgoland, German Bight), as names are set on charts |
| `--font-mono` | JetBrains Mono, 300–700, italic 400 | the brand, buttons, tags and status labels, table heads, sidebar group labels, code and file names, numbers, coordinates, compass and plot labels, chart soundings (italic) |

Fonts are self-hosted in `src/assets/fonts/` (woff2, latin subset, SIL Open Font License, licence files beside them). Every `@font-face` uses `font-display: swap`. The layout preloads the upright sans and mono; the two italics load on demand. The site's own type uses no third-party font service; only the Tide Mechanics page loads its fonts from Google Fonts. Code turns off ligatures (`--font-features-mono`).

Scale: hero `clamp(2rem, …, 4.5rem)` at 1.08 line height and -0.03em; h1 `clamp(1.9rem, …, 3rem)` at -0.02em; h2 `clamp(1.5rem, …, 2.25rem)` at -0.02em (Home sections); inside documentation pages h2 `clamp(1.4rem, …, 1.85rem)` and h3 1.25rem, both at -0.01em; h3 elsewhere 1.05rem / 600; lead 1.2rem at 1.7; body 1rem. Line height is 1.7 for running text (paragraphs, list items) and 1.6 for interface text (navigation, tables, captions); headings are 1.1. Small 0.875rem, mono labels 0.8rem.

Spacing: the section rhythm (`--space-section`) is `clamp(1.75rem, …, 2rem)`, tighter than aimock's, by the owner's request. Headings have line height 1.1, so each gap at a heading adds `--space-h2-leading` (0.25em) to match the visible gap of a 1.6 line height.

## Width

Running text has no width cap of its own: paragraphs, list items, captions and section introductions fill their column, so their edges line up with the tables, cards and figures in the same column. Where a column is too wide to read, the column is capped, not the paragraph:

- Content max width (`--content-max`) is 70rem for Home and the header.
- A stand-alone text page (`.wrap.doc`: About, Why, Downloads, Changelog, Citation, Licence, Data, 404) is a 52rem column (`--measure-page`), the same width as a docs page's content, with its left edge on the header's.
- A docs page's text column is at most 960px (see below).
- `--measure` (68ch) is kept only for a narrow table (`.table-wrap.narrow`). Headings have no width cap and use `text-wrap: balance`.

## Header and footer

The header is sticky (`--z-sticky`) on a solid background. `assets/header.js` watches a 1px sentinel with an IntersectionObserver and adds `.is-stuck` once the page scrolls, which shows the bottom rule and a faint shadow. The navigation has five links: Home, Why, Documentation, Downloads and About. Below 48rem it becomes one row under the brand that scrolls sideways, with the current page's link scrolled into view. Every element with an `id` has `scroll-margin-top` of the header height (`--header-h`, 4rem, or 5.75rem for the two-row header below 48rem) plus 1rem, so anchored headings are not hidden under it. On documentation pages the header spans the full width.

The brand mark is a tidal diamond: an outlined square on its point, with the top-right and bottom-left quarters filled in the accent. The footer carries the data licence and links to Tide Mechanics (first), How to cite, Releases, Contact and Issues.

## Documentation layout

Documentation pages (`layout: docs`) work like the aimock docs:

- **Left sidebar.** Fixed under the header, 260px (`--sidebar-w`), on the surface colour, scrolling on its own. It lists the docs pages in two groups (Documentation, then the fourteen Sources), with mono uppercase group labels. The current page is tinted with the accent and scrolled to the middle of the sidebar on load. A page with `sidebar: hidden` in its front matter is built and stays in the sitemap, but is not listed (used for a source that is not in the current release).
- **Text column.** At most 960px, centred between the sidebars. The footer lines up with it.
- **"On this page".** A sticky 220px list at the right edge that links the page's h2 and h3 headings (h3 indented) and marks the one being read (`assets/docs.js`). It appears only when a page has four or more such headings. The build gives every h2 and h3 an id.
- Below 75rem (1200px) the right list is hidden. At 48rem (768px) and below the left sidebar is off-screen and slides in from the left when the header's menu button (☰) is pressed.

## Chart idiom

- **Graduated neatline.** The hero chart sheet has the alternating black-and-white border of a printed chart. Every documentation page title sits on the same neatline.
- **Cartouche.** The hero title and the release box are framed like a chart's title cartouche: a 1px rule with a double outline.
- **Caution frame.** Status notes (for example "Not in the current release" on a source page) have a 2px accent frame (magenta by day, sea-glass green by night), as cautions are printed on charts. The Helgoland note on the hero uses the same frame.
- **Graticule.** The proof section sits on a graticule of the same cell size as the hero chart (`--graticule`).
- **Diamonds.** Lists of properties use the tidal-diamond mark.
- **Request icon.** A per-item action is an inline outline SVG (`currentColor`, link colour, `aria-hidden` inside a link with an `aria-label` and a matching `title`), never an emoji or a repeated text link; it sits in one trailing column with a 32px hit target (44px on touch).

## Home

1. **Chart sheet hero.** A chart of Helgoland (depth bands, contours, soundings, a compass rose, a tide-gauge symbol), with the title in a cartouche and a framed Helgoland note. The chart is decorative (`aria-hidden`); its soundings are illustrative, not survey data.
2. **Three kinds of source, one release.** Official constants, gauge records that OTC fits and model constants run as three streams, into one release box. The introduction has a line of its own that links Tide Mechanics. On narrow screens the streams stack and a single line joins them to the release.
3. **Why one consistent dataset matters.** The Helgoland tide-curve plot: the official prediction, TICON-4 shifted by its mean time error (88–90 min) and our fit shifted by its mean time error (6.7 min). There is a wide and a compact version of the plot.
4. **What is different.** A diamond-marked list; the first item ("one dataset from many open sources") spans the full width.
5. **Sources at a glance.** One table of every source, with what OTC takes, its licence and its rank. The closing paragraph is set like a section introduction.

The artwork is generated by `node scripts/art/generate.mjs` into `src/partials/` and inlined by the build with `{{include <file>}}`.

## Documentation pages

Pages without `layout: full` or `layout: docs` are wrapped in `.wrap.doc`, the 52rem column. Text and tables share the column width. Definition lists become a two-column label grid on wide screens. Tables scroll inside their wrapper (minimum width 34rem), never the page; table heads are mono, muted, over a 1.5px text-colour rule. Code blocks use the surface blue with a 1px border.

- **About** opens with the title straight into the bio paragraph (no section heading above it), then: Report a problem or ask a question, Privacy, Licence and attribution (with Attribution and Thanks), and How to cite. The owner's name links to his ORCID record; the number is not printed.
- **Downloads** lists every release, newest first, read from the release index by `assets/releases.js`. Formats and Locations are an accordion, closed by default, with a mono `+` / `−` marker.

## Tide Mechanics

`/tide-mechanics/` (`layout: full`) is an interactive explainer with its own design. Its type and palette are set in `assets/tide-mechanics.css` and scoped to the `.tm` page body, so the site header and footer keep the site tokens.

- **Type.** Libre Bodoni for headings and the formula, IBM Plex Sans for text, IBM Plex Mono for values and labels, loaded from Google Fonts. Body 17px at 1.55. The column is at most 980px; text fills it, as on the rest of the site.
- **Palette.** Its own day and night hex palette (paper, ink, rule, shoal, land, a magenta accent and one colour per constituent), switched by `prefers-color-scheme` like the rest of the site. `assets/tide-mechanics.js` redraws the canvases when the OS scheme changes.
- **Figures.** Each canvas's drawn height is reserved in CSS (with container queries for narrow figures), so the page does not shift after load and an anchor link lands on its heading.
- **Table heads.** Plain words are uppercase and tracked. Math symbols and units are wrapped in `<span class="sym">`, which keeps their case: Ω is not ω, and H and M are not the units h and m.
- **The full constituent table** (all 37 NOAA constituents, section 2) is a `<details>`, closed by default on every screen size. It opens only when a link targets it (`#s2-full-det`); the native summary is the toggle.
- **Section 8, From source to SDK.** The pipeline is a numbered flow of steps joined by a rule and arrow. Status values are chips: `ok` and `verified` in the ok green; `corrected` and `flagged` in the M2 blue; `unverified` and `no_constants` in the muted grey; `disputed` in the error red.

## Icons and link previews

All of these are generated by `node scripts/og-images.mjs` (Playwright) and committed:

- **`assets/favicon.svg`.** The header's diamond mark on a rounded tile, drawn on a 16-unit grid (`viewBox="0 0 16 16"`) so it renders symmetric at 16px: the corners land at 8,1 / 15,8 / 8,15 / 1,8, the centre is on a pixel corner, and the 1.5-unit outline is drawn after the fill so all four sides have the same weight. Under `prefers-color-scheme: dark` the tile, ink and accent switch to the night palette.
- **`assets/icons/favicon-32.png` and `apple-touch-icon.png` (180×180).** The same mark in the day palette.
- **Link previews**, 1200×630: `assets/og/default.png` (site palette and fonts) and `assets/og/tide-mechanics.png` (that page's palette and fonts). A page uses `assets/og/<page>.png` when it exists, else the default. The build writes the Open Graph and X card tags, reads the image size from the PNG header, and fails if an image has no alt text in `OG_ALT` in `build.mjs`.

## Asset URLs

The host lets browsers cache assets for hours, so the build adds `?v=<hash of the file>` to every `/assets/*.css` and `/assets/*.js` URL in a page. New HTML then always loads the CSS and JavaScript it was built with. Images, fonts, icons and preview images are not hashed, so their URLs stay stable (link-preview caches keep the image URL).

## Motion

- Hero: the depth contours draw outward, the soundings fade in one by one and the compass rose turns to north (about 2.4 s, ease-out-expo).
- Proof plot: when it first scrolls into view (`assets/plot.js`), the official curve draws, then TICON-4 and our fit slide out to their lag and the high-water marks appear. The plot shows its final state by default, so it is complete without JavaScript, before it is reached, and in screenshots.
- With `prefers-reduced-motion: reduce`, every animation and transition is off and the final state shows at once, on Tide Mechanics too.

## Checks

`node build.mjs`, `node scripts/check-links.mjs` (both are `npm run check`), `npx html-validate "dist/**/*.html"`, `node scripts/check-contrast.mjs`, and `scripts/screenshots.mjs` (375x667, 430x932 and 1440x900, day and night, fails on horizontal page scroll).
