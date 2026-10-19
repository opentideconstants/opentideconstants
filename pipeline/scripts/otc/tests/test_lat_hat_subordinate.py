"""Bounded real-engine controls; pinned native inputs supplied by the method gate."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


INPUT = os.environ.get("OTC_SUBORDINATE_INPUT")


@pytest.fixture
def payload():
    if not INPUT:
        pytest.skip("set OTC_SUBORDINATE_INPUT to a sealed native tuning input")
    return json.loads(Path(INPUT).read_text())


def test_real_boundary_applies_subordinate_offsets(payload):
    reference = payload["reference_set"]
    offsets = payload["station"]["subordinate_offsets"]
    datum = reference["datum"]
    term = datum["msl_offset_m"] - datum["named"][datum["chart_datum"]]
    request = {"astro_table": payload["astro_table"], "constituents": reference["constituents"],
               "msl_offset_m": term, "subordinate_offsets": offsets,
               "start": "2020-01-01T00:00:00Z", "end": "2020-01-03T00:00:00Z"}
    wrapper = Path(__file__).parents[1] / "otc_pipeline/lat_hat_strict.rb"
    actual = json.loads(subprocess.check_output(["ruby", str(wrapper)], input=json.dumps(request), text=True))
    direct = """require 'json'; require 'time'; require File.join(ENV.fetch('OTC_ENGINE_WORKTREE'), 'lib/harmonics_strict'); r=JSON.parse(STDIN.read); a=Harmonics::Strict::AstroTable.from_h(r.fetch('astro_table')); e=Harmonics::Engine.allocate; v=e.subordinate_extremes_strict(r.fetch('constituents'),r.fetch('msl_offset_m'),r.fetch('subordinate_offsets'),Time.iso8601(r.fetch('start')),Time.iso8601(r.fetch('end')),astro:a); puts JSON.generate([v.select{|x|x['type']=='low'}.map{|x|x['height']}.min,v.select{|x|x['type']=='high'}.map{|x|x['height']}.max])"""
    expected = json.loads(subprocess.check_output(["ruby", "-e", direct], input=json.dumps(request), text=True))
    assert [actual["lat_m"], actual["hat_m"]] == expected


def test_reference_and_chart_origin(payload):
    from otc_pipeline.lat_hat_subordinate import strict_request
    request = strict_request(payload)
    assert request["msl_offset_m"] == payload["reference_set"]["datum"]["msl_offset_m"]
    for mutation, error in (("removed", "reference_unavailable"), ("wrong_source", "reference_unavailable"),
                            ("unpublished", "datum_unavailable")):
        altered = copy.deepcopy(payload)
        if mutation == "removed":
            altered["reference_station"]["status"] = "removed"
        elif mutation == "wrong_source":
            altered["reference_set"]["source"] = "gesla-fit"
        else:
            altered["reference_set"]["datum"]["basis"]["mllw"]["kind"] = "computed"
        with pytest.raises(ValueError, match=error):
            strict_request(altered)


def test_real_production_batch_cli_matches_single_requests(payload, tmp_path):
    from otc_pipeline.lat_hat_subordinate import compute_raw

    payload = copy.deepcopy(payload)
    payload.pop("comparison")
    batch = tmp_path / "batch.json"
    output = tmp_path / "raw.jsonl"
    batch.write_text(json.dumps([payload, payload]))
    subprocess.run([sys.executable, "-m", "otc_pipeline.lat_hat_subordinate", "--batch-input", str(batch),
                    "--out", str(output), "--start", "2020-01-01T00:00:00Z", "--end", "2020-01-03T00:00:00Z"], check=True)
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    expected = compute_raw(payload, start="2020-01-01T00:00:00Z", end="2020-01-03T00:00:00Z")
    assert len(rows) == 2
    for row in rows:
        for key in ("lat_m", "hat_m", "lat_time", "hat_time", "low_count", "high_count", "chunks", "request_sha256"):
            assert row["raw"][key] == expected[key]
        assert "draft_datum" not in row
