"""Arithmetic method tests; real NOAA tuning runs are explicit, never the holdout."""
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys

import pytest

from otc_pipeline.lat_hat import (EPOCH, comparison_summary, compute_raw, datum_draft,
                                  read_tuning_input, uncertainty_label, official_calibration,
                                  calibrated_draft)
from otc_pipeline.datum_partition import load
from otc_pipeline import lat_hat

PARTITION_DATA = Path(__file__).resolve().parents[3] / "data/datums"
EXTERNAL_ENGINE = pytest.mark.skipif("OTC_ENGINE_WORKTREE" not in os.environ,
                                     reason="strict reference integration requires OTC_ENGINE_WORKTREE")


def simple_input():
    return {
        "constant_set": {
            "source": "noaa", "source_record_id": "9410660", "quantity": "water_level",
            "qc_status": "ok", "datum": {"msl_offset_m": 3.0},
            "constituents": [{"name": "M2", "amplitude_m": 2.0, "phase_deg": 0.0}],
        },
        "astro_table": {"first_year": 2019, "last_year": 2040,
                        "constituents": {"M2": {"speed_deg_per_hour": 30.0,
                                                  "v0u_deg": [0.0] * 22, "f": [1.0] * 22}}},
    }


@EXTERNAL_ENGINE
def test_strict_engine_extrema_add_zero_once():
    result = compute_raw(simple_input(), start="2020-01-01T00:00:00Z", end="2020-01-03T00:00:00Z")
    assert result["lat_m"] == pytest.approx(1.0)
    assert result["hat_m"] == pytest.approx(5.0)
    assert result["low_count"] == 4
    assert result["high_count"] == 4
    assert result["accepted"] is False


@EXTERNAL_ENGINE
def test_month_chunks_match_direct_engine_across_new_year():
    payload = simple_input()
    # Changing annual tables exercises the engine's New Year reconciliation.
    payload["astro_table"]["constituents"]["M2"]["v0u_deg"][2] = 0.1
    start, end = "2020-12-15T00:00:00Z", "2021-01-15T00:00:00Z"
    chunked = compute_raw(payload, start=start, end=end)
    direct = """
require 'json'
require 'time'
require File.join(ENV.fetch('OTC_ENGINE_WORKTREE'), 'lib/harmonics_strict')
input = JSON.parse($stdin.read)
table = Harmonics::Strict::AstroTable.from_h(input.fetch('astro_table'))
events = Harmonics::Engine.allocate.extremes_strict(input.fetch('constant_set').fetch('constituents'),
    Time.iso8601(input.fetch('start')), Time.iso8601(input.fetch('end')), datum_term: 3.0, astro: table)
puts JSON.generate(events.group_by { |e| e.fetch('type') }.transform_values do |rows|
    { 'count' => rows.size, 'min' => rows.map { |e| e.fetch('height') }.min,
      'max' => rows.map { |e| e.fetch('height') }.max }
end)
"""
    result = subprocess.run(["ruby", "-e", direct], input=json.dumps({**payload, "start": start, "end": end}),
                            text=True, capture_output=True, check=True, env=os.environ)
    unchunked = json.loads(result.stdout)
    assert chunked["chunks"] == 2
    assert chunked["lat_m"] == unchunked["low"]["min"]
    assert chunked["hat_m"] == unchunked["high"]["max"]
    assert chunked["low_count"] == unchunked["low"]["count"]
    assert chunked["high_count"] == unchunked["high"]["count"]


def test_raw_extrema_missing_absolute_origin_is_refused():
    payload = simple_input()
    payload["constant_set"]["datum"] = {}
    with pytest.raises(ValueError, match="msl_offset_m"):
        compute_raw(payload)


@pytest.mark.parametrize("invalid", [math.nan, math.inf, "0", None, True])
def test_invalid_zero_is_refused(invalid):
    payload = simple_input()
    payload["constant_set"]["datum"]["msl_offset_m"] = invalid
    with pytest.raises(ValueError, match="msl_offset_m"):
        compute_raw(payload)


def test_draft_preserves_published_and_does_not_mutate_input():
    payload = simple_input()
    datum = payload["constant_set"]["datum"]
    datum.update(named={"lat": 0.8, "hat": 5.2}, basis={
        "lat": {"kind": "published", "method": "source", "source_name": "LAT"},
        "hat": {"kind": "published", "method": "source", "source_name": "HAT"}})
    original = copy.deepcopy(payload)
    raw = {"lat_m": 1.0, "hat_m": 5.0, "start": "2020-01-01T00:00:00Z",
           "end": "2039-01-01T00:00:00Z"}
    draft = datum_draft(payload["constant_set"], raw)
    assert payload == original
    assert draft["named"] == {"lat": 0.8, "hat": 5.2, "otc_lat": 1.0, "otc_hat": 5.0}
    assert draft["basis"]["otc_lat"] == {
        "kind": "computed", "method": "harmonic_extremes", "epoch": EPOCH}
    assert "uncertainty_m" not in draft["basis"]["otc_lat"]


@pytest.mark.parametrize("flatline,truncated", [(True, False), (False, True)])
def test_cut_off_lows_keep_published_but_suppress_computed_lat(flatline, truncated):
    payload = simple_input()
    cs = payload["constant_set"]
    cs["datum"].update(named={"lat": 0.8, "mhw": 4.0}, basis={
        "lat": {"kind": "published"}, "mhw": {"kind": "observed", "flags":
                                                        ["truncated_lows"] if truncated else []}})
    raw = {"lat_m": 1.0, "hat_m": 5.0, "start": "2020-01-01T00:00:00Z",
           "end": "2039-01-01T00:00:00Z"}
    draft = datum_draft(cs, raw, flatline=flatline)
    assert "otc_lat" not in draft["named"]
    assert draft["named"]["lat"] == 0.8
    assert draft["named"]["hat"] == 5.0


def test_flatline_removes_an_existing_computed_lat_without_removing_published():
    cs = simple_input()["constant_set"]
    cs["datum"].update(named={"lat": 0.8, "otc_lat": 1.0}, basis={
        "lat": {"kind": "published"}, "otc_lat": {"kind": "computed"}})
    raw = {"lat_m": 1.0, "hat_m": 5.0, "start": "2020-01-01T00:00:00Z",
           "end": "2039-01-01T00:00:00Z"}
    draft = datum_draft(cs, raw, flatline=True)
    assert "otc_lat" not in draft["named"]
    assert "otc_lat" not in draft["basis"]
    assert draft["named"]["lat"] == 0.8


def test_fit_with_unknown_low_water_eligibility_keeps_labelled_fallback():
    cs = simple_input()["constant_set"]
    cs.update(source="gesla-fit", source_type="gauge")
    raw = {"lat_m": 1.0, "hat_m": 5.0, "start": "2020-01-01T00:00:00Z",
           "end": "2039-01-01T00:00:00Z"}
    draft = datum_draft(cs, raw)
    assert draft["named"]["lat"] == 1.0
    assert draft["basis"]["lat"]["fallback"]["reason"] == "datum_gate_failed"
    assert "unknown" in draft["basis"]["lat"]["fallback"]["otc_finding"].lower()
    assert draft["named"]["hat"] == 5.0
    assert datum_draft(cs, raw, flatline=False)["named"]["lat"] == 1.0


def test_native_flatline_issue_suppresses_lat_even_without_a_truncated_flag():
    cs = simple_input()["constant_set"]
    cs.update(source="gesla-fit", source_type="gauge", record_issues=[{"issue": "flatline", "detail": "low-water floor"}])
    raw = {"lat_m": 1.0, "hat_m": 5.0, "start": "2020-01-01T00:00:00Z", "end": "2039-01-01T00:00:00Z"}
    datum = datum_draft(cs, raw, flatline=False)
    assert "lat" not in datum["named"]
    assert datum["named"]["hat"] == 5.0


def test_short_window_cannot_receive_pinned_epoch():
    with pytest.raises(ValueError, match="pinned"):
        datum_draft(simple_input()["constant_set"], {"lat_m": 1, "hat_m": 5,
                                                   "start": "2020-01-01T00:00:00Z",
                                                   "end": "2020-01-03T00:00:00Z"})


def test_tuning_loader_refuses_non_tuning_station(tmp_path):
    path = tmp_path / "input.json"
    payload = simple_input()
    payload["constant_set"]["source_record_id"] = "8443970"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="tuning"):
        read_tuning_input(path)


def test_t5b_loader_keeps_fit_and_published_comparison_distinct(tmp_path):
    payload = simple_input()
    reference = copy.deepcopy(payload["constant_set"])
    reference["datum"].update(named={"lat": -1.0, "hat": 5.0, "msl": 2.0}, basis={
        key: {"kind": "published"} for key in ("lat", "hat", "msl")})
    payload.update(comparison_set=reference, station_id="OTC-123", comparison_station_id="OTC-123")
    payload["constant_set"].update(source="gesla-fit", source_record_id="los_angeles-9410660-usa-noaa")
    path = tmp_path / "input.json"
    path.write_text(json.dumps(payload))
    got = read_tuning_input(path, tier="T5b", data_dir=PARTITION_DATA)
    assert got["constant_set"]["source"] == "gesla-fit"
    assert got["comparison_set"]["source"] == "noaa"
    payload["comparison_station_id"] = "OTC-other"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="same native station"):
        read_tuning_input(path, tier="T5b", data_dir=PARTITION_DATA)


def test_t5b_scores_each_station_once_while_retaining_fit_count():
    rows = [{"lat_residual_m": 0.02, "hat_residual_m": -0.02,
             "comparison_source_record_id": "9410660"}] * 2
    summary = comparison_summary(rows, tier="T5b")
    assert summary["station_count"] == 1
    assert summary["fit_set_count"] == 2
    assert summary["lat"]["median_limit_m"] == 0.03
    assert summary["lat"]["p95_limit_m"] == 0.08
    assert summary["lat"]["n"] == 1
    assert summary["pooled_fit_diagnostics"]["lat"]["n"] == 2
    assert summary["station_level_gate"] == "PASS"


def test_t6_loader_accepts_only_sealed_tuning_reference(tmp_path):
    payload = simple_input()
    payload["constant_set"].update(source="kartverket", source_record_id=load("partitions", "tuning", "kartverket")[0])
    payload["constant_set"]["datum"].update(named={"lat": 0.0, "hat": 5.0},
                                            basis={key: {"kind": "published"} for key in ("lat", "hat")})
    path = tmp_path / "kv.json"
    path.write_text(json.dumps(payload))
    assert read_tuning_input(path, tier="T6")["constant_set"]["source"] == "kartverket"


def test_t6_linz_joins_native_id_to_sealed_name_through_checked_header(tmp_path):
    payload = simple_input()
    payload["constant_set"].update(source="linz", source_record_id="071WELLINGTON")
    payload["constant_set"]["datum"].update(named={"lat": 0.0, "hat": 5.0},
                                            basis={key: {"kind": "published"} for key in ("lat", "hat")})
    header = tmp_path / "Wellington.csv"
    data = "071,Wellington,41°17'S,174°47'E\n".encode()
    header.write_bytes(data)
    payload.update(linz_port_header_path=str(header), linz_header_sha256=hashlib.sha256(data).hexdigest(),
                   partition_source_id="wellington")
    path = tmp_path / "linz.json"
    path.write_text(json.dumps(payload))
    assert read_tuning_input(path, tier="T6")["constant_set"]["source_record_id"] == "071WELLINGTON"
    payload["constant_set"]["source_record_id"] = "077NELSON"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="header number"):
        read_tuning_input(path, tier="T6")


def test_t6_uses_kartverket_p95_and_linz_maximum_without_a_median_bar():
    rows = [{"comparison_source": "kartverket", "lat_residual_m": 0.04, "hat_residual_m": -0.04},
            {"comparison_source": "linz", "lat_residual_m": 0.051, "hat_residual_m": 0.01}]
    summary = comparison_summary(rows, tier="T6")
    assert summary["kartverket"]["lat"]["within_frozen_thresholds"] is True
    assert summary["linz"]["lat"]["within_frozen_thresholds"] is False
    assert summary["linz"]["hat"]["within_frozen_thresholds"] is True
    assert "median_limit_m" not in summary["kartverket"]["lat"]


def test_t5_reports_signed_and_absolute_residuals_per_level():
    summary = comparison_summary([{"lat_residual_m": x, "hat_residual_m": -x}
                                  for x in [0.0, 0.01, 0.02]])
    assert summary["lat"]["signed_median_m"] == 0.01
    assert summary["hat"]["signed_median_m"] == -0.01
    assert summary["lat"]["p95_abs_m"] == pytest.approx(0.019)
    assert summary["pilot_only"] is True
    assert summary["accepted"] is False


@pytest.mark.parametrize("propagated,analytic,label", [(0.2, 0.1, "propagated"),
                                                      (0.1, 0.2, "calibrated"),
                                                      (0.1, 0.1, "propagated")])
def test_uncertainty_label_largest_contribution_tie_propagated(propagated, analytic, label):
    assert uncertainty_label(propagated, analytic, analytic_basis="calibrated") == label


def test_analytic_formula_does_not_implicitly_claim_calibration():
    assert uncertainty_label(0.1, 0.2, analytic_basis="propagated") == "propagated"
    with pytest.raises(ValueError, match="basis"):
        uncertainty_label(0.1, 0.2, analytic_basis="analytic")


def test_cli_validates_all_inputs_but_retains_only_current_payload(tmp_path, monkeypatch):
    payload = simple_input()
    payload["constant_set"]["datum"].update(named={"lat": 0.8, "hat": 5.2, "msl": 3.0},
                                            basis={key: {"kind": "published"} for key in ("lat", "hat", "msl")})
    paths = []
    for number in range(4):
        path = tmp_path / f"{number}.json"
        path.write_text(json.dumps(payload))
        paths.append(str(path))
    metrics = {"live": 0, "peak": 0, "validated": [], "computations": 0}
    real_loader = lat_hat.read_tuning_input

    class TrackedPayload(dict):
        def __init__(self, value):
            super().__init__(value)
            metrics["live"] += 1
            metrics["peak"] = max(metrics["peak"], metrics["live"])

        def __del__(self):
            metrics["live"] -= 1

    def load_tracked(path, **kwargs):
        metrics["validated"].append(str(path))
        return TrackedPayload(real_loader(path, **kwargs))

    def arithmetic_already_proved(_payload, **_kwargs):
        assert set(metrics["validated"]) == set(paths)
        metrics["computations"] += 1
        return {"lat_m": 1.0, "hat_m": 5.0, "start": lat_hat.START, "end": lat_hat.END}

    monkeypatch.setattr(lat_hat, "read_tuning_input", load_tracked)
    monkeypatch.setattr(lat_hat, "compute_raw", arithmetic_already_proved)
    monkeypatch.setattr(sys, "argv", ["lat_hat", "--out", str(tmp_path / "results.jsonl"), *paths])
    lat_hat.main()
    assert metrics["computations"] == 4
    assert metrics["peak"] <= 2  # Current payload plus a transient next payload, never the cohort.


def official_examples():
    rows, sets = [], {}
    for sid, residual in (("9410660", 0.0), ("1770000", 0.002)):
        cs = simple_input()["constant_set"]
        cs.update(set_id=f"OTC-example/noaa/{sid}", source_record_id=sid, source_type="official")
        cs["datum"].update(named={"lat": 1 - residual, "hat": 5 - residual, "msl": 3.0},
                            basis={key: {"kind": "published"} for key in ("lat", "hat", "msl")})
        raw = {"lat_m": 1.0, "hat_m": 5.0, "start": lat_hat.START, "end": lat_hat.END}
        rows.append({"set_id": cs["set_id"], "source_record_id": sid, "raw": raw,
                     "draft_datum": datum_draft(cs, raw), "lat_residual_m": residual,
                     "hat_residual_m": residual})
        sets[cs["set_id"]] = cs
    return rows, sets, {"noaa": set(sets)}


def test_official_calibration_uses_sample_spread_and_keeps_bias_count_separate():
    rows, _, expected = official_examples()
    group = official_calibration(rows, expected)["noaa"]
    assert group["status"] == "available"
    assert group["lat"]["n"] == 2
    assert group["lat"]["sample_sd_m"] == pytest.approx(math.sqrt(0.000002))
    assert group["lat"]["signed_bias_m"] == 0.001


def test_official_calibration_never_turns_missing_or_invalid_cohort_into_zero():
    rows, _, expected = official_examples()
    incomplete = official_calibration(rows[:1], expected)["noaa"]
    assert incomplete["status"] == "unavailable"
    assert incomplete["lat"]["sample_sd_m"] is None
    rows[1]["lat_residual_m"] = 0.2
    failed = official_calibration(rows, expected)["noaa"]
    assert failed["status"] == "unavailable"
    assert failed["gate_passed"] is False


def test_official_calibrated_draft_adds_sampling_literally_and_does_not_mutate():
    rows, sets, expected = official_examples()
    calibration = official_calibration(rows, expected)
    row = rows[0]
    cs = sets[row["set_id"]]
    original = copy.deepcopy((row, cs))
    result = calibrated_draft(cs, row, calibration)
    basis = result["draft_datum"]["basis"]["otc_lat"]
    assert basis["uncertainty_m"] == pytest.approx(math.sqrt(0.000002) + 0.011 * 2)
    assert basis["uncertainty_basis"] == "propagated"
    assert basis["flags"] == ["sampling_assumed"]
    assert result["uncertainty_components"]["lat"]["base_basis"] == "calibrated"
    assert (row, cs) == original
    cs["time_base"] = {"code": "verified", "published": "utc_instant"}
    verified = calibrated_draft(cs, row, calibration)["draft_datum"]["basis"]["otc_lat"]
    assert verified["uncertainty_basis"] == "calibrated"
    assert verified["uncertainty_m"] == pytest.approx(math.sqrt(0.000002))
    assert "flags" not in verified


def test_missing_official_production_input_needs_no_published_comparator(tmp_path, monkeypatch):
    payload = simple_input()
    payload["constant_set"].update(source_type="official", set_id="OTC-test/noaa/9410660")
    path = tmp_path / "native-shape.json"
    path.write_text(json.dumps(payload))
    monkeypatch.setattr(lat_hat, "load", lambda *a, **kw: pytest.fail("production must not read sealed comparison lists"))
    assert lat_hat.read_production_input(path) == payload
    payload["comparison_set"] = copy.deepcopy(payload["constant_set"])
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="comparator"):
        lat_hat.read_production_input(path)


@pytest.mark.skipif("OTC_T5B_NATIVE_ROSTER" not in os.environ,
                    reason="unknown-quality proof requires the completed native T5b cohort")
@pytest.mark.parametrize("index", [0, 1, 2])
def test_native_unknown_quality_fits_keep_labelled_computed_lat(index):
    roster = json.loads(Path(os.environ["OTC_T5B_NATIVE_ROSTER"]).read_text())
    rows = {row["set_id"]: row for line in Path(os.environ["OTC_T5B_NATIVE_RESULTS"]).read_text().splitlines()
            if (row := json.loads(line)).get("set_id")}
    for entry in roster["rows"][index:index + 1]:
        cs = json.loads(Path(entry["path"]).read_text())["constant_set"]
        original = copy.deepcopy(cs)
        raw = rows[cs["set_id"]]["raw"]
        datum = datum_draft(cs, raw)
        assert datum["basis"]["lat"]["kind"] == "computed"
        assert datum["named"]["lat"] == raw["lat_m"]
        assert datum["basis"]["lat"]["fallback"]["reason"] == "datum_gate_failed"
        assert "unknown" in datum["basis"]["lat"]["fallback"]["otc_finding"].lower()
        for key, value in cs["datum"].get("named", {}).items():
            assert datum["named"][key] == value
            assert datum["basis"][key] == cs["datum"]["basis"][key]
        assert cs == original


@pytest.mark.skipif("OTC_T5B_NATIVE_RESULTS" not in os.environ,
                    reason="station gate proof requires completed native T5b outputs")
def test_native_t5b_gate_uses_approved_worst_absolute_residual_per_station():
    rows = [row for line in Path(os.environ["OTC_T5B_NATIVE_RESULTS"]).read_text().splitlines()
            if (row := json.loads(line)).get("set_id")]
    summary = comparison_summary(rows, tier="T5b")
    assert summary["station_level_gate"] == "FAIL"
    assert summary["fit_set_count"] == len(rows) == 360
    assert summary["station_count"] == 310
    assert summary["lat"]["n"] == summary["hat"]["n"] == 310
    assert summary["lat"]["median_abs_m"] == pytest.approx(0.05260890585243316)
    assert summary["lat"]["p95_abs_m"] == pytest.approx(0.24002776493791547)
    assert summary["hat"]["median_abs_m"] == pytest.approx(0.047485134179978905)
    assert summary["hat"]["p95_abs_m"] == pytest.approx(0.2624312575416726)
