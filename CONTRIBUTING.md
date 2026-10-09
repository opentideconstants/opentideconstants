# Contributing to OpenTideConstants

Contributions are welcome from anyone. You do not need to write code to help: a report about one station is useful. This page lists the ways to help and how to work on this repository.

## Ways to contribute

[Not included](https://opentideconstants.org/not-included/) lists what OTC leaves out and why; each item has a link that opens a request.

### Report a data problem

If a station has a wrong position, a wrong time base, or constants that do not match what you see, open an issue in the [issue tracker](https://github.com/opentideconstants/opentideconstants/issues). Include:

- the `station_id`, and the release you used (`OTC_{YYYYMMDD}`);
- the source of the constant set (for example `gesla` or `noaa`), and its `set_id` if you have it;
- your evidence: an official prediction, an observation or another dataset, with where it comes from and the times you compared.

Every automated decision in a release is recorded with its evidence. A confirmed issue reverses the decision in the next release.

### Propose a source

To propose an open source of tidal constants or gauge records, open an issue with:

- the **licence**, with a link to its text. It must allow redistribution of derived data, including commercial use;
- the **access** path: an API, files or an archive, and whether automated access is allowed;
- the **coverage**: how many stations, where, and how long the records are.

[Sources](https://opentideconstants.org/sources/) shows the same three things for every source in OTC, and lists the sources that were checked and not added.

### Improve the docs and the site

The site pages are in `src/pages/`. Fixes to wrong or unclear text are welcome, as an issue or as a pull request.

### Contribute code

This repository holds the site, its build and check scripts, the JSON Schema of the format, and the PEGELONLINE harvest in `pipeline/pegelonline/`. See [Working on this repository](#working-on-this-repository).

The SDKs (Python, Ruby, TypeScript and C) are in a separate repository, [opentideconstants/sdk](https://github.com/opentideconstants/sdk). Send SDK issues and pull requests there.

## Working on this repository

### Setup

You need Node 20 or later. The site has no npm dependencies. The schema check needs Python 3 with `jsonschema`; [uv](https://docs.astral.sh/uv/) installs it for one run.

### Build, check and preview

```sh
node build.mjs                                   # build src/ into dist/
node scripts/check-links.mjs                     # internal links and #fragments
node scripts/check-contrast.mjs                  # contrast of the site's colour pairs, day and night
npx --yes html-validate@11 "dist/**/*.html"      # HTML validation
uv run --no-project --with jsonschema python scripts/check_schema.py   # schema and example
python3 -m http.server 8000 --directory dist     # preview at http://localhost:8000
```

CI runs the same build and checks on every pull request. `node scripts/art/generate.mjs` regenerates the SVG artwork in `src/partials/`.

### Layout

```
build.mjs              the build: src/ -> dist/, with the shared layout, navigation, canonical URLs and sitemap
src/pages/             one HTML file per page; src/pages/<name>.html is published at /<name>/
src/layout.html        the shared page layout
src/assets/            CSS (tokens.css holds the theme), fonts and the small optional scripts
src/schema/            the JSON Schema of the format and its examples, served at /schema/
scripts/               link, contrast and schema checks, screenshots, and the artwork generators
pipeline/pegelonline/  the daily PEGELONLINE harvest (see its README)
.github/workflows/     the site build and deploy, and the daily harvest
```

[DESIGN.md](DESIGN.md) describes the theme. The site is served by GitHub Pages and deployed on every push to `main`.

### Schema changes

The format is defined by `src/schema/otc-1.0.schema.json` and `src/schema/otc-index-1.0.schema.json`. A schema change goes through a pull request and review. In the same pull request, update the examples in `src/schema/` and the [Data format](https://opentideconstants.org/data-format/) page (`src/pages/data-format.html`), so that the docs match the schema. `scripts/check_schema.py` checks the examples against the schema. The SDK conformance fixtures and the pipeline's copy of the schema pin it by commit and SHA-256, so after a schema change is merged, those pins move to the new commit.

## Pull requests

- Keep a pull request small and about one thing.
- For a change to data or to a rule that changes data, describe the evidence: what you compared, with which source, and the numbers.
- CI builds the site and runs the link, contrast, HTML and schema checks. They must pass before a pull request is merged.

## Licence of contributions

By contributing, you agree that your contribution is licensed under the same terms as this repository: code under the [MIT licence](LICENSE), and data, documentation and site text under CC BY 4.0, as [LICENSE-DATA.md](LICENSE-DATA.md) describes. Data keeps the per-provider attribution listed on [About](https://opentideconstants.org/about/#attribution).
