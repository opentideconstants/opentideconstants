"""Chart pointers name held NOAA levels without converting their source frame."""
import copy
import hashlib
import json
import os
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from otc_pipeline import retained_datums
from otc_pipeline.release_writer import pinned_schema


NATIVE_SHA256 = "8da26f7f4203ea3f06a92b4d5de5c3b95ebfcf76d62154c237a9c6429496cb3e"
TARGETS = {
    "OTC-d14a171f": ("8660754", 3.456),
    "OTC-e48e302c": ("9751388", 8.97),
}


@pytest.fixture(scope="module")
def actual_noaa_chart_stations():
    selected = os.environ.get("OTC_NOAA_PUBLISHED_CHART_FIXTURE")
    if selected:
        root = Path(selected)
        facts = json.loads((root / "input-binding.json").read_text())
        raw = (root / "actual-stations.json").read_bytes()
        assert facts["source_sha256"] == NATIVE_SHA256
        assert hashlib.sha256(raw).hexdigest() == facts["selected_sha256"]
        stations = {station["station_id"]: station for station in json.loads(raw)}
        assert set(stations) == set(TARGETS)
        return stations
    name = os.environ.get("OTC_NOAA_PUBLISHED_CHART_NATIVE")
    if not name:
        pytest.skip("requires the pinned integrated native NOAA chart witnesses")
    path = Path(name)
    with path.open("rb") as stream:
        assert hashlib.file_digest(stream, "sha256").hexdigest() == NATIVE_SHA256
    stations = {}
    with path.open() as stream:
        for line in stream:
            station = json.loads(line)
            if station["station_id"] in TARGETS:
                stations[station["station_id"]] = station
    assert set(stations) == set(TARGETS)
    return stations


def source_chart_candidates(station):
    rid, _ = TARGETS[station["station_id"]]
    records = [record for record in station["source_records"]
               if record["source"] == "noaa" and record["source_record_id"] == rid]
    assert len(records) == 1
    return [candidate for candidate in records[0]["retained_datums"]
            if "MLLW" in candidate["values"]]


@pytest.mark.parametrize("station_id", TARGETS)
def test_actual_published_mllw_pointer_preserves_source_frame(actual_noaa_chart_stations, station_id, tmp_path):
    before = copy.deepcopy(actual_noaa_chart_stations[station_id])
    release = {"stations": [copy.deepcopy(before)]}
    candidates = source_chart_candidates(before)
    rid, value = TARGETS[station_id]
    assert candidates
    assert all(candidate["values"]["MLLW"] == value for candidate in candidates)
    assert all(candidate.get("basis", {}).get("chart_datum") is None for candidate in candidates)
    print(f"RED NOAA {rid}: held_MLLW={value}; frame=STND; explicit_chart_pointers=0")
    designate = getattr(retained_datums, "designate_noaa_published_charts", None)
    if designate is not None:
        receipt = designate(release)
        assert receipt == {"changed_stations": 1, "changed_candidates": len(candidates)}
    after = release["stations"][0]
    actual = source_chart_candidates(after)
    assert all(candidate["basis"].get("chart_datum") == "MLLW" for candidate in actual), (
        f"NOAA {rid}: held published MLLW lacks an explicit chart pointer")
    validator = Draft202012Validator({"$ref": "#/$defs/retained_datum", "$defs": pinned_schema()["$defs"]})
    for candidate in actual:
        assert candidate["values"][candidate["basis"]["chart_datum"]] == value
        assert candidate["kind"] == "published" and candidate["method"] == "source"
        assert candidate["units"] == "m"
        assert candidate["frame"] == {"kind": "gauge_zero", "name": "STND", "tie_status": "source_declared"}
        assert candidate["uncertainty_m"] is None
        assert candidate["reliability"] == {
            "status": "source_declared",
            "limitations": ["not_independently_validated", "not_selected_for_prediction"],
        }
        validator.validate(candidate)
    once = copy.deepcopy(release)
    assert designate(release) == {"changed_stations": 0, "changed_candidates": 0}
    assert release == once
    restored = copy.deepcopy(after)
    for candidate in source_chart_candidates(restored):
        candidate["basis"].pop("chart_datum")
    assert restored == before
    evidence = Path(os.environ.get("OTC_NOAA_PUBLISHED_CHART_EVIDENCE", tmp_path))
    evidence.mkdir(parents=True, exist_ok=True)
    for stage, station in (("before", before), ("after", after)):
        (evidence / f"{stage}-{station_id}.json").write_text(json.dumps(station, sort_keys=True, allow_nan=False) + "\n")
    print(f"GREEN NOAA {rid}: chart_pointer=basis.chart_datum:MLLW; STND_value={value}; "
          f"candidates={len(actual)}; all_original_values_bindings_reliability_unchanged=True")


def test_pointer_must_resolve_an_existing_finite_level():
    candidate = {"source_record_id": "8660754", "kind": "published", "values": {"MLLW": 3.456},
                 "basis": {"chart_datum": "missing"}}
    errors = retained_datums.candidate_errors(candidate, {"source_record_id": "8660754"}, {})
    assert any("chart_datum" in error for error in errors)


def test_finalizer_designates_the_same_actual_source_pointers(actual_noaa_chart_stations):
    release = {"stations": copy.deepcopy(list(actual_noaa_chart_stations.values()))}
    before = copy.deepcopy(release)
    report = retained_datums.finalize_datum_references(release)
    assert report["published_noaa_chart"] == {"changed_stations": 2, "changed_candidates": 3}
    assert report["chart_designation"]["changed_sets"] == 0
    for station in release["stations"]:
        for candidate in source_chart_candidates(station):
            assert candidate["basis"].pop("chart_datum") == "MLLW"
    assert release == before


def test_conflicting_pointer_is_preserved_before_any_target_mutates(actual_noaa_chart_stations):
    release = {"stations": [copy.deepcopy(actual_noaa_chart_stations[sid]) for sid in TARGETS]}
    source_chart_candidates(release["stations"][-1])[0]["basis"]["chart_datum"] = "LAT"
    before = copy.deepcopy(release)
    with pytest.raises(ValueError, match="existing retained chart pointer conflicts"):
        retained_datums.designate_noaa_published_charts(release)
    assert release == before
