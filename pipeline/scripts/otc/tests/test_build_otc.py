"""Direct consumer checks for staging official computed datums without admission."""
import copy
import hashlib
import json
import os
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from otc_pipeline import build_otc
from otc_pipeline.lat_hat import EPOCH


@pytest.fixture
def staged_input(tmp_path):
    datum = {"zero": "station_datum", "msl_offset_m": 1.0,
             "named": {"lat": 0.5, "hat": 2.0},
             "basis": {key: {"kind": "published", "method": "source"} for key in ("lat", "hat")}}
    cs = {"set_id": "OTC-test/noaa/9410660", "source": "noaa", "source_type": "official", "datum": datum}
    draft = copy.deepcopy(datum)
    for level, value in (("lat", 0.51), ("hat", 1.99)):
        draft["named"][f"otc_{level}"] = value
        draft["basis"][f"otc_{level}"] = {"kind": "computed", "method": "harmonic_extremes",
                                           "epoch": EPOCH, "uncertainty_m": 0.02,
                                           "uncertainty_basis": "calibrated"}
    row = {"set_id": cs["set_id"], "draft_datum": draft, "calibration_status": "available",
           "raw": {"lat_m": 0.51, "hat_m": 1.99}}
    return {"stations": [{"station_id": "OTC-test", "constant_sets": [cs]}]}, row, tmp_path / "drafts.jsonl"


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
