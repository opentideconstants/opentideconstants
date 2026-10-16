"""Direct consumer checks for staging official computed datums without admission."""
import copy
import hashlib
import importlib
import json
import os
from pathlib import Path
from statistics import stdev
import sys

import pytest
from jsonschema import Draft202012Validator

from otc_pipeline import build_otc
from otc_pipeline.lat_hat import EPOCH, START, END, computation_input_sha256, datum_draft
from otc_pipeline import lat_hat


@pytest.mark.skipif("OTC_GESLA_METADATA_PROOF" not in os.environ, reason="requires actual frozen GESLA metadata cases")
@pytest.mark.parametrize("index", range(3))
def test_native_gesla_sampling_class_is_not_timestamp_verification(index, tmp_path):
    root = Path(os.environ["OTC_GESLA_METADATA_PROOF"])
    directory = root / "missing-gesla"
    facts = json.loads((directory / "metadata-issue-facts.json").read_text())
    sid = facts["actual_gesla_wrong_sampling_examples"][index]["set_id"]
    entry = next(e for e in json.loads((directory / "roster.json").read_text())["rows"] if e["set_id"] == sid)
    payload = json.loads(Path(entry["path"]).read_text())
    assert hashlib.sha256(Path(entry["path"]).read_bytes()).hexdigest() == entry["input_sha256"]
    frozen = None
    for path in [directory / "pilot.jsonl", *[directory / f"shard-{n}.jsonl" for n in range(4)]]:
        with path.open() as stream:
            for line in stream:
                candidate = json.loads(line)
                if candidate["set_id"] == sid:
                    frozen = candidate
                    break
        if frozen is not None:
            break
    assert frozen is not None
    calibration = json.loads((directory / "diagnostic-calibration.json").read_text())
    cs = payload["constant_set"]
    original = copy.deepcopy((cs, frozen))
    assert cs["time_base"]["code"] == "verified"
    assert not any("sampl" in key for key in cs)
    row = lat_hat.calibrated_draft(cs, frozen, calibration)
    sampling = 0.011 * sum(c["amplitude_m"] for c in cs["constituents"] if c["name"] in ("M2", "S2"))
    assert sampling > 0
    for level in ("lat", "hat"):
        parts = row["uncertainty_components"][level]
        assert parts["sampling_verified"] is False
        assert parts["sampling_m"] == sampling
        basis = row["draft_datum"]["basis"][level]
        assert basis["uncertainty_m"] == calibration["gesla-fit"][level]["sample_sd_m"] + sampling
        expected_label = "propagated" if sampling >= parts["base_m"] else "calibrated"
        assert basis["uncertainty_basis"] == expected_label
        assert "sampling_assumed" in basis["flags"]
        assert basis["fallback"]["reason"] == "datum_gate_failed"
        assert f"plus sampling {sampling:.9g} m" in basis["fallback"]["otc_finding"]
    release = json.loads((root / "native-build/OTC_20261011.meta.json").read_text())
    with (root / "native-build/OTC_20261011.jsonl").open() as stream:
        station = next(s for line in stream if (s := json.loads(line))["station_id"] == entry["station_id"])
    release["stations"] = [station]
    before = copy.deepcopy(release)
    target = next(c for c in station["constant_sets"] if c["set_id"] == sid)
    path = tmp_path / "actual-metadata-draft.jsonl"
    sha = write_actual_draft(row, path)
    receipt = build_otc.apply_official_datum_fallbacks(release, path, sha)
    assert receipt["computed_basis_fallback_count"] == 2
    from otc_pipeline.release_writer import validate_release
    assert validate_release(release) == []
    for level in ("lat", "hat"):
        assert target["datum"]["named"][level] == frozen["raw"][f"{level}_m"]
        assert target["datum"]["basis"][level] == row["draft_datum"]["basis"][level]
    target["datum"] = copy.deepcopy(cs["datum"])
    assert release == before
    assert (cs, frozen) == original
    print(json.dumps({"set_id": sid, "sampling_m": sampling, "literal_sum_and_basis": True,
                      "raw_values_sources_published_rankings_and_bindings_preserved": True}))


@pytest.mark.skipif("OTC_GESLA_PILOT_RESULTS" not in os.environ, reason="requires the completed three missing-place pilot")
def test_native_missing_gesla_pilot_public_values_and_sdk(tmp_path):
    root = Path(os.environ["OTC_GESLA_PROOF"])
    path = Path(os.environ["OTC_GESLA_PILOT_RESULTS"])
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    ids = {r["set_id"] for r in rows}
    station_ids = {r["station_id"] for r in rows}
    assert len(ids) == len(station_ids) == 3
    release = json.loads((root / "native-build/OTC_20261011.meta.json").read_text())
    release["stations"] = []
    with (root / "native-build/OTC_20261011.jsonl").open() as stream:
        for line in stream:
            station = json.loads(line)
            if station["station_id"] in station_ids:
                release["stations"].append(station)
    original = copy.deepcopy(release)
    from otc_pipeline.release_writer import validate_release
    assert validate_release(release) == []
    missing = sum(level not in c.get("datum", {}).get("named", {}) for s in release["stations"]
                  for c in s["constant_sets"] if c["set_id"] in ids for level in ("lat", "hat"))
    assert missing == 6
    receipt = build_otc.apply_official_datum_fallbacks(release, path, hashlib.sha256(path.read_bytes()).hexdigest())
    assert receipt["computed_basis_fallback_count"] == 6
    assert receipt["normal_datum_gate_accepted"] is False
    assert validate_release(release) == []
    expected = {r["set_id"]: r for r in rows}
    witnesses = []
    for old, station in zip(original["stations"], release["stations"], strict=True):
        assert station["recommended_set_id"] == old["recommended_set_id"]
        for before, cs in zip(old["constant_sets"], station["constant_sets"], strict=True):
            if cs["set_id"] not in ids:
                assert cs == before
                continue
            assert {k: v for k, v in cs.items() if k != "datum"} == {k: v for k, v in before.items() if k != "datum"}
            for k, v in before["datum"].get("named", {}).items():
                assert cs["datum"]["named"][k] == v
                assert cs["datum"]["basis"][k] == before["datum"]["basis"][k]
            for level in ("lat", "hat"):
                row = expected[cs["set_id"]]
                assert cs["datum"]["named"][level] == row["raw"][f"{level}_m"]
                basis = cs["datum"]["basis"][level]
                parts = row["uncertainty_components"][level]
                assert basis["uncertainty_m"] == parts["base_m"] + parts["sampling_m"]
                assert "unknown" in basis["fallback"]["otc_finding"]
    sys.path.insert(0, os.environ["OTC_DATUM_SDK_SOURCE"])
    try:
        otc = importlib.import_module("opentideconstants")
        for mode in ("eager", "stream"):
            fixture = tmp_path / ("OTC_20261011.json" if mode == "eager" else "OTC_20261011.jsonl")
            if mode == "eager":
                fixture.write_text(json.dumps(release))
            else:
                fixture.write_text("".join(json.dumps(s) + "\n" for s in release["stations"]))
                fixture.with_name("OTC_20261011.meta.json").write_text(json.dumps({k: v for k, v in release.items() if k != "stations"}))
            with otc.OpenTideConstants(file=fixture, mode=mode) as client:
                for row in rows:
                    info = client.datums(row["station_id"], set_id=row["set_id"])
                    for level in ("lat", "hat"):
                        exposed = next(v for v in info.levels if v.key == level)
                        assert exposed.value_m == row["raw"][f"{level}_m"]
                        assert exposed.uncertainty_m == row["draft_datum"]["basis"][level]["uncertainty_m"]
                        assert exposed.fallback["reason"] == "datum_gate_failed"
                        assert dict(exposed.fallback) == row["draft_datum"]["basis"][level]["fallback"]
                    witnesses.append({"mode": mode, "set_id": row["set_id"], "values": {v.key: v.value_m for v in info.levels}})
    finally:
        sys.path.pop(0)
    (root / "missing-gesla/pilot-public-green.json").write_text(json.dumps({
        "new_places": 3, "new_levels": 6, "native_validation_errors": 0,
        "old_sources_values_and_rankings_unchanged": True, "sdk_witnesses": witnesses}, indent=2) + "\n")


@pytest.mark.skipif("OTC_GESLA_PROOF" not in os.environ, reason="requires actual native GESLA sets and frozen extrema")
@pytest.mark.parametrize("index", range(3))
def test_native_gesla_fallback_admission_preserves_sources(index, tmp_path):
    root = Path(os.environ["OTC_GESLA_PROOF"])
    entry = json.loads((root / "full-t5b/roster.json").read_text())["rows"][index]
    payload = json.loads(Path(entry["path"]).read_text())
    comparisons = [r for line in (root / "full-t5b/all-results.jsonl").read_text().splitlines()
                   if (r := json.loads(line)).get("set_id")]
    row = next(r for r in comparisons if r["set_id"] == entry["set_id"])
    release = json.loads((root / "native-build/OTC_20261011.meta.json").read_text())
    with (root / "native-build/OTC_20261011.jsonl").open() as stream:
        station = next(s for line in stream if (s := json.loads(line))["station_id"] == payload["station_id"])
    release["stations"] = [station]
    cs = next(c for c in station["constant_sets"] if c["set_id"] == entry["set_id"])
    before = copy.deepcopy(release)
    draft = datum_draft(cs, row["raw"])
    # Independently calculate the existing sample-spread and literal-sum rule.
    sampling = 0.011 * sum(c["amplitude_m"] for c in cs["constituents"] if c["name"] in ("M2", "S2"))
    for level in ("lat", "hat"):
        spread = stdev(r[f"{level}_residual_m"] for r in comparisons)
        draft["basis"][level].update(uncertainty_m=spread + sampling, uncertainty_basis="calibrated")
    row.update(draft_datum=draft, calibration_status="diagnostic",
               computation_input_sha256=computation_input_sha256({"constant_set": cs, "astro_table": payload["astro_table"]}))
    path = tmp_path / "native-gesla.jsonl"
    sha = write_actual_draft(row, path)
    receipt = build_otc.apply_official_datum_fallbacks(release, path, sha)
    assert receipt["normal_datum_gate_accepted"] is False
    assert receipt["computed_basis_fallback_count"] == 2
    for key in ("lat", "hat"):
        assert cs["datum"]["named"][key] == row["raw"][f"{key}_m"]
        assert cs["datum"]["basis"][key]["fallback"]["reason"] == "datum_gate_failed"
        assert "unknown" in cs["datum"]["basis"][key]["fallback"]["otc_finding"]
    cs["datum"] = before["stations"][0]["constant_sets"][station["constant_sets"].index(cs)]["datum"]
    assert release == before


@pytest.fixture
def actual_datum_case(tmp_path, request):
    """Three cached production stations and their frozen, unrecomputed extrema."""
    root = os.environ.get("OTC_DATUM_FIX_PROOF")
    if not root:
        pytest.skip("requires the actual cached datum fix proof")
    root = Path(root)
    contract = json.loads((root / "new-place-sdk-contract.json").read_bytes())
    case = contract["cases"][request.param]
    release = copy.deepcopy(contract["release_fixture"])
    release["stations"] = [s for s in release["stations"] if s["station_id"] == case["station_id"]]
    entry = next(e for e in json.loads((root / "missing-official/roster.json").read_text())["rows"]
                 if e["set_id"] == case["set_id"])
    payload = json.loads(Path(entry["path"]).read_text())
    cs = next(c for c in release["stations"][0]["constant_sets"] if c["set_id"] == case["set_id"])
    cs["datum"] = copy.deepcopy(payload["constant_set"]["datum"])
    row = next(r for line in (root / "official-public-drafts.jsonl").read_text().splitlines()
               if (r := json.loads(line)).get("set_id") == cs["set_id"])
    # Independently bind the actual request, before any intentional stale-input mutation.
    body = {"request": {"astro_table": payload["astro_table"], "constituents": cs["constituents"],
                        "msl_offset_m": cs["datum"]["msl_offset_m"],
                        "start": "2020-01-01T00:00:00Z", "end": "2039-01-01T00:00:00Z"},
            "convention_id": cs.get("convention_id"), "source_version": cs.get("source_version"),
            "time_base": cs.get("time_base"), "record_issues": cs.get("record_issues", []),
            "truncated_lows": sorted(k for k, b in cs["datum"].get("basis", {}).items()
                                     if "truncated_lows" in b.get("flags", []))}
    row["computation_input_sha256"] = hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return release, cs, row, tmp_path / "actual-drafts.jsonl"


def write_actual_draft(row, path):
    path.write_text(json.dumps(row) + "\n")
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def native_chart_datum_input(tmp_path):
    """Replay the three saved native drafts without recomputing their levels."""
    root = os.environ.get("OTC_CHART_DATUM_PROOF")
    if not root:
        pytest.skip("requires the saved native chart-datum witnesses")
    root = Path(root)
    facts = json.loads((root / "native-cases.json").read_text())
    fixture = Path(facts["fixture_path"])
    assert hashlib.sha256(fixture.read_bytes()).hexdigest() == facts["fixture_sha256"]
    original = json.loads(fixture.read_text())
    release = copy.deepcopy(original)
    ids = {row["set_id"] for row in facts["cases"]}
    assert len(ids) == 3
    drafts = Path(os.environ["OTC_CHART_DATUM_DRAFTS"])
    assert hashlib.sha256(drafts.read_bytes()).hexdigest() == "77006af6f3274eae6437651abc93440c3d2a9d7d6452c9a6f7fa50a82b21fa99"
    rows = [row for line in drafts.read_text().splitlines()
            if (row := json.loads(line)).get("set_id") in ids]
    assert len(rows) == 3
    for station in release["stations"]:
        for cs in station["constant_sets"]:
            if cs["set_id"] in ids:
                for level in ("lat", "hat"):
                    assert cs["datum"]["basis"][level]["kind"] == "computed"
                    del cs["datum"]["named"][level]
                    del cs["datum"]["basis"][level]
    path = tmp_path / "actual-chart-drafts.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return root, original, release, ids, path, hashlib.sha256(path.read_bytes()).hexdigest()


def test_native_chart_datum_designation_follows_labelled_level_attachment(native_chart_datum_input):
    _, original, release, ids, path, sha = native_chart_datum_input
    expected = copy.deepcopy(original)
    for station in expected["stations"]:
        for cs in station["constant_sets"]:
            if cs["set_id"] in ids:
                cs["datum"]["chart_datum"] = "lat"
    receipt = build_otc.apply_official_datum_fallbacks(release, path, sha)
    assert release == expected
    assert receipt["computed_basis_fallback_count"] == 6
    derivation = receipt["chart_datum_derivation"]
    assert derivation["changed_places"] == derivation["changed_sets"] == 3
    assert derivation["conventions_sha256"] == hashlib.sha256(
        Path(build_otc.__file__).with_name("chart_datum_conventions.json").read_bytes()).hexdigest()
    assert derivation["citations"]["iho-2021-uk"]["stated_convention"] == "approximate LAT"


def test_native_staging_only_does_not_designate_chart_datum(native_chart_datum_input):
    _, _, release, _, path, sha = native_chart_datum_input
    before = copy.deepcopy(release)
    receipt = build_otc.stage_official_datums(release, path, sha)
    assert release == before
    assert receipt["public_admission"] is False
    assert "chart_datum_derivation" not in receipt


@pytest.mark.parametrize("control", ["published_precedence", "uk_country_overseas_area_not_evidenced",
                                     "southern_norway_not_unmargined_lat", "flatline", "truncated_lows"])
def test_native_producer_chart_datum_controls(native_chart_datum_input, control, tmp_path):
    root, original, _, _, _, _ = native_chart_datum_input
    if control in ("flatline", "truncated_lows"):
        station = copy.deepcopy(original["stations"][0])
        for cs in station["constant_sets"]:
            if control == "flatline":
                cs.setdefault("record_issues", []).append({"issue": "flatline"})
            else:
                for basis in cs.get("datum", {}).get("basis", {}).values():
                    basis.setdefault("flags", []).append("truncated_lows")
    else:
        station = json.loads((root / "native-controls.json").read_text())[control]
    release = {"stations": [station]}
    before = copy.deepcopy(release)
    path = tmp_path / "no-drafts.jsonl"
    path.write_text("")
    receipt = build_otc.apply_official_datum_fallbacks(release, path, hashlib.sha256(path.read_bytes()).hexdigest())
    assert release == before
    assert receipt["chart_datum_derivation"]["changed_places"] == 0


@pytest.mark.parametrize("actual_datum_case", range(3), indirect=True)
def test_actual_tombstone_keeps_official_datum_producer_working(actual_datum_case):
    release, cs, row, path = actual_datum_case
    examples = json.loads((Path(__file__).parent / "fixtures/example-1.0.json").read_text())
    tombstone = copy.deepcopy(next(s for s in examples["stations"] if s["status"] == "removed"))
    tombstone["merged_into"] = release["stations"][0]["station_id"]
    release["stations"].append(tombstone)
    schema = json.loads((Path(__file__).parents[1] / "schema/otc-1.0.schema.json").read_text())
    Draft202012Validator(schema).validate(release)
    receipt = build_otc.apply_official_datum_fallbacks(release, path, write_actual_draft(row, path))
    assert release["stations"][-1] == tombstone
    assert receipt["computed_basis_fallback_count"] == 2


@pytest.mark.parametrize("actual_datum_case", range(3), indirect=True)
@pytest.mark.parametrize("mutation", ["amplitude", "phase", "table", "time_base", "missing_binding"])
def test_actual_stale_harmonics_rejected(actual_datum_case, mutation):
    release, cs, row, path = actual_datum_case
    if mutation == "amplitude":
        cs["constituents"][0]["amplitude_m"] += 0.001
    elif mutation == "phase":
        cs["constituents"][0]["phase_deg"] = (cs["constituents"][0]["phase_deg"] + 1) % 360
    elif mutation == "table":
        release["astro_tables"][0]["constituents"]["M2"]["f"][0] *= 1.001
    elif mutation == "time_base":
        cs["time_base"] = {"status": "unverified"}
    else:
        del row["computation_input_sha256"]
    with pytest.raises(ValueError, match="computation input"):
        build_otc.apply_official_datum_fallbacks(release, path, write_actual_draft(row, path))


@pytest.mark.parametrize("actual_datum_case", range(3), indirect=True)
def test_actual_unrelated_computed_basis_preserved(actual_datum_case):
    release, cs, row, path = actual_datum_case
    prior = {"kind": "computed", "method": "harmonic_extremes", "epoch": EPOCH,
             "uncertainty_m": 0.123, "uncertainty_basis": "propagated",
             "fallback": {"reason": "datum_gate_failed", "basis": "Existing independent computation",
                          "otc_finding": "Preserve this prior finding."}}
    published = next(k for k, b in cs["datum"]["basis"].items() if b["kind"] == "published")
    key = f"otc_{published}"
    cs["datum"]["named"][key] = cs["datum"]["named"][published]
    cs["datum"]["basis"][key] = copy.deepcopy(prior)
    row["draft_datum"]["named"][key] = cs["datum"]["named"][key]
    row["draft_datum"]["basis"][key] = copy.deepcopy(prior)
    schema = json.loads((Path(__file__).parents[1] / "schema/otc-1.0.schema.json").read_text())
    Draft202012Validator(schema).validate(release)
    receipt = build_otc.apply_official_datum_fallbacks(release, path, write_actual_draft(row, path))
    assert cs["datum"]["basis"][key] == prior
    assert receipt["computed_basis_fallback_count"] == 2


@pytest.mark.skipif("OTC_DATUM_FIX_PROOF" not in os.environ,
                    reason="requires the actual frozen519 inputs and completed build")
def test_actual519_binding_preserves_completed_build(tmp_path):
    root = Path(os.environ["OTC_DATUM_FIX_PROOF"])
    rows = {r["set_id"]: r for line in (root / "official-public-drafts.jsonl").read_text().splitlines()
            if (r := json.loads(line)).get("set_id")}
    paths = {}
    for name in ("full-t5", "t6", "missing-official"):
        for entry in json.loads((root / name / "roster.json").read_text())["rows"]:
            assert entry["set_id"] not in paths
            paths[entry["set_id"]] = entry
    assert rows.keys() == paths.keys() and len(rows) == 519
    bound = []
    for sid, row in rows.items():
        entry = paths[sid]
        raw_input = Path(entry["path"]).read_bytes()
        if entry.get("input_sha256"):
            assert hashlib.sha256(raw_input).hexdigest() == entry["input_sha256"]
        payload = json.loads(raw_input)
        assert payload["constant_set"]["set_id"] == sid
        updated = {**row, "computation_input_sha256": computation_input_sha256(payload)}
        assert {k: v for k, v in updated.items() if k != "computation_input_sha256"} == row
        bound.append(updated)
    artifact = root / "official-public-drafts-bound.jsonl"
    artifact.write_text("".join(json.dumps(r, sort_keys=True, allow_nan=False) + "\n" for r in bound))
    sha = hashlib.sha256(artifact.read_bytes()).hexdigest()
    baseline = root.parent / "datum-stated-batch/native-build"
    release = json.loads((baseline / "OTC_20261011.meta.json").read_bytes())
    release["stations"] = []
    with (baseline / "OTC_20261011.jsonl").open() as stream:
        for line in stream:
            station = json.loads(line)
            if any(cs["set_id"] in rows for cs in station.get("constant_sets", [])):
                release["stations"].append(station)
    receipt = build_otc.apply_official_datum_fallbacks(release, artifact, sha)
    assert receipt["staged_set_count"] == 519
    assert receipt["computed_basis_fallback_count"] == 1038
    resulting = {cs["set_id"]: cs["datum"] for s in release["stations"]
                 for cs in s.get("constant_sets", []) if cs["set_id"] in rows}
    checked = 0
    with (root / "native-build/OTC_20261011.jsonl").open() as stream:
        for line in stream:
            station = json.loads(line)
            for cs in station.get("constant_sets", []):
                if cs["set_id"] in resulting:
                    assert cs["datum"] == resulting[cs["set_id"]]
                    checked += 1
    assert checked == 519
    proof = {"bound_artifact": str(artifact), "sha256": sha, "sets": checked,
             "computed_levels": 1038, "unchanged_existing_build_data": True,
             "existing_build_commit": "d992aa3", "strict_predictions_rerun": 0}
    (root / "source-fix519-binding-green.json").write_text(json.dumps(proof, indent=2) + "\n")


@pytest.fixture
def staged_input(tmp_path):
    datum = {"zero": "station_datum", "msl_offset_m": 1.0,
             "named": {"lat": 0.5, "hat": 2.0},
             "basis": {key: {"kind": "published", "method": "source"} for key in ("lat", "hat")}}
    cs = {"set_id": "OTC-test/noaa/9410660", "source": "noaa", "source_type": "official", "datum": datum,
          "convention_id": "test", "constituents": [{"name": "M2", "amplitude_m": 1,
                                                       "phase_deg": 0, "speed_deg_per_hour": 28.9841042}]}
    draft = copy.deepcopy(datum)
    for level, value in (("lat", 0.51), ("hat", 1.99)):
        draft["named"][f"otc_{level}"] = value
        draft["basis"][f"otc_{level}"] = {"kind": "computed", "method": "harmonic_extremes",
                                           "epoch": EPOCH, "uncertainty_m": 0.02,
                                           "uncertainty_basis": "calibrated"}
    row = {"set_id": cs["set_id"], "draft_datum": draft, "calibration_status": "available",
           "raw": {"lat_m": 0.51, "hat_m": 1.99, "start": START, "end": END}}
    table = {"astro_table_id": "test"}
    row["computation_input_sha256"] = computation_input_sha256({"constant_set": cs, "astro_table": table})
    return {"stations": [{"station_id": "OTC-test", "constant_sets": [cs]}],
            "conventions": [{"convention_id": "test", "astro_table_id": "test"}],
            "astro_tables": [table]}, row, tmp_path / "drafts.jsonl"


@pytest.mark.parametrize("defect", ["sha", "published", "source", "method", "epoch", "uncertainty", "raw", "duplicate"])
def test_staging_rejects_invalid_or_changed_drafts(staged_input, defect):
    release, row, path = staged_input
    if defect == "published":
        row["draft_datum"]["named"]["lat"] = 0.6
    elif defect == "source":
        release["stations"][0]["constant_sets"][0]["source"] = "gesla-fit"
    elif defect == "method":
        row["draft_datum"]["basis"]["otc_lat"]["method"] = "source"
    elif defect == "epoch":
        row["draft_datum"]["basis"]["otc_lat"]["epoch"] = {}
    elif defect == "uncertainty":
        row["draft_datum"]["basis"]["otc_lat"]["uncertainty_m"] = -1.0
    elif defect == "raw":
        row["raw"]["lat_m"] = 0.52
    path.write_text(json.dumps(row) + "\n" + (json.dumps(row) + "\n" if defect == "duplicate" else ""))
    sha = "wrong" if defect == "sha" else hashlib.sha256(path.read_bytes()).hexdigest()
    before = copy.deepcopy(release)
    with pytest.raises(ValueError):
        build_otc.stage_official_datums(release, path, sha)
    assert release == before


def test_missing_official_levels_use_unprefixed_keys_and_keep_set_ranking(staged_input):
    release, row, path = staged_input
    release["stations"][0]["country"] = "FRA"
    cs = release["stations"][0]["constant_sets"][0]
    cs["datum"]["named"].clear()
    cs["datum"]["basis"].clear()
    for level in ("lat", "hat"):
        row["draft_datum"]["named"][level] = row["draft_datum"]["named"].pop(f"otc_{level}")
        row["draft_datum"]["basis"][level] = row["draft_datum"]["basis"].pop(f"otc_{level}")
    path.write_text(json.dumps(row) + "\n")
    receipt = build_otc.apply_official_datum_fallbacks(release, path, hashlib.sha256(path.read_bytes()).hexdigest())
    assert receipt["computed_basis_fallback_count"] == 2
    assert set(cs["datum"]["named"]) == {"lat", "hat"}
    assert "fallback" not in cs
    assert cs["datum"]["basis"]["lat"]["fallback"]["reason"] == "datum_gate_failed"
    assert cs["datum"]["chart_datum"] == "lat"
    assert receipt["chart_datum_derivation"]["changed_places"] == 1


@pytest.mark.skipif("OTC_DATUM_NATIVE_RELEASE" not in os.environ,
                    reason="native staging proof requires the pinned cached release and drafts")
def test_native_official_drafts_stage_without_changing_release():
    release_path = Path(os.environ["OTC_DATUM_NATIVE_RELEASE"])
    drafts_path = Path(os.environ["OTC_DATUM_NATIVE_DRAFTS"])
    release = json.loads(release_path.read_bytes())
    before = hashlib.sha256(json.dumps(release, sort_keys=True).encode()).hexdigest()
    candidate = build_otc.stage_official_datums(
        release, drafts_path, hashlib.sha256(drafts_path.read_bytes()).hexdigest())
    assert candidate["public_admission"] is False
    assert candidate["staged_set_count"] == 412
    assert candidate["staged_level_count"] == 824
    assert candidate["release_unchanged"] is True
    assert hashlib.sha256(json.dumps(release, sort_keys=True).encode()).hexdigest() == before
    native = {cs["set_id"]: cs for station in release["stations"] for cs in station["constant_sets"]}
    source_rows = {row["set_id"]: row for line in drafts_path.read_text().splitlines()
                   if (row := json.loads(line)).get("set_id")}
    for staged in candidate["drafts"]:
        sid = staged["set_id"]
        assert staged["datum"] == source_rows[sid]["draft_datum"]
        assert staged["source"] == native[sid]["source"]
        assert staged["input_sha256"] == candidate["input_sha256"]
        for key, basis in native[sid]["datum"]["basis"].items():
            if basis["kind"] == "published":
                assert staged["datum"]["named"][key] == native[sid]["datum"]["named"][key]
                assert staged["datum"]["basis"][key] == basis


@pytest.mark.skipif("OTC_DATUM_NATIVE_RELEASE" not in os.environ,
                    reason="native fallback schema proof requires the pinned cached release and drafts")
def test_native_computed_fallback_schema_preserves_official_sets():
    release = json.loads(Path(os.environ["OTC_DATUM_NATIVE_RELEASE"]).read_bytes())
    drafts = [row for line in Path(os.environ["OTC_DATUM_NATIVE_DRAFTS"]).read_text().splitlines()
              if (row := json.loads(line)).get("set_id")]
    native = {cs["set_id"]: cs for station in release["stations"] for cs in station["constant_sets"]}
    schema_path = Path(__file__).resolve().parents[1] / "schema/otc-1.0.schema.json"
    schema = json.loads(schema_path.read_text())
    validator = Draft202012Validator({"$ref": "#/$defs/datum", "$defs": schema["$defs"]})
    for row in drafts[:3]:
        cs = native[row["set_id"]]
        before = copy.deepcopy(cs)
        datum = copy.deepcopy(row["draft_datum"])
        for key, basis in datum["basis"].items():
            if basis["kind"] == "computed":
                basis["fallback"] = {"reason": "datum_gate_failed", "basis": "Frozen harmonic extrema with tuning uncertainty",
                                     "otc_finding": "Full DT4a tuning gate has not passed; no validated accuracy claim."}
        validator.validate(datum)
        assert cs == before
        assert "fallback" not in cs
        for key, basis in cs["datum"]["basis"].items():
            if basis["kind"] == "published":
                assert datum["basis"][key] == basis
                assert datum["named"][key] == cs["datum"]["named"][key]


@pytest.mark.skipif("OTC_DATUM_NATIVE_RELEASE" not in os.environ,
                    reason="native producer proof requires the pinned cached release and drafts")
def test_native_official_fallbacks_publish_values_without_demoting_sets():
    release = json.loads(Path(os.environ["OTC_DATUM_NATIVE_RELEASE"]).read_bytes())
    drafts_path = Path(os.environ["OTC_DATUM_NATIVE_DRAFTS"])
    before = copy.deepcopy(release)
    receipt = build_otc.apply_official_datum_fallbacks(
        release, drafts_path, hashlib.sha256(drafts_path.read_bytes()).hexdigest())
    assert receipt["public_admission"] is True
    assert receipt["normal_datum_gate_accepted"] is False
    assert receipt["computed_basis_fallback_count"] == 824
    candidates = {row["set_id"]: row for line in drafts_path.read_text().splitlines()
                  if (row := json.loads(line)).get("set_id")}
    count = 0
    for original_station, station in zip(before["stations"], release["stations"], strict=True):
        assert {k: v for k, v in station.items() if k != "constant_sets"} == {
            k: v for k, v in original_station.items() if k != "constant_sets"}
        for old, cs in zip(original_station["constant_sets"], station["constant_sets"], strict=True):
            if cs["set_id"] not in candidates:
                assert cs == old
                continue
            assert {k: v for k, v in cs.items() if k != "datum"} == {k: v for k, v in old.items() if k != "datum"}
            expected = copy.deepcopy(candidates[cs["set_id"]]["draft_datum"])
            for key, basis in cs["datum"]["basis"].items():
                if basis["kind"] == "computed":
                    assert basis["fallback"]["reason"] == "datum_gate_failed"
                    assert basis["fallback"]["basis"] and basis["fallback"]["otc_finding"]
                    expected["basis"][key]["fallback"] = basis["fallback"]
                    count += 1
            assert cs["datum"] == expected
    assert count == 824
    from otc_pipeline.release_writer import validate_release
    assert validate_release(release) == []


@pytest.mark.skipif("OTC_MISSING_OFFICIAL_RESULTS" not in os.environ,
                    reason="full missing-level value proof requires the completed107 native outputs")
def test_native_missing_official_fallbacks_add107_usable_places():
    release = json.loads(Path(os.environ["OTC_DATUM_NATIVE_RELEASE"]).read_bytes())
    path = Path(os.environ["OTC_MISSING_OFFICIAL_RESULTS"])
    before = copy.deepcopy(release)

    def usable_places(doc, level):
        found = set()
        for station in doc["stations"]:
            if station.get("status") != "active":
                continue
            datums = [cs.get("datum", {}) for cs in station.get("constant_sets", [])]
            datums.append(station.get("subordinate_offsets", {}).get("datum", {}))
            if any(any(key in datum.get("named", {}) and datum.get("basis", {}).get(key, {}).get("kind")
                       in ("published", "computed", "observed") for key in (level, f"otc_{level}")) for datum in datums):
                found.add(station["station_id"])
        return found

    initial = {level: usable_places(before, level) for level in ("lat", "hat")}
    receipt = build_otc.apply_official_datum_fallbacks(release, path, hashlib.sha256(path.read_bytes()).hexdigest())
    assert receipt["staged_set_count"] == 107
    assert receipt["computed_basis_fallback_count"] == 214
    assert receipt["normal_datum_gate_accepted"] is False
    candidate_ids = {row["set_id"] for line in path.read_text().splitlines() if (row := json.loads(line)).get("set_id")}
    for old_station, station in zip(before["stations"], release["stations"], strict=True):
        assert {k: v for k, v in station.items() if k != "constant_sets"} == {k: v for k, v in old_station.items() if k != "constant_sets"}
        for old, cs in zip(old_station["constant_sets"], station["constant_sets"], strict=True):
            if cs["set_id"] not in candidate_ids:
                assert cs == old
                continue
            assert {k: v for k, v in cs.items() if k != "datum"} == {k: v for k, v in old.items() if k != "datum"}
            for key, value in old["datum"].get("named", {}).items():
                assert cs["datum"]["named"][key] == value
                assert cs["datum"]["basis"][key] == old["datum"]["basis"][key]
            for level in ("lat", "hat"):
                assert cs["datum"]["basis"][level]["fallback"]["reason"] == "datum_gate_failed"
    for level in ("lat", "hat"):
        final = usable_places(release, level)
        assert len(initial[level]) == 1258
        assert initial[level] <= final
        assert len(final - initial[level]) == 107
        assert len(final) == 1365
    from otc_pipeline.release_writer import validate_release
    assert validate_release(release) == []
