r"""Check the JSON Schema and the example document.

1. The schema (1.0) and the index schema are valid draft 2020-12 schemas.
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
   a release (or .meta.json) with no astro_tables while a convention uses
   f_u_at_prediction, an empty astro_tables, an unknown key in a table or
   a row, a table without (or with a malformed) tables_sha256 or with a key
   that is not a constituent name, a row without f, a v0u_deg outside
   [0, 360), a negative f, a convention without (or with an empty)
   astro_table_id, a datum.named key chart, mean_level or zero, a
   datum.zero outside its enum, an empty datum.chart_datum,
   a licence without commercial_use (or with a string), a non-commercial
   licence without restriction or terms_url, an empty or non-string
   restriction, a terms_url that is not a string or not a URI,
   a record_span.start that is a date and not a date-time,
   a current set without current_bins (or with water-level constituents),
   current offsets without a reference bin, a mean current written as a
   constituent Z0 or as a string, and a value with a trailing
   newline. Controls for the record annotations: a time base without a
   code or with an unknown one, corrected without from/to, no_constants
   with constants, and any qc_status excluded must fail; every annotation
   code, a broken record with a usable segment and a no-constants record
   must pass. Retired values and fields
   (qc_status accepted and fallback, qc_flags verdict, provenance.time_base,
   provenance.build_commit, release.doi, depth_type above_bottom) must fail. Python's re lets $ match before a
   final newline; ECMA-262 does not. The schema ends every pattern with
   $(?![\s\S]) so both reject it.
6. Datums: every named level has a basis (published, computed or observed)
   whose fields match its kind and method. The must-pass datum blocks are
   also in example.json, and the script checks that they are the same.
   Datum checks that only the release build can make (checks 9-14 of the
   schema's description) are left to it on purpose. Each must-fail
   datum control names the rule that rejects it, and the script checks
   that only that rule does, so no control is masked by another rule.

Needs the jsonschema package with its format checkers
(pip install 'jsonschema[format-nongpl]'), so that "format": "uri" and
"date-time" are enforced. Without them jsonschema skips those formats, so
this script stops if they are missing.
"""
import copy
import json
import re
import sys
from pathlib import Path

from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match

root = Path(__file__).resolve().parent.parent
schema_dir = root / "src/schema"
schema = json.loads((schema_dir / "otc-1.0.schema.json").read_text())
example = json.loads((schema_dir / "example.json").read_text())

for path in sorted(schema_dir.glob("otc-*.schema.json")):
    Draft202012Validator.check_schema(json.loads(path.read_text()))
FORMATS = Draft202012Validator.FORMAT_CHECKER
missing = {"uri", "date-time"} - set(FORMATS.checkers)
if missing:
    print(f"FAIL: jsonschema cannot check the formats {sorted(missing)}; pip install 'jsonschema[format-nongpl]'")
    sys.exit(1)
validator = Draft202012Validator(schema, format_checker=FORMATS)


def sub_validator(name):
    return Draft202012Validator({"$schema": schema["$schema"], "$defs": schema["$defs"], "$ref": f"#/$defs/{name}"},
                                format_checker=FORMATS)


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
must_fail("depth_type above_bottom (a 0.4 draft value)",
          lambda d: current_offset(d).__setitem__("depth_type", "above_bottom"))


# --- record annotations ------------------------------------------------------------------------

def fjord(d, letter):
    return next(cs for cs in station(d, "OTC-EXAMPLE-0005")["constant_sets"] if cs["set_id"].endswith("-" + letter))


codes = {cs["time_base"]["code"] for st in example["stations"] for cs in st.get("constant_sets", []) if "time_base" in cs}
if codes != {"verified", "corrected", "unverified", "disputed"}:
    print(f"FAIL: example.json should show every time-base code, has {sorted(codes)}")
    sys.exit(1)
print("ok: example.json has a set with each time-base code (verified, corrected, unverified, disputed)")

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
must_fail("time base in provenance.time_base instead of the set (0.5 draft style)",
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
must_fail("qc_status accepted (a 0.x draft value)",
          lambda d: d["stations"][0]["constant_sets"][1].__setitem__("qc_status", "accepted"))
must_fail("qc_status fallback on a carried-over set (a 0.x draft value)",
          lambda d: [current_set(d).__setitem__("qc_status", "fallback"),
                     current_set(d)["provenance"].__setitem__("carried_from_release", "20991130"),
                     current_set(d).__setitem__("qc_flags", [{"flag": "carried_over", "reason": "NOAA failed its check"}])])
must_fail("qc_flags verdict next to reason (a 0.x draft field)",
          lambda d: fjord(d, "b")["qc_flags"][1].__setitem__("verdict", "unverified"))
must_fail("provenance.time_base on an official set (a 0.x draft field)",
          lambda d: d["stations"][0]["constant_sets"][1]["provenance"].__setitem__(
              "time_base", {"verdict": "utc_instant", "correction": "none"}))
must_fail("release.doi null (a 0.x draft field)", lambda d: d["release"].__setitem__("doi", None))
must_pass("a concept DOI", lambda d: d["release"].__setitem__("concept_doi", "10.5281/zenodo.1234566"))


# --- holes found in the format gate review (S1-S7, D1, D9, C1, C3) -------------------------------

def no_flag(cs, name):
    cs["qc_flags"] = [f for f in cs["qc_flags"] if f["flag"] != name]


def carried(cs):
    cs["provenance"]["carried_from_release"] = "20991130"
    cs.setdefault("qc_flags", []).append({"flag": "carried_over", "reason": "the source failed its check in this build"})
    if cs["qc_status"] == "ok":
        cs["qc_status"] = "flagged"


must_fail("S1 too_short without the short_record flag",
          lambda d: fjord(d, "d").__setitem__("qc_flags", [{"flag": "time_base", "reason": "time base unverified"}]))
must_fail("S1 too_short with only a time_base flag and no record_span",
          lambda d: [fjord(d, "d").__setitem__("qc_flags", [{"flag": "time_base", "reason": "x"}]), fjord(d, "d").pop("record_span")])
must_fail("S2 short record with constants and no dropped_constituents", lambda d: fjord(d, "c").pop("dropped_constituents"))
must_fail("S2 short record with constants and empty dropped_constituents",
          lambda d: fjord(d, "c").__setitem__("dropped_constituents", []))
must_fail("S3 accepted water-level set with no constants",
          lambda d: [fjord(d, "a").__setitem__("qc_status", "accepted"), fjord(d, "a").__setitem__("constituents", [])])
must_fail("S3 fallback water-level set with no constants",
          lambda d: [fjord(d, "a").__setitem__("qc_status", "fallback"), fjord(d, "a").__setitem__("constituents", [])])
must_fail("S3 accepted broken record without constants",
          lambda d: [fjord(d, "b").__setitem__("qc_status", "accepted"), fjord(d, "b").__setitem__("constituents", []),
                     fjord(d, "b").pop("fit_segment")])
must_fail("S3 accepted with a disputed time base and no time_base flag",
          lambda d: [fjord(d, "c").__setitem__("qc_status", "accepted"), no_flag(fjord(d, "c"), "time_base")])
must_fail("S4 tombstone with a free-text reason",
          lambda d: station(d, "OTC-EXAMPLE-0002").__setitem__("removed_reason", "short record"))
must_fail("S4 tombstone without merged_into", lambda d: station(d, "OTC-EXAMPLE-0002").pop("merged_into"))
must_fail("S4 tombstone merged into a non-OTC id",
          lambda d: station(d, "OTC-EXAMPLE-0002").__setitem__("merged_into", "example-file-id"))


def tombstone_fjord(d):
    st = station(d, "OTC-EXAMPLE-0005")
    for k in list(st):
        if k != "station_id":
            st.pop(k)
    st.update(status="removed", removed_in="20991231", removed_reason="short record")


must_fail("S4 a GESLA station tombstoned for a short record", tombstone_fjord)
must_fail("S5 provenance key excluded", lambda d: fjord(d, "a")["provenance"].__setitem__("excluded", True))
must_fail("S5 provenance key qc_status", lambda d: fjord(d, "a")["provenance"].__setitem__("qc_status", "excluded"))
must_fail("S5 provenance.decision with an extra key",
          lambda d: fjord(d, "a")["provenance"]["decision"].__setitem__("excluded", True))
must_fail("S5 no_constants with decision outcome fallback",
          lambda d: fjord(d, "d")["provenance"].__setitem__("decision", {"tier": "fallback", "outcome": "fallback"}))
must_fail("S5 no_constants carried over", lambda d: carried(fjord(d, "d")))
must_fail("S5 the set's time_base and provenance.time_base together",
          lambda d: fjord(d, "a")["provenance"].__setitem__("time_base", {"verdict": "rejected", "correction": "none"}))
must_fail("S5 provenance.time_base with an extra key",
          lambda d: d["stations"][0]["constant_sets"][1]["provenance"].__setitem__("time_base", {"verdict": "x", "excluded": True}))
must_fail("S6 time_base flag on a verified time base",
          lambda d: [first_set(d).__setitem__("qc_status", "flagged"),
                     first_set(d).__setitem__("qc_flags", [{"flag": "time_base", "reason": "x"}])])
must_fail("S6 time_base flag on a set without a time base",
          lambda d: [d["stations"][0]["constant_sets"][1].__setitem__("qc_status", "flagged"),
                     d["stations"][0]["constant_sets"][1].__setitem__("qc_flags", [{"flag": "time_base", "reason": "x"}])])
must_fail("D1 qc flag without a reason", lambda d: fjord(d, "c")["qc_flags"][0].pop("reason"))
must_fail("D9 carried_from_release without the carried_over flag",
          lambda d: fjord(d, "a")["provenance"].__setitem__("carried_from_release", "20991130"))
must_fail("D9 carried_over flag without carried_from_release",
          lambda d: [fjord(d, "a").__setitem__("qc_status", "flagged"),
                     fjord(d, "a").__setitem__("qc_flags", [{"flag": "carried_over", "reason": "x"}])])
must_fail("D9 decision outcome fallback without carried_from_release",
          lambda d: fjord(d, "a")["provenance"].__setitem__("decision", {"tier": "fallback", "outcome": "fallback"}))
must_fail("D9 carried-over set with qc_status ok",
          lambda d: [carried(fjord(d, "a")), fjord(d, "a").__setitem__("qc_status", "ok")])
must_fail("C1 release without min_reader_version", lambda d: d["release"].pop("min_reader_version"))
must_fail("C3 gauge set without good_hours", lambda d: fjord(d, "a")["record_span"].pop("good_hours"))
must_fail("C3 gauge set without record_span", lambda d: fjord(d, "a").pop("record_span"))
must_fail("reference station with no constant sets",
          lambda d: station(d, "OTC-EXAMPLE-0005").update(constant_sets=[], recommended_set_id=None))

must_pass("D9 a set carried over from the previous release", lambda d: carried(fjord(d, "a")))
must_pass("D9 a gate fallback carried over", lambda d: [carried(fjord(d, "a")), fjord(d, "a")["provenance"].__setitem__(
    "decision", {"tier": "fallback", "outcome": "fallback", "reason": "the source failed its convention check"})])
must_pass("a duplicate tombstone", lambda d: station(d, "OTC-EXAMPLE-0002").__setitem__("removed_reason", "duplicate"))
must_pass("an official set without record_span", lambda d: d["stations"][0]["constant_sets"][1].pop("record_span", None))
must_pass("a reference station whose only sets have no constants (no recommended set)",
          lambda d: station(d, "OTC-EXAMPLE-0005").update(
              constant_sets=[fjord(d, "d"), fjord(d, "e")], recommended_set_id=None))

# Astronomical tables and datum fields.
def no_astro_tables(d):
    d.pop("astro_tables", None)


def no_astro_table_id(d):
    d["conventions"][0].pop("astro_table_id", None)


def meta_must_fail(label, mutate):
    doc = copy.deepcopy(example)
    mutate(doc)
    doc.pop("stations")
    if meta_validator.is_valid(doc):
        print(f"FAIL: negative control passed validation as .meta.json: {label}")
        sys.exit(1)
    print(f"ok: rejected as .meta.json: {label}")


def named_datums(d, named):
    first_set(d).setdefault("datum", {})["named"] = named


must_fail("no astro_tables while a convention uses f_u_at_prediction", no_astro_tables)
meta_must_fail("no astro_tables while a convention uses f_u_at_prediction", no_astro_tables)
must_fail("an f_u_at_prediction convention without astro_table_id", no_astro_table_id)
meta_must_fail("an f_u_at_prediction convention without astro_table_id", no_astro_table_id)
must_fail("an empty astro_tables", lambda d: d.__setitem__("astro_tables", []))
meta_must_fail("an empty astro_tables", lambda d: d.__setitem__("astro_tables", []))
must_fail("an unknown key in an astro table", lambda d: d["astro_tables"][0].__setitem__("note", "x"))
must_fail("an unknown key in an astro table row (constituents.M2.u)",
          lambda d: d["astro_tables"][0]["constituents"]["M2"].__setitem__("u", 1))


def astro_table(d):
    return d["astro_tables"][0]


def m2_row(d):
    return astro_table(d)["constituents"]["M2"]


must_fail("v0u_deg 360.0", lambda d: m2_row(d)["v0u_deg"].__setitem__(0, 360.0))
must_fail("v0u_deg -0.01", lambda d: m2_row(d)["v0u_deg"].__setitem__(0, -0.01))
must_fail("a negative f", lambda d: m2_row(d)["f"].__setitem__(0, -0.5))
must_fail("an astro table row without f", lambda d: m2_row(d).pop("f"))
must_fail("an astro table without tables_sha256", lambda d: astro_table(d).pop("tables_sha256"))
must_fail("an astro table with a malformed tables_sha256", lambda d: astro_table(d).__setitem__("tables_sha256", "33f97cb7"))
must_fail("an astro table constituent key that is not a constituent name (M2(KS)2)",
          lambda d: astro_table(d)["constituents"].__setitem__("M2(KS)2", m2_row(d)))
must_fail("an empty convention.astro_table_id", lambda d: d["conventions"][0].__setitem__("astro_table_id", ""))
meta_must_fail("an invalid table in astro_tables (no constituents)", lambda d: astro_table(d).pop("constituents"))
for key in ("chart", "mean_level", "zero"):
    must_fail(f"datum.named key {key}", lambda d, k=key: named_datums(d, {k: 0.0}))
must_fail("datum.zero lat (not a kind of zero)", lambda d: first_set(d)["datum"].__setitem__("zero", "lat"))
for zero in ("chart_datum", "gauge_zero", "msl", "unknown"):
    must_pass(f"datum.zero {zero}", lambda d, z=zero: first_set(d)["datum"].__setitem__("zero", z))
must_fail("an empty datum.chart_datum", lambda d: first_set(d)["datum"].update(chart_datum="", named={"mllw": 0.0}))


def all_conventions_none(d):
    for convention in d["conventions"]:
        convention["nodal_handling"] = "none"
        convention.pop("astro_table_id", None)
    no_astro_tables(d)


def meta_must_pass(label, mutate):
    doc = copy.deepcopy(example)
    mutate(doc)
    doc.pop("stations")
    errors = [e.message for e in meta_validator.iter_errors(doc)]
    if errors:
        print(f"FAIL: {label} as .meta.json: {errors[0]}")
        sys.exit(1)
    print(f"ok: accepted as .meta.json: {label}")


must_pass("every convention with nodal_handling none, no astro_table_id and no astro_tables", all_conventions_none)
meta_must_pass("every convention with nodal_handling none, no astro_table_id and no astro_tables", all_conventions_none)

# Licences: commercial_use, and the restriction and terms_url of a non-commercial licence.
NON_COMMERCIAL = {
    "licence_id": "nc-control",
    "spdx": "CC-BY-NC-4.0",
    "provider": "Example provider via GESLA",
    "attribution": "Example attribution text.",
    "commercial_use": False,
    "restriction": "Non-commercial use only: example restriction.",
    "terms_url": "https://example.org/terms",
}


def with_nc_licence(d, **changes):
    licence = dict(NON_COMMERCIAL, **changes)
    for key in [k for k, v in changes.items() if v is None]:
        licence.pop(key)
    d["licences"].append(licence)


must_fail("a licence without commercial_use", lambda d: d["licences"][0].pop("commercial_use", None))
must_fail("commercial_use false without restriction", lambda d: with_nc_licence(d, restriction=None))
must_fail("commercial_use false without terms_url", lambda d: with_nc_licence(d, terms_url=None))
must_fail("a record_span.start that is not a date-time (2007-01-01)",
          lambda d: first_set(d)["record_span"].__setitem__("start", "2007-01-01"))
must_fail("commercial_use as the string \"false\", without restriction",
          lambda d: with_nc_licence(d, commercial_use="false", restriction=None))
must_fail("an empty restriction", lambda d: with_nc_licence(d, restriction=""))
must_fail("a restriction that is not a string", lambda d: with_nc_licence(d, restriction=5))
must_fail("a terms_url that is not a string", lambda d: with_nc_licence(d, terms_url=5))
must_fail("a terms_url that is not a URI", lambda d: with_nc_licence(d, terms_url="not a uri"))
must_pass("a non-commercial licence with spdx CC-BY-NC-4.0", lambda d: with_nc_licence(d))
must_pass("a non-commercial licence with spdx LicenseRef-GESLA-Research",
          lambda d: with_nc_licence(d, spdx="LicenseRef-GESLA-Research"))

# Numbers that depend on the build (the ~15-day minimum, the ranking by good_hours) are build
# checks, listed in the schema's top description. These instances are valid JSON Schema on purpose.
for label, mutate in (
        ("too_short with 10000 good hours", lambda d: fjord(d, "d")["record_span"].__setitem__("good_hours", 10000)),
        ("constants fitted from 24 good hours", lambda d: fjord(d, "a")["record_span"].__setitem__("good_hours", 24)),
        ("datum.chart_datum naming a key that is not in datum.named",
         lambda d: first_set(d)["datum"].update(chart_datum="mllw", named={"msl": 1.0}))):
    doc = copy.deepcopy(example)
    mutate(doc)
    if problems(doc):
        print(f"FAIL: {label} should be left to the build check")
        sys.exit(1)
    print(f"ok: left to the build check: {label}")


# --- OTC_index.json and the latest pointers -----------------------------------------------------

index_schema = json.loads((schema_dir / "otc-index-1.0.schema.json").read_text())
Draft202012Validator.check_schema(index_schema)
index_validator = Draft202012Validator(index_schema, format_checker=FORMATS)
pointer_validator = Draft202012Validator({"$schema": index_schema["$schema"], "$defs": index_schema["$defs"],
                                          "$ref": "#/$defs/release_info"}, format_checker=FORMATS)
index_example = json.loads((schema_dir / "index-example.json").read_text())
errors = [e.message for e in index_validator.iter_errors(index_example)]
if errors:
    print("index-example.json:", errors[0])
    sys.exit(1)
print("ok: index-example.json is a valid OTC_index.json")
for entry in index_example["releases"]:
    if not pointer_validator.is_valid(entry):
        print(f"FAIL: index entry {entry['datestamp']} is not a valid OTC_latest.json")
        sys.exit(1)
print("ok: each index entry is a valid OTC_latest.json")


def index_must_fail(label, mutate):
    doc = copy.deepcopy(index_example)
    mutate(doc)
    if index_validator.is_valid(doc):
        print(f"FAIL: negative control passed validation: index {label}")
        sys.exit(1)
    print(f"ok: rejected index {label}")


latest = lambda d: d["releases"][0]
index_must_fail("entry with doi instead of zenodo_version_doi",
                lambda d: latest(d).__setitem__("doi", latest(d).pop("zenodo_version_doi")))
index_must_fail("entry without zenodo_version_doi", lambda d: latest(d).pop("zenodo_version_doi"))
index_must_fail("entry without concept_doi", lambda d: latest(d).pop("concept_doi"))
index_must_fail("entry without content_sha256", lambda d: latest(d).pop("content_sha256"))
index_must_fail("entry without min_reader_version", lambda d: latest(d).pop("min_reader_version"))
index_must_fail("file without sha256", lambda d: latest(d)["files"][0].pop("sha256"))
index_must_fail("zenodo_version_doi that is not a DOI", lambda d: latest(d).__setitem__("zenodo_version_doi", "zenodo.1"))
index_must_fail("bare list instead of {releases: [...]}", lambda d: d.__setitem__("releases", {}))


# --- confirmation round (provenance allowlist, fallback, versions, reasons, stations) ------------

for key in ("Excluded", "EXCLUDED", "is_excluded", "exclusion", "suppressed", "qc", "state", "visibility", "rejected"):
    must_fail(f"provenance key {key}", lambda d, k=key: fjord(d, "a")["provenance"].__setitem__(k, True))
must_fail("provenance with a nested gate status",
          lambda d: fjord(d, "a")["provenance"].__setitem__("gate", {"status": "excluded"}))
must_fail("JMA analysis_years with an extra key",
          lambda d: d["stations"][0]["constant_sets"][1]["provenance"].__setitem__("analysis_years", {"status": "excluded"}))
must_fail("qc_status fallback without carried_from_release", lambda d: fjord(d, "a").__setitem__("qc_status", "fallback"))
must_fail("min_reader_version 0.6", lambda d: d["release"].__setitem__("min_reader_version", "0.6"))
must_fail("min_reader_version 9.9", lambda d: d["release"].__setitem__("min_reader_version", "9.9"))
must_fail("format_version 0.6 under the 1.0 schema", lambda d: d.__setitem__("format_version", "0.6"))
must_fail("release without fit_min_good_hours", lambda d: d["release"].pop("fit_min_good_hours"))
must_fail("flag reason that is only whitespace", lambda d: fjord(d, "b")["qc_flags"][0].__setitem__("reason", " \t"))
must_fail("time_base reason that is only whitespace", lambda d: fjord(d, "b")["time_base"].__setitem__("reason", "  "))
must_fail("record issue reason that is only whitespace", lambda d: fjord(d, "b")["record_issues"][0].__setitem__("reason", " "))
must_fail("reference station with constants but no recommended set",
          lambda d: station(d, "OTC-EXAMPLE-0005").__setitem__("recommended_set_id", None))
must_fail("subordinate station with no sets and no offsets",
          lambda d: station(d, "OTC-EXAMPLE-0004").pop("current_offsets"))
index_must_fail("zenodo_material that is not a boolean", lambda d: latest(d).__setitem__("zenodo_material", "yes"))
index_must_fail("file name with a bad datestamp counter", lambda d: latest(d)["files"][0].__setitem__("name", "OTC_20991231.1.json"))
index_must_fail("file name without a datestamp", lambda d: latest(d)["files"][0].__setitem__("name", "OTC_latest.json"))
for label, mutate in (
        ("zenodo_material on an index entry", lambda d: latest(d).__setitem__("zenodo_material", True)),
        ("an older release with min_reader_version 0.5", lambda d: d["releases"][1].__setitem__("min_reader_version", "0.5")),
        ("a second release of the day, OTC_20991231.2.json", lambda d: latest(d)["files"][0].__setitem__("name", "OTC_20991231.2.json"))):
    doc = copy.deepcopy(index_example)
    mutate(doc)
    if not index_validator.is_valid(doc):
        print(f"FAIL: index {label}")
        sys.exit(1)
    print(f"ok: accepted index {label}")
doc = copy.deepcopy(example)
fjord(doc, "a")["record_span"]["good_hours"] = 1e9
if problems(doc):
    print("FAIL: good_hours above the span should be left to the build check")
    sys.exit(1)
print("ok: left to the build check: good_hours above the record span")


# --- provenance written by the real adapters ----------------------------------------------------
# scripts/fixtures/adapter-sets.json holds the provenance of real sets from every adapter that
# writes sets (one per distinct shape). Provenance is a closed list, so it must accept each.

prov_validator = sub_validator("provenance")
real = json.loads((root / "scripts/fixtures/adapter-sets.json").read_text())["sets"]
# provenance.build is required in 1.0 and the fixtures predate it, so a missing build is listed as
# a writer follow-up below, not counted as a rejection.
def missing_build(e):
    return e.validator == "required" and not e.path and "'build'" in e.message


bad = [(r["adapter"], r["source_record_id"], e.message[:120]) for r in real
       for e in prov_validator.iter_errors(r["provenance"]) if not missing_build(e)]
if bad:
    for b in bad:
        print("FAIL: real provenance rejected:", *b)
    sys.exit(1)
print(f"ok: accepted the provenance of {len(real)} real sets from "
      f"{', '.join(sorted({r['adapter'] for r in real}))}")
# The fixtures keep only provenance and identity, so their recorded qc_status is not validated here.
# A status that 1.0 does not have is a writer follow-up, not a schema failure.
old_status = sorted({r["adapter"] for r in real if r.get("qc_status") not in (None, "ok", "flagged", "no_constants")})
if old_status:
    print(f"note: fixtures record a qc_status that 1.0 does not have (writer follow-up): {', '.join(old_status)}")
no_build = sorted({r["adapter"] for r in real if "build" not in r["provenance"]})
if no_build:
    print(f"note: fixtures without the required provenance.build (writer follow-up): {', '.join(no_build)}")


def official(d):
    return d["stations"][0]["constant_sets"][1]


def jma(d):
    return official(d)["provenance"]


def as_source(source, prov):
    """Put a real adapter's provenance on the official set. The fixtures predate the required
    provenance.build, so the build object of the example set is kept."""
    def f(d):
        official(d)["source"] = source
        build = official(d)["provenance"]["build"]
        official(d)["provenance"] = copy.deepcopy(prov)
        official(d)["provenance"].setdefault("build", build)
    return f


real_jma = next(r["provenance"] for r in real if r["adapter"] == "A-JMA-a")
real_linz = next(r["provenance"] for r in real if r["adapter"] == "A-LINZ" and r["provenance"]["position"]["from"] == "station_list")
must_pass("the real JMA provenance on a jma set", as_source("jma", real_jma))
must_pass("the real LINZ provenance (position from the station list) on a linz set", as_source("linz", real_linz))
must_pass("a LINZ header position that could not be read (null)",
          lambda d: [as_source("linz", real_linz)(d), jma(d)["position"].update(header_lat=None, header_lon=None)])
must_fail("a set without provenance.build", lambda d: fjord(d, "a")["provenance"].pop("build"))
must_fail("an official set without provenance.build", lambda d: official(d)["provenance"].pop("build"))
must_pass("build-only fields in provenance.build",
          lambda d: jma(d).__setitem__("build", {"build_commit": "0123abc", "built_at": "2099-12-31T00:00:00Z",
                                                 "fetched_at": "2099-12-30T00:00:00Z", "run_id": "123456",
                                                 "tool_versions": {"python": "3.12.7", "uv": "v0.4.18"}}))
must_fail("JMA owner other than jma", lambda d: [as_source("jma", real_jma)(d), jma(d).__setitem__("owner", "third_party")])
must_fail("JMA owner jma with comparator_only",
          lambda d: [as_source("jma", real_jma)(d), jma(d).__setitem__("redistribution", "comparator_only")])
must_fail("a comparator_only JMA set",
          lambda d: [as_source("jma", real_jma)(d), jma(d).update(owner="jcg_or_gsi", redistribution="comparator_only")])
must_fail("a jma set without owner and redistribution",
          lambda d: [as_source("jma", real_jma)(d), jma(d).pop("owner"), jma(d).pop("redistribution")])
must_fail("a GESLA set carrying owner and redistribution",
          lambda d: fjord(d, "a")["provenance"].update(owner="jma", redistribution="redistribute"))
must_fail("a GESLA set carrying redistribution comparator_only",
          lambda d: fjord(d, "a")["provenance"].__setitem__("redistribution", "comparator_only"))
must_fail("a kartverket set carrying the LINZ position", lambda d: jma(d).__setitem__("position", copy.deepcopy(real_linz["position"])))
must_fail("a GESLA set carrying the LINZ member", lambda d: fjord(d, "a")["provenance"].__setitem__("member", "x_Harm_y.txt"))
must_fail("LINZ position with an extra key",
          lambda d: [as_source("linz", real_linz)(d), jma(d)["position"].__setitem__("status", "excluded")])
must_fail("LINZ position from the station list without replaced_because",
          lambda d: [as_source("linz", real_linz)(d), jma(d)["position"].pop("replaced_because")])
must_fail("list_distance_km at the top of provenance", lambda d: jma(d).__setitem__("list_distance_km", 0.4))
must_fail("provenance.build without build_commit", lambda d: jma(d)["build"].pop("build_commit"))
must_fail("provenance.build with an extra key", lambda d: jma(d).__setitem__("build", {"build_commit": "0123abc", "status": "excluded"}))
must_fail("tool_versions smuggling a status",
          lambda d: jma(d).__setitem__("build", {"build_commit": "0123abc", "tool_versions": {"excluded": "true", "qc_status": "removed"}}))
must_fail("tool_versions with an upper-case name", lambda d: jma(d).__setitem__("build", {"build_commit": "0123abc", "tool_versions": {"Python": "3.12"}}))
must_fail("provenance.build_commit that is not hex", lambda d: jma(d).__setitem__("build_commit", "not a commit"))
must_fail("both build_commit and build.build_commit",
          lambda d: jma(d).update(build_commit="aaaaaaa", build={"build_commit": "bbbbbbb"}))
must_fail("provenance.build_commit alone (a 0.x draft field; use build.build_commit)",
          lambda d: jma(d).__setitem__("build_commit", "aaaaaaa"))
must_fail("LINZ header position null but from the header",
          lambda d: [as_source("linz", real_linz)(d),
                     jma(d).__setitem__("position", {"header_lat": None, "header_lon": None, "from": "header"})])
must_fail("LINZ header latitude null, from the header",
          lambda d: [as_source("linz", real_linz)(d),
                     jma(d).__setitem__("position", {"header_lat": None, "header_lon": 174.0, "from": "header"})])
for name in ("excluded", "removed", "status", "qc_status", "state", "suppressed", "withheld"):
    must_fail(f"tool_versions name {name}", lambda d, n=name: jma(d).__setitem__("build", {"build_commit": "0123abc", "tool_versions": {n: "1"}}))
for name in ("excluded", "Removed", "STATUS", "qc_status", "state", "suppressed", "withheld"):
    must_fail(f"qc_flags values key {name}",
              lambda d, n=name: fjord(d, "b")["qc_flags"][0].__setitem__("values", {n: 1}))
    must_fail(f"record_issues values key {name}",
              lambda d, n=name: fjord(d, "b")["record_issues"][0]["values"].__setitem__(n, 1))
must_fail("qc_flags values {excluded: true, status: removed}",
          lambda d: fjord(d, "b")["qc_flags"][0].__setitem__("values", {"excluded": True, "status": "removed"}))
for where, target in (("qc_flags", lambda d: fjord(d, "b")["qc_flags"][0].setdefault("values", {})),
                      ("record_issues", lambda d: fjord(d, "b")["record_issues"][0]["values"])):
    must_fail(f"{where} values with a nested status object",
              lambda d, t=target: t(d).__setitem__("gate", {"status": "excluded"}))
    must_fail(f"{where} values with a string", lambda d, t=target: t(d).__setitem__("note", "removed"))
    must_fail(f"{where} values with an array", lambda d, t=target: t(d).__setitem__("months", [1, 2]))
    must_fail(f"{where} values with a boolean", lambda d, t=target: t(d).__setitem__("ok", True))
must_pass("a null in values", lambda d: fjord(d, "b")["record_issues"][0]["values"].__setitem__("lag_min", None))
must_pass("ordinary values keys (step_m, lag_min, days)",
          lambda d: [fjord(d, "b")["qc_flags"][0].__setitem__("values", {"lag_min": -29.6, "days": 39.2}),
                     fjord(d, "b")["record_issues"][0]["values"].__setitem__("step_m", -0.31)])
must_pass("tool_versions with ordinary names (python, uv, numpy)",
          lambda d: jma(d).__setitem__("build", {"build_commit": "0123abc", "tool_versions": {"python": "3.12.7", "uv": "0.4.18", "numpy": "2.1.2"}}))


# --- datums: named levels with a basis (published, computed, observed) ---------------------------
# The must-pass datum blocks below are also in example.json, and this section checks that they are
# the same, so the example and the controls cannot drift apart. Every failure in this section is
# printed before the script stops, so a run shows the whole picture.

NTDE = {"start": "1983-01-01", "end": "2001-12-31", "name": "NTDE 1983-2001"}
OTC_EPOCH = {"start": "2002-01-01", "end": "2020-12-31", "name": "OTC 2002-2020"}
LAT_WINDOW = {"start": "2020-01-01", "end": "2038-12-31", "name": "OTC LAT 2020-2038"}


def unalias(obj):
    """A copy with no shared sub-objects, so that a control that edits one basis in place cannot
    also change another basis (copy.deepcopy keeps sharing)."""
    return json.loads(json.dumps(obj))


def noaa_published(name, **extra):
    return unalias(dict({"kind": "published", "method": "source", "source_name": name}, **extra))


# NOAA's ctrlStation is "id name", e.g. "8443970 Boston, MA"; source_id keeps the id. A primary
# station such as Boston has no control; its subordinates (e.g. Lynn, 8443725) name it.
BOSTON_CONTROL = {"source_id": "8443970"}

# A NOAA reference set modelled on a primary station: zero MLLW, published levels with no control
# (lat and hat with no epoch: NOAA computes them over a 40-year window, currently 2000-2040, not
# stated per station; a few stations still carry older values), and OTC's computed LAT and HAT next to NOAA's as otc_lat and otc_hat.
NOAA_DATUM = {
    "msl_offset_m": 1.554,
    "zero": "chart_datum",
    "zero_name": "MLLW",
    "chart_datum": "mllw",
    "named": {"mhhw": 3.141, "msl": 1.554, "mllw": 0.0, "lat": -0.511, "hat": 3.765, "otc_lat": -0.523, "otc_hat": 3.781},
    "basis": {
        "mhhw": noaa_published("MHHW", epoch=NTDE),
        "msl": noaa_published("MSL", epoch=NTDE),
        "mllw": noaa_published("MLLW", epoch=NTDE),
        "lat": noaa_published("LAT"),
        "hat": noaa_published("HAT"),
        "otc_lat": {"kind": "computed", "method": "harmonic_extremes", "epoch": LAT_WINDOW,
                    "uncertainty_m": 0.012, "uncertainty_basis": "calibrated"},
        "otc_hat": {"kind": "computed", "method": "harmonic_extremes", "epoch": LAT_WINDOW,
                    "uncertainty_m": 0.012, "uncertainty_basis": "calibrated"},
    },
}


def gesla_observed(**extra):
    return unalias({"kind": "observed", "method": "modified_range_ratio", "epoch": OTC_EPOCH,
                 "data_span": {"start": "2007-01-01", "end": "2025-12-31", "months": 214},
                 "control": {"station_id": "OTC-EXAMPLE-0001", "set_id": "OTC-EXAMPLE-0001/gesla-fit"},
                 "uncertainty_m": 0.008, "uncertainty_basis": "calibrated", **extra})


# A GESLA set reduced to the OTC epoch through a control station by the modified range ratio method.
GESLA_OBSERVED_DATUM = {
    "msl_offset_m": 1.1,
    "zero": "gauge_zero",
    "named": {"mhhw": 1.78, "mhw": 1.72, "msl": 1.102, "mtl": 1.1, "mlw": 0.48, "mllw": 0.42},
    "basis": {k: gesla_observed() for k in ("mhhw", "mhw", "msl", "mtl", "mlw", "mllw")},
}

# A 19-year primary determination: the plain first reduction over the whole OTC epoch, no control.
# It has every level the modified range ratio method needs from a control (MTL, DTL, MSL, Mn, Gt).
PRIMARY_KEYS = ("mhhw", "mhw", "dtl", "mtl", "msl", "mlw", "mllw")
PRIMARY_DATUM = {
    "msl_offset_m": 1.0,
    "zero": "gauge_zero",
    "named": {"mhhw": 1.55, "mhw": 1.5, "dtl": 1.005, "mtl": 1.005, "msl": 1.003, "mlw": 0.51, "mllw": 0.46},
    "basis": {k: {"kind": "observed", "method": "first_reduction", "epoch": OTC_EPOCH,
                  "data_span": {"start": "2002-01-01", "end": "2020-12-31", "months": 228},
                  "uncertainty_m": 0.004, "uncertainty_basis": "propagated"} for k in PRIMARY_KEYS},
}

# A NOAA subordinate station (the Lynn pattern): its own published MLLW and MHHW, with NOAA's
# control station (also in the release, as OTC-EXAMPLE-0006), and LAT and HAT computed from the
# reference station's predicted extremes over 2020-2038 with the offsets applied: ratio 0.94 of the
# reference's otc_lat and otc_hat (-0.523, 3.781), never of NOAA's own LAT and HAT.
BOSTON_CONTROL_IN_RELEASE = dict(BOSTON_CONTROL, station_id="OTC-EXAMPLE-0006")
SUBORDINATE_DATUM = {
    "zero": "chart_datum",
    "zero_name": "MLLW",
    "chart_datum": "mllw",
    "named": {"mhhw": 2.953, "mllw": 0.0, "lat": -0.492, "hat": 3.554},
    "basis": {
        "mhhw": noaa_published("MHHW", epoch=NTDE, control=BOSTON_CONTROL_IN_RELEASE),
        "mllw": noaa_published("MLLW", epoch=NTDE, control=BOSTON_CONTROL_IN_RELEASE),
        "lat": {"kind": "computed", "method": "subordinate_offsets", "epoch": LAT_WINDOW,
                "uncertainty_m": 0.03, "uncertainty_basis": "calibrated"},
        "hat": {"kind": "computed", "method": "subordinate_offsets", "epoch": LAT_WINDOW,
                "uncertainty_m": 0.03, "uncertainty_basis": "calibrated"},
    },
}

# An RWS gauge set whose zero is NAP. NAP is published with no epoch; MSL is a labelled first
# reduction over the record's own months (no qualified control, not adjusted to the OTC epoch).
RWS_DATUM = {
    "msl_offset_m": 0.071,
    "zero": "vertical_datum",
    "zero_name": "NAP",
    "named": {"nap": 0.0, "msl": 0.068},
    "basis": {
        "nap": {"kind": "published", "method": "source", "source_name": "NAP"},
        "msl": {"kind": "observed", "method": "first_reduction",
                "epoch": {"start": "2023-01-01", "end": "2024-12-31"},
                "data_span": {"start": "2023-01-01", "end": "2024-12-31", "months": 24},
                "uncertainty_m": 0.03, "uncertainty_basis": "calibrated", "flags": ["no_qualified_control"]},
    },
}

# The example's Kartverket set: zero and chart datum CD (spec 4.1 item 3), published LAT with no
# epoch, and MSL with the epoch Kartverket states for it (1996-2014).
KARTVERKET_DATUM = {
    "msl_offset_m": 0.9,
    "zero": "chart_datum",
    "zero_name": "CD",
    "chart_datum": "cd",
    "named": {"cd": 0.0, "lat": 0.0, "msl": 0.9},
    "basis": {"cd": {"kind": "published", "method": "source", "source_name": "CD"},
              "lat": {"kind": "published", "method": "source", "source_name": "LAT"},
              "msl": {"kind": "published", "method": "source", "source_name": "MSL",
                      "epoch": {"start": "1996-01-01", "end": "2014-12-31", "name": "Kartverket MSL 1996-2014"}}},
}

# An SMHI gauge set in RH 2000, whose chart datum is the charting authority's BSCD2000 realization
# (Sjöfartsverket's label "RH 2000").
SMHI_DATUM = {
    "msl_offset_m": 0.152,
    "zero": "vertical_datum",
    "zero_name": "RH 2000",
    "chart_datum": "rh2000",
    "named": {"rh2000": 0.0},
    "basis": {"rh2000": {"kind": "published", "method": "source", "source_name": "RH 2000"}},
}

# An FMI gauge set in N2000 whose chart is not yet converted: the older MSL-based chart datum of
# one stated year, key mw, with Traficom's own label MSL.
FMI_OLDER_DATUM = {
    "msl_offset_m": 0.231,
    "zero": "vertical_datum",
    "zero_name": "N2000",
    "chart_datum": "mw",
    "named": {"mw": 0.214},
    "basis": {"mw": {"kind": "published", "method": "source", "source_name": "MSL",
                     "epoch": {"start": "2023-01-01", "end": "2023-12-31"}}},
}

# A GESLA record whose stated zero is the chart datum: Admiralty Chart Datum, published cd = 0,
# with no epoch.
GESLA_CD_DATUM = {
    "msl_offset_m": 1.1,
    "zero": "chart_datum",
    "zero_name": "ACD",
    "chart_datum": "cd",
    "named": {"cd": 0.0},
    "basis": {"cd": {"kind": "published", "method": "source", "source_name": "Admiralty Chart Datum (ACD)"}},
}

for _name in ("NOAA_DATUM", "GESLA_OBSERVED_DATUM", "PRIMARY_DATUM", "SUBORDINATE_DATUM", "RWS_DATUM",
              "KARTVERKET_DATUM", "SMHI_DATUM", "FMI_OLDER_DATUM", "GESLA_CD_DATUM"):
    globals()[_name] = unalias(globals()[_name])

datum_failures = []


def short_problem(doc):
    """The most relevant error, short: its path and the first 160 characters of its message."""
    error = best_match(validator.iter_errors(doc))
    if error is None:
        return problems(doc)[0][:160]
    return f"{list(error.absolute_path)} {error.message[:160]}"


def datum_must_pass(label, mutate):
    doc = copy.deepcopy(example)
    mutate(doc)
    if problems(doc):
        datum_failures.append(f"must pass, was rejected: {label}")
        print(f"FAIL: must pass, was rejected: {label}: {short_problem(doc)}")
    else:
        print(f"ok: accepted {label}")


# Each must-fail control names the rule that must reject it. A rule is a regular expression on the
# schema path of an error (jsonschema leaves $ref out of it), relative to the datum's container schema (constant_set.datum or
# subordinate_offsets.datum). Every leaf error of every datum in the document must match it, so a
# control that another rule also rejects (a masked control) fails here.
_D = r"^allOf/0/"                                        # $defs/datum ($ref is not in the path)
_B = _D + r"properties/basis/additionalProperties/"         # $defs/datum_basis


def basis_rule(tail):
    return _B + tail


R_DEP = _D + r"dependentRequired"
R_ZERO = _D + r"properties/zero/enum"
R_ZERO_NAME = _D + r"properties/zero_name/(minLength|maxLength)"
R_CHART = _D + r"properties/chart_datum/(pattern|not)"
R_NAMED_KEY = _D + r"properties/named/propertyNames/(pattern|not)"
R_BASIS_KEY = _D + r"properties/basis/propertyNames/(pattern|not)"
R_KEY = _D + r"properties/(named|basis)/propertyNames/(pattern|not)"
R_NAMED_NUMBER = _D + r"properties/named/additionalProperties/type"
R_DATUM_EXTRA = _D + r"additionalProperties"
R_MSL_OFFSET = _D + r"properties/msl_offset_m/type"
R_OTC = _D + r"allOf/0/properties/basis/patternProperties/\^otc_/"
R_HIGH_ONLY = _D + r"allOf/0/properties/basis/patternProperties/\^\(\?!"
R_MSL_SAMPLING = _D + r"allOf/0/properties/basis/patternProperties/\^\(otc_\)\?\(msl\|mtl\|dtl\)\$/"
R_TRUNCATED_DATUM = _D + r"allOf/1/then/"
R_PUBLISHED = basis_rule(r"allOf/0/then/")
R_COMPUTED = basis_rule(r"allOf/1/then/")
R_OBSERVED = basis_rule(r"allOf/2/then/")
R_COMPARISON = basis_rule(r"allOf/3/then/")
R_FR_NO_CONTROL = basis_rule(r"allOf/4/then/")
R_OWN_MONTH = basis_rule(r"allOf/5/then/")
R_NINETEEN = basis_rule(r"allOf/5/else/then/")
R_DIRECT_FLAG = basis_rule(r"allOf/6/then/")
R_FLAG_DIRECT = basis_rule(r"allOf/7/then/")
R_SHORT = basis_rule(r"allOf/8/then/(then|else)/")
R_UNCERTAINTY_PAIR = basis_rule(r"dependentRequired")
R_KIND = basis_rule(r"properties/kind/enum")
R_BASIS_EXTRA = basis_rule(r"additionalProperties")
R_FLAGS = basis_rule(r"properties/flags/")
R_CONTROL = basis_rule(r"properties/control/")
R_SOURCE_NAME = basis_rule(r"properties/source_name/(minLength|maxLength)")
R_UNCERTAINTY = basis_rule(r"properties/uncertainty_(m|basis)/")
R_EPOCH = basis_rule(r"properties/epoch/")
R_DATA_SPAN = basis_rule(r"properties/data_span/")
R_DATE = basis_rule(r"properties/(epoch|data_span)/properties/(start|end)/pattern")
R_ONLY_SUBORDINATE_METHODS = r"^allOf/1/properties/basis/additionalProperties/properties/method/enum"
R_NO_SUBORDINATE_METHOD = r"^allOf/1/properties/basis/additionalProperties/properties/method/not"


def either(*rules):
    """A control that two rules both reject by design (the rules overlap); see the comment at its use."""
    return "|".join(f"(?:{r})" for r in rules)


# The rule of each control that does not name one at its call. The overlapping pairs (either) are
# shapes that two rules reject by design, so neither rule can be tested alone there; each such rule
# has its own single-rule control elsewhere, or its mutant is equivalent (PR body).
EXPECTED_RULES = {
    "named without basis (the PR #45 shape: zero, chart_datum and named only)": R_DEP,
    "named without basis": R_DEP,
    "basis without named": R_DEP,
    "subordinate_offsets.datum named without basis": R_DEP,
    "kind published with method first_reduction (no control)": R_PUBLISHED,
    "kind published with method harmonic_extremes": R_PUBLISHED,
    "kind published with data_span": R_PUBLISHED,
    # truncated_lows goes with the direct method, which a published level never has.
    "kind published with flag truncated_lows (on mhhw, a high-water level)": either(R_PUBLISHED, R_FLAG_DIRECT),
    "kind computed with method source": R_COMPUTED,
    "kind computed with control": R_COMPUTED,
    "kind computed with data_span": R_COMPUTED,
    "kind computed without epoch": R_COMPUTED,
    "kind computed without uncertainty_m": R_COMPUTED,
    "kind computed with flag gaps": R_COMPUTED,
    "kind observed with method harmonic_extremes": R_OBSERVED,
    "kind observed with method subordinate_offsets (on subordinate_offsets.datum, where the method is allowed)": R_OBSERVED,
    "kind observed without epoch": R_OBSERVED,
    "kind observed without data_span": R_OBSERVED,
    "kind observed without uncertainty_m": R_OBSERVED,
    "kind observed with flag provisional": R_OBSERVED,
    "uncertainty_m without uncertainty_basis": R_UNCERTAINTY_PAIR,
    "uncertainty_basis without uncertainty_m": R_UNCERTAINTY_PAIR,
    "data_span without months": R_DATA_SPAN,
    "method standard without control": R_COMPARISON,
    "method standard with a control that has only source_id": R_COMPARISON,
    "method standard without control.set_id": R_COMPARISON,
    "method first_reduction with control": R_FR_NO_CONTROL,
    # A control with only set_id is rejected by the control's anyOf and, on a published level, by
    # its required source_id.
    "a published control with only set_id": either(R_CONTROL, R_PUBLISHED),
    # amplitude_ratio is outside the method enum and outside the observed methods.
    "method amplitude_ratio": either(basis_rule(r"properties/method/enum"), R_OBSERVED),
    "a datum key Lat": R_KEY,
    "a datum key mean_level": R_KEY,
    "a datum key of 32 characters": R_KEY,
    "a datum key ending in a newline": R_KEY,
    "a datum key 1lat": R_KEY,
    "a datum key _lat": R_KEY,
    "a datum key lAt": R_KEY,
    "a datum key od-malin": R_KEY,
    "a basis key Lat with no named key Lat": R_BASIS_KEY,
    "a key Lat in named only (no basis key)": R_NAMED_KEY,
    "chart_datum Mllw (not a datum key)": R_CHART,
    "zero geoid": R_ZERO,
    "zero_name of 32 characters": R_ZERO_NAME,
    "an empty zero_name": R_ZERO_NAME,
    "a flag wrong_flag": R_FLAGS,
    "an epoch date 2020-13-45": R_DATE,
    "an epoch date x": R_DATE,
    "a data_span date with a trailing newline": R_DATE,
    "an epoch without end": R_EPOCH,
    "an epoch without start": R_EPOCH,
    "a data_span without start": R_DATA_SPAN,
    "a data_span without end": R_DATA_SPAN,
    "data_span months 0": R_DATA_SPAN,
    "data_span months 12.5": R_DATA_SPAN,
    "a negative uncertainty_m": R_UNCERTAINTY,
    "uncertainty_m as a string": R_UNCERTAINTY,
    # Each kind limits uncertainty_basis as well, so a value outside the enum hits both.
    "uncertainty_basis guess": either(R_UNCERTAINTY, R_OBSERVED),
    "a control station_id that is not an OTC id": R_CONTROL,
    "a control station_id with a trailing newline": R_CONTROL,
    "an empty control.set_id": R_CONTROL,
    "an empty control.source_id": R_CONTROL,
    "an unknown key in a control": R_CONTROL,
    "an unknown key in a basis": R_BASIS_EXTRA,
    "an unknown key in an epoch": R_EPOCH,
    "an unknown key in a data_span": R_DATA_SPAN,
    "an empty epoch name": R_EPOCH,
    "an unknown key in a datum": R_DATUM_EXTRA,
    "msl_offset_m as a string": R_MSL_OFFSET,
    "a named level as a string": R_NAMED_NUMBER,
    "an own-month first reduction (24 months) without the no_qualified_control flag": R_NINETEEN,
    "no_qualified_control on a comparison (method modified_range_ratio)": R_OWN_MONTH,
    "a comparison (modified_range_ratio) on the NTDE epoch 1983-2001": R_COMPARISON,
    "a comparison (standard) with epoch end 2021-12-31": R_COMPARISON,
    "a comparison (direct) with epoch start 2001-01-01": R_COMPARISON,
    "an unflagged first_reduction of 228 months over 1950-1968 (not the OTC epoch)": R_NINETEEN,
    "an unflagged first_reduction of 228 months with epoch 2002-01-01 to 2021-12-31": R_NINETEEN,
    "an unflagged first_reduction of 215 months over the OTC epoch": R_NINETEEN,
    "a published level with uncertainty_basis calibrated": R_PUBLISHED,
    "a published level with uncertainty_basis propagated": R_PUBLISHED,
    "an observed level with uncertainty_basis source": R_OBSERVED,
    "a computed level with uncertainty_basis source": R_COMPUTED,
    "a subordinate station's lat by harmonic_extremes": R_ONLY_SUBORDINATE_METHODS,
    "a subordinate station's level of kind observed (first_reduction over the OTC epoch)": R_ONLY_SUBORDINATE_METHODS,
    "a constant set's otc_lat by subordinate_offsets": R_NO_SUBORDINATE_METHOD,
    "an otc_lat of kind published": R_OTC,
    "method modified_range_ratio without control": R_COMPARISON,
    "method direct without control": R_COMPARISON,
    "a comparison control with set_id and source_id but no station_id": R_COMPARISON,
    "kind estimated": R_KIND,
    "kind computed with method first_reduction (on the OTC epoch)": R_COMPUTED,
    "kind observed with method source": R_OBSERVED,
    "a repeated flag": R_FLAGS,
    "kind published with flag short_record": R_PUBLISHED,
    "kind computed with flag provisional": R_COMPUTED,
    # truncated_lows goes with the direct method, which a computed level never has.
    "kind computed with flag truncated_lows (on otc_mhw)": either(R_COMPUTED, R_FLAG_DIRECT),
    "an empty source_name": R_SOURCE_NAME,
    "a source_name of 32 characters": R_SOURCE_NAME,
}
EXPECTED_RULES.update({f"a data_span date {v}": R_DATE for v in
                       ("2020-13-01", "2020-01-32", "2020-00-10", "2020-01-00", "20201-01-01", "x2020-01-01", "2020-1-01")})

container_validators = {
    "constant_set": Draft202012Validator(dict(schema["$defs"]["constant_set"]["properties"]["datum"],
                                              **{"$schema": schema["$schema"], "$defs": schema["$defs"]}),
                                         format_checker=FORMATS),
    "subordinate_offsets": Draft202012Validator(dict(schema["$defs"]["subordinate_offsets"]["properties"]["datum"],
                                                     **{"$schema": schema["$schema"], "$defs": schema["$defs"]}),
                                                format_checker=FORMATS),
}


def leaves(error):
    if error.context:
        for child in error.context:
            yield from leaves(child)
    else:
        yield error


def datum_error_paths(doc, with_keys=False):
    """The schema path of every leaf error of every datum in doc, relative to its container.
    With with_keys, (path, basis key) pairs; the key is None for an error not inside one basis."""
    found = []
    for st in doc["stations"]:
        datums = [("constant_set", cs["datum"]) for cs in st.get("constant_sets", []) if "datum" in cs]
        if "datum" in st.get("subordinate_offsets", {}):
            datums.append(("subordinate_offsets", st["subordinate_offsets"]["datum"]))
        for container, datum in datums:
            for error in container_validators[container].iter_errors(datum):
                for leaf in leaves(error):
                    where = list(leaf.absolute_path)
                    key = where[1] if len(where) > 1 and where[0] == "basis" else None
                    found.append(("/".join(map(str, leaf.absolute_schema_path)), key))
    return found if with_keys else [path for path, _ in found]


def datum_rule_problem(doc, rule):
    """None when every leaf datum error is under rule and on at most one basis key; else why not.
    One key: the controls change one basis each, so errors on other keys mean the control is
    rejected by the same rule elsewhere (a mask the rule check alone cannot see)."""
    found = datum_error_paths(doc, with_keys=True)
    if not found:
        return "no datum error"
    other = [path for path, _ in found if not re.search(rule, path)]
    if other:
        return f"rejected by another rule: {other[:2]}"
    keys = sorted({key for _, key in found if key is not None})
    if len(keys) > 1:
        return f"rejected on several basis keys: {keys}"
    return None


def datum_must_fail(label, mutate, rule=None):
    doc = copy.deepcopy(example)
    mutate(doc)
    if not problems(doc):
        datum_failures.append(f"must fail, was accepted: {label}")
        print(f"FAIL: must fail, was accepted: {label}")
        return
    rule = rule or EXPECTED_RULES.get(label)
    if rule is None:
        datum_failures.append(f"must fail without a rule: {label}")
        print(f"RULE? {label} :: {sorted(set(datum_error_paths(doc)))}")
        return
    why = datum_rule_problem(doc, rule)
    if why:
        datum_failures.append(f"must fail, {why}: {label}")
        print(f"FAIL: must fail, {why}: {label}")
    else:
        print(f"ok: rejected {label}")


def datum_left_to_build(label, mutate):
    doc = copy.deepcopy(example)
    mutate(doc)
    if problems(doc):
        datum_failures.append(f"build-check case rejected by the schema: {label}")
        print(f"FAIL: {label} should be left to the build check: {short_problem(doc)}")
    else:
        print(f"ok: left to the build check: {label}")


def gauge_a(d):
    return station(d, "OTC-EXAMPLE-0005")["constant_sets"][0]


def put(target, datum):
    """Return a mutation that sets the datum of target(d) to a copy of datum."""
    return lambda d: target(d).__setitem__("datum", copy.deepcopy(datum))


def put_and_get(target, datum):
    """Like put, but the mutation returns the datum it set (a base for trim and edit)."""
    def f(d):
        put(target, datum)(d)
        return target(d)["datum"]
    return f


def with_subordinate(d, datum=SUBORDINATE_DATUM):
    d["stations"].append({
        "station_id": "OTC-EXAMPLE-9998", "status": "active", "name": "Datum control subordinate",
        "country": "USA", "lat": 41.7, "lon": -70.1, "timezone": "America/New_York", "type": "subordinate",
        "recommended_set_id": None, "constant_sets": [],
        "subordinate_offsets": {"reference_station_id": "OTC-EXAMPLE-0006", "height_offset_high": 0.94,
                                "height_offset_low": 0.94, "height_adjusted_type": "R",
                                "datum": copy.deepcopy(datum)}})


def noaa_on_first(d):
    first_set(d)["datum"] = copy.deepcopy(NOAA_DATUM)
    return first_set(d)["datum"]


def observed_on_a(d):
    gauge_a(d)["datum"] = copy.deepcopy(GESLA_OBSERVED_DATUM)
    return gauge_a(d)["datum"]


def edit(base, key, change):
    """Return a mutation: put a must-pass datum block in place, then change basis[key]."""
    def f(d):
        datum = base(d)
        change(datum["basis"][key])
    return f


def set_flags(*flags):
    return lambda b: b.__setitem__("flags", list(flags))


HIGH = ("mhw", "mhhw")


def trim(base, keep=HIGH):
    """Return a base that keeps only the keep levels: a truncated record has no low-water levels
    in its datum (round 4), so controls about the direct method and truncated_lows start here."""
    def f(d):
        datum = base(d)
        for part in ("named", "basis"):
            for k in [k for k in datum[part] if k not in keep]:
                datum[part].pop(k)
        return datum
    return f


def high_on_a(d):
    return trim(observed_on_a)(d)


def high_noaa(d):
    return trim(noaa_on_first, ("mhhw",))(d)


def own_months(start, end, months, flags=("no_qualified_control",)):
    """Return a mutation: fjord set a gets RWS_DATUM, whose msl is a first reduction over start..end."""
    def f(d):
        put(gauge_a, RWS_DATUM)(d)
        b = gauge_a(d)["datum"]["basis"]["msl"]
        b.update(epoch={"start": start, "end": end}, data_span={"start": start, "end": end, "months": months})
        if flags:
            b["flags"] = list(flags)
        else:
            b.pop("flags", None)
    return f


def on_subordinate(key, change):
    def f(d):
        with_subordinate(d)
        change(d["stations"][-1]["subordinate_offsets"]["datum"]["basis"][key])
    return f


OBSERVED_228 = unalias({"kind": "observed", "method": "first_reduction", "epoch": OTC_EPOCH,
                        "data_span": {"start": "2002-01-01", "end": "2020-12-31", "months": 228},
                        "uncertainty_m": 0.004, "uncertainty_basis": "propagated"})



# Must pass (§3.8 of the datums spec).
datum_must_pass("a NOAA-style set (zero MLLW, published mllw, mhhw and lat with control.source_id, computed otc_lat)",
                noaa_on_first)
datum_must_pass("a GESLA set with observed levels by modified_range_ratio (control station_id and set_id)",
                observed_on_a)
datum_must_pass("a 19-year primary determination (first_reduction over the OTC epoch, no control)",
                put(first_set, PRIMARY_DATUM))
datum_must_pass("subordinate_offsets.datum with published MLLW and LAT/HAT computed by subordinate_offsets",
                with_subordinate)
datum_must_pass("an RWS set with zero vertical_datum, zero_name NAP, and a labelled own-month MSL",
                lambda d: [gauge_a(d).__setitem__("source", "rws"), put(gauge_a, RWS_DATUM)(d)])
datum_must_pass("a published lat with no epoch (the Kartverket set; its msl has Kartverket's 1996-2014 epoch)", put(official, KARTVERKET_DATUM))
datum_must_pass("an observed level with flags time_base_unverified, sampling_assumed, gaps and segment",
                edit(observed_on_a, "mhw", set_flags("time_base_unverified", "sampling_assumed", "gaps", "segment")))
datum_must_pass("an observed MHW by the direct method with truncated_lows",
                edit(high_on_a, "mhw", lambda b: [b.__setitem__("method", "direct"),
                                                      b.__setitem__("flags", ["truncated_lows"])]))
datum_must_pass("a computed short_record LAT", edit(noaa_on_first, "otc_lat", set_flags("short_record", "microtidal")))
datum_must_pass("a provisional published level (JMA style)", edit(noaa_on_first, "msl", set_flags("provisional")))
datum_must_pass("a published lat with control.source_id (a NOAA reference station that has a control)",
                edit(noaa_on_first, "lat", lambda b: b.__setitem__("control", dict(BOSTON_CONTROL))))
datum_must_pass("a NOAA published control naming an OTC station as well",
                edit(noaa_on_first, "lat", lambda b: b.__setitem__("control", dict(BOSTON_CONTROL, station_id="OTC-EXAMPLE-0001"))))
datum_must_pass("a short own-month reduction (under 12 months, short_record)",
                lambda d: [put(gauge_a, RWS_DATUM)(d),
                           gauge_a(d)["datum"]["basis"]["msl"].update(
                               epoch={"start": "2024-01-01", "end": "2024-06-30"},
                               data_span={"start": "2024-01-01", "end": "2024-06-30", "months": 6},
                               flags=["no_qualified_control", "short_record"])])
datum_must_pass("datum.zero vertical_datum", lambda d: first_set(d)["datum"].__setitem__("zero", "vertical_datum"))

# Must fail (§3.8 of the datums spec), each one change from a block that passes.
datum_must_fail("named without basis (the PR #45 shape: zero, chart_datum and named only)",
                put(first_set, {"zero": "chart_datum", "chart_datum": "mllw", "named": {"mllw": 0.0, "msl": 1.554}}))
datum_must_fail("named without basis", lambda d: noaa_on_first(d).pop("basis"))
datum_must_fail("basis without named", lambda d: noaa_on_first(d).pop("named"))
datum_must_fail("subordinate_offsets.datum named without basis",
                lambda d: with_subordinate(d, {k: v for k, v in SUBORDINATE_DATUM.items() if k != "basis"}))
datum_must_fail("kind published with method first_reduction (no control)",
                edit(noaa_on_first, "mllw", lambda b: [b.pop("control", None), b.pop("epoch"), b.__setitem__("method", "first_reduction")]))
datum_must_fail("kind published with method harmonic_extremes",
                edit(noaa_on_first, "mllw", lambda b: b.__setitem__("method", "harmonic_extremes")))
datum_must_fail("kind published with data_span",
                edit(noaa_on_first, "mllw", lambda b: b.__setitem__("data_span", {"start": "1983-01-01", "end": "2001-12-31", "months": 228})))
datum_must_fail("kind published with flag truncated_lows (on mhhw, a high-water level)", edit(high_noaa, "mhhw", set_flags("truncated_lows")))
datum_must_fail("kind computed with method source", edit(noaa_on_first, "otc_lat", lambda b: b.__setitem__("method", "source")))
datum_must_fail("kind computed with control",
                edit(noaa_on_first, "otc_lat", lambda b: b.__setitem__("control", {"source_id": "8443970"})))
datum_must_fail("kind computed with data_span",
                edit(noaa_on_first, "otc_lat", lambda b: b.__setitem__("data_span", {"start": "2020-01-01", "end": "2020-12-31", "months": 12})))
datum_must_fail("kind computed without epoch", edit(noaa_on_first, "otc_lat", lambda b: b.pop("epoch")))
datum_must_fail("kind computed without uncertainty_m",
                edit(noaa_on_first, "otc_lat", lambda b: [b.pop("uncertainty_m"), b.pop("uncertainty_basis")]))
datum_must_fail("kind computed with flag gaps", edit(noaa_on_first, "otc_lat", set_flags("gaps")))
datum_must_fail("kind observed with method harmonic_extremes",
                edit(observed_on_a, "msl", lambda b: [b.__setitem__("method", "harmonic_extremes"), b.pop("control")]))
datum_must_fail("kind observed with method subordinate_offsets (on subordinate_offsets.datum, where the method is allowed)",
                on_subordinate("mhhw", lambda b: [b.clear(), b.update(copy.deepcopy(OBSERVED_228), method="subordinate_offsets")]))
datum_must_fail("kind observed without epoch", edit(observed_on_a, "msl", lambda b: b.pop("epoch")))
datum_must_fail("kind observed without data_span", edit(observed_on_a, "msl", lambda b: b.pop("data_span")))
datum_must_fail("kind observed without uncertainty_m",
                edit(observed_on_a, "msl", lambda b: [b.pop("uncertainty_m"), b.pop("uncertainty_basis")]))
datum_must_fail("kind observed with flag provisional", edit(observed_on_a, "msl", set_flags("provisional")))
datum_must_fail("uncertainty_m without uncertainty_basis", edit(observed_on_a, "msl", lambda b: b.pop("uncertainty_basis")))
datum_must_fail("uncertainty_basis without uncertainty_m", edit(noaa_on_first, "mllw", lambda b: b.__setitem__("uncertainty_basis", "source")))
datum_must_fail("data_span without months", edit(observed_on_a, "msl", lambda b: b["data_span"].pop("months")))
datum_must_fail("method standard without control",
                edit(observed_on_a, "msl", lambda b: [b.__setitem__("method", "standard"), b.pop("control")]))
datum_must_fail("method standard with a control that has only source_id",
                edit(observed_on_a, "msl", lambda b: [b.__setitem__("method", "standard"),
                                                      b.__setitem__("control", {"source_id": "8443970"})]))
datum_must_fail("method standard without control.set_id",
                edit(observed_on_a, "msl", lambda b: [b.__setitem__("method", "standard"), b["control"].pop("set_id")]))
datum_must_fail("method first_reduction with control",
                edit(observed_on_a, "msl", lambda b: [b.__setitem__("method", "first_reduction"),
                                                      b["data_span"].__setitem__("months", 228)]))
datum_must_fail("a published control with only set_id",
                edit(noaa_on_first, "mllw", lambda b: b.__setitem__("control", {"set_id": "OTC-EXAMPLE-0001/gesla-fit"})))
datum_must_fail("method amplitude_ratio", edit(observed_on_a, "msl", lambda b: b.__setitem__("method", "amplitude_ratio")))
for label, key in (("Lat", "Lat"), ("mean_level", "mean_level"), ("of 32 characters", "a" * 32), ("ending in a newline", "lat\n")):
    datum_must_fail(f"a datum key {label}",
                    lambda d, k=key: [noaa_on_first(d)["named"].__setitem__(k, 0.0),
                                      first_set(d)["datum"]["basis"].__setitem__(k, noaa_published("X"))])
datum_must_fail("a basis key Lat with no named key Lat",
                lambda d: noaa_on_first(d)["basis"].__setitem__("Lat", noaa_published("LAT")))
datum_must_fail("chart_datum Mllw (not a datum key)", lambda d: noaa_on_first(d).__setitem__("chart_datum", "Mllw"))
datum_must_fail("zero geoid", lambda d: noaa_on_first(d).__setitem__("zero", "geoid"))
datum_must_fail("zero_name of 32 characters", lambda d: noaa_on_first(d).__setitem__("zero_name", "N" * 32))
datum_must_fail("an empty zero_name", lambda d: noaa_on_first(d).__setitem__("zero_name", ""))
datum_must_fail("a flag wrong_flag", edit(observed_on_a, "msl", set_flags("wrong_flag")))
datum_must_fail("an epoch date 2020-13-45",
                lambda d: [own_months("2023-01-01", "2024-12-31", 24)(d), gauge_a(d)["datum"]["basis"]["msl"]["epoch"].__setitem__("start", "2020-13-45")])
datum_must_fail("an epoch date x",
                lambda d: [own_months("2023-01-01", "2024-12-31", 24)(d), gauge_a(d)["datum"]["basis"]["msl"]["epoch"].__setitem__("end", "x")])
datum_must_fail("a data_span date with a trailing newline",
                edit(observed_on_a, "msl", lambda b: b["data_span"].__setitem__("start", "2007-01-01\n")))
datum_must_fail("an epoch without end", edit(observed_on_a, "msl", lambda b: b["epoch"].pop("end")))
datum_must_fail("data_span months 0",
                lambda d: [own_months("2024-01-01", "2024-06-30", 6, flags=("no_qualified_control", "short_record"))(d),
                           gauge_a(d)["datum"]["basis"]["msl"]["data_span"].__setitem__("months", 0)])
datum_must_fail("a negative uncertainty_m", edit(observed_on_a, "msl", lambda b: b.__setitem__("uncertainty_m", -0.01)))
datum_must_fail("a control station_id that is not an OTC id",
                edit(observed_on_a, "msl", lambda b: b["control"].__setitem__("station_id", "8443970")))
datum_must_fail("an unknown key in a basis", edit(observed_on_a, "msl", lambda b: b.__setitem__("amplitude_ratio", 0.9)))
# Own-month averages are labelled: a first reduction with fewer months than a primary (216) carries
# no_qualified_control, and that flag goes only with a first reduction.
datum_must_fail("an own-month first reduction (24 months) without the no_qualified_control flag",
                lambda d: [put(gauge_a, RWS_DATUM)(d), gauge_a(d)["datum"]["basis"]["msl"].pop("flags")])
datum_must_fail("no_qualified_control on a comparison (method modified_range_ratio)",
                edit(observed_on_a, "msl", set_flags("no_qualified_control")))

# --- PR #47 review round 1 ---------------------------------------------------------------------

# D1: the OTC datum epoch 2002-01-01 to 2020-12-31 is enforced for 19-year determinations and comparisons.
datum_must_fail("a comparison (modified_range_ratio) on the NTDE epoch 1983-2001",
                edit(observed_on_a, "msl", lambda b: b.__setitem__("epoch", dict(NTDE))))
datum_must_fail("a comparison (standard) with epoch end 2021-12-31",
                edit(observed_on_a, "msl", lambda b: [b.__setitem__("method", "standard"),
                                                      b["epoch"].__setitem__("end", "2021-12-31")]))
datum_must_fail("a comparison (direct) with epoch start 2001-01-01",
                edit(high_on_a, "mhw", lambda b: [b.update(method="direct", flags=["truncated_lows"]),
                                                      b["epoch"].__setitem__("start", "2001-01-01")]))
datum_must_fail("an unflagged first_reduction of 228 months over 1950-1968 (not the OTC epoch)",
                own_months("1950-01-01", "1968-12-31", 228, flags=()))
datum_must_fail("an unflagged first_reduction of 228 months with epoch 2002-01-01 to 2021-12-31",
                lambda d: [put(first_set, PRIMARY_DATUM)(d),
                           first_set(d)["datum"]["basis"]["msl"]["epoch"].__setitem__("end", "2021-12-31")])
datum_must_pass("a long out-of-epoch record averaged over its own months (1950-1968, no_qualified_control)",
                own_months("1950-01-01", "1968-12-31", 228))
datum_must_pass("an unflagged first_reduction of exactly 216 months over the OTC epoch",
                lambda d: [put(first_set, PRIMARY_DATUM)(d),
                           first_set(d)["datum"]["basis"]["msl"]["data_span"].__setitem__("months", 216)])
datum_must_fail("an unflagged first_reduction of 215 months over the OTC epoch",
                lambda d: [put(first_set, PRIMARY_DATUM)(d),
                           first_set(d)["datum"]["basis"]["msl"]["data_span"].__setitem__("months", 215)])
# D2: uncertainty_basis follows the kind.
datum_must_fail("a published level with uncertainty_basis calibrated",
                edit(noaa_on_first, "mllw", lambda b: b.update(uncertainty_m=0.01, uncertainty_basis="calibrated")))
datum_must_fail("a published level with uncertainty_basis propagated",
                edit(noaa_on_first, "mllw", lambda b: b.update(uncertainty_m=0.01, uncertainty_basis="propagated")))
datum_must_fail("an observed level with uncertainty_basis source",
                edit(observed_on_a, "msl", lambda b: b.__setitem__("uncertainty_basis", "source")))
datum_must_fail("a computed level with uncertainty_basis source",
                edit(noaa_on_first, "otc_lat", lambda b: b.__setitem__("uncertainty_basis", "source")))
datum_must_pass("a published level with its source's own uncertainty (uncertainty_basis source)",
                edit(noaa_on_first, "mllw", lambda b: b.update(uncertainty_m=0.01, uncertainty_basis="source")))
datum_must_pass("a computed LAT with a propagated uncertainty",
                edit(noaa_on_first, "otc_lat", lambda b: b.__setitem__("uncertainty_basis", "propagated")))
# A-F3: methods follow the container.
datum_must_fail("a subordinate station's lat by harmonic_extremes",
                on_subordinate("lat", lambda b: b.__setitem__("method", "harmonic_extremes")))
datum_must_fail("a subordinate station's level of kind observed (first_reduction over the OTC epoch)",
                on_subordinate("mhhw", lambda b: [b.clear(), b.update(copy.deepcopy(OBSERVED_228))]))
datum_must_fail("a constant set's otc_lat by subordinate_offsets",
                edit(noaa_on_first, "otc_lat", lambda b: b.__setitem__("method", "subordinate_offsets")))
# A-F4: an otc_ key is never published.
datum_must_fail("an otc_lat of kind published", lambda d: noaa_on_first(d)["basis"].__setitem__("otc_lat", noaa_published("LAT")))
datum_must_pass("an otc_msl of kind observed next to a published msl",
                lambda d: [noaa_on_first(d)["named"].__setitem__("otc_msl", 1.56),
                           first_set(d)["datum"]["basis"].__setitem__("otc_msl", copy.deepcopy(OBSERVED_228))])
# A-F7 and round 2: by the direct method (with truncated_lows), only mhw and mhhw, by allow-list.
DIRECT = {"method": "direct", "flags": ["truncated_lows"]}
# Every key but mhw and mhhw (and their otc_ forms): the canonical names, their otc_ forms, the
# source names of build check 10, and a few that differ from mhw or mhhw by one part.
HIGH_ONLY_REJECTED = (["lat", "hat", "msl", "mtl", "dtl", "mlw", "mllw"]
                      + ["otc_" + k for k in ("lat", "hat", "msl", "mtl", "dtl", "mlw", "mllw")]
                      + ["mhws", "mlws", "mhwn", "mlwn", "mllws", "stnd", "navd88", "nn2000", "nhn", "n2000",
                         "rh2000", "dvr90", "mw", "cd", "tide_table_datum", "tp", "lmsl",
                         "otc_otc_mhw", "mhw2", "xmhw", "mhhws", "otc_mhws"])
for key in HIGH_ONLY_REJECTED:
    datum_must_fail(f"{key} by the direct method with truncated_lows",
                    lambda d, k=key: [high_on_a(d),
                                      gauge_a(d)["datum"]["named"].__setitem__(k, 0.5),
                                      gauge_a(d)["datum"]["basis"].__setitem__(k, gesla_observed(**DIRECT))],
                    # An observed key outside the truncated-datum allow-list (mhw, mhhw, hat and otc_
                    # forms) that carries truncated_lows breaks both rules by design; hat and otc_hat
                    # are the keys that test the key rule alone.
                    R_HIGH_ONLY if key in ("hat", "otc_hat") else either(R_HIGH_ONLY, R_TRUNCATED_DATUM))
for key in ("otc_mhw", "otc_mhhw"):
    datum_must_pass(f"{key} by the direct method with truncated_lows",
                    lambda d, k=key: [high_noaa(d)["named"].__setitem__(k, 3.0),
                                      first_set(d)["datum"]["basis"].__setitem__(k, gesla_observed(**DIRECT))])
datum_must_pass("mhhw by the direct method with truncated_lows", edit(high_on_a, "mhhw", lambda b: b.update(method="direct", flags=["truncated_lows"])))

# Reviewer C coverage: one change per constraint that existed but had no control of its own.
datum_must_fail("method modified_range_ratio without control", edit(observed_on_a, "msl", lambda b: b.pop("control")))
datum_must_fail("method direct without control",
                edit(high_on_a, "mhw", lambda b: [b.update(DIRECT), b.pop("control")]))
datum_must_fail("a comparison control with set_id and source_id but no station_id",
                edit(observed_on_a, "msl", lambda b: b.__setitem__("control", {"set_id": "OTC-EXAMPLE-0001/gesla-fit", "source_id": "x"})))
for bad in ("2020-13-01", "2020-01-32", "2020-00-10", "2020-01-00", "20201-01-01", "x2020-01-01", "2020-1-01"):
    datum_must_fail(f"a data_span date {bad}", edit(observed_on_a, "msl", lambda b, v=bad: b["data_span"].__setitem__("start", v)))
datum_must_fail("kind estimated", edit(observed_on_a, "msl", lambda b: b.__setitem__("kind", "estimated")))
datum_must_fail("kind computed with method first_reduction (on the OTC epoch)",
                edit(noaa_on_first, "otc_lat", lambda b: b.update(method="first_reduction", epoch=dict(OTC_EPOCH))))
datum_must_fail("kind observed with method source",
                edit(observed_on_a, "msl", lambda b: [b.__setitem__("method", "source"), b.pop("control")]))
datum_must_fail("an epoch without start", edit(observed_on_a, "msl", lambda b: b["epoch"].pop("start")))
datum_must_fail("a data_span without start", edit(observed_on_a, "msl", lambda b: b["data_span"].pop("start")))
datum_must_fail("a data_span without end", edit(observed_on_a, "msl", lambda b: b["data_span"].pop("end")))
datum_must_fail("a key Lat in named only (no basis key)", lambda d: noaa_on_first(d)["named"].__setitem__("Lat", 0.0))
for label, key in (("1lat", "1lat"), ("_lat", "_lat"), ("lAt", "lAt"), ("od-malin", "od-malin")):
    datum_must_fail(f"a datum key {label}",
                    lambda d, k=key: [noaa_on_first(d)["named"].__setitem__(k, 0.0),
                                      first_set(d)["datum"]["basis"].__setitem__(k, noaa_published("X"))])
datum_must_fail("a control station_id with a trailing newline",
                edit(observed_on_a, "msl", lambda b: b["control"].__setitem__("station_id", "OTC-EXAMPLE-0001\n")))
datum_must_fail("uncertainty_basis guess", edit(observed_on_a, "msl", lambda b: b.__setitem__("uncertainty_basis", "guess")))
datum_must_fail("uncertainty_m as a string", edit(observed_on_a, "msl", lambda b: b.__setitem__("uncertainty_m", "0.008")))
datum_must_fail("data_span months 12.5", edit(observed_on_a, "msl", lambda b: b["data_span"].__setitem__("months", 12.5)))
datum_must_fail("a repeated flag", edit(observed_on_a, "msl", set_flags("gaps", "gaps")))
datum_must_fail("kind published with flag short_record", edit(noaa_on_first, "mllw", set_flags("short_record")))
datum_must_fail("kind computed with flag provisional", edit(noaa_on_first, "otc_lat", set_flags("provisional")))
datum_must_fail("kind computed with flag truncated_lows (on otc_mhw)",
                lambda d: [high_noaa(d)["named"].__setitem__("otc_mhw", 2.9),
                           first_set(d)["datum"]["basis"].__setitem__("otc_mhw", dict(NOAA_DATUM["basis"]["otc_lat"], flags=["truncated_lows"]))])
datum_must_fail("an empty source_name", edit(noaa_on_first, "mllw", lambda b: b.__setitem__("source_name", "")))
datum_must_fail("a source_name of 32 characters", edit(noaa_on_first, "mllw", lambda b: b.__setitem__("source_name", "M" * 32)))
datum_must_fail("an empty control.set_id", edit(observed_on_a, "msl", lambda b: b["control"].__setitem__("set_id", "")))
datum_must_fail("an empty control.source_id",
                on_subordinate("mllw", lambda b: b.__setitem__("control", {"source_id": ""})))
datum_must_fail("an unknown key in a control", edit(observed_on_a, "msl", lambda b: b["control"].__setitem__("distance_km", 5)))
datum_must_fail("an unknown key in an epoch", edit(observed_on_a, "msl", lambda b: b["epoch"].__setitem__("note", "x")))
datum_must_fail("an unknown key in a data_span", edit(observed_on_a, "msl", lambda b: b["data_span"].__setitem__("days", 6000)))
datum_must_fail("an empty epoch name", edit(observed_on_a, "msl", lambda b: b["epoch"].__setitem__("name", "")))
datum_must_fail("an unknown key in a datum", lambda d: noaa_on_first(d).__setitem__("note", "x"))
datum_must_fail("msl_offset_m as a string", lambda d: noaa_on_first(d).__setitem__("msl_offset_m", "1.554"))
datum_must_fail("a named level as a string", lambda d: noaa_on_first(d)["named"].__setitem__("msl", "1.554"))
datum_must_pass("an observed level by the standard method", edit(observed_on_a, "msl", lambda b: b.__setitem__("method", "standard")))
for flag in ("time_base_unverified", "time_base_disputed", "sampling_assumed"):
    datum_must_pass(f"a computed LAT with flag {flag}", edit(noaa_on_first, "otc_lat", set_flags(flag)))
datum_must_pass("an observed level with flag time_base_disputed", edit(observed_on_a, "msl", set_flags("time_base_disputed")))
datum_must_pass("a key of 31 characters",
                lambda d: [noaa_on_first(d)["named"].__setitem__("a" * 31, 0.0),
                           first_set(d)["datum"]["basis"].__setitem__("a" * 31, noaa_published("X"))])
datum_must_pass("keys with digits (navd88, nn2000)",
                lambda d: [noaa_on_first(d)["named"].update(navd88=0.1, nn2000=0.2),
                           first_set(d)["datum"]["basis"].update(navd88=noaa_published("NAVD88"), nn2000=noaa_published("NN2000"))])
for zero in ("msl", "unknown", "gauge_zero"):
    datum_must_pass(f"subordinate_offsets.datum with zero {zero}",
                    lambda d, z=zero: [with_subordinate(d), d["stations"][-1]["subordinate_offsets"]["datum"].__setitem__("zero", z)])

# --- PR #47 review round 2 ---------------------------------------------------------------------

def gauge_b(d):
    return station(d, "OTC-EXAMPLE-0005")["constant_sets"][1]


datum_must_pass("an SMHI set in RH 2000 with chart datum rh2000 (published rh2000 = 0, source_name RH 2000)",
                put(gauge_a, SMHI_DATUM))
datum_must_pass("an FMI set in N2000 with the older chart datum mw (source_name MSL, epoch 2023)",
                put(gauge_a, FMI_OLDER_DATUM))
datum_must_pass("a GESLA-style set whose stated zero is Admiralty Chart Datum (zero chart_datum, zero_name ACD, published cd = 0, no epoch)",
                put(gauge_b, GESLA_CD_DATUM))
datum_must_fail("an unflagged first_reduction with epoch start 2001-01-01 (end 2020-12-31)",
                lambda d: [put(first_set, PRIMARY_DATUM)(d),
                           first_set(d)["datum"]["basis"]["msl"]["epoch"].__setitem__("start", "2001-01-01")],
                R_NINETEEN)
datum_must_fail("a comparison on epoch start 1983-01-01 (end 2020-12-31)",
                edit(observed_on_a, "msl", lambda b: b["epoch"].__setitem__("start", "1983-01-01")), R_COMPARISON)
datum_must_fail("a comparison on epoch end 2001-12-31 (start 2002-01-01)",
                edit(observed_on_a, "msl", lambda b: b["epoch"].__setitem__("end", "2001-12-31")), R_COMPARISON)
SUBORDINATE_HIGH = unalias({k: v for k, v in SUBORDINATE_DATUM.items() if k not in ("named", "basis", "chart_datum")})
SUBORDINATE_HIGH.update(named={"mhhw": SUBORDINATE_DATUM["named"]["mhhw"], "hat": SUBORDINATE_DATUM["named"]["hat"]},
                        basis=unalias({"mhhw": SUBORDINATE_DATUM["basis"]["mhhw"], "hat": SUBORDINATE_DATUM["basis"]["hat"]}))
for method, extra in (("standard", {}), ("modified_range_ratio", {}), ("direct", {"flags": ["truncated_lows"]})):
    datum_must_fail(f"a subordinate station's mhhw observed by {method}",
                    lambda d, m=method, x=extra: [with_subordinate(d, SUBORDINATE_HIGH),
                                                  d["stations"][-1]["subordinate_offsets"]["datum"]["basis"]["mhhw"].clear(),
                                                  d["stations"][-1]["subordinate_offsets"]["datum"]["basis"]["mhhw"].update(gesla_observed(method=m, **x))],
                    R_ONLY_SUBORDINATE_METHODS)
for key in ("hat", "mhhw"):
    datum_must_fail(f"a constant set's {key} computed by subordinate_offsets",
                    edit(noaa_on_first, key, lambda b: [b.clear(), b.update(kind="computed", method="subordinate_offsets",
                                                                             epoch=dict(LAT_WINDOW), uncertainty_m=0.03,
                                                                             uncertainty_basis="calibrated")]),
                    R_NO_SUBORDINATE_METHOD)
for key in ("otc_msl", "otc_mhw", "otc_hat"):
    datum_must_fail(f"an {key} of kind published",
                    lambda d, k=key: [noaa_on_first(d)["named"].__setitem__(k, 1.0),
                                      first_set(d)["datum"]["basis"].__setitem__(k, noaa_published("X"))],
                    R_OTC)
datum_must_fail("mhw by the direct method without truncated_lows",
                edit(high_on_a, "mhw", lambda b: b.__setitem__("method", "direct")), R_DIRECT_FLAG)
datum_must_fail("mhw by the direct method with flags gaps only (no truncated_lows)",
                edit(high_on_a, "mhw", lambda b: b.update(method="direct", flags=["gaps"])), R_DIRECT_FLAG)
datum_must_fail("mhw by the standard method with truncated_lows",
                edit(high_on_a, "mhw", lambda b: b.update(method="standard", flags=["truncated_lows"])), R_FLAG_DIRECT)
datum_must_fail("an unflagged first_reduction with epoch start 1950-01-01 (end 2020-12-31)",
                lambda d: [put(first_set, PRIMARY_DATUM)(d),
                           first_set(d)["datum"]["basis"]["msl"]["epoch"].__setitem__("start", "1950-01-01")],
                R_NINETEEN)
datum_must_fail("an own-month average of 11 months without short_record",
                own_months("2024-01-01", "2024-11-30", 11), R_SHORT)
datum_must_fail("mhw by modified_range_ratio with truncated_lows",
                edit(high_on_a, "mhw", set_flags("truncated_lows")), R_FLAG_DIRECT)
datum_must_fail("a comparison of 214 months with short_record", edit(observed_on_a, "msl", set_flags("short_record")), R_SHORT)
datum_must_fail("a 19-year determination with short_record",
                lambda d: [put(first_set, PRIMARY_DATUM)(d), first_set(d)["datum"]["basis"]["msl"].__setitem__("flags", ["short_record"])],
                R_SHORT)
datum_must_fail("an own-month average of 6 months without short_record",
                own_months("2024-01-01", "2024-06-30", 6), R_SHORT)
datum_must_fail("an own-month average of 12 months with short_record",
                own_months("2024-01-01", "2024-12-31", 12, flags=("no_qualified_control", "short_record")), R_SHORT)
datum_must_pass("an own-month average of 11 months with short_record",
                own_months("2024-01-01", "2024-11-30", 11, flags=("no_qualified_control", "short_record")))
datum_must_pass("an own-month average of 12 months without short_record", own_months("2024-01-01", "2024-12-31", 12))
datum_must_fail("an own-month average of 229 months (more than the latest 19 years)",
                own_months("1990-01-01", "2009-01-31", 229), R_OWN_MONTH)
datum_must_fail("a published control with station_id only (no source_id)",
                on_subordinate("mllw", lambda b: b.__setitem__("control", {"station_id": "OTC-EXAMPLE-0006"})), R_PUBLISHED)
datum_must_fail("a published control with station_id and set_id (comparison style)",
                on_subordinate("mllw", lambda b: b.__setitem__("control", {"station_id": "OTC-EXAMPLE-0006",
                                                                           "set_id": "OTC-EXAMPLE-0006/noaa"})), R_PUBLISHED)
datum_must_pass("a subordinate's published level with no control",
                on_subordinate("mllw", lambda b: b.pop("control")))

# --- PR #47 review round 3 ---------------------------------------------------------------------

# The empty-paths guard: a document rejected only outside every datum is not a datum control.
_outside = copy.deepcopy(example)
station(_outside, "OTC-EXAMPLE-0001").pop("timezone")
if not problems(_outside) or datum_rule_problem(_outside, R_DEP) != "no datum error":
    datum_failures.append("the which-rule check accepts a document rejected outside every datum")
    print("FAIL: the which-rule check accepts a document rejected outside every datum")
else:
    print("ok: the which-rule check reports a document rejected outside every datum")

# Orchestrator decision 2026-10-09 (PR #47 round 3, A-F1): truncated_lows also goes with a first
# reduction (own-month or 19-year); with the flag, only mhw and mhhw (and otc_ forms).
OWN_MONTH_MHW = {"msl_offset_m": 1.1, "zero": "gauge_zero", "named": {"mhw": 1.8, "mhhw": 1.9},
                 "basis": {k: {"kind": "observed", "method": "first_reduction",
                               "epoch": {"start": "2023-01-01", "end": "2024-12-31"},
                               "data_span": {"start": "2023-01-01", "end": "2024-12-31", "months": 24},
                               "uncertainty_m": 0.03, "uncertainty_basis": "calibrated",
                               "flags": ["no_qualified_control", "truncated_lows"]} for k in ("mhw", "mhhw")}}
datum_must_pass("a 19-year determination of mhw with truncated_lows",
                lambda d: [trim(put_and_get(first_set, PRIMARY_DATUM))(d), first_set(d)["datum"]["basis"]["mhw"].__setitem__("flags", ["truncated_lows"])])
datum_must_pass("own-month averages of mhw and mhhw with truncated_lows", put(gauge_a, OWN_MONTH_MHW))
datum_must_pass("a 19-year determination of mhhw with truncated_lows",
                lambda d: [trim(put_and_get(first_set, PRIMARY_DATUM))(d), first_set(d)["datum"]["basis"]["mhhw"].__setitem__("flags", ["truncated_lows"])])
datum_must_fail("an own-month average of mlw with truncated_lows",
                lambda d: [put(gauge_a, OWN_MONTH_MHW)(d), gauge_a(d)["datum"]["named"].__setitem__("mlw", 0.4),
                           gauge_a(d)["datum"]["basis"].__setitem__("mlw", copy.deepcopy(OWN_MONTH_MHW["basis"]["mhw"]))],
                either(R_HIGH_ONLY, R_TRUNCATED_DATUM))
for key in ("mlw", "mllw", "msl", "mtl", "dtl", "otc_mlw"):
    datum_must_fail(f"a 19-year determination of {key} with truncated_lows",
                    lambda d, k=key: [trim(put_and_get(first_set, PRIMARY_DATUM))(d),
                                      first_set(d)["datum"]["named"].__setitem__(k, 0.5),
                                      first_set(d)["datum"]["basis"].__setitem__(k, dict(copy.deepcopy(OBSERVED_228), flags=["truncated_lows"]))],
                    either(R_HIGH_ONLY, R_TRUNCATED_DATUM))
# hat and otc_hat are inside the truncated-datum allow-list, so a first reduction with truncated_lows
# on them reaches only the key rule; mhws and cd reach both rules by design.
for key in ("hat", "mhws", "cd", "otc_hat"):
    datum_must_fail(f"a 19-year determination of {key} with truncated_lows",
                    lambda d, k=key: [trim(put_and_get(first_set, PRIMARY_DATUM))(d),
                                      first_set(d)["datum"]["named"].__setitem__(k, 2.0),
                                      first_set(d)["datum"]["basis"].__setitem__(k, dict(copy.deepcopy(OBSERVED_228), flags=["truncated_lows"]))],
                    R_HIGH_ONLY if key in ("hat", "otc_hat") else either(R_HIGH_ONLY, R_TRUNCATED_DATUM))
datum_must_fail("an own-month average of msl with truncated_lows",
                own_months("2023-01-01", "2024-12-31", 24, flags=("no_qualified_control", "truncated_lows")),
                either(R_HIGH_ONLY, R_TRUNCATED_DATUM))
# A string flags makes every "contains" test pass, so with truncated_lows allowed on a first reduction
# the only rule left to reject it, on an own-month mhw under 12 months, is the flags type.
datum_must_fail("flags as a string on an own-month mhw of 6 months",
                lambda d: [put(gauge_a, OWN_MONTH_MHW)(d), gauge_a(d)["datum"]["basis"].pop("mhhw"), gauge_a(d)["datum"]["named"].pop("mhhw"),
                           gauge_a(d)["datum"]["basis"]["mhw"].update(flags="truncated_lows",
                                                                    data_span={"start": "2024-01-01", "end": "2024-06-30", "months": 6},
                                                                    epoch={"start": "2024-01-01", "end": "2024-06-30"})],
                R_FLAGS)
# Reviewer C round 3: no_qualified_control with each comparison method.
datum_must_fail("no_qualified_control on a comparison (method standard)",
                edit(observed_on_a, "msl", lambda b: b.update(method="standard", flags=["no_qualified_control"])), R_OWN_MONTH)
datum_must_fail("no_qualified_control on a comparison (method direct)",
                edit(high_on_a, "mhw", lambda b: b.update(method="direct", flags=["truncated_lows", "no_qualified_control"])), R_OWN_MONTH)

# --- PR #47 round 4 (orchestrator decisions on C-F4 and C-F8) -----------------------------------

# C-F4 (spec 3.5 note (1), an allow-list since C-F4/C-F8 review 2): when any level of a datum
# carries truncated_lows, its only computed or observed levels are mhw, mhhw, hat and their otc_
# forms; a published level of any key is kept; named has no other otc_ key.
GESLA_HIGH_DATUM = unalias({"msl_offset_m": 1.1, "zero": "gauge_zero",
                            "named": {"mhhw": 1.78, "mhw": 1.72},
                            "basis": {"mhhw": gesla_observed(method="direct", flags=["truncated_lows"]),
                                      "mhw": gesla_observed(method="direct", flags=["truncated_lows"])}})
datum_must_pass("a truncated record with only high-water levels (mhw, mhhw by the direct method)", put(gauge_a, GESLA_HIGH_DATUM))
LOW_WATER_KEYS = ["mlw", "mllw", "mtl", "dtl", "msl", "lat", "mlws", "mlwn", "mllws"]
for key in LOW_WATER_KEYS + ["otc_" + k for k in LOW_WATER_KEYS]:
    datum_must_fail(f"an observed {key} by modified_range_ratio next to a truncated mhw in one datum",
                    lambda d, k=key: [put(gauge_a, GESLA_HIGH_DATUM)(d), gauge_a(d)["datum"]["named"].__setitem__(k, 0.4),
                                      gauge_a(d)["datum"]["basis"].__setitem__(k, gesla_observed())],
                    R_TRUNCATED_DATUM)
# Keys that differ between named and basis are build check 9, so each half of the rule gets its own control.
datum_must_fail("mlw in basis only (not in named) next to a truncated mhw in one datum",
                lambda d: [put(gauge_a, GESLA_HIGH_DATUM)(d), gauge_a(d)["datum"]["basis"].__setitem__("mlw", gesla_observed())],
                R_TRUNCATED_DATUM)
datum_must_fail("otc_mlw in named only (not in basis) next to a truncated mhw in one datum",
                lambda d: [put(gauge_a, GESLA_HIGH_DATUM)(d), gauge_a(d)["datum"]["named"].__setitem__("otc_mlw", 0.4)],
                R_TRUNCATED_DATUM)
datum_must_fail("an observed mlw by the standard method next to a truncated mhw first reduction",
                lambda d: [trim(put_and_get(first_set, PRIMARY_DATUM), ("mhw",))(d),
                           first_set(d)["datum"]["basis"]["mhw"].__setitem__("flags", ["truncated_lows"]),
                           first_set(d)["datum"]["named"].__setitem__("mlw", 0.5),
                           first_set(d)["datum"]["basis"].__setitem__("mlw", gesla_observed(method="standard"))],
                R_TRUNCATED_DATUM)
datum_must_fail("a computed otc_lat next to a truncated mhw in one datum",
                lambda d: [put(gauge_a, GESLA_HIGH_DATUM)(d), gauge_a(d)["datum"]["named"].__setitem__("otc_lat", -0.1),
                           gauge_a(d)["datum"]["basis"].__setitem__("otc_lat", copy.deepcopy(NOAA_DATUM["basis"]["otc_lat"]))],
                R_TRUNCATED_DATUM)
# Published lows are kept at a truncated set: a PEGELONLINE-shape datum (published MThw, MTnw) with
# an observed otc_mhw first reduction carrying truncated_lows.
PEGEL_TRUNCATED = unalias({"msl_offset_m": 5.0, "zero": "gauge_zero", "zero_name": "PNP",
                           "named": {"mhw": 6.5, "mlw": 3.4, "otc_mhw": 6.48},
                           "basis": {"mhw": {"kind": "published", "method": "source", "source_name": "MThw",
                                             "epoch": {"start": "2010-11-01", "end": "2020-10-31"}},
                                     "mlw": {"kind": "published", "method": "source", "source_name": "MTnw",
                                             "epoch": {"start": "2010-11-01", "end": "2020-10-31"}},
                                     "otc_mhw": OWN_MONTH_MHW["basis"]["mhw"]}})
datum_must_pass("published mhw and mlw (PEGELONLINE MThw, MTnw) with an observed otc_mhw carrying truncated_lows",
                put(gauge_a, PEGEL_TRUNCATED))
datum_must_fail("the PEGELONLINE-shape truncated datum with an observed otc_mlw first reduction",
                lambda d: [put(gauge_a, PEGEL_TRUNCATED)(d), gauge_a(d)["datum"]["named"].__setitem__("otc_mlw", 3.38),
                           gauge_a(d)["datum"]["basis"].__setitem__("otc_mlw", dict(copy.deepcopy(OWN_MONTH_MHW["basis"]["mhw"]), flags=["no_qualified_control"]))],
                R_TRUNCATED_DATUM)
# Spec 3.8 must-passes not shown elsewhere in this section.
datum_must_pass("an mhw own-month first reduction of 12 months (2015) with no_qualified_control and truncated_lows",
                lambda d: [put(gauge_a, OWN_MONTH_MHW)(d), [gauge_a(d)["datum"][part].pop("mhhw") for part in ("named", "basis")],
                           gauge_a(d)["datum"]["basis"]["mhw"].update(epoch={"start": "2015-01-01", "end": "2015-12-31"},
                                                                    data_span={"start": "2015-01-01", "end": "2015-12-31", "months": 12})])
datum_must_pass("a first reduction with no_qualified_control, 7 months, short_record, epoch equal to its data span",
                own_months("2024-01-01", "2024-07-31", 7, flags=("no_qualified_control", "short_record")))
datum_must_pass("a 6-month standard comparison with short_record",
                edit(observed_on_a, "msl", lambda b: [b.update(method="standard", flags=["short_record"]),
                                                      b["data_span"].update(start="2024-01-01", end="2024-06-30", months=6)]))
datum_must_pass("a truncated mhw with a published cd = 0 as chart_datum",
                lambda d: [put(gauge_a, GESLA_HIGH_DATUM)(d),
                           gauge_a(d)["datum"].update(zero="chart_datum", zero_name="CD", chart_datum="cd"),
                           gauge_a(d)["datum"]["named"].__setitem__("cd", 0.0),
                           gauge_a(d)["datum"]["basis"].__setitem__("cd", {"kind": "published", "method": "source", "source_name": "CD"})])
datum_must_fail("a computed lat next to a truncated 19-year mhw in one datum",
                lambda d: [put(first_set, PRIMARY_DATUM)(d),
                           [first_set(d)["datum"][part].pop(k) for k in ("mtl", "dtl", "msl", "mlw", "mllw") for part in ("named", "basis")],
                           first_set(d)["datum"]["basis"]["mhw"].__setitem__("flags", ["truncated_lows"]),
                           first_set(d)["datum"]["named"].__setitem__("lat", -0.1),
                           first_set(d)["datum"]["basis"].__setitem__("lat", copy.deepcopy(NOAA_DATUM["basis"]["otc_lat"]))],
                R_TRUNCATED_DATUM)
datum_must_pass("a truncated mhw and mhhw with a computed hat in one datum",
                lambda d: [put(gauge_a, GESLA_HIGH_DATUM)(d), gauge_a(d)["datum"]["named"].__setitem__("hat", 2.0),
                           gauge_a(d)["datum"]["basis"].__setitem__("hat", copy.deepcopy(NOAA_DATUM["basis"]["otc_lat"]))])
datum_must_pass("a truncated mhw with a computed otc_hat next to a published hat",
                lambda d: [put(gauge_a, GESLA_HIGH_DATUM)(d),
                           gauge_a(d)["datum"]["named"].update(hat=2.0, otc_hat=2.01),
                           gauge_a(d)["datum"]["basis"].update(hat={"kind": "published", "method": "source", "source_name": "HAT"},
                                                               otc_hat=copy.deepcopy(NOAA_DATUM["basis"]["otc_lat"]))])
# Review 2 bypasses of the old deny-list: any other computed or observed key at a truncated set.
OBS_FR = unalias({"kind": "observed", "method": "first_reduction", "epoch": OTC_EPOCH,
                  "data_span": {"start": "2002-01-01", "end": "2020-12-31", "months": 228},
                  "uncertainty_m": 0.01, "uncertainty_basis": "calibrated"})
COMPUTED_CD = unalias({"kind": "computed", "method": "harmonic_extremes", "epoch": LAT_WINDOW,
                       "uncertainty_m": 0.02, "uncertainty_basis": "calibrated"})
datum_must_fail("a truncated mhw with an observed mw first reduction as chart_datum",
                lambda d: [put(gauge_a, GESLA_HIGH_DATUM)(d), gauge_a(d)["datum"].__setitem__("chart_datum", "mw"),
                           gauge_a(d)["datum"]["named"].__setitem__("mw", 0.9), gauge_a(d)["datum"]["basis"].__setitem__("mw", copy.deepcopy(OBS_FR))],
                R_TRUNCATED_DATUM)
datum_must_fail("a truncated mhw with a computed cd (harmonic_extremes) as chart_datum",
                lambda d: [put(gauge_a, GESLA_HIGH_DATUM)(d), gauge_a(d)["datum"].__setitem__("chart_datum", "cd"),
                           gauge_a(d)["datum"]["named"].__setitem__("cd", 0.2), gauge_a(d)["datum"]["basis"].__setitem__("cd", copy.deepcopy(COMPUTED_CD))],
                R_TRUNCATED_DATUM)
datum_must_fail("a truncated mhw with a published cd = 0 and an observed otc_cd",
                lambda d: [put(gauge_a, GESLA_HIGH_DATUM)(d), gauge_a(d)["datum"].update(zero="chart_datum", chart_datum="cd"),
                           gauge_a(d)["datum"]["named"].update(cd=0.0, otc_cd=0.05),
                           gauge_a(d)["datum"]["basis"].update(cd={"kind": "published", "method": "source", "source_name": "CD"},
                                                               otc_cd=copy.deepcopy(OBS_FR))],
                R_TRUNCATED_DATUM)
for key in ("elw", "mlhw", "mhws"):
    datum_must_fail(f"a truncated mhw with an observed {key}",
                    lambda d, k=key: [put(gauge_a, GESLA_HIGH_DATUM)(d), gauge_a(d)["datum"]["named"].__setitem__(k, 0.6),
                                      gauge_a(d)["datum"]["basis"].__setitem__(k, copy.deepcopy(OBS_FR))],
                    R_TRUNCATED_DATUM)
datum_must_fail("a truncated mhw with a computed mhws",
                lambda d: [put(gauge_a, GESLA_HIGH_DATUM)(d), gauge_a(d)["datum"]["named"].__setitem__("mhws", 2.1),
                           gauge_a(d)["datum"]["basis"].__setitem__("mhws", copy.deepcopy(COMPUTED_CD))],
                R_TRUNCATED_DATUM)
datum_must_fail("a truncated mhw with an observed otc_cd in basis only (no named.otc_cd)",
                lambda d: [put(gauge_a, GESLA_HIGH_DATUM)(d), gauge_a(d)["datum"]["basis"].__setitem__("otc_cd", copy.deepcopy(OBS_FR))],
                R_TRUNCATED_DATUM)
datum_must_fail("a truncated mhw whose named has otc_elw (no basis.otc_elw)",
                lambda d: [put(gauge_a, GESLA_HIGH_DATUM)(d), gauge_a(d)["datum"]["named"].__setitem__("otc_elw", 0.6)],
                R_TRUNCATED_DATUM)
datum_must_pass("a truncated mhw whose named has otc_hat (no basis.otc_hat; build check 9)",
                lambda d: [put(gauge_a, GESLA_HIGH_DATUM)(d), gauge_a(d)["datum"]["named"].__setitem__("otc_hat", 2.0)])
# C-F8: sampling_assumed is not allowed on msl or otc_msl (MSL is a mean of all samples).
datum_must_fail("an observed msl with sampling_assumed", edit(observed_on_a, "msl", set_flags("sampling_assumed")), R_MSL_SAMPLING)
datum_must_fail("an observed otc_msl with sampling_assumed",
                lambda d: [noaa_on_first(d)["named"].__setitem__("otc_msl", 1.56),
                           first_set(d)["datum"]["basis"].__setitem__("otc_msl", dict(copy.deepcopy(OBSERVED_228), flags=["sampling_assumed"]))],
                R_MSL_SAMPLING)
for key in ("mtl", "dtl"):
    datum_must_fail(f"an observed {key} with sampling_assumed", edit(observed_on_a if key == "mtl" else put_and_get(first_set, PRIMARY_DATUM), key, set_flags("sampling_assumed")), R_MSL_SAMPLING)
datum_must_fail("an observed otc_mtl with sampling_assumed",
                lambda d: [noaa_on_first(d)["named"].__setitem__("otc_mtl", 1.56),
                           first_set(d)["datum"]["basis"].__setitem__("otc_mtl", dict(copy.deepcopy(OBSERVED_228), flags=["sampling_assumed"]))],
                R_MSL_SAMPLING)
datum_must_pass("an observed mhw, mlw and mllw with sampling_assumed",
                lambda d: [edit(observed_on_a, "mhw", set_flags("sampling_assumed"))(d)] +
                          [gauge_a(d)["datum"]["basis"][k].__setitem__("flags", ["sampling_assumed"]) for k in ("mlw", "mllw")])
datum_must_pass("an observed msl with time_base_unverified and no sampling_assumed",
                edit(observed_on_a, "msl", set_flags("time_base_unverified")))
datum_must_pass("a computed lat with sampling_assumed", edit(noaa_on_first, "otc_lat", set_flags("sampling_assumed")))

# Build checks 9-14 of the schema's description: valid JSON Schema on purpose, the release build stops them.
datum_left_to_build("named and basis with different keys (check 9)",
                    lambda d: noaa_on_first(d)["basis"].pop("hat"))
datum_left_to_build("a control set that is not in the release (check 11)",
                    edit(observed_on_a, "msl", lambda b: b["control"].__setitem__("set_id", "OTC-EXAMPLE-0001/nowhere")))
datum_left_to_build("a computed chart_datum on a set with a published chart datum (check 12)",
                    lambda d: noaa_on_first(d).__setitem__("chart_datum", "otc_lat"))
datum_left_to_build("the date 2021-02-30 (check 13)",
                    edit(observed_on_a, "msl", lambda b: b["data_span"].__setitem__("end", "2021-02-30")))
datum_left_to_build("an own-month epoch with end before start (check 13)",
                    own_months("2024-12-31", "2023-01-01", 24))
OBSERVED_MSL_CHART = {"msl_offset_m": 1.0, "zero": "gauge_zero", "chart_datum": "msl", "named": {"msl": 1.003},
                      "basis": {"msl": copy.deepcopy(OBSERVED_228)}}
datum_left_to_build("a GESLA set with an observed msl chart datum at a station whose SMHI set has a published rh2000 chart datum (check 12, per station)",
                    lambda d: [put(gauge_a, SMHI_DATUM)(d), put(gauge_b, OBSERVED_MSL_CHART)(d)])
datum_left_to_build("an own-month average with a primary's coverage (228 months over the OTC epoch; check 14)",
                    own_months("2002-01-01", "2020-12-31", 228))
datum_left_to_build("an own-month average of 13 months, not whole years (check 14)", own_months("2024-01-01", "2025-01-31", 13))
datum_left_to_build("a data_span of 300 months over 19 years (check 14)",
                    edit(observed_on_a, "msl", lambda b: b["data_span"].__setitem__("months", 300)))
def high_only_primary(d):
    """The control 0001/gesla-fit holds a truncated primary: only MHW and MHHW."""
    put(first_set, PRIMARY_DATUM)(d)
    datum = first_set(d)["datum"]
    for k in [k for k in datum["named"] if k not in ("mhw", "mhhw")]:
        datum["named"].pop(k)
        datum["basis"].pop(k)
    for k in ("mhw", "mhhw"):
        datum["basis"][k]["flags"] = ["truncated_lows"]


datum_left_to_build("a modified_range_ratio comparison whose control's primary has only MHW and MHHW (check 11)",
                    lambda d: [high_only_primary(d), observed_on_a(d)])
datum_left_to_build("a standard comparison whose control's primary has only MHW and MHHW (check 11)",
                    lambda d: [high_only_primary(d), observed_on_a(d),
                               [b.__setitem__("method", "standard") for b in gauge_a(d)["datum"]["basis"].values()]])
datum_left_to_build("a truncated mhw with named.mlw but no basis.mlw (check 9)",
                    lambda d: [put(gauge_a, GESLA_HIGH_DATUM)(d), gauge_a(d)["datum"]["named"].__setitem__("mlw", 0.4)])
datum_left_to_build("a data_span with end before start (check 13)",
                    edit(observed_on_a, "msl", lambda b: b["data_span"].update(start="2025-12-31", end="2007-01-01")))
datum_left_to_build("an own-month average whose epoch is not its data_span (check 14)",
                    lambda d: [own_months("2023-01-01", "2024-12-31", 24)(d),
                               gauge_a(d)["datum"]["basis"]["msl"].__setitem__("epoch", dict(OTC_EPOCH))])
datum_left_to_build("a 19-year determination whose data_span is outside the epoch (check 14)",
                    lambda d: [put(first_set, PRIMARY_DATUM)(d),
                               first_set(d)["datum"]["basis"]["msl"]["data_span"].update(start="1990-01-01", end="2015-12-31", months=216)])
datum_left_to_build("an otc_lat with kind computed and no published lat (check 10)",
                    lambda d: [noaa_on_first(d)["named"].pop("lat"), first_set(d)["datum"]["basis"].pop("lat")])

# example.json shows each must-pass block, unchanged.
def example_datum(station_id, set_suffix=None):
    st = next((s for s in example["stations"] if s["station_id"] == station_id), None)
    if st is None:
        return None
    if set_suffix is None:
        return st.get("subordinate_offsets", {}).get("datum")
    cs = next((c for c in st.get("constant_sets", []) if c["set_id"] == f"{station_id}/{set_suffix}"), None)
    return cs and cs.get("datum")


for label, station_id, set_suffix, block in (
        ("the NOAA-style set", "OTC-EXAMPLE-0006", "noaa", NOAA_DATUM),
        ("the subordinate station's offsets datum", "OTC-EXAMPLE-0007", None, SUBORDINATE_DATUM),
        ("the RWS set with zero NAP", "OTC-EXAMPLE-0008", "rws", RWS_DATUM),
        ("the GESLA set with observed levels", "OTC-EXAMPLE-0005", "gesla-fit-a", GESLA_OBSERVED_DATUM),
        ("the primary determination", "OTC-EXAMPLE-0001", "gesla-fit", PRIMARY_DATUM),
        ("the Kartverket set with a basis", "OTC-EXAMPLE-0001", "kartverket", KARTVERKET_DATUM),
        ("the SMHI set in RH 2000", "OTC-EXAMPLE-0009", "smhi", SMHI_DATUM),
        ("the FMI set with the older chart datum mw", "OTC-EXAMPLE-0010", "fmi", FMI_OLDER_DATUM),
        ("the UK GESLA record on Admiralty Chart Datum", "OTC-EXAMPLE-0011", "gesla-fit", GESLA_CD_DATUM)):
    if example_datum(station_id, set_suffix) != block:
        datum_failures.append(f"example.json does not show {label}")
        print(f"FAIL: example.json does not show {label} ({station_id})")
    else:
        print(f"ok: example.json shows {label}")

if datum_failures:
    print(f"FAIL: {len(datum_failures)} datum checks failed")
    sys.exit(1)
print("ok: every datum check")
