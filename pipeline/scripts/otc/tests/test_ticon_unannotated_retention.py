"""TICON's declared phase axis permits labelled sets without inventing a gauge-clock audit."""
import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

from gesla.tcd_table import TcdTable
from otc_pipeline import gesla_release as gr
from otc_pipeline import ticon_sets as ts
from otc_pipeline.release_writer import pinned_schema


RECORDS = (
    "aukfield_platform-aukfpfm-nld-rws",
    "l9_platform-l9pfm-nld-rws",
    "north_cormorant-northcmrt-nld-rws",
    "platform_a12-a12-nld-rws",
    "platform_d15_a-d15-nld-rws",
    "platform_f16_a-f16-nld-rws",
)


@pytest.fixture(scope="module")
def actual_retention():
    source = os.environ.get("OTC_DUTCH_RETENTION_INPUTS")
    if not source:
        pytest.skip("requires actual pinned OTC_DUTCH_RETENTION_INPUTS")
    proof = json.loads(Path(source).read_text())
    records = ts.load_ticon(Path(proof["csv_path"]), sha256=proof["csv_sha256"])
    assert set(records) == set(RECORDS)
    release = json.loads(Path(proof["meta_path"]).read_text())
    table = TcdTable.load(Path(os.environ["OTC_DUTCH_TCD_TABLE"]))
    release["constituents"] = [row for row in release["constituents"] if row["name"] in table]
    release["stations"] = [copy.deepcopy(cell["station"]) for cell in proof["cells"]]
    originals = {cell["record"]["source_record_id"]: cell["record"] for cell in proof["cells"]}
    for station in release["stations"]:
        # retain_ticon_sources runs on the GESLA component before model merge.
        # These six identities have no GESLA or TICON predictive set at that stage.
        station["constant_sets"] = [cs for cs in station["constant_sets"] if cs["source"] != "fes2022"]
        if not any(cs["set_id"] == station["recommended_set_id"] for cs in station["constant_sets"]):
            station["recommended_set_id"] = None
        station["source_records"] = [record for record in station.get("source_records", [])
                                     if record["source"] != "ticon"]
    inventory = SimpleNamespace(
        document={"identities": [{"source": "ticon", "version": "4", "upstream_record_id": rid,
                                   "input_sha256": originals[rid]["provenance"]["input_sha256"]}
                                  for rid in RECORDS]},
        provenance={"sha256": next(iter(originals.values()))["provenance"]["inventory_sha256"]},
    )
    gr.retain_ticon_sources(
        release, gr.BuildReport(), ticon=ts.TiconCarry(records, {}, "actual missing annotation proof"),
        table=table,
        station_ids={cell["record"]["source_record_id"]: cell["station"]["station_id"] for cell in proof["cells"]},
        metadata={}, licences=[], licence_ids={}, inventory=inventory, docs=[], epoch_hours={}, sibling_flags={},
    )
    output = {record["source_record_id"]: record for station in release["stations"]
              for record in station.get("source_records", []) if record["source"] == "ticon"}
    return originals, output, release, records


@pytest.mark.parametrize("rid", RECORDS)
def test_actual_unannotated_platform_preserves_values_and_labels_unknown_clock(actual_retention, rid):
    originals, output, release, records = actual_retention
    record = output[rid]
    assert record["original_harmonics"] == originals[rid]["original_harmonics"]
    assert record["provenance"] == originals[rid]["provenance"]
    assert len(record["published_set_ids"]) == 1, rid
    cs = next(cs for station in release["stations"] for cs in station["constant_sets"]
              if cs["set_id"] == record["published_set_ids"][0])
    assert cs["time_base"]["code"] == "unverified"
    assert cs["time_base"]["declared"] == cs["time_base"]["published"] == "utc_instant"
    assert cs["fallback"]["reason"] == "time_base_unresolved"
    assert "independently audited" in cs["time_base"]["reason"]
    assert "modified" not in cs["provenance"]
    assert record["original_metadata"]["time_base_status"] == "utc_by_source_convention"
    assert "not independently audited" in record["original_metadata"]["time_base_limitation"]
    schema = pinned_schema()
    for name, value in (("source_record", record), ("constant_set", cs)):
        validator = Draft202012Validator({"$ref": f"#/$defs/{name}", "$defs": schema["$defs"]})
        assert [error.message for error in validator.iter_errors(value)] == []
    original_constituents = {ts._otc_name(name): (amp / 100.0, phase % 360.0)
                             for name, (amp, phase) in records[rid].constituents.items()}
    for constituent in cs["constituents"]:
        assert (constituent["amplitude_m"], constituent["phase_deg"]) == original_constituents[constituent["name"]]
    print(f"{rid}: 50 original rows preserved; unverified source-phase-axis set; original clock not independently audited")
