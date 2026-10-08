# Design system: B2 "Contour"

The site reads like a nautical chart. The scene: a marine-software developer checks a station against a chart, on a day chart at a desk or, at night, on a dimmed bridge display. So the site is light by default and has a night palette. Product context, audiences and principles are in `PRODUCT.md`.

All values are CSS custom properties in `src/assets/tokens.css`. `src/assets/site.css` uses only those properties.

## Colour

Strategy: full palette, from the chart symbol set. Each colour has one job.

| Role | Day (OKLCH) | Night (OKLCH) | Used for |
|---|---|---|---|
| Water (`--color-bg`) | `1 0 0` | `0.15 0.005 250` | page background |
| Sounding black (`--color-text`) | `0.2 0.02 260` (18.1:1) | `0.86 0.008 250` (12.9:1) | text, neatlines, frames |
| Muted (`--color-text-muted`) | `0.46 0.03 255` (7.1:1) | `0.68 0.012 250` (6.8:1) | secondary text, captions |
| Accent (`--color-accent`, `--color-link`, `--color-focus`): chart magenta by day, sea-glass green by night | `0.5 0.21 345` (6.8:1) | `0.68 0.12 158`, `#4dae7b` (7.2:1); hover `0.78 0.09 160` (10.2:1) | links, the button, focus ring, cautions, badges, the compass rose and highlight, our fit |
| Shoal (`--color-shoal`) and shallow (`--color-shallow`) blue | `0.86 0.06 225`, `0.92 0.04 225` | `0.3 0.045 235`, `0.22 0.03 235` | depth bands, source streams |
| Land buff (`--color-land`) | `0.89 0.075 85` | `0.34 0.04 85` | the island fill only; never a background |
| Graticule, contour, sounding | `--color-grat`, `--color-contour`, `--color-sounding` | | chart texture |
| Plot lines | official = text colour, TICON-4 `0.52 0.12 240` / `0.8 0.1 225`, our fit = the accent (magenta by day, sea-glass green by night) | | tide-curve plot |

Contrast is checked by `node scripts/check-contrast.mjs` for every pair the site uses, in both palettes. All text pairs are 4.5:1 or more; the lowest text pair is magenta links on the shallow blue, 5.4:1 (day); at night it is green links on the shallow blue, 6.3:1. Colour never carries meaning alone: plot lines and status notes carry words.

### Night palette

- The night palette applies when the OS asks for dark (`prefers-color-scheme: dark`), unless the reader picked the day palette.
- The header button ("Night palette" / "Day palette") sets `data-theme` on `<html>` and keeps the choice in `localStorage` (`otc-palette`). Storage access is wrapped in `try`/`catch`; when it is blocked, the choice lasts for the page.
- Without JavaScript the button stays hidden and the OS setting applies.
- The night accent is sea-glass green (`0.68 0.12 158`, `#4dae7b`), not magenta. It is 7.2:1 on the night background and is used for links, the button, the focus ring, caution frames, badges, the compass rose and highlight, and our fit line. The day palette keeps chart magenta.

## Type

The type system follows aimock.copilotkit.dev: Instrument Sans for prose and headings, JetBrains Mono for everything that reads as a label or a value.

| Token | Family | Use |
|---|---|---|
| `--font-body`, `--font-heading`, `--font-display` (`--font-sans`) | Instrument Sans, 400–700 | body text, headings (700), card and list headings (600), nav links (500) |
| `--font-label` | Instrument Sans italic 500 | hydrographic names only (Helgoland, German Bight), as names are set on charts |
| `--font-mono` | JetBrains Mono, 300–700, italic 400 | the brand, buttons, tags and status labels, table heads, the palette button, code and file names, numbers, coordinates, compass and plot labels, chart soundings (italic) |

Fonts are self-hosted in `src/assets/fonts/` (woff2, latin subset, SIL Open Font License, licence files beside them). Every `@font-face` uses `font-display: swap`. The layout preloads the upright sans and mono; the two italics load on demand. No third-party font service is used. Code turns off ligatures (`--font-features-mono`).

Scale: hero `clamp(2rem, …, 4.5rem)` at 1.08 line height and -0.03em, h1 `clamp(2rem, …, 3.5rem)`, h2 `clamp(1.6rem, …, 2.8rem)` at -0.02em, h3 1.05rem / 600, lead 1.2rem at 1.7, body 1rem at 1.6, small 0.875rem, mono labels 0.8rem, measure 68ch. Content max width 70rem; section rhythm 4.5rem.

## Header

The header is sticky (`--z-sticky`) on a solid background. `assets/header.js` watches a 1px sentinel with an IntersectionObserver and adds `.is-stuck` once the page scrolls, which shows the bottom rule and a faint shadow. Below 70rem the navigation becomes one row under the brand that scrolls sideways, with the current page's link scrolled into view. Every element with an `id` has `scroll-margin-top` of the header height (`--header-h`), so anchored headings are not hidden under it.

## Chart idiom

- **Graduated neatline.** The hero chart sheet has the alternating black-and-white border of a printed chart. Every documentation page title sits on the same neatline.
- **Cartouche.** The hero title and the release box are framed like a chart's title cartouche: a 1px rule with a double outline.
- **Caution frame.** Status notes ("no release yet", "not release results") have a 2px accent frame (magenta by day, sea-glass green by night), as cautions are printed on charts.
- **Graticule.** The proof section sits on a graticule of the same cell size as the hero chart (`--graticule`).
- **Diamonds.** Lists of properties use the tidal-diamond mark.

## Home

1. **Chart sheet hero.** A chart of Helgoland (depth bands, contours, soundings, a compass rose, a tide-gauge symbol), with the title in a cartouche and a Helgoland note. The chart is decorative (`aria-hidden`); its soundings are illustrative, not survey data. The status note follows directly.
2. **Three kinds of source, one release.** Official constants, gauge records that OTC fits and model constants run as three streams, into one release box. On narrow screens the streams stack and a single line joins them to the release.
3. **Proof.** The Helgoland tide-curve plot, labelled "Proof of concept" and "Schematic": the official prediction, TICON-4 shifted by its mean time error (88–90 min) and our fit shifted by its mean time error (6.7 min). There is a wide and a compact version of the plot.
4. **What is different.** A diamond-marked list; the first item ("one dataset from many open sources") spans the full width.
5. **Sources at a glance.** One table of every source, with what OTC takes, its licence and its rank.

The artwork is generated by `node scripts/art/generate.mjs` into `src/partials/` and inlined by the build with `{{include <file>}}`.

## Documentation pages

Pages without `layout: full` are wrapped in `.wrap.doc`: text at the 68ch measure, tables at full width. Definition lists become a two-column label grid on wide screens. Tables scroll inside their wrapper (minimum width 34rem), never the page. Code blocks use the surface blue with a 1px border.

## Motion

- Hero: the depth contours draw outward, the soundings fade in one by one and the compass rose turns to north (about 2.4 s, ease-out-expo).
- Proof plot: when it first scrolls into view (`assets/plot.js`), the official curve draws, then TICON-4 and our fit slide out to their lag and the high-water marks appear. The plot shows its final state by default, so it is complete without JavaScript, before it is reached, and in screenshots.
- With `prefers-reduced-motion: reduce`, every animation and transition is off and the final state shows at once.

## Checks

`node build.mjs`, `node scripts/check-links.mjs`, `npx html-validate "dist/**/*.html"`, `node scripts/check-contrast.mjs`, and `scripts/screenshots.mjs` (375x667, 430x932 and 1440x900, day and night, fails on horizontal page scroll).
