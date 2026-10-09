r"""Check the draft JSON Schema and the example document.

1. The current schema (0.6) and the older schemas are valid draft 2020-12 schemas.
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
   current offsets without a reference bin, a mean current written as a
   constituent Z0 or as a string, and a value with a trailing
   newline. 0.6 adds controls for the record annotations: a time base
   without a code or with an unknown one, corrected without from/to,
   no_constants with constants, and any qc_status excluded must fail;
   every annotation code, a broken record with a usable segment and a
   no-constants record must pass. Python's re lets $ match before a
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
schema = json.loads((schema_dir / "otc-0.6.schema.json").read_text())
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
must_fail("mean current as constituent Z0",
          lambda d: current_bin(d)["constituents"].append(
              {"name": "Z0", "speed_deg_per_hour": 0.0, "major_amplitude_ms": 0.0772, "major_phase_deg": 0.0,
               "minor_amplitude_ms": 0.0, "minor_phase_deg": 0.0}))
must_fail("mean_major_ms as a string",
          lambda d: current_bin(d).__setitem__("mean_major_ms", "0.0772"))
must_fail("mean current in cm/s under NOAA's name",
          lambda d: current_bin(d).__setitem__("majorMeanSpeed", 7.72))
must_fail("depth_type as NOAA letter B",
          lambda d: current_bin(d).__setitem__("depth_type", "B"))


def must_pass(label, mutate):
    doc = copy.deepcopy(example)
    mutate(doc)
    errors = problems(doc)
    if errors:
        print(f"FAIL: {label}: {errors[0]}")
        sys.exit(1)
    print(f"ok: accepted {label}")


must_pass("a negative mean current (BOS1101 bin 1: -2.06 / 0.21 cm/s)",
          lambda d: current_bin(d).update(mean_major_ms=-0.0206, mean_minor_ms=0.0021))
must_pass("a current bin with no mean current",
          lambda d: [current_bin(d).pop(k) for k in ("mean_major_ms", "mean_minor_ms")])
must_pass("depth_type below_chart_datum (NOAA B)",
          lambda d: current_bin(d).__setitem__("depth_type", "below_chart_datum"))
must_pass("depth_type above_bottom (kept from 0.4)",
          lambda d: current_offset(d).__setitem__("depth_type", "above_bottom"))


# --- 0.6: record annotations -------------------------------------------------------------------

def fjord(d, letter):
    return next(cs for cs in station(d, "OTC-EXAMPLE-0005")["constant_sets"] if cs["set_id"].endswith("-" + letter))


codes = {cs["time_base"]["code"] for st in example["stations"] for cs in st.get("constant_sets", []) if "time_base" in cs}
if codes != {"verified", "corrected", "unverified", "disputed"}:
    print(f"FAIL: example.json should show every time-base code, has {sorted(codes)}")
    sys.exit(1)
print("ok: example.json has a set with each time-base code (verified, corrected, unverified, disputed)")

# 0.5 cannot express an annotation: its constant_set rejects the 0.6 fields and values.
old = json.loads((schema_dir / "otc-0.5.schema.json").read_text())
old_set = Draft202012Validator({"$schema": old["$schema"], "$defs": old["$defs"], "$ref": "#/$defs/constant_set"})
for letter in "abcde":
    if old_set.is_valid(fjord(example, letter)):
        print(f"FAIL: 0.5 accepts the 0.6 set gesla-fit-{letter}")
        sys.exit(1)
print("ok: 0.5 rejects every annotated 0.6 set of OTC-EXAMPLE-0005")

must_fail("time base without a code", lambda d: fjord(d, "a")["time_base"].pop("code"))
must_fail("unknown time-base code", lambda d: fjord(d, "a")["time_base"].__setitem__("code", "probably"))
must_fail("corrected without from/to", lambda d: fjord(d, "a")["time_base"].pop("correction"))
must_fail("corrected with from but no to", lambda d: fjord(d, "a")["time_base"]["correction"].pop("to"))
must_fail("corrected without evidence", lambda d: fjord(d, "a")["time_base"].__setitem__("evidence", []))
must_fail("verified with a correction",
          lambda d: first_set(d)["time_base"].__setitem__("correction", {"from": "utc_instant", "to": "utc_plus_1"}))
must_fail("verified without evidence", lambda d: first_set(d)["time_base"].pop("evidence"))
must_fail("time base without a reason", lambda d: first_set(d)["time_base"].pop("reason"))
must_fail("time base without decided_in", lambda d: first_set(d)["time_base"].pop("decided_in"))
must_fail("time-base label that is not a class", lambda d: first_set(d)["time_base"].__setitem__("declared", "UTC"))
must_fail("evidence without comparator_id", lambda d: first_set(d)["time_base"]["evidence"][0].pop("comparator_id"))
must_fail("unknown comparator kind",
          lambda d: first_set(d)["time_base"]["evidence"][0].__setitem__("comparator_kind", "guess"))
must_fail("gauge set without a time base", lambda d: first_set(d).pop("time_base"))
must_fail("time base still only in provenance (0.5 style)",
          lambda d: first_set(d).__setitem__("provenance", {"time_base": first_set(d).pop("time_base")}))
for i, label in enumerate(("gesla-fit (ok)", "kartverket (ok)")):
    must_fail(f"qc_status excluded on {label}",
              lambda d, i=i: d["stations"][0]["constant_sets"][i].__setitem__("qc_status", "excluded"))
must_fail("qc_status excluded on a current set", lambda d: current_set(d).__setitem__("qc_status", "excluded"))
for letter in "bcde":
    must_fail(f"qc_status excluded on gesla-fit-{letter}", lambda d, l=letter: fjord(d, l).__setitem__("qc_status", "excluded"))
must_fail("no_constants carrying constants",
          lambda d: fjord(d, "d")["constituents"].append(copy.deepcopy(fjord(d, "a")["constituents"][0])))
must_fail("no_constants without no_constants_reason", lambda d: fjord(d, "d").pop("no_constants_reason"))
must_fail("no_constants without a flag", lambda d: fjord(d, "d").pop("qc_flags"))
must_fail("no_constants with expected_accuracy",
          lambda d: fjord(d, "d").__setitem__("expected_accuracy", copy.deepcopy(fjord(d, "c")["expected_accuracy"])))
must_fail("no_constants_reason on a set with constants", lambda d: fjord(d, "c").__setitem__("no_constants_reason", "too_short"))
must_fail("fit_unusable without record_issues",
          lambda d: [fjord(d, "e").pop("record_issues"), fjord(d, "e").__setitem__("qc_flags", fjord(d, "e")["qc_flags"][1:])])
must_fail("water-level set ok with no constants", lambda d: fjord(d, "a").__setitem__("constituents", []))
must_fail("ok with a flag", lambda d: fjord(d, "a").__setitem__("qc_flags", [{"flag": "microtidal", "reason": "x"}]))
must_fail("flagged without flags", lambda d: fjord(d, "c").pop("qc_flags"))
must_fail("unverified time base on an ok set",
          lambda d: [fjord(d, "b").__setitem__("qc_status", "ok"), fjord(d, "b").pop("qc_flags"),
                     fjord(d, "b").pop("record_issues"), fjord(d, "b").pop("fit_segment")])
must_fail("disputed time base without a time_base flag",
          lambda d: fjord(d, "c").__setitem__("qc_flags", fjord(d, "c")["qc_flags"][:1]))
must_fail("broken_record flag without record_issues",
          lambda d: [fjord(d, "b").pop("record_issues"), fjord(d, "b").pop("fit_segment")])
must_fail("record_issues without the broken_record flag", lambda d: fjord(d, "b").__setitem__("qc_flags", fjord(d, "b")["qc_flags"][1:]))
must_fail("fit_segment without record_issues",
          lambda d: fjord(d, "a").__setitem__("fit_segment", {"start": "2007-01-01T00:00:00Z", "end": "2010-01-01T00:00:00Z"}))
must_fail("unknown record issue", lambda d: fjord(d, "b")["record_issues"][0].__setitem__("issue", "gremlins"))
must_fail("short record without good_hours", lambda d: fjord(d, "c")["record_span"].pop("good_hours"))
must_fail("short record with constants but no expected_accuracy", lambda d: fjord(d, "c").pop("expected_accuracy"))
must_fail("expected_accuracy without uncertainty or error",
          lambda d: fjord(d, "c").__setitem__("expected_accuracy", {"cut_to_days": 39, "calibration_records": 25}))
must_fail("rayleigh drop without not_separable_from", lambda d: fjord(d, "c")["dropped_constituents"][0].pop("not_separable_from"))
must_fail("not_separable_from on a non-rayleigh drop",
          lambda d: first_set(d)["dropped_constituents"][0].__setitem__("not_separable_from", "SSA"))
must_fail("release.doi as a version DOI", lambda d: d["release"].__setitem__("doi", "10.5281/zenodo.1234567"))

must_pass("a corrected time base to local time with daylight saving",
          lambda d: [fjord(d, "a")["time_base"].__setitem__("published", "local_dst:Europe/Oslo"),
                     fjord(d, "a")["time_base"]["correction"].__setitem__("to", "local_dst:Europe/Oslo")])
must_pass("a corrected time base with a step and dropped months",
          lambda d: fjord(d, "a")["time_base"].update(
              published="step:2010-03-01", correction={"from": "utc_instant", "to": "step:2010-03-01"},
              months_dropped=["2011-02"]))
must_pass("an unverified time base that lists the weak checks tried",
          lambda d: fjord(d, "d")["time_base"].__setitem__(
              "evidence", [{"comparator_kind": "model", "comparator_id": "eot20", "distance_km": None}]))
must_pass("deprecated qc_status accepted and fallback (0.5 writers)",
          lambda d: [d["stations"][0]["constant_sets"][1].__setitem__("qc_status", "accepted"),
                     current_set(d).__setitem__("qc_status", "fallback")])
must_pass("deprecated qc_flags verdict and provenance.time_base alongside the 0.6 fields",
          lambda d: [fjord(d, "b")["qc_flags"][1].__setitem__("verdict", "unverified"),
                     fjord(d, "b")["provenance"].__setitem__("time_base", {"verdict": "utc_instant", "correction": "none"})])
must_pass("a concept DOI with a null release.doi",
          lambda d: d["release"].update(doi=None, concept_doi="10.5281/zenodo.1234566"))
must_pass("a release without release.doi", lambda d: d["release"].pop("doi"))
