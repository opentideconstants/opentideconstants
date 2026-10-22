"""Actual cached NOAA references without harmonics retain source datum numbers.

Opt in with OTC_NOAA_DATUM_RETENTION_CACHE and OTC_NOAA_DATUM_RETENTION_SOURCE.
These are the three pinned audit witnesses, not invented API response fixtures.
"""
from __future__ import annotations

import copy
import hashlib
import importlib
import importlib.util
import json
import math
import os
from pathlib import Path

import pytest

from otc_pipeline.adapters.noaa import NoaaAdapter, noaa_datum
from otc_pipeline.combine import no_network


RETENTION_SPEC = importlib.util.find_spec("otc_pipeline.retained_datums")
RETENTION = importlib.import_module("otc_pipeline.retained_datums") if RETENTION_SPEC is not None else None
SOURCE_SHA256 = "7695d765a6787fb21c51d41cb6449b6699f019489c25e7a3dbbb6aa2aad7d1e5"
WITNESSES = {
    "8660754": "96ed7a6175f23e125c884f14159b8593a13c44a2549c7adc918c22bc340746a8",
    "9751388": "0f4b7c88d808f1b74b1cd8c99389c53183e9fa239ef374f6cd3c38ae05508816",
    "9450623": "80ba072da540b4e73f8dcf4207166525874337a443b1efb44f68951e2c22a1c8",
}


@pytest.fixture(scope="module")
def actual_noaa_without_harmonics():
    cache_name = os.environ.get("OTC_NOAA_DATUM_RETENTION_CACHE")
    source_name = os.environ.get("OTC_NOAA_DATUM_RETENTION_SOURCE")
    if not cache_name or not source_name:
        pytest.skip("requires the pinned NOAA cache and original native release")
    cache, source = Path(cache_name), Path(source_name)
    with source.open("rb") as stream:
        assert hashlib.file_digest(stream, "sha256").hexdigest() == SOURCE_SHA256
    stations = {}
    with source.open() as stream:
        for line in stream:
            station = json.loads(line)
            for record in station.get("source_records", []):
                if record["source"] == "noaa" and record["source_record_id"] in WITNESSES:
                    assert record["source_record_id"] not in stations
                    stations[record["source_record_id"]] = station
    assert set(stations) == set(WITNESSES)
    adapter = NoaaAdapter()
    with no_network():
        fetched = adapter.fetch(cache, only=list(WITNESSES))
        assert list(adapter.sets(fetched)) == []
    assert adapter.skipped(fetched) == {rid: "no harmonic constituents" for rid in WITNESSES}
    for rid, digest in WITNESSES.items():
        assert hashlib.sha256((cache / "datums" / (rid + ".json")).read_bytes()).hexdigest() == digest
        assert fetched.items[rid]["harcon"]["HarmonicConstituents"] == []
    return cache, fetched, stations


def source_numbers(body):
    """Every finite number in NOAA's datum table or explicit extreme fields."""
    values = [(row["name"], row["value"]) for row in body["datums"]
              if isinstance(row.get("value"), (int, float)) and not isinstance(row["value"], bool)
              and math.isfinite(row["value"])]
    values.extend((name, body[name]) for name in ("LAT", "HAT", "min", "max")
                  if isinstance(body.get(name), (int, float)) and not isinstance(body[name], bool)
                  and math.isfinite(body[name]))
    return values


@pytest.mark.parametrize("rid", WITNESSES)
def test_actual_noaa_datums_survive_without_predictive_constants(actual_noaa_without_harmonics, rid):
    cache, fetched, stations = actual_noaa_without_harmonics
    release = {"stations": [copy.deepcopy(stations[rid])]}
    before = copy.deepcopy(release)
    record = next(row for row in release["stations"][0]["source_records"]
                  if row["source"] == "noaa" and row["source_record_id"] == rid)
    assert not record.get("retained_datums")
    body = fetched.items[rid]["datums"]
    expected = source_numbers(body)
    assert expected
    raw_rows = json.loads(record.get("original_metadata", {}).get("raw_metadata_rows_json", "[]"))
    parsed, _ = noaa_datum(body, with_msl_offset=False)
    print(f"NOAA {rid}: predictive_sets=0; source_values={len(expected)}; "
          f"normalized_parser_available={parsed is not None}; numerical_records_before=0; "
          f"original_response_already_retained={body in raw_rows}")
    if RETENTION is not None:
        RETENTION.retain_source_datums(release, noaa_cache=cache)
    retained = record.get("retained_datums", [])
    actual = [(name, value) for candidate in retained for name, value in candidate["values"].items()]
    assert all(pair in actual for pair in expected), f"NOAA {rid}: published values lack numerical source datum records"
    for candidate in retained:
        assert candidate["kind"] == "published" and candidate["method"] == "source"
        assert candidate["source_record_id"] == rid
        assert "set_id" not in candidate
        assert candidate["uncertainty_m"] is None
        assert candidate["reliability"] == {
            "status": "source_declared",
            "limitations": ["not_independently_validated", "not_selected_for_prediction"],
        }
        binding = candidate["input_binding"]
        if "sha256" in binding:
            assert binding["sha256"] == WITNESSES[rid]
            receipt = Path(str(cache / "datums" / (rid + ".json")) + ".meta.json")
            assert binding["receipt_sha256"] == hashlib.sha256(receipt.read_bytes()).hexdigest()
        else:
            assert binding["original_provenance"] == record["provenance"]
            assert binding["metadata_inputs"] == json.loads(record["original_metadata"]["metadata_inputs_json"])
        for name, value in candidate["values"].items():
            assert (name, value) in expected
            if name in ("HWI", "LWI"):
                assert candidate["units"] == "h" and candidate["frame"]["kind"] == "elapsed_time"
            else:
                assert candidate["units"] == "m"
            if name not in ("HWI", "LWI", "GT", "MN", "DHQ", "DLQ", "CRD_OFFSET"):
                assert candidate["frame"] == {"kind": "gauge_zero", "name": "STND", "tie_status": "source_declared"}
    once = copy.deepcopy(release)
    RETENTION.retain_source_datums(release, noaa_cache=cache)
    assert release == once
    record.pop("retained_datums")
    assert release == before
    print(f"GREEN NOAA {rid}: exact_source_values={len(expected)}; candidates={len(retained)}; "
          "station_prediction_and_provenance_unchanged=True; chart_row_closure_not_claimed=True")
