"""The sealed T1 harness (datums spec §5.2 T1 and T1g, §5.3, slot DT1).

T1 compares OTC's monthly means, tabulated from NOAA's raw 2015 6-minute data (and the same data
decimated to whole-hour samples), with NOAA's 2015 `monthly_mean` product, and OTC's higher-high and
lower-low labels with NOAA's `high_low` product. T1g injects the sealed gap patterns into five T1
stations, reduces each against a second T1 station of the same tide class, and compares the result
with the same reduction without gaps.

The harness reads the sealed lists only through `datum_partition.load`. It refuses to run unless the
caller passes the SHA-256 of every sealed file (as written in the DT0b slot report) and they match
both the files and the loader's record. It runs on the tuning partition; the holdout and reserve
partitions need `--sealed-run`, which only the sealed runs (DT3 run 1 and run 2) pass.

Subcommands (python -m otc_pipeline.datum_t1 ...):
  fetch  --partition P            NOAA data of P's T1 stations into the work dir (1 request/s)
  plan   --partition P            T1g pairs and the expanded gap positions (hash only; no water levels read)
  run    --partition P --sealed NAME=SHA256 (x3)   score T1 and T1g; exit 0 only when every threshold passes

Exit codes of `run`: 0 pass, 1 a threshold failed, 2 "tabulation not implemented", 3 refused.
"""
from __future__ import annotations

import argparse
import calendar
import csv
import hashlib
import io
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import datum_partition

# --- seals ---------------------------------------------------------------------------------------------

T1G_FILE = "t1g_gap_patterns.json"
SEALED_FILES = ("partitions.json", "t1_stations.json", T1G_FILE)


class Refused(RuntimeError):
    """The harness will not run (a missing or wrong seal, or a sealed partition without --sealed-run)."""


def check_seals(given: dict[str, str], data_dir: Path | None = None) -> None:
    """Every sealed file must be named with its SHA-256; each must match the file and the loader's record."""
    data_dir = Path(data_dir) if data_dir else datum_partition.DATA_DIR
    missing = [n for n in SEALED_FILES if n not in given]
    if missing:
        raise Refused(f"sealed SHA-256 not given for {missing}: pass --sealed NAME=SHA256 for each of {SEALED_FILES}")
    extra = sorted(set(given) - set(SEALED_FILES))
    if extra:
        raise Refused(f"unknown sealed files {extra}")
    for name in SEALED_FILES:
        want = given[name].lower()
        if want != datum_partition.SEALED.get(name):
            raise Refused(f"{name}: given SHA-256 {want} is not the loader's sealed {datum_partition.SEALED.get(name)}")
        got = hashlib.sha256((data_dir / name).read_bytes()).hexdigest()
        if got != want:
            raise Refused(f"{name}: file SHA-256 {got} is not the sealed {want}")


def t1_stations(partition: str, *, sealed_run: bool) -> list[dict]:
    try:
        return datum_partition.load("t1", partition, sealed_run=sealed_run)
    except PermissionError as e:
        raise Refused(str(e)) from None


def t1g_patterns(data_dir: Path | None = None) -> dict:
    data_dir = Path(data_dir) if data_dir else datum_partition.DATA_DIR
    raw = (data_dir / T1G_FILE).read_bytes()
    if hashlib.sha256(raw).hexdigest() != datum_partition.SEALED[T1G_FILE]:
        raise Refused(f"{T1G_FILE} is not the sealed file")
    return json.loads(raw)["patterns"]


# --- work dir and NOAA fetch ---------------------------------------------------------------------------

YEAR = 2015
DG = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
MD = "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations"
UA = "OpenTideConstants-DT1-t1/1 (+https://opentideconstants.org)"
RETRY = {403, 429} | set(range(500, 600))
TRIES = 6
_last = [0.0]


def work_dir(partition: str) -> Path:
    base = Path(os.environ.get("OTC_WORK", "~/.local/share/opentideconstants/work")).expanduser()
    return base / "datums" / f"t1-noaa-{YEAR}" / partition


def _get(url: str) -> tuple[int | None, bytes, str | None, int]:
    """One request at a time, at most one per second; 2 s doubling backoff, 6 tries."""
    delay = 2.0
    for attempt in range(1, TRIES + 1):
        wait = 1.0 - (time.monotonic() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.monotonic()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.status, r.read(), None, attempt
        except urllib.error.HTTPError as e:
            body = e.read()
            if e.code not in RETRY or attempt == TRIES:
                return e.code, body, f"HTTP {e.code}", attempt
        except Exception as e:  # network errors: retried, then recorded
            if attempt == TRIES:
                return None, b"", f"{type(e).__name__}: {e}", attempt
        time.sleep(delay)
        delay *= 2
    raise AssertionError("unreachable")


def _dg(product: str, sid: str, begin: str, end: str, fmt: str) -> str:
    return (f"{DG}?product={product}&station={sid}&begin_date={begin}&end_date={end}&datum=STND"
            f"&units=metric&time_zone=gmt&format={fmt}&application=OpenTideConstants")


def fetch_requests(sid: str) -> list[tuple[str, str]]:
    """(file name, url) for one station: metadata, harmonic constants, monthly means, high/low waters and
    the 6-minute water levels of 2015 with 2 days either side (tidal days cross the year's ends)."""
    reqs = [("meta.json", f"{MD}/{sid}.json"),
            ("harcon.json", f"{MD}/{sid}/harcon.json?units=metric"),
            ("monthly_mean.json", _dg("monthly_mean", sid, f"{YEAR}0101", f"{YEAR}1231", "json")),
            ("high_low-1.json", _dg("high_low", sid, f"{YEAR - 1}1230", f"{YEAR}0630", "json")),   # at most a year
            ("high_low-2.json", _dg("high_low", sid, f"{YEAR}0701", f"{YEAR + 1}0103", "json")),   # per request
            ("wl-edge-before.csv", _dg("water_level", sid, f"{YEAR - 1}1230", f"{YEAR - 1}1231", "csv"))]
    for m in range(1, 13):
        last = calendar.monthrange(YEAR, m)[1]
        reqs.append((f"wl-{YEAR}-{m:02d}.csv", _dg("water_level", sid, f"{YEAR}{m:02d}01", f"{YEAR}{m:02d}{last:02d}", "csv")))
    reqs.append(("wl-edge-after.csv", _dg("water_level", sid, f"{YEAR + 1}0101", f"{YEAR + 1}0103", "csv")))
    return reqs


def fetch(partition: str, *, sealed_run: bool) -> int:
    stations = t1_stations(partition, sealed_run=sealed_run)
    out = work_dir(partition)
    log = out / "fetch.jsonl"
    out.mkdir(parents=True, exist_ok=True)
    errors = 0
    for s in stations:
        d = out / s["id"]
        d.mkdir(exist_ok=True)
        for name, url in fetch_requests(s["id"]):
            if (d / name).exists():
                continue
            code, body, err, tries = _get(url)
            ok = err is None and not (name.endswith(".json") and b'"error"' in body[:200])
            if ok:
                tmp = d / (name + ".part")
                tmp.write_bytes(body)
                tmp.replace(d / name)
            else:
                errors += 1
            with log.open("a") as f:
                f.write(json.dumps({"id": s["id"], "file": name, "http": code, "tries": tries, "error": err,
                                    "noaa_error": None if ok or err else body[:200].decode("utf-8", "replace"),
                                    "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}) + "\n")
    print(f"fetch {partition}: {len(stations)} stations, {errors} errors")
    return 1 if errors else 0


# --- reading the fetched data --------------------------------------------------------------------------

NOAA_TO_TCD = {"LAM2": "LDA2", "RHO": "RHO1"}


def read_water_levels(d: Path) -> tuple[np.ndarray, np.ndarray]:
    """6-minute water levels (m above STND), sorted, unique, non-blank."""
    files = ["wl-edge-before.csv"] + [f"wl-{YEAR}-{m:02d}.csv" for m in range(1, 13)] + ["wl-edge-after.csv"]
    rows = {}
    for name in files:
        text = (d / name).read_text()
        for r in csv.DictReader(io.StringIO(text), skipinitialspace=True):
            v = (r.get("Water Level") or "").strip()
            if not v:
                continue
            t = int(datetime.strptime(r["Date Time"].strip(), "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc).timestamp())
            rows.setdefault(t, float(v))
    t = np.array(sorted(rows), dtype=np.int64)
    return t, np.array([rows[x] for x in t], dtype=np.float64)


def read_station(partition: str, sid: str) -> dict:
    d = work_dir(partition) / sid
    if not d.is_dir():
        raise Refused(f"no fetched data for {sid} in {d}: run `fetch --partition {partition}` first")
    meta = json.loads((d / "meta.json").read_text())["stations"][0]
    harcon = json.loads((d / "harcon.json").read_text())["HarmonicConstituents"]
    mm = [r for r in json.loads((d / "monthly_mean.json").read_text())["data"] if int(r["year"]) == YEAR]
    hl = [r for n in ("high_low-1.json", "high_low-2.json") for r in json.loads((d / n).read_text())["data"]]
    t, h = read_water_levels(d)
    return {"id": sid, "lat": float(meta["lat"]), "lon": float(meta["lng"]), "state": meta.get("state"),
            "name": meta.get("name"), "harcon": harcon, "monthly_mean": mm, "high_low": hl, "t": t, "h": h}


def noaa_events(hl: list[dict]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """NOAA's tabulated high and low waters: times (s), kind (+1 high, -1 low), label (HH, H, L, LL)."""
    t, k, lab = [], [], []
    for r in hl:
        ty = r["ty"].strip()
        if ty not in ("HH", "H", "L", "LL") or not (r.get("v") or "").strip():
            continue
        t.append(int(datetime.strptime(r["t"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc).timestamp()))
        k.append(1 if ty.startswith("H") else -1)
        lab.append(ty)
    return np.array(t, dtype=np.int64), np.array(k, dtype=np.int8), np.array(lab)


# --- T1g: the sealed seeds as gap positions (committed before any T1g score) ---------------------------

T1G_EXPANSION = "otc-t1g-expand-v1"
SHORT_MIXED, SHORT_DIURNAL = 3, 2   # T1g's 5 short stations: the first 3 mixed and first 2 diurnal, sealed order
SIX_MIN = 360


def _u(*parts) -> float:
    """A uniform number in [0, 1) from SHA-256 of the parts (platform- and version-independent)."""
    h = hashlib.sha256("|".join(str(p) for p in (T1G_EXPANSION,) + parts).encode("utf-8")).digest()
    return int.from_bytes(h[:8], "big") / 2.0 ** 64


def month_bounds(year: int, month: int) -> tuple[int, int]:
    a = int(datetime(year, month, 1, tzinfo=timezone.utc).timestamp())
    y2, m2 = (year + 1, 1) if month == 12 else (year, month + 1)
    return a, int(datetime(y2, m2, 1, tzinfo=timezone.utc).timestamp())


def expand_pattern(letter: str, pattern: dict, station: str, role: str, year: int = YEAR) -> list[list[int]]:
    """Gap intervals [start, end) in Unix seconds for one station in one role ("short" or "control").

    (a)-(c): per calendar month (UTC), `gaps_per_month` gaps of `gap_hours`, each starting on the 6-minute
    grid and lying wholly inside the month. Draw k = 0, 1, ...: start = month start + 6 min x
    floor(u x number of possible starts), with u from SHA-256(expansion tag | letter | seed | station |
    role | year-month | k). A draw that overlaps or touches an accepted gap (fewer than 6 minutes between
    them) is rejected, so the gaps stay separate. (d): per calendar month, the round(10%) of its clock hours
    with the smallest u(letter, seed, station, role, year-month, hour index); each is one gap [hour, hour+1h).
    """
    seed = pattern["seed"]
    out: list[list[int]] = []
    for m in range(1, 13):
        a, b = month_bounds(year, m)
        ym = f"{year}-{m:02d}"
        if "fraction_of_hours" in pattern:
            n_hours = (b - a) // 3600
            n_drop = round(pattern["fraction_of_hours"] * n_hours)
            ranked = sorted(range(n_hours), key=lambda i: (_u(letter, seed, station, role, ym, i), i))
            out.extend([a + 3600 * i, a + 3600 * (i + 1)] for i in sorted(ranked[:n_drop]))
            continue
        length = int(pattern["gap_hours"] * 3600)
        n_starts = (b - a - length) // SIX_MIN + 1
        accepted: list[tuple[int, int]] = []
        k = 0
        while len(accepted) < pattern["gaps_per_month"]:
            if k > 100_000:
                raise RuntimeError(f"{letter}/{station}/{ym}: cannot place the gaps")
            s = a + SIX_MIN * math.floor(_u(letter, seed, station, role, ym, k) * n_starts)
            e = s + length
            k += 1
            if all(e + SIX_MIN <= s2 or e2 + SIX_MIN <= s for s2, e2 in accepted):
                accepted.append((s, e))
        out.extend([s, e] for s, e in sorted(accepted))
    return out


def roles(letter: str, pattern: dict) -> tuple[str, ...]:
    """Patterns (a)-(c) apply at the short station and at the control, with independent positions;
    (d) at the short station only (§5.2)."""
    return ("short",) if pattern.get("applies_to") == "short station only" else ("short", "control")


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(a))


def t1g_pairs(stations: list[dict], positions: dict[str, tuple[float, float]]) -> list[dict]:
    """The 5 short stations (first 3 mixed, first 2 diurnal, sealed order) and each one's control: the
    nearest other T1 station of the same class in the same partition (ties: sealed order)."""
    pairs = []
    for cls, n in (("mixed", SHORT_MIXED), ("diurnal", SHORT_DIURNAL)):
        same = [s["id"] for s in stations if s["class"] == cls]
        for sid in same[:n]:
            others = [o for o in same if o != sid]
            ctrl = min(others, key=lambda o: (haversine_km(*positions[sid], *positions[o]), same.index(o)))
            pairs.append({"short": sid, "control": ctrl, "class": cls,
                          "distance_km": round(haversine_km(*positions[sid], *positions[ctrl]), 1)})
    return pairs


def t1g_plan(stations: list[dict], positions: dict[str, tuple[float, float]], patterns: dict) -> dict:
    return t1g_gaps(t1g_pairs(stations, positions), patterns)


def t1g_gaps(pairs: list[dict], patterns: dict) -> dict:
    """Every gap list of the plan, keyed pattern|short|role|station, and the SHA-256 of their canonical JSON."""
    gaps = {}
    for letter in sorted(patterns):
        for p in pairs:
            for role in roles(letter, patterns[letter]):
                sid = p[role]
                gaps[f"{letter}|{p['short']}|{role}|{sid}"] = expand_pattern(letter, patterns[letter], sid, role)
    canon = json.dumps(gaps, sort_keys=True, separators=(",", ":"))
    return {"pairs": pairs, "gaps": gaps, "gaps_sha256": hashlib.sha256(canon.encode()).hexdigest()}


def apply_gaps(t: np.ndarray, gaps: list[list[int]]) -> np.ndarray:
    """Mask of the samples kept (those outside every [start, end))."""
    keep = np.ones(len(t), dtype=bool)
    for s, e in gaps:
        keep[(t >= s) & (t < e)] = False
    return keep


# --- the hourly-sample picking method (Slackwater's; a reported measurement, not part of RED/GREEN) ----

SW_TIDAL_DAY_S = 2 * 360.0 / 28.9841042 * 3600.0


def slackwater_month(t: np.ndarray, h: np.ndarray, year: int, month: int) -> dict | None:
    """Slackwater's observed-datum method (database packages/datums/datum.ts at e046e80), re-expressed:
    bin to the mean of each UTC hour; walk tidal days of two M2 periods from the first hourly value; in
    each day with at least 3 hourly values, a high is an hourly value >= both neighbours (and > one) and a
    low the reverse, the day's first and last values excluded; MHW/MLW = mean of all highs/lows; MHHW/MLLW
    = mean of each day's highest high / lowest low; MSL = mean of the hourly values. Applied here to one
    calendar month of data at a time, so it can be compared with NOAA's monthly means."""
    a, b = month_bounds(year, month)
    sel = (t >= a) & (t < b)
    if not sel.any():
        return None
    hours = t[sel] // 3600
    uh, inv = np.unique(hours, return_inverse=True)
    hv = np.bincount(inv, weights=h[sel]) / np.bincount(inv)
    ht = uh * 3600.0
    highs, lows, hh, ll = [], [], [], []
    day0, idx = ht[0], 0
    while day0 < ht[-1]:
        day1 = day0 + SW_TIDAL_DAY_S
        i0 = idx
        while idx < len(ht) and ht[idx] < day1:
            idx += 1
        if idx - i0 >= 3:
            dh, dl = [], []
            for i in range(i0 + 1, idx - 1):
                p, c, n = hv[i - 1], hv[i], hv[i + 1]
                if c >= p and c >= n and (c > p or c > n):
                    dh.append(c)
                elif c <= p and c <= n and (c < p or c < n):
                    dl.append(c)
            if dh:
                highs += dh
                hh.append(max(dh))
            if dl:
                lows += dl
                ll.append(min(dl))
        day0 = day1
        while idx < len(ht) and ht[idx] < day0:
            idx += 1
    if not highs or not lows:
        return None
    mhw, mlw, mhhw, mllw = (float(np.mean(x)) for x in (highs, lows, hh, ll))
    return derived({"MHHW": mhhw, "MHW": mhw, "MSL": float(np.mean(hv)), "MLW": mlw, "MLLW": mllw})


def derived(lv: dict) -> dict:
    out = dict(lv)
    out["MTL"] = (lv["MHW"] + lv["MLW"]) / 2
    out["DTL"] = (lv["MHHW"] + lv["MLLW"]) / 2
    out["MN"] = lv["MHW"] - lv["MLW"]
    out["GT"] = lv["MHHW"] - lv["MLLW"]
    out["DHQ"] = lv["MHHW"] - lv["MHW"]
    out["DLQ"] = lv["MLW"] - lv["MLLW"]
    return out


# --- T1 scoring ----------------------------------------------------------------------------------------

# (level, statistic, 6-minute limit cm, hourly limit cm); "max" = every month
T1_LIMITS = [("MSL", "max", 0.2, 0.2)]
T1_LIMITS += [(lv, s, a, b) for lv in ("MHW", "MLW", "MTL", "MN") for s, a, b in (("median", 0.3, 0.5), ("p95", 1.0, 1.5))]
T1_LIMITS += [(lv, "p95", 1.5, 2.5) for lv in ("MHHW", "MLLW", "DHQ", "DLQ")]
LABEL_AGREEMENT = 0.99
T1G_LEVELS = ("MHHW", "MHW", "MSL", "MTL", "DTL", "MLW", "MLLW")
T1G_LIMITS = (("median", 0.3), ("p95", 1.0))


def _stat(x: np.ndarray, s: str) -> float:
    x = np.abs(np.asarray(x, dtype=np.float64))
    if s == "max":
        return float(x.max())
    if s == "median":
        return float(np.median(x))
    return float(np.percentile(x, 95))


def score_months(ours: dict[str, dict[tuple[int, int], dict]], noaa: dict[str, dict[int, dict]], col: int) -> dict:
    """Pooled over every (station, month) that both sides have: |ours - NOAA| in cm per level."""
    diffs: dict[str, list[float]] = {lv: [] for lv, *_ in T1_LIMITS}
    signed: dict[str, list[float]] = {lv: [] for lv in diffs}
    n_missing = 0
    for sid, months in ours.items():
        for m in range(1, 13):
            o, n = months.get((YEAR, m)), noaa[sid].get(m)
            if o is None or n is None:
                n_missing += 1
                continue
            for lv in diffs:
                d = 100.0 * (o[lv] - n[lv])
                diffs[lv].append(abs(d))
                signed[lv].append(d)
    rows, ok = [], True
    for lv, s, *lim in T1_LIMITS:
        if not diffs[lv]:
            rows.append({"level": lv, "stat": s, "value_cm": None, "limit_cm": lim[col], "pass": False})
            ok = False
            continue
        v = _stat(np.array(diffs[lv]), s)
        p = v <= lim[col] + 1e-9
        ok &= p
        rows.append({"level": lv, "stat": s, "value_cm": round(v, 3), "limit_cm": lim[col], "pass": bool(p),
                     "signed_median_cm": round(float(np.median(signed[lv])), 3), "n": len(diffs[lv])})
    return {"pass": bool(ok), "months_not_compared": n_missing, "rows": rows}


def annual_diffs(ours: dict, noaa: dict) -> dict:
    """Per station, the mean over the compared months of (ours - NOAA) in cm: what a year's datum would carry."""
    out = {}
    for sid, months in ours.items():
        ms = [m for m in range(1, 13) if (YEAR, m) in months and m in noaa[sid]]
        out[sid] = {lv: round(float(np.mean([100.0 * (months[(YEAR, m)][lv] - noaa[sid][m][lv]) for m in ms])), 3)
                    for lv in ("MHHW", "MHW", "MSL", "MTL", "MLW", "MLLW")} if ms else None
    return out


def noaa_monthly(rows: list[dict]) -> dict[int, dict]:
    out = {}
    for r in rows:
        if r.get("inferred") != "0":
            continue
        try:
            out[int(r["month"])] = {lv: float(r[lv]) for lv in ("MHHW", "MHW", "MSL", "MTL", "DTL", "MLW", "MLLW", "MN", "GT", "DHQ", "DLQ")}
        except (KeyError, ValueError):
            continue
    return out


def label_agreement(ev_t, ev_kind, ev_label, n_t, n_kind, n_label, window: tuple[float, float],
                    match_s: int = 3600) -> dict:
    """Match each NOAA event in the window to our nearest event of the same kind within match_s; a
    NOAA event counts as agreeing when it is matched and the labels (HH/H, LL/L) are equal."""
    sel = (n_t >= window[0]) & (n_t < window[1])
    agree = matched = 0
    for t, k, lab in zip(n_t[sel], n_kind[sel], n_label[sel]):
        cand = np.flatnonzero(ev_kind == k)
        if len(cand) == 0:
            continue
        j = cand[np.argmin(np.abs(ev_t[cand] - t))]
        if abs(int(ev_t[j]) - int(t)) <= match_s:
            matched += 1
            agree += int(ev_label[j] == lab)
    ours = int(((ev_t >= window[0]) & (ev_t < window[1])).sum())
    return {"noaa_events": int(sel.sum()), "our_events": ours, "matched": matched, "label_agree": agree}


# --- the run -------------------------------------------------------------------------------------------

def _parse_sealed(items: list[str] | None) -> dict[str, str]:
    out = {}
    for it in items or []:
        if "=" not in it:
            raise Refused(f"--sealed wants NAME=SHA256, got {it!r}")
        k, v = it.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _positions(partition: str, stations: list[dict]) -> dict[str, tuple[float, float]]:
    pos = {}
    for s in stations:
        meta = json.loads((work_dir(partition) / s["id"] / "meta.json").read_text())["stations"][0]
        pos[s["id"]] = (float(meta["lat"]), float(meta["lng"]))
    return pos


def plan_path(partition: str) -> Path:
    return datum_partition.DATA_DIR / f"t1g_plan_{partition}.json"


def cmd_plan(partition: str, *, sealed_run: bool) -> dict:
    stations = t1_stations(partition, sealed_run=sealed_run)
    plan = t1g_plan(stations, _positions(partition, stations), t1g_patterns())
    return {"partition": partition, "expansion": T1G_EXPANSION, "pairs": plan["pairs"],
            "gaps_sha256": plan["gaps_sha256"],
            "rule": "datum_t1.expand_pattern and datum_t1.t1g_pairs; gaps_sha256 is SHA-256 of the canonical "
                    "JSON (sorted keys, no spaces) of every pattern|short|role|station gap list"}


def run(partition: str, sealed: dict[str, str], *, sealed_run: bool, out_dir: Path | None) -> int:
    check_seals(sealed)
    if partition != "tuning" and not sealed_run:
        raise Refused(f"the {partition} partition is sealed: only a sealed run (--sealed-run) may score it")
    stations = t1_stations(partition, sealed_run=sealed_run)
    data = {s["id"]: read_station(partition, s["id"]) for s in stations}
    report: dict = {"partition": partition, "stations": [s["id"] for s in stations], "year": YEAR}

    # Reported with the RED, not part of it: the hourly-sample picking method on the same data.
    noaa = {sid: noaa_monthly(d["monthly_mean"]) for sid, d in data.items()}
    sw = {sid: {(YEAR, m): r for m in range(1, 13) if (r := slackwater_month(d["t"], d["h"], YEAR, m))}
          for sid, d in data.items()}
    report["slackwater_hourly_picking"] = score_months(sw, noaa, col=1)

    try:
        from .observed_datums import monthly, tabulate
    except ImportError as e:
        report["result"] = "tabulation not implemented"
        report["error"] = f"{type(e).__name__}: {e}"
        _emit(report, out_dir)
        print("tabulation not implemented", file=sys.stderr)
        return 2

    from gesla.tcd_table import TcdTable
    table_path = Path(os.environ.get("OTC_TCD_TABLE", Path(os.environ.get(
        "OTC_WORK", "~/.local/share/opentideconstants/work")).expanduser() / "tcd_table.json"))
    table = TcdTable.load(table_path)

    tabs6, tabsH, labels = {}, {}, {"noaa_events": 0, "our_events": 0, "matched": 0, "label_agree": 0}
    per_station = {}
    for sid, d in data.items():
        pred = tabulate.predictor(table, {NOAA_TO_TCD.get(c["name"], c["name"]): (c["amplitude"], c["phase_GMT"])
                                          for c in d["harcon"] if c["amplitude"] > 0})
        tab6 = tabulate.tabulate(d["t"], d["h"], pred, interval_s=SIX_MIN)
        on_hour = d["t"] % 3600 == 0
        tabH = tabulate.tabulate(d["t"][on_hour], d["h"][on_hour], pred, interval_s=3600)
        tabs6[sid], tabsH[sid] = tab6, tabH
        window = (tab6.days.bounds(tab6.days.month_days(YEAR, 1)[0])[0],
                  tab6.days.bounds(tab6.days.month_days(YEAR, 12)[-1])[1])
        nt, nk, nl = noaa_events(d["high_low"])
        la = label_agreement(tab6.ev_t, tab6.ev_kind, tab6.ev_label, nt, nk, nl, window)
        for k in labels:
            labels[k] += la[k]
        per_station[sid] = la

    m6 = {sid: monthly.monthly_means(t) for sid, t in tabs6.items()}
    mH = {sid: monthly.monthly_means(t) for sid, t in tabsH.items()}
    report["annual_mean_signed_diff_cm_6min"] = annual_diffs(m6, noaa)   # a measurement, not a threshold
    report["t1_6min"] = score_months(m6, noaa, col=0)
    report["t1_hourly"] = score_months(mH, noaa, col=1)
    frac = labels["label_agree"] / labels["noaa_events"] if labels["noaa_events"] else 0.0
    report["t1_labels"] = {**labels, "agreement": round(frac, 5), "limit": LABEL_AGREEMENT,
                           "pass": frac >= LABEL_AGREEMENT, "per_station": per_station}

    report["station_gate"] = station_gate(partition, m6, mH, noaa, per_station)

    report["t1g"] = run_t1g(partition, stations, data, tabulate, monthly, table, sealed_run=sealed_run)
    ok = all(report[k]["pass"] for k in ("t1_6min", "t1_hourly", "t1_labels", "t1g"))
    report["result"] = "pass" if ok else "fail"
    report["monthly_output"] = _emit_monthly(report, m6, mH, data, out_dir=out_dir, tcd_path=table_path)
    _emit(report, out_dir)
    if out_dir:
        (Path(out_dir) / gate_file_name(partition)).write_text(
            json.dumps(report["station_gate"], indent=1, sort_keys=True) + "\n")
    return 0 if ok else 1


def run_t1g(partition, stations, data, tabulate, monthly, table, *, sealed_run) -> dict:
    """Gap injection: for each pair and pattern, reduce the short station against its control with and
    without the gaps (comparison on common tidal days, §4.3.4), and compare the reduced levels."""
    planned = json.loads(plan_path(partition).read_text())
    fresh = t1g_plan(stations, _positions(partition, stations), t1g_patterns())
    if fresh["gaps_sha256"] != planned["gaps_sha256"] or fresh["pairs"] != planned["pairs"]:
        raise Refused(f"T1g plan differs from the committed {plan_path(partition).name}")
    patterns = t1g_patterns()
    preds = {}

    def tab(sid, keep=None):
        d = data[sid]
        if sid not in preds:
            preds[sid] = tabulate.predictor(table, {NOAA_TO_TCD.get(c["name"], c["name"]): (c["amplitude"], c["phase_GMT"])
                                                    for c in d["harcon"] if c["amplitude"] > 0})
        t, h = (d["t"], d["h"]) if keep is None else (d["t"][keep], d["h"][keep])
        return tabulate.tabulate(t, h, preds[sid], interval_s=SIX_MIN)

    base = {}
    diffs = {letter: {lv: [] for lv in T1G_LEVELS} for letter in patterns}
    nocommon = {letter: {lv: [] for lv in T1G_LEVELS} for letter in patterns}
    detail = []
    for p in planned["pairs"]:
        a_id, b_id = p["short"], p["control"]
        method = us_method(data[a_id]["lon"])
        for sid in (a_id, b_id):
            if sid not in base:
                base[sid] = tab(sid)
        acc = monthly.annual_from_gap_free(base[b_id])
        ref = compare_reduce(base[a_id], base[b_id], acc, monthly, method, common=True)
        for letter in sorted(patterns):
            g = fresh["gaps"]
            ka = apply_gaps(data[a_id]["t"], g[f"{letter}|{a_id}|short|{a_id}"])
            gb = g.get(f"{letter}|{a_id}|control|{b_id}")
            ta = tab(a_id, ka)
            tb = tab(b_id, apply_gaps(data[b_id]["t"], gb)) if gb else base[b_id]
            got = compare_reduce(ta, tb, acc, monthly, method, common=True)
            loose = compare_reduce(ta, tb, acc, monthly, method, common=False)
            row = {"short": a_id, "control": b_id, "pattern": letter, "method": method,
                   "months_counted": got["n"] if got else 0, "months_counted_no_gaps": ref["n"] if ref else 0}
            for lv in T1G_LEVELS:
                if got and ref:
                    diffs[letter][lv].append(100.0 * (got[lv] - ref[lv]))
                    row[lv] = round(100.0 * (got[lv] - ref[lv]), 3)
                else:
                    diffs[letter][lv].append(float("nan"))
                    row[lv] = None
                if loose and ref:
                    nocommon[letter][lv].append(100.0 * (loose[lv] - ref[lv]))
            detail.append(row)
    rows, ok = [], True
    for letter in sorted(patterns):
        for lv in T1G_LEVELS:
            x = np.array(diffs[letter][lv])
            for s, lim in T1G_LIMITS:
                if np.isnan(x).any() or len(x) == 0:
                    v, p = None, False
                else:
                    v = round(_stat(x, s), 3)
                    p = v <= lim + 1e-9
                ok &= p
                rows.append({"pattern": letter, "level": lv, "stat": s, "value_cm": v, "limit_cm": lim, "pass": bool(p),
                             "n": int((~np.isnan(x)).sum())})
    bias = {letter: {lv: (round(float(np.median(v)), 3) if v else None) for lv, v in d.items()} for letter, d in nocommon.items()}
    return {"pass": bool(ok), "rows": rows, "pairs": planned["pairs"], "detail": detail,
            "measurement_without_common_tidal_days_signed_median_cm": bias}


def us_method(lon: float) -> str:
    """NOAA's regions (§4.3.4): standard for the West Coast, Alaska and the Pacific islands (west of
    115° W or east of 100° E), modified range ratio for the East Coast, the Gulf and the Caribbean."""
    return "standard" if (lon < -115.0 or lon > 100.0) else "modified"


def compare_reduce(ta, tb, acc_b: dict, monthly, method: str, *, common: bool) -> dict | None:
    """§4.3.4 comparison of A (short) with B (control), B's accepted values `acc_b`. With common=True,
    both stations' monthly means use only their common complete tidal days and a month counts with at
    least 24 of them; with common=False (a reported measurement), each station's own gap-free months."""
    if common:
        days = ta.complete_days & tb.complete_days
        ma = monthly.monthly_means(ta, days=days, min_days=monthly.MIN_COMMON_DAYS)
        mb = monthly.monthly_means(tb, days=days, min_days=monthly.MIN_COMMON_DAYS)
    else:
        ma, mb = monthly.monthly_means(ta), monthly.monthly_means(tb)
    months = sorted(set(ma) & set(mb))
    if not months:
        return None
    def mean_diff(lv):
        return float(np.mean([ma[m][lv] - mb[m][lv] for m in months]))
    def mean_ratio(lv):
        return float(np.mean([ma[m][lv] / mb[m][lv] for m in months]))
    mtl = acc_b["MTL"] + mean_diff("MTL")
    dtl = acc_b["DTL"] + mean_diff("DTL")
    msl = acc_b["MSL"] + mean_diff("MSL")
    mn = acc_b["MN"] * mean_ratio("MN")
    mlw = mtl - mn / 2
    mhw = mlw + mn
    if method == "standard":
        mllw = mlw - acc_b["DLQ"] * mean_ratio("DLQ")
        mhhw = mhw + acc_b["DHQ"] * mean_ratio("DHQ")
    else:
        gt = acc_b["GT"] * mean_ratio("GT")
        mllw = dtl - gt / 2
        mhhw = mllw + gt
    return {"n": len(months), "MHHW": mhhw, "MHW": mhw, "MSL": msl, "MTL": mtl, "DTL": dtl, "MLW": mlw, "MLLW": mllw}


GATE_FORMAT = "otc-t1-station-gate-v1"


def gate_file_name(partition: str) -> str:
    return f"t1-station-gate-{partition}.json"


def station_gate(partition: str, m6: dict, mH: dict, noaa: dict, labels: dict) -> dict:
    """Per-station T1 verdicts (first-release scope: datums from tabulation publish only for stations
    that pass T1). Each station is scored alone, on its own compared months, with the same limits as the
    pooled T1 (6-minute and hourly) and the same label agreement. `pass` needs every limit and the label
    agreement; `pass_without_labels` drops the label criterion (open with the owner). A month NOAA or we
    do not have fails the station: a station is never passed on fewer than 12 compared months."""
    out = {}
    for sid in sorted(m6):
        rows, num_ok = [], True
        for res, ours, col in (("6min", m6, 0), ("hourly", mH, 1)):
            sc = score_months({sid: ours[sid]}, {sid: noaa[sid]}, col)
            num_ok &= sc["pass"] and sc["months_not_compared"] == 0
            rows += [dict(r, input=res) for r in sc["rows"] if not r["pass"]]
            if sc["months_not_compared"]:
                rows.append({"input": res, "months_not_compared": sc["months_not_compared"]})
        la = labels[sid]
        agree = la["label_agree"] / la["noaa_events"] if la["noaa_events"] else 0.0
        out[sid] = {"pass": bool(num_ok and agree >= LABEL_AGREEMENT), "pass_without_labels": bool(num_ok),
                    "label_agreement": round(agree, 5), "failed": rows}
    return {"format": GATE_FORMAT, "partition": partition, "year": YEAR, "label_limit": LABEL_AGREEMENT,
            "stations": out}


def _emit_monthly(report: dict, m6: dict, mH: dict, data: dict, *,
                  out_dir: Path | None, tcd_path: Path | None) -> dict | None:
    """Serialize existing monthly measurements without changing their scientific gate.

    This native evidence is not a reduced datum or release admission. Failed T1
    measurements remain available and explicitly unvalidated; no uncertainty is invented.
    """
    if out_dir is None:
        return None
    accepted = report["result"] == "pass"
    checks = {key: report[key]["pass"] for key in ("t1_6min", "t1_hourly", "t1_labels", "t1g")}
    table_sha = hashlib.sha256(Path(tcd_path).read_bytes()).hexdigest()
    lineage = {}
    for sid in sorted(data):
        times = data[sid]["t"]
        directory = work_dir(report["partition"]) / sid
        lineage[sid] = {
            "input_sha256": {name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
                             for name, _ in fetch_requests(sid)},
            "observation_sample_count": len(times),
            "observation_start": datetime.fromtimestamp(int(times[0]), timezone.utc).isoformat(),
            "observation_end": datetime.fromtimestamp(int(times[-1]), timezone.utc).isoformat(),
            "observation_span_s": int(times[-1] - times[0]),
            "observation_sample_hours": len(times) * SIX_MIN / 3600,
        }
    rows = []
    for mode, measurements in (("6min", m6), ("hourly_picked", mH)):
        for sid in sorted(measurements):
            for (year, month), values in sorted(measurements[sid].items()):
                rows.append({
                    "source": "noaa", "station_id": sid, "year": year, "month": month,
                    "sampling_mode": mode, "kind": "observed", "units": "m", "zero": "STND",
                    "levels_m": {key: value for key, value in values.items() if key != "n_days"},
                    "complete_tidal_days": values["n_days"],
                    "status": "t1_pass" if accepted else "diagnostic",
                    "normal_t1_accepted": accepted,
                    "gate_failure_reason": None if accepted else "datum_gate_failed",
                    "full_t1_checks": checks,
                    "station_gate": report["station_gate"]["stations"][sid],
                    "reduced_to_otc_epoch": False,
                    "finding": "Monthly observed tabulation only, not an epoch reduction or admitted release datum; uncertainty is not calibrated.",
                    "tcd_sha256": table_sha, **lineage[sid],
                })
    text = "".join(json.dumps(row, sort_keys=True, allow_nan=False) + "\n" for row in rows)
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    path = Path(out_dir) / f"monthly-{report['partition']}.jsonl"
    path.write_text(text)
    return {"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "row_count": len(rows), "normal_t1_accepted": accepted}


def _emit(report: dict, out_dir: Path | None) -> None:
    text = json.dumps(report, indent=1, sort_keys=True, default=str)
    if out_dir:
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        (Path(out_dir) / f"t1-{report['partition']}.json").write_text(text + "\n")
    summary = {k: report[k]["pass"] for k in ("t1_6min", "t1_hourly", "t1_labels", "t1g") if k in report}
    print(json.dumps({"partition": report["partition"], "result": report["result"], "pass": summary,
                      "slackwater_hourly_picking_pass": report["slackwater_hourly_picking"]["pass"]}))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m otc_pipeline.datum_t1")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("fetch", "plan", "run"):
        p = sub.add_parser(name)
        p.add_argument("--partition", default="tuning", choices=datum_partition.PARTITIONS)
        p.add_argument("--sealed-run", action="store_true", help="only DT3's sealed runs pass this")
        if name == "run":
            p.add_argument("--sealed", action="append", metavar="NAME=SHA256")
            p.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "fetch":
            return fetch(a.partition, sealed_run=a.sealed_run)
        if a.cmd == "plan":
            print(json.dumps(cmd_plan(a.partition, sealed_run=a.sealed_run), indent=1, sort_keys=True))
            return 0
        return run(a.partition, _parse_sealed(a.sealed), sealed_run=a.sealed_run, out_dir=a.out)
    except Refused as e:
        print(f"refused: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
