# OpenTideConstants pipeline

The release producer, source adapters, GESLA fit tools and comparison harnesses
live here. Published OTC releases are the boundary for downstream applications.
The separate [PEGELONLINE harvester](pegelonline/README.md) remains unchanged.

## Local regression tests

Use Python 3.12, uv 0.10.7 and Ruby 3.2 or later. The two Python projects must
remain siblings: `scripts/otc` uses the local `scripts/gesla` package.

From `pipeline/scripts/otc`, run `uv sync --frozen`, then the same regression
command listed in `.github/workflows/producer.yml`. From `pipeline`, run
`bundle install`, then `bundle exec rspec spec`. The producer Ruby bundle contains
RSpec, Rubyzip, TCD and TZInfo. It does not load the WebCalTides application.

CI checks out Git LFS data before running data-dependent tests. The seven selected
metadata files use LFS; raw response fixtures remain byte-preserved plain Git.

## External proof profiles

Ordinary CI has no private release, engine, SDK or work-bucket inputs. Tests which
need these inputs report visible pending results when their entire profile is
absent. A configured missing or partially configured input is a failure. These
pending cases do not claim a new scientific comparison or release-build pass.

- `OTC_ENGINE_WORKTREE` selects the real external WebCalTides checkout for the
  GESLA TCD comparison. Its `Gemfile`, installed locked bundle,
  `lib/harmonics_engine.rb` and `data/latest-xtide.tcd` must exist. The engine runs
  in its own bundle; no application files or gems are copied into this pipeline.
- `OTC_REGISTRY_TICON` and `OTC_REGISTRY_TCD` select the original served TICON JSON
  and engine TCD for registry comparisons. Set both. Their existing SHA pins must
  match. The bucket-lock checks also need the accepted lock at
  `$OTC_WORK/inputs/gesla/4.1/inputs.lock.json`; existing work-bucket retrieval is
  unchanged. Committed registry count, identity ordering and small-pin checks run
  without this external profile.
- The native vector and Tier B profiles retain their existing named inputs and
  assertions. See [vectors/README.md](scripts/otc/vectors/README.md). Vector CLIs
  accept `--strict-source` for the exact external strict oracle. The comparison
  gates retain their explicit engine-worktree argument.

The native release command is `pipeline/bin/build-otc` from the repository root.
Full release builds require the already pinned external input and comparison
profiles. Reuse accepted fit artifacts; relocation does not authorize refits or
changed scientific bounds.

## Dependency audit

CI audits the standalone Ruby lock and every registry package in both Python
locks, including dev and platform-specific packages. Local editable projects are
audited through their own locks. The audit uses bundler-audit 0.9.3 and
pip-audit 2.10.0; it does not alter project locks.

The OTC Python audit has one explicit exception:
[GHSA-rgxp-2hwp-jwgg / CVE-2026-25087](https://github.com/advisories/GHSA-rgxp-2hwp-jwgg).
The advisory concerns Arrow C++ `RecordBatchFileReader::PreBufferMetadata` and
states that Python bindings do not expose this operation and are not vulnerable.
The exception applies only to the OTC Python audit containing PyArrow 21.0.0.
It does not exempt other packages, advisories or the GESLA lock.
