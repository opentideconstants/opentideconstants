"""Published-level helpers, chart-datum policy and build checks 8, 9 and 13."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path

import pytest

from otc_pipeline import datums
from otc_pipeline.datums import datum_build_errors, period, published
from otc_pipeline.gesla_sets import _published_stated_zero


@pytest.fixture(scope="module")
def native_uhslc_fd_chart_evidence():
    root = os.environ.get("OTC_UHSLC_FD_CHART_PROOF")
    if not root:
        pytest.skip("requires the pinned UHSLC_FD computed chart evidence")
    path = Path(root) / "receipt.json"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "dd7aa95cb1ed19bc60e2bb1f47eba851e0b352f104e5d2ac2da94f1dffa0491e"
    facts = json.loads(path.read_text())
    source = Path(facts["input_pins"]["candidate"]["path"])
    with source.open("rb") as stream:
        assert hashlib.file_digest(stream, "sha256").hexdigest() == facts["input_pins"]["candidate"]["sha256"]
    targets = facts["three_real_targets"]
    holdouts = [row for row in facts["frozen_family_records_at_current_missing_stations"] if row["cached_guard_failures"]]
    assert len(holdouts) == 19
    wanted = {row["line"]: row for row in targets + holdouts}
    stations = {}
    with source.open("rb") as stream:
        for number, line in enumerate(stream, 1):
            if number not in wanted:
                continue
            row = wanted[number]
            assert hashlib.sha256(line).hexdigest() == row["line_sha256"]
            station = json.loads(line)
            assert station["station_id"] == row["station_id"]
            stations[row["set_id"]] = station
            if number == max(wanted):
                break
    assert len(stations) == len(wanted)
    for row in targets:
        cs = next(c for c in stations[row["set_id"]]["constant_sets"] if c["set_id"] == row["set_id"])
        assert cs["datum"]["named"]["lat"] == row["lat_m"]
        assert cs["datum"]["msl_offset_m"] == row["msl_offset_m"]
        assert cs["datum"]["basis"]["lat"] == row["lat_basis"]
        assert "chart_datum" not in cs["datum"]
        assert "zero" not in cs["datum"] and "zero_name" not in cs["datum"]
    return facts, stations


@pytest.fixture(scope="module")
def native_uhslc_fd_chart_cases(native_uhslc_fd_chart_evidence):
    facts, stations = native_uhslc_fd_chart_evidence
    return [(stations[row["set_id"]], row["set_id"]) for row in facts["three_real_targets"]]


def test_native_uhslc_fd_holdouts_preserve_payload(native_uhslc_fd_chart_evidence):
    facts, stations = native_uhslc_fd_chart_evidence
    rows = [r for r in facts["frozen_family_records_at_current_missing_stations"] if r["cached_guard_failures"]]
    for row in rows:
        station = copy.deepcopy(stations[row["set_id"]])
        assert datums.derive_chart_datums({"stations": [station]})["changed_sets"] == 0
        assert station == stations[row["set_id"]]
        print(f"REAL UHSLC_FD HOLDOUT {row['set_id']}: unchanged; guards={row['cached_guard_failures']}")


@pytest.mark.parametrize("defect", ["rq", "known_frame", "missing_mean", "boolean_mean", "nonfinite_mean",
    "missing_link", "foreign_link", "string_link", "missing_identity", "incomplete_country", "unknown_country"])
def test_native_uhslc_fd_source_guards(native_uhslc_fd_chart_cases, defect):
    original, set_id = native_uhslc_fd_chart_cases[0]
    station = copy.deepcopy(original)
    cs = next(c for c in station["constant_sets"] if c["set_id"] == set_id)
    own = next(r for r in station["source_records"] if r["source"] == "gesla"
               and r["source_record_id"] == cs["source_record_id"])
    if defect == "rq":
        own["original_metadata"]["CONTRIBUTOR (ABBREVIATED)"] = "UHSLC_RQ"
    elif defect == "known_frame":
        own["original_metadata"]["DATUM INFORMATION"] = "MSL"
    elif defect == "missing_mean":
        del cs["datum"]["msl_offset_m"]
    elif defect in ("boolean_mean", "nonfinite_mean"):
        cs["datum"]["msl_offset_m"] = True if defect == "boolean_mean" else float("inf")
    elif defect == "missing_link":
        del own["published_set_ids"]
    elif defect == "foreign_link":
        own["published_set_ids"] = ["different-set"]
    elif defect == "string_link":
        own["published_set_ids"] = set_id
    elif defect == "missing_identity":
        cs["source_record_id"] = own["source_record_id"] = own["original_metadata"]["FILE NAME"] = None
    else:
        country = "XXX" if defect == "unknown_country" else None
        station["country"] = country
        for row in station["source_records"]:
            row.setdefault("original_metadata", {})["COUNTRY"] = country
    before = copy.deepcopy(station)
    assert datums.derive_chart_datums({"stations": [station]})["changed_sets"] == 0
    assert station == before


@pytest.fixture(scope="module")
def native_nld_computed_chart_cases():
    root = os.environ.get("OTC_NLD_CHART_PROOF")
    if not root:
        pytest.skip("requires the pinned NLD computed chart evidence")
    path = Path(root) / "receipt.json"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "0e5569e16ac1198426537b218e6d9162f1a77370d9e8055ded3362482a084166"
    facts = json.loads(path.read_text())
    source = Path(facts["input_pins"]["candidate"]["path"])
    with source.open("rb") as stream:
        assert hashlib.file_digest(stream, "sha256").hexdigest() == facts["input_pins"]["candidate"]["sha256"]
    wanted = {row["candidate_line"]: row for row in facts["target_records"]}
    cases = []
    with source.open("rb") as stream:
        for number, line in enumerate(stream, 1):
            if number not in wanted:
                continue
            row = wanted[number]
            assert hashlib.sha256(line).hexdigest() == row["line_sha256"]
            station = json.loads(line)
            assert station["station_id"] == row["station_id"]
            cs = next(c for c in station["constant_sets"] if c["set_id"] == row["set_id"])
            assert cs["datum"]["named"]["lat"] == row["lat_m"]
            assert cs["datum"]["msl_offset_m"] == row["msl_offset_m"]
            assert cs["datum"]["basis"]["lat"] == row["lat_basis"]
            assert "zero" not in cs["datum"] and "zero_name" not in cs["datum"]
            assert "chart_datum" not in cs["datum"]
            cases.append((station, row["set_id"]))
            if number == max(wanted):
                break
    assert len(cases) == 3
    return cases


@pytest.fixture(scope="module")
def native_computed_chart_cases():
    path = os.environ.get("OTC_COMPUTED_CHART_CANDIDATE")
    if not path:
        pytest.skip("requires the pinned native computed chart candidate")
    source = Path(path)
    with source.open("rb") as stream:
        assert hashlib.file_digest(stream, "sha256").hexdigest() == "4c49cd4eb198f620f0e52c362c4ba4c0faefdd02851835e39dbab88195a0c994"
    wanted = {
        8957: ("OTC-8fc5a96e", "roonagh_pier-roo-irl-mi_c", -1.9520731465557302),
        12099: ("OTC-c146e862", "inishmore-ini-irl-mi_c", -2.7427211249859993),
        13001: ("OTC-d02f0d69", "killybegs_port-kil-irl-mi_c", -2.248872469229044),
    }
    cases = []
    with source.open() as stream:
        for number, line in enumerate(stream, 1):
            if number not in wanted:
                continue
            station = json.loads(line)
            station_id, record_id, value = wanted[number]
            assert station["station_id"] == station_id
            cs = next(c for c in station["constant_sets"]
                      if c["set_id"] == f"{station_id}/gesla-fit/{record_id}")
            assert cs["datum"]["named"]["lat"] == value
            assert cs["datum"]["basis"]["lat"]["kind"] == "computed"
            assert "chart_datum" not in cs["datum"]
            assert "zero" not in cs["datum"] and "zero_name" not in cs["datum"]
            cases.append((station, cs["set_id"]))
            if number == max(wanted):
                break
    assert len(cases) == 3
    return cases


@pytest.mark.parametrize("case_fixture", ["native_computed_chart_cases", "native_nld_computed_chart_cases", "native_uhslc_fd_chart_cases"])
@pytest.mark.parametrize("index", range(3))
def test_native_computed_approximate_chart(request, case_fixture, index):
    original, set_id = request.getfixturevalue(case_fixture)[index]
    station = copy.deepcopy(original)
    cs = next(c for c in station["constant_sets"] if c["set_id"] == set_id)
    receipt = datums.derive_chart_datums({"stations": [station]})
    print(f"REAL COMPUTED {set_id}: lat={cs['datum']['named']['lat']!r}; "
          f"before=None; after={cs['datum'].get('chart_datum')!r}; zero/zero_name absent")
    assert cs["datum"].get("chart_datum") == "lat"
    fallback = cs["datum"]["basis"]["lat"]["fallback"]
    assert "approximate chart-reference stand-in: existing computed LAT (nonofficial)" in fallback["basis"]
    assert "agency chart-datum convention, equivalence and vertical tie are not established" in fallback["otc_finding"]
    assert receipt["approximate_changed_places"] == receipt["approximate_changed_sets"] == 1
    assert receipt["by_convention"] == {} and receipt["citations"] == {}
    after = copy.deepcopy(station)
    assert datums.derive_chart_datums({"stations": [station]})["changed_sets"] == 0
    assert station == after
    del cs["datum"]["chart_datum"]
    old = next(c for c in original["constant_sets"] if c["set_id"] == set_id)
    fallback.update(old["datum"]["basis"]["lat"]["fallback"])
    assert station == original


@pytest.mark.parametrize("defect", ["missing_own", "foreign_own", "country_conflict", "published_other",
    "published_subordinate", "existing_pointer", "flatline", "truncated_lows", "nonfinite", "boolean",
    "wrong_basis", "missing_fallback", "wrong_fallback", "missing_fallback_text", "blank_fallback_text",
    "unknown_country", "other_contributor", "other_frame", "conflicting_filename"])
@pytest.mark.parametrize("case_fixture", ["native_computed_chart_cases", "native_nld_computed_chart_cases", "native_uhslc_fd_chart_cases"])
def test_native_computed_chart_controls_preserve_payload(request, case_fixture, defect):
    original, set_id = request.getfixturevalue(case_fixture)[0]
    station = copy.deepcopy(original)
    cs = next(c for c in station["constant_sets"] if c["set_id"] == set_id)
    own = next(r for r in station["source_records"] if r["source"] == "gesla"
               and r["source_record_id"] == cs["source_record_id"])
    datum = cs["datum"]
    if defect == "missing_own":
        station["source_records"].remove(own)
    elif defect == "foreign_own":
        own["source_record_id"] = "different-source-record"
    elif defect == "country_conflict":
        own["original_metadata"]["COUNTRY"] = "CAN"
    elif defect == "unknown_country":
        station["country"] = own["original_metadata"]["COUNTRY"] = "XXX"
    elif defect in ("other_contributor", "other_frame", "conflicting_filename"):
        field = {"other_contributor": "CONTRIBUTOR (ABBREVIATED)", "other_frame": "DATUM INFORMATION",
                 "conflicting_filename": "FILE NAME"}[defect]
        own["original_metadata"][field] = "MSL" if own["original_metadata"].get(field) == "Unspecified" else "Unspecified"
    elif defect in ("published_other", "published_subordinate"):
        agency = {"named": {"cd": 0.0}, "basis": {"cd": published("CD")}, "chart_datum": "cd"}
        if defect == "published_other":
            station["constant_sets"].append({"set_id": "agency", "datum": agency})
        else:
            station["subordinate_offsets"] = {"datum": agency}
    elif defect == "existing_pointer":
        datum["chart_datum"] = "hat"
    elif defect == "flatline":
        cs.setdefault("record_issues", []).append({"issue": "flatline"})
    elif defect == "truncated_lows":
        datum["basis"]["lat"].setdefault("flags", []).append("truncated_lows")
    elif defect in ("nonfinite", "boolean"):
        datum["named"]["lat"] = float("inf") if defect == "nonfinite" else True
    elif defect == "wrong_basis":
        datum["basis"]["lat"]["kind"] = "observed"
    elif defect == "missing_fallback":
        del datum["basis"]["lat"]["fallback"]
    elif defect == "wrong_fallback":
        datum["basis"]["lat"]["fallback"]["reason"] = "record_unreliable"
    elif defect == "missing_fallback_text":
        del datum["basis"]["lat"]["fallback"]["otc_finding"]
    else:
        datum["basis"]["lat"]["fallback"]["basis"] = "  "
    before = copy.deepcopy(station)
    assert datums.derive_chart_datums({"stations": [station]})["changed_sets"] == 0
    assert station == before


@pytest.fixture(scope="module")
def native_approximate_chart_cases():
    root = os.environ.get("OTC_APPROXIMATE_CHART_PROOF")
    assert root, "OTC_APPROXIMATE_CHART_PROOF must name the pinned approximate chart evidence"
    path = Path(root) / "policy/selected-native-9-witnesses.json"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "4a772ac92cb717b96543605dfc80ea890e6dfbb8839412af1800deb943cfe4f4"
    facts = json.loads(path.read_text())
    source = Path(facts["source"])
    with source.open("rb") as stream:
        assert hashlib.file_digest(stream, "sha256").hexdigest() == facts["source_sha256"]
    wanted = {row["source_line"]: row for row in facts["witnesses"]}
    stations = {}
    with source.open("rb") as stream:
        for number, line in enumerate(stream, 1):
            if number in wanted:
                row = wanted[number]
                assert hashlib.sha256(line).hexdigest() == row["source_line_sha256"]
                station = json.loads(line)
                assert station["station_id"] == row["station_id"]
                stations[station["station_id"]] = station
            if number == max(wanted):
                break
    assert len(stations) == 9
    return facts, stations


@pytest.mark.parametrize("index", range(9))
def test_native_approximate_chart_fallback(native_approximate_chart_cases, index):
    facts, stations = native_approximate_chart_cases
    row = facts["witnesses"][index]
    station = copy.deepcopy(stations[row["station_id"]])
    before = copy.deepcopy(station)
    cs = next(cs for cs in station["constant_sets"] if cs["set_id"] == row["set_id"])
    key = row["key"]
    assert cs["datum"]["named"][key] == row["existing_value_m"]
    assert cs["datum"]["basis"][key] == row["existing_basis"]
    assert all("chart_datum" not in c.get("datum", {}) for c in station["constant_sets"])
    receipt = datums.derive_chart_datums({"stations": [station]})
    print(f"REAL APPROXIMATE {row['group']} {row['station_id']} {row['set_id']}: "
          f"{key}={row['existing_value_m']!r}; before=None; after={cs['datum'].get('chart_datum')!r}; "
          "T1=FAIL; total_sigma=None")
    assert cs["datum"].get("chart_datum") == key
    fallback = cs["datum"]["basis"][key]["fallback"]
    original = row["existing_basis"]["fallback"]
    assert fallback["reason"] == "datum_gate_failed"
    assert fallback["basis"] == original["basis"] + f"; approximate chart-reference stand-in: existing observed {key.upper()}"
    assert fallback["otc_finding"] == original["otc_finding"] + (
        "; approximate chart-reference stand-in; agency chart-datum equivalence and vertical tie are not established")
    assert cs["datum"]["basis"][key]["kind"] == "observed"
    assert "sigma_m" not in cs["datum"]["basis"][key]
    assert receipt["changed_places"] == receipt["changed_sets"] == 1
    assert receipt["approximate_changed_places"] == receipt["approximate_changed_sets"] == 1
    assert receipt["by_convention"] == {} and receipt["citations"] == {}
    assert datums.derive_chart_datums({"stations": [station]})["changed_places"] == 0
    del cs["datum"]["chart_datum"]
    fallback.update(original)
    assert station == before


@pytest.fixture(scope="module")
def native_observed_chart_cases():
    root = os.environ.get("OTC_OBSERVED_CHART_PROOF")
    assert root, "OTC_OBSERVED_CHART_PROOF must name the pinned native observed chart evidence"
    facts_path = Path(root) / "selected-native-3-witnesses.json"
    assert hashlib.sha256(facts_path.read_bytes()).hexdigest() == "00d24525a3095ac69c457091ac15b750ca4077a015532ff0d8b5643334275ea5"
    facts = json.loads(facts_path.read_text())
    source = Path(facts["source"])
    with source.open("rb") as stream:
        assert hashlib.file_digest(stream, "sha256").hexdigest() == facts["source_sha256"]
    rows = facts["witnesses"] + [facts["exclusion_control"]]
    wanted = {row["source_line"]: row for row in rows}
    stations = {}
    with source.open("rb") as stream:
        for number, line in enumerate(stream, 1):
            if number in wanted:
                row = wanted[number]
                assert hashlib.sha256(line).hexdigest() == row["source_line_sha256"]
                station = json.loads(line)
                assert station["station_id"] == row["station_id"]
                stations[station["station_id"]] = station
            if number == max(wanted):
                break
    assert len(stations) == 4
    return facts, stations


@pytest.mark.parametrize("index", range(3))
def test_native_observed_chart_fallback(native_observed_chart_cases, index):
    facts, stations = native_observed_chart_cases
    row = facts["witnesses"][index]
    station = copy.deepcopy(stations[row["station_id"]])
    before = copy.deepcopy(station)
    cs = next(cs for cs in station["constant_sets"] if cs["set_id"] == row["set_id"])
    assert cs["datum"]["named"]["mllw"] == row["existing_value_m"]
    assert cs["datum"]["basis"]["mllw"] == row["existing_basis"]
    assert all("chart_datum" not in cs.get("datum", {}) for cs in station["constant_sets"])
    receipt = datums.derive_chart_datums({"stations": [station]})
    print(f"REAL CHART {row['station_id']} {row['set_id']}: mllw={row['existing_value_m']!r}; "
          f"before=None; after={cs['datum'].get('chart_datum')!r}; T1=FAIL; total_sigma=None")
    assert cs["datum"].get("chart_datum") == "mllw"
    assert receipt["changed_places"] == receipt["changed_sets"] == 1
    assert receipt["citations"]["iho-2021-usa"]["stated_convention"] == "MLLW"
    assert receipt["citations"]["iho-2021-usa"]["pdf_page_1_based"] == 2
    assert datums.derive_chart_datums({"stations": [station]})["changed_places"] == 0
    del cs["datum"]["chart_datum"]
    assert station == before


def test_native_lake_observed_mllw_is_explicit_approximate_chart(native_observed_chart_cases):
    facts, stations = native_observed_chart_cases
    station = copy.deepcopy(stations[facts["exclusion_control"]["station_id"]])
    before = copy.deepcopy(station)
    assert datums.derive_chart_datums({"stations": [station]})["approximate_changed_places"] == 1
    for cs in station["constant_sets"]:
        if cs.get("datum", {}).pop("chart_datum", None) is not None:
            original = next(c for c in before["constant_sets"] if c["set_id"] == cs["set_id"])
            cs["datum"]["basis"]["mllw"]["fallback"] = original["datum"]["basis"]["mllw"]["fallback"]
    assert station == before


def test_native_observed_chart_does_not_equate_lat_and_mllw(native_observed_chart_cases):
    facts, stations = native_observed_chart_cases
    row = facts["witnesses"][0]
    station = copy.deepcopy(stations[row["station_id"]])
    cs = next(cs for cs in station["constant_sets"] if cs["set_id"] == row["set_id"])
    sibling = copy.deepcopy(cs)
    sibling["set_id"] += "/lat-only"
    del sibling["datum"]["named"]["mllw"], sibling["datum"]["basis"]["mllw"]
    station["constant_sets"].append(sibling)
    before = copy.deepcopy(sibling)
    assert datums.derive_chart_datums({"stations": [station]})["changed_sets"] == 1
    assert cs["datum"]["chart_datum"] == "mllw"
    assert sibling == before
    # Existing LAT coverage receipt semantics do not count an MLLW-only place as LAT.
    station = copy.deepcopy(stations[row["station_id"]])
    for item in station["constant_sets"]:
        for field in ("named", "basis"):
            item.get("datum", {}).get(field, {}).pop("lat", None)
    receipt = datums.derive_chart_datums({"stations": [station]})
    assert receipt["lat_places"] == 0 and receipt["changed_places"] == 1


@pytest.mark.parametrize("defect", ["missing_domain", "foreign_record", "conflicting_domain",
    "unknown_country", "conflicting_country", "published_other", "published_subordinate", "existing_pointer",
    "flatline", "truncated_lows", "nonfinite", "boolean", "computed_mllw", "published_mllw", "observed_lat"])
def test_native_observed_chart_negative_variants_preserve_payload(native_observed_chart_cases, defect):
    facts, stations = native_observed_chart_cases
    row = facts["witnesses"][0]
    station = copy.deepcopy(stations[row["station_id"]])
    cs = next(cs for cs in station["constant_sets"] if cs["set_id"] == row["set_id"])
    own = next(r for r in station["source_records"] if r["source"] == "gesla" and r["source_record_id"] == cs["source_record_id"])
    datum = cs["datum"]
    if defect in ("Lake", "River", "missing_domain"):
        own["original_metadata"]["GAUGE TYPE"] = "Unspecified" if defect == "missing_domain" else defect
        # A different source's Coastal label cannot qualify this source record.
        sibling = copy.deepcopy(own)
        sibling["source_record_id"] = "different-source-record"
        sibling["original_metadata"]["GAUGE TYPE"] = "Coastal"
        station["source_records"].append(sibling)
    elif defect == "foreign_record":
        own["source_record_id"] = "different-source-record"
    elif defect == "conflicting_domain":
        other = copy.deepcopy(own)
        other["original_metadata"]["GAUGE TYPE"] = "Lake"
        station["source_records"].append(other)
    elif defect == "unknown_country":
        station["country"] = own["original_metadata"]["COUNTRY"] = "XXX"
    elif defect == "conflicting_country":
        own["original_metadata"]["COUNTRY"] = "CAN"
    elif defect in ("published_other", "published_subordinate"):
        agency = {"named": {"cd": 0.0}, "basis": {"cd": published("CD")}, "chart_datum": "cd"}
        if defect == "published_other":
            station["constant_sets"].append({"set_id": "agency", "datum": agency})
        else:
            station["subordinate_offsets"] = {"datum": agency}
    elif defect == "existing_pointer":
        datum["chart_datum"] = "lat"
    elif defect == "flatline":
        cs["record_issues"] = cs.get("record_issues", []) + [{"issue": "flatline"}]
    elif defect == "truncated_lows":
        datum["basis"]["mllw"].setdefault("flags", []).append("truncated_lows")
    elif defect in ("nonfinite", "boolean"):
        datum["named"]["mllw"] = float("inf") if defect == "nonfinite" else True
    elif defect in ("computed_mllw", "published_mllw"):
        datum["basis"]["mllw"]["kind"] = defect.split("_")[0]
    else:
        del datum["named"]["mllw"], datum["basis"]["mllw"]
        datum["basis"]["lat"]["kind"] = "observed"
    before = copy.deepcopy(station)
    assert datums.derive_chart_datums({"stations": [station]})["changed_places"] == 0
    assert station == before


@pytest.mark.parametrize("group", ["River", "Lake", "Baltic"])
@pytest.mark.parametrize("defect", ["published_other", "published_subordinate", "existing_pointer", "flatline",
    "truncated_lows", "nonfinite", "boolean", "missing_own", "foreign_own", "country_conflict",
    "wrong_basis", "missing_fallback", "wrong_fallback", "missing_fallback_text", "non_target"])
def test_approximate_chart_controls_preserve_payload(native_approximate_chart_cases, group, defect):
    facts, stations = native_approximate_chart_cases
    row = next(r for r in facts["witnesses"] if r["group"] == group)
    station = copy.deepcopy(stations[row["station_id"]])
    cs = next(c for c in station["constant_sets"] if c["set_id"] == row["set_id"])
    own = next(r for r in station["source_records"] if r["source"] == "gesla" and r["source_record_id"] == cs["source_record_id"])
    datum, key = cs["datum"], row["key"]
    if defect in ("published_other", "published_subordinate"):
        agency = {"named": {"cd": 0.0}, "basis": {"cd": published("CD")}, "chart_datum": "cd"}
        if defect == "published_other":
            station["constant_sets"].append({"set_id": "agency", "datum": agency})
        else:
            station["subordinate_offsets"] = {"datum": agency}
    elif defect == "existing_pointer":
        datum["chart_datum"] = "hat"
    elif defect == "flatline":
        cs.setdefault("record_issues", []).append({"issue": "flatline"})
    elif defect == "truncated_lows":
        datum["basis"][key].setdefault("flags", []).append("truncated_lows")
    elif defect in ("nonfinite", "boolean"):
        datum["named"][key] = float("inf") if defect == "nonfinite" else True
    elif defect == "missing_own":
        station["source_records"].remove(own)
    elif defect == "foreign_own":
        own["source_record_id"] = "foreign-record"
    elif defect == "country_conflict":
        own["original_metadata"]["COUNTRY"] = "CAN"
    elif defect == "wrong_basis":
        datum["basis"][key]["kind"] = "computed"
    elif defect == "missing_fallback":
        del datum["basis"][key]["fallback"]
    elif defect == "wrong_fallback":
        datum["basis"][key]["fallback"]["reason"] = "record_unreliable"
    elif defect == "missing_fallback_text":
        del datum["basis"][key]["fallback"]["otc_finding"]
    elif group == "Baltic":
        cs["source_record_id"] = own["source_record_id"] = "not-a-dt5f-exclusion"
    else:
        station["country"] = own["original_metadata"]["COUNTRY"] = "CAN"
    before = copy.deepcopy(station)
    assert datums.derive_chart_datums({"stations": [station]})["changed_sets"] == 0
    assert station == before


@pytest.mark.parametrize("group", ["River", "Lake", "Baltic"])
def test_approximate_chart_requires_consistent_own_metadata(native_approximate_chart_cases, group):
    facts, stations = native_approximate_chart_cases
    row = next(r for r in facts["witnesses"] if r["group"] == group)
    station = copy.deepcopy(stations[row["station_id"]])
    cs = next(c for c in station["constant_sets"] if c["set_id"] == row["set_id"])
    own = next(r for r in station["source_records"] if r["source"] == "gesla" and r["source_record_id"] == cs["source_record_id"])
    duplicate = copy.deepcopy(own)
    duplicate["original_metadata"]["COUNTRY" if group == "Baltic" else "GAUGE TYPE"] = "CAN" if group == "Baltic" else "Coastal"
    station["source_records"].append(duplicate)
    before = copy.deepcopy(station)
    assert datums.derive_chart_datums({"stations": [station]})["changed_sets"] == 0
    assert station == before


def test_observed_companion_preserves_published_level_and_chart_pointer():
    datum = {"zero": "gauge_zero", "chart_datum": "mhw", "named": {"mhw": 3.0},
             "basis": {"mhw": {"kind": "published", "method": "source", "source_name": "MHW"}}}
    draft = {"named": {"otc_mhw": 2.9}, "basis": {"otc_mhw": {"kind": "observed"}}}
    result = datums.observed_companions(datum, draft, ["otc_mhw"])
    assert result["named"] == {"mhw": 3.0, "otc_mhw": 2.9}
    assert result["basis"]["mhw"] == datum["basis"]["mhw"]
    assert result["chart_datum"] == "mhw"
    assert datum["named"] == {"mhw": 3.0}


@pytest.mark.parametrize("defect", ["orphan", "computed_companion", "conflict", "unknown_key", "extra_metadata", "different_keys", "low_cutoff", "unflagged_cutoff"])
def test_observed_companion_rejects_invalid_augmentation(defect):
    datum = {"named": {}, "basis": {}}
    draft = {"named": {"mhw": 2.9}, "basis": {"mhw": {"kind": "observed"}}}
    truncated = False
    if defect in ("orphan", "computed_companion"):
        draft = {"named": {"otc_mhw": 2.9}, "basis": {"otc_mhw": {"kind": "observed"}}}
        if defect == "computed_companion":
            datum = {"named": {"mhw": 3.0}, "basis": {"mhw": {"kind": "computed"}}}
    elif defect == "conflict":
        datum = {"named": {"mhw": 3.0}, "basis": {"mhw": {"kind": "published"}}}
    elif defect == "unknown_key":
        draft = {"named": {"fake": 1.0}, "basis": {"fake": {"kind": "observed"}}}
    elif defect == "extra_metadata":
        draft["msl_offset_m"] = 9.0
    elif defect == "different_keys":
        draft["basis"] = {}
    elif defect == "low_cutoff":
        truncated = True
        draft = {"named": {"mlw": 1.0}, "basis": {"mlw": {"kind": "observed", "flags": ["truncated_lows"]}}}
    else:
        truncated = True
    with pytest.raises(ValueError):
        datums.observed_companions(datum, draft, list(draft["named"]), truncated_lows=truncated)


def test_period():
    assert period("1983-01-01", "2001-12-31", "NTDE 1983-2001") == {
        "start": "1983-01-01", "end": "2001-12-31", "name": "NTDE 1983-2001"}
    assert period("2020-01-01", "2020-01-01") == {"start": "2020-01-01", "end": "2020-01-01"}
    with pytest.raises(ValueError):
        period("2021-02-30", "2021-03-01")          # not a real date
    with pytest.raises(ValueError, match="end before start"):
        period("2002-01-01", "2001-12-31")


def test_published():
    assert published("MSL") == {"kind": "published", "method": "source", "source_name": "MSL"}
    assert published("MHHW", epoch={"start": "1983-01-01", "end": "2001-12-31"}, control_source_id="8443970",
                     provisional=True) == {
        "kind": "published", "method": "source", "source_name": "MHHW",
        "epoch": {"start": "1983-01-01", "end": "2001-12-31"}, "control": {"source_id": "8443970"},
        "flags": ["provisional"]}
    with pytest.raises(ValueError):
        published("x" * 32)
    with pytest.raises(ValueError):
        published("")


def brest_header(**overrides):
    return {"record_id": "brest-3-fra-refmar", "source_version": "4.1", "contributor": "REFMAR",
            "site_code": "3", "country": "FRA", "datum_information": "Zero Hydrographique", **overrides}


@pytest.mark.parametrize("header", ["Zero Hydrographique", "  zErO hYdRoGrApHiQuE  "])
def test_confirmed_gesla_stated_chart_zero_is_published(header):
    original = {"msl_offset_m": 4.20872}
    datum = _published_stated_zero(brest_header(datum_information=header), original)
    assert datum == {"msl_offset_m": 4.20872, "zero": "chart_datum", "zero_name": "ZH",
                     "named": {"cd": 0.0}, "chart_datum": "cd",
                     "basis": {"cd": published("Zero Hydrographique")}}
    assert original == {"msl_offset_m": 4.20872}
    assert datum_build_errors(datum) == []


@pytest.mark.parametrize("overrides", [
    {"record_id": "unconfirmed-3-fra-refmar"}, {"source_version": "future"},
    {"contributor": "OTHER"}, {"site_code": "other"}, {"country": "SWE"},
    {"datum_information": "unknown"}, {"datum_information": "BSCD2000"},
    {"datum_information": None},
])
def test_unconfirmed_gesla_zero_is_unchanged(overrides):
    original = {"msl_offset_m": 4.20872}
    assert _published_stated_zero(brest_header(**overrides), original) == original


@pytest.mark.parametrize("record_id,contributor,site_code,country,header,zero_name,source_name", [
    ("abbot_point-59300-aus-bom", "BOM", "59300", "AUS", "Chart Datum / Lowest Astronomical Tide",
     "CD / LAT", "CD / LAT"),
    ("abbotts_harbour-382-can-meds", "MEDS", "382", "CAN", "Chart Datum (CD)", "CD", "Chart Datum (CD)"),
    ("aberdeen-abe-gbr-bodc", "BODC", "ABE", "GBR", "Admiralty Chart Datum (ACD) ",
     "ACD", "Admiralty Chart Datum (ACD)"),
    ("aberdeen-abe-gbr-cmems", "CMEMS", "Abe", "GBR", "Admiralty Chart Datum", "ACD", "Admiralty Chart Datum"),
    ("aiguillon_sur_mer-6257-fra-refmar", "REFMAR", "6257", "FRA", "Zero Hydrographique", "ZH", "Zero Hydrographique"),
    ("aiguillonsurmertg_60minute-aig-fra-cmems", "CMEMS", "Aig", "FRA", "chart datum", "CD", "Chart Datum"),
    ("antalya-ant-tur-eseas", "ESEAS", "ANT", "TUR", "Local Chart Datum", "Local CD", "Local Chart Datum"),
    ("howth_water_level_1-how-irl-mi_c", "MI_C", "HOW", "IRL", "Lowest Astronomical Tide (LAT)",
     "LAT", "Lowest Astronomical Tide (LAT)"),
    ("northcormoranttg-nor-gbr-cmems", "CMEMS", "Nor", "GBR", "ACD", "ACD", "ACD"),
])
def test_cached_published_chart_zero_families(record_id, contributor, site_code, country, header, zero_name, source_name):
    doc = brest_header(record_id=record_id, contributor=contributor, site_code=site_code,
                       country=country, datum_information=header)
    original = {"msl_offset_m": 1.25}
    datum = _published_stated_zero(doc, original)
    assert datum == {**original, "zero": "chart_datum", "zero_name": zero_name, "chart_datum": "cd",
                     "named": {"cd": 0.0}, "basis": {"cd": published(source_name)}}
    assert original == {"msl_offset_m": 1.25}
    assert datum_build_errors(datum) == []


def test_unconfirmed_refmar_header_is_not_a_published_chart_zero():
    doc = brest_header(record_id="bourcefranc_le_chapus-155-fra-refmar", site_code="155")
    original = {"msl_offset_m": 1.25}
    assert _published_stated_zero(doc, original) == original


GOOD = {"zero": "chart_datum", "chart_datum": "cd", "named": {"cd": 0.0, "msl": 1.0},
        "basis": {"cd": published("CD"), "msl": published("MSL", epoch=period("1996-01-01", "2014-12-31"))}}


def test_build_checks_pass():
    assert datum_build_errors(GOOD) == []
    assert datum_build_errors({"msl_offset_m": 1.0}) == []


def test_check_8_chart_datum_names_a_key_of_named():
    assert [e for e in datum_build_errors({**GOOD, "chart_datum": "lat"}) if "check 8" in e]


def test_check_9_same_keys():
    bad = {**GOOD, "named": {**GOOD["named"], "lat": 0.1}}
    errs = datum_build_errors(bad)
    assert len(errs) == 1 and "check 9" in errs[0] and "['lat']" in errs[0]
    bad = {**GOOD, "basis": {**GOOD["basis"], "hat": published("HAT")}}
    assert [e for e in datum_build_errors(bad) if "check 9" in e]


@pytest.mark.parametrize("ep", [{"start": "2021-02-30", "end": "2021-03-01"},
                                {"start": "2015-01-01", "end": "2014-12-31"}])
def test_check_13_real_dates_in_order(ep):
    bad = {**GOOD, "basis": {**GOOD["basis"], "msl": {**published("MSL"), "epoch": ep}}}
    assert [e for e in datum_build_errors(bad) if "check 13" in e]


def chart_candidate(country="GBR", *, uk_area=True):
    """Small native-shaped policy input; numerical/native SDK proof is separate."""
    basis = {"kind": "computed", "method": "harmonic_extremes",
             "epoch": period("2020-01-01", "2038-12-31", "OTC LAT 2020-2038"),
             "uncertainty_m": 0.1893589011529287, "uncertainty_basis": "calibrated",
             "flags": ["time_base_unverified", "sampling_assumed"],
             "fallback": {"reason": "datum_gate_failed", "basis": "existing strict engine",
                          "otc_finding": "Accuracy is not validated; low-water eligibility is unknown."}}
    cs = {"set_id": "test/fit", "source": "gesla-fit", "source_record_id": "native-gauge",
          "source_type": "gauge", "record_issues": [], "provenance": {"input_sha256": ["a" * 64]},
          "datum": {"msl_offset_m": 6.8869, "named": {"lat": -0.18679186340525789, "hat": 14.70597353102088},
                    "basis": {"lat": basis, "hat": copy.deepcopy(basis)}}}
    return {"station_id": "test", "country": country, "recommended_set_id": "test/fit",
            "constant_sets": [cs], "source_records": [{"source": "gesla", "source_record_id": "native-gauge",
                "original_metadata": {"COUNTRY": country, "COORDINATE SYSTEM": "UK National Grid" if uk_area else "Unspecified"}}]}


def derive(release):
    helper = getattr(datums, "derive_chart_datums", None)
    assert callable(helper), "computed LAT has no convention-based chart-datum designation"
    return helper(release)


def test_derived_chart_preserves_everything_except_pointer_and_returns_citation():
    station = chart_candidate()
    before = copy.deepcopy(station)
    receipt = derive({"stations": [station]})
    assert station["constant_sets"][0]["datum"].pop("chart_datum") == "lat"
    assert station == before
    assert receipt["changed_places"] == receipt["changed_sets"] == 1
    assert receipt["matched_places"] == receipt["lat_places"] == 1
    table = Path(datums.__file__).with_name("chart_datum_conventions.json")
    assert receipt["conventions_sha256"] == hashlib.sha256(table.read_bytes()).hexdigest()
    assert receipt["by_convention"] == {"iho-2021-uk": {"matched_places": 1, "changed_places": 1, "changed_sets": 1}}
    assert receipt["citations"]["iho-2021-uk"]["pdf_page_1_based"] == 2
    assert receipt["citations"]["iho-2021-uk"]["stated_convention"] == "approximate LAT"


@pytest.mark.parametrize("country", ["AUS", "FRA", "IND", "NZL", "ESP"])
def test_unconditional_cited_country_rows_are_not_limited_to_uk_witnesses(country):
    station = chart_candidate(country, uk_area=False)
    assert derive({"stations": [station]})["changed_places"] == 1
    assert station["constant_sets"][0]["datum"]["chart_datum"] == "lat"


@pytest.mark.parametrize("country", ["GBR", "AUS", "FRA", "IND", "NZL", "ESP"])
def test_conflicting_source_country_withholds_without_changing_source_identity(country):
    station = chart_candidate(country)
    station["source_records"].append({"source": "gesla", "original_metadata": {"COUNTRY": "KIR"}})
    before = copy.deepcopy(station)
    receipt = derive({"stations": [station]})
    assert receipt["changed_places"] == 0
    assert receipt["withheld"]["unknown_convention_or_area"] == 1
    assert station == before


@pytest.mark.parametrize("country", ["USA", "CAN", "BRA", "CHL", "ITA", "KOR", "SWE", "XXX",
                                     "NOR", "DEU", "NLD", "PNG", "SLB", "GBR"])
def test_non_lat_or_unresolved_area_has_no_country_default(country):
    station = chart_candidate(country, uk_area=False)
    before = copy.deepcopy(station)
    receipt = derive({"stations": [station]})
    assert receipt["changed_places"] == 0
    assert station == before
    assert receipt["withheld"]["unknown_convention_or_area" if country in {
        "XXX", "NOR", "DEU", "NLD", "PNG", "SLB", "GBR"} else "non_lat_convention"] == 1


@pytest.mark.parametrize("where", ["other_set", "subordinate_offsets"])
def test_published_chart_precedence_is_station_wide(where):
    station = chart_candidate()
    datum = {"named": {"cd": 0.0}, "basis": {"cd": published("CD")}, "chart_datum": "cd"}
    if where == "other_set":
        station["constant_sets"].append({"set_id": "test/agency", "datum": datum})
    else:
        station["subordinate_offsets"] = {"datum": datum}
    before = copy.deepcopy(station)
    assert derive({"stations": [station]})["withheld"]["published_chart_datum"] == 1
    assert station == before


@pytest.mark.parametrize("kind", ["computed", "published", "observed"])
def test_existing_chart_pointer_is_never_replaced(kind):
    station = chart_candidate()
    datum = station["constant_sets"][0]["datum"]
    datum["chart_datum"] = "hat"
    datum["basis"]["hat"]["kind"] = kind
    before = copy.deepcopy(station)
    assert derive({"stations": [station]})["changed_places"] == 0
    assert station == before


@pytest.mark.parametrize("cutoff", ["flatline", "truncated_lows"])
def test_actual_cutoff_withholds_but_does_not_modify_existing_lat(cutoff):
    station = chart_candidate()
    cs = station["constant_sets"][0]
    if cutoff == "flatline":
        cs["record_issues"] = [{"issue": "flatline"}]
    else:
        cs["datum"]["basis"]["hat"]["flags"] = [cutoff]
    before = copy.deepcopy(station)
    assert derive({"stations": [station]})["withheld"]["actual_low_water_cutoff"] == 1
    assert station == before


@pytest.mark.parametrize("value,kind", [(float("nan"), "computed"), (float("inf"), "computed"),
                                       (True, "computed"), ("0", "computed"), (0.0, "published"), (0.0, "observed")])
def test_only_finite_computed_lat_is_designated(value, kind):
    station = chart_candidate()
    cs = station["constant_sets"][0]
    cs["datum"]["named"]["lat"] = value
    cs["datum"]["basis"]["lat"]["kind"] = kind
    assert derive({"stations": [station]})["changed_places"] == 0
    assert "chart_datum" not in cs["datum"]


def test_saved_dt5f_explicit_exclusion_cannot_be_overridden_by_country_or_uk_metadata():
    station = chart_candidate()
    station["constant_sets"][0]["source_record_id"] = "aarhus-aar-dnk-cmems"
    assert derive({"stations": [station]})["withheld"]["baltic_or_bscd2000"] == 1


def test_counts_places_once_and_second_call_is_idempotent():
    station = chart_candidate()
    station["constant_sets"].append(copy.deepcopy(station["constant_sets"][0]))
    receipt = derive({"stations": [station]})
    assert receipt["changed_places"] == 1
    assert receipt["changed_sets"] == 2
    before = copy.deepcopy(station)
    assert derive({"stations": [station]})["changed_places"] == 0
    assert station == before


def test_lat_place_receipt_includes_published_subordinate_datums():
    station = {"station_id": "subordinate", "country": "USA", "constant_sets": [],
               "subordinate_offsets": {"datum": {"named": {"lat": -0.215, "mllw": 0.0},
                   "basis": {"lat": published("LAT"), "mllw": published("MLLW")}, "chart_datum": "mllw"}}}
    before = copy.deepcopy(station)
    receipt = derive({"stations": [station]})
    assert receipt["lat_places"] == 1
    assert receipt["changed_places"] == 0
    assert station == before
