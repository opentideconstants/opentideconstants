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

## XTide and OpenCPN data files

From `pipeline`, install the locked Ruby bundle and export a qualified native
release with its matching metadata file:

```text
bundle exec ruby scripts/otc/export_tcd.rb --input /data/OTC_20261011.jsonl --release /data/OTC_20261011.meta.json --output-dir /data/tcd --date 20261011
```

The command writes `OTC_20261011.tcd` with each native recommended set, and
`OTC_20261011-complete.tcd` with every representable set and subordinate tide
path. A station with no recommended set uses its existing subordinate tide
offsets when the reference can be exported. Recommendations, input records,
licences, astronomical tables and datum values are not changed. These files
contain data only; no npm compatibility package is involved.

Use the recommended file as XTide's harmonics input (`HFILE_PATH`), or add it in
OpenCPN's tide/current data-file settings. Use the complete file instead when
you want the alternatives. Loading both introduces duplicate recommended
records. Station names include their OTC identity; alternatives have
`[alt:<source>:<ordinal>]`, and fallbacks have
`[fallback:<reason>:<ordinal>]`. Ordinals follow sorted native set IDs.
Recommended fallback records retain their fallback label. Notes include the
full name, set identity, source, real licence attribution, provenance and
quality caveats. Subordinate alternatives use `[alt:subordinate:1]`.

TCD limits names to 89 ISO-8859-1 bytes. The display-name portion is shortened
to preserve identity and labels. Characters outside that encoding use explicit
Unicode escapes. Full original names remain in notes and native input.
Amplitudes round to 0.0001 meters, epochs to 0.01 degree and positions to
0.00001 degree. The native release retains its original precision. Native
phases are already Greenwich, including JMA, LINZ and Kartverket; the exporter
does not apply the publishers' time offsets again. Native datum offsets are
retained; datum-less model constants are explicitly mean-level-relative, with
no claim to an absolute chart datum.

`OTC_20261011-tcd-report.json` binds input/output hashes, records the astronomy
and writer version, and accounts for all native station identities and selected
records. It includes full set metadata and identifies exported record indices
or precise conversion errors. Native two-dimensional multi-bin current
ellipses and their subordinate current paths cannot be represented losslessly
by TCD's scalar current records; they are listed as unrepresentable, not
converted to invented tide/scalar data. Identity-only stations have no
prediction record. Missing references, fields or oversized required notes are
reported by identity. Usable tide records still export.

Exit status is `0` for an export without conversion errors, `2` for usable
partial output plus per-record errors, and `1` for a structural/input/write
failure. `-complete` denotes the all-alternatives selection mode, not a claim
that unrepresentable data became lossless TCD records. Always read the report
before distributing either file. File generation alone does not establish
consumer prediction accuracy or superiority to other databases.

Run the real export/readback profile with the same qualified inputs:

```text
OTC_TCD_INPUT=/data/OTC_20261011.jsonl OTC_TCD_RELEASE=/data/OTC_20261011.meta.json bundle exec rspec spec/otc/export_tcd_spec.rb
```

With neither variable configured, the external-data tests are visibly pending;
a partial or missing configured profile fails. The profile checks all exported
coefficients, subordinate references, complete-set accounting, and three named
real station cells. Normal XTide/libtcd and OpenCPN acceptance must also be
recorded against the actual generated files before claiming compatibility.

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
