r"""Check the draft JSON Schema and the example document.

1. The current schema (0.6), the index schema and the older schemas are valid draft 2020-12 schemas.
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
                     current_set(d).__setitem__("qc_status", "fallback"),
                     current_set(d)["provenance"].__setitem__("carried_from_release", "20991130"),
                     current_set(d).__setitem__("qc_flags", [{"flag": "carried_over", "reason": "NOAA failed its check"}])])
must_pass("deprecated qc_flags verdict next to reason", lambda d: fjord(d, "b")["qc_flags"][1].__setitem__("verdict", "unverified"))
must_pass("deprecated provenance.time_base on an official set (no 0.6 time_base)",
          lambda d: d["stations"][0]["constant_sets"][1]["provenance"].__setitem__(
              "time_base", {"verdict": "utc_instant", "correction": "none"}))
must_pass("a concept DOI with a null release.doi",
          lambda d: d["release"].update(doi=None, concept_doi="10.5281/zenodo.1234566"))
must_pass("a release without release.doi", lambda d: d["release"].pop("doi"))


# --- 0.6 gate review: holes found in review (S1-S7, D1, D9, C1, C3) ---------------------------

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
must_fail("S5 0.6 time_base and the deprecated provenance.time_base together",
          lambda d: fjord(d, "a")["provenance"].__setitem__("time_base", {"verdict": "rejected", "correction": "none"}))
must_fail("S5 deprecated provenance.time_base with an extra key",
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

# Numbers that depend on the build (the ~15-day minimum, the ranking by good_hours) are build
# checks, listed in the schema's top description. These instances are valid JSON Schema on purpose.
for label, mutate in (
        ("too_short with 10000 good hours", lambda d: fjord(d, "d")["record_span"].__setitem__("good_hours", 10000)),
        ("constants fitted from 24 good hours", lambda d: fjord(d, "a")["record_span"].__setitem__("good_hours", 24))):
    doc = copy.deepcopy(example)
    mutate(doc)
    if problems(doc):
        print(f"FAIL: {label} should be left to the build check")
        sys.exit(1)
    print(f"ok: left to the build check: {label}")


# --- OTC_index.json and the latest pointers -----------------------------------------------------

index_schema = json.loads((schema_dir / "otc-index-0.6.schema.json").read_text())
Draft202012Validator.check_schema(index_schema)
index_validator = Draft202012Validator(index_schema)
pointer_validator = Draft202012Validator({"$schema": index_schema["$schema"], "$defs": index_schema["$defs"],
                                          "$ref": "#/$defs/release_info"})
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


# --- 0.6 confirmation round (provenance allowlist, fallback, versions, reasons, stations) --------

for key in ("Excluded", "EXCLUDED", "is_excluded", "exclusion", "suppressed", "qc", "state", "visibility", "rejected"):
    must_fail(f"provenance key {key}", lambda d, k=key: fjord(d, "a")["provenance"].__setitem__(k, True))
must_fail("provenance with a nested gate status",
          lambda d: fjord(d, "a")["provenance"].__setitem__("gate", {"status": "excluded"}))
must_fail("JMA analysis_years with an extra key",
          lambda d: d["stations"][0]["constant_sets"][1]["provenance"].__setitem__("analysis_years", {"status": "excluded"}))
must_fail("qc_status fallback without carried_from_release", lambda d: fjord(d, "a").__setitem__("qc_status", "fallback"))
must_fail("min_reader_version 0.5", lambda d: d["release"].__setitem__("min_reader_version", "0.5"))
must_fail("min_reader_version 9.9", lambda d: d["release"].__setitem__("min_reader_version", "9.9"))
must_fail("format_version 0.5 under the 0.6 schema", lambda d: d.__setitem__("format_version", "0.5"))
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
# writes sets (one per distinct shape). 0.6 provenance is a closed list, so it must accept each.

prov_validator = sub_validator("provenance")
real = json.loads((root / "scripts/fixtures/adapter-sets.json").read_text())["sets"]
bad = [(r["adapter"], r["source_record_id"], e.message[:120]) for r in real for e in prov_validator.iter_errors(r["provenance"])]
if bad:
    for b in bad:
        print("FAIL: real provenance rejected:", *b)
    sys.exit(1)
print(f"ok: accepted the provenance of {len(real)} real sets from "
      f"{', '.join(sorted({r['adapter'] for r in real}))}")


def official(d):
    return d["stations"][0]["constant_sets"][1]


def jma(d):
    return official(d)["provenance"]


def as_source(source, prov):
    def f(d):
        official(d)["source"] = source
        official(d)["provenance"] = copy.deepcopy(prov)
    return f


real_jma = next(r["provenance"] for r in real if r["adapter"] == "A-JMA-a")
real_linz = next(r["provenance"] for r in real if r["adapter"] == "A-LINZ" and r["provenance"]["position"]["from"] == "station_list")
must_pass("the real JMA provenance on a jma set", as_source("jma", real_jma))
must_pass("the real LINZ provenance (position from the station list) on a linz set", as_source("linz", real_linz))
must_pass("a LINZ header position that could not be read (null)",
          lambda d: [as_source("linz", real_linz)(d), jma(d)["position"].update(header_lat=None, header_lon=None)])
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
must_fail("provenance.build with an extra key", lambda d: jma(d).__setitem__("build", {"status": "excluded"}))
must_fail("tool_versions smuggling a status",
          lambda d: jma(d).__setitem__("build", {"tool_versions": {"excluded": "true", "qc_status": "removed"}}))
must_fail("tool_versions with an upper-case name", lambda d: jma(d).__setitem__("build", {"tool_versions": {"Python": "3.12"}}))
must_fail("the deprecated build_commit that is not hex", lambda d: jma(d).__setitem__("build_commit", "not a commit"))
must_fail("both build_commit and build.build_commit",
          lambda d: jma(d).update(build_commit="aaaaaaa", build={"build_commit": "bbbbbbb"}))
must_pass("the deprecated build_commit alone", lambda d: jma(d).__setitem__("build_commit", "aaaaaaa"))
must_fail("LINZ header position null but from the header",
          lambda d: [as_source("linz", real_linz)(d),
                     jma(d).__setitem__("position", {"header_lat": None, "header_lon": None, "from": "header"})])
must_fail("LINZ header latitude null, from the header",
          lambda d: [as_source("linz", real_linz)(d),
                     jma(d).__setitem__("position", {"header_lat": None, "header_lon": 174.0, "from": "header"})])
for name in ("excluded", "removed", "status", "qc_status", "state", "suppressed", "withheld"):
    must_fail(f"tool_versions name {name}", lambda d, n=name: jma(d).__setitem__("build", {"tool_versions": {n: "1"}}))
must_pass("tool_versions with ordinary names (python, uv, numpy)",
          lambda d: jma(d).__setitem__("build", {"tool_versions": {"python": "3.12.7", "uv": "0.4.18", "numpy": "2.1.2"}}))
