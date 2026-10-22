"""The nested offset datum accepts the approved algebraic-zero method."""
import copy
import hashlib
import json
import os
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from otc_pipeline import lat_hat_subordinate, release_writer


IDS = ("OTC-01592bc8", "OTC-055ad107", "OTC-05a34359")


@pytest.fixture(scope="module")
def actual_stations():
    path = os.environ.get("OTC_SUBORDINATE_SCHEMA_NATIVE")
    if not path:
        pytest.skip("requires the unchanged native subordinate source records")
    native = Path(path)
    assert hashlib.sha256(native.read_bytes()).hexdigest() == (
        "7695d765a6787fb21c51d41cb6449b6699f019489c25e7a3dbbb6aa2aad7d1e5")
    with native.open() as stream:
        stations = {row["station_id"]: row for line in stream
                    if (row := json.loads(line))["station_id"] in IDS}
    assert set(stations) == set(IDS)
    return stations


def errors(station):
    schema = release_writer.pinned_schema()
    validator = Draft202012Validator({"$schema": schema["$schema"], "$defs": schema["$defs"],
                                      "$ref": "#/$defs/station"})
    return [f"{'/'.join(map(str, error.absolute_path))}: {error.message}"
            for top in validator.iter_errors(station) for error in release_writer._leaf_errors(top)]


@pytest.fixture(scope="module")
def actual_reference_stations(actual_stations):
    wanted = {row["subordinate_offsets"]["reference_station_id"] for row in actual_stations.values()}
    with Path(os.environ["OTC_SUBORDINATE_SCHEMA_NATIVE"]).open() as stream:
        stations = {row["station_id"]: row for line in stream
                    if (row := json.loads(line))["station_id"] in wanted}
    assert set(stations) == wanted
    return stations


@pytest.mark.parametrize("station_id", IDS)
def test_actual_subordinate_offset_zero_station_schema(actual_stations, station_id):
    original = actual_stations[station_id]
    station = copy.deepcopy(original)
    datum = lat_hat_subordinate.offset_chart_datum(station)
    assert datum["basis"]["mllw"]["method"] == "subordinate_offset_zero"
    station["subordinate_offsets"]["datum"] = datum
    failures = errors(station)
    print(f"REAL {station_id}: chart=mllw value=0.0 schema_errors={failures}")
    assert failures == []
    if "datum" in original["subordinate_offsets"]:
        station["subordinate_offsets"]["datum"] = original["subordinate_offsets"]["datum"]
    else:
        del station["subordinate_offsets"]["datum"]
    assert station == original


@pytest.mark.parametrize("change", ["missing_fallback", "epoch", "uncertainty", "other_method"])
def test_actual_subordinate_zero_restrictions_remain(actual_stations, change):
    station = copy.deepcopy(actual_stations[IDS[0]])
    station["subordinate_offsets"]["datum"] = lat_hat_subordinate.offset_chart_datum(station)
    basis = station["subordinate_offsets"]["datum"]["basis"]["mllw"]
    if change == "missing_fallback":
        del basis["fallback"]
    elif change == "epoch":
        basis["epoch"] = {"start": "2020-01-01", "end": "2038-12-31", "name": "Invented epoch"}
    elif change == "uncertainty":
        basis.update(uncertainty_m=0.1, uncertainty_basis="propagated")
    else:
        basis["method"] = "harmonic_extremes"
        basis["epoch"] = {"start": "2020-01-01", "end": "2038-12-31", "name": "Not an offset method"}
    assert errors(station)


@pytest.mark.parametrize("station_id", IDS)
@pytest.mark.parametrize("change", ["nonzero", "other_level", "other_level_alongside_valid",
                                   "wrong_zero", "wrong_zero_name", "wrong_chart",
                                   "missing_zero", "missing_zero_name", "missing_chart", "constant_set"])
def test_actual_subordinate_zero_is_only_its_mllw_frame(actual_stations, actual_reference_stations, station_id, change):
    station = copy.deepcopy(actual_stations[station_id])
    datum = lat_hat_subordinate.offset_chart_datum(station)
    station["subordinate_offsets"]["datum"] = datum
    assert errors(station) == []
    if change == "nonzero":
        datum["named"]["mllw"] = 1.25
    elif change == "other_level":
        datum["basis"]["hat"] = datum["basis"].pop("mllw")
        datum["named"]["hat"] = datum["named"].pop("mllw")
        datum["chart_datum"] = "hat"
    elif change == "other_level_alongside_valid":
        datum["basis"]["hat"] = copy.deepcopy(datum["basis"]["mllw"])
    elif change == "wrong_zero":
        datum["zero"] = "gauge_zero"
    elif change == "wrong_zero_name":
        datum["zero_name"] = "STND"
    elif change == "wrong_chart":
        datum["chart_datum"] = "hat"
    elif change.startswith("missing_"):
        del datum[{"missing_zero": "zero", "missing_zero_name": "zero_name", "missing_chart": "chart_datum"}[change]]
    else:
        # The actual subordinate's own model set has no constants, so exercise
        # method placement on its actual reference's published NOAA constants.
        station = copy.deepcopy(actual_reference_stations[station["subordinate_offsets"]["reference_station_id"]])
        assert errors(station) == []
        cs = next(cs for cs in station["constant_sets"] if cs["source"] == "noaa" and cs.get("constituents"))
        cs["datum"] = copy.deepcopy(datum)
        cs["datum"]["named"] = {"mllw": datum["named"]["mllw"]}
        cs["datum"]["basis"] = {"mllw": datum["basis"]["mllw"]}
    failures = errors(station)
    print(f"REAL {station_id}: {change} rejected={bool(failures)}")
    assert failures, f"subordinate_offset_zero accepted outside its identity: {station_id} {change}"
