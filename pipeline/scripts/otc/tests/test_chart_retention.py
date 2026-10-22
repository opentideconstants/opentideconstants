"""Retain source-linked computed LAT as an explicitly nonofficial chart reference."""
import copy
import json
import os
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from otc_pipeline.datums import derive_chart_datums
from otc_pipeline.release_writer import pinned_schema


RECORDS = ["aburatsubo-gs01-jpn-jodc_giaj", "akita-2002-jpn-jodc_pahb",
           "aden-172-yem-uhslc_fd"]


@pytest.fixture(scope="module")
def held_chart_stations():
    source = os.environ.get("OTC_CHART_RETENTION_NATIVE")
    if not source:
        pytest.skip("requires the held native release for real-source chart retention proof")
    stations = {}
    with Path(source).open() as stream:
        for line in stream:
            station = json.loads(line)
            for cs in station.get("constant_sets", []):
                record_id = cs.get("source_record_id")
                if record_id in RECORDS and "lat" in cs.get("datum", {}).get("named", {}):
                    stations[record_id] = station
    assert set(stations) == set(RECORDS)
    return stations


@pytest.mark.parametrize("record_id", RECORDS)
def test_held_computed_lat_gets_nonofficial_reference_without_country_allowlist(held_chart_stations, record_id):
    station = copy.deepcopy(held_chart_stations[record_id])
    before = copy.deepcopy(station)
    receipt = derive_chart_datums({"stations": [station]})
    changed = []
    validator = Draft202012Validator({"$ref": "#/$defs/datum", "$defs": pinned_schema()["$defs"]})
    for cs, old_cs in zip(station["constant_sets"], before["constant_sets"]):
        datum = cs.get("datum", {})
        old = old_cs.get("datum", {})
        if "lat" not in old.get("named", {}) or old.get("chart_datum") is not None:
            assert cs == old_cs
            continue
        assert datum.get("chart_datum") == "lat", f"held LAT withheld: {record_id} {cs['set_id']}"
        assert datum["named"] == old["named"]
        assert datum.get("zero") == old.get("zero")
        assert datum.get("msl_offset_m") == old.get("msl_offset_m")
        basis = datum["basis"]["lat"]
        assert basis["kind"] == "computed"
        assert "convention_unverified" in basis["flags"]
        assert "approximate chart-reference stand-in" in basis["fallback"]["basis"]
        assert "nonofficial" in basis["fallback"]["basis"]
        assert "convention unverified" in basis["fallback"]["otc_finding"]
        for field in ("uncertainty_m", "uncertainty_basis"):
            assert basis.get(field) == old["basis"]["lat"].get(field)
        validator.validate(datum)
        changed.append(cs["set_id"])
    assert changed and receipt["changed_places"] == 1
    assert station["source_records"] == before["source_records"]
    assert station.get("recommended_set_id") == before.get("recommended_set_id")
    after = copy.deepcopy(station)
    assert derive_chart_datums({"stations": [station]})["changed_sets"] == 0
    assert station == after
    print(f"VALUE {record_id}: {len(changed)} retained computed LAT references; values, frames, provenance preserved")


def test_native_release_reference_designation_preserves_all_held_payloads():
    source = os.environ.get("OTC_CHART_RETENTION_NATIVE")
    output = os.environ.get("OTC_CHART_RETENTION_OUTPUT")
    if not source or not output:
        pytest.skip("requires held native release and a proof output directory")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    changes = []
    station_count = 0
    validator = Draft202012Validator({"$ref": "#/$defs/datum", "$defs": pinned_schema()["$defs"]})
    with Path(source).open() as stream, (output / "candidate.jsonl").open("w") as candidate:
        for line in stream:
            station = json.loads(line)
            before = copy.deepcopy(station)
            receipt = derive_chart_datums({"stations": [station]})
            station_count += 1
            if not receipt["changed_sets"]:
                assert station == before
                candidate.write(line)
                continue
            for cs, old in zip(station["constant_sets"], before["constant_sets"]):
                if cs == old:
                    continue
                datum = cs["datum"]
                previous = old["datum"]
                assert "chart_datum" not in previous
                assert datum["chart_datum"] == "lat"
                assert datum["named"] == previous["named"]
                validator.validate(datum)
                restored = copy.deepcopy(cs)
                del restored["datum"]["chart_datum"]
                restored["datum"]["basis"]["lat"] = copy.deepcopy(previous["basis"]["lat"])
                assert restored == old
                changes.append({"station_id": station["station_id"], "set_id": cs["set_id"],
                                "source_record_id": cs["source_record_id"], "country": station.get("country"),
                                "lat_m": datum["named"]["lat"]})
            assert station["source_records"] == before["source_records"]
            assert station.get("recommended_set_id") == before.get("recommended_set_id")
            candidate.write(json.dumps(station, separators=(",", ":")) + "\n")
    assert station_count == 16039
    assert changes
    (output / "changes.json").write_text(json.dumps(changes, indent=2) + "\n")
    print(f"NATIVE {station_count} stations: {len(changes)} chart references added; every held value and source record preserved")
