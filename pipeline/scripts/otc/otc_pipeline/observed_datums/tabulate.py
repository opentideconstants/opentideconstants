"""Tabulation of high and low waters and hourly heights (datums spec §4.3.1, slot DT1).

1. Input: a gauge series after QC, in the record's zero; times in true UTC.
2. Reconstruction: P(t) is the instantaneous harmonic prediction from the set's own constants, P_s the
   same prediction sampled as the gauge samples (instantaneous, or the mean over [t, t + interval) for a
   record of means). The residual r = observed - P_s is interpolated linearly to a 1-minute grid,
   smoothed by a centred running mean of SMOOTH_S, and the curve is h(t) = P(t) + r(t). A mean
   sample's residual is placed at the centre of its interval.
   [DT1 decision D2, tuning partition only: the smoothing is not in the spec text. Without it the
   tabulated highs sit above NOAA's and the lows below, by about the sample noise (MHW signed median
   +0.62 cm, MN +1.09 cm at 6 minutes); NOAA fits a curve to the 6-minute data (handbook §2.6).]
3. Gaps: a gap is the time two successive samples are apart less one sampling interval. Gaps of 3 hours
   or less are filled by the same reconstruction; longer gaps are left empty (never filled).
4. Hourly heights: h at each whole hour UTC; for a record of hourly means, the means themselves.
5. High and low waters: the turning points of h on the 1-minute grid, then, while any two successive
   events are under MIN_DT_S apart or differ by under MIN_DH_M, the pair with the smallest height
   difference among those is removed (removing an adjacent high/low pair keeps the alternation).
   2 h and 0.03 m were kept: no other pair on the tuning grid matched NOAA's events better.
6. Tidal days (TidalDays): by default ("monthly") tidal days of 24.8412 h counted from the start of each
   calendar month (UTC), as NOAA's own high/low labels and monthly means are [DT1 decision D1, tuning
   partition only]; "anchored" is the spec text (from 1983-01-01T00:00:00Z). In each tidal day the
   highest high in its label window is the higher high (HH, the first one on a tie) and the other highs
   are H; the same for lows (LL, L). A tidal day is complete when its whole span is on the grid with
   no unfilled gap.
"""
from __future__ import annotations

import heapq
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Mapping

import numpy as np

ANCHOR_S = 410_227_200            # 1983-01-01T00:00:00Z
TIDAL_DAY_S = 24.8412 * 3600.0    # one mean lunar day
FILL_MAX_S = 3 * 3600
MIN_DT_S = 2 * 3600               # [unverified as NOAA's; tunable on the tuning partition only, frozen before DT3]
MIN_DH_M = 0.03
SMOOTH_S = 91 * 60                # residual running mean (s), chosen on the tuning partition (DT1)
GRID_S = 60
EVENT_ROUND_S = 360               # high/low times to the nearest 0.1 h (CO-OPS 2 §2.6, p. 13)

Predict = Callable[[np.ndarray], np.ndarray]


def _month_start(year: int, month: int) -> int:
    return int(datetime(year, month, 1, tzinfo=timezone.utc).timestamp())


class TidalDays:
    """The tidal-day scheme (§4.3.1 step 6).

    "anchored": intervals of TIDAL_DAY_S from ANCHOR_S; a tidal day belongs to the month in which it starts.
    "monthly": tidal days of TIDAL_DAY_S counted from the start of each calendar month (UTC); the month's
    last tidal day ends at the month's end. Every tidal day then lies inside one calendar month.
    Day keys are integers: the anchored day number, or (12 x year + month - 1) x 64 + n for "monthly".
    """

    MODES = ("anchored", "monthly")

    def __init__(self, mode: str = "monthly") -> None:
        if mode not in self.MODES:
            raise ValueError(f"tidal-day mode {mode!r} is not one of {self.MODES}")
        self.mode = mode

    def index(self, t_s) -> np.ndarray:
        t = np.asarray(t_s, dtype=np.float64)
        if self.mode == "anchored":
            return np.floor((t - ANCHOR_S) / TIDAL_DAY_S).astype(np.int64)
        dt = t.astype(np.int64).astype("datetime64[s]")
        mon = dt.astype("datetime64[M]")
        start = mon.astype("datetime64[s]").astype(np.int64)
        n = np.floor((t - start) / TIDAL_DAY_S).astype(np.int64)
        return (mon.astype(np.int64) + 1970 * 12) * 64 + n

    def bounds(self, k: int) -> tuple[float, float]:
        k = int(k)
        if self.mode == "anchored":
            return ANCHOR_S + k * TIDAL_DAY_S, ANCHOR_S + (k + 1) * TIDAL_DAY_S
        ym, n = divmod(k, 64)
        y, m = divmod(ym, 12)
        a = _month_start(y, m + 1)
        b = _month_start(y + 1, 1) if m == 11 else _month_start(y, m + 2)
        return a + n * TIDAL_DAY_S, min(a + (n + 1) * TIDAL_DAY_S, float(b))

    def label_window(self, k: int) -> tuple[float, float]:
        """The span whose events decide the day's higher high and lower low: one full TIDAL_DAY_S from
        the day's start, so the month's last tidal day also looks past the month's end. An event of the
        next month in that span takes part in the comparison but is labelled by its own month's day."""
        a, _ = self.bounds(k)
        return a, a + TIDAL_DAY_S

    def month_days(self, year: int, month: int) -> np.ndarray:
        """The tidal days that belong to the month."""
        a = _month_start(year, month)
        b = _month_start(year + 1, 1) if month == 12 else _month_start(year, month + 1)
        if self.mode == "anchored":
            k0 = int(np.ceil((a - ANCHOR_S) / TIDAL_DAY_S))
            k1 = int(np.ceil((b - ANCHOR_S) / TIDAL_DAY_S))
            return np.arange(k0, k1, dtype=np.int64)
        n = int(np.ceil((b - a) / TIDAL_DAY_S))
        return (12 * year + month - 1) * 64 + np.arange(n, dtype=np.int64)

    def month_of(self, k: int) -> tuple[int, int]:
        if self.mode == "anchored":
            d = datetime.fromtimestamp(ANCHOR_S + int(k) * TIDAL_DAY_S, tz=timezone.utc)
            return d.year, d.month
        y, m = divmod(int(k) // 64, 12)
        return y, m + 1

    def covering(self, g0: float, g1: float) -> list[int]:
        """The tidal days whose whole span lies inside [g0, g1 + GRID_S)."""
        out = []
        d0 = datetime.fromtimestamp(g0, tz=timezone.utc)
        y, m = d0.year, d0.month
        while _month_start(y, m) <= g1:
            for k in self.month_days(y, m):
                a, b = self.bounds(k)
                if a >= g0 and b <= g1 + GRID_S and (self.mode == "monthly" or (y, m) == self.month_of(k)):
                    out.append(int(k))
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        return sorted(set(out))


def predictor(table, constituents: Mapping[str, tuple[float, float]], z0: float = 0.0) -> Predict:
    """P(t) = z0 + sum f A cos(speed x hours since 1 Jan of t's year + (V0+u) - g), in the engine's
    convention (speed, V0+u and f per year from the TCD table; g the Greenwich phase lag)."""
    terms = []
    for raw, (amp, phase) in sorted(constituents.items()):
        c = table[raw]
        terms.append((c.speed, np.asarray(c.v0u, dtype=np.float64), np.asarray(c.f, dtype=np.float64),
                      float(amp), float(phase)))
    first, last = table.first_year, table.last_year

    def predict(t_s: np.ndarray) -> np.ndarray:
        t = np.asarray(t_s, dtype=np.int64)
        dt = t.astype("datetime64[s]")
        years = dt.astype("datetime64[Y]")
        hours = (dt - years.astype("datetime64[s]")).astype(np.int64) / 3600.0
        yi = years.astype(np.int64) + 1970 - first
        if len(t) and (yi.min() < 0 or yi.max() > last - first):
            raise ValueError("time outside the TCD table's years")
        out = np.full(len(t), float(z0))
        for speed, v0u, f, amp, phase in terms:
            out += f[yi] * amp * np.cos(np.radians(speed * hours + v0u[yi] - phase))
        return out

    return predict


@dataclass(frozen=True, eq=False)
class Tabulation:
    grid_t0: int                 # Unix s of the first grid point (a whole minute)
    h: np.ndarray                # reconstructed height on the 1-minute grid; NaN in unfilled gaps
    msl_t: np.ndarray            # times of the heights MSL averages (whole hours, or the hourly means' stamps)
    msl_h: np.ndarray
    ev_t: np.ndarray             # high and low waters: time (Unix s), height, kind (+1 high, -1 low),
    ev_h: np.ndarray             #   tidal day, label (HH, H, LL, L)
    ev_kind: np.ndarray
    ev_day: np.ndarray
    ev_label: np.ndarray
    complete_days: frozenset     # tidal days with their whole span on the grid and no unfilled gap
    days: TidalDays
    filled_gaps: int
    unfilled_gaps: int


def _sampled(predict: Predict, t: np.ndarray, interval_s: int, sampling: str) -> tuple[np.ndarray, np.ndarray]:
    if sampling == "instantaneous":
        return predict(t), t.astype(np.float64)
    if sampling != "mean":
        raise ValueError(f"sampling {sampling!r} is not 'instantaneous' or 'mean'")
    steps = max(1, interval_s // GRID_S)
    offs = (np.arange(steps) + 0.5) * (interval_s / steps)
    ps = predict((t[:, None] + offs[None, :]).round().astype(np.int64).ravel()).reshape(len(t), steps).mean(axis=1)
    return ps, t + interval_s / 2.0


def running_mean(x: np.ndarray, width: int) -> np.ndarray:
    """Centred running mean over `width` points (odd), within each run of finite values; NaN stays NaN.
    Near a run's ends the window is cut to what the run has."""
    w = width | 1
    half = w // 2
    out = np.full(len(x), np.nan)
    finite = np.isfinite(x)
    edges = np.flatnonzero(np.diff(np.concatenate(([0], finite.view(np.int8), [0]))))
    for a, b in zip(edges[::2], edges[1::2]):
        v = x[a:b]
        c = np.concatenate(([0.0], np.cumsum(v)))
        i = np.arange(len(v))
        lo, hi = np.maximum(i - half, 0), np.minimum(i + half + 1, len(v))
        out[a:b] = (c[hi] - c[lo]) / (hi - lo)
    return out


def turning_points(h: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Indices and kinds (+1 high, -1 low) of the turning points of h within each run of finite values."""
    idx, kind = [], []
    finite = np.isfinite(h)
    edges = np.flatnonzero(np.diff(np.concatenate(([0], finite.view(np.int8), [0]))))
    for a, b in zip(edges[::2], edges[1::2]):
        v = h[a:b]
        if len(v) < 3:
            continue
        s = np.sign(np.diff(v))
        nz = np.flatnonzero(s)
        if len(nz) == 0:
            continue
        # carry the last non-zero slope over flat steps
        fill = np.maximum.accumulate(np.where(s != 0, np.arange(len(s)), -1))
        s = np.where(fill >= 0, s[np.maximum(fill, 0)], 0)
        ch = np.flatnonzero((s[:-1] != 0) & (s[1:] != 0) & (s[:-1] != s[1:])) + 1
        idx.append(a + ch)
        kind.append(np.where(s[ch - 1] > 0, 1, -1))
    if not idx:
        return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int8)
    return np.concatenate(idx).astype(np.int64), np.concatenate(kind).astype(np.int8)


def filter_events(t: np.ndarray, h: np.ndarray, kind: np.ndarray, segment: np.ndarray,
                  min_dt_s: float, min_dh_m: float) -> np.ndarray:
    """Mask of the events kept (§4.3.1 step 5). Successive events of one segment that are under min_dt_s
    apart or differ by under min_dh_m: the pair with the smallest height difference is removed, and the
    events either side become successive; repeated until no such pair is left."""
    n = len(t)
    alive = np.ones(n, dtype=bool)
    nxt = np.arange(1, n + 1)
    prv = np.arange(-1, n - 1)

    def bad(i, j):
        return (0 <= i < n and 0 <= j < n and segment[i] == segment[j] and kind[i] != kind[j]
                and (t[j] - t[i] < min_dt_s or abs(h[j] - h[i]) < min_dh_m))

    heap = [(abs(h[i + 1] - h[i]), i, i + 1) for i in range(n - 1) if bad(i, i + 1)]
    heapq.heapify(heap)
    while heap:
        _, i, j = heapq.heappop(heap)
        if not (alive[i] and alive[j] and nxt[i] == j):
            continue
        alive[i] = alive[j] = False
        p, q = prv[i], nxt[j]
        if p >= 0:
            nxt[p] = q
        if q < n:
            prv[q] = p
        if bad(p, q):
            heapq.heappush(heap, (abs(h[q] - h[p]), p, q))
    return alive


def label_events(ev_t: np.ndarray, ev_h: np.ndarray, kind: np.ndarray, ev_day: np.ndarray,
                 days: TidalDays) -> np.ndarray:
    """HH/H and LL/L: an event is HH (LL) when it is the highest high (lowest low) of its own tidal day's
    label window, the first one on a tie; the window can hold events of the next month, which take part
    in the comparison but are labelled by their own day."""
    label = np.where(kind > 0, "H", "L").astype("<U2")
    for k in np.unique(ev_day):
        a, b = days.label_window(k)
        win = (ev_t >= a) & (ev_t < b)
        for sign, top in ((1, "HH"), (-1, "LL")):
            sel = np.flatnonzero(win & (kind == sign))
            if len(sel):
                j = sel[np.argmax(sign * ev_h[sel])]
                if ev_day[j] == k:
                    label[j] = top
    return label


def tabulate(times_s, heights_m, predict: Predict, *, interval_s: int, sampling: str = "instantaneous",
             fill_max_s: int = FILL_MAX_S, min_dt_s: float = MIN_DT_S, min_dh_m: float = MIN_DH_M,
             smooth_s: int = SMOOTH_S, days: TidalDays | None = None,
             event_round_s: int = EVENT_ROUND_S) -> Tabulation:
    t = np.asarray(times_s, dtype=np.int64)
    obs = np.asarray(heights_m, dtype=np.float64)
    if len(t) < 2 or np.any(np.diff(t) <= 0) or not np.all(np.isfinite(obs)):
        raise ValueError("tabulate wants at least 2 samples, strictly increasing times and finite heights")
    ps, tr = _sampled(predict, t, interval_s, sampling)
    r = obs - ps
    g0 = int(np.ceil(tr[0] / GRID_S)) * GRID_S
    g1 = int(np.floor(tr[-1] / GRID_S)) * GRID_S
    grid = np.arange(g0, g1 + 1, GRID_S, dtype=np.int64)
    rg = np.interp(grid.astype(np.float64), tr, r)
    missing = np.diff(t) - interval_s
    big = np.flatnonzero(missing > fill_max_s)
    for i in big:
        lo = np.searchsorted(grid, tr[i], side="right")
        hi = np.searchsorted(grid, tr[i + 1], side="left")
        rg[lo:hi] = np.nan
    p_grid = predict(grid)
    if sampling == "mean":
        msl_t, msl_h = t.copy(), obs.copy()
    else:
        # Hourly heights are the series' own values at the whole hour (CO-OPS 2 §2.6: "every tenth
        # 6-minute interval value"), so they come from the unsmoothed curve: the observed sample
        # where there is one, the 3-hour fill where there is not.
        raw = p_grid + rg
        on = (grid % 3600 == 0) & np.isfinite(raw)
        msl_t, msl_h = grid[on], raw[on]
    if smooth_s > GRID_S:
        rg = running_mean(rg, smooth_s // GRID_S)
    h = p_grid + rg

    ti, kind = turning_points(h)
    seg = np.cumsum(~np.isfinite(h))[ti] if len(ti) else np.zeros(0, dtype=np.int64)
    ev_t, ev_h = grid[ti], h[ti]
    keep = filter_events(ev_t, ev_h, kind, seg, min_dt_s, min_dh_m)
    ev_t, ev_h, kind = ev_t[keep], ev_h[keep], kind[keep]
    if event_round_s:
        # CO-OPS 2 §2.6: times of high and low water to the nearest tenth of an hour
        ev_t = (np.floor(ev_t / event_round_s + 0.5) * event_round_s).astype(np.int64)
    days = days or TidalDays()
    ev_day = days.index(ev_t)
    label = label_events(ev_t, ev_h, kind, ev_day, days)

    nan_before = np.concatenate(([0], np.cumsum(~np.isfinite(h))))
    complete = set()
    for k in days.covering(g0, g1):
        a, b = days.bounds(k)
        i0 = int(np.ceil((a - g0) / GRID_S))
        i1 = int(np.ceil((b - g0) / GRID_S))
        if nan_before[i1] - nan_before[i0] == 0:
            complete.add(k)
    return Tabulation(g0, h, msl_t, msl_h, ev_t, ev_h, kind, ev_day, label, frozenset(complete), days,
                      int(((missing > 0) & (missing <= fill_max_s)).sum()), int(len(big)))
