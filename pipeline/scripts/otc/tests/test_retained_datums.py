"""Retained source values do not depend on predictive datum admission."""
import copy
import hashlib
import json
import os
from pathlib import Path

import pytest

from otc_pipeline.release_writer import validate_release, write_release
from otc_pipeline import build_otc
from otc_pipeline.datums import derive_chart_datums


@pytest.fixture(scope="module")
def actual_observed_retention_only():
    evidence = os.environ.get("OTC_OBSERVED_RECEIPT_PROOF")
    if not evidence:
        pytest.skip("requires actual held observed retention-only rows")
    root = Path(evidence)
    facts = json.loads((root / "input-binding.json").read_text())
    raw = (root / "actual-input.json").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == facts["selected_input_sha256"]
    return json.loads(raw)


@pytest.mark.parametrize("index", range(3))
def test_actual_observed_retention_only_publication_receipt(actual_observed_retention_only, index):
    row = copy.deepcopy(actual_observed_retention_only["rows"][index])
    station = next(s for s in actual_observed_retention_only["stations"]
                   if s["station_id"] == row["station_id"])
    release = {"stations": [copy.deepcopy(station)]}
    assert row["draft_datum"] is None and row["observed_keys"] == []
    assert row["retained_observed"]
    # Settle existing chart designations first to isolate source retention.
    derive_chart_datums(release)
    before = copy.deepcopy(release)
    staged = {"drafts": [], "staged_level_count": 0, "retained_rows": [row]}
    receipt = build_otc._publish_observed_datum_stage(release, staged)
    count = len(row["retained_observed"])
    print(f"REAL {row['station_id']}: source_changed={release != before} "
          f"retained_candidates={count} chart_changes={receipt['chart_datum_derivation']['changed_sets']} "
          f"release_unchanged={receipt['release_unchanged']}")
    assert receipt["chart_datum_derivation"]["changed_sets"] == 0
    assert release != before
    assert receipt["release_unchanged"] is False
    assert receipt["retained_candidate_count"] == count
    records = release["stations"][0]["source_records"]
    assert all(any(candidate in record.get("retained_datums", []) for record in records)
               for candidate in row["retained_observed"])
    restored = copy.deepcopy(release)
    for record, old in zip(restored["stations"][0]["source_records"], before["stations"][0]["source_records"], strict=True):
        if "retained_datums" in old:
            record["retained_datums"] = old["retained_datums"]
        else:
            record.pop("retained_datums", None)
    assert restored == before
    published = copy.deepcopy(release)
    replay = build_otc._publish_observed_datum_stage(release, staged)
    assert replay["release_unchanged"] is True and replay["retained_candidate_count"] == 0
    assert release == published
    print(f"REPLAY {row['station_id']}: added_candidates=0 release_unchanged=True full_payload_unchanged=True")


@pytest.mark.skipif(not os.environ.get("OTC_RETENTION_NATIVE"), reason="requires actual held native/source cache")
def test_actual_noaa_values_survive_native_writer(tmp_path):
    from otc_pipeline.retained_datums import retain_source_datums

    native = Path(os.environ["OTC_RETENTION_NATIVE"])
    cache = Path(os.environ["OTC_RETENTION_NOAA_CACHE"])
    ids = {"8443970", "9447130", "9414290"}
    release = json.loads(native.with_suffix(".meta.json").read_text())
    release["stations"] = []
    with native.open() as source:
        for line in source:
            station = json.loads(line)
            if any(r["source"] == "noaa" and r["source_record_id"] in ids
                   for r in station.get("source_records", [])):
                release["stations"].append(station)
    before = copy.deepcopy(release)
    report = retain_source_datums(release, noaa_cache=cache)
    assert report["changed_stations"] == 3
    assert validate_release(release) == []
    write_release(release, tmp_path, datestamp=release["release"]["datestamp"], app_export=None, congen_run=None)
    output = tmp_path / native.name
    delivered = [json.loads(line) for line in output.read_text().splitlines()]
    for station in delivered:
        for record in station["source_records"]:
            if record["source"] != "noaa" or record["source_record_id"] not in ids:
                continue
            raw = json.loads((cache / "datums" / f"{record['source_record_id']}.json").read_text())
            expected = next(row["value"] for row in raw["datums"] if row["name"] == "GT")
            assert any(c["values"].get("GT") == expected and c["frame"]["kind"] == "difference"
                       for c in record["retained_datums"])
            assert all(c["uncertainty_m"] is None for c in record["retained_datums"])
        old = next(s for s in before["stations"] if s["station_id"] == station["station_id"])
        restored = copy.deepcopy(station)
        for record in restored["source_records"]:
            record.pop("retained_datums", None)
        assert restored == old
    assert retain_source_datums(release, noaa_cache=cache)["added_candidates"] == 0


def test_conflicts_survive_and_computed_source_binding_is_required():
    from otc_pipeline.retained_datums import retain_candidates

    record = {"source": "ticon", "source_version": "4", "source_record_id": "original"}
    cs = {"set_id": "own", "source": "ticon-4", "source_version": "4", "source_record_id": "original"}
    release = {"stations": [{"station_id": "OTC-test", "source_records": [record], "constant_sets": [cs]}]}
    candidate = {"kind": "computed", "method": "harmonic_extremes", "values": {"lat": -1.0},
                 "units": "m", "frame": {"kind": "harmonic_mean", "name": None, "tie_status": "unknown"},
                 "source_record_id": "original", "set_id": "own", "input_binding": {"source": "test"},
                 "window": {"start": "2020-01-01", "end": "2038-12-31"}, "uncertainty_m": None,
                 "reliability": {"status": "limited", "limitations": ["uncertainty_unknown"]}}
    alternative = copy.deepcopy(candidate)
    alternative["values"]["lat"] = -2.0
    row = {"station_id": "OTC-test", "retained_datums": [candidate, alternative]}
    assert retain_candidates(release, [row])["added_candidates"] == 2
    assert [c["values"]["lat"] for c in record["retained_datums"]] == [-1.0, -2.0]
    assert retain_candidates(release, [row])["added_candidates"] == 0
    for field, wrong in (("set_id", "foreign"), ("source_record_id", "different"),
                         ("window", {"start": "2020-02-30", "end": "2020-03-01"})):
        broken = copy.deepcopy(candidate)
        broken[field] = wrong
        with pytest.raises(ValueError):
            retain_candidates(release, [{"station_id": "OTC-test", "retained_datums": [broken]}])
    broken = copy.deepcopy(candidate)
    broken.pop("set_id")
    with pytest.raises(ValueError):
        retain_candidates(release, [{"station_id": "OTC-test", "retained_datums": [broken]}])


def test_duplicate_noaa_source_names_keep_each_value():
    from otc_pipeline.retained_datums import noaa_candidates

    record = {"source_record_id": "123"}
    body = {"self": "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations/123/datums.json",
            "units": "meters", "datums": [{"name": "MLLW", "value": 1.0}, {"name": "MLLW", "value": 2.0}]}
    candidates = noaa_candidates(record, body, {"input": "test"})
    assert [candidate["values"]["MLLW"] for candidate in candidates] == [1.0, 2.0]
    assert all(candidate["frame"]["name"] == "STND" for candidate in candidates)


@pytest.mark.skipif(not os.environ.get("OTC_RETENTION_TRANSPORTS"), reason="requires actual produced candidate transports")
def test_actual_observed_and_computed_transports_survive_writer(tmp_path):
    from otc_pipeline.retained_datums import retain_candidates

    rows = []
    for item in os.environ["OTC_RETENTION_TRANSPORTS"].split(os.pathsep):
        path = Path(item)
        if path.suffix == ".jsonl":
            rows.extend(json.loads(line) for line in path.read_text().splitlines() if line.strip())
        else:
            rows.append(json.loads(path.read_text()))
    native = Path(os.environ["OTC_RETENTION_NATIVE"])
    release = json.loads(native.with_suffix(".meta.json").read_text())
    all_stations = {}
    with native.open() as source:
        for line in source:
            station = json.loads(line)
            all_stations[station["station_id"]] = station
    wanted = {row["station_id"] for row in rows}
    pending = list(wanted)
    while pending:
        sid = pending.pop()
        ref = all_stations[sid].get("subordinate_offsets", {}).get("reference_station_id")
        if ref and ref not in wanted:
            wanted.add(ref)
            pending.append(ref)
    release["stations"] = [all_stations[sid] for sid in sorted(wanted)]
    before = copy.deepcopy(release)
    expected = sum(len(row.get("retained_datums", row.get("retained_observed", []))) for row in rows)
    assert retain_candidates(release, iter(rows))["added_candidates"] == expected
    assert validate_release(release) == []
    write_release(release, tmp_path, datestamp=release["release"]["datestamp"], app_export=None, congen_run=None)
    delivered = {s["station_id"]: s for s in map(json.loads, (tmp_path / native.name).read_text().splitlines())}
    for row in rows:
        station = delivered[row["station_id"]]
        for candidate in row.get("retained_datums", row.get("retained_observed", [])):
            assert any(candidate in record.get("retained_datums", []) for record in station["source_records"])
    for old in before["stations"]:
        restored = copy.deepcopy(delivered[old["station_id"]])
        for record in restored["source_records"]:
            record.pop("retained_datums", None)
        assert restored == old


@pytest.mark.skipif(not os.environ.get("OTC_RETENTION_SOURCE_PROJECTION"), reason="requires actual source projection")
def test_actual_source_projection_preserves_complete_native_profiles():
    from otc_pipeline.retained_datums import append_source_sets

    projection = json.loads(Path(os.environ["OTC_RETENTION_SOURCE_PROJECTION"]).read_text())
    wanted = {station["station_id"] for station in projection["stations"]}
    native = Path(os.environ["OTC_RETENTION_NATIVE"])
    release = json.loads(native.with_suffix(".meta.json").read_text())
    with native.open() as source:
        release["stations"] = [station for station in map(json.loads, source) if station["station_id"] in wanted]
    before = copy.deepcopy(release)
    assert append_source_sets(release, projection)["added_sets"] == 6
    assert validate_release(release) == []
    for station, old in zip(release["stations"], before["stations"]):
        old_sets = {cs["set_id"]: cs for cs in old["constant_sets"]}
        for cs in station["constant_sets"]:
            if cs["set_id"] in old_sets:
                assert cs == old_sets[cs["set_id"]]
        for record, original in zip(station["source_records"], old["source_records"]):
            for key in ("provenance", "original_harmonics", "licence_id"):
                assert record.get(key) == original.get(key)
    assert append_source_sets(release, projection)["added_sets"] == 0
