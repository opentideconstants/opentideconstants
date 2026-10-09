# PEGELONLINE harvest

A daily archive of water-level measurements from tidal coastal and estuary gauges in
[PEGELONLINE](https://www.pegelonline.wsv.de/), the gauge service of the German Federal
Waterways and Shipping Administration (WSV).

PEGELONLINE serves only about the last 31 days of measurements. This harvester keeps them,
so that OpenTideConstants can fit tidal harmonic constants to longer records. The
harvester lives here, and its archive is in the R2 work bucket under `archive/pegelonline/`.

## Layout

The data is not in git. It is in the private OpenTideConstants work bucket (Cloudflare R2,
`s3://$OTC_WORK_BUCKET/archive/pegelonline/`):

| Path in the bucket | Contents |
| --- | --- |
| `YYYY/MM/DD/<station-uuid>.csv.gz` | One file per station per UTC day. Columns: `timestamp_utc,value_cm` |
| `YYYY/MM/DD/MANIFEST.json` | sha256, size, source URL and fetch time of each file of the day |
| `stations/YYYY-MM-DD.json` | Daily snapshot of the selected stations (name, water, km, position, unit, gauge zero) |
| `stations/MANIFEST.json` | sha256, size and fetch time of each snapshot |

In this repository:

| Path | Contents |
| --- | --- |
| `pipeline/pegelonline/harvest.py` | The harvester (Python standard library only) |
| `pipeline/pegelonline/r2sync.py` | Pull from and push to the bucket (uses the `aws` CLI) |
| `.github/workflows/pegelonline-harvest.yml` | Daily run at 04:23 UTC, and manual runs (concurrency group `otc-harvest`) |

The workflow uses a work directory under `$RUNNER_TEMP`, outside the checkout. A local run
needs a `--root` directory; `.gitignore` here keeps data out of git if you use this folder.
Changes in this folder do not start the site build (`pages.yml` ignores them).

- `timestamp_utc`: the PEGELONLINE timestamp (local time with offset) converted to UTC,
  ISO 8601 with `Z`. The raw series is mostly at 1-minute intervals.
- `value_cm`: the `W` time series (WASSERSTAND ROHDATEN), in cm above the gauge zero
  (Pegelnullpunkt). The gauge zero and its datum are in the station snapshot
  `stations/YYYY-MM-DD.json` (`W.gaugeZero`). These are unchecked raw data.
- Station identity: use the UUID. PEGELONLINE says that names and numbers can change.

## How a run works

1. Pull from the bucket every UTC day that the run can touch, and check each file
   against the day's `MANIFEST.json`. The run stops on any mismatch.
2. Get the station list (`/stations.json?includeTimeseries=true`) and select the tidal
   stations (rules below).
3. For each station, get the last 2 days of `W` (`/stations/{uuid}/W/measurements.json?start=P2D`).
4. Merge into the pulled per-day files. Rows are keyed by timestamp. A new value for an
   existing timestamp replaces the old one (PEGELONLINE revises raw data). The files are
   sorted and written with a fixed gzip header, so a re-run with the same data changes nothing.
5. Push only the files that changed, then the manifests of their days, and the station
   snapshot if it changed. Nothing is committed to git.

The run fails if more than 10% of the stations fail. It pushes the data of the other
stations first.

To backfill, start the workflow manually with `period` set up to `P31D`.

## Station selection

A station is selected if it has a `W` time series and:

- its water is `NORDSEE`, `OSTSEE`, `JADE`, `TRAVE`, `KLEINES HAFF`, `WARNOW` or `PEENE`; or
- its water is a tidal tributary: `HUNTE`, `LESUM`, `WÜMME`, `LEDA`, `OSTE`, `STÖR`,
  `PINNAU`, `KRÜCKAU`, `LÜHE`, `ESTE`, `SCHWINGE`, `EIDER`, `TREENE`, `ILMENAU`,
  `BÜTZFLETHER SÜDERELBE`, `WISCHHAFENER SÜDERELBE`, `FREIBURGER HAFENPRIEL`, `RUTHENSTROM`; or
- `ELBE` at km 585.9 or more (below the Geesthacht weir); or
- `WESER` from the Bremen or Bremerhaven offices at km 120 or less (Unterweser and
  Aussenweser), and `WESERWEHR UW`; or
- `EMS` from the Emden office (below the Herbrum weir).

The selection is wide on purpose: lost days cannot be fetched again. Downstream code
decides which stations are tidal enough to fit.

## Licence

- **Code:** MIT (Copyright (c) 2026 OpenTideConstants contributors).
- **Data:** the data comes from PEGELONLINE (WSV) under the
  [Data licence Germany – Zero – Version 2.0](https://www.govdata.de/dl-de/zero-2-0)
  (`dl-de/zero-2-0`). The PEGELONLINE web-service page says:

  > "Die Wasserstraßen- und Schifffahrtsverwaltung des Bundes (WSV) stellt ungeprüfte
  > Rohdaten bereit. Daher wird keine Haftung oder Gewährleistung für die inhaltliche
  > Richtigkeit, Genauigkeit, Aktualität, Zuverlässigkeit oder Vollständigkeit der Daten
  > übernommen. Die Daten sind unter der Lizenz DL-DE->Zero-2.0 frei verfügbar."

  (WSV supplies unchecked raw data with no warranty of correctness, accuracy,
  timeliness, reliability or completeness. The data is freely available under
  DL-DE->Zero-2.0.) Source: <https://www.pegelonline.wsv.de/webservice/guideRestapi>.
  Attribution is not required by the licence, but we name the source: "Data:
  Wasserstraßen- und Schifffahrtsverwaltung des Bundes (WSV), PEGELONLINE".
