"""Monthly means (datums spec §4.3.1 steps 7-8, and the common tidal days of §4.3.4; slot DT1).

A tidal day belongs to one calendar month (UTC), and so do its high and low waters and its hourly
heights (TidalDays: with the default "monthly" days every tidal day lies inside its calendar month,
so the month's means are NOAA's calendar-month means; with "anchored" days a day belongs to the
month in which it starts, the spec text). Per month: MHHW (mean of each tidal day's higher high; a day
with one high has that high as its higher high), MHW (mean of all highs), MSL (mean of the hourly
heights), MLW, MLLW; then MTL, DTL, MN, GT, DHQ and DLQ (handbook §3.1).

Which months count:
- gap-free months (a primary determination, a first reduction, T1): every tidal day of the month is
  complete after the 3-hour fill;
- a comparison (§4.3.4): only the given common tidal days are used, and the month counts with at least
  MIN_COMMON_DAYS of them.
"""
from __future__ import annotations

import numpy as np

from .tabulate import Tabulation

MIN_COMMON_DAYS = 24
LEVELS = ("MHHW", "MHW", "MSL", "MTL", "DTL", "MLW", "MLLW", "MN", "GT", "DHQ", "DLQ")


def derived(lv: dict) -> dict:
    out = dict(lv)
    out["MTL"] = (lv["MHW"] + lv["MLW"]) / 2
    out["DTL"] = (lv["MHHW"] + lv["MLLW"]) / 2
    out["MN"] = lv["MHW"] - lv["MLW"]
    out["GT"] = lv["MHHW"] - lv["MLLW"]
    out["DHQ"] = lv["MHHW"] - lv["MHW"]
    out["DLQ"] = lv["MLW"] - lv["MLLW"]
    return out


def month_means(tab: Tabulation, use: np.ndarray) -> dict | None:
    """The levels from the events and hourly heights of the tidal days `use`; None without a high and a low."""
    return _month_means(tab, use, tab.days.index(tab.msl_t))


def _month_means(tab: Tabulation, use: np.ndarray, hourly_days: np.ndarray) -> dict | None:
    ev = np.isin(tab.ev_day, use)
    hi = ev & (tab.ev_kind > 0)
    lo = ev & (tab.ev_kind < 0)
    hh = ev & (tab.ev_label == "HH")
    ll = ev & (tab.ev_label == "LL")
    hrs = np.isin(hourly_days, use)
    if not (hi.any() and lo.any() and hrs.any()):
        return None
    out = derived({"MHHW": float(tab.ev_h[hh].mean()), "MHW": float(tab.ev_h[hi].mean()),
                   "MSL": float(tab.msl_h[hrs].mean()), "MLW": float(tab.ev_h[lo].mean()),
                   "MLLW": float(tab.ev_h[ll].mean())})
    out["n_days"] = int(len(use))
    return out


def monthly_means(tab: Tabulation, *, days=None, min_days: int | None = None) -> dict[tuple[int, int], dict]:
    """{(year, month): levels} for the months that count (see the module text). Without `days`: gap-free
    months. With `days` (common tidal days): those days only, and at least `min_days` of them."""
    if days is not None and min_days is None:
        raise ValueError("a comparison needs min_days")
    complete = tab.complete_days
    if not complete:
        return {}
    months = sorted({tab.days.month_of(k) for k in complete})
    out = {}
    hourly_days = None
    for y, m in months:
        ks = tab.days.month_days(y, m)
        if days is None:
            if not all(int(k) in complete for k in ks):
                continue
            use = ks
        else:
            use = np.array([k for k in ks if int(k) in days and int(k) in complete], dtype=np.int64)
            if len(use) < min_days:
                continue
        if hourly_days is None:
            hourly_days = tab.days.index(tab.msl_t)
        r = _month_means(tab, use, hourly_days)
        if r is not None:
            out[(y, m)] = r
    return out


def annual_from_gap_free(tab: Tabulation) -> dict:
    """Each level's mean over the gap-free months, per calendar month first (§4.3.3's balanced mean,
    here over the months the series has)."""
    mm = monthly_means(tab)
    if not mm:
        raise ValueError("no gap-free month")
    by_cal: dict[int, list[dict]] = {}
    for (_, m), v in mm.items():
        by_cal.setdefault(m, []).append(v)
    return {lv: float(np.mean([np.mean([v[lv] for v in vs]) for vs in by_cal.values()])) for lv in LEVELS}
