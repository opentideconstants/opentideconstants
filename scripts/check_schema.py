r"""Check the draft JSON Schema and the example document.

1. The current schema (0.4) and the older schemas are valid draft 2020-12 schemas.
2. src/schema/example.json validates. Its .meta.json form (no stations)
   validates against #/$defs/meta, and each station (one .jsonl line)
   validates against #/$defs/station.
3. Alias ids are unique within each alias system across the release. JSON
   Schema cannot express this, so this script checks it, as the release
   build will.
4. Every alias system accepts a real id from its source, and
5. negative controls must fail: for example a constant set without
   convention_id or quantity, a local convention without utc_offset_hours, a
   bad datestamp, a constituent name over 15 characters, duplicate aliases,
   a current set without current_bins (or with water-level constituents),
   current offsets without a reference bin, and a value with a trailing
   newline. Python's re lets $ match before a
   final newline; ECMA-262 does not. The schema ends every pattern with
   $(?![\s\S]) so both reject it.

Needs the jsonschema package (pip install jsonschema).
"""
import copy
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

root = Path(__file__).resolve().parent.parent
schema_dir = root / "src/schema"
schema = json.loads((schema_dir / "otc-0.4.schema.json").read_text())
example = json.loads((schema_dir / "example.json").read_text())

for old in sorted(schema_dir.glob("otc-*.schema.json")):
    Draft202012Validator.check_schema(json.loads(old.read_text()))
validator = Draft202012Validator(schema)


def sub_validator(name):
    return Draft202012Validator({"$schema": schema["$schema"], "$defs": schema["$defs"], "$ref": f"#/$defs/{name}"})


meta_validator = sub_validator("meta")
station_validator = sub_validator("station")


def duplicate_aliases(doc):
    """Return (system, id) pairs used by more than one station."""
    seen = {}
    dups = []
    for station in doc["stations"]:
        for system, ids in station.get("aliases", {}).items():
            for alias in ids if isinstance(ids, list) else [ids]:
                owner = seen.setdefault((system, alias), station["station_id"])
                if owner != station["station_id"]:
                    dups.append((system, alias))
    return dups


def problems(doc):
    found = [f"{list(e.path)} {e.message}" for e in validator.iter_errors(doc)]
    found += [f"duplicate alias {system}:{alias}" for system, alias in duplicate_aliases(doc)]
    return found


errors = problems(example)
if errors:
    for e in errors:
        print("example.json:", e)
    sys.exit(1)
print("ok: example.json is valid")

meta = {k: v for k, v in example.items() if k != "stations"}
if not meta_validator.is_valid(meta):
    print("FAIL: example.json without stations is not a valid .meta.json")
    sys.exit(1)
print("ok: example.json without stations is a valid .meta.json")
for station in example["stations"]:
    if not station_validator.is_valid(station):
        print(f"FAIL: {station['station_id']} is not a valid .jsonl line")
        sys.exit(1)
print("ok: each station is a valid .jsonl line")


# One real id per alias system, taken from each source's station list.
REAL_ALIASES = {
    "linz": "077NELSON",                                       # LINZ NZ_Tide_Constituents_2025.zip
    "shom": "3",                                               # SHOM tidegauges list: BREST
    "pegelonline": "aad49293-242a-43ad-a8b1-e91d7792c4b2",     # CUXHAVEN STEUBENHOEFT
    "rws": "hoekvanholland",                                   # RWS OphalenCatalogus
    "mi": "Ballycotton Harbour",                               # Marine Institute ERDDAP station_id
    "dmi": "20002",                                            # DMI oceanObs: Skagen Havn
    "smhi": "2545",                                            # SMHI ocobs: Arkö
    "fmi": "132310",                                           # FMI fmisid: Helsinki Kaivopuisto
    "uhslc": "001",                                            # UHSLC Fast Delivery h001.csv
}
doc = copy.deepcopy(example)
doc["stations"][0]["aliases"].update(REAL_ALIASES)
errors = problems(doc)
if errors:
    for e in errors:
        print("real aliases:", e)
    sys.exit(1)
print(f"ok: accepted real ids for {', '.join(REAL_ALIASES)}")


def must_fail(label, mutate):
    doc = copy.deepcopy(example)
    mutate(doc)
    if not problems(doc):
        print(f"FAIL: negative control passed validation: {label}")
        sys.exit(1)
    print(f"ok: rejected {label}")


def first_set(d):
    return d["stations"][0]["constant_sets"][0]


def second_station_same_aliases(d):
    twin = copy.deepcopy(d["stations"][0])
    twin["station_id"] = "OTC-EXAMPLE-9999"
    d["stations"].append(twin)


must_fail("constant set without convention_id",
          lambda d: first_set(d).pop("convention_id"))
must_fail("local convention without utc_offset_hours",
          lambda d: d["conventions"][1].pop("utc_offset_hours"))
must_fail("bad datestamp",
          lambda d: d["release"].__setitem__("datestamp", "2099-12-31"))
must_fail("phase of 360 degrees",
          lambda d: first_set(d)["constituents"][0].__setitem__("phase_deg", 360))
must_fail("constant set without quantity",
          lambda d: first_set(d).pop("quantity"))
must_fail("constant set without source_type",
          lambda d: first_set(d).pop("source_type"))
must_fail("active station without timezone",
          lambda d: d["stations"][0].pop("timezone"))
must_fail("release without the constituents table",
          lambda d: d.pop("constituents"))
must_fail("constituent name of 16 characters",
          lambda d: first_set(d)["constituents"][0].__setitem__("name", "M" * 16))
must_fail("dropped constituent name of 16 characters",
          lambda d: first_set(d)["dropped_constituents"][0].__setitem__("name", "S" * 16))
must_fail("constituent source_name of 32 characters",
          lambda d: first_set(d)["constituents"][0].__setitem__("source_name", "x" * 32))
must_fail("duplicate gesla alias in one station",
          lambda d: d["stations"][0]["aliases"].__setitem__("gesla", ["example-file-id", "example-file-id"]))
must_fail("two stations with the same aliases", second_station_same_aliases)
must_fail("datestamp with a trailing newline",
          lambda d: d["release"].__setitem__("datestamp", "20261008\n"))
must_fail("linz alias with a trailing newline",
          lambda d: d["stations"][0]["aliases"].__setitem__("linz", "077NELSON\n"))
must_fail("pegelonline shortname instead of uuid",
          lambda d: d["stations"][0]["aliases"].__setitem__("pegelonline", "CUXHAVEN STEUBENHOEFT"))
must_fail("unknown alias system",
          lambda d: d["stations"][0]["aliases"].__setitem__("bogus", "1"))


def station(d, station_id):
    return next(st for st in d["stations"] if st["station_id"] == station_id)


def current_set(d):
    return station(d, "OTC-EXAMPLE-0003")["constant_sets"][0]


def current_bin(d):
    return current_set(d)["current_bins"][0]


def current_offset(d):
    return station(d, "OTC-EXAMPLE-0004")["current_offsets"][0]


must_fail("current set without current_bins",
          lambda d: current_set(d).pop("current_bins"))
must_fail("current set with water-level constituents",
          lambda d: current_set(d)["constituents"].append(copy.deepcopy(first_set(d)["constituents"][0])))
must_fail("water-level set with current_bins",
          lambda d: first_set(d).__setitem__("current_bins", copy.deepcopy(current_set(d)["current_bins"])))
must_fail("current set with empty current_bins",
          lambda d: current_set(d).__setitem__("current_bins", []))
must_fail("current bin 0",
          lambda d: current_bin(d).__setitem__("bin", 0))
must_fail("current bin without depth_type",
          lambda d: current_bin(d).pop("depth_type"))
must_fail("current bin depth_type as NOAA letter",
          lambda d: current_bin(d).__setitem__("depth_type", "S"))
must_fail("azimuth of 360 degrees",
          lambda d: current_bin(d).__setitem__("azimuth_deg", 360))
must_fail("current constituent with amplitude_m instead of major_amplitude_ms",
          lambda d: current_bin(d)["constituents"][0].__setitem__("amplitude_m", 0.9))
must_fail("current constituent without minor_phase_deg",
          lambda d: current_bin(d)["constituents"][0].pop("minor_phase_deg"))
must_fail("current constituent major phase of 360 degrees",
          lambda d: current_bin(d)["constituents"][0].__setitem__("major_phase_deg", 360))
must_fail("current constituent name of 16 characters",
          lambda d: current_bin(d)["constituents"][0].__setitem__("name", "M" * 16))
must_fail("current constituent with NOAA majorMeanSpeed",
          lambda d: current_bin(d)["constituents"][0].__setitem__("majorMeanSpeed", 7.72))
must_fail("current offsets without reference_bin",
          lambda d: current_offset(d).pop("reference_bin"))
must_fail("current offsets with a NOAA reference id",
          lambda d: current_offset(d).__setitem__("reference_station_id", "ACT1616"))
must_fail("current offsets reference id with a trailing newline",
          lambda d: current_offset(d).__setitem__("reference_station_id", "OTC-EXAMPLE-0003\n"))
must_fail("default_current_bin 0",
          lambda d: station(d, "OTC-EXAMPLE-0003").__setitem__("default_current_bin", 0))
