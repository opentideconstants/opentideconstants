"""Retained harmonic levels: arithmetic and exact source/frame binding."""
import copy
from datetime import datetime, timedelta
import hashlib
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


def tidal_day_case():
    data = case()
    data["window"] = {"start": "2020-01-01T00:00:00+00:00", "end": "2020-02-01T00:00:00+00:00"}
    data["prediction"]["items"] = [
        {"time": "2020-01-01T06:00:00+00:00", "type": "high", "height_m": 2.0},
        {"time": "2020-01-01T12:00:00+00:00", "type": "low", "height_m": -1.0},
        {"time": "2020-01-31T06:00:00+00:00", "type": "high", "height_m": 2.0},
        {"time": "2020-01-31T12:00:00+00:00", "type": "low", "height_m": 0.4},
        {"time": "2020-01-31T18:00:00+00:00", "type": "high", "height_m": 2.1},
    ]
    padded = copy.deepcopy(data["prediction"])
    padded["items"] = [
        {"time": "2020-02-01T00:30:00+00:00", "type": "low", "height_m": 0.1},
        {"time": "2020-02-01T06:00:00+00:00", "type": "high", "height_m": 2.2},
    ]
    data["tidal_day_padding"] = {
        "prediction": padded,
        "window": {"start": data["window"]["end"], "end": "2020-02-02T00:50:29+00:00"},
        "input_binding": {**data["input_binding"], "request_sha256": "c" * 64, "results_sha256": "d" * 64},
    }
    return data


def hourly_case():
    data = case()
    data["window"]["end"] = "2020-01-01T04:00:00+00:00"
    series = copy.deepcopy(data["prediction"])
    series["items"] = [{"time": event["time"], "height_m": height}
                       for event, height in zip(series["items"], [2.0, -1.0, 1.0, 0.0])]
    data["hourly_heights"] = {
        "prediction": series, "window": copy.deepcopy(data["window"]),
        "input_binding": {**data["input_binding"], "request_sha256": "c" * 64, "results_sha256": "d" * 64},
    }
    return data


def test_hourly_mean_is_computed_in_its_actual_frame_and_preserves_default():
    data = hourly_case()
    before = copy.deepcopy(data)
    original = retained_levels(**{key: value for key, value in data.items() if key != "hourly_heights"})
    result = retained_levels(**data)
    assert result["retained_datums"][0] == original["retained_datums"][0]
    mean = result["retained_datums"][1]
    assert mean["values"] == {"msl": 0.5}
    assert mean["frame"]["kind"] == "harmonic_mean"
    assert mean["uncertainty_m"] is None
    assert mean["basis"]["hourly_sample_count"] == 4
    assert data == before


@pytest.mark.parametrize("change", ["convention", "native", "missing_hour", "nonfinite", "window"])
def test_hourly_mean_requires_complete_bound_same_source_series(change):
    data = hourly_case()
    hourly = data["hourly_heights"]
    if change == "convention":
        hourly["prediction"]["convention_id"] = "other"
    elif change == "native":
        hourly["input_binding"]["native_sha256"] = "other"
    elif change == "missing_hour":
        hourly["prediction"]["items"].pop(1)
    elif change == "nonfinite":
        hourly["prediction"]["items"][0]["height_m"] = float("nan")
    else:
        hourly["window"]["end"] = "2020-01-01T05:00:00+00:00"
    with pytest.raises(ValueError, match="Hourly"):
        retained_levels(**data)


def test_opt_in_spring_convention_uses_bound_hourly_mean_and_own_amplitudes():
    data = hourly_case()
    data["station"]["constant_sets"][0]["constituents"].append({"name": "S2", "amplitude_m": 0.25})
    original = retained_levels(**data)
    result = retained_levels(**data, spring_convention=True)
    assert result["retained_datums"][:-1] == original["retained_datums"]
    spring = result["retained_datums"][-1]
    assert spring["values"] == {"mhws": 1.75, "mlws": -0.75}
    assert spring["units"] == "m"
    assert "No station-local spring definition is evidenced; this is not an agency datum." in spring["reliability"]["limitations"]
    data.pop("hourly_heights")
    with pytest.raises(ValueError, match="Spring"):
        retained_levels(**data, spring_convention=True)


@pytest.mark.parametrize("m2,s2,expected", [(1.0, 0.25, {"mhwn": 1.25, "mlwn": -0.25}),
                                            (0.25, 1.0, {"mhwn": 1.25, "mlwn": -0.25}),
                                            (1.0, 1.0, {"mhwn": 0.5, "mlwn": 0.5})])
def test_opt_in_neap_plane_proxy_is_low_and_uses_own_mean_and_unsigned_envelope(m2, s2, expected):
    data = hourly_case()
    cs = data["station"]["constant_sets"][0]
    cs["constituents"][0]["amplitude_m"] = m2
    cs["constituents"].append({"name": "S2", "amplitude_m": s2})
    before = copy.deepcopy(data)
    original = retained_levels(**data, spring_convention=True)
    result = retained_levels(**data, spring_convention=True, neap_proxy=True)
    assert result["retained_datums"][:-1] == original["retained_datums"]
    proxy = result["retained_datums"][-1]
    assert proxy["values"] == expected
    assert proxy["basis"]["amplitudes_m"] == {"M2": m2, "S2": s2}
    assert proxy["basis"]["computed_msl_m"] == 0.5
    assert proxy["basis"]["not_selected_for_prediction"] is True
    assert proxy["basis"]["neap_plane_proxy"] == "approximation: two-constituent neap-plane proxy"
    assert "spring_convention" not in proxy["basis"]
    assert proxy["basis"]["qc_flags"] == cs["qc_flags"]
    assert proxy["frame"] == {"kind": "harmonic_mean", "name": None, "tie_status": "unknown"}
    assert proxy["uncertainty_m"] is None
    assert proxy["reliability"]["status"] == "low"
    assert "Not formal mean high/low water neaps: no neap-event averaging or surveyed datum is established." in proxy["reliability"]["limitations"]
    assert proxy["window"] == data["window"]
    assert proxy["input_binding"]["hourly_heights"]["input_binding"] == data["hourly_heights"]["input_binding"]
    assert data == before


@pytest.mark.parametrize("change", ["hourly", "missing", "duplicate", "negative", "nonfinite", "boolean"])
def test_neap_plane_proxy_rejects_missing_bound_mean_or_invalid_own_amplitudes(change):
    data = hourly_case()
    rows = data["station"]["constant_sets"][0]["constituents"]
    rows.append({"name": "S2", "amplitude_m": 0.25})
    if change == "hourly":
        del data["hourly_heights"]
    elif change == "missing":
        rows.pop()
    elif change == "duplicate":
        rows.append(copy.deepcopy(rows[-1]))
    else:
        rows[-1]["amplitude_m"] = {"negative": -0.1, "nonfinite": float("nan"), "boolean": True}[change]
    with pytest.raises(ValueError, match="Neap"):
        retained_levels(**data, neap_proxy=True)


def nllw_case():
    data = hourly_case()
    data["station"]["constant_sets"][0]["constituents"].extend([
        {"name": "S2", "amplitude_m": 0.25}, {"name": "K1", "amplitude_m": 0.1},
        {"name": "O1", "amplitude_m": 0.05}])
    return data


def test_nllw_uses_own_bound_hourly_mean_and_four_jhod_amplitudes():
    data = nllw_case()
    before = copy.deepcopy(data)
    original = retained_levels(**data)
    result = retained_levels(**data, nllw_convention=True, spring_convention=True)
    assert result["retained_datums"][:2] == original["retained_datums"]
    datum = next(d for d in result["retained_datums"] if "nllw" in d["values"])
    assert datum["values"] == pytest.approx({"nllw": 0.5 - 1 - 0.25 - 0.1 - 0.05})
    assert datum["basis"]["amplitudes_m"] == {"M2": 1, "S2": 0.25, "K1": 0.1, "O1": 0.05}
    assert datum["basis"]["computed_msl_m"] == 0.5
    assert datum["frame"] == {"kind": "harmonic_mean", "name": None, "tie_status": "unknown"}
    assert datum["uncertainty_m"] is None
    assert datum["reliability"]["status"] == "accuracy_unverified"
    assert "Computed JHOD four-constituent NLLW convention in the same harmonic-mean frame." in datum["reliability"]["limitations"]
    assert data == before


@pytest.mark.parametrize("change", ["hourly", "missing", "duplicate", "negative", "nonfinite", "boolean"])
def test_nllw_rejects_missing_mean_or_invalid_own_constituents(change):
    data = nllw_case()
    rows = data["station"]["constant_sets"][0]["constituents"]
    if change == "hourly":
        del data["hourly_heights"]
    elif change == "missing":
        rows.pop()
    elif change == "duplicate":
        rows.append(copy.deepcopy(rows[-1]))
    else:
        rows[-1]["amplitude_m"] = {"negative": -0.1, "nonfinite": float("nan"), "boolean": True}[change]
    with pytest.raises(ValueError, match="NLLW"):
        retained_levels(**data, nllw_convention=True)


def annual_low_case():
    data = case()
    data["prediction"]["items"] = [
        {"time": f"{year}-01-02T0{index}:00:00+00:00", "type": kind, "height_m": height}
        for year in range(2020, 2039)
        for index, (kind, height) in enumerate([("high", -100), ("low", -1 - (year - 2020)), ("low", 1)])]
    return data


def test_llwlt_is_the_mean_of_nineteen_annual_low_event_minima():
    data = annual_low_case()
    before = copy.deepcopy(data)
    original = retained_levels(**data)
    result = retained_levels(**data, llwlt_convention=True)
    assert result["retained_datums"][:-1] == original["retained_datums"]
    datum = result["retained_datums"][-1]
    assert datum["values"] == {"llwlt": -10}
    assert datum["basis"]["annual_minimum_low_events_m"] == {str(year): -1 - (year - 2020) for year in range(2020, 2039)}
    assert datum["basis"]["annual_low_event_counts"] == {str(year): 2 for year in range(2020, 2039)}
    assert datum["basis"]["year_count"] == 19
    assert datum["frame"]["kind"] == "harmonic_mean"
    assert datum["uncertainty_m"] is None
    assert datum["reliability"]["status"] == "accuracy_unverified"
    assert "Mean annual lowest predicted low water over nineteen complete calendar years." in datum["reliability"]["limitations"]
    assert data == before


@pytest.mark.parametrize("change", ["missing_year", "eighteen_years", "partial_year", "timezone"])
def test_llwlt_requires_nineteen_complete_calendar_years_with_low_events(change):
    data = annual_low_case()
    if change == "missing_year":
        data["prediction"]["items"] = [e for e in data["prediction"]["items"] if not (e["time"].startswith("2025") and e["type"] == "low")]
    elif change == "eighteen_years":
        data["window"]["end"] = "2038-01-01T00:00:00+00:00"
        data["prediction"]["items"] = [e for e in data["prediction"]["items"] if not e["time"].startswith("2038")]
    elif change == "partial_year":
        data["window"]["start"] = "2020-01-01T01:00:00+00:00"
    else:
        data["window"]["start"] = "2020-01-01T00:00:00+01:00"
    with pytest.raises(ValueError, match="LLWLT"):
        retained_levels(**data, llwlt_convention=True)


def model_digest(model):
    return hashlib.sha256(json.dumps(model, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def tlt_case():
    data = case()
    original = data["station"]["constant_sets"][0]
    original["constituents"].extend({"name": name, "amplitude_m": 0.1} for name in ["S2", "N2", "K2", "K1", "O1", "P1", "Q1", "M4", "SA"])
    for row in original["constituents"]:
        row.update({"phase_deg": 0.0, "speed_deg_per_hour": 1.0})
    projected = copy.deepcopy(original)
    projected["set_id"] += "/tlt-projection-v1"
    projected["recommended"] = False
    projected["constituents"] = [row for row in projected["constituents"] if row["name"] != "SA"]
    start = datetime.fromisoformat(data["window"]["start"])
    window = {"start": start.isoformat(), "end": (start + timedelta(seconds=18.6 * 365.25 * 86400)).isoformat()}
    prediction = copy.deepcopy(data["prediction"])
    prediction["set_id"] = projected["set_id"]
    boundaries = copy.deepcopy(prediction)
    boundaries["items"] = [{"time": window["start"], "height_m": -10}, {"time": window["end"], "height_m": -20}]
    data["tlt_projection"] = {
        "constant_set": projected, "prediction": prediction, "boundary_prediction": boundaries,
        "window": window, "year_basis_days": 365.25,
        "input_binding": {"native_sha256": "c" * 64, "sdk_commit": data["input_binding"]["sdk_commit"],
                          "source_native_sha256": data["input_binding"]["native_sha256"],
                          "source_constant_set_sha256": model_digest(original),
                          "projected_constant_set_sha256": model_digest(projected),
                          "events_request_sha256": "d" * 64, "events_results_sha256": "e" * 64,
                          "boundaries_request_sha256": "f" * 64, "boundaries_results_sha256": "1" * 64},
    }
    return data


def test_tlt_retains_only_explicit_selected_projection_minimum_including_boundaries():
    data = tlt_case()
    before = copy.deepcopy(data)
    original = retained_levels(**{key: value for key, value in data.items() if key != "tlt_projection"})
    result = retained_levels(**data)
    assert result["retained_datums"][:-1] == original["retained_datums"]
    datum = result["retained_datums"][-1]
    assert datum["values"] == {"tlt": -20}
    assert datum["set_id"] == "s/model"
    assert datum["basis"]["selected_constituents"] == ["M2", "S2", "N2", "K2", "K1", "O1", "P1", "Q1", "M4"]
    assert datum["basis"]["excluded_constituents"] == ["SA"]
    assert datum["basis"]["duration_years"] == 18.6
    assert datum["basis"]["year_basis_days"] == 365.25
    assert datum["window"] == data["tlt_projection"]["window"]
    assert datum["input_binding"]["tlt_projection"] == data["tlt_projection"]["input_binding"]
    assert datum["frame"] == {"kind": "harmonic_mean", "name": None, "tie_status": "unknown"}
    assert datum["uncertainty_m"] is None
    assert datum["reliability"]["status"] == "accuracy_unverified"
    assert "Not the full formal thirteen-constituent Vladimirsky result or an official GB datum." in datum["reliability"]["limitations"]
    assert data == before


@pytest.mark.parametrize("change", ["required", "optional", "coefficient", "original_hash", "projected_hash", "native", "sdk", "request", "frame", "boundary_frame", "boundary_missing", "boundary_time", "boundary_nonfinite", "window", "year_basis"])
def test_tlt_rejects_unbound_or_different_projection_and_incomplete_boundary_values(change):
    data = tlt_case()
    projection = data["tlt_projection"]
    if change in ("required", "optional"):
        name = "Q1" if change == "required" else "M4"
        projection["constant_set"]["constituents"] = [row for row in projection["constant_set"]["constituents"] if row["name"] != name]
    elif change == "coefficient":
        projection["constant_set"]["constituents"][0]["amplitude_m"] = 42
    elif change in ("original_hash", "projected_hash", "native", "sdk", "request"):
        key = {"original_hash": "source_constant_set_sha256", "projected_hash": "projected_constant_set_sha256", "native": "source_native_sha256", "sdk": "sdk_commit", "request": "events_request_sha256"}[change]
        projection["input_binding"][key] = "" if change == "request" else "different"
    elif change == "frame":
        projection["prediction"]["datum"] = "msl"
    elif change == "boundary_frame":
        projection["boundary_prediction"]["set_id"] = data["set_id"]
    elif change == "boundary_missing":
        projection["boundary_prediction"]["items"].pop()
    elif change == "boundary_time":
        projection["boundary_prediction"]["items"][-1]["time"] = data["window"]["end"]
    elif change == "boundary_nonfinite":
        projection["boundary_prediction"]["items"][-1]["height_m"] = float("nan")
    elif change == "window":
        projection["window"]["end"] = data["window"]["end"]
    else:
        projection["year_basis_days"] = 365
    with pytest.raises(ValueError, match="TLT"):
        retained_levels(**data)


@pytest.mark.parametrize("field,value", [("amplitude_m", None), ("amplitude_m", -1), ("phase_deg", None), ("phase_deg", float("nan")), ("speed_deg_per_hour", None)])
def test_tlt_never_accepts_missing_or_invalid_selected_source_coefficients(field, value):
    data = tlt_case()
    cs = data["station"]["constant_sets"][0]
    cs["constituents"][0][field] = value
    projected = data["tlt_projection"]["constant_set"]
    projected["constituents"][0][field] = value
    data["tlt_projection"]["input_binding"].update({
        "source_constant_set_sha256": model_digest(cs), "projected_constant_set_sha256": model_digest(projected)})
    with pytest.raises(ValueError, match="TLT"):
        retained_levels(**data)


def lunitidal_case():
    data = case()
    data["station"]["lon"] = -70.0
    data["station"]["constant_sets"][0]["constituents"][0].update({"phase_deg": 200.0, "speed_deg_per_hour": 28.9841042})
    data["lunitidal_convention"] = {
        "convention_id": "model-convention", "phase_reference": "greenwich_utc",
        "nodal_handling": "f_u_at_prediction", "astro_table_id": "table", "v0_model": "schureman_tcd",
    }
    return data


def test_approximate_intervals_are_hours_with_explicit_greenwich_reference():
    data = lunitidal_case()
    result = retained_levels(**data)["retained_datums"][-1]
    assert result["values"] == pytest.approx({"hwi": 200 / 28.9841042, "lwi": 20 / 28.9841042})
    assert result["units"] == "h"
    assert result["frame"] == {"kind": "elapsed_time", "name": "Greenwich lunar transit", "tie_status": "not_applicable"}
    assert result["basis"]["local_hwi_hours"] == pytest.approx(60 / 28.9841042)
    assert result["basis"]["local_lwi_hours"] == pytest.approx(240 / 28.9841042)
    assert result["uncertainty_m"] is None


@pytest.mark.parametrize("change", ["phase", "convention", "longitude", "speed"])
def test_intervals_reject_unverified_phase_or_invalid_m2(change):
    data = lunitidal_case()
    if change == "phase":
        data["lunitidal_convention"]["phase_reference"] = "local_standard"
    elif change == "convention":
        data["lunitidal_convention"]["convention_id"] = "other"
    elif change == "longitude":
        data["station"]["lon"] = None
    else:
        data["station"]["constant_sets"][0]["constituents"][0]["speed_deg_per_hour"] = 0
    with pytest.raises(ValueError, match="Lunitidal"):
        retained_levels(**data)


def test_opt_in_uses_existing_monthly_labels_and_preserves_original_candidate():
    data = tidal_day_case()
    before = copy.deepcopy(data)
    original = retained_levels(**{key: value for key, value in data.items() if key != "tidal_day_padding"})
    result = retained_levels(**data)
    assert result["retained_datums"][0] == original["retained_datums"][0]
    extra = result["retained_datums"][1]
    # February's lower low participates in January's last label window, but is
    # not itself averaged into January. This is the existing label_events rule.
    assert extra["values"]["mhhw"] == 2.05
    assert extra["values"]["mllw"] == -1.0
    assert extra["values"]["dtl"] == pytest.approx(0.525)
    assert extra["values"]["gt"] == pytest.approx(3.05)
    assert extra["values"]["dhq"] == pytest.approx(2.05 - (2.0 + 2.0 + 2.1) / 3)
    assert extra["values"]["dlq"] == pytest.approx(0.7)
    assert extra["basis"]["higher_high_count"] == 2
    assert extra["basis"]["lower_low_count"] == 1
    assert extra["basis"]["tidal_day_mode"] == "monthly"
    assert extra["frame"]["kind"] == "harmonic_mean"
    assert extra["uncertainty_m"] is None
    assert data == before


@pytest.mark.parametrize("change", ["convention", "native", "short_window", "outside_event"])
def test_opt_in_rejects_unbound_or_insufficient_padding(change):
    data = tidal_day_case()
    padding = data["tidal_day_padding"]
    if change == "convention":
        padding["prediction"]["convention_id"] = "other"
    elif change == "native":
        padding["input_binding"]["native_sha256"] = "other"
    elif change == "short_window":
        padding["window"]["end"] = "2020-02-01T12:00:00+00:00"
    else:
        padding["prediction"]["items"][0]["time"] = "2020-01-31T23:59:59+00:00"
    with pytest.raises(ValueError, match="padding"):
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


@pytest.mark.skipif("OTC_HARMONIC_LAST_NINE" not in os.environ, reason="actual nine-place derived payload")
def test_actual_nine_candidates_attach_without_changing_original_records():
    root = Path(os.environ["OTC_HARMONIC_LAST_NINE"])
    rows = [json.loads(line) for line in (root / "derived-datums.jsonl").read_text().splitlines()]
    assert len(rows) == 9
    for row in rows:
        station = json.loads((root / f"{row['station_id']}-input.json").read_text())["station"]
        before = copy.deepcopy(station)
        count = len(row["retained_datums"])
        assert retain_candidates({"stations": [station]}, [row])["added_candidates"] == count
        record = next(record for record in station["source_records"]
                      if record["source"] == row["source"] and record["source_record_id"] == row["source_record_id"]
                      and record.get("source_version") == row.get("source_version"))
        assert record["retained_datums"][-count:] == row["retained_datums"]
        del record["retained_datums"][-count:]
        if not record["retained_datums"]:
            record.pop("retained_datums")
        assert station == before
