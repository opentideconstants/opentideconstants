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
from otc_pipeline.retained_datums import append_source_sets, candidate_errors, noaa_candidates, retain_candidates


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


@pytest.fixture
def metadata_only_source_projection():
    release = json.loads((Path(__file__).parent / "fixtures/example-1.0.json").read_text())
    station = next(s for s in release["stations"] if s["station_id"] == "OTC-EXAMPLE-0007")
    reference = next(s for s in release["stations"] if s["station_id"] == "OTC-EXAMPLE-0006")
    cs = copy.deepcopy(reference["constant_sets"][0])
    cs["set_id"] = station["station_id"] + "/noaa"
    record = {key: cs[key] for key in ("source", "source_version", "source_record_id", "licence_id")}
    record.update({"role": "gauge_station", "disposition": "metadata_only", "detail": "Held source metadata.",
                   "published_set_ids": [], "original_metadata": {"station_type": "S"},
                   "provenance": {"input_sha256": ["a" * 64], "inventory_sha256": "b" * 64}})
    station["source_records"] = [record]
    incoming = copy.deepcopy(station)
    incoming["constant_sets"] = [cs]
    original = incoming["source_records"][0]
    original["disposition"] = "retained"
    original["published_set_ids"] = [cs["set_id"]]
    original["original_metadata"]["published_response_json"] = '{"held":true}'
    return release, {"stations": [incoming]}


def test_published_set_promotes_only_its_metadata_source_and_survives_writer(
        metadata_only_source_projection, tmp_path):
    release, projection = metadata_only_source_projection
    before = copy.deepcopy(release)
    assert validate_release(release) == []
    assert append_source_sets(release, projection) == {"added_sets": 1}
    assert validate_release(release) == []
    station = next(s for s in release["stations"] if s["station_id"] == "OTC-EXAMPLE-0007")
    record = station["source_records"][0]
    assert record["disposition"] == "retained"
    assert record["published_set_ids"] == ["OTC-EXAMPLE-0007/noaa"]
    assert station["recommended_set_id"] is None
    assert station["constant_sets"] == projection["stations"][0]["constant_sets"]
    write_release(release, tmp_path, datestamp=release["release"]["datestamp"],
                  app_export=None, congen_run=None)
    stem = "OTC_" + release["release"]["datestamp"]
    delivered = [json.loads(line) for line in (tmp_path / (stem + ".jsonl")).read_text().splitlines()]
    assert {s["station_id"]: s for s in delivered} == {s["station_id"]: s for s in release["stations"]}
    unchanged = copy.deepcopy(release)
    restored_station = next(s for s in unchanged["stations"] if s["station_id"] == station["station_id"])
    restored_record = restored_station["source_records"][0]
    restored_record["disposition"] = "metadata_only"
    restored_record["published_set_ids"] = []
    restored_record["original_metadata"].pop("published_response_json")
    restored_station["constant_sets"] = []
    assert unchanged == before
    published = copy.deepcopy(release)
    assert append_source_sets(release, projection) == {"added_sets": 0}
    assert release == published


def test_source_without_new_set_keeps_disposition(metadata_only_source_projection):
    release, projection = metadata_only_source_projection
    projection["stations"][0]["constant_sets"] = []
    before = copy.deepcopy(release)
    assert append_source_sets(release, projection) == {"added_sets": 0}
    assert release == before


@pytest.mark.parametrize("disposition", ["retained", "unavailable", "not_fitted", "licence_restricted"])
def test_append_preserves_other_source_dispositions(metadata_only_source_projection, disposition):
    release, projection = metadata_only_source_projection
    station = next(s for s in release["stations"] if s["station_id"] == "OTC-EXAMPLE-0007")
    record = station["source_records"][0]
    record["disposition"] = disposition
    assert append_source_sets(release, projection) == {"added_sets": 1}
    assert record["disposition"] == disposition


@pytest.mark.parametrize("conflict", ["station_id", "source", "source_version", "source_record_id",
                                      "published_set_ids", "provenance", "original_harmonics", "licence_id",
                                      "original_metadata"])
def test_source_promotion_requires_existing_original_binding(metadata_only_source_projection, conflict):
    release, projection = metadata_only_source_projection
    incoming = projection["stations"][0]
    record = incoming["source_records"][0]
    if conflict == "station_id":
        incoming[conflict] = "OTC-foreign"
    elif conflict == "published_set_ids":
        record[conflict] = []
    elif conflict == "original_harmonics":
        record[conflict] = [{"source_name": "foreign"}]
    elif conflict == "original_metadata":
        key = next(iter(record[conflict]))
        record[conflict][key] = "foreign"
    else:
        record[conflict] = "foreign"
    before = copy.deepcopy(release)
    with pytest.raises(ValueError):
        append_source_sets(release, projection)
    assert release == before


@pytest.fixture(params=["ratio", "feet", "uniform_msl", "extrema"])
def subordinate_reference_case(request):
    actual = os.environ.get("OTC_SUBORDINATE_PROOF")
    if actual:
        root = Path(actual)
        native = root / "actual-pilots-and-references.jsonl"
        packets = root / "pilots-candidates.jsonl"
        assert hashlib.sha256(native.read_bytes()).hexdigest() == "dabf812072133781dbb26dbdb3b8f23a0261fda09f5dbb519c4283fbf77a7db5"
        assert hashlib.sha256(packets.read_bytes()).hexdigest() == "6948a7166038ea74454135a3dba401736b35b1071b024afb7689f2cdd885fe2c"
        release = json.loads(native.with_suffix(".meta.json").read_text())
        release["stations"] = [json.loads(line) for line in native.read_text().splitlines()]
        index = ["ratio", "feet", "uniform_msl", "extrema"].index(request.param)
        row = [json.loads(line) for line in packets.read_text().splitlines()][index]
        return release, row
    release = json.loads((Path(__file__).parent / "fixtures/example-1.0.json").read_text())
    station = next(s for s in release["stations"] if s["station_id"] == "OTC-EXAMPLE-0007")
    reference = next(s for s in release["stations"] if s["station_id"] == "OTC-EXAMPLE-0006")
    source_id, reference_id = "8416804", "8418150"
    operation = "F" if request.param == "feet" else "R"
    high, low, high_time, low_time = (0.2, 0.0, 53, 56) if operation == "F" else (1.1, 0.9, 10, 20)
    if request.param == "uniform_msl":
        high, low, high_time, low_time = 1.05, 1.05, 20, 20
    offsets = {"reference_station_id": reference["station_id"], "height_adjusted_type": "A" if operation == "F" else "R",
               "height_offset_high": high * 0.3048 if operation == "F" else high,
               "height_offset_low": low * 0.3048 if operation == "F" else low,
               "time_offset_high_min": high_time, "time_offset_low_min": low_time}
    station["subordinate_offsets"].update(offsets)
    record = {"source": "noaa", "source_version": "20991231", "source_record_id": source_id,
              "role": "gauge_station", "disposition": "metadata_only", "detail": "Held source metadata.",
              "licence_id": "noaa", "published_set_ids": [],
              "provenance": {"input_sha256": ["a" * 64], "inventory_sha256": "b" * 64},
              "original_metadata": {"raw_metadata_rows_json": json.dumps([{"id": source_id, "reference_id": reference_id}])}}
    station["source_records"] = [record]
    endpoint = "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations/"
    published = {"refStationId": reference_id, "heightAdjustedType": operation,
                 "heightOffsetHighTide": high, "heightOffsetLowTide": low,
                 "timeOffsetHighTide": high_time, "timeOffsetLowTide": low_time}
    body = {"self": endpoint + reference_id + "/datums.json", "units": "meters", "epoch": "1983-2001",
            "datums": [{"name": key, "value": value} for key, value in
                       {"MLLW": 1.0, "MHHW": 4.0, "MHW": 3.8, "MLW": 1.2, "MSL": 2.5, "NAVD88": 3.0}.items()],
            "LAT": 0.8, "HAT": 4.5, "LATdate": "20140131", "HATdate": "20331009"}
    reference_binding = {"source": "noaa", "source_record_id": reference_id,
                         "native_station_id": reference["station_id"], "units": "meters",
                         "source_url": body["self"] + "?units=metric", "response_json": json.dumps(body)}
    reference_binding["sha256"] = hashlib.sha256(reference_binding["response_json"].encode()).hexdigest()
    reference_record = copy.deepcopy(record)
    reference_record.update({"source_record_id": reference_id, "disposition": "retained", "original_metadata": {},
                             "provenance": {"input_sha256": [reference_binding["sha256"]], "inventory_sha256": "b" * 64}})
    reference_record["retained_datums"] = noaa_candidates(reference_record, body, reference_binding)
    reference["source_records"] = [reference_record]
    offset_binding = {"source": "noaa", "source_record_id": source_id, "source_version": record["source_version"],
                      "source_url": endpoint + source_id + "/tidepredoffsets.json",
                      "reference_source_record_id": reference_id, "response_json": json.dumps(published)}
    offset_binding["sha256"] = hashlib.sha256(offset_binding["response_json"].encode()).hexdigest()
    values = {"mhhw": 3.06096, "mhw": 2.86096, "mlw": 0.2} if operation == "F" else {"mhhw": 3.3, "mhw": 3.08, "mlw": 0.18}
    if request.param == "uniform_msl":
        values = {"msl": 1.575}
    elif request.param == "extrema":
        values = {"lat": -0.18, "hat": 3.85}
    candidate = {"kind": "computed", "method": "noaa_subordinate_reference_datums_v1", "source_record_id": source_id,
                 "units": "m", "values": values, "uncertainty_m": None,
                 "frame": {"kind": "unknown", "name": "NOAA subordinate MLLW", "tie_status": "unknown"},
                 "input_binding": {"published_offsets": offset_binding, "published_reference": reference_binding},
                 "basis": {"native_offsets": offsets, "reference_epoch": body["epoch"], "reference_zero": "MLLW",
                           "operation": operation, "offset_height_units": "feet" if operation == "F" else "ratio",
                           "output_height_units": "m"},
                 "reliability": {"status": "unverified", "limitations": [
                     "derived_from_published_NOAA_reference_and_offsets", "not_locally_observed_or_surveyed",
                     "accuracy_unverified", "not_selected_for_prediction"]}}
    if request.param == "extrema":
        candidate["basis"]["extrema_period"] = {"lat": {"date": body["LATdate"]}, "hat": {"date": body["HATdate"]}}
    return release, {"station_id": station["station_id"], "source": "noaa", "source_version": record["source_version"],
                     "retained_datums": [candidate]}


def test_subordinate_reference_datums_survive_native_writer(subordinate_reference_case, tmp_path):
    release, row = subordinate_reference_case
    station = next(s for s in release["stations"] if s["station_id"] == row["station_id"])
    for candidate in row["retained_datums"]:
        record = next(r for r in station["source_records"] if r["source_record_id"] == candidate["source_record_id"])
        assert (row["source"], row["source_version"]) == (record["source"], record["source_version"])
        assert not station["constant_sets"] and "set_id" not in candidate and "window" not in candidate
        reference = next(s for s in release["stations"] if s["station_id"] == station["subordinate_offsets"]["reference_station_id"])
        errors = candidate_errors(candidate, record, {}, station=station, reference_station=reference)
        print(f"EXACT OWN SOURCE {station['station_id']} {record['source_record_id']}: {errors}")
        assert errors == []
    before = copy.deepcopy(release)
    assert retain_candidates(release, [row])["added_candidates"] == len(row["retained_datums"])
    assert validate_release(release) == []
    write_release(release, tmp_path, datestamp=release["release"]["datestamp"], app_export=None, congen_run=None)
    delivered = [json.loads(line) for line in (tmp_path / ("OTC_" + release["release"]["datestamp"] + ".jsonl")).read_text().splitlines()]
    assert {s["station_id"]: s for s in delivered} == {s["station_id"]: s for s in release["stations"]}
    for old, current in zip(before["stations"], release["stations"], strict=True):
        restored = copy.deepcopy(current)
        for prior, record in zip(old.get("source_records", []), restored.get("source_records", []), strict=True):
            if current["station_id"] == station["station_id"]:
                if "retained_datums" in prior:
                    record["retained_datums"] = prior["retained_datums"]
                else:
                    record.pop("retained_datums", None)
        assert restored == old


def _subordinate_context(release, row):
    station = next(s for s in release["stations"] if s["station_id"] == row["station_id"])
    candidate = row["retained_datums"][0]
    record = next(r for r in station["source_records"] if r["source_record_id"] == candidate["source_record_id"])
    reference = next(s for s in release["stations"] if s["station_id"] == station["subordinate_offsets"]["reference_station_id"])
    return station, candidate, record, reference


def _rewrite_response(candidate, member, field, value):
    binding = candidate["input_binding"][member]
    body = json.loads(binding["response_json"])
    body[field] = value
    binding["response_json"] = json.dumps(body)
    binding["sha256"] = hashlib.sha256(binding["response_json"].encode()).hexdigest()


@pytest.mark.parametrize("defect", [
    "missing_proof", "wrong_hash", "malformed_body", "own_version", "offset_reference", "offset_value",
    "reference_units", "reference_self", "native_reference", "metric_url", "own_url", "own_metadata",
    "unbound_reference", "native_reference_value", "derived_value", "epoch", "copied_values", "extrema_date",
    "published_kind", "uncertainty", "fake_set", "fake_window", "mixed_frame", "HWI", "LWI", "NAVD88"])
def test_subordinate_reference_rejects_unbound_or_invented_claims(subordinate_reference_case, defect):
    release, row = subordinate_reference_case
    station, candidate, record, reference = _subordinate_context(release, row)
    own = candidate["input_binding"]["published_offsets"]
    ref = candidate["input_binding"]["published_reference"]
    if defect == "missing_proof":
        candidate["input_binding"].pop("published_offsets")
    elif defect == "wrong_hash":
        ref["sha256"] = "0" * 64
    elif defect == "malformed_body":
        ref["response_json"] = "["
        ref["sha256"] = hashlib.sha256(b"[").hexdigest()
    elif defect == "own_version":
        own["source_version"] = "foreign"
    elif defect == "offset_reference":
        _rewrite_response(candidate, "published_offsets", "refStationId", "9999999")
    elif defect == "offset_value":
        _rewrite_response(candidate, "published_offsets", "heightOffsetHighTide", 99.0)
    elif defect == "reference_units":
        _rewrite_response(candidate, "published_reference", "units", "feet")
    elif defect == "reference_self":
        _rewrite_response(candidate, "published_reference", "self", "https://example.invalid/datums.json")
    elif defect == "native_reference":
        ref["native_station_id"] = "OTC-foreign"
    elif defect == "metric_url":
        ref["source_url"] = ref["source_url"].split("?", 1)[0]
    elif defect == "own_url":
        own["source_url"] = ref["source_url"]
    elif defect == "own_metadata":
        record["original_metadata"]["raw_metadata_rows_json"] = "[]"
    elif defect == "unbound_reference":
        _rewrite_response(candidate, "published_reference", "epoch", "unbound")
        candidate["basis"]["reference_epoch"] = "unbound"
    elif defect == "native_reference_value":
        original = next(r for r in reference["source_records"] if r["source_record_id"] == ref["source_record_id"])
        published = next(c for c in original["retained_datums"] if c["kind"] == "published" and "MHHW" in c["values"])
        published["values"]["MHHW"] += 1
    elif defect == "derived_value":
        key = next(iter(candidate["values"]))
        candidate["values"][key] += 1
    elif defect == "epoch":
        candidate["basis"]["reference_epoch"] = "invented"
    elif defect == "copied_values":
        candidate["basis"]["published_values"] = {"MLLW": 123.0}
    elif defect == "extrema_date":
        candidate["basis"]["extrema_period"] = {"lat": {"date": "invented"}}
    elif defect == "published_kind":
        candidate["kind"] = "published"
    elif defect == "uncertainty":
        candidate["uncertainty_m"] = 0.0
    elif defect == "fake_set":
        candidate["set_id"] = "foreign"
    elif defect == "fake_window":
        candidate["window"] = {"start": "2020-01-01", "end": "2038-12-31"}
    elif defect == "mixed_frame":
        candidate["values"].update({"mhhw": 3.3, "gt": 3.3})
    else:
        candidate["values"][defect.lower()] = 1.0
    assert candidate_errors(candidate, record, {}, station=station, reference_station=reference)
    before = copy.deepcopy(release)
    with pytest.raises(ValueError):
        retain_candidates(release, [row])
    assert release == before


@pytest.mark.parametrize("field", ["reference_station_id", "height_adjusted_type", "height_offset_high",
                                    "height_offset_low", "time_offset_high_min", "time_offset_low_min"])
def test_subordinate_reference_checks_each_native_offset(subordinate_reference_case, field):
    release, row = subordinate_reference_case
    station, candidate, record, reference = _subordinate_context(release, row)
    candidate["basis"]["native_offsets"][field] = "foreign" if field in (
        "reference_station_id", "height_adjusted_type") else 99.0
    assert candidate_errors(candidate, record, {}, station=station, reference_station=reference)


def test_subordinate_reference_requires_context_and_keeps_harmonic_guards(subordinate_reference_case):
    release, row = subordinate_reference_case
    station, candidate, record, reference = _subordinate_context(release, row)
    assert candidate_errors(candidate, record, {})
    assert candidate_errors(candidate, record, {}, station=station)
    candidate["method"] = "harmonic_extremes"
    assert candidate_errors(candidate, record, {}, station=station, reference_station=reference) == [
        "computed retained datum requires its actual set_id",
        "observed/computed retained datum requires its actual calculation window"]


def test_subordinate_reference_never_derives_nonuniform_mean_level(subordinate_reference_case):
    release, row = subordinate_reference_case
    station, candidate, record, reference = _subordinate_context(release, row)
    own = candidate["input_binding"]["published_offsets"]
    body = json.loads(own["response_json"])
    body["timeOffsetLowTide"] = body["timeOffsetHighTide"] + 1
    own["response_json"] = json.dumps(body)
    own["sha256"] = hashlib.sha256(own["response_json"].encode()).hexdigest()
    station["subordinate_offsets"]["time_offset_low_min"] = body["timeOffsetLowTide"]
    candidate["basis"]["native_offsets"]["time_offset_low_min"] = body["timeOffsetLowTide"]
    candidate["values"] = {"msl": 1.5}
    assert candidate_errors(candidate, record, {}, station=station, reference_station=reference)


def test_subordinate_reference_native_validation_rechecks_published_binding(subordinate_reference_case):
    release, row = subordinate_reference_case
    assert retain_candidates(release, [row])["added_candidates"] == len(row["retained_datums"])
    station, candidate, record, reference = _subordinate_context(release, row)
    retained = next(c for c in record["retained_datums"] if c["method"] == candidate["method"])
    _rewrite_response(retained, "published_reference", "epoch", "invented")
    retained["basis"]["reference_epoch"] = "invented"
    assert any("not bound to retained native source values" in error for error in validate_release(release))


def test_subordinate_reference_accepts_exact_alphanumeric_noaa_source(subordinate_reference_case):
    release, row = subordinate_reference_case
    station, candidate, record, reference = _subordinate_context(release, row)
    old_id = record["source_record_id"]
    record["source_record_id"] = "TEC1603"
    record["original_metadata"]["raw_metadata_rows_json"] = json.dumps([
        {"id": "TEC1603", "reference_id": candidate["input_binding"]["published_reference"]["source_record_id"]}])
    for item in row["retained_datums"]:
        item["source_record_id"] = "TEC1603"
        own = item["input_binding"]["published_offsets"]
        own["source_record_id"] = "TEC1603"
        own["source_url"] = own["source_url"].replace("/" + old_id + "/", "/TEC1603/")
        assert candidate_errors(item, record, {}, station=station, reference_station=reference) == []
    assert retain_candidates(release, [row])["added_candidates"] == len(row["retained_datums"])
    assert validate_release(release) == []


def test_subordinate_reference_requires_positive_published_ratios(subordinate_reference_case):
    release, row = subordinate_reference_case
    station, candidate, record, reference = _subordinate_context(release, row)
    _rewrite_response(candidate, "published_offsets", "heightAdjustedType", "R")
    _rewrite_response(candidate, "published_offsets", "heightOffsetHighTide", 0.0)
    candidate["basis"].update({"operation": "R", "offset_height_units": "ratio"})
    for offsets in (station["subordinate_offsets"], candidate["basis"]["native_offsets"]):
        offsets.update({"height_adjusted_type": "R", "height_offset_high": 0.0})
    assert any("positive ratios" in error for error in candidate_errors(
        candidate, record, {}, station=station, reference_station=reference))


def test_subordinate_reference_requires_finite_published_mllw(subordinate_reference_case):
    release, row = subordinate_reference_case
    station, candidate, record, reference = _subordinate_context(release, row)
    binding = candidate["input_binding"]["published_reference"]
    body = json.loads(binding["response_json"])
    for datum in body["datums"]:
        if datum["name"] == "MLLW":
            datum["value"] = None
    binding["response_json"] = json.dumps(body)
    binding["sha256"] = hashlib.sha256(binding["response_json"].encode()).hexdigest()
    original = next(r for r in reference["source_records"] if r["source_record_id"] == binding["source_record_id"])
    original["retained_datums"] = noaa_candidates(original, body, binding)
    assert any("finite MLLW baseline" in error for error in candidate_errors(
        candidate, record, {}, station=station, reference_station=reference))


def test_actual_all_subordinate_reference_bindings():
    evidence = os.environ.get("OTC_SUBORDINATE_ALL_PROOF")
    if not evidence:
        pytest.skip("requires the frozen complete subordinate source transport")
    root = Path(evidence)
    native = root / "actual-targets-and-references.jsonl"
    packets = root / "all-candidates.jsonl"
    for path, expected in (
            (native, "2d743d0f3e5b110cfd2a6264019bc13fe61754d98ecfc8c4b8d7f03bd9bd22e0"),
            (packets, "e5b88432a16855982d54e3ff414f635ef9de2335a04200476cfa9a0bbd852a25")):
        with path.open("rb") as source:
            assert hashlib.file_digest(source, "sha256").hexdigest() == expected
    with native.open() as source:
        stations = {s["station_id"]: s for s in map(json.loads, source)}
    failures = []
    count = 0
    for row in map(json.loads, packets.open()):
        station = stations[row["station_id"]]
        reference = stations[station["subordinate_offsets"]["reference_station_id"]]
        for candidate in row["retained_datums"]:
            records = [r for r in station["source_records"] if
                       (r["source"], r["source_version"], r["source_record_id"]) ==
                       (row["source"], row["source_version"], candidate["source_record_id"])]
            assert len(records) == 1
            errors = candidate_errors(candidate, records[0], {}, station=station, reference_station=reference)
            if errors:
                failure = (station["station_id"], candidate["source_record_id"], sorted(candidate["values"]), errors)
                failures.append(failure)
                if len(failures) <= 5:
                    print(f"ACTUAL FULL SOURCE REJECTION: {failure}")
            count += 1
    print(f"ACTUAL FULL SOURCE AUDIT: candidates={count} rejected={len(failures)}")
    assert failures == []


@pytest.fixture
def noaa_time_proxy_case(subordinate_reference_case, request):
    actual = os.environ.get("OTC_NOAA_TIME_PROXY_PROOF")
    if actual:
        root = Path(actual)
        native = root / "pilots-native.jsonl"
        packets = root / "pilots-candidates.jsonl"
        for path, expected in (
                (native, "14a214b64e9fe339f766621ec59e0d1f9cc225fd5c999598d866aac0f1fe6a4b"),
                (packets, "43aa2f94b0de096f57fd79f1d2473e4c357c6afc1d281657c65bc4052c0d938a")):
            with path.open("rb") as source:
                assert hashlib.file_digest(source, "sha256").hexdigest() == expected
        release = json.loads(native.with_suffix(".meta.json").read_text())
        with native.open() as source:
            release["stations"] = list(map(json.loads, source))
        index = {"ratio": 0, "feet": 1, "uniform_msl": 2, "extrema": 2}[
            request.node.callspec.params["subordinate_reference_case"]]
        with packets.open() as source:
            return release, list(map(json.loads, source))[index]
    release, row = subordinate_reference_case
    station, old, record, reference = _subordinate_context(release, row)
    own, ref = old["input_binding"]["published_offsets"], old["input_binding"]["published_reference"]
    offsets = json.loads(own["response_json"])
    offsets["timeOffsetLowTide"] = offsets["timeOffsetHighTide"] + 2
    own["response_json"] = json.dumps(offsets)
    own["sha256"] = hashlib.sha256(own["response_json"].encode()).hexdigest()
    station["subordinate_offsets"]["time_offset_low_min"] = offsets["timeOffsetLowTide"]
    old["basis"]["native_offsets"]["time_offset_low_min"] = offsets["timeOffsetLowTide"]
    body = json.loads(ref["response_json"])
    body["datums"].extend([
        {"name": "HWI", "value": 2.25, "description": "Greenwich High Water Interval (in hours)"},
        {"name": "LWI", "value": 8.5, "description": "Greenwich Low Water Interval (in hours)"}])
    ref["response_json"] = json.dumps(body)
    ref["sha256"] = hashlib.sha256(ref["response_json"].encode()).hexdigest()
    original = next(r for r in reference["source_records"] if r["source_record_id"] == ref["source_record_id"])
    original["retained_datums"] = noaa_candidates(original, body, ref)
    base = {key: copy.deepcopy(old[key]) for key in ("kind", "source_record_id", "input_binding", "uncertainty_m")}
    time = {**copy.deepcopy(base), "method": "noaa_subordinate_reference_time_offsets_v1", "units": "h",
            "values": {"hwi": 2.25 + offsets["timeOffsetHighTide"] / 60,
                       "lwi": 8.5 + offsets["timeOffsetLowTide"] / 60},
            "frame": {"kind": "elapsed_time", "name": "Greenwich lunar transit", "tie_status": "not_applicable"},
            "basis": {key: copy.deepcopy(old["basis"][key]) for key in
                      ("native_offsets", "reference_epoch", "operation", "offset_height_units")},
            "reliability": {"status": "unverified", "limitations": [
                "derived NOAA subordinate time offsets from REF",
                "carried published Greenwich reference; not a newly evaluated own local lunar-transit/event average",
                "source reference hours and published minute-offset precision preserved",
                "not_selected_for_prediction", "accuracy_unverified"]}}
    time["basis"].update({"derivation_operation": "published_interval_plus_time_offset",
                          "reference_interval_basis": "published_greenwich", "reference_interval_units": "h",
                          "published_time_offset_units": "min", "output_interval_units": "h",
                          "time_offset_precision": "Sourcepublishedminutevalues; nofalsegreaterclockaccuracy",
                          "reference_meridian_policy": "CarryactualpublishedGreenwichreference; noextraownlongitude/localtransitcorrection",
                          "cycle_policy": "Noadditionalmoduloorcyclecorrectionintroducedbeyondownerapprovedstraightaddition"})
    label = "approximation: MTL used as MSL proxy, subordinate-derived"
    values = {r["name"]: r["value"] for r in body["datums"]}
    high = values["MHW"] - values["MLLW"]
    low = values["MLW"] - values["MLLW"]
    if offsets["heightAdjustedType"] == "R":
        high *= offsets["heightOffsetHighTide"]
        low *= offsets["heightOffsetLowTide"]
    else:
        high += offsets["heightOffsetHighTide"] * 0.3048
        low += offsets["heightOffsetLowTide"] * 0.3048
    proxy = {**copy.deepcopy(base), "method": "noaa_subordinate_mtl_msl_proxy_v1", "units": "m",
             "values": {"msl": (high + low) / 2},
             "frame": {"kind": "unknown", "name": "NOAA subordinate MLLW", "tie_status": "unknown"},
             "basis": {key: copy.deepcopy(old["basis"][key]) for key in (
                 "native_offsets", "reference_epoch", "operation", "offset_height_units", "reference_zero", "output_height_units")},
             "reliability": {"status": "low", "limitations": [label, "not_selected_for_prediction",
                 "accuracy_unverified", "not_locally_observed_or_surveyed"]}}
    proxy["basis"].update({"derivation_operation": "derived_mtl_as_msl_proxy", "approximation": label,
                           "proxy_of": "derived_mtl", "reference_statistic_origin": "published", "never_default": True})
    row["retained_datums"] = [time, proxy]
    return release, row


def test_noaa_time_proxy_native_writer(noaa_time_proxy_case, tmp_path):
    release, row = noaa_time_proxy_case
    station, candidate, record, reference = _subordinate_context(release, row)
    assert (row["source"], row["source_version"]) == (record["source"], record["source_version"])
    all_errors = []
    for candidate in row["retained_datums"]:
        assert "set_id" not in candidate and "window" not in candidate
        errors = candidate_errors(candidate, record, {}, station=station, reference_station=reference)
        print(f"EXACT NEW SOURCE {record['source_record_id']} {candidate['method']}: {errors}")
        all_errors.extend(errors)
    assert all_errors == []
    before = copy.deepcopy(release)
    assert retain_candidates(release, [row])["added_candidates"] == 2
    assert validate_release(release) == []
    write_release(release, tmp_path, datestamp=release["release"]["datestamp"], app_export=None, congen_run=None)
    published = copy.deepcopy(release)
    assert retain_candidates(release, [row])["added_candidates"] == 0 and release == published
    for prior, current in zip(before["stations"], release["stations"], strict=True):
        restored = copy.deepcopy(current)
        if current["station_id"] == row["station_id"]:
            for old_record, new_record in zip(prior["source_records"], restored["source_records"], strict=True):
                if "retained_datums" in old_record:
                    new_record["retained_datums"] = old_record["retained_datums"]
                else:
                    new_record.pop("retained_datums", None)
        assert restored == prior


@pytest.mark.parametrize("defect", ["hash", "own_version", "reference_url", "native_offset", "epoch", "unknown_method"])
def test_noaa_time_proxy_require_exact_source_proof(noaa_time_proxy_case, defect):
    release, row = noaa_time_proxy_case
    station, first, record, reference = _subordinate_context(release, row)
    for candidate in row["retained_datums"]:
        if defect == "hash":
            candidate["input_binding"]["published_reference"]["sha256"] = "0" * 64
        elif defect == "own_version":
            candidate["input_binding"]["published_offsets"]["source_version"] = "foreign"
        elif defect == "reference_url":
            candidate["input_binding"]["published_reference"]["source_url"] = "https://example.invalid/"
        elif defect == "native_offset":
            candidate["basis"]["native_offsets"]["time_offset_low_min"] += 1
        elif defect == "epoch":
            candidate["basis"]["reference_epoch"] = "invented"
        else:
            candidate["method"] += "_unapproved"
        assert candidate_errors(candidate, record, {}, station=station, reference_station=reference)
    before = copy.deepcopy(release)
    with pytest.raises(ValueError):
        retain_candidates(release, [row])
    assert release == before


@pytest.mark.parametrize("defect", ["value", "units", "frame", "key", "meridian", "source_hours", "native_hours", "precision"])
def test_noaa_time_intervals_require_greenwich_hours_and_minute_conversion(noaa_time_proxy_case, defect):
    release, row = noaa_time_proxy_case
    station, candidate, record, reference = _subordinate_context(release, row)
    if defect == "value":
        candidate["values"]["hwi"] += 1
    elif defect == "units":
        candidate["units"] = "m"
    elif defect == "frame":
        candidate["frame"]["name"] = "local lunar transit"
    elif defect == "key":
        candidate["values"]["msl"] = 0.0
    elif defect == "precision":
        candidate["basis"].pop("time_offset_precision")
    elif defect == "native_hours":
        binding = candidate["input_binding"]["published_reference"]
        original = next(r for r in reference["source_records"] if r["source_record_id"] == binding["source_record_id"])
        published = next(c for c in original["retained_datums"] if c["units"] == "h")
        published["values"]["HWI"] += 1
    else:
        binding = candidate["input_binding"]["published_reference"]
        body = json.loads(binding["response_json"])
        for item in body["datums"]:
            if item["name"] == "HWI":
                item["description"] = "Local High Water Interval (in hours)" if defect == "meridian" else "Greenwich High Water Interval (in minutes)"
        binding["response_json"] = json.dumps(body)
        binding["sha256"] = hashlib.sha256(binding["response_json"].encode()).hexdigest()
        original = next(r for r in reference["source_records"] if r["source_record_id"] == binding["source_record_id"])
        original["retained_datums"] = noaa_candidates(original, body, binding)
    assert candidate_errors(candidate, record, {}, station=station, reference_station=reference)


@pytest.mark.parametrize("defect", ["value", "status", "label", "limitation", "default", "key", "origin", "uniform"])
def test_noaa_msl_proxy_requires_low_label_and_mapped_levels(noaa_time_proxy_case, defect):
    release, row = noaa_time_proxy_case
    station, first, record, reference = _subordinate_context(release, row)
    candidate = row["retained_datums"][1]
    if defect == "value":
        candidate["values"]["msl"] += 1
    elif defect == "status":
        candidate["reliability"]["status"] = "unverified"
    elif defect == "label":
        candidate["basis"]["approximation"] = "official MSL"
    elif defect == "limitation":
        candidate["reliability"]["limitations"] = ["accuracy_unverified"]
    elif defect == "default":
        candidate["basis"]["never_default"] = False
    elif defect == "key":
        candidate["values"]["mtl"] = candidate["values"]["msl"]
    elif defect == "origin":
        candidate["basis"]["reference_statistic_origin"] = "computed_harmonic_reference"
    else:
        own = candidate["input_binding"]["published_offsets"]
        body = json.loads(own["response_json"])
        body["heightOffsetLowTide"] = body["heightOffsetHighTide"]
        body["timeOffsetLowTide"] = body["timeOffsetHighTide"]
        own["response_json"] = json.dumps(body)
        own["sha256"] = hashlib.sha256(own["response_json"].encode()).hexdigest()
        for offsets in (station["subordinate_offsets"], candidate["basis"]["native_offsets"]):
            offsets["height_offset_low"] = offsets["height_offset_high"]
            offsets["time_offset_low_min"] = offsets["time_offset_high_min"]
    assert candidate_errors(candidate, record, {}, station=station, reference_station=reference)


def test_noaa_time_values_do_not_use_height_adjustments(noaa_time_proxy_case):
    release, row = noaa_time_proxy_case
    station, candidate, record, reference = _subordinate_context(release, row)
    own = candidate["input_binding"]["published_offsets"]
    body = json.loads(own["response_json"])
    body["heightOffsetHighTide"] += 1
    own["response_json"] = json.dumps(body)
    own["sha256"] = hashlib.sha256(own["response_json"].encode()).hexdigest()
    normalized = body["heightOffsetHighTide"] * (0.3048 if body["heightAdjustedType"] == "F" else 1)
    station["subordinate_offsets"]["height_offset_high"] = normalized
    candidate["basis"]["native_offsets"]["height_offset_high"] = normalized
    assert candidate_errors(candidate, record, {}, station=station, reference_station=reference) == []


@pytest.fixture
def noaa_computed_proxy_case(noaa_time_proxy_case):
    release, row = noaa_time_proxy_case
    station, first, record, reference = _subordinate_context(release, row)
    candidate = copy.deepcopy(row["retained_datums"][1])
    binding = candidate["input_binding"]["published_reference"]
    body = json.loads(binding["response_json"])
    body["datums"] = [value for value in body["datums"] if value["name"] not in ("MHW", "MLW")]
    binding["response_json"] = json.dumps(body)
    binding["sha256"] = hashlib.sha256(binding["response_json"].encode()).hexdigest()
    original = next(r for r in reference["source_records"] if r["source_record_id"] == binding["source_record_id"])
    original["retained_datums"] = noaa_candidates(original, body, binding)
    model = reference["constant_sets"][0]
    model["source_record_id"] = original["source_record_id"]
    model["source_version"] = original["source_version"]
    model_json = json.dumps(model, sort_keys=True, separators=(",", ":"))
    model_sha = hashlib.sha256(model_json.encode()).hexdigest()
    window = {"start": "2020-01-01", "end": "2038-12-31"}
    common = {"kind": "computed", "units": "m", "source_record_id": original["source_record_id"],
              "set_id": model["set_id"], "window": window, "uncertainty_m": None,
              "frame": {"kind": "harmonic_mean", "name": None, "tie_status": "unknown"},
              "input_binding": {"constant_set_sha256": model_sha, "set_id": model["set_id"],
                  "convention_id": model["convention_id"], "source_provenance": copy.deepcopy(model["provenance"]),
                  "request_sha256": "c" * 64, "results_sha256": "d" * 64},
              "reliability": {"status": "unverified", "limitations": ["accuracy_unverified"]}}
    events = {**copy.deepcopy(common), "method": "sdk_harmonic_extremes_event_means_v1", "values": {"mhw": 1.8, "mlw": -0.3}}
    hourly = {**copy.deepcopy(common), "method": "sdk_harmonic_hourly_mean_v1", "values": {"msl": 0.7}}
    hourly["input_binding"]["hourly_heights"] = {"window": copy.deepcopy(window),
        "input_binding": {"request_sha256": "b" * 64, "results_sha256": "e" * 64}}
    original["retained_datums"].extend([events, hourly])
    transport = {"station_id": reference["station_id"], "source": original["source"],
                 "source_version": original["source_version"], "source_record_id": original["source_record_id"],
                 "retained_datums": [events, hourly]}
    transport_json = json.dumps(transport)
    candidate["input_binding"]["computed_reference"] = {"source_candidate_json": transport_json,
        "sha256": hashlib.sha256(transport_json.encode()).hexdigest(), "reference_model_json": model_json,
        "reference_model_sha256": model_sha, "events_response_sha256": "d" * 64, "hourly_response_sha256": "e" * 64}
    candidate["basis"]["reference_statistic_origin"] = "computed_harmonic_reference"
    values = {item["name"]: item["value"] for item in body["datums"]}
    shift = values["MSL"] - values["MLLW"]
    high, low = 1.8 - 0.7 + shift, -0.3 - 0.7 + shift
    own = json.loads(candidate["input_binding"]["published_offsets"]["response_json"])
    if own["heightAdjustedType"] == "R":
        high *= own["heightOffsetHighTide"]
        low *= own["heightOffsetLowTide"]
    else:
        high += own["heightOffsetHighTide"] * 0.3048
        low += own["heightOffsetLowTide"] * 0.3048
    candidate["values"] = {"msl": (high + low) / 2}
    row["retained_datums"] = [candidate]
    return release, row


def test_noaa_computed_reference_proxy_uses_actual_hourly_mean(noaa_computed_proxy_case, tmp_path):
    release, row = noaa_computed_proxy_case
    station, candidate, record, reference = _subordinate_context(release, row)
    assert candidate_errors(candidate, record, {}, station=station, reference_station=reference) == []
    assert retain_candidates(release, [row])["added_candidates"] == 1
    assert validate_release(release) == []
    write_release(release, tmp_path, datestamp=release["release"]["datestamp"], app_export=None, congen_run=None)
    assert candidate["reliability"]["status"] == "low" and candidate["basis"]["never_default"] is True
    assert "set_id" not in candidate and "window" not in candidate


@pytest.mark.parametrize("defect", ["candidate_hash", "model_hash", "missing_member", "coefficients",
                                    "window", "event_response", "hourly_response", "hourly_request",
                                    "omit_mean", "published_origin"])
def test_noaa_computed_reference_proxy_requires_native_model_and_archive_bindings(noaa_computed_proxy_case, defect):
    release, row = noaa_computed_proxy_case
    station, candidate, record, reference = _subordinate_context(release, row)
    proof = candidate["input_binding"]["computed_reference"]
    original = next(r for r in reference["source_records"] if r["source_record_id"] ==
                    candidate["input_binding"]["published_reference"]["source_record_id"])
    if defect == "candidate_hash":
        proof["sha256"] = "0" * 64
    elif defect == "model_hash":
        proof["reference_model_sha256"] = "0" * 64
    elif defect == "missing_member":
        original["retained_datums"] = [c for c in original["retained_datums"] if c.get("kind") != "computed"]
    elif defect == "coefficients":
        reference["constant_sets"][0]["constituents"][0]["amplitude_m"] += 0.1
    elif defect in ("window", "hourly_request"):
        transport = json.loads(proof["source_candidate_json"])
        hourly = transport["retained_datums"][1]
        native_hourly = next(c for c in original["retained_datums"] if c.get("method") == "sdk_harmonic_hourly_mean_v1")
        for source in (hourly, native_hourly):
            if defect == "window":
                source["window"]["end"] = "2037-12-31"
                source["input_binding"]["hourly_heights"]["window"]["end"] = "2037-12-31"
            else:
                source["input_binding"]["hourly_heights"]["input_binding"]["request_sha256"] = "unbound"
        proof["source_candidate_json"] = json.dumps(transport)
        proof["sha256"] = hashlib.sha256(proof["source_candidate_json"].encode()).hexdigest()
    elif defect == "event_response":
        proof["events_response_sha256"] = "0" * 64
    elif defect == "hourly_response":
        proof["hourly_response_sha256"] = "0" * 64
    elif defect == "omit_mean":
        candidate["values"]["msl"] += 0.7
    else:
        candidate["basis"]["reference_statistic_origin"] = "published"
    assert candidate_errors(candidate, record, {}, station=station, reference_station=reference)
    before = copy.deepcopy(release)
    with pytest.raises(ValueError):
        retain_candidates(release, [row])
    assert release == before
