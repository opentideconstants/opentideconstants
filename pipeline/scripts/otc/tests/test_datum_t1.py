"""The sealed T1 harness (slot DT1): its refusals, the T1g seed expansion and the reported
hourly-sample picking measurement. No fetched data: synthetic series only."""
import hashlib
import json
import math

import numpy as np
import pytest

from otc_pipeline import datum_partition, datum_t1
from otc_pipeline.datum_t1 import Refused

GOOD = dict(datum_partition.SEALED)


def test_monthly_evidence_retains_values_when_the_scientific_gate_fails(tmp_path, monkeypatch):
    cache = tmp_path / "tuning" / "0000001"
    cache.mkdir(parents=True)
    for name, _ in datum_t1.fetch_requests("0000001"):
        (cache / name).write_bytes(b"cached native input")
    monkeypatch.setattr(datum_t1, "work_dir", lambda partition: cache.parent)
    tcd = tmp_path / "table.json"
    tcd.write_bytes(b"pinned table")
    levels = {level: float(i) for i, level in enumerate(("MHHW", "MHW", "MSL", "MTL", "DTL", "MLW", "MLLW", "MN", "GT", "DHQ", "DLQ"))}
    levels["n_days"] = 30
    means = {"0000001": {(2015, 1): levels}}
    report = {"partition": "tuning", "result": "fail",
              "station_gate": {"stations": {"0000001": {"pass": False, "failed": [{"level": "MHW"}]}}},
              **{key: {"pass": False} for key in ("t1_6min", "t1_hourly", "t1_labels", "t1g")}}
    data = {"0000001": {"t": np.array([1420070400, 1420070760]), "h": np.array([1.0, 2.0])}}
    out = tmp_path / "output"
    receipt = datum_t1._emit_monthly(report, means, means, data, out_dir=out, tcd_path=tcd)
    rows = [json.loads(line) for line in (out / "monthly-tuning.jsonl").read_text().splitlines()]
    assert len(rows) == receipt["row_count"] == 2
    assert receipt["sha256"] == hashlib.sha256((out / "monthly-tuning.jsonl").read_bytes()).hexdigest()
    for row in rows:
        assert row["kind"] == "observed" and row["zero"] == "STND" and row["units"] == "m"
        assert row["levels_m"] == {k: v for k, v in levels.items() if k != "n_days"}
        assert row["complete_tidal_days"] == 30
        assert row["status"] == "diagnostic" and row["normal_t1_accepted"] is False
        assert row["gate_failure_reason"] == "datum_gate_failed"
        assert row["station_gate"] == report["station_gate"]["stations"]["0000001"]
        assert row["tcd_sha256"] == hashlib.sha256(tcd.read_bytes()).hexdigest()
        assert len(row["input_sha256"]) == len(datum_t1.fetch_requests("0000001"))
        assert row["observation_sample_count"] == 2 and row["observation_span_s"] == 360
        assert "uncertainty_m" not in row and row["reduced_to_otc_epoch"] is False
    assert report["result"] == "fail"


def test_monthly_evidence_without_an_output_directory_does_not_write():
    assert datum_t1._emit_monthly({}, {}, {}, {}, out_dir=None, tcd_path=None) is None


def test_seal_check_passes_with_every_sealed_sha():
    datum_t1.check_seals(GOOD)


@pytest.mark.parametrize("drop", sorted(GOOD))
def test_refuses_when_a_sealed_sha_is_missing(drop):
    given = {k: v for k, v in GOOD.items() if k != drop}
    with pytest.raises(Refused, match="not given"):
        datum_t1.check_seals(given)


@pytest.mark.parametrize("name", sorted(GOOD))
def test_refuses_a_wrong_sha(name):
    given = dict(GOOD, **{name: "0" * 64})
    with pytest.raises(Refused, match="not the loader's sealed"):
        datum_t1.check_seals(given)


def test_refuses_a_file_that_differs_from_its_seal(tmp_path):
    for n in GOOD:
        (tmp_path / n).write_bytes((datum_partition.DATA_DIR / n).read_bytes())
    (tmp_path / "t1g_gap_patterns.json").write_bytes(b"{}")
    with pytest.raises(Refused, match="file SHA-256"):
        datum_t1.check_seals(GOOD, data_dir=tmp_path)


def test_run_refuses_without_seals_before_reading_any_data(capsys):
    assert datum_t1.main(["run", "--partition", "tuning"]) == 3
    assert "sealed SHA-256 not given" in capsys.readouterr().err


@pytest.mark.parametrize("p", ["holdout", "reserve"])
def test_run_refuses_a_sealed_partition_without_sealed_run(p, capsys):
    args = ["run", "--partition", p] + [f"--sealed={k}={v}" for k, v in GOOD.items()]
    assert datum_t1.main(args) == 3
    assert "only a sealed run" in capsys.readouterr().err


@pytest.mark.parametrize("p", ["holdout", "reserve"])
def test_fetch_and_plan_refuse_a_sealed_partition_without_sealed_run(p):
    with pytest.raises(Refused):
        datum_t1.t1_stations(p, sealed_run=False)


# --- T1g expansion -------------------------------------------------------------------------------------

PATTERNS = datum_t1.t1g_patterns()


@pytest.mark.parametrize("letter", ["a", "b", "c"])
def test_gap_patterns_a_to_c_place_separate_gaps_inside_each_month(letter):
    p = PATTERNS[letter]
    gaps = datum_t1.expand_pattern(letter, p, "0000001", "short")
    assert len(gaps) == 12 * p["gaps_per_month"]
    for m in range(1, 13):
        a, b = datum_t1.month_bounds(2015, m)
        mine = sorted(g for g in gaps if a <= g[0] < b)
        assert len(mine) == p["gaps_per_month"]
        for s, e in mine:
            assert e - s == p["gap_hours"] * 3600 and a <= s and e <= b and (s - a) % 360 == 0
        for (s1, e1), (s2, e2) in zip(mine, mine[1:]):
            assert e1 + 360 <= s2


def test_gap_pattern_d_drops_ten_percent_of_each_months_hours():
    gaps = datum_t1.expand_pattern("d", PATTERNS["d"], "0000001", "short")
    for m in range(1, 13):
        a, b = datum_t1.month_bounds(2015, m)
        mine = [g for g in gaps if a <= g[0] < b]
        assert len(mine) == round(0.1 * (b - a) / 3600)
        assert all(e - s == 3600 and (s - a) % 3600 == 0 for s, e in mine)


def test_expansion_depends_on_station_role_and_seed():
    p = PATTERNS["a"]
    base = datum_t1.expand_pattern("a", p, "0000001", "short")
    assert datum_t1.expand_pattern("a", p, "0000001", "short") == base
    assert datum_t1.expand_pattern("a", p, "0000002", "short") != base
    assert datum_t1.expand_pattern("a", p, "0000001", "control") != base
    assert datum_t1.expand_pattern("a", dict(p, seed=p["seed"] + 1), "0000001", "short") != base


def test_expansion_is_pinned():
    # Pinned when the expansion was committed (before any T1g score): a change to the rule fails here.
    pairs = [{"short": "0000001", "control": "0000002", "class": "mixed", "distance_km": 0.0}]
    got = datum_t1.t1g_gaps(pairs, PATTERNS)["gaps_sha256"]
    assert got == PINNED_SYNTHETIC_GAPS_SHA256


PINNED_SYNTHETIC_GAPS_SHA256 = "26e3e554ad77397d924a7256d01f26650871421faa415d0cc9c3980114078d6f"


def test_pattern_d_is_at_the_short_station_only_and_a_to_c_at_both():
    assert datum_t1.roles("d", PATTERNS["d"]) == ("short",)
    for k in "abc":
        assert datum_t1.roles(k, PATTERNS[k]) == ("short", "control")


def test_committed_tuning_plan_reproduces_from_the_code():
    path = datum_t1.plan_path("tuning")
    plan = json.loads(path.read_text())
    assert datum_t1.t1g_gaps(plan["pairs"], PATTERNS)["gaps_sha256"] == plan["gaps_sha256"]
    shorts = [p["short"] for p in plan["pairs"]]
    tuning = datum_partition.load("t1", "tuning")
    mixed = [s["id"] for s in tuning if s["class"] == "mixed"][:3]
    diurnal = [s["id"] for s in tuning if s["class"] == "diurnal"][:2]
    assert shorts == mixed + diurnal
    cls = {s["id"]: s["class"] for s in tuning}
    assert all(cls[p["control"]] == p["class"] and p["control"] != p["short"] for p in plan["pairs"])


def test_apply_gaps_keeps_samples_outside_every_interval():
    t = np.arange(0, 36000, 360, dtype=np.int64)
    keep = datum_t1.apply_gaps(t, [[3600, 3600 + 3 * 3600]])
    assert (~keep).sum() == 30 and keep[t < 3600].all() and keep[t >= 3600 * 4].all()


def test_nearest_same_class_control():
    st = [{"id": "1", "class": "mixed"}, {"id": "2", "class": "mixed"}, {"id": "3", "class": "mixed"},
          {"id": "4", "class": "mixed"}, {"id": "5", "class": "diurnal"}, {"id": "6", "class": "diurnal"},
          {"id": "7", "class": "diurnal"}]
    pos = {"1": (0, 0), "2": (0, 5), "3": (0, 1), "4": (0, 9), "5": (0, 0.5), "6": (0, 3), "7": (0, 2)}
    pairs = datum_t1.t1g_pairs(st, pos)
    assert [(p["short"], p["control"]) for p in pairs] == [("1", "3"), ("2", "3"), ("3", "1"), ("5", "7"), ("6", "7")]


# --- the hourly-sample picking measurement and the US method rule --------------------------------------

def test_slackwater_month_on_a_pure_semidiurnal_tide():
    a, b = datum_t1.month_bounds(2015, 1)
    t = np.arange(a, b, 360, dtype=np.int64)
    h = 1.0 + 0.5 * np.cos(2 * math.pi * t / (12.42 * 3600))
    r = datum_t1.slackwater_month(t, h, 2015, 1)
    assert abs(r["MSL"] - 1.0) < 0.01
    assert 1.45 < r["MHW"] <= 1.5 and 0.5 <= r["MLW"] < 0.55
    assert abs(r["MTL"] - 1.0) < 0.01


@pytest.mark.parametrize("lon,method", [(-118.3, "standard"), (-170.7, "standard"), (144.7, "standard"),
                                        (-160.0, "standard"), (-88.1, "modified"), (-65.4, "modified"),
                                        (-76.3, "modified")])
def test_us_method_by_region(lon, method):
    assert datum_t1.us_method(lon) == method


def test_t1_limits_are_the_spec_values():
    lim = {(lv, s): (a, b) for lv, s, a, b in datum_t1.T1_LIMITS}
    assert lim[("MSL", "max")] == (0.2, 0.2)
    for lv in ("MHW", "MLW", "MTL", "MN"):
        assert lim[(lv, "median")] == (0.3, 0.5) and lim[(lv, "p95")] == (1.0, 1.5)
    for lv in ("MHHW", "MLLW", "DHQ", "DLQ"):
        assert lim[(lv, "p95")] == (1.5, 2.5)
    assert datum_t1.LABEL_AGREEMENT == 0.99
    assert dict(datum_t1.T1G_LIMITS) == {"median": 0.3, "p95": 1.0}


def test_sealed_seed_derivation():
    for k, p in PATTERNS.items():
        h = hashlib.sha256(f"otc-datums-t1g-v1|{k}".encode()).digest()
        assert p["seed"] == int.from_bytes(h[:8], "big")
