"""Bounded real-engine controls; pinned native inputs supplied by the method gate."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from otc_pipeline.lat_hat_subordinate import datum_draft, offset_chart_datum


INPUT = os.environ.get("OTC_SUBORDINATE_INPUT")
CHART_INPUTS = os.environ.get("OTC_SUBORDINATE_CHART_INPUTS")


@pytest.mark.parametrize("index", range(3))
def test_retained_cached_subordinate_chart_origin(index):
    if not CHART_INPUTS:
        pytest.skip("set OTC_SUBORDINATE_CHART_INPUTS to the retained real cohort witnesses")
    witness = json.loads(Path(CHART_INPUTS).read_text())[index]
    payload, raw = witness["payload"], witness["raw"]
    station = copy.deepcopy(payload["station"])
    original = datum_draft(payload, raw)
    station["subordinate_offsets"]["datum"] = original
    datum = offset_chart_datum(station)
    print(f"{payload['station']['station_id']}: zero={datum.get('zero_name')} "
          f"chart={datum.get('chart_datum')} mllw={datum.get('named', {}).get('mllw')}")
    assert datum["zero_name"] == "MLLW"
    assert datum.get("chart_datum") == "mllw"
    assert datum["named"]["mllw"] == 0.0
    assert datum["named"]["lat"] == raw["lat_m"]
    assert datum["named"]["hat"] == raw["hat_m"]
    assert station["subordinate_offsets"]["datum"] == original
    basis = datum["basis"]["mllw"]
    assert basis["kind"] == "computed"
    assert basis["method"] == "subordinate_offset_zero"
    assert basis["fallback"]["reason"] == "datum_gate_failed"
    assert station["subordinate_offsets"]["reference_station_id"] in basis["fallback"]["basis"]
    assert not {"epoch", "uncertainty_m", "control"} & basis.keys()


@pytest.fixture
def offset_station():
    return {"station_id": "OTC-subordinate", "type": "subordinate", "aliases": {"noaa": "123"},
            "source_records": [{"source": "noaa", "source_record_id": "123"}],
            "subordinate_offsets": {"reference_station_id": "OTC-reference", "licence_id": "noaa-public-domain"}}


def test_offset_zero_without_extrema_preserves_source_and_unknown_uncertainty(offset_station):
    before = copy.deepcopy(offset_station)
    datum = offset_chart_datum(offset_station)
    assert datum["chart_datum"] == "mllw"
    assert datum["named"] == {"mllw": 0.0}
    assert set(datum["basis"]["mllw"]) == {"kind", "method", "fallback"}
    assert offset_station == before


@pytest.mark.parametrize("datum", [
    {"zero": "chart_datum", "zero_name": "MLLW", "chart_datum": "lat", "named": {"lat": -0.4}},
    {"zero": "chart_datum", "zero_name": "MLLW", "named": {"mllw": 0.2}},
    {"zero": "gauge_zero", "zero_name": "STND", "named": {}},
])
def test_offset_zero_keeps_existing_datum_conflicts(offset_station, datum):
    offset_station["subordinate_offsets"]["datum"] = datum
    result = offset_chart_datum(offset_station)
    assert result == datum
    assert result is not datum


@pytest.mark.parametrize("mutation", ["reference", "noaa_alias", "source_record", "licence", "reference_id"])
def test_offset_zero_requires_actual_noaa_offset_provenance(offset_station, mutation):
    if mutation == "reference":
        offset_station["type"] = "reference"
    elif mutation == "noaa_alias":
        offset_station["aliases"] = {}
    elif mutation == "source_record":
        offset_station["source_records"][0]["source"] = "gesla-fit"
    elif mutation == "licence":
        offset_station["subordinate_offsets"]["licence_id"] = "other"
    else:
        offset_station["subordinate_offsets"].pop("reference_station_id")
    assert offset_chart_datum(offset_station) is None


def test_actual_chart_cohort_preserves_held_material():
    cohort = os.environ.get("OTC_SUBORDINATE_CHART_COHORT")
    if not cohort:
        pytest.skip("set OTC_SUBORDINATE_CHART_COHORT to the frozen 247-station native cohort")
    stations = json.loads(Path(cohort).read_text())
    assert len(stations) == 247
    assert len({station["station_id"] for station in stations}) == 247
    rows = []
    for station in stations:
        original = copy.deepcopy(station)
        previous = station["subordinate_offsets"].get("datum", {})
        assert not previous.get("chart_datum")
        datum = offset_chart_datum(station)
        assert datum["chart_datum"] == "mllw"
        assert datum["named"]["mllw"] == 0.0
        for key, value in previous.get("named", {}).items():
            assert datum["named"][key] == value
            assert datum["basis"][key] == previous["basis"][key]
        basis = datum["basis"]["mllw"]
        assert basis["method"] == "subordinate_offset_zero"
        assert basis["fallback"]["reason"] == "datum_gate_failed"
        assert not {"epoch", "uncertainty_m", "uncertainty_basis", "control"} & basis.keys()
        assert station == original
        rows.append({"station_id": station["station_id"], "datum": datum})
    output = os.environ.get("OTC_SUBORDINATE_CHART_OUTPUT")
    if output:
        Path(output).write_text("".join(json.dumps(row, allow_nan=False) + "\n" for row in rows))
    print("247 distinct listed stations: 0 -> 247 chart pointers; all retained levels, labels and source records preserved")


@pytest.fixture
def payload():
    if not INPUT:
        pytest.skip("set OTC_SUBORDINATE_INPUT to a sealed native tuning input")
    return json.loads(Path(INPUT).read_text())


def test_real_boundary_applies_subordinate_offsets(payload):
    reference = payload["reference_set"]
    offsets = payload["station"]["subordinate_offsets"]
    datum = reference["datum"]
    term = datum["msl_offset_m"] - datum["named"][datum["chart_datum"]]
    request = {"astro_table": payload["astro_table"], "constituents": reference["constituents"],
               "msl_offset_m": term, "subordinate_offsets": offsets,
               "start": "2020-01-01T00:00:00Z", "end": "2020-01-03T00:00:00Z"}
    wrapper = Path(__file__).parents[1] / "otc_pipeline/lat_hat_strict.rb"
    actual = json.loads(subprocess.check_output(["ruby", str(wrapper)], input=json.dumps(request), text=True))
    direct = """require 'json'; require 'time'; require File.join(ENV.fetch('OTC_ENGINE_WORKTREE'), 'lib/harmonics_strict'); r=JSON.parse(STDIN.read); a=Harmonics::Strict::AstroTable.from_h(r.fetch('astro_table')); e=Harmonics::Engine.allocate; v=e.subordinate_extremes_strict(r.fetch('constituents'),r.fetch('msl_offset_m'),r.fetch('subordinate_offsets'),Time.iso8601(r.fetch('start')),Time.iso8601(r.fetch('end')),astro:a); puts JSON.generate([v.select{|x|x['type']=='low'}.map{|x|x['height']}.min,v.select{|x|x['type']=='high'}.map{|x|x['height']}.max])"""
    expected = json.loads(subprocess.check_output(["ruby", "-e", direct], input=json.dumps(request), text=True))
    assert [actual["lat_m"], actual["hat_m"]] == expected


def test_reference_and_chart_origin(payload):
    from otc_pipeline.lat_hat_subordinate import strict_request
    request = strict_request(payload)
    assert request["msl_offset_m"] == payload["reference_set"]["datum"]["msl_offset_m"]
    for mutation, error in (("removed", "reference_unavailable"), ("wrong_source", "reference_unavailable"),
                            ("unpublished", "datum_unavailable")):
        altered = copy.deepcopy(payload)
        if mutation == "removed":
            altered["reference_station"]["status"] = "removed"
        elif mutation == "wrong_source":
            altered["reference_set"]["source"] = "gesla-fit"
        else:
            altered["reference_set"]["datum"]["basis"]["mllw"]["kind"] = "computed"
        with pytest.raises(ValueError, match=error):
            strict_request(altered)


def test_real_production_batch_cli_matches_single_requests(payload, tmp_path):
    from otc_pipeline.lat_hat_subordinate import compute_raw

    payload = copy.deepcopy(payload)
    payload.pop("comparison")
    batch = tmp_path / "batch.json"
    output = tmp_path / "raw.jsonl"
    batch.write_text(json.dumps([payload, payload]))
    subprocess.run([sys.executable, "-m", "otc_pipeline.lat_hat_subordinate", "--batch-input", str(batch),
                    "--out", str(output), "--start", "2020-01-01T00:00:00Z", "--end", "2020-01-03T00:00:00Z"], check=True)
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    expected = compute_raw(payload, start="2020-01-01T00:00:00Z", end="2020-01-03T00:00:00Z")
    assert len(rows) == 2
    for row in rows:
        for key in ("lat_m", "hat_m", "lat_time", "hat_time", "low_count", "high_count", "chunks", "request_sha256"):
            assert row["raw"][key] == expected[key]
        assert "draft_datum" not in row
