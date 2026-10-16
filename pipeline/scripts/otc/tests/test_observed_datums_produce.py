"""Original-record reduction orchestration and optional immutable native witnesses."""
import copy
import importlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from gesla.qc import Series, clean
from gesla.read_gesla import GeslaRelease, load_contributor_roles
from gesla.tcd_table import TcdTable
from otc_pipeline.gesla_sets import _label_correction
from otc_pipeline.observed_datums import monthly, tabulate


def test_producer_entry_point_exists():
    assert importlib.util.find_spec("otc_pipeline.observed_datums.produce") is not None


def producer():
    return importlib.import_module("otc_pipeline.observed_datums.produce")


def months(first, last):
    return {(year, month): {key: float(month + year - first) for key in monthly.LEVELS}
            for year in range(first, last + 1) for month in range(1, 13)}


def test_primary_requires_each_calendar_month_and_balances_means():
    p = producer()
    mm = months(2002, 2020)
    del mm[(2002, 1)]
    assert p.is_primary(mm)
    reduced = p.balanced_mean(mm)
    expected = np.mean([np.mean([v["MSL"] for (_, m), v in mm.items() if m == month])
                        for month in range(1, 13)])
    assert reduced["MSL"] == expected
    del mm[(2003, 1)]
    del mm[(2004, 1)]
    assert len(mm) >= 216
    assert not p.is_primary(mm)


def test_own_record_uses_whole_year_window_and_latest_19_year_cap():
    p = producer()
    mm = months(1990, 2025)
    mm[(2026, 1)] = mm[(2025, 1)]
    used = p.own_months(mm)
    assert min(used) == (2007, 1)
    assert max(used) == (2025, 12)
    assert len(used) == 228
    assert p.own_months({(2026, 1): mm[(2026, 1)]}) == {(2026, 1): mm[(2026, 1)]}


def test_own_months_do_not_discard_interior_gap_months_as_entire_years():
    p = producer()
    mm = months(2021, 2025)
    del mm[(2023, 5)]
    used = p.own_months(mm)
    assert len(used) == 59
    assert (2023, 4) in used and (2023, 6) in used


def test_fallback_unknown_uncertainty_and_sampling_are_independent_of_clock():
    p = producer()
    cs = {"time_base": {"code": "verified", "published": "utc_instant"}, "datum": {}, "constituents": []}
    mm = months(2022, 2022)
    values = {key: float(i) for i, key in enumerate(monthly.LEVELS)}
    draft, components = p.draft_datum(cs, values, mm, method="first_reduction")
    assert set(draft["named"]) == {key.lower() for key in monthly.LEVELS}
    for key, basis in draft["basis"].items():
        assert basis["kind"] == "observed"
        assert basis["epoch"] == {"start": basis["data_span"]["start"], "end": basis["data_span"]["end"]}
        assert "no_qualified_control" in basis["flags"]
        assert "uncertainty_m" not in basis and "uncertainty_basis" not in basis
        assert ("sampling_assumed" in basis["flags"]) == (key not in ("msl", "mtl", "dtl"))
        assert "normal acceptance false" in basis["fallback"]["otc_finding"]
    assert components["mhw"]["sampling_m"] == pytest.approx(0.011 * values["MN"] / 2)


def test_published_companions_conflicts_and_truncated_lows():
    p = producer()
    cs = {"time_base": {"code": "unverified", "published": "utc_instant"},
          "record_issues": [{"issue": "flatline", "reason": "cut off at low water"}],
          "datum": {"named": {"mhw": 4.0}, "basis": {"mhw": {"kind": "published"}}}, "constituents": []}
    mm = months(2022, 2022)
    values = {key: float(i) for i, key in enumerate(monthly.LEVELS)}
    draft, _ = p.draft_datum(cs, values, mm, method="first_reduction")
    assert set(draft["named"]) == {"otc_mhw", "mhhw"}
    assert all("truncated_lows" in b["flags"] for b in draft["basis"].values())
    assert cs["datum"]["named"]["mhw"] == 4.0


def test_good_hours_measure_occupied_hours_not_subhourly_sample_count():
    p = producer()
    assert p.good_hours(np.arange(0, 3600, 60, dtype=np.int64)) == 1
    assert p.good_hours(np.array([0, 7200], dtype=np.int64)) == 2


def test_compact_years_keep_boundary_months_events_and_hourly_means(tmp_path):
    p = producer()
    start = int(datetime(2020, 12, 15, tzinfo=timezone.utc).timestamp())
    end = int(datetime(2022, 1, 15, tzinfo=timezone.utc).timestamp())
    times = np.arange(start, end, 3600, dtype=np.int64)
    predict = lambda t: 1 + np.cos(np.asarray(t) * 2 * np.pi / (12.4206012 * 3600))
    heights = predict(times) + 0.1 * np.sin(times / 500000)
    series = Series("boundary", times, heights)
    full = tabulate.tabulate(times, heights, predict, interval_s=3600)
    compact, mm = p.compact_tabulation(series, predict, "instantaneous")
    assert compact.complete_days == full.complete_days
    np.testing.assert_array_equal(compact.ev_t, full.ev_t)
    np.testing.assert_allclose(compact.ev_h, full.ev_h, atol=1e-11)
    np.testing.assert_array_equal(compact.ev_label, full.ev_label)
    assert set(mm) == set(monthly.monthly_means(full))
    for key, row in monthly.monthly_means(full).items():
        assert mm[key] == pytest.approx(row, abs=1e-11)
    p.save_tab(tmp_path / "tab.npz", compact)
    restored = p.load_tab(tmp_path / "tab.npz")
    assert restored.complete_days == compact.complete_days
    np.testing.assert_array_equal(restored.ev_t, compact.ev_t)


def test_tide_class_and_method_boundaries():
    p = producer()
    def cs(factor):
        return {"constituents": [{"name": "M2", "amplitude_m": 1.0},
                                 {"name": "K1", "amplitude_m": factor}]}
    assert [p.tide_class(cs(f)) for f in (0.24, 0.25, 1.5, 3.0, 3.01)] == [0, 1, 2, 2, 3]
    assert p.comparison_method({"country": "GBR"}, cs(3.0)) == "standard"
    assert p.comparison_method({"country": "USA", "lon": -122}, cs(0.1)) == "standard"


def test_datum_step_segments_use_evidenced_fit_segment_or_longest_first():
    p = producer()
    times = np.arange(0, 10 * 86400, 3600, dtype=np.int64)
    series = Series("segments", times, np.ones(len(times)))
    cs = {"record_issues": [{"issue": "datum_step", "start": "1970-01-04T00:00:00Z", "reason": "step"}]}
    segments = p.measurement_segments(series, cs)
    assert [len(s) for s in segments] == [168, 72]
    cs["fit_segment"] = {"start": "1970-01-01T00:00:00Z", "end": "1970-01-03T23:00:00Z"}
    assert [len(s) for s in p.measurement_segments(series, cs)] == [72]


@pytest.mark.skipif("OTC_ORIGINAL_PROOF" not in os.environ, reason="requires pinned original cache")
def test_native_producer_values_and_retained_unavailable_sets():
    root = Path(os.environ["OTC_ORIGINAL_PROOF"])
    report = json.loads((root / "pilot/observed-reduction-report.json").read_text())
    drafts = {row["set_id"]: row for row in map(json.loads, (root / "pilot/observed-drafts.jsonl").read_text().splitlines())}
    witnesses = json.loads((root / "existing-monthly-witnesses.json").read_text())
    for witness in witnesses:
        row = next(r for r in report["records"] if r["set_id"] == witness["set_id"])
        draft = drafts[row["set_id"]]
        assert row["disposition"] == "reduced"
        assert draft["input_binding"]["member_sha256"] == witness["input_binding"]["member_sha256"]
        assert row["monthly_inputs"]["2022-01"] == pytest.approx(witness["monthly_values"], abs=1e-10)
        mm = row["used_monthly_inputs"]
        if row["control"]:
            comp = row["comparison_inputs"]
            ma, mb, acc = comp["short"], comp["control"], comp["control_primary"]
            diff = lambda key: np.mean([ma[m][key] - mb[m][key] for m in ma])
            ratio = lambda key: np.mean([ma[m][key] / mb[m][key] for m in ma])
            values = {key: acc[key] + diff(key) for key in ("MSL", "MTL", "DTL")}
            mn, gt = acc["MN"] * ratio("MN"), acc["GT"] * ratio("GT")
            values.update(MLW=values["MTL"] - mn / 2, MHW=values["MTL"] + mn / 2,
                          MLLW=values["DTL"] - gt / 2, MHHW=values["DTL"] + gt / 2)
            values = monthly.derived(values)
        elif row["data_span"]["start"] == "2002-01-01" and row["data_span"]["months"] >= 216:
            values = {key: np.mean([np.mean([value[key] for month, value in mm.items() if int(month[-2:]) == m])
                                   for m in range(1, 13)]) for key in monthly.LEVELS}
        else:
            values = {key: np.mean([value[key] for value in mm.values()]) for key in monthly.LEVELS}
        assert draft["draft_datum"]["named"] == pytest.approx({key.lower(): value for key, value in values.items()}, abs=1e-10)
        for key, basis in draft["draft_datum"]["basis"].items():
            assert basis["kind"] == "observed" and "uncertainty_m" not in basis
            assert basis["data_span"]["months"] == len(mm)
            assert basis["fallback"]["reason"] == "datum_gate_failed"
    unavailable = [r for r in report["records"] if r["disposition"] == "unavailable"]
    assert len(unavailable) == 2
    assert all(r["reason"] == "ticon_original_zero_tie_unavailable" for r in unavailable)
    assert all(drafts[r["set_id"]]["draft_datum"] is None for r in unavailable)
    assert report["counts"] == {"reduced": 3, "target_sets": 5, "unavailable": 2}


@pytest.mark.skipif("OTC_ORIGINAL_PROOF" not in os.environ, reason="requires pinned original cache")
def test_native_failed_clock_binding_is_reported_without_dropping_set(tmp_path):
    p = producer()
    root = Path(os.environ["OTC_ORIGINAL_PROOF"])
    receipt = json.loads((root.parent / "input-receipt.json").read_text())
    station = json.loads((root / "pilot-release.jsonl").read_text().splitlines()[0])
    station = copy.deepcopy(station)
    station["constant_sets"] = [station["constant_sets"][0]]
    station["constant_sets"][0]["time_base"]["published"] = "utc_plus_1"
    path = tmp_path / "wrong-clock.jsonl"
    path.write_text(json.dumps(station) + "\n")
    recipe = json.loads(Path(receipt["inputs"]["build_recipe"]["path"]).read_text())["argv"]
    result = p.produce(path, Path(receipt["inputs"]["gesla_manifest"]["path"]).parent,
        Path(recipe[recipe.index("--fit-run") + 1]),
        Path(receipt["inputs"]["tcd"]["path"]), tmp_path / "failed")
    assert result["counts"] == {"failed": 1, "target_sets": 1}
    assert "published clock differs" in result["records"][0]["reason"]
    row = json.loads((tmp_path / "failed/observed-drafts.jsonl").read_text())
    assert row["set_id"] == station["constant_sets"][0]["set_id"]
    assert row["draft_datum"] is None and row["observed_keys"] == []


@pytest.mark.skipif("OTC_ORIGINAL_PROOF" not in os.environ, reason="requires pinned original cache")
def test_original_months_before_producer():
    """Prepare genuine native measurements before implementing orchestration."""
    root = Path(os.environ["OTC_ORIGINAL_PROOF"])
    receipt = json.loads((root.parent / "input-receipt.json").read_text())
    native = [json.loads(line) for line in (root / "pilot-release.jsonl").read_text().splitlines()]
    release = GeslaRelease(Path(receipt["inputs"]["gesla_manifest"]["path"]).parent)
    table = TcdTable.load(Path(receipt["inputs"]["tcd"]["path"]))
    roles = load_contributor_roles(Path(__file__).resolve().parents[3] / "data/gesla/contributor_roles.csv")
    fixtures = []
    for station in native:
        for cs in station["constant_sets"]:
            if cs["source"] != "gesla-fit":
                continue
            rid = cs["source_record_id"]
            record = release.record(rid, roles)
            series = clean(record, _label_correction(rid, cs["time_base"]["published"]))
            # January 2022 with full original context either side, no sample invention.
            start = int(datetime(2021, 12, 28, tzinfo=timezone.utc).timestamp())
            end = int(datetime(2022, 2, 4, tzinfo=timezone.utc).timestamp())
            use = (series.times_s >= start) & (series.times_s < end)
            cons = {c["name"]: (c["amplitude_m"], c["phase_deg"]) for c in cs["constituents"]}
            predict = tabulate.predictor(table, cons, cs["datum"]["msl_offset_m"])
            tab = tabulate.tabulate(series.times_s[use], series.heights_m[use], predict, interval_s=3600)
            means = monthly.monthly_means(tab)[(2022, 1)]
            assert all(np.isfinite(means[k]) for k in monthly.LEVELS)
            binding = {"member_sha256": record.member_sha256, "time_base": cs["time_base"],
                       "record_span": cs["record_span"], "datum": cs["datum"],
                       "tcd_sha256": receipt["inputs"]["tcd"]["sha256"]}
            fixture = {"station_id": station["station_id"], "set_id": cs["set_id"],
                       "source_record_id": rid, "input_binding": binding,
                       "month": "2022-01", "monthly_values": means,
                       "used_context": {"start": str(series.times_s[use][0]), "end": str(series.times_s[use][-1])}}
            fixtures.append(fixture)
            print(json.dumps(fixture, sort_keys=True))
    assert len(fixtures) == 3
    (root / "existing-monthly-witnesses.json").write_text(json.dumps(fixtures, sort_keys=True, indent=2) + "\n")
