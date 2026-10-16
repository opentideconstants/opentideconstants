"""The first-release T1 gate (slot DT1): per-station verdicts from the harness, read by the build."""
import json

import pytest

from otc_pipeline import datum_t1
from otc_pipeline.observed_datums.gate import GATE_FORMAT, T1Gate

LV = ("MHHW", "MHW", "MSL", "MTL", "DTL", "MLW", "MLLW", "MN", "GT", "DHQ", "DLQ")


def months(offset_cm=0.0, mllw_cm=0.0, drop=()):
    out = {}
    for m in range(1, 13):
        if m in drop:
            continue
        v = {lv: 1.0 + offset_cm / 100 for lv in LV}
        v["MLLW"] += mllw_cm / 100
        v["DLQ"] -= mllw_cm / 100
        out[(2015, m)] = v
    return out


NOAA = {m: {lv: 1.0 for lv in LV} for m in range(1, 13)}
GOOD_LABELS = {"label_agree": 995, "noaa_events": 1000}


def gate(ours, labels=GOOD_LABELS):
    return datum_t1.station_gate("tuning", {"1": ours}, {"1": ours}, {"1": NOAA}, {"1": labels})


def test_a_station_within_every_limit_passes():
    g = gate(months())
    assert g["format"] == GATE_FORMAT and g["stations"]["1"]["pass"] is True


def test_a_station_over_one_limit_fails_and_says_which():
    v = gate(months(mllw_cm=3.0))["stations"]["1"]
    assert v["pass"] is False and v["pass_without_labels"] is False
    assert {r["level"] for r in v["failed"] if "level" in r} >= {"MLLW", "DLQ"}


def test_a_missing_month_fails_the_station():
    v = gate(months(drop=(7,)))["stations"]["1"]
    assert v["pass"] is False


def test_label_agreement_under_99_percent_fails_only_the_labelled_verdict():
    v = gate(months(), {"label_agree": 950, "noaa_events": 1000})["stations"]["1"]
    assert v["pass"] is False and v["pass_without_labels"] is True


def test_the_build_reads_the_gate_and_untested_is_not_a_pass(tmp_path):
    g = gate(months())
    g["stations"]["2"] = {"pass": False, "pass_without_labels": True, "label_agreement": 0.95, "failed": []}
    p = tmp_path / datum_t1.gate_file_name("tuning")
    p.write_text(json.dumps(g))
    t1 = T1Gate.load(p)
    assert t1.verdict("1") == "pass" and t1.allows("1")
    assert t1.verdict("2") == "fail" and not t1.allows("2")
    assert t1.verdict("9999999") == "untested" and not t1.allows("9999999")
    assert T1Gate.load(p, require_labels=False).allows("2")


def test_two_runs_that_disagree_on_a_station_are_refused(tmp_path):
    a, b = gate(months()), gate(months(mllw_cm=3.0))
    (tmp_path / "a.json").write_text(json.dumps(a))
    (tmp_path / "b.json").write_text(json.dumps(b))
    with pytest.raises(ValueError, match="two different verdicts"):
        T1Gate.load(tmp_path / "a.json", tmp_path / "b.json")


def test_harness_and_reader_share_the_format():
    assert datum_t1.GATE_FORMAT == GATE_FORMAT
