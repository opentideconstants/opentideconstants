# Row 31 source-aware predicate amendment — 2026-10-23

The owner approved this amendment on 2026-10-23 at 4:42 PM PDT and reaffirmed it at 11:01 PM PDT.

Keep the original 6,353 matched-place identities and named datum definitions. Exclude only the 2,703 survey/geodetic requirement cells that the frozen comparator classification proves came exclusively from a selected reference station, rather than the matched place's own source. These cells affect 1,752 leaders. This is an origin correction for individual requirements, not a global exclusion of STND, NAVD88 or any other named key. X31's undefined-key exclusion class remains empty.

Own published requirements remain required. In particular, Gold Beach NOAA 9431011 still requires its own STND and NAVD88. A source label, model zero or reference-station value never becomes a surveyed local tie. No source data, SDK, schema, model/default selection or datum value changes in this amendment.

| Measurement | Complete places | Incomplete places | Missing cells | Availability verdict |
| --- | ---: | ---: | ---: | --- |
| Original strict frozen 079ff253 data | 4,600 | 1,753 | 2,705 | LOSS |
| Source-aware predicate on the same 079ff253 data | 6,352 | 1 | 2 | LOSS |
| Strict current ba2c7d39 data | 4,602 | 1,751 | 2,701 | LOSS |
| Source-aware predicate on current ba2c7d39 data | 6,353 | 0 | 0 | TIE |

The strict historical LOSS and original predicate remain separately recorded. The current strict gain includes Gold Beach's two own published fields and the mechanical matched-place overlap with TWC0817. The overlap audit states `same_gauge=false`; no physical alignment is inferred.

**Full Row31 remains LOSS.** Equal named-key availability is a measured subgate. It is not proof that a retained candidate can be selected and consumed by predictions, that the default chart path works, or that every applicable reader returns the required method and uncertainty. The approved parity plan's Row31/X31 requirements and later data-file swapping amendment still apply. No new runtime gate or SDK behavior is introduced here.

## Evidence and reproduction

The bundled [requirements](row31-source-aware-20261023.json) retain every excluded `(leader, key)` pair and actual before-change control rows. The [measurement](row31-source-aware-measurement-20261023.json) binds the closed native artifact, full 6,353-place key map, original-preservation receipt and actual positive controls by SHA-256. Raw source records remain in their existing artifacts.

Classification authority: `6ae3fd4cab75ef357ed8529340c537a10670d88a7c62152b3b7105cc7eceba31`. Full current map and literal real RED/GREEN logs are retained under the existing local `datum-row31-full-run/row31-source-aware-amendment/` evidence directory.

Run `ruby scripts/test_row31_score.rb` for the focused contract and real control fixtures. Run `ruby scripts/score_row31.rb --map /absolute/path/to/final-key-map.json --output /absolute/path/to/result.json` to remeasure the complete frozen cohort. The scorer rejects changed place/source identities, changed original requirements, non-physical exclusions, duplicates and a map whose stored missing keys disagree with its actual key union.
