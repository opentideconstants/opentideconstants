"""Original-record reduction orchestration and optional immutable native witnesses."""
import ast
import copy
import hashlib
import importlib
import inspect
import json
import os
from dataclasses import replace
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


def test_producer_has_explicit_resume_and_bounded_workers_without_selection():
    parameters = inspect.signature(producer().produce).parameters
    assert parameters["resume"].default is False
    assert parameters["workers"].default == 1
    assert "only" not in parameters


@pytest.mark.parametrize("workers", (0, -1, 5, 1.5, True))
def test_invalid_workers_fail_before_output_changes(tmp_path, workers):
    absent = tmp_path / "missing-input"
    out = tmp_path / "output"
    with pytest.raises(ValueError, match="workers"):
        producer().produce(absent, absent, absent, absent, out, workers=workers)
    assert not out.exists()


@pytest.mark.parametrize("disposition", ("failed", "unavailable"))
def test_checkpoint_retains_completed_disposition_and_rejects_corruption(tmp_path, disposition):
    p = producer()
    profile = {"report": {"disposition": disposition, "reason": "actual reason"}, "binding": {"member_sha256": "a"}}
    p.write_checkpoint(tmp_path, "key", "job-sha", profile, None)
    assert p.read_checkpoint(tmp_path, "key", "job-sha") == profile
    payload = tmp_path / "key.profile.json"
    payload.write_text(payload.read_text() + " ")
    with pytest.raises(ValueError, match="checkpoint"):
        p.read_checkpoint(tmp_path, "key", "job-sha")


def test_checkpoint_missing_partial_and_wrong_identity_are_not_hits(tmp_path):
    p = producer()
    assert p.read_checkpoint(tmp_path, "key", "job-sha") is None
    # An uncommitted temporary payload is incomplete, never a completed cache hit.
    (tmp_path / "key.profile.json.part").write_text("partial")
    assert p.read_checkpoint(tmp_path, "key", "job-sha") is None
    p.write_checkpoint(tmp_path, "key", "job-sha", {"report": {"disposition": "unavailable"}}, None)
    with pytest.raises(ValueError, match="checkpoint"):
        p.read_checkpoint(tmp_path, "key", "wrong-job")
    (tmp_path / "key.profile.json").unlink()
    with pytest.raises(ValueError, match="checkpoint"):
        p.read_checkpoint(tmp_path, "key", "job-sha")


def test_completed_run_missing_checkpoint_must_refuse_instead_of_remeasure(tmp_path):
    with pytest.raises(ValueError, match="checkpoint"):
        producer().read_checkpoint(tmp_path, "key", "job-sha", require_complete=True)


def producer():
    return importlib.import_module("otc_pipeline.observed_datums.produce")


@pytest.fixture(scope="module")
def cached_control_profiles():
    if "OTC_ZERO_CONTROL_CACHE" not in os.environ:
        pytest.skip("requires frozen actual full-cohort measurement checkpoints")
    p = producer()
    root = Path(os.environ["OTC_ZERO_CONTROL_CACHE"])
    cache = root / "checkpoints"
    run = json.loads((root / "observed-run.json").read_text())
    rids = ("zwartsluistg-zwa-nld-cmems", "hellevoetsluis-hel-nld-rws",
            "werkendambuitentg-wer-nld-cmems", "penarth-pen-gbr-cco")
    targets = {}
    for item in run["targets"]:
        for rid in rids:
            if item["set_id"].endswith("/gesla-fit/" + rid):
                targets[rid] = p.read_checkpoint(cache, item["key"], item["job_sha256"], require_complete=True)
    assert len(targets) == 4
    primaries = []
    for item in run["targets"]:
        job = json.loads((cache / f"{item['key']}.job.json").read_text())
        if not any(p.tide_class(job["cs"]) == p.tide_class(target["cs"]) and
                   p.ticon_sets._km((job["station"]["lat"], job["station"]["lon"]),
                                   (target["station"]["lat"], target["station"]["lon"])) <= 250
                   for target in targets.values()):
            continue
        profile_path = cache / f"{item['key']}.profile.json"
        profile = json.loads(profile_path.read_text())
        if profile.get("primary") and profile["report"].get("reason") is None:
            authenticated = p.read_checkpoint(cache, item["key"], item["job_sha256"], require_complete=True)
            primaries.append(p.primary_summary(authenticated))
    return cache, targets, primaries, run


def test_actual_cached_control_has_defined_reduction(cached_control_profiles):
    p = producer()
    cache, targets, primaries, _ = cached_control_profiles
    target = copy.deepcopy(targets["zwartsluistg-zwa-nld-cmems"])
    try:
        row, detail = p.reduce_profile(target, primaries, cache)
    except ZeroDivisionError as error:
        trace = error.__traceback__
        finding = {}
        while trace is not None:
            local = trace.tb_frame.f_locals
            if trace.tb_frame.f_code.co_name == "choose_control":
                finding["selected_control"] = local["other"]["cs"]["set_id"]
                finding["method"] = local["method"]
            if trace.tb_frame.f_code.co_name == "mean_ratio":
                level = local["lv"]
                finding["denominator_level"] = level
                finding["zero_months"] = [list(month) for month in local["months"] if local["mb"][month][level] == 0]
                finding["first_values"] = {side: local[side][tuple(finding["zero_months"][0])] for side in ("ma", "mb")}
            trace = trace.tb_next
        print(json.dumps(finding, sort_keys=True))
        raise
    assert row["draft_datum"]["named"]
    assert detail["disposition"] == "reduced"
    rejected = detail["ineligible_controls"]
    assert any(r["control_set_id"].endswith("/hundsmhlen-4960020-deu-wsv") and
               r["month"] == "2014-08" and r["level"] == "DLQ" for r in rejected)
    assert not detail["control"] or not detail["control"]["set_id"].endswith("/hundsmhlen-4960020-deu-wsv")
    # Independently check three scalars against the actual selected common-month inputs.
    values = row["draft_datum"]["named"]
    if detail["control"]:
        inputs = detail["comparison_inputs"]
        ma, mb, acc = inputs["short"], inputs["control"], inputs["control_primary"]
        mtl = acc["MTL"] + np.mean([ma[m]["MTL"] - mb[m]["MTL"] for m in ma])
        mn = acc["MN"] * np.mean([ma[m]["MN"] / mb[m]["MN"] for m in ma])
        expected = {"mtl": mtl, "mlw": mtl - mn / 2, "mhw": mtl + mn / 2}
    else:
        expected = {key: np.mean([month[key.upper()] for month in detail["used_monthly_inputs"].values()])
                    for key in ("mtl", "mlw", "mhw")}
    for key, value in expected.items():
        assert values[key] == pytest.approx(value, abs=1e-12)
    proof = Path(os.environ["OTC_ZERO_CONTROL_PROOF"])
    (proof / "target-green.json").write_text(json.dumps({"row": row, "detail": detail}, sort_keys=True, indent=2) + "\n")
    print(json.dumps({"set_id": row["set_id"], "method": detail["method"], "control": detail["control"],
                      "independent_values": expected, "ineligible_denominators": len(rejected)}, sort_keys=True))


def test_actual_cached_existing_valid_controls(cached_control_profiles):
    p = producer()
    cache, targets, primaries, run = cached_control_profiles
    output = {}
    for rid, target in targets.items():
        if rid == "zwartsluistg-zwa-nld-cmems":
            continue
        row, detail = p.reduce_profile(copy.deepcopy(target), primaries, cache)
        assert row["draft_datum"]["named"]
        output[rid] = {"row": row, "detail": detail}
    proof = Path(os.environ["OTC_ZERO_CONTROL_PROOF"])
    baseline = proof / "valid-controls-before.json"
    if baseline.exists():
        before = json.loads(baseline.read_text())
        for rid, actual in output.items():
            assert actual["row"] == before[rid]["row"]
            comparison = {key: value for key, value in actual["detail"].items() if key != "ineligible_controls"}
            assert comparison == before[rid]["detail"]
        print("three actual cached draft/control/method/month/binding witnesses exactly unchanged")
    (proof / os.environ.get("OTC_ZERO_VALID_NAME", "valid-controls.json")).write_text(json.dumps(output, sort_keys=True, indent=2) + "\n")
    names = {"timestamp", "good_hours", "is_primary", "input_binding", "measurement_segments", "compact_tabulation",
             "save_tab", "measure_profile", "write_json", "write_checkpoint", "_initialize_measurement_worker",
             "_measure_checkpoint", "_measurement_worker", "_complete_measurements", "canonical_hash", "sha256"}
    tree = ast.parse(Path(p.__file__).read_text())
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    digest = hashlib.sha256(ast.dump(ast.Module(body=functions, type_ignores=[]), include_attributes=False).encode()).hexdigest()
    markers = [p.sha256(cache / f"{item['key']}.done.json") for item in run["targets"]]
    excluded = {"choose_control", "reduce_profile", "produce", "_produce", "main", "authenticate_measured_inputs",
                "_initialize_reduction_worker", "_reduction_worker", "reduction_results"}
    surface = ast.Module(body=[node for node in tree.body if not
                        (isinstance(node, ast.FunctionDef) and node.name in excluded)], type_ignores=[])
    surface_digest = hashlib.sha256(ast.dump(surface, include_attributes=False).encode()).hexdigest()
    (proof / "measurement-surface.json").write_text(json.dumps({"sha256": surface_digest}, sort_keys=True) + "\n")
    auth_path = proof / "frozen-measurement-auth.json"
    if auth_path.exists():
        assert json.loads(auth_path.read_text())["functions_sha256"] == digest
        return
    auth_path.write_text(json.dumps({"run_sha256": p.sha256(cache.parent / "observed-run.json"),
        "profile_manifests_sha256": p.canonical_hash({"ordered": markers}), "functions_sha256": digest,
        "function_names": sorted(names), "producer_sha256": run["identity"]["implementation"]["producer_sha256"]}, sort_keys=True, indent=2) + "\n")
    assert len(output) == 3


@pytest.mark.skipif("OTC_ZERO_CONTROL_CACHE" not in os.environ, reason="requires frozen real measurements")
def test_actual_measured_input_authentication_and_negative_controls(tmp_path, monkeypatch):
    p = producer()
    source = Path(os.environ["OTC_ZERO_CONTROL_CACHE"])
    run = json.loads((source / "observed-run.json").read_text())
    identity = copy.deepcopy(run["identity"])
    identity["implementation"] = p.implementation_identity()
    # The frozen run includes misaligned hourly means; it must not be rebound.
    with pytest.raises(ValueError, match="measurement implementation"):
        p.authenticate_measured_inputs(source, run, identity)
    changed = copy.deepcopy(identity)
    changed["inputs"]["release_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="current source"):
        p.authenticate_measured_inputs(source, run, changed)
    changed = copy.deepcopy(identity)
    changed["implementation"]["modules"]["gesla.qc"] = "0" * 64
    with pytest.raises(ValueError, match="scientific dependency"):
        p.authenticate_measured_inputs(source, run, changed)
    changed = copy.deepcopy(identity)
    changed["implementation"]["modules"]["otc_pipeline.observed_datums.monthly"] = "0" * 64
    with pytest.raises(ValueError, match="scientific dependency"):
        p.authenticate_measured_inputs(source, run, changed)
    changed_source = tmp_path / "changed-producer.py"
    changed_source.write_text(Path(p.__file__).read_text().replace("// 3600", "// 3601", 1))
    with monkeypatch.context() as patch:
        patch.setattr(p, "__file__", str(changed_source))
        with pytest.raises(ValueError, match="measurement implementation"):
            p.authenticate_measured_inputs(source, run, identity)
    (tmp_path / "observed-run.json").write_text(json.dumps(run) + " ")
    with pytest.raises(ValueError, match="frozen run"):
        p.authenticate_measured_inputs(tmp_path, run, identity)
    item = next(item for item in run["targets"] if item["set_id"].endswith("/zwartsluistg-zwa-nld-cmems"))
    key, job_sha = item["key"], item["job_sha256"]
    marker = source / "checkpoints" / f"{key}.done.json"
    files = json.loads(marker.read_text())["files"]
    for name in files:
        (tmp_path / name).symlink_to(source / "checkpoints" / name)
    (tmp_path / marker.name).write_bytes(marker.read_bytes())
    assert p.read_checkpoint(tmp_path, key, job_sha, require_complete=True)["mm"]
    profile = tmp_path / f"{key}.profile.json"
    original = profile.read_bytes()
    profile.unlink()
    profile.write_bytes(original + b" ")
    with pytest.raises(ValueError, match="artifact hash mismatch"):
        p.read_checkpoint(tmp_path, key, job_sha, require_complete=True)
    profile.unlink()
    with pytest.raises(ValueError, match="checkpoint"):
        p.read_checkpoint(tmp_path, key, job_sha, require_complete=True)
    (tmp_path / marker.name).unlink()
    (tmp_path / f"{key}.profile.json.part").write_bytes(original)
    with pytest.raises(ValueError, match="checkpoint missing"):
        p.read_checkpoint(tmp_path, key, job_sha, require_complete=True)
    print("legacy mean measurements REFUSED; changed source/code/dependency, corrupt/missing/partial checkpoints REFUSED")


def test_actual_cached_serial_and_four_reductions_are_exact(cached_control_profiles, monkeypatch, tmp_path):
    p = producer()
    cache, targets, primaries, run = cached_control_profiles
    selected = {target["cs"]["set_id"] for target in targets.values()}
    index = [item for item in run["targets"] if item["set_id"] in selected]
    serial = list(p.reduction_results(index, cache, primaries, 1, spool_parent=tmp_path))
    stale = tmp_path / ".reduction-stale"
    stale.mkdir()
    (stale / "0.json").write_text("foreign result")
    (stale / "1.json.part").write_text("partial result")
    parallel = list(p.reduction_results(index, cache, primaries, 4, spool_parent=tmp_path))
    assert list(tmp_path.iterdir()) == [stale]
    assert (stale / "0.json").read_text() == "foreign result"
    assert len(serial) == len(parallel) == 4
    assert json.dumps(serial, sort_keys=True, allow_nan=False) == json.dumps(parallel, sort_keys=True, allow_nan=False)
    assert [row["set_id"] for row, _ in parallel] == [item["set_id"] for item in index]
    # Execute the unchanged comparison once with the original monthly implementation.
    original = p.datum_t1.compare_reduce
    def uncached(ta, tb, accepted, supplied, method, *, common):
        cached = original(ta, tb, accepted, supplied, method, common=common)
        actual = original(ta, tb, accepted, monthly, method, common=common)
        assert cached == actual
        return cached
    monkeypatch.setattr(p.datum_t1, "compare_reduce", uncached)
    target = copy.deepcopy(targets["werkendambuitentg-wer-nld-cmems"])
    p.reduce_profile(target, primaries, cache)
    print("four actual records: serial/four ordered byte/value/control/method/month/binding equality; winning-pair adapter exact")


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
    saved_report = json.loads((tmp_path / "failed/observed-reduction-report.json").read_text())
    assert "published clock differs" in saved_report["records"][0]["reason"]
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


@pytest.mark.skipif("OTC_MEAN_CLOCK_PROOF" not in os.environ, reason="requires original hourly-mean witnesses")
def test_actual_hourly_mean_clock_integration(monkeypatch):
    """Real reader/QC/fit-clock integration, with bounded original-data tabulation windows."""
    p = producer()
    proof = Path(os.environ["OTC_MEAN_CLOCK_PROOF"])
    inputs = json.loads((proof / "inputs.json").read_text())
    cache = Path(inputs["original"]) / "checkpoints"
    run = json.loads((cache.parent / "observed-run.json").read_text())
    roles_path = Path(__file__).resolve().parents[3] / "data/gesla/contributor_roles.csv"
    p._initialize_measurement_worker(Path(inputs["gesla_root"]), Path(inputs["fit_run"]),
                                     Path(inputs["tcd"]), roles_path)
    release, dump, fit_run, manifest, fit_rows, audit, roles = p._WORKER_CONTEXT
    original_compact = p.compact_tabulation
    witnesses = []
    for item in inputs["targets"]:
        old = p.read_checkpoint(cache, item["key"], item["job_sha256"], require_complete=True)
        job = json.loads((cache / f"{item['key']}.job.json").read_text())
        rid = job["cs"]["source_record_id"]
        record = release.record(rid, roles)
        assert record.member_sha256 == job["binding"]["member_sha256"]
        # The published convention independently identifies raw stamps as interval starts.
        raw = clean(record)
        corr = p.gesla_sets.published_correction(audit.rows.get(rid),
                                               p.gesla_sets.declared_hours(release.metadata()[rid]))
        assert corr.kind == "utc_mean_start60"
        centred = clean(record, corr)
        np.testing.assert_array_equal(centred.times_s, raw.times_s + 1800)
        assert p.canonical_hash(job) == item["job_sha256"]
        common = dict(run["identity"]["inputs"])
        current_binding = p.input_binding(job["station"], job["cs"], member=release.members[rid],
            fit_sha256=p.sha256(fit_run / "fits" / f"{rid}.json"), **common)
        assert current_binding == job["binding"]
        consumers = [row for row in inputs["consumers"] if row["control"]["set_id"] == item["set_id"]]
        consumer = None
        for row in consumers:
            consumer_item = next(target for target in run["targets"] if target["set_id"] == row["set_id"])
            candidate = p.read_checkpoint(cache, consumer_item["key"], consumer_item["job_sha256"], require_complete=True)
            overlap = sorted(set(candidate.get("mm", {})) & set(old["mm"]))
            if overlap:
                consumer = candidate
                year, month = overlap[len(overlap) // 2]
                break
        if consumer is None:
            year, month = 2022, 1
        start = p.timestamp(f"{year:04d}-{month:02d}-01T00:00:00Z") - 32 * 86400
        end = start + 96 * 86400
        use = (raw.times_s >= start) & (raw.times_s < end)
        interval_starts = replace(raw, times_s=raw.times_s[use], heights_m=raw.heights_m[use])
        captured = {}

        def bounded_compact(series, predict, sampling):
            assert sampling == "mean"
            window = (series.times_s >= start) & (series.times_s < end)
            selected = replace(series, times_s=series.times_s[window], heights_m=series.heights_m[window])
            actual_tab, actual_mm = original_compact(selected, predict, sampling)
            oracle_tab, oracle_mm = original_compact(interval_starts, predict, "mean")
            _, actual_centres = tabulate._sampled(predict, selected.times_s, 3600, sampling)
            expected_centres = interval_starts.times_s + 1800
            captured.update(actual=actual_tab, oracle=oracle_tab, actual_mm=actual_mm, oracle_mm=oracle_mm,
                            centre_error_s=int(actual_centres[0] - expected_centres[0]),
                            exact_arrays=all(np.array_equal(getattr(actual_tab, key), getattr(oracle_tab, key))
                                for key in ("msl_t", "msl_h", "ev_t", "ev_h", "ev_kind", "ev_day", "ev_label")))
            return actual_tab, actual_mm

        monkeypatch.setattr(p, "compact_tabulation", bounded_compact)
        actual_profile, actual_tab = p.measure_profile(job, release, dump, fit_run, manifest, fit_rows, audit, roles)
        assert actual_profile["report"]["reason"] is None
        assert actual_profile["report"]["actual_qc_span"]["start"] == old["report"]["actual_qc_span"]["start"]
        values = captured["actual_mm"][(year, month)]
        expected = captured["oracle_mm"][(year, month)]
        witness = {"set_id": item["set_id"], "member_sha256": record.member_sha256,
                   "job_sha256": item["job_sha256"], "month": f"{year:04d}-{month:02d}",
                   "centre_error_s": captured["centre_error_s"], "exact_arrays": captured["exact_arrays"],
                   "actual": values, "interval_oracle": expected,
                   "qc_span_unchanged": actual_profile["report"]["actual_qc_span"] == old["report"]["actual_qc_span"]}
        if consumer is not None:
            target_tab = p.load_tab(cache / consumer["cache"])
            method = p.comparison_method(consumer["station"], consumer["cs"])
            if method == "direct":
                common_days = target_tab.complete_days & actual_tab.complete_days & captured["oracle"].complete_days
                ma = monthly.monthly_means(target_tab, days=common_days, min_days=monthly.MIN_COMMON_DAYS)
                before = monthly.monthly_means(actual_tab, days=common_days, min_days=monthly.MIN_COMMON_DAYS)
                after = monthly.monthly_means(captured["oracle"], days=common_days, min_days=monthly.MIN_COMMON_DAYS)
                pair = sorted(set(ma) & set(before) & set(after))
                assert pair
                effect = {"actual": float(np.mean([ma[key]["MHW"] - before[key]["MHW"] for key in pair])),
                          "oracle": float(np.mean([ma[key]["MHW"] - after[key]["MHW"] for key in pair]))}
            else:
                before = p.datum_t1.compare_reduce(target_tab, actual_tab, old["primary"], monthly, method, common=True)
                after = p.datum_t1.compare_reduce(target_tab, captured["oracle"], old["primary"], monthly, method, common=True)
                assert before is not None and after is not None
                effect = {"actual": before, "oracle": after}
            witness["control_effect"] = {"actual_selected_consumer": consumer["cs"]["set_id"], "method": method,
                "scope": "bounded common-month operands with legacy primary held fixed; not refreshed full reduction", **effect}
        witnesses.append(witness)
        print(json.dumps(witness, sort_keys=True), flush=True)
    (proof / "native-result.json").write_text(json.dumps(witnesses, sort_keys=True, indent=2) + "\n")
    assert len(witnesses) == 3
    assert all(row["centre_error_s"] == 0 and row["exact_arrays"] for row in witnesses)
    assert all(row["actual"] == row["interval_oracle"] for row in witnesses)
    assert all(row["qc_span_unchanged"] for row in witnesses)
    assert all("control_effect" in row for row in witnesses)
    assert all(row["control_effect"]["actual"] == row["control_effect"]["oracle"]
               for row in witnesses if "control_effect" in row)


@pytest.mark.skipif("OTC_MEAN_CLOCK_PROOF" not in os.environ, reason="requires frozen full-cohort metadata")
def test_actual_hourly_mean_refresh_scope():
    p = producer()
    proof = Path(os.environ["OTC_MEAN_CLOCK_PROOF"])
    inputs = json.loads((proof / "inputs.json").read_text())
    source = Path(inputs["original"])
    assert p.sha256(source / "observed-run.json") == "a8dfec8864ca6944a01d27a60d9e7a3f5799faec7d217a053d4cef75ef583728"
    run = json.loads((source / "observed-run.json").read_text())
    impacted = []
    for item in run["targets"]:
        cache = source / "checkpoints"
        job = json.loads((cache / f"{item['key']}.job.json").read_text())
        assert p.canonical_hash(job) == item["job_sha256"]
        if job["cs"].get("time_base", {}).get("published") != "utc_mean_start60":
            continue
        old = p.read_checkpoint(cache, item["key"], item["job_sha256"], require_complete=True)
        assert old.get("mm") and old["report"]["reason"] is None
        impacted.append({**item, "old_marker_sha256": p.sha256(cache / f"{item['key']}.done.json")})
    assert {row["set_id"] for row in impacted} == {
        row["set_id"] for row in json.loads((proof / "impacted.json").read_text())}
    result = {"frozen_run_sha256": p.sha256(source / "observed-run.json"), "targets": len(run["targets"]),
              "invalid_mean_measurements": len(impacted), "unchanged_measurements": len(run["targets"]) - len(impacted),
              "impacted": impacted}
    (proof / "authenticated-impact.json").write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    print(json.dumps({key: value for key, value in result.items() if key != "impacted"}, sort_keys=True))
