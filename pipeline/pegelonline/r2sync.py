#!/usr/bin/env python3
"""Move the PEGELONLINE archive between the local work tree and the R2 bucket.

The archive lives in the private R2 bucket, not in git:

    s3://$OTC_WORK_BUCKET/archive/pegelonline/YYYY/MM/DD/<station-uuid>.csv.gz
    s3://$OTC_WORK_BUCKET/archive/pegelonline/YYYY/MM/DD/MANIFEST.json
    s3://$OTC_WORK_BUCKET/archive/pegelonline/stations/YYYY-MM-DD.json
    s3://$OTC_WORK_BUCKET/archive/pegelonline/stations/MANIFEST.json

Commands:

    pull --period P2D   Download every day that a harvest of PERIOD can touch into
                        <root>/data/, and check each file against the day's manifest.
    push                Upload the files that changed, and rewrite the manifests of
                        those days. Upload stations.json as today's snapshot.
    manifest            Write the day manifests for all days under <root>/data/
                        (used for the first upload of an existing tree).

A harvest merges into the pulled files, so the bucket object for a day is always
the union of all fetches. Push writes nothing when nothing changed.

Uses the aws CLI (S3 API). Credentials come from the usual AWS_* variables;
AWS_ENDPOINT_URL_S3 points at the R2 endpoint.
"""

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

PREFIX = "archive/pegelonline"
API = "https://www.pegelonline.wsv.de/webservices/rest-api/v2"
LICENCE = "dl-de/zero-2-0 (https://www.govdata.de/dl-de/zero-2-0)"
PULLED = ".pulled-days.json"
DAY_RE = re.compile(r"^\d{4}/\d{2}/\d{2}$")


def bucket():
    b = os.environ.get("OTC_WORK_BUCKET")
    if not b:
        sys.exit("OTC_WORK_BUCKET is not set")
    return b


def aws(*args):
    cmd = ["aws", "s3", *args, "--only-show-errors"]
    r = subprocess.run(cmd)
    if r.returncode != 0:
        sys.exit(f"failed: aws s3 {' '.join(args)} (exit {r.returncode})")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def now_iso():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_period(p):
    m = re.fullmatch(r"P(\d+)D", p)
    if m:
        return dt.timedelta(days=int(m.group(1)))
    m = re.fullmatch(r"PT(\d+)H", p)
    if m:
        return dt.timedelta(hours=int(m.group(1)))
    sys.exit(f"unsupported period {p!r} (use PnD or PTnH)")


def window_days(period):
    """UTC days a fetch of PERIOD can write, with one day of margin each side."""
    now = dt.datetime.now(dt.timezone.utc)
    d = (now - parse_period(period)).date() - dt.timedelta(days=1)
    last = now.date() + dt.timedelta(days=1)
    days = []
    while d <= last:
        days.append(d.strftime("%Y/%m/%d"))
        d += dt.timedelta(days=1)
    return days


def load_json(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def day_manifest(day_dir, day, old, fetched_at):
    """Manifest for the *.csv.gz files in day_dir. Keeps fetched_at of unchanged files.

    Returns (manifest, changed_paths)."""
    prev = {e["path"]: e for e in (old or {}).get("files", [])}
    files, changed = [], []
    for name in sorted(os.listdir(day_dir)):
        if not name.endswith(".csv.gz"):
            continue
        p = os.path.join(day_dir, name)
        e = {
            "path": name,
            "sha256": sha256(p),
            "size": os.path.getsize(p),
            "source_url": f"{API}/stations/{name[:-len('.csv.gz')]}/W/measurements.json",
        }
        o = prev.get(name)
        if o and o["sha256"] == e["sha256"] and o["size"] == e["size"]:
            e["fetched_at"] = o["fetched_at"]
        else:
            e["fetched_at"] = fetched_at(name) if callable(fetched_at) else fetched_at
            changed.append(name)
        files.append(e)
    manifest = {
        "dataset": "PEGELONLINE W (water level) raw measurements, tidal stations",
        "day_utc": day.replace("/", "-"),
        "columns": "timestamp_utc,value_cm",
        "licence": LICENCE,
        "files": files,
    }
    return manifest, changed


def cmd_pull(args):
    root = os.path.join(args.root, "data")
    days = window_days(args.period)
    b = bucket()
    for day in days:
        local = os.path.join(root, day)
        os.makedirs(local, exist_ok=True)
        aws("cp", "--recursive", f"s3://{b}/{PREFIX}/{day}/", local + "/")
        m = load_json(os.path.join(local, "MANIFEST.json"))
        names = [n for n in os.listdir(local) if n.endswith(".csv.gz")]
        if m is None:
            if names:
                sys.exit(f"{day}: {len(names)} objects but no MANIFEST.json; stop")
            continue
        listed = {e["path"]: e for e in m["files"]}
        if set(listed) != set(names):
            sys.exit(f"{day}: files differ from MANIFEST.json; stop")
        for n in names:
            if sha256(os.path.join(local, n)) != listed[n]["sha256"]:
                sys.exit(f"{day}/{n}: sha256 differs from MANIFEST.json; stop")
        print(f"pulled {day}: {len(names)} files, verified")
    write_json(os.path.join(args.root, PULLED), days)


def cmd_push(args):
    root = os.path.join(args.root, "data")
    pulled = load_json(os.path.join(args.root, PULLED))
    if pulled is None:
        sys.exit("no pull record; run pull first")
    present = sorted(
        os.path.relpath(dp, root)
        for dp, _, fns in os.walk(root)
        if DAY_RE.match(os.path.relpath(dp, root)) and any(f.endswith(".csv.gz") for f in fns)
    )
    stray = [d for d in present if d not in pulled]
    if stray:
        sys.exit(f"days written but not pulled (would overwrite): {stray}; stop")
    b = bucket()
    ts = now_iso()
    stage = tempfile.mkdtemp(prefix="r2push-")
    n_files = n_days = 0
    try:
        for day in present:
            local = os.path.join(root, day)
            mpath = os.path.join(local, "MANIFEST.json")
            old = load_json(mpath)
            new, changed = day_manifest(local, day, old, ts)
            if not changed and old == new:
                continue
            out = os.path.join(stage, day)
            os.makedirs(out, exist_ok=True)
            for n in changed:
                shutil.copy2(os.path.join(local, n), os.path.join(out, n))
            write_json(mpath, new)
            shutil.copy2(mpath, os.path.join(out, "MANIFEST.json"))
            n_files += len(changed)
            n_days += 1
        snap = os.path.join(args.root, "stations.json")
        if os.path.exists(snap):
            n_files += push_stations(b, snap, stage, ts)
        if n_files or n_days:
            # Data files go first, manifests last, so a reader never sees a manifest
            # that lists a file that is not there yet.
            aws("cp", "--recursive", stage + "/", f"s3://{b}/{PREFIX}/", "--exclude", "*MANIFEST.json")
            aws("cp", "--recursive", stage + "/", f"s3://{b}/{PREFIX}/", "--exclude", "*", "--include", "*MANIFEST.json")
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    summary = f"r2 push: {n_files} files uploaded, {n_days} day manifests updated"
    print(summary)
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as f:
            f.write(f"\n`{summary}`\n")


def push_stations(b, snap, stage, ts, day=None):
    """Store stations.json as stations/<day>.json if it changed. Returns 0 or 1."""
    day = day or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    name = f"{day}.json"
    tmp = tempfile.mkdtemp(prefix="r2st-")
    try:
        mlocal = os.path.join(tmp, "MANIFEST.json")
        r = subprocess.run(
            ["aws", "s3", "cp", f"s3://{b}/{PREFIX}/stations/MANIFEST.json", mlocal, "--only-show-errors"],
            capture_output=True, text=True,
        )
        if r.returncode != 0 and "404" not in r.stderr and "Not Found" not in r.stderr:
            sys.exit(f"stations manifest download failed: {r.stderr.strip()}")
        m = load_json(mlocal) or {"dataset": "PEGELONLINE selected tidal stations, daily snapshots",
                                  "licence": LICENCE, "files": []}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    digest = sha256(snap)
    entries = {e["path"]: e for e in m["files"]}
    if name in entries and entries[name]["sha256"] == digest:
        return 0
    entries[name] = {
        "path": name,
        "sha256": digest,
        "size": os.path.getsize(snap),
        "source_url": f"{API}/stations.json?includeTimeseries=true",
        "fetched_at": ts,
    }
    m["files"] = [entries[k] for k in sorted(entries)]
    out = os.path.join(stage, "stations")
    os.makedirs(out, exist_ok=True)
    shutil.copy2(snap, os.path.join(out, name))
    write_json(os.path.join(out, "MANIFEST.json"), m)
    return 1


def cmd_manifest(args):
    root = os.path.join(args.root, "data")
    fetched = load_json(args.fetched_at_map) if args.fetched_at_map else {}
    n = 0
    for dp, _, fns in sorted(os.walk(root)):
        day = os.path.relpath(dp, root)
        if not DAY_RE.match(day) or not any(f.endswith(".csv.gz") for f in fns):
            continue
        m, _ = day_manifest(dp, day, None, lambda name, day=day: fetched[f"data/{day}/{name}"])
        write_json(os.path.join(dp, "MANIFEST.json"), m)
        n += 1
    print(f"wrote {n} day manifests")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=".")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pull")
    p.add_argument("--period", default="P2D")
    sub.add_parser("push")
    p = sub.add_parser("manifest")
    p.add_argument("--fetched-at-map", help="JSON {data/YYYY/MM/DD/<file>: fetched_at}")
    args = ap.parse_args()
    {"pull": cmd_pull, "push": cmd_push, "manifest": cmd_manifest}[args.cmd](args)


if __name__ == "__main__":
    main()
