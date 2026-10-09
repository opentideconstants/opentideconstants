#!/usr/bin/env python3
"""Harvest PEGELONLINE water-level (W) measurements for tidal stations.

PEGELONLINE serves only about the last 31 days of measurements, so this
script runs daily and archives them. For each selected station it fetches the
last PERIOD (default P2D) of W measurements and merges them into one gzip CSV
per station per UTC day:

    data/YYYY/MM/DD/<station-uuid>.csv.gz   (columns: timestamp_utc,value_cm)

Merging is idempotent: rows are keyed by timestamp, a newer fetch replaces the
value of an existing timestamp, rows are sorted, and the gzip is written with a
fixed header (mtime=0), so a re-run with the same data rewrites nothing.

Standard library only. Exit codes: 0 = ok, 2 = too many station failures.
"""

import argparse
import concurrent.futures
import csv
import datetime as dt
import gzip
import io
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://www.pegelonline.wsv.de/webservices/rest-api/v2"
USER_AGENT = "opentideconstants-pegelonline-harvest/1 (+https://github.com/opentideconstants/opentideconstants)"
HEADER = "timestamp_utc,value_cm\n"

# Selection of tidal coastal and estuary stations (see README).
COASTAL_WATERS = {"NORDSEE", "OSTSEE", "JADE", "TRAVE", "KLEINES HAFF", "WARNOW", "PEENE"}
TIDAL_TRIBUTARIES = {
    "HUNTE", "LESUM", "WÜMME", "LEDA", "OSTE", "STÖR", "PINNAU", "KRÜCKAU", "LÜHE",
    "ESTE", "SCHWINGE", "EIDER", "TREENE", "ILMENAU", "BÜTZFLETHER SÜDERELBE",
    "WISCHHAFENER SÜDERELBE", "FREIBURGER HAFENPRIEL", "RUTHENSTROM",
}
ELBE_TIDAL_MIN_KM = 585.9  # downstream of the Geesthacht weir (Elbe-km 586)
WESER_TIDAL_AGENCIES = {"BREMEN", "BREMERHAVEN"}
WESER_TIDAL_MAX_KM = 120.0  # Unterweser/Aussenweser kilometres start at Bremen
EMS_TIDAL_AGENCIES = {"STANDORT EMDEN"}  # Papenburg to Emshörn, below the Herbrum weir


def w_series(station):
    for ts in station.get("timeseries") or []:
        if ts.get("shortname") == "W":
            return ts
    return None


def is_selected(station):
    if w_series(station) is None:
        return False
    water = (station.get("water") or {}).get("longname", "")
    km = station.get("km")
    agency = station.get("agency", "")
    name = station.get("longname", "")
    if water in COASTAL_WATERS or water in TIDAL_TRIBUTARIES:
        return True
    if water == "ELBE":
        return km is not None and km >= ELBE_TIDAL_MIN_KM
    if water == "WESER":
        if agency not in WESER_TIDAL_AGENCIES:
            return False
        return name == "WESERWEHR UW" or (km is not None and km <= WESER_TIDAL_MAX_KM)
    if water == "EMS":
        return agency in EMS_TIDAL_AGENCIES
    return False


def http_get_json(url, attempts=4, timeout=60):
    last = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            last = e
        except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as e:
            last = e
        if i + 1 < attempts:
            time.sleep(2 ** (i + 1))
    raise RuntimeError(f"GET {url} failed after {attempts} attempts: {last}")


def fmt_value(v):
    s = f"{float(v):.3f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def to_utc(ts):
    t = dt.datetime.fromisoformat(ts)
    if t.tzinfo is None:
        raise ValueError(f"timestamp without offset: {ts}")
    return t.astimezone(dt.timezone.utc)


def read_day(path):
    rows = {}
    if os.path.exists(path):
        with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                rows[r["timestamp_utc"]] = r["value_cm"]
    return rows


def render(rows):
    buf = io.StringIO()
    buf.write(HEADER)
    for k in sorted(rows):
        buf.write(f"{k},{rows[k]}\n")
    return buf.getvalue()


def write_gzip(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    raw = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0) as g:
        g.write(text.encode("utf-8"))
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(raw.getvalue())
    os.replace(tmp, path)


def merge_station(root, uuid, measurements):
    """Merge measurements into per-day files. Returns stats dict."""
    by_day = {}
    for m in measurements:
        if m.get("value") is None or not m.get("timestamp"):
            continue
        t = to_utc(m["timestamp"])
        key = t.strftime("%Y-%m-%dT%H:%M:%SZ")
        by_day.setdefault(t.strftime("%Y/%m/%d"), {})[key] = fmt_value(m["value"])
    stats = {"rows_in": sum(len(v) for v in by_day.values()), "added": 0, "revised": 0, "files_written": 0}
    for day, new in by_day.items():
        path = os.path.join(root, "data", day, f"{uuid}.csv.gz")
        old = read_day(path)
        merged = dict(old)
        for k, v in new.items():
            if k not in old:
                stats["added"] += 1
            elif old[k] != v:
                stats["revised"] += 1
            merged[k] = v
        if merged != old:
            write_gzip(path, render(merged))
            stats["files_written"] += 1
    return stats


def station_record(s):
    w = w_series(s) or {}
    return {
        "uuid": s["uuid"],
        "number": s.get("number"),
        "shortname": s.get("shortname"),
        "longname": s.get("longname"),
        "water": (s.get("water") or {}).get("longname"),
        "km": s.get("km"),
        "agency": s.get("agency"),
        "latitude": s.get("latitude"),
        "longitude": s.get("longitude"),
        "W": {
            "unit": w.get("unit"),
            "equidistance_min": w.get("equidistance"),
            "gaugeZero": w.get("gaugeZero"),
        },
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=".", help="archive root (default: .)")
    ap.add_argument("--period", default="P2D", help="ISO-8601 period to fetch, max about P31D (default: P2D)")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--max-failure-ratio", type=float, default=0.10)
    args = ap.parse_args()

    stations = http_get_json(f"{API}/stations.json?includeTimeseries=true")
    if not stations:
        print("::error::station list is empty", file=sys.stderr)
        return 2
    selected = sorted((s for s in stations if is_selected(s)), key=lambda s: s["uuid"])
    print(f"stations upstream: {len(stations)}; selected tidal: {len(selected)}")

    snapshot = {
        "source": f"{API}/stations.json?includeTimeseries=true",
        "licence": "dl-de/zero-2-0 (https://www.govdata.de/dl-de/zero-2-0)",
        "stations": [station_record(s) for s in selected],
    }
    with open(os.path.join(args.root, "stations.json"), "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")

    q = urllib.parse.urlencode({"start": args.period})

    def work(s):
        url = f"{API}/stations/{s['uuid']}/W/measurements.json?{q}"
        data = http_get_json(url)
        if data is None:
            return s, {"rows_in": 0, "added": 0, "revised": 0, "files_written": 0, "missing": True}
        return s, merge_station(args.root, s["uuid"], data)

    totals = {"rows_in": 0, "added": 0, "revised": 0, "files_written": 0}
    failures, empty = [], []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(work, s): s for s in selected}
        for fut in concurrent.futures.as_completed(futs):
            s = futs[fut]
            try:
                _, st = fut.result()
            except Exception as e:  # report and continue with the other stations
                failures.append(s)
                print(f"::warning::{s['uuid']} {s.get('longname')}: {e}", file=sys.stderr)
                continue
            if st["rows_in"] == 0:
                empty.append(s)
            for k in totals:
                totals[k] += st[k]

    summary = (
        f"selected={len(selected)} ok={len(selected) - len(failures)} failed={len(failures)} "
        f"no_data={len(empty)} rows_fetched={totals['rows_in']} rows_added={totals['added']} "
        f"rows_revised={totals['revised']} files_written={totals['files_written']}"
    )
    print(summary)
    if empty:
        print("no data: " + ", ".join(sorted(str(s.get("longname")) for s in empty)))
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as f:
            f.write(f"### PEGELONLINE harvest ({args.period})\n\n`{summary}`\n")
            if failures:
                f.write("\nFailed: " + ", ".join(sorted(str(s.get("longname")) for s in failures)) + "\n")
    if not selected or len(failures) > args.max_failure_ratio * len(selected):
        print(f"::error::{len(failures)} of {len(selected)} stations failed", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
