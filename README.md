# OpenTideConstants

OpenTideConstants (OTC) is one open dataset of tidal harmonic constants. It combines 14 open sources into one format, with declared conventions, provenance for every record, and the accuracy of every station against official predictions. It is rebuilt automatically from the latest upstream data.

Site: [opentideconstants.org](https://opentideconstants.org)

## What is in the dataset

OTC takes three kinds of source. At one location, they rank in this order:

1. **Official constants**, published by the agency responsible for the gauge: NOAA CO-OPS (water levels and currents), Kartverket (Norway), LINZ (New Zealand) and JMA (Japan).
2. **Gauge fits**: constants that OTC fits from sea-level records: GESLA, PEGELONLINE (Germany), Rijkswaterstaat (Netherlands), Marine Institute (Ireland), DMI (Denmark), SMHI (Sweden), FMI (Finland) and SHOM REFMAR (France).
3. **Model constants** from the EOT20 global tide model, interpolated to station points. They are always labelled as model constants.

A station keeps every constant set from every source. One set is recommended by a published rule. Each station has a stable id and its aliases in other systems (NOAA, GESLA, TICON, XTide, Kartverket, Slackwater). The [Sources](https://opentideconstants.org/sources/) page gives the licence, access and coverage of each source.

The format is version 1.0. A release has one JSON document with a published [JSON Schema](https://opentideconstants.org/schema/otc-1.0.schema.json), the same data as JSON Lines (one station per line), and one long CSV table for spreadsheets.

Every constant set carries:

- its **convention**: phase reference, astronomical-argument model, nodal corrections and constituent table;
- its **provenance**: where the set came from and what the build did to it;
- its **QC status and flags**: `ok`, `flagged` or `no_constants`, with a reason for each flag;
- a **time-base annotation** for gauge fits: `verified`, `corrected`, `unverified` or `disputed`, with the evidence;
- **broken-record** and **short-record** details, where they apply;
- its **validation** scores against official predictions, with the previous release's scores;
- its **licence** and attribution text.

The fields are on [Data format](https://opentideconstants.org/data-format/).

## Getting the data

Releases are published at `https://data.opentideconstants.org/`. Each release has one stem, `OTC_{YYYYMMDD}`, and its files never change after publication.

- `OTC_latest.json` and `OTC_latest-f1.json` point to the current release (the second one to the newest release of format major 1).
- `OTC_index.json` lists every release, newest first.
- `OTC_{YYYYMMDD}.sha256` gives the SHA-256 checksum of every file in the release. Check a download with `sha256sum -c OTC_{YYYYMMDD}.sha256 --ignore-missing`.

The [Downloads](https://opentideconstants.org/downloads/) page lists every published release, with its changes, files and checksums. It reads the list from `OTC_index.json`.

Zenodo archives one version a month and one for each material change, all under one concept DOI. Use the concept DOI to cite the data (see [Licence and citation](#licence-and-citation)).

## SDKs

Client libraries for Python, Ruby, TypeScript and C are in [opentideconstants/sdk](https://github.com/opentideconstants/sdk). They read a release, check its SHA-256 checksums, cache it, and give typed access to stations, constant sets, constituents, conventions, provenance, validation and licences. All four pass one shared conformance suite.

The SDKs are not published to the package registries. The `opentideconstants` packages on [PyPI](https://pypi.org/project/opentideconstants/), [RubyGems](https://rubygems.org/gems/opentideconstants) and [npm](https://www.npmjs.com/package/opentideconstants) are `0.0.0` placeholders that reserve the name and contain no SDK code. The SDKs read release format 0.x; reading format 1.0 is a condition for publishing them. The [SDK repository](https://github.com/opentideconstants/sdk) shows the current status.

| Language | Package | Install command | Requires |
|---|---|---|---|
| Python | `opentideconstants` | `pip install opentideconstants` | Python 3.10 or later |
| Ruby | `opentideconstants` | `gem install opentideconstants` | Ruby 3.0 or later |
| TypeScript / JavaScript | `opentideconstants` | `npm install opentideconstants` | Node 22.12 or later |
| C | `libopentideconstants` | build `c/` in the SDK repository with CMake, or copy in the single-file amalgamation (`opentideconstants.c` and `.h`) | a C99 compiler |

Each snippet below opens the latest release, finds the reference stations within 25 km of a point, prints the M2 constituent of each station's recommended constant set, and prints the attribution text for those stations. The snippets were run against the SDK's conformance fixtures.

The SDKs read a release and predict tides and currents from it. [Tide Mechanics](https://opentideconstants.org/tide-mechanics/) shows how the constants turn into a prediction.

**Python**

```python
from opentideconstants import OpenTideConstants

with OpenTideConstants() as otc:                  # the latest release, cached on disk
    hits = otc.near(lat=37.8, lon=-122.4, radius_km=25, limit=3, type="reference")
    for hit in hits:
        m2 = hit.station.recommended_set.constituent("M2")
        print(hit.station.name, round(hit.distance_km, 1), "km", m2.amplitude_m, "m", m2.phase_deg, "deg")
    print(otc.attribution([hit.station for hit in hits]))   # CC BY 4.0 credit for what you show
```

**Ruby**

```ruby
require "opentideconstants"

otc  = OpenTideConstants.new                      # the latest release, cached on disk
hits = otc.near(lat: 37.8, lon: -122.4, radius_km: 25, limit: 3, type: "reference")
hits.each do |hit|
    m2 = hit.station.recommended_set.constituent("M2")
    puts "#{hit.station.name} #{hit.distance_km.round(1)} km: M2 #{m2.amplitude_m} m, #{m2.phase_deg}°"
end
puts otc.attribution(hits.map(&:station))         # CC BY 4.0 credit for what you show
```

**TypeScript**

```ts
import { OpenTideConstants } from "opentideconstants";

const otc = await OpenTideConstants.open();       // the latest release, cached on disk
const hits = otc.near({ lat: 37.8, lon: -122.4, radiusKm: 25, limit: 3, type: "reference" });
for (const hit of hits) {
    const m2 = hit.station.recommendedSet?.constituent("M2");
    console.log(`${hit.station.name} ${hit.distance_km.toFixed(1)} km: M2 ${m2?.amplitude_m} m, ${m2?.phase_deg}°`);
}
console.log(otc.attribution(hits.map((hit) => hit.station)));   // CC BY 4.0 credit for what you show
otc.close();
```

**C**

The C library opens a release file that the application provides. An optional module, built with libcurl, downloads it.

```c
#include <stdio.h>
#include <opentideconstants.h>

int main(void)
{
    otc_open_options opts;
    otc_open_options_init(&opts);
    otc_release *rel = NULL;
    if (otc_open_file("OTC_20261008.jsonl", &opts, &rel) != OTC_OK)   /* reads the .meta.json and checks the .sha256 next to it */
        return 1;

    otc_filter filter;
    otc_filter_init(&filter);
    filter.type = "reference";
    otc_nearby hits[3];
    size_t count = 0;
    otc_near(rel, 37.8, -122.4, 25.0, &filter, 3, hits, 3, &count);

    for (size_t i = 0; i < count; i++) {
        otc_station_t *st = NULL;
        const otc_set *set = NULL;
        const otc_constituent_t *m2 = NULL;
        char name[128];
        if (otc_station(rel, hits[i].id, &st) != OTC_OK)
            continue;
        otc_station_name(st, name, sizeof name, NULL);
        if (otc_station_recommended_set(st, &set) == OTC_OK && otc_set_constituent(set, "M2", &m2) == OTC_OK)
            printf("%s %.1f km: M2 %g m, %g deg\n", name, hits[i].distance_km, m2->amplitude_m, m2->phase_deg);
        otc_station_free(st);
    }
    otc_close(rel);
    return 0;
}
```

## Licence and citation

The data that OTC derives, and the documentation and site text, are under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). The code in this repository (the site build, the scripts, `pipeline/` and the schema files) is under the [MIT licence](LICENSE). [LICENSE-DATA.md](LICENSE-DATA.md) gives the details. The SDK code is also MIT.

OTC publishes derived constants only and never redistributes the raw sea-level series. Constants from other providers keep their own licence (for example, NOAA data is in the public domain). Every constant set carries its own licence id and attribution text. [Licence and attribution](https://opentideconstants.org/about/#licence) on About gives the details.

When you use OTC data, credit OpenTideConstants and the upstream providers of the stations you use. The [attribution list on About](https://opentideconstants.org/about/#attribution) gives the citation text each provider requires.

To cite a release, give the concept DOI (the one DOI for all versions) and the release datestamp, `OTC_{YYYYMMDD}`. If the release has its own Zenodo version, add its version DOI. The concept DOI is in the release files (`release.concept_doi`), in `OTC_index.json` and in the latest pointers. [CITATION.cff](CITATION.cff) gives the citation for GitHub and reference managers, and [How to cite](https://opentideconstants.org/about/#cite) gives the full form.

## Accuracy and method

Every release publishes, for every station with an official reference, how well its constants reproduce official predictions: time and height errors, missed and extra events, and the same numbers for the previous release. Every input record is checked for a wrong or undeclared clock and for broken data before a fit, and a clock is corrected only on positive evidence. At 11 validation stations, OTC's fits matched or beat TICON-4 at 10. At Helgoland, the mean time error went from 88–90 min (TICON-4) to 6.7 min.

- [Why](https://opentideconstants.org/why/): the problems in today's open tidal constants that this dataset addresses.
- [Method](https://opentideconstants.org/method/): how a release is built, from source records to published files.
- [Validation](https://opentideconstants.org/validation/): how accuracy is measured, with results at 11 stations.
- [Tide Mechanics](https://opentideconstants.org/tide-mechanics/): how harmonic constants turn into a tide prediction.

## Contributing and issues

Contributions are welcome from anyone. [CONTRIBUTING.md](CONTRIBUTING.md) lists the ways to help.

[Not included](https://opentideconstants.org/not-included/) lists what OTC and its SDKs leave out, and why. To ask for one of them, use the [request form](https://github.com/opentideconstants/opentideconstants/issues/new?template=request.yml).

Report a problem with a station, the data or the site in the [issue tracker](https://github.com/opentideconstants/opentideconstants/issues). For a station, include its `station_id`, the release date, and what you compared it with. Issues about the SDKs go to the [SDK issue tracker](https://github.com/opentideconstants/sdk/issues).

## Working on this repository

This repository holds the source of the opentideconstants.org site (plain HTML and a small Node build script with no dependencies) and the PEGELONLINE harvest pipeline. [Working on this repository](CONTRIBUTING.md#working-on-this-repository) in CONTRIBUTING.md gives the setup, the build and check commands, and the layout.
