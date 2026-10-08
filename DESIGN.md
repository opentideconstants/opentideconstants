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
| Chart magenta (`--color-accent`, `--color-link`) | `0.5 0.21 345` (6.8:1) | `0.72 0.16 345` (7.3:1) | links, the button, cautions, the compass rose, our fit |
| Shoal (`--color-shoal`) and shallow (`--color-shallow`) blue | `0.86 0.06 225`, `0.92 0.04 225` | `0.3 0.045 235`, `0.22 0.03 235` | depth bands, source streams |
| Land buff (`--color-land`) | `0.89 0.075 85` | `0.34 0.04 85` | the island fill only; never a background |
| Graticule, contour, sounding | `--color-grat`, `--color-contour`, `--color-sounding` | | chart texture |
| Plot lines | official = text colour, TICON-4 `0.52 0.12 240` / `0.8 0.1 225`, our fit = magenta | | tide-curve plot |

Contrast is checked by `node scripts/check-contrast.mjs` for every pair the site uses, in both palettes. All text pairs are 4.5:1 or more; the lowest text pair is magenta links on the shallow blue, 5.4:1 (day). Colour never carries meaning alone: launch and planned sources, plot lines and status notes all carry words.

### Night palette

- The night palette applies when the OS asks for dark (`prefers-color-scheme: dark`), unless the reader picked the day palette.
- The header button ("Night palette" / "Day palette") sets `data-theme` on `<html>` and keeps the choice in `localStorage` (`otc-palette`). Storage access is wrapped in `try`/`catch`; when it is blocked, the choice lasts for the page.
- Without JavaScript the button stays hidden and the OS setting applies.

## Type

| Token | Family | Use |
|---|---|---|
| `--font-body` | Atkinson Hyperlegible Next | body text, tables |
| `--font-heading`, `--font-display` | Schibsted Grotesk, 700–850 | headings, the brand, the hero title |
| `--font-label` | Spectral italic 500 | hydrographic names only (Helgoland, German Bight) and the hero's emphasis, as names are set on charts |
| `--font-mono` | Atkinson Hyperlegible Mono | code, file names, numbers in tables, axis labels |

Fonts are self-hosted in `src/assets/fonts/` (woff2, latin subset, SIL Open Font License). Every `@font-face` uses `font-display: swap`. The layout preloads the body, heading and label fonts. No third-party font service is used.

Scale: hero `clamp(2.4rem, …, 4.4rem)`, h1 `clamp(2.2rem, …, 3.6rem)`, h2 `clamp(1.5rem, …, 2.25rem)`, h3 1.15rem, body 1.0625rem at line height 1.6, measure 68ch.

## Chart idiom

- **Graduated neatline.** The hero chart sheet has the alternating black-and-white border of a printed chart. Every documentation page title sits on the same neatline.
- **Cartouche.** The hero title and the release box are framed like a chart's title cartouche: a 1px rule with a double outline.
- **Caution frame.** Status notes ("no release yet", "not release results") have a 2px magenta frame, as cautions are printed on charts.
- **Graticule.** The proof section sits on a graticule of the same cell size as the hero chart (`--graticule`).
- **Diamonds.** Lists of properties use the tidal-diamond mark. A filled diamond marks a launch-set source; a dashed, empty diamond marks a planned source. Both always carry the word.

## Home

1. **Chart sheet hero.** A chart of Helgoland (depth bands, contours, soundings, a compass rose, a tide-gauge symbol), with the title in a cartouche and a Helgoland note. The chart is decorative (`aria-hidden`); its soundings are illustrative, not survey data. The status note follows directly.
2. **Three kinds of source, one release.** Official constants, gauge records that OTC fits and model constants run as three streams, with each source labelled "Launch set" or "Planned", into one release box. On narrow screens the streams stack and a single line joins them to the release.
3. **Proof.** The Helgoland tide-curve plot, labelled "Proof of concept" and "Schematic": the official prediction, TICON-4 shifted by its mean time error (88–90 min) and our fit shifted by its mean time error (6.7 min). There is a wide and a compact version of the plot.
4. **What is different.** A diamond-marked list; the first item ("one dataset from many open sources") spans the full width.
5. **Sources at a glance.** The launch-set and planned tables.

The artwork is generated by `node scripts/art/generate.mjs` into `src/partials/` and inlined by the build with `{{include <file>}}`.

## Documentation pages

Pages without `layout: full` are wrapped in `.wrap.doc`: text at the 68ch measure, tables at full width. Definition lists become a two-column label grid on wide screens. Tables scroll inside their wrapper (minimum width 34rem), never the page. Code blocks use the surface blue with a 1px border.

## Motion

- Hero: the depth contours draw outward, the soundings fade in one by one and the compass rose turns to north (about 2.4 s, ease-out-expo).
- Proof plot: when it first scrolls into view (`assets/plot.js`), the official curve draws, then TICON-4 and our fit slide out to their lag and the high-water marks appear. The plot shows its final state by default, so it is complete without JavaScript, before it is reached, and in screenshots.
- With `prefers-reduced-motion: reduce`, every animation and transition is off and the final state shows at once.

## Checks

`node build.mjs`, `node scripts/check-links.mjs`, `npx html-validate "dist/**/*.html"`, `node scripts/check-contrast.mjs`, and `scripts/screenshots.mjs` (375x667, 430x932 and 1440x900, day and night, fails on horizontal page scroll).
