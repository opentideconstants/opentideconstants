"""Arithmetic method tests; real NOAA tuning runs are explicit, never the holdout."""
import copy
import hashlib
import json
import math
import os
from pathlib import Path
from statistics import mean, stdev
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


@pytest.mark.skipif("OTC_GESLA_METADATA_PROOF" not in os.environ, reason="requires actual frozen native metadata cases")
@pytest.mark.parametrize("index", range(3))
def test_native_gesla_computed_basis_copies_source_flags(index):
    root = Path(os.environ["OTC_GESLA_METADATA_PROOF"])
    directory = root / "missing-gesla"
    entry = json.loads((directory / "roster.json").read_text())["rows"][index]
    cs = json.loads(Path(entry["path"]).read_text())["constant_set"]
    row = next(r for line in (directory / "pilot.jsonl").read_text().splitlines()
               if (r := json.loads(line))["set_id"] == cs["set_id"])
    calibration = json.loads((directory / "diagnostic-calibration.json").read_text())
    original = copy.deepcopy((row, cs))
    result = calibrated_draft(cs, row, calibration)
    expected = {"time_base_unverified", "sampling_assumed"}
    if index < 2:
        assert any(f["flag"] == "short_record" for f in cs["qc_flags"])
        assert cs["record_span"]["good_hours"] < 365.25 * 24
        expected.add("short_record")
    if index == 1:
        assert sum(c["amplitude_m"] for c in cs["constituents"] if c["name"] in ("M2", "S2", "K1", "O1")) < 0.05
        expected.add("microtidal")
    for level in ("lat", "hat"):
        assert set(result["draft_datum"]["basis"][level]["flags"]) == expected
        assert result["draft_datum"]["named"][level] == row["raw"][f"{level}_m"]
    assert (row, cs) == original
    print(json.dumps({"set_id": cs["set_id"], "expected_flags": sorted(expected), "raw_values_unchanged": True}))


@pytest.mark.skipif("OTC_GESLA_METADATA_PROOF" not in os.environ, reason="requires the two evidenced official microtidal sets")
@pytest.mark.parametrize("sid", ("OTC-29ff8d2e/noaa/8773896", "OTC-a4de929f/noaa/8721843"))
def test_native_official_microtidal_flag_keeps_values_and_uncertainty(sid):
    root = Path(os.environ["OTC_GESLA_METADATA_PROOF"])
    with (root / "native-build/OTC_20261011.jsonl").open() as stream:
        cs = next(c for line in stream for c in json.loads(line).get("constant_sets", []) if c["set_id"] == sid)
    row = next(r for line in (root / "official-public-drafts-bound.jsonl").read_text().splitlines()
               if (r := json.loads(line))["set_id"] == sid)
    calibration = json.loads((root / "missing-official/source-calibration.json").read_text())
    # Reconstruct the same pre-attachment datum from the saved, bound official draft.
    cs = copy.deepcopy(cs)
    for level in ("otc_lat", "otc_hat"):
        cs["datum"]["named"].pop(level)
        cs["datum"]["basis"].pop(level)
    original = copy.deepcopy((row, cs))
    result = calibrated_draft(cs, row, calibration)
    for level in ("lat", "hat"):
        key = f"otc_{level}"
        assert set(result["draft_datum"]["basis"][key]["flags"]) == {"sampling_assumed", "microtidal"}
        assert result["draft_datum"]["basis"][key]["uncertainty_m"] == row["draft_datum"]["basis"][key]["uncertainty_m"]
    assert result["draft_datum"]["named"] == row["draft_datum"]["named"]
    assert result["uncertainty_components"] == row["uncertainty_components"]
    assert (row, cs) == original


@pytest.mark.skipif("OTC_GESLA_REDERIVE_METADATA" not in os.environ, reason="explicit complete frozen-batch metadata proof")
def test_native_rederive_frozen_batch_metadata_without_predictions():
    root = Path(os.environ["OTC_GESLA_METADATA_PROOF"])
    directory = root / "missing-gesla"
    entries = {e["set_id"]: e for e in json.loads((directory / "roster.json").read_text())["rows"]}
    calibration = json.loads((directory / "diagnostic-calibration.json").read_text())
    frozen = []
    for path in [directory / "pilot.jsonl", *[directory / f"shard-{n}.jsonl" for n in range(4)]]:
        frozen.extend(json.loads(line) for line in path.read_text().splitlines())
    assert len(frozen) == len({r["set_id"] for r in frozen}) == len(entries) == 3096
    corrected, sampling_changed = [], 0
    for row in frozen:
        entry = entries[row["set_id"]]
        raw_input = Path(entry["path"]).read_bytes()
        assert hashlib.sha256(raw_input).hexdigest() == entry["input_sha256"] == row["input_sha256"]
        payload = json.loads(raw_input)
        assert lat_hat.computation_input_sha256(payload) == row["computation_input_sha256"]
        cs = payload["constant_set"]
        fresh = calibrated_draft(cs, row, calibration)
        assert {k: v for k, v in fresh.items() if k not in ("draft_datum", "uncertainty_components")} == {
            k: v for k, v in row.items() if k not in ("draft_datum", "uncertainty_components")}
        assert fresh["draft_datum"]["named"] == row["draft_datum"]["named"]
        sampling = 0.011 * sum(c["amplitude_m"] for c in cs["constituents"] if c["name"] in ("M2", "S2"))
        flags = {"sampling_assumed"}
        if cs.get("time_base", {}).get("code") in ("unverified", "disputed"):
            flags.add(f"time_base_{cs['time_base']['code']}")
        if cs.get("record_span", {}).get("good_hours", 365.25 * 24) < 365.25 * 24 or any(
                f["flag"] == "short_record" for f in cs.get("qc_flags", [])):
            flags.add("short_record")
        if sum(c["amplitude_m"] for c in cs["constituents"] if c["name"] in ("M2", "S2", "K1", "O1")) < 0.05:
            flags.add("microtidal")
        for level in ("lat", "hat"):
            basis = fresh["draft_datum"]["basis"][level]
            parts = fresh["uncertainty_components"][level]
            assert parts["sampling_m"] == sampling and parts["sampling_verified"] is False
            assert basis["uncertainty_m"] == parts["base_m"] + sampling
            assert basis["uncertainty_basis"] == ("propagated" if sampling >= parts["base_m"] else "calibrated")
            assert set(basis["flags"]) == flags
            assert basis["fallback"]["reason"] == "datum_gate_failed"
        sampling_changed += fresh["uncertainty_components"] != row["uncertainty_components"]
        corrected.append(fresh)
    raw_official = (root / "official-public-drafts-bound.jsonl").read_bytes()
    assert hashlib.sha256(raw_official).hexdigest() == "970f7b280c9507585995aed40f8be7b679bad0adaf0fa79f98f98f3054f07a3f"
    official = [json.loads(line) for line in raw_official.splitlines()]
    changed_official = []
    # Only the two already evidenced official metadata cells need a new flag.
    affected = {"OTC-29ff8d2e/noaa/8773896", "OTC-a4de929f/noaa/8721843"}
    with (root / "native-build/OTC_20261011.jsonl").open() as stream:
        source_sets = {c["set_id"]: c for line in stream for c in json.loads(line).get("constant_sets", []) if c["set_id"] in affected}
    for row in official:
        original = copy.deepcopy(row)
        if row["set_id"] in affected:
            for key in ("otc_lat", "otc_hat"):
                row["draft_datum"]["basis"][key]["flags"].append("microtidal")
            changed_official.append(row["set_id"])
        assert row["raw"] == original["raw"]
        assert row["uncertainty_components"] == original["uncertainty_components"]
        assert row["draft_datum"]["named"] == original["draft_datum"]["named"]
        for key in row["draft_datum"]["basis"]:
            assert {k: v for k, v in row["draft_datum"]["basis"][key].items() if k != "flags"} == {
                k: v for k, v in original["draft_datum"]["basis"][key].items() if k != "flags"}
    assert set(changed_official) == set(source_sets) == affected
    assert len(official) == 519
    assert not ({r["set_id"] for r in official} & set(entries))
    for name, rows in (("raw-results-frozen.jsonl", frozen), ("all-results.jsonl", corrected),
                       ("all-public-drafts.jsonl", official + corrected)):
        (directory / name).write_text("".join(json.dumps(r, sort_keys=True, allow_nan=False) + "\n" for r in rows))
    receipt = {"new_raw_sets": len(corrected), "official_sets_retained": len(official),
               "new_sampling_components_corrected_sets": sampling_changed,
               "official_numeric_uncertainty_changes": 0, "official_microtidal_flags_added": sorted(changed_official),
               "raw_extrema_and_input_bindings_unchanged": True, "predictions_rerun": 0,
               "sha256": {name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
                          for name in ("raw-results-frozen.jsonl", "all-results.jsonl", "all-public-drafts.jsonl")}}
    (directory / "metadata-complete-green.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt))


@pytest.mark.skipif("OTC_GESLA_PROOF" not in os.environ, reason="requires actual cached GESLA production inputs")
@pytest.mark.parametrize("index", range(3))
def test_native_missing_gesla_production_route(index):
    root = Path(os.environ["OTC_GESLA_PROOF"])
    entry = json.loads((root / "missing-gesla/roster.json").read_text())["rows"][index]
    payload = lat_hat.read_production_input(Path(entry["path"]))
    assert payload["constant_set"]["set_id"] == entry["set_id"]
    assert not any(k in payload["constant_set"]["datum"].get("named", {}) for k in ("lat", "hat", "otc_lat", "otc_hat"))


@pytest.mark.skipif("OTC_GESLA_PROOF" not in os.environ, reason="requires actual frozen T5b outputs")
@pytest.mark.parametrize("index", range(3))
def test_native_gesla_diagnostic_uncertainty(index):
    root = Path(os.environ["OTC_GESLA_PROOF"])
    entries = json.loads((root / "full-t5b/roster.json").read_text())["rows"]
    rows = [r for line in (root / "full-t5b/all-results.jsonl").read_text().splitlines()
            if (r := json.loads(line)).get("set_id")]
    group = {"status": "diagnostic", "cohort_complete": True, "gate_passed": False}
    for level in ("lat", "hat"):
        values = [r[f"{level}_residual_m"] for r in rows]
        group[level] = {"sample_sd_m": stdev(values), "signed_bias_m": mean(values), "n": len(values)}
    payload = json.loads(Path(entries[index]["path"]).read_text())
    cs = payload["constant_set"]
    row = next(r for r in rows if r["set_id"] == cs["set_id"])
    result = calibrated_draft(cs, row, {"gesla-fit": group})
    assert result["calibration_status"] == "diagnostic"
    assert result["accepted"] is False
    sampling = 0.011 * sum(c["amplitude_m"] for c in cs["constituents"] if c["name"] in ("M2", "S2"))
    for level in ("lat", "hat"):
        basis = result["draft_datum"]["basis"][level]
        assert basis["uncertainty_m"] == group[level]["sample_sd_m"] + sampling
        assert basis["fallback"]["reason"] == "datum_gate_failed"
        assert "unknown" in basis["fallback"]["otc_finding"]
        assert "diagnostic" in basis["fallback"]["otc_finding"].lower()


@pytest.mark.skipif("OTC_GESLA_PROOF" not in os.environ, reason="requires frozen360 tuning inputs and native release")
def test_native_gesla_calibration_artifact():
    root = Path(os.environ["OTC_GESLA_PROOF"])
    result_bytes = (root / "full-t5b/all-results.jsonl").read_bytes()
    assert hashlib.sha256(result_bytes).hexdigest() == "89662b6310d9ad65b30655afd6b3546267f2f50eb085cd80eec2810b14e435a8"
    rows = [r for line in result_bytes.splitlines() if (r := json.loads(line)).get("set_id")]
    entries = json.loads((root / "full-t5b/roster.json").read_text())["rows"]
    fingerprints = {}
    for entry in entries:
        payload = read_tuning_input(Path(entry["path"]), tier="T5b")
        fingerprints[entry["set_id"]] = lat_hat.computation_input_sha256(payload)
    meta = json.loads((root / "native-build/OTC_20261011.meta.json").read_text())
    tables = {t["astro_table_id"]: t for t in meta["astro_tables"]}
    conventions = {c["convention_id"]: c for c in meta["conventions"]}
    matched = set()
    with (root / "native-build/OTC_20261011.jsonl").open() as stream:
        for line in stream:
            for cs in json.loads(line).get("constant_sets", []):
                if cs["set_id"] not in fingerprints:
                    continue
                table = tables[conventions[cs["convention_id"]]["astro_table_id"]]
                assert lat_hat.computation_input_sha256({"constant_set": cs, "astro_table": table}) == fingerprints[cs["set_id"]]
                matched.add(cs["set_id"])
    assert len(rows) == len(matched) == 360
    calibration = lat_hat.gesla_calibration(rows, matched)
    group = calibration["gesla-fit"]
    assert group["status"] == "diagnostic" and group["gate_passed"] is False
    assert group["station_gate"]["station_count"] == 310
    for level in ("lat", "hat"):
        assert group[level]["n"] == 360
        assert group[level]["sample_sd_m"] == stdev(r[f"{level}_residual_m"] for r in rows)
    path = root / "missing-gesla/diagnostic-calibration.json"
    path.write_text(json.dumps(calibration, sort_keys=True, allow_nan=False) + "\n")
    (root / "missing-gesla/calibration-receipt.json").write_text(json.dumps({
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "frozen_fit_inputs_matched": len(matched),
        "raw_predictions_rerun": 0, "diagnostic_calibration": calibration}, indent=2) + "\n")


@pytest.mark.skipif("OTC_GESLA_RUN_PILOT" not in os.environ, reason="explicit actual three-place strict pilot")
def test_native_gesla_production_three_place_pilot():
    root = Path(os.environ["OTC_GESLA_PROOF"])
    entries = json.loads((root / "missing-gesla/roster.json").read_text())["rows"][:3]
    calibration = root / "missing-gesla/diagnostic-calibration.json"
    command = [sys.executable, "-m", "otc_pipeline.lat_hat", "--production-calibration", str(calibration),
               "--production-official-calibration-sha256", hashlib.sha256(calibration.read_bytes()).hexdigest(),
               "--out", str(root / "missing-gesla/pilot.jsonl"), "--ruby", os.environ["OTC_PILOT_RUBY"],
               *[entry["path"] for entry in entries]]
    subprocess.run(command, check=True)
    rows = [json.loads(line) for line in (root / "missing-gesla/pilot.jsonl").read_text().splitlines()]
    assert len(rows) == 3
    for entry, row in zip(entries, rows, strict=True):
        assert row["set_id"] == entry["set_id"]
        assert row["calibration_status"] == "diagnostic"
        assert row["normal_datum_gate_accepted"] is False
        for level in ("lat", "hat"):
            assert row["draft_datum"]["named"][level] == row["raw"][f"{level}_m"]
            assert row["draft_datum"]["basis"][level]["fallback"]["reason"] == "datum_gate_failed"
            assert row["uncertainty_components"][level]["n"] == 360


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
    timestamp_verified = calibrated_draft(cs, row, calibration)["draft_datum"]["basis"]["otc_lat"]
    assert timestamp_verified == basis


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


@pytest.fixture
def native_calibration_case(tmp_path, monkeypatch):
    root = os.environ.get("OTC_DATUM_FIX_PROOF")
    if root is None:
        pytest.skip("requires saved native official inputs and completed extrema")
    root = Path(root)
    entries = json.loads((root / "full-t5/roster.json").read_text())["rows"][:3]
    rows = {row["set_id"]: row for line in (root / "official-public-drafts-bound.jsonl").read_text().splitlines()
            if (row := json.loads(line)).get("set_id")}

    def prepare(index, change=None):
        entry = entries[index]
        payload = json.loads(Path(entry["path"]).read_text())
        row = copy.deepcopy(rows[entry["set_id"]])
        assert row["computation_input_sha256"] == lat_hat.computation_input_sha256(payload)
        cs = payload["constant_set"]
        if change == "amplitude":
            cs["constituents"][0]["amplitude_m"] += 0.1
        elif change == "phase":
            cs["constituents"][0]["phase_deg"] += 1.0
        elif change == "table":
            table = next(iter(payload["astro_table"]["constituents"].values()))
            table["v0u_deg"][0] += 1.0
        elif change == "time_base":
            cs["time_base"] = {"code": "verified", "published": "changed native time base"}
        elif change == "missing":
            row.pop("computation_input_sha256")
        assert cs["set_id"] == row["set_id"] == entry["set_id"]
        native = tmp_path / "native.json"
        native.write_text(json.dumps(payload))
        roster = tmp_path / "roster.json"
        roster.write_text(json.dumps({"rows": [{**entry, "path": str(native)}]}))
        completed = tmp_path / "completed.jsonl"
        completed.write_text(json.dumps(row) + "\n")
        out = tmp_path / "calibrated.jsonl"
        monkeypatch.setattr(sys, "argv", ["lat_hat", str(completed), "--out", str(out),
                                         "--calibrate-official-rosters", str(roster),
                                         "--partition-data-dir", str(PARTITION_DATA)])
        return row, out

    return prepare


@pytest.mark.parametrize("index", [0, 1, 2])
@pytest.mark.parametrize("change", ["amplitude", "phase", "table", "time_base", "missing"])
def test_native_recalibration_rejects_stale_calculation_binding(native_calibration_case, index, change):
    _, out = native_calibration_case(index, change)
    with pytest.raises(ValueError, match="computation input"):
        lat_hat.main()
    assert not out.exists()


@pytest.mark.parametrize("index", [0, 1, 2])
def test_native_recalibration_preserves_verified_calculation_binding(native_calibration_case, index):
    row, out = native_calibration_case(index)
    lat_hat.main()
    result = json.loads(out.read_text().splitlines()[0])
    assert result["raw"] == row["raw"]
    assert result["computation_input_sha256"] == row["computation_input_sha256"]
    for level in ("lat", "hat"):
        assert result[f"{level}_residual_m"] == row[f"{level}_residual_m"]
    assert result["draft_datum"]["named"] == row["draft_datum"]["named"]


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
