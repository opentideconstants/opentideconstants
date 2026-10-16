"""Direct consumer checks for staging official computed datums without admission."""
import copy
import hashlib
import json
import os
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from otc_pipeline import build_otc
from otc_pipeline.lat_hat import EPOCH, START, END, computation_input_sha256


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
