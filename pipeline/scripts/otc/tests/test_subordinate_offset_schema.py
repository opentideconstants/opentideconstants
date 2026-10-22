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
