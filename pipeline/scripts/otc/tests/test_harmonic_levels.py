"""Retained harmonic levels: arithmetic and exact source/frame binding."""
import copy
import json
import os
from pathlib import Path

import pytest

from otc_pipeline.harmonic_levels import retained_levels
from otc_pipeline.retained_datums import retain_candidates


def case():
    cs = {"set_id": "s/model", "source": "model", "source_record_id": "record",
          "source_version": "v1", "quantity": "water_level", "qc_status": "flagged", "convention_id": "model-convention",
          "qc_flags": [{"flag": "accuracy_unverified"}], "constituents": [{"name": "M2", "amplitude_m": 1}]}
    station = {"id": "s", "constant_sets": [cs], "source_records": [
        {"source": "model", "source_record_id": "record", "source_version": "v1",
         "published_set_ids": ["s/model"]}]}
    prediction = {"station_id": "s", "set_id": "s/model", "method": "harmonic",
                  "datum": "mean_level", "convention_id": "model-convention", "astro_table_id": "table", "algorithm": "otc-predict-1",
                  "items": [{"type": kind, "height_m": height, "time": f"2020-01-01T0{i}:00:00+00:00"}
                            for i, (kind, height) in enumerate([("high", 2), ("low", -1), ("high", 4), ("low", -3)])]}
    return {"station": station, "set_id": "s/model", "prediction": prediction,
            "window": {"start": "2020-01-01T00:00:00+00:00", "end": "2039-01-01T00:00:00+00:00"},
            "input_binding": {"native_sha256": "a" * 64, "sdk_commit": "b" * 40}}


def assert_event_extrema_limitations(datum):
    limitations = datum["reliability"]["limitations"]
    assert "LAT/HAT are event-sampled minima/maxima of the returned extreme-event heights." in limitations
    assert "Window boundary heights were not evaluated; continuous-window extrema are not established." in limitations


def test_event_weighted_levels_keep_relative_frame_and_source_flags():
    data = case()
    before = copy.deepcopy(data)
    row = retained_levels(**data)
    datum = row["retained_datums"][0]
    assert datum["values"] == {"mhw": 3, "mlw": -2, "mtl": 0.5, "mn": 5, "lat": -3, "hat": 4}
    assert datum["frame"] == {"kind": "harmonic_mean", "name": None, "tie_status": "unknown"}
    assert datum["uncertainty_m"] is None
    assert_event_extrema_limitations(datum)
    assert datum["basis"]["qc_flags"] == data["station"]["constant_sets"][0]["qc_flags"]
    assert "msl" not in datum["values"] and "mhhw" not in datum["values"]
    assert row["source_record_id"] == "record"
    assert data == before


@pytest.mark.parametrize("change", ["set", "frame", "source", "nonfinite", "outside"])
def test_rejects_mixed_or_invalid_event_provenance(change):
    data = case()
    if change == "set":
        data["prediction"]["set_id"] = "other"
    elif change == "frame":
        data["prediction"]["datum"] = "msl"
    elif change == "source":
        data["station"]["source_records"][0]["published_set_ids"] = []
    elif change == "nonfinite":
        data["prediction"]["items"][0]["height_m"] = float("nan")
    else:
        data["prediction"]["items"][0]["time"] = "2019-12-31T23:59:59+00:00"
    with pytest.raises(ValueError):
        retained_levels(**data)


@pytest.mark.parametrize("set_source,record_source", [("gesla-fit", "gesla"), ("ticon-4", "ticon")])
def test_derived_sets_bind_their_publishing_source_not_the_first_record(set_source, record_source):
    data = case()
    data["station"]["constant_sets"][0]["source"] = set_source
    data["station"]["source_records"][0]["source"] = record_source
    data["station"]["source_records"].insert(0, {
        "source": record_source, "source_record_id": "unrelated", "source_version": "v1",
        "published_set_ids": []})
    result = retained_levels(**data)
    assert result["source"] == record_source
    assert result["source_record_id"] == "record"


@pytest.mark.parametrize("invalid", ["different", "missing_prediction", "null_prediction", "missing_both", "empty_both"])
def test_rejects_missing_or_mismatched_harmonic_convention(invalid):
    data = case()
    cs = data["station"]["constant_sets"][0]
    if invalid == "different":
        data["prediction"]["convention_id"] = "other-convention"
    elif invalid == "null_prediction":
        data["prediction"]["convention_id"] = None
    elif invalid == "empty_both":
        cs["convention_id"] = data["prediction"]["convention_id"] = ""
    else:
        del data["prediction"]["convention_id"]
        if invalid == "missing_both":
            del cs["convention_id"]
    with pytest.raises(ValueError, match="convention"):
        retained_levels(**data)


@pytest.mark.skipif("OTC_HARMONIC_LEVELS_PROOF" not in os.environ, reason="actual held source/event proof")
def test_real_pilot_matches_independent_event_arithmetic():
    root = Path(os.environ["OTC_HARMONIC_LEVELS_PROOF"])
    data = json.loads((root / "pilot-input.json").read_text())
    expected = json.loads((root / "pilot-independent-values.json").read_text())
    result = retained_levels(**data)["retained_datums"][0]
    assert result["values"] == pytest.approx(expected, abs=1e-12)
    assert result["set_id"] == "OTC-1df2efef/fes2022"


@pytest.mark.skipif("OTC_HARMONIC_LEVELS_PROOF" not in os.environ, reason="actual held source/event proof")
@pytest.mark.parametrize("pilot", ["", "akabane", "amami"], ids=["fes", "akabane", "amami"])
def test_real_pilots_label_event_extrema_and_preserve_payload(pilot):
    root = Path(os.environ["OTC_HARMONIC_LEVELS_PROOF"]) / pilot
    data = json.loads((root / "pilot-input.json").read_text())
    before = copy.deepcopy(data)
    previous = json.loads((root / "candidate.jsonl").read_text())
    result = retained_levels(**data)
    datum = result["retained_datums"][0]
    limitations = datum["reliability"]["limitations"]
    assert_event_extrema_limitations(datum)
    restored = copy.deepcopy(result)
    restored["retained_datums"][0]["reliability"]["limitations"] = previous["retained_datums"][0]["reliability"]["limitations"]
    assert restored == previous
    assert data == before

    station = copy.deepcopy(data["station"])
    assert retain_candidates({"stations": [station]}, [result])["added_candidates"] == 1
    record = next(record for record in station["source_records"]
                  if datum in record.get("retained_datums", []))
    assert record["retained_datums"][-1]["reliability"]["limitations"] == limitations
    record.pop("retained_datums")
    assert station == before["station"]
