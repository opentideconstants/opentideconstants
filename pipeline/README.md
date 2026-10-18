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
ellipses cannot be represented losslessly by TCD's scalar current records.
Current records export the stored signed major-axis harmonics and native
subordinate offsets, with the limitations described below. Identity-only stations have no
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

### Slackwater station JSON and TCDB file swaps

Run the exporter from `pipeline` with Ruby 3.2 or later and the existing bundle:

```text
bundle exec ruby scripts/otc/export_slackwater.rb --input /absolute/OTC_20261011.jsonl --release /absolute/OTC_20261011.meta.json --output-dir /absolute/output --date 20261011 --station-schema /absolute/station.schema.json --database-schema /absolute/database.fbs --country-codes /absolute/country-codes.csv --flatc /absolute/flatc
```

Supply the external `schemas/station.schema.json` and `schemas/database.fbs` from
`openwatersio/slackwater-database` revision `e046e800c40b0033b675d7f985ecfc62534d1b16`.
Their SHA256 hashes must be `cf24a8947fd1d159741aef1de74a0281a0a281d6c05ccfb40cf20db42fcac9f4`
and `5e0cc616baf47aa8bf8ce2baa679a81881aff7e2ed90027487009145a19496ea`, respectively.
Use standard FlatBuffers `flatc` version **25.9.23**. These reference files and
consumer implementations are not included in OTC or its exported files.

The country CSV uses `datasets/country-codes`, revision
`2d84034a0cae19ea5444b78c2131304a5c7f4bb9`, file `data/country-codes.csv`
(SHA256 `bb199ebc9636b9872705e8216f1f849359f136a7668ebfa23a3c58048462ab92`).
Its ISO3, ISO2, English country name and continent columns provide the required
format fields. The exporter records the supplied CSV hash and preserves the
native country code. It does not resolve missing countries from coordinates.

Outputs are `OTC_<date>-stations/`, `OTC_<date>.tcdb`, and
`OTC_<date>-swap-report.json`. Each reference station uses its native
`recommended_set_id`; the exporter does not rank or combine sets. Alternatives
are listed as omitted in the report. Subordinate tides retain their native
reference and offsets only when that reference is exported. Tide
subordinates without a local chart/MSL tie can use their selected reference's
published chart coordinate. This is a labelled reference-relative approximation;
the subordinate's local vertical tie remains unknown. The exporter copies the
reference's existing datum values and chart key, records its exact station/set
identity, and retains the original local datum metadata separately in `source.otc`.
The visible disclaimer and TCDB attribution describe this limitation. Fully tied
local subordinates keep their existing local frame. Missing or untied references
remain explicit omissions. Reference harmonics without a usable native chart/MSL tie use an empty
`chart_datum` and an export-only `datum_fallback` label in source metadata and
the report. Predictions then remain relative to the harmonic mean. No absolute
or chart-datum height is established. Missing datum objects produce empty
`datums`; missing chart keys or values retain all available native named values
and the existing MSL offset mapping. No numeric datum or chart tie is invented.
The complete native datum path takes precedence; native selection and quality
remain unchanged. Ratio offsets multiply heights in the reference chart frame.
For JSON prediction, resolve `station.offsets.reference` from the same export and
pass its `harmonic_constituents` with the subordinate's other fields to `useStation`:

```javascript
const reference = stations.find(item => item.id === station.offsets.reference);
const predictor = useStation({...station, harmonic_constituents: reference.harmonic_constituents});
const events = predictor.getExtremesPrediction({start, end});
```

The TCDB reader resolves that reference automatically. These extrema preserve
native ratios, additions and time offsets; they do not certify a local chart datum.
No chart datum or scientific value is derived. Native datum basis,
fallback findings, provenance and licence notices accompany exported records.
The report includes the native selected records and licence registry.

Exit **0** means all records and sets were represented; **2** means partial
output, including omitted alternatives or records without predictions; **1**
means an input/tool error. Read the report before using partial output. A rerun
replaces this release's station directory to remove stale omitted records.
TCDB stores amplitudes, phases and datums as float32; JSON retains native
precision. Standard flatc does not promise the reference builder's preferred
physical layout or scan performance. No Slackwater quality score is inferred.

Compatibility proof uses unchanged engine revision `97ef33e30572` for JSON
predictions and unchanged Swift reader revision `e046e800c40b0033b675d7f985ecfc62534d1b16`
for TCDB decoding. Swift-decoded values are then predicted by that same engine;
the Swift reader itself has no prediction API. Three tested station examples
do not establish prediction accuracy or acceptance for every exported station.
Run `spec/otc/export_slackwater_spec.rb` with `OTC_SWAP_INPUT`, `OTC_SWAP_RELEASE`,
`OTC_SWAP_DATABASE_SCHEMA`, `OTC_SWAP_STATION_SCHEMA`, `OTC_SWAP_COUNTRY_CODES`
and `OTC_SWAP_FLATC` set to the real release and external dependencies.

### Scalar current contract (all three formats)

Current records predict **signed major-axis velocity**, in knots. Positive means
flood and negative means ebb. These records do not predict the full vector speed
or direction: minor-axis motion, the ellipse and other depths remain in the
native release and export reports. Each record carries a `signed_major_axis`
notice. Read the native data for a different bin or vector prediction.

The recommended output selects the native recommended set and
`default_current_bin`. A subordinate uses its exact `reference_bin`; a required
non-default reference bin has the stable swap identity `<station_id>_bin_<bin>`.
The native station ID identifies its default bin. TCD complete output includes
all supported sets, bins and subordinate paths. Reports count current places
separately from additional bin records. Additional records are not new places.

Exporters divide stored m/s values by `0.5144444444444445` to obtain knots.
They use the native SDK flood convention: when the cosine of axis azimuth minus
flood bearing is negative, each major phase gains 180 degrees modulo 360 and
the stored mean changes sign. Amplitudes stay positive. No ellipse is fitted.
Missing axis, mean or bearings produce a reported omission. Flood and ebb
bearings retain their actual, possibly non-antipodal values.

JSON currents follow the pinned engine's published `CurrentStation` type and
`useCurrentStation`, not its tide-only station JSON schema. TCDB uses
`Kind.Current` and `Current`/`CurrentOffsets`, including missing-value flags.
Missing subordinate values remain absent. The unchanged current engine supplies
its documented defaults for absent offsets. Native event coverage remains the
authority for which subordinate events have source offsets.

TCD current references use `knots`, `degrees true` and an Unknown vertical datum.
Their datum-offset slot contains the actual signed current mean, not a water
height. Subordinates preserve flood/ebb ratios, minute offsets and available
slack offsets through the writer API. Zero additive terms are mathematical
identities. A missing slack offset uses the writer's NULL sentinel. TCD rounds
current amplitudes and means to 0.0001 knot, and phases to 0.01 degree.

For JSON or a station decoded by the unchanged Swift `StationDatabase` reader,
call the existing current engine directly. Resolve subordinate references by ID:

```ts
const predictor = useCurrentStation(station, { reference });
const options = {
    start: new Date("2026-10-11T00:00:00Z"),
    end: new Date("2026-10-13T00:00:00Z"),
};
const events = predictor.getEventsPrediction(options);
const timeline = predictor.getTimelinePrediction({ ...options, timeFidelity: 600 });
```

`timeFidelity` is seconds. The native SDK exposes subordinate events; the swap
engine's interpolated subordinate timeline is not a native vector curve.
TCD files load through XTide's ordinary `HFILE_PATH` and location selection.
Cross-engine numeric differences must be measured; format compatibility alone
does not establish scientific equivalence or navigation suitability.
