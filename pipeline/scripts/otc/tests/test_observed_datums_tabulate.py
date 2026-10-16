"""Tabulation and monthly means (datums spec §4.3.1, slot DT1) on synthetic series."""
import csv
import hashlib
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from otc_pipeline.observed_datums import monthly, tabulate
from otc_pipeline.observed_datums.tabulate import ANCHOR_S, TIDAL_DAY_S
from gesla.tcd_table import TcdTable

M2 = 12.4206012 * 3600
K1 = 23.9344697 * 3600


@pytest.mark.skipif("OTC_OBSERVED_NATIVE_INPUTS" not in os.environ, reason="requires the pinned actual NOAA2015 tuning cache")
@pytest.mark.parametrize("sid", ("9410660", "1770000", "9463502"))
def test_native_january_tabulation_keeps_observed_hourly_mean(sid):
    receipt = json.loads(Path(os.environ["OTC_OBSERVED_NATIVE_INPUTS"]).read_text())
    station = next(s for s in receipt["stations"] if s["id"] == sid)
    directory = Path(station["path"])
    samples = {}
    files = ("wl-edge-before.csv", "wl-2015-01.csv", "wl-2015-02.csv")
    for name in files:
        path = directory / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == station["files"][name]["sha256"]
        with path.open() as stream:
            for row in csv.DictReader(stream, skipinitialspace=True):
                value = (row.get("Water Level") or "").strip()
                if value:
                    timestamp = int(datetime.strptime(row["Date Time"].strip(), "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc).timestamp())
                    samples.setdefault(timestamp, float(value))
    times = np.array(sorted(samples), dtype=np.int64)
    heights = np.array([samples[t] for t in times])
    table_path = Path(receipt["tcd"]["path"])
    assert hashlib.sha256(table_path.read_bytes()).hexdigest() == receipt["tcd"]["sha256"]
    table = TcdTable.load(table_path)
    constituents = json.loads((directory / "harcon.json").read_text())["HarmonicConstituents"]
    names = {"LAM2": "LDA2", "RHO": "RHO1"}
    predict = tabulate.predictor(table, {names.get(c["name"], c["name"]): (c["amplitude"], c["phase_GMT"])
                                        for c in constituents if c["amplitude"] > 0})
    result = monthly.monthly_means(tabulate.tabulate(times, heights, predict, interval_s=360))[(2015, 1)]
    on_hour = (times % 3600 == 0) & (times >= ts(2015, 1, 1)) & (times < ts(2015, 2, 1))
    assert result["MSL"] == pytest.approx(float(heights[on_hour].mean()), abs=1e-12)
    assert all(math.isfinite(result[level]) for level in monthly.LEVELS)
    assert result["MTL"] == (result["MHW"] + result["MLW"]) / 2
    assert result["MN"] == result["MHW"] - result["MLW"]
    assert result["DTL"] == (result["MHHW"] + result["MLLW"]) / 2
    assert result["n_days"] > 0
    for name in files:
        assert hashlib.sha256((directory / name).read_bytes()).hexdigest() == station["files"][name]["sha256"]
    print(json.dumps({"station": sid, "month": "2015-01", "observed_levels_m_above_STND": result,
                      "raw_cache_unchanged": True, "scientific_T1_acceptance_claimed": False}))


def ts(*a):
    return int(datetime(*a, tzinfo=timezone.utc).timestamp())


def mixed(t):
    t = np.asarray(t, dtype=np.float64)
    return 1.0 + 0.5 * np.cos(2 * math.pi * t / M2) + 0.3 * np.cos(2 * math.pi * t / K1 + 1.0)


def series(a, b, step=360, f=mixed):
    t = np.arange(a, b, step, dtype=np.int64)
    return t, f(t)


JAN, MAR = ts(2015, 1, 1), ts(2015, 3, 1)


def test_anchored_tidal_days_start_at_1983():
    days = tabulate.TidalDays("anchored")
    assert ANCHOR_S == ts(1983, 1, 1)
    end = int(math.ceil(ANCHOR_S + TIDAL_DAY_S))
    assert days.index([ANCHOR_S, end - 1, end]).tolist() == [0, 0, 1]
    assert TIDAL_DAY_S == pytest.approx(24.8412 * 3600)
    ks = days.month_days(2015, 1)
    assert days.bounds(ks[0])[0] >= JAN > days.bounds(ks[0] - 1)[0]
    assert days.bounds(ks[-1])[0] < ts(2015, 2, 1) <= days.bounds(ks[-1] + 1)[0]
    assert days.month_of(ks[-1]) == (2015, 1)


def test_monthly_tidal_days_restart_at_each_month_and_end_with_it():
    days = tabulate.TidalDays("monthly")
    ks = days.month_days(2015, 1)
    assert len(ks) == math.ceil(31 * 86400 / TIDAL_DAY_S)
    assert days.bounds(ks[0]) == (JAN, JAN + TIDAL_DAY_S)
    assert days.bounds(ks[-1])[1] == ts(2015, 2, 1)
    assert days.index([JAN, JAN + TIDAL_DAY_S - 1, ts(2015, 2, 1) - 1, ts(2015, 2, 1)]).tolist() == \
        [ks[0], ks[0], ks[-1], days.month_days(2015, 2)[0]]
    assert all(days.month_of(k) == (2015, 1) for k in ks)
    # the last day's label window looks one full tidal day ahead, past the month's end
    assert days.label_window(ks[-1])[1] > ts(2015, 2, 1)


def test_month_end_event_is_labelled_against_its_whole_tidal_day():
    # Last tidal day of January: a low at 12:00 on the 31st and a lower one at 00:30 on 1 February. The
    # February low is the lower of the window, so the January low is not LL; February labels its own.
    days = tabulate.TidalDays("monthly")
    k = int(days.month_days(2015, 1)[-1])
    a, _ = days.bounds(k)
    assert a < ts(2015, 1, 31, 12)
    t = np.array([ts(2015, 1, 31, 6), ts(2015, 1, 31, 12), ts(2015, 1, 31, 18), ts(2015, 2, 1, 0, 30),
                  ts(2015, 2, 1, 6)])
    h = np.array([2.0, 0.4, 2.1, 0.1, 2.2])
    kind = np.array([1, -1, 1, -1, 1], dtype=np.int8)
    day = days.index(t)
    assert day[3] != k and day[1] == k
    lab = tabulate.label_events(t, h, kind, day, days)
    assert lab.tolist() == ["H", "L", "HH", "LL", "HH"]
    # "anchored" days do not look past the month: the same January low is then LL in its own day
    anch = tabulate.TidalDays("anchored")
    lab = tabulate.label_events(t, h, kind, anch.index(t), anch)
    assert (lab == "LL").sum() >= 1


def test_events_of_a_known_curve_with_the_exact_predictor():
    t, h = series(JAN - 86400 * 2, MAR + 86400 * 2)
    tab = tabulate.tabulate(t, h, mixed, interval_s=360)
    grid = np.arange(JAN, MAR, 60)
    v = mixed(grid)
    i = np.flatnonzero((v[1:-1] > v[:-2]) & (v[1:-1] >= v[2:])) + 1
    sel = (tab.ev_kind > 0) & (tab.ev_t >= JAN) & (tab.ev_t < MAR)
    assert np.all(np.abs(tab.ev_t[sel] - grid[i]) <= 180)
    assert np.allclose(tab.ev_h[sel], v[i], atol=1e-9)


def test_reconstruction_restores_the_curve_between_samples():
    # Hourly samples; the prediction carries the shape between them, the residual is linear.
    t, h = series(JAN, JAN + 10 * 86400, step=3600)
    tab = tabulate.tabulate(t, h + 0.05, mixed, interval_s=3600)
    grid = tab.grid_t0 + 60 * np.arange(len(tab.h))
    assert np.allclose(tab.h, mixed(grid) + 0.05, atol=1e-9)


def test_hh_and_ll_labels_one_each_per_tidal_day():
    t, h = series(JAN - 86400 * 2, MAR + 86400 * 2)
    tab = tabulate.tabulate(t, h, mixed, interval_s=360)
    for k in np.unique(tab.ev_day):
        d = tab.ev_day == k
        for kind, top in ((1, "HH"), (-1, "LL")):
            sel = d & (tab.ev_kind == kind)
            if sel.any():
                assert (tab.ev_label[sel] == top).sum() == 1
                j = np.flatnonzero(sel)[np.argmax(kind * tab.ev_h[sel])]
                assert tab.ev_label[j] == top


def test_gap_of_three_hours_is_filled_and_a_longer_one_is_not():
    t, h = series(JAN, JAN + 6 * 86400)
    g3 = (t >= JAN + 86400 * 2) & (t < JAN + 86400 * 2 + 3 * 3600)
    tab = tabulate.tabulate(t[~g3], h[~g3], mixed, interval_s=360)
    assert np.isfinite(tab.h).all() and tab.filled_gaps == 1 and tab.unfilled_gaps == 0
    g = (t >= JAN + 86400 * 2) & (t < JAN + 86400 * 2 + 3 * 3600 + 360)
    tab = tabulate.tabulate(t[~g], h[~g], mixed, interval_s=360)
    assert np.isnan(tab.h).sum() > 0 and tab.unfilled_gaps == 1
    days = tab.days
    k = int(days.index(JAN + 86400 * 2 + 3600))
    assert k not in tab.complete_days and int(days.index(JAN + 86400 * 5)) in tab.complete_days


def test_small_wiggles_do_not_make_events():
    t, h = series(JAN, JAN + 5 * 86400)
    noise = 0.004 * np.sin(2 * math.pi * t / 1800.0)
    tab = tabulate.tabulate(t, h + noise, mixed, interval_s=360)
    clean = tabulate.tabulate(t, h, mixed, interval_s=360)
    assert len(tab.ev_t) == len(clean.ev_t)
    assert np.all(np.abs(tab.ev_t - clean.ev_t) <= 1800)


@pytest.mark.parametrize("dt_h,dh,kept", [(2.5, 0.05, 4), (1.5, 0.05, 2), (2.5, 0.02, 2)])
def test_minimum_spacing_and_height_difference(dt_h, dh, kept):
    # H, L, H, L: the middle pair (and the last) may be too close in time or in height.
    t = np.array([0, 6, 6 + dt_h, 6 + 2 * dt_h]) * 3600.0
    kind = np.array([1, -1, 1, -1], dtype=np.int8)
    h = np.array([1.0, 0.0, dh, 0.0 - 0.001])
    keep = tabulate.filter_events(t, h, kind, np.zeros(4, dtype=np.int64), 2 * 3600, 0.03)
    assert keep.sum() == kept


def test_monthly_means_of_a_pure_tide_and_the_month_rule():
    t, h = series(JAN - 3 * 86400, MAR + 3 * 86400)
    tab = tabulate.tabulate(t, h, mixed, interval_s=360)
    mm = monthly.monthly_means(tab)
    assert set(mm) == {(2015, 1), (2015, 2)}
    jan = mm[(2015, 1)]
    assert jan["n_days"] == len(tab.days.month_days(2015, 1))
    assert jan["MHHW"] >= jan["MHW"] > jan["MSL"] > jan["MLW"] >= jan["MLLW"]
    assert jan["MSL"] == pytest.approx(1.0, abs=0.02)
    assert jan["MN"] == pytest.approx(jan["MHW"] - jan["MLW"])
    assert jan["DTL"] == pytest.approx((jan["MHHW"] + jan["MLLW"]) / 2)


def test_a_month_with_an_unfilled_gap_does_not_count_and_a_filled_one_does():
    t, h = series(JAN - 3 * 86400, MAR + 3 * 86400)
    g = (t >= ts(2015, 1, 15)) & (t < ts(2015, 1, 15) + 4 * 3600)
    mm = monthly.monthly_means(tabulate.tabulate(t[~g], h[~g], mixed, interval_s=360))
    assert (2015, 1) not in mm and (2015, 2) in mm
    g = (t >= ts(2015, 1, 15)) & (t < ts(2015, 1, 15) + 3 * 3600)
    mm = monthly.monthly_means(tabulate.tabulate(t[~g], h[~g], mixed, interval_s=360))
    assert (2015, 1) in mm


def test_common_tidal_days_need_24_in_the_month():
    t, h = series(JAN - 3 * 86400, MAR + 3 * 86400)
    tab = tabulate.tabulate(t, h, mixed, interval_s=360)
    ks = [int(k) for k in tab.days.month_days(2015, 1)]
    assert monthly.monthly_means(tab, days=set(ks[:24]), min_days=24)[(2015, 1)]["n_days"] == 24
    assert (2015, 1) not in monthly.monthly_means(tab, days=set(ks[:23]), min_days=24)


def test_hourly_means_record_uses_the_means_for_msl():
    t = np.arange(JAN - 3 * 86400, MAR + 3 * 86400, 3600, dtype=np.int64)
    fine = np.arange(t[0], t[-1] + 3600, 60) + 30   # the minute midpoints the reconstruction samples
    means = mixed(fine).reshape(-1, 60).mean(axis=1)
    tab = tabulate.tabulate(t, means, mixed, interval_s=3600, sampling="mean")
    assert np.array_equal(tab.msl_h, means)
    grid = tab.grid_t0 + 60 * np.arange(len(tab.h))
    assert np.allclose(tab.h, mixed(grid), atol=1e-9)


def test_predictor_matches_the_engine_convention():
    class C:
        def __init__(self, speed):
            self.speed, self.v0u, self.f = speed, tuple([10.0] * 3), tuple([0.9] * 3)

    class T:
        first_year, last_year = 2014, 2016
        def __getitem__(self, k):
            return C(28.9841042)

    p = tabulate.predictor(T(), {"M2": (1.0, 40.0)}, z0=0.5)
    t = np.array([ts(2015, 1, 1), ts(2015, 1, 1, 6)])
    want = 0.5 + 0.9 * np.cos(np.radians(28.9841042 * np.array([0.0, 6.0]) + 10.0 - 40.0))
    assert np.allclose(p(t), want)


def test_running_mean_keeps_gaps_and_cuts_the_window_at_run_ends():
    x = np.array([1.0, 2.0, 3.0, np.nan, 10.0, 10.0, 10.0, 10.0])
    r = tabulate.running_mean(x, 3)
    assert np.isnan(r[3])
    assert r[:3].tolist() == [1.5, 2.0, 2.5]
    assert r[4:].tolist() == [10.0] * 4


def test_residual_smoothing_removes_the_noise_bias_of_highs():
    # Sample noise lifts the highest sample of each high water; the smoothed residual does not.
    rng = np.random.default_rng(1)
    t, h = series(JAN - 3 * 86400, MAR + 3 * 86400)
    noisy = h + rng.normal(0, 0.01, len(t))
    raw = monthly.monthly_means(tabulate.tabulate(t, noisy, mixed, interval_s=360, smooth_s=0))[(2015, 1)]
    smooth = monthly.monthly_means(tabulate.tabulate(t, noisy, mixed, interval_s=360))[(2015, 1)]
    truth = monthly.monthly_means(tabulate.tabulate(t, h, mixed, interval_s=360, smooth_s=0))[(2015, 1)]
    assert raw["MHW"] - truth["MHW"] > 0.005
    assert abs(smooth["MHW"] - truth["MHW"]) < 0.002
    assert tabulate.SMOOTH_S == 91 * 60


def test_hourly_heights_are_the_observed_values_at_the_hour_not_the_smoothed_curve():
    # CO-OPS 2 §2.6 (p. 13): hourly heights are every tenth 6-minute value.
    rng = np.random.default_rng(2)
    t, h = series(JAN, JAN + 5 * 86400)
    noisy = h + rng.normal(0, 0.01, len(t))
    tab = tabulate.tabulate(t, noisy, mixed, interval_s=360)
    on = t % 3600 == 0
    assert np.array_equal(tab.msl_t, t[on]) and np.allclose(tab.msl_h, noisy[on], atol=1e-12)


def test_event_times_are_to_the_nearest_tenth_of_an_hour():
    # CO-OPS 2 §2.6 (p. 13): times of high and low water to the nearest tenth of an hour.
    t, h = series(JAN, JAN + 5 * 86400)
    tab = tabulate.tabulate(t, h, mixed, interval_s=360)
    exact = tabulate.tabulate(t, h, mixed, interval_s=360, event_round_s=0)
    assert np.all(tab.ev_t % 360 == 0)
    assert np.all(np.abs(tab.ev_t - exact.ev_t) <= 180)
    assert tabulate.EVENT_ROUND_S == 360
